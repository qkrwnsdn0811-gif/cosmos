"""Collector recovery/range tests; all source I/O and outbox calls are fake."""

from datetime import date
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from services.news_pipeline import overseas


class MemoryQueue:
    """Small model of the queue's claim and state contract, no network/SQL."""

    databases = {}

    def __init__(self, path):
        self.path = str(path)
        self.data = self.databases.setdefault(self.path, {"items": {}, "state": {}, "errors": []})
        self.connection = self
        self._row = (0,)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        items = self.data["items"].values()
        if "count(*)" in sql:
            attempts = params[-1]
            if "status='failed'" in sql:
                count = sum(x["status"] == "failed" and x["attempts"] >= attempts for x in items)
            else:
                count = sum(x["status"] in {"pending", "failed"} and x["attempts"] < attempts for x in items)
            self._row = (count,)
        elif "UPDATE history_queue" in sql:
            for item in items:
                if item["status"] == "processing":
                    item["attempts"] = max(0, item["attempts"] - 1)
                    if "status='pending'" in sql:
                        item["status"] = "pending"
        return self

    def fetchone(self):
        return self._row

    def commit(self):
        pass

    def reset_interrupted(self):
        changed = 0
        for item in self.data["items"].values():
            if item["status"] == "processing":
                item["status"] = "pending"
                changed += 1
        return changed

    def get_state(self, source, key):
        return self.data["state"].get(key)

    def set_states(self, source, **values):
        self.data["state"].update(values)

    def enqueue(self, candidates, archive_key):
        count = 0
        for candidate in candidates:
            if candidate.url not in self.data["items"]:
                self.data["items"][candidate.url] = {"candidate": candidate, "status": "pending", "attempts": 0}
                count += 1
        return count

    def claim_batch(self, source, attempts, limit):
        selected = []
        for item in self.data["items"].values():
            if item["status"] in {"pending", "failed"} and item["attempts"] < attempts:
                item["status"] = "processing"
                item["attempts"] += 1
                selected.append(item["candidate"])
                if len(selected) >= limit:
                    break
        return selected

    def mark_done(self, url):
        self.data["items"][url]["status"] = "done"

    def mark_failed(self, url, error):
        self.data["items"][url]["status"] = "failed"

    def record_discovery_error(self, *args):
        self.data["errors"].append(args)

    def stats(self, source):
        return {status: sum(x["status"] == status for x in self.data["items"].values())
                for status in ("pending", "processing", "done", "failed")}


def candidate(number):
    return SimpleNamespace(source="yahoo_finance", url=f"https://finance.yahoo.com/news/{number}.html", title=f"News {number}", organization="Yahoo")


class Client:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.urls = []
        self.denied = set()
        self.errors = set()

    def assert_robots_allowed(self, url):
        if url in self.denied:
            raise RuntimeError("robots denied")

    def get_text(self, url):
        self.urls.append(url)
        if url in self.errors:
            raise RuntimeError("network failed")
        return url


class Source:
    minimum_content_chars = 120

    def __init__(self, client):
        self.client = client

    def parse_article(self, html, item):
        return SimpleNamespace(source=item.source, url=item.url, title=item.title,
                               content="A" * 200, organization=item.organization,
                               published_at="2026-09-14T10:00:00+00:00",
                               crawled_at="2026-09-15T01:00:00+00:00")


class Outbox:
    def __init__(self):
        self.events = []
        self.existing = set()
        self.fail = False

    def seen_url(self, url):
        return url in self.existing

    def enqueue(self, event):
        if self.fail:
            raise OSError("outbox disk full")
        self.events.append(event)
        self.existing.add(event["url"])
        return True


class CollectorTests(unittest.TestCase):
    def setUp(self):
        MemoryQueue.databases = {}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.outbox = Outbox()
        self.pages = {}
        self.default_candidates = []

        def parse(html, url):
            return self.pages.get(url, SimpleNamespace(candidates=self.default_candidates, next_url=None))

        modules = (
            SimpleNamespace(BrowserHttpClient=Client, SOURCE_CLASSES={"yahoo": Source}),
            SimpleNamespace(HistoryQueue=MemoryQueue, parse_yahoo_archive_page=parse),
        )
        loader = patch.object(overseas, "_load_crawler", return_value=modules)
        loader.start()
        self.addCleanup(loader.stop)
        clock = patch.object(overseas, "_today", return_value=date(2026, 9, 15))
        clock.start()
        self.addCleanup(clock.stop)
        envelope = patch.object(overseas, "make_event", side_effect=lambda **kwargs: kwargs)
        envelope.start()
        self.addCleanup(envelope.stop)
        self.collector = overseas.OverseasCollector("unused", self.temp.name, self.outbox)

    def queue(self, name="latest.db"):
        return MemoryQueue(Path(self.temp.name) / name)

    def test_latest_retains_bounds_and_does_not_redownload_seen_articles(self):
        self.default_candidates = [candidate(i) for i in range(10)]
        self.outbox.existing.add(candidate(0).url)
        result = self.collector.collect_latest(limit=3)
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["existing"], 1)
        self.assertEqual(result["enqueued"], 2)
        self.assertEqual(result["queue"]["pending"], 7)
        self.assertNotIn(candidate(0).url, self.collector.client.urls)
        self.assertTrue(all(event["region"] == "overseas" for event in self.outbox.events))
        self.assertEqual(self.collector.client.options["request_delay"], 4.5)

    def test_outbox_failure_releases_whole_claim_without_exhausting_retries(self):
        self.default_candidates = [candidate(i) for i in range(3)]
        self.outbox.fail = True
        with self.assertRaisesRegex(OSError, "disk full"):
            self.collector.collect_latest(limit=3)
        self.assertEqual(self.queue().stats("yahoo_finance")["pending"], 3)
        self.assertTrue(all(item["attempts"] == 0 for item in self.queue().data["items"].values()))
        self.outbox.fail = False
        result = self.collector.collect_latest(limit=3)
        self.assertEqual(result["enqueued"], 3)

    def test_discovery_failure_does_not_advance_or_complete_gap(self):
        url = "https://finance.yahoo.com/sitemap/2026_09_15"
        self.collector.client.errors.add(url)
        result = self.collector.collect_latest()
        self.assertEqual(result["discovery_errors"], 1)
        self.assertEqual(result["cursor_date"], "2026-09-15")
        self.assertFalse(result["complete"])

    def test_backfill_round_robin_state_survives_reinstantiation(self):
        first = self.collector.collect_backfill(start_year=2024, end_year=2026)
        restarted = overseas.OverseasCollector("unused", self.temp.name, self.outbox)
        second = restarted.collect_backfill(start_year=2024, end_year=2026)
        third = restarted.collect_backfill(start_year=2024, end_year=2026)
        self.assertEqual([first["year"], second["year"], third["year"]], [2024, 2025, 2026])
        self.assertEqual(first["discovery_pages"], 3)
        self.assertIn(str(Path(self.temp.name) / "backfill-2024.db"), MemoryQueue.databases)

    def test_latest_gap_and_rolling_have_separate_cursors(self):
        first = self.collector.collect_latest()
        second = self.collector.collect_latest()
        third = self.collector.collect_latest()
        self.assertEqual([first["mode"], second["mode"], third["mode"]], ["gap", "rolling", "gap"])
        self.assertEqual(first["cursor_date"], "2026-09-12")
        self.assertEqual(second["cursor_date"], "2026-09-12")
        self.assertEqual(third["cursor_date"], "2026-09-09")

    def test_robots_failure_is_observable_and_not_done(self):
        self.default_candidates = [candidate(1)]
        self.collector.client.denied.add(candidate(1).url)
        for _ in range(3):
            result = self.collector.collect_latest(limit=1)
        self.assertEqual(result["queue"]["exhausted"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(self.outbox.events, [])
        self.assertNotIn(candidate(1).url, self.collector.client.urls)

    def test_archive_next_page_is_saved_for_next_bounded_call(self):
        url = "https://finance.yahoo.com/sitemap/2026_09_15"
        next_url = url + "_start1"
        self.pages[url] = SimpleNamespace(candidates=[candidate(1)], next_url=next_url)
        self.collector.collect_latest(limit=1)
        self.assertEqual(self.queue().get_state("yahoo_finance", "gap_url"), next_url)
        self.collector.collect_latest(limit=1)  # rolling turn
        self.collector.collect_latest(limit=1)  # resumes gap
        self.assertIn(next_url, self.collector.client.urls)


if __name__ == "__main__":
    unittest.main()
