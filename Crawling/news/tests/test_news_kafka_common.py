import json
import tempfile
import unittest
from pathlib import Path
from services.news_pipeline.common import Outbox, make_event


def event(url="https://example.org/news?id=1"):
    return make_event(source="test", region="domestic", language="ko", url=url,
                      title="Article", content="Article body. " * 30)


class Message:
    def partition(self): return 1
    def offset(self): return 7


class Producer:
    def __init__(self, fail=False): self.fail = fail
    def produce(self, topic, key, value, on_delivery):
        on_delivery("unavailable" if self.fail else None, Message())
    def poll(self, timeout): pass
    def flush(self, timeout): return 0


class OutboxTest(unittest.TestCase):
    def test_durable_acceptance_and_delivery_retry(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "outbox.db"
            store = Outbox(path)
            self.assertTrue(store.enqueue(event()))
            store.close()
            store = Outbox(path)
            self.assertTrue(store.seen_url(event()["url"]))
            self.assertFalse(store.enqueue(event()))
            self.assertEqual(store.publish(Producer(True))["errors"], 1)
            self.assertEqual(store.stats()["pending"], 1)
            self.assertEqual(store.publish(Producer())["acknowledged"], 1)
            self.assertEqual(store.stats()["pending"], 0)
            row = store.db.execute("SELECT * FROM outbox").fetchone()
            self.assertEqual(row["kafka_offset"], 7)
            self.assertEqual(json.loads(row["payload"])["content"], event()["content"])
            store.close()

    def test_domestic_query_identifiers_are_distinct(self):
        self.assertNotEqual(event()["event_id"], event("https://example.org/news?id=2")["event_id"])

    def test_oversize_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            make_event(source="x", region="domestic", language="ko", url="https://example.org/a", title="a", content="가" * 400000)

    def test_unknown_publication_timezone_preserved_without_poisoning_writer(self):
        value = make_event(source="x", region="overseas", language="en", url="https://example.org/a",
                           title="a", content="body " * 50, published_at="2026-09-15 12:00:00")
        self.assertIsNone(value["published_at"])
        self.assertEqual(value["metadata"]["published_at_original"], "2026-09-15 12:00:00")
        self.assertTrue(value["run_id"])


if __name__ == "__main__": unittest.main()
