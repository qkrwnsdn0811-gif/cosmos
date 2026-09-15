"""Crash/retry tests for domestic collection; no network or child processes."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.news_pipeline import collector as collector_module
from services.news_pipeline.collector import DomesticCollector
from services.news_pipeline.common import Outbox


class DomesticCollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.outbox = Outbox(self.root / "outbox.db")
        self.collector = DomesticCollector(self.root, self.outbox, "node", "adapter.mjs")
        self.sleep = patch.object(collector_module.time, "sleep", return_value=None)
        self.sleep.start()
        self.stop = patch.object(collector_module, "STOP", False)
        self.stop.start()

    def tearDown(self):
        self.collector.db.close()
        self.outbox.close()
        self.stop.stop()
        self.sleep.stop()
        self.directory.cleanup()

    def candidate(self, number=100, source="kpenews"):
        return {"url": f"https://kpenews.com/View.aspx?No={number}", "title": "발견 제목",
                "source": source, "organization": "한국정경신문"}

    def article(self, candidate=None):
        candidate = candidate or self.candidate()
        return {"status": "ok", **candidate, "content": "국내 산업 뉴스 본문입니다. " * 20,
                "published_at": None, "final_url": candidate["url"], "region": "domestic",
                "language": "ko", "truncated": False, "robots": {"status": "allowed"}}

    def page(self, candidates=None, cursor="next-page"):
        return {"status": "ok", "candidates": candidates if candidates is not None else [self.candidate()],
                "next_cursor": cursor, "done": cursor is None}

    def select_source(self, index=0):
        with self.collector.db:
            self.collector.db.execute("INSERT OR REPLACE INTO state VALUES ('source_index',?)", (str(index),))

    def insert_candidate(self, candidate=None):
        candidate = candidate or self.candidate()
        with self.collector.db:
            self.collector.db.execute("INSERT INTO candidates (url,source,title,organization) VALUES (?,?,?,?)",
                                      (candidate["url"], candidate["source"], candidate["title"], candidate["organization"]))

    def restart_collector(self):
        self.collector.db.close()
        self.collector = DomesticCollector(self.root, self.outbox, "node", "adapter.mjs")

    def test_candidates_and_cursor_are_committed_before_fetch(self):
        candidates = [self.candidate(100), self.candidate(101)]

        def invoke(mode, source, **kwargs):
            if mode == "discover":
                return self.page(candidates)
            with closing(sqlite3.connect(self.root / "domestic.db")) as independent:
                self.assertEqual(independent.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 2)
                self.assertEqual(independent.execute("SELECT value FROM state WHERE key='cursor:kpenews'").fetchone()[0], "next-page")
            return self.article(candidates[0])

        with patch.object(self.collector, "invoke", side_effect=invoke):
            result = self.collector.collect(limit=1)
        self.assertEqual(result["enqueued"], 1)
        self.assertEqual(self.outbox.stats()["pending"], 1)
        self.assertEqual(self.collector.db.execute("SELECT COUNT(*) FROM candidates WHERE status='pending'").fetchone()[0], 1)

    def test_bad_candidate_rolls_back_whole_discovery_page_and_cursor(self):
        with self.collector.db:
            self.collector.db.execute("INSERT INTO state VALUES ('cursor:kpenews','original-page')")
        with patch.object(self.collector, "invoke", return_value=self.page([self.candidate(), {"title": "missing URL"}])):
            with self.assertRaises((KeyError, ValueError, RuntimeError)):
                self.collector.collect(limit=1)
        self.assertEqual(self.collector.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)
        self.assertEqual(self.collector.get_state("cursor:kpenews"), "original-page")
        self.assertEqual(self.outbox.stats()["pending"], 0)

    def test_outbox_storage_failure_keeps_candidate_retryable(self):
        with patch.object(self.collector, "invoke", side_effect=[self.page(), self.article()]), \
                patch.object(self.outbox, "enqueue", side_effect=OSError("temporary local storage error")):
            with self.assertRaises(OSError):
                self.collector.collect(limit=1)
        row = self.collector.db.execute("SELECT * FROM candidates").fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["attempts"], 0)
        self.assertEqual(self.outbox.stats()["pending"], 0)
        self.select_source()
        with self.collector.db:
            self.collector.db.execute("UPDATE candidates SET next_try=0")
        with patch.object(self.collector, "invoke", return_value=self.article()) as invoke:
            second = self.collector.collect(limit=1)
        self.assertEqual(second["enqueued"], 1)
        self.assertEqual(invoke.call_args.args[0], "fetch")
        self.assertEqual(self.collector.db.execute("SELECT status FROM candidates").fetchone()[0], "done")
        self.assertEqual(self.outbox.stats()["pending"], 1)

    def test_interruption_after_outbox_commit_recovers_without_fetch_or_duplicate(self):
        real_enqueue = self.outbox.enqueue

        def interrupted_enqueue(event):
            real_enqueue(event)
            raise KeyboardInterrupt("crash before candidate done commit")

        with patch.object(self.collector, "invoke", side_effect=[self.page(), self.article()]), \
                patch.object(self.outbox, "enqueue", side_effect=interrupted_enqueue):
            with self.assertRaises(KeyboardInterrupt):
                self.collector.collect(limit=1)
        self.assertEqual(self.collector.db.execute("SELECT status FROM candidates").fetchone()[0], "processing")
        self.assertEqual(self.outbox.stats()["pending"], 1)
        self.restart_collector()
        self.assertEqual(self.collector.db.execute("SELECT attempts FROM candidates").fetchone()[0], 0)
        self.select_source()
        with patch.object(self.collector, "invoke", side_effect=AssertionError("already durable article must not be fetched")):
            resumed = self.collector.collect(limit=1)
        self.assertEqual(resumed["existing"], 1)
        self.assertEqual(self.collector.db.execute("SELECT status FROM candidates").fetchone()[0], "done")
        self.assertEqual(self.outbox.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)

    def test_interruption_during_fetch_resets_processing_and_retries(self):
        with patch.object(self.collector, "invoke", side_effect=[self.page(), KeyboardInterrupt("child interrupted")]):
            with self.assertRaises(KeyboardInterrupt):
                self.collector.collect(limit=1)
        self.assertEqual(self.outbox.stats()["pending"], 0)
        self.restart_collector()
        self.select_source()
        with patch.object(self.collector, "invoke", return_value=self.article()):
            resumed = self.collector.collect(limit=1)
        self.assertEqual(resumed["enqueued"], 1)
        self.assertEqual(self.collector.db.execute("SELECT status FROM candidates").fetchone()[0], "done")

    def test_failed_kafka_delivery_keeps_body_durable_for_retry(self):
        with patch.object(self.collector, "invoke", side_effect=[self.page(), self.article()]):
            self.collector.collect(limit=1)

        class Producer:
            def __init__(self, error):
                self.error, self.callback, self.values = error, None, []

            def produce(self, topic, key, value, on_delivery):
                self.callback = on_delivery
                self.values.append(value)

            def poll(self, timeout):
                pass

            def flush(self, timeout):
                class Message:
                    def partition(self): return 1
                    def offset(self): return 25
                self.callback(self.error, Message())
                return 0

        failed = Producer("broker unavailable")
        self.assertEqual(self.outbox.publish(failed)["errors"], 1)
        self.assertEqual(self.outbox.stats()["pending"], 1)
        self.assertEqual(self.collector.db.execute("SELECT status FROM candidates").fetchone()[0], "done")
        self.outbox.close()
        self.outbox = Outbox(self.root / "outbox.db")
        self.collector.outbox = self.outbox
        recovered = Producer(None)
        self.assertEqual(self.outbox.publish(recovered)["acknowledged"], 1)
        self.assertEqual(recovered.values, failed.values)
        self.assertEqual(self.outbox.stats()["pending"], 0)
        payload = json.loads(recovered.values[0])
        self.assertEqual(payload["region"], "domestic")
        self.assertIsNone(payload["published_at"])

    def test_all_sources_rotate_even_when_first_discovery_fails(self):
        called = []

        def invoke(mode, source, **kwargs):
            called.append(source)
            if source == "kpenews":
                raise RuntimeError("sitemap unavailable")
            return self.page([], None)

        with patch.object(self.collector, "invoke", side_effect=invoke):
            failed = self.collector.collect(limit=1)
            self.assertIn("discovery_error", failed)
            for _ in range(5):
                self.collector.collect(limit=1)
        self.assertEqual(called, list(DomesticCollector.SOURCES))
        self.assertEqual(self.collector.get_state("source_index"), "0")

    def test_sitemap_failure_does_not_starve_existing_pending_article(self):
        self.insert_candidate()

        def invoke(mode, source, **kwargs):
            if mode == "discover":
                raise RuntimeError("sitemap temporarily unavailable")
            return self.article()

        with patch.object(self.collector, "invoke", side_effect=invoke):
            result = self.collector.collect(limit=8)
        self.assertEqual(result["enqueued"], 1)
        self.assertEqual(self.collector.db.execute("SELECT status FROM candidates").fetchone()[0], "done")

    def test_source_url_identity_and_extraction_provenance_are_preserved(self):
        candidate = self.candidate()
        article = self.article(candidate)
        article.update(content="짧은 본문 기사 검증 " * 7, final_url="https://kpenews.com/View.aspx?No=101",
                       truncated=True, external_id="100", extraction_method="json-ld")
        self.assertGreaterEqual(len(article["content"].strip()), 80)
        self.assertLess(len(article["content"].strip()), 100)
        with patch.object(self.collector, "invoke", side_effect=[self.page(), article]):
            result = self.collector.collect(limit=1)
        self.assertEqual(result["enqueued"], 1)
        payload = json.loads(self.outbox.db.execute("SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(payload["url"], candidate["url"])
        self.assertEqual(payload["metadata"]["final_url"], article["final_url"])
        self.assertEqual(payload["metadata"]["robots"], {"status": "allowed"})
        self.assertTrue(payload["metadata"]["truncated"])
        self.assertEqual(payload["metadata"]["external_id"], "100")
        self.assertEqual(payload["metadata"]["extraction_method"], "json-ld")
        self.assertIsNone(payload["published_at"])


if __name__ == "__main__":
    unittest.main()
