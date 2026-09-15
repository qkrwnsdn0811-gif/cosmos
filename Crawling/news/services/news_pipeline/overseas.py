"""Bounded Yahoo archive collection into the durable Kafka outbox.

The original article/parser and SQLite queue are reused without importing the
RDS entrypoint. Existing HDFS snapshots and the old AWS queues are untouched.
"""

from __future__ import annotations

import importlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import uuid

from .common import make_event


SOURCE = "yahoo_finance"


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _load_crawler(crawler_root: str):
    root = Path(crawler_root).resolve()
    expected = root / "lib" / "Crawling" / "News" / "overseas.py"
    if not expected.is_file():
        raise ValueError(f"Overseas crawler source not found under {root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    overseas = importlib.import_module("lib.Crawling.News.overseas")
    history = importlib.import_module("lib.Crawling.News.history")
    if Path(overseas.__file__).resolve() != expected:
        raise RuntimeError("A different overseas crawler package is already imported")
    return overseas, history


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class OverseasCollector:
    """One process owns these queues; the service runner supplies its lock.

    ``outbox.enqueue`` must return only after durable acceptance, including
    when it returns False because the same event already exists. A failed
    outbox write leaves the crawl item retryable and propagates to the caller.
    """

    def __init__(
        self,
        crawler_root: str,
        state_dir: str,
        outbox,
        *,
        latest_start_date: str = "2026-09-09",
        max_discovery_pages: int = 3,
        request_delay: float = 4.5,
        article_attempts: int = 3,
        stop_requested=None,
    ):
        self.overseas, self.history = _load_crawler(crawler_root)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.outbox = outbox
        self.stop_requested = stop_requested or (lambda: False)
        self.latest_start = date.fromisoformat(latest_start_date)
        if max_discovery_pages < 1 or article_attempts < 1 or request_delay < 0:
            raise ValueError("Invalid overseas collector bounds")
        self.max_discovery_pages = max_discovery_pages
        self.article_attempts = article_attempts
        self.client = self.overseas.BrowserHttpClient(
            timeout=25.0, retries=2, request_delay=request_delay
        )
        self.source = self.overseas.SOURCE_CLASSES["yahoo"](self.client)
        self.recovered = 0
        # Startup recovery happens once, before the service calls either loop.
        for path in [self.state_dir / "latest.db", *self.state_dir.glob("backfill-*.db")]:
            if path.exists():
                with self.history.HistoryQueue(path) as queue:
                    # Interrupted fetches do not consume their final retry.
                    queue.connection.execute(
                        "UPDATE history_queue SET attempts=max(0,attempts-1) "
                        "WHERE status='processing'"
                    )
                    queue.connection.commit()
                    self.recovered += queue.reset_interrupted()

    def _read_scheduler(self) -> dict:
        path = self.state_dir / "scheduler.json"
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("Invalid overseas scheduler state")
        return value

    def _write_scheduler(self, value: dict) -> None:
        _atomic_json(self.state_dir / "scheduler.json", value)

    @staticmethod
    def _state(queue, prefix: str, key: str):
        return queue.get_state(SOURCE, f"{prefix}_{key}")

    @staticmethod
    def _save_cursor(queue, prefix: str, current: date, cursor_url: str) -> None:
        queue.set_states(
            SOURCE,
            **{f"{prefix}_date": current.isoformat(), f"{prefix}_url": cursor_url},
        )

    def _eligible_count(self, queue) -> int:
        return queue.connection.execute(
            "SELECT count(*) FROM history_queue WHERE source=? "
            "AND status IN ('pending','failed') AND attempts<?",
            (SOURCE, self.article_attempts),
        ).fetchone()[0]

    def _queue_stats(self, queue) -> dict:
        return {
            **queue.stats(SOURCE),
            "exhausted": queue.connection.execute(
                "SELECT count(*) FROM history_queue WHERE source=? "
                "AND status='failed' AND attempts>=?",
                (SOURCE, self.article_attempts),
            ).fetchone()[0],
        }

    def _collect(self, path: Path, prefix: str, lower: date, upper: date, limit: int) -> dict:
        if limit < 1:
            raise ValueError("limit must be at least one")
        result = {
            "source": SOURCE, "mode": prefix, "fetched": 0, "enqueued": 0,
            "existing": 0, "failed": 0, "discovery_pages": 0,
            "discovered": 0, "discovery_errors": 0, "recovered": self.recovered,
        }
        with self.history.HistoryQueue(path) as queue:
            saved_date = self._state(queue, prefix, "date")
            current = date.fromisoformat(saved_date) if saved_date else upper
            cursor_url = self._state(queue, prefix, "url") or ""
            # Persist the initial upper bound before network activity. Restarting
            # must not silently move a partially scanned archive to another day.
            if not saved_date:
                self._save_cursor(queue, prefix, current, cursor_url)
            for _ in range(self.max_discovery_pages):
                if self.stop_requested():
                    break
                if current < lower or self._eligible_count(queue) >= limit:
                    break
                page_url = cursor_url or (
                    f"https://finance.yahoo.com/sitemap/{current.strftime('%Y_%m_%d')}"
                )
                try:
                    self.client.assert_robots_allowed(page_url)
                    html = self.client.get_text(page_url)
                    page = self.history.parse_yahoo_archive_page(html, page_url)
                except Exception as exc:
                    queue.record_discovery_error(
                        SOURCE, page_url, current.isoformat(),
                        f"{type(exc).__name__}: {str(exc)[:500]}",
                    )
                    result["discovery_errors"] += 1
                    # Do not advance past an unavailable page and claim it done.
                    break
                result["discovered"] += queue.enqueue(
                    page.candidates, archive_key=current.isoformat()
                )
                result["discovery_pages"] += 1
                if page.next_url and page.next_url != page_url:
                    cursor_url = page.next_url
                else:
                    current -= timedelta(days=1)
                    cursor_url = ""
                self._save_cursor(queue, prefix, current, cursor_url)

            run_id = str(uuid.uuid4())
            for candidate in queue.claim_batch(SOURCE, self.article_attempts, limit):
                if self.stop_requested():
                    self._release_claimed(queue)
                    break
                try:
                    existing = self.outbox.seen_url(candidate.url)
                except Exception:
                    self._release_claimed(queue)
                    raise
                if existing:
                    queue.mark_done(candidate.url)
                    result["existing"] += 1
                    continue
                result["fetched"] += 1
                try:
                    self.client.assert_robots_allowed(candidate.url)
                    article = self.source.parse_article(
                        self.client.get_text(candidate.url), candidate
                    )
                    if len(article.content) < self.source.minimum_content_chars:
                        raise ValueError("article body too short")
                    event = make_event(
                        source=article.source, region="overseas", language="en",
                        url=article.url, title=article.title, content=article.content,
                        organization=article.organization or "",
                        published_at=article.published_at,
                        collected_at=article.crawled_at, run_id=run_id,
                    )
                except Exception as exc:
                    queue.mark_failed(candidate.url, f"{type(exc).__name__}: {str(exc)[:500]}")
                    result["failed"] += 1
                    continue
                try:
                    accepted = self.outbox.enqueue(event)
                except Exception:
                    # Local storage/Kafka infrastructure failures are not final
                    # article failures and must not exhaust article attempts.
                    self._release_claimed(queue)
                    raise
                queue.mark_done(candidate.url)
                result["enqueued" if accepted else "existing"] += 1

            result["cursor_date"] = current.isoformat()
            result["cursor_has_next_page"] = bool(cursor_url)
            result["discovery_complete"] = current < lower
            result["queue"] = self._queue_stats(queue)
            result["complete"] = bool(
                result["discovery_complete"]
                and not any(result["queue"][key] for key in ("pending", "processing", "failed"))
            )
        return result

    @staticmethod
    def _release_claimed(queue) -> None:
        queue.connection.execute(
            "UPDATE history_queue SET status='pending',attempts=max(0,attempts-1) "
            "WHERE status='processing'"
        )
        queue.connection.commit()

    def collect_latest(self, limit: int = 10) -> dict:
        today = _today()
        scheduler = self._read_scheduler()
        turn = int(scheduler.get("latest_turn", 0))
        path = self.state_dir / "latest.db"
        with self.history.HistoryQueue(path) as queue:
            gap_date = self._state(queue, "gap", "date")
            gap_done = bool(gap_date and date.fromisoformat(gap_date) < self.latest_start)
            prefix = "rolling" if gap_done or turn % 2 else "gap"
            lower = max(self.latest_start, today - timedelta(days=2)) if prefix == "rolling" else self.latest_start
            upper = today
            if prefix == "rolling":
                # An unfinished three-day scan keeps its original floor until
                # complete; new days are picked up on the next bounded sweep.
                floor = self._state(queue, prefix, "floor")
                cursor = self._state(queue, prefix, "date")
                if floor and cursor and date.fromisoformat(cursor) >= date.fromisoformat(floor):
                    lower = date.fromisoformat(floor)
                else:
                    queue.set_states(SOURCE, rolling_floor=lower.isoformat())
                    self._save_cursor(queue, prefix, upper, "")
        result = self._collect(path, prefix, lower, upper, limit)
        scheduler["latest_turn"] = turn + 1
        self._write_scheduler(scheduler)
        return result

    def collect_backfill(self, limit: int = 10, start_year: int = 2016, end_year: int = 2026) -> dict:
        if start_year < 2012 or end_year < start_year:
            raise ValueError("Invalid Yahoo backfill year range")
        end_year = min(end_year, _today().year)
        if end_year < start_year:
            raise ValueError("Backfill years are in the future")
        scheduler = self._read_scheduler()
        year = int(scheduler.get("next_backfill_year", start_year))
        if not start_year <= year <= end_year:
            year = start_year
        result = self._collect(
            self.state_dir / f"backfill-{year}.db", "backfill",
            date(year, 1, 1), min(date(year, 12, 31), _today()), limit,
        )
        result["year"] = year
        scheduler["next_backfill_year"] = start_year if year >= end_year else year + 1
        self._write_scheduler(scheduler)
        return result
