"""Separate Naver discovery/delivery workers; neither blocks the existing crawler."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import time

from .common import Outbox, atomic_json, kafka_producer, utcnow
from .naver import NaverSearchClient, NaverStore, load_companies
from .naver_delivery import ArticleFetcher, NaverDelivery


LOG = logging.getLogger("cosmos-naver-news")
STOP = False


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=("search", "deliver"), required=True)
    result.add_argument("--state-dir", required=True)
    result.add_argument("--companies", default=str(Path(__file__).resolve().parents[2] / "config/kospi100.json"))
    result.add_argument("--poll-seconds", type=int, default=600)
    result.add_argument("--daily-budget", type=int, default=24000)
    result.add_argument("--api-mode", choices=("openapi", "hub"), default=os.environ.get("NAVER_API_MODE", "hub"))
    result.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"))
    result.add_argument("--topic", default="news.raw")
    result.add_argument("--node", default="node")
    result.add_argument("--article-script", default=str(Path(__file__).with_name("naver-fetch.bundle.mjs")))
    result.add_argument("--limit", type=int, default=16)
    result.add_argument("--idle-seconds", type=int, default=2)
    result.add_argument("--once", action="store_true")
    return result


def main(argv=None):
    global STOP
    STOP = False
    cli = parser()
    args = cli.parse_args(argv)
    if args.poll_seconds < 600 or not 100 <= args.daily_budget <= 24000:
        cli.error("poll-seconds must be >=600 and daily-budget must be 100..24000")
    if not 1 <= args.limit <= 100 or not 1 <= args.idle_seconds <= 60:
        cli.error("limit must be 1..100 and idle-seconds must be 1..60")
    companies = load_companies(args.companies)
    client = None
    if args.mode == "search":
        client_id = os.environ.get("NAVER_CLIENT_ID", "")
        client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            cli.error("NAVER_CLIENT_ID and NAVER_CLIENT_SECRET must be configured")
        client = NaverSearchClient(client_id, client_secret, mode=args.api_mode)
    elif not Path(args.article_script).is_file():
        cli.error("article bundle is missing; run npm ci and npm run build")
    import fcntl
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = (state_dir / (args.mode + ".lock")).open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    def stop(*_):
        global STOP
        STOP = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = NaverStore(state_dir / "naver.db", companies, poll_seconds=args.poll_seconds,
                       daily_budget=args.daily_budget)
    outbox = None
    producer = None
    delivery = None
    if args.mode == "deliver":
        outbox = Outbox(state_dir / "outbox.db")
        producer = kafka_producer(args.bootstrap)
        def publish_article():
            if outbox.publish(producer, args.topic).get("errors"):
                raise RuntimeError("kafka_delivery_failed")
            for _ in range(2):
                if STOP:
                    break
                time.sleep(1)
        delivery = NaverDelivery(store, companies, outbox, ArticleFetcher(args.node, args.article_script),
                                 stop_requested=lambda: STOP, after_enqueue=publish_article)
    status = {"started_at": utcnow(), "mode": args.mode, "cycles": 0, "company_count": len(companies)}
    try:
        while not STOP:
            failed = False
            try:
                disk_free = shutil.disk_usage(state_dir).free
                if args.mode == "search":
                    store_stats = store.stats()
                    candidates = store_stats["candidates"]
                    pending = (candidates.get("pending", 0) + candidates.get("failed", 0)
                               - store_stats["exhausted_candidates"])
                    if disk_free < 2 * 1024**3 or pending >= 50000:
                        result = {"status": "paused", "reason": "disk_space_or_candidate_limit"}
                    else:
                        result = store.discover_one(client)
                        failed = result.get("status") in {"error", "overflow"}
                else:
                    result = {"delivery": outbox.publish(producer, args.topic)}
                    if result["delivery"].get("errors"):
                        failed = True
                    if disk_free < 2 * 1024**3 or outbox.stats()["pending"] >= 10000:
                        result["collection_paused"] = "outbox_or_disk_limit"
                    else:
                        result["articles"] = delivery.collect(args.limit)
                        failed = failed or bool(result["articles"]["failed"])
                        result["delivery_after_collect"] = outbox.publish(producer, args.topic)
                        failed = failed or bool(result["delivery_after_collect"].get("errors"))
                status.update(result=result, store=store.stats(), updated_at=utcnow())
                status.pop("error", None)
                if outbox is not None:
                    status["outbox"] = outbox.stats()
            except Exception as error:
                failed = True
                status.update(error=type(error).__name__, updated_at=utcnow())
                LOG.error("worker=%s error=%s", args.mode, type(error).__name__)
            status["cycles"] += 1
            atomic_json(state_dir / (args.mode + "-status.json"), status)
            if args.once:
                print(json.dumps(status, ensure_ascii=False))
                return 1 if failed else 0
            # Short interruptible idle; all clocks/cursors are durable in NaverStore.
            for _ in range(args.idle_seconds):
                if STOP:
                    break
                time.sleep(1)
    finally:
        if outbox is not None:
            outbox.close()
        if client is not None:
            client.close()
        store.close()
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
