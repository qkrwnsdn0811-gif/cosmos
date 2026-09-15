"""Bounded collector scheduler with persistent queues and a Kafka outbox."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

from .common import Outbox, atomic_json, kafka_producer, make_event, utcnow
from .overseas import OverseasCollector

LOG = logging.getLogger("cosmos-news-collector")
STOP = False


class DomesticCollector:
    SOURCES = ("kpenews", "mdtoday", "sedaily", "newspim", "newstomato", "hellot")

    def __init__(self, state_dir, outbox, node, script):
        self.outbox, self.node, self.script = outbox, node, script
        self.db = sqlite3.connect(Path(state_dir) / "domestic.db", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS candidates (
                url TEXT PRIMARY KEY,source TEXT NOT NULL,title TEXT,organization TEXT,
                status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,next_try REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS candidates_pending ON candidates(source,status,next_try);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        self.db.execute("UPDATE candidates SET status='pending',attempts=max(0,attempts-1) WHERE status='processing'")
        self.db.commit()

    def invoke(self, mode, source, **kwargs):
        cmd = [self.node, self.script, "--mode", mode, "--source", source]
        for k, v in kwargs.items():
            if v is not None and v != "":
                cmd.extend(["--" + k.replace("_", "-"), str(v)])
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=180)
        if r.returncode:
            # Only our adapter's bounded diagnostic, never full child environment.
            raise RuntimeError((r.stderr or r.stdout or f"node exit {r.returncode}")[-1500:])
        return json.loads(r.stdout)

    def get_state(self, key, default=""):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def collect(self, limit=8):
        index = int(self.get_state("source_index", "0")) % len(self.SOURCES)
        source = self.SOURCES[index]
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('source_index',?)", (str((index+1) % len(self.SOURCES)),))
        result = {"source": source, "enqueued": 0, "existing": 0, "fetched": 0, "failed": 0, "discovered": 0}
        pending = self.db.execute("""SELECT COUNT(*) FROM candidates WHERE source=?
            AND status IN ('pending','failed') AND attempts<5 AND next_try<=?""", (source, time.time())).fetchone()[0]
        if pending < limit:
            cursor = self.get_state("cursor:" + source)
            last_complete = float(self.get_state("complete:" + source, "0"))
            if cursor or time.time() - last_complete > 600:
                try:
                    page = self.invoke("discover", source, max_urls=50, cursor=cursor,
                                       min_id=10000 if source == "hellot" else None,
                                       max_id=114676 if source == "hellot" else None)
                except Exception as exc:
                    # A temporarily broken sitemap must not block durable work.
                    page = None
                    result["discovery_error"] = str(exc)[-1500:]
                # Candidate queue and page cursor advance in the SAME transaction.
                if page is not None:
                    with self.db:
                        for item in page.get("candidates", []):
                            self.db.execute("""INSERT OR IGNORE INTO candidates
                                (url,source,title,organization) VALUES (?,?,?,?)""",
                                (item["url"], source, item.get("title", ""), item.get("organization", "")))
                        next_cursor = page.get("next_cursor") or ""
                        self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", ("cursor:"+source, next_cursor))
                        if not next_cursor:
                            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", ("complete:"+source, str(time.time())))
                    result["discovered"] = len(page.get("candidates", []))
        rows = self.db.execute("""SELECT * FROM candidates WHERE source=?
            AND status IN ('pending','failed') AND attempts<5 AND next_try<=?
            ORDER BY rowid LIMIT ?""", (source, time.time(), limit)).fetchall()
        for row in rows:
            if STOP:
                break
            if self.outbox.seen_url(row["url"]):
                with self.db:
                    self.db.execute("UPDATE candidates SET status='done',last_error=NULL WHERE url=?", (row["url"],))
                result["existing"] += 1
                continue
            try:
                with self.db:
                    self.db.execute("UPDATE candidates SET status='processing',attempts=attempts+1 WHERE url=?", (row["url"],))
                article = self.invoke("fetch", source, url=row["url"])
                result["fetched"] += 1
                event = make_event(source=article.get("source", source), region="domestic",
                    language="ko", url=article["url"], title=article["title"],
                    content=article["content"], organization=article.get("organization", ""),
                    published_at=article.get("published_at"), run_id=str(uuid.uuid4()),
                    metadata={key: article.get(key) for key in
                        ("final_url", "robots", "truncated", "extraction_method", "external_id")})
            except Exception as exc:
                with self.db:
                    self.db.execute("UPDATE candidates SET status='failed',last_error=?,next_try=? WHERE url=?",
                        (str(exc)[-1500:], time.time()+300, row["url"]))
                result["failed"] += 1
                LOG.warning("domestic source=%s failure=%s", source, type(exc).__name__)
                time.sleep(2)
                continue
            try:
                added = self.outbox.enqueue(event)
            except Exception:
                # Infrastructure failures do not consume an article's retry budget.
                with self.db:
                    self.db.execute("UPDATE candidates SET status='pending',attempts=max(0,attempts-1) WHERE url=?", (row["url"],))
                raise
            with self.db:
                self.db.execute("UPDATE candidates SET status='done',last_error=NULL WHERE url=?", (row["url"],))
            result["enqueued" if added else "existing"] += 1
            time.sleep(2)
        result["queue"] = [dict(r) for r in self.db.execute("SELECT status,COUNT(*) AS rows FROM candidates WHERE source=? GROUP BY status", (source,))]
        result["exhausted"] = self.db.execute("SELECT COUNT(*) FROM candidates WHERE source=? AND status='failed' AND attempts>=5", (source,)).fetchone()[0]
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--crawler-root", default=str(Path(__file__).resolve().parents[2] / "vendor/overseas-news-crawler"))
    parser.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"))
    parser.add_argument("--topic", default="news.raw")
    parser.add_argument("--node", default="node")
    parser.add_argument("--domestic-script", default=str(Path(__file__).with_name("domestic.bundle.mjs")))
    parser.add_argument("--jobs", default="latest,domestic,backfill")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--idle-seconds", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("limit must be 1..100")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock = (state_dir / "collector.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    def stop(*_):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    outbox = Outbox(state_dir / "outbox.db")
    overseas = OverseasCollector(args.crawler_root, str(state_dir / "overseas"), outbox,
                                 stop_requested=lambda: STOP)
    domestic = DomesticCollector(state_dir, outbox, args.node, args.domestic_script)
    producer = kafka_producer(args.bootstrap)
    jobs = args.jobs.split(",")
    if any(job not in {"latest", "domestic", "backfill", "publish"} for job in jobs):
        parser.error("unknown job")
    status = {"started_at": utcnow(), "cycles": 0, "jobs": {}}
    try:
        while not STOP:
            stats = outbox.stats()
            if stats["pending"] > 10000 or stats["payload_bytes"] > 10*1024**3 or shutil.disk_usage(state_dir).free < 20*1024**3:
                current_jobs = ["publish"]
                status["collection_paused"] = "outbox_or_disk_limit"
            else:
                current_jobs = jobs
                status.pop("collection_paused", None)
            failed = False
            for job in current_jobs:
                if STOP: break
                status.update(updated_at=utcnow(), active_job=job)
                atomic_json(state_dir / "collector-status.json", status)
                try:
                    if job == "latest": result = overseas.collect_latest(limit=args.limit)
                    elif job == "backfill": result = overseas.collect_backfill(limit=args.limit)
                    elif job == "domestic": result = domestic.collect(limit=args.limit)
                    else: result = {}
                    result["delivery"] = outbox.publish(producer, args.topic)
                    status["jobs"][job] = {"updated_at": utcnow(), "result": result}
                    LOG.info("job=%s %s", job, json.dumps(result, ensure_ascii=False))
                except Exception as exc:
                    failed = True
                    status["jobs"][job] = {"updated_at": utcnow(), "error": str(exc)[-1500:]}
                    LOG.exception("job=%s failed", job)
                status.update(updated_at=utcnow(), outbox=outbox.stats())
                atomic_json(state_dir / "collector-status.json", status)
            status["cycles"] += 1
            status.update(updated_at=utcnow(), active_job=None)
            atomic_json(state_dir / "collector-status.json", status)
            if args.once: return 1 if failed else 0
            for _ in range(args.idle_seconds):
                if STOP: break
                time.sleep(1)
    finally:
        outbox.close()
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
