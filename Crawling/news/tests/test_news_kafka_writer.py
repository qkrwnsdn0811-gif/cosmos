import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("news_writer", Path(__file__).resolve().parents[1] /
                                            "services/news_pipeline/writer.py")
writer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(writer)


def article(region="overseas", **changes):
    value = {"schema_version": 1, "event_id": "a", "source": "yahoo", "region": region,
             "language": "en", "url": "https://example.com/news", "title": "기사 🌍",
             "content": "본문\nsecond line", "organization": "publisher", "published_at": None,
             "collected_at": "2026-09-15T09:01:02+09:00", "run_id": "run-1"}
    value.update(changes)
    return writer.encoded(value)


class MemoryStore:
    def __init__(self):
        self.files, self.directories = {}, set()
        self.fail_before_rename = self.fail_after_rename = False

    def exists(self, path):
        return path in self.files or path in self.directories

    def mkdir(self, path):
        self.directories.add(path)
        self.directories.update(str(parent) for parent in Path(path).parents if str(parent) != ".")

    def write(self, path, payload):
        self.files[path] = payload

    def read(self, path):
        return self.files[path]

    def size(self, path):
        return len(self.files[path])

    def children(self, path):
        return [item for item in self.files.keys() | self.directories
                if item.startswith(path + "/") and "/" not in item[len(path) + 1:]]

    def rename_new(self, source, destination):
        if self.fail_before_rename:
            raise RuntimeError("before rename")
        if self.exists(destination):
            raise FileExistsError(destination)
        self.files.update({destination + name[len(source):]: payload
                           for name, payload in list(self.files.items()) if name.startswith(source + "/")})
        self.files = {name: payload for name, payload in self.files.items() if not name.startswith(source + "/")}
        self.directories.update(destination + name[len(source):]
                                for name in list(self.directories) if name == source or name.startswith(source + "/"))
        self.directories = {name for name in self.directories if name != source and not name.startswith(source + "/")}
        if self.fail_after_rename:
            raise RuntimeError("acknowledgement lost after rename")


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.sink = writer.BatchSink(self.store, "/data-lake/raw/realtime/news", "news.raw", "group", "cluster",
                                     encode_parquet=lambda rows: writer.encoded(rows), topic_id="topic-id")
        self.commits, self.dlq = [], []
        self.core = writer.Writer(self.sink, lambda p, o: self.commits.append((p, o)), self.dlq.append)
        self.core.recover(0, 0, 20, -1001)

    def test_one_atomic_batch_routes_regions_and_keeps_raw_metadata(self):
        manifest = self.core.flush(0, [(0, article()), (1, article("domestic"))])
        self.assertEqual(self.commits, [(0, 2)])
        self.assertEqual(manifest["records"], 2)
        self.assertEqual({row["path"] for row in manifest["files"]}, {
            "region=domestic/date=2026-09-15/part.parquet", "region=overseas/date=2026-09-15/part.parquet"})
        destination = self.sink.destination(0, 0)
        rows = json.loads(self.store.read(destination + "/region=overseas/date=2026-09-15/part.parquet"))
        self.assertEqual(rows[0]["kafka_offset"], 0)
        self.assertEqual(rows[0]["event_id"], "a")
        self.assertEqual(rows[0]["collected_at"], "2026-09-15T00:01:02+00:00")
        self.assertEqual(json.loads(rows[0]["raw_json"])["content"], "본문\nsecond line")

    def test_failure_before_rename_never_commits_or_exposes_partial_batch(self):
        self.store.fail_before_rename = True
        with self.assertRaises(RuntimeError):
            self.core.flush(0, [(0, article())])
        self.assertEqual(self.commits, [])
        self.assertFalse(self.store.exists(self.sink.destination(0, 0)))
        self.assertEqual(self.sink.recover(0)[0], 0)
        self.store.fail_before_rename = False
        self.core.flush(0, [(0, article()), (1, article())])
        self.assertEqual(self.sink.recover(0)[0], 2)

    def test_rename_ack_lost_recovers_exact_previous_end_before_new_batch(self):
        self.store.fail_after_rename = True
        with self.assertRaises(RuntimeError):
            self.core.flush(0, [(0, article()), (1, article())])
        self.assertEqual(self.commits, [])
        self.store.fail_after_rename = False
        restarted = writer.Writer(self.sink, lambda p, o: self.commits.append((p, o)), self.dlq.append)
        self.assertEqual(restarted.recover(0, 0, 20, -1001), 2)
        restarted.flush(0, [(2, article())])
        self.assertEqual(self.commits, [(0, 2), (0, 3)])
        self.assertEqual([m["records"] for m in self.sink.recover(0)[1]], [2, 1])

    def test_kafka_commit_failure_recovers_without_duplicate_data(self):
        def failing_commit(*_):
            raise RuntimeError("broker commit failed")
        self.core.commit = failing_commit
        with self.assertRaises(RuntimeError):
            self.core.flush(0, [(0, article())])
        self.assertEqual(self.sink.recover(0)[0], 1)
        self.core.commit = lambda p, o: self.commits.append((p, o))
        self.assertEqual(self.core.recover(0, 0, 20, -1001), 1)
        self.assertEqual(self.commits, [(0, 1)])
        self.assertEqual(len(self.sink.recover(0)[1]), 1)

    def test_invalid_original_is_durable_before_dlq_and_offset_commit(self):
        events = []
        def emit(value):
            self.assertTrue(self.store.exists(self.sink.destination(0, 0)))
            events.append("dlq")
            self.dlq.append(value)
        self.core.send_dlq = emit
        self.core.commit = lambda *_: events.append("commit")
        manifest = self.core.flush(0, [(0, b"\xffbad json"), (1, article())])
        self.assertEqual(events, ["dlq", "commit"])
        self.assertEqual(manifest["invalid_records"], 1)
        self.assertEqual(self.dlq[0]["event_id"], "news.raw:0:0")
        original = self.sink.quarantine(manifest)[0]
        self.assertEqual(writer.base64.b64decode(original["raw_base64"]), b"\xffbad json")
        self.assertNotIn("raw_base64", self.dlq[0])
        self.assertTrue(self.dlq[0]["quarantine_uri"].startswith("hdfs:///data-lake/"))

    def test_large_invalid_original_uses_small_dlq_notice_and_preserves_all_bytes(self):
        raw = b"x" * 900_000
        manifest = self.core.flush(0, [(0, raw)])
        self.assertLess(len(writer.encoded(self.dlq[0])), 2048)
        self.assertEqual(self.dlq[0]["raw_bytes"], len(raw))
        self.assertEqual(self.dlq[0]["raw_sha256"], writer.sha256(raw))
        self.assertEqual(writer.base64.b64decode(self.sink.quarantine(manifest)[0]["raw_base64"]), raw)
        self.assertEqual(self.commits, [(0, 1)])

    def test_json_escaped_lone_surrogate_is_quarantined_without_poisoning_partition(self):
        value = json.loads(article())
        value["content"] = "invalid surrogate: \ud800"
        raw = json.dumps(value, ensure_ascii=True).encode()
        manifest = self.core.flush(0, [(0, raw), (1, article())])
        self.assertEqual(manifest["invalid_records"], 1)
        self.assertEqual(manifest["valid_records"], 1)
        self.assertEqual(self.dlq[0]["error_type"], "UnicodeEncodeError")
        self.assertEqual(self.commits, [(0, 2)])

    def test_deeply_nested_json_is_quarantined_without_poisoning_partition(self):
        raw = b"[" * 4000 + b"0" + b"]" * 4000
        manifest = self.core.flush(0, [(0, raw), (1, article())])
        self.assertEqual(manifest["invalid_records"], 1)
        self.assertEqual(manifest["valid_records"], 1)
        # Decoder recursion limits differ between Python versions/platforms;
        # either rejection must preserve the original and advance only durably.
        self.assertIn(self.dlq[0]["error_type"], {"RecursionError", "ValueError"})
        self.assertEqual(self.commits, [(0, 2)])

    def test_dlq_failure_does_not_commit_and_restart_retries_quarantined_original(self):
        def fail(_):
            raise RuntimeError("DLQ unavailable")
        self.core.send_dlq = fail
        with self.assertRaises(RuntimeError):
            self.core.flush(0, [(0, b"not json")])
        self.assertEqual(self.commits, [])
        self.core.send_dlq = self.dlq.append
        self.assertEqual(self.core.recover(0, 0, 20, -1001), 1)
        self.assertEqual(len(self.dlq), 1)
        self.assertEqual(self.commits, [(0, 1)])
        self.core.recover(0, 0, 20, 1)
        self.assertEqual(len(self.dlq), 1)

    def test_retention_gap_and_external_commit_ahead_fail_closed(self):
        for values in ((0, 1, 20, -1001), (1, 0, 0, 0), (0, 0, 20, 4)):
            with self.assertRaises(RuntimeError):
                writer.check_start(*values)

    def test_existing_batch_cannot_be_replaced_by_different_retry_boundary(self):
        self.core.flush(0, [(0, article())])
        before = dict(self.store.files)
        with self.assertRaises(RuntimeError):
            self.sink.publish(0, 0, [(0, article()), (1, article())])
        self.assertEqual(self.store.files, before)

    def test_missing_payload_and_manifest_gap_fail_recovery(self):
        self.core.flush(0, [(0, article())])
        path = self.sink.destination(0, 0) + "/region=overseas/date=2026-09-15/part.parquet"
        del self.store.files[path]
        with self.assertRaises(RuntimeError):
            self.sink.recover(0)

    def test_schema_validation_and_fixed_utc_date(self):
        for payload in (article(region="../outside"), article(collected_at="2026-09-15T09:00:00"),
                        article(schema_version=2), article(content="")):
            with self.assertRaises(ValueError):
                writer.normalize(payload, "news.raw", 0, 0)
        row, day = writer.normalize(article(collected_at="2026-09-15T01:00:00+09:00"), "news.raw", 0, 0)
        self.assertEqual(day, "2026-09-14")

    def test_legacy_datasets_and_noncanonical_roots_are_rejected(self):
        for root in ("/datasets/news/live/mysql_overseas", "/datasets/news/snapshots/old",
                     "/data-lake/raw/realtime/news/../old", "/data-lake/raw/realtime/news-evil"):
            with self.assertRaises(ValueError):
                writer.BatchSink(self.store, root, "news.raw", "g", "c", topic_id="topic-id")

    def test_noncontiguous_input_never_publishes(self):
        with self.assertRaises(RuntimeError):
            self.core.flush(0, [(0, article()), (2, article())])
        self.assertFalse(self.store.exists(self.sink.destination(0, 0)))

    def test_changed_cluster_cannot_reuse_old_offset_manifests(self):
        self.core.flush(0, [(0, article())])
        other = writer.BatchSink(self.store, "/data-lake/raw/realtime/news", "news.raw", "group", "new-cluster",
                                 topic_id="topic-id")
        with self.assertRaises(RuntimeError):
            other.recover(0)

    def test_recreated_topic_in_same_cluster_cannot_reuse_manifests(self):
        self.core.flush(0, [(0, article())])
        other = writer.BatchSink(self.store, "/data-lake/raw/realtime/news", "news.raw", "group", "cluster",
                                 topic_id="new-topic-id")
        with self.assertRaises(RuntimeError):
            other.recover(0)

    def test_sigterm_after_jvm_initialization_stops_run_cleanly(self):
        handlers, lifecycle = {}, []

        def install(signum, handler):
            handlers[signum] = handler
            lifecycle.append("python_handler_installed")

        def start_jvm(*args, **kwargs):
            # PyArrow otherwise defaults to replication=3 even when Hadoop's
            # dfs.replication configuration is 2; that would change storage cost.
            self.assertEqual(kwargs.get("replication"), 2)
            lifecycle.append("jvm_started")
            # Reproduce JNI/JVM replacing native handlers that Python registered
            # earlier. A poll-triggered SIGTERM must find the new Python handler.
            handlers[writer.signal.SIGTERM] = "jvm_native_handler"
            handlers[writer.signal.SIGINT] = "jvm_native_handler"
            return object()

        class TopicPartition:
            def __init__(self, topic, partition, offset=-1001):
                self.topic, self.partition, self.offset = topic, partition, offset

        class Consumer:
            def __init__(self, config):
                pass

            def list_topics(self, **kwargs):
                return SimpleNamespace(cluster_id="cluster", topics={"news.raw": SimpleNamespace(
                    error=None, partitions={0: None, 1: None, 2: None})})

            def committed(self, partitions, **kwargs):
                return partitions

            def get_watermark_offsets(self, *args, **kwargs):
                return (0, 0)

            def assign(self, partitions):
                lifecycle.append("assigned")

            def poll(self, timeout):
                lifecycle.append("sigterm_received")
                handlers[writer.signal.SIGTERM](writer.signal.SIGTERM, None)
                return None

            def close(self):
                lifecycle.append("consumer_closed")

        class Admin:
            def __init__(self, config):
                pass

            def describe_topics(self, *args, **kwargs):
                return {"news.raw": SimpleNamespace(result=lambda: SimpleNamespace(topic_id="topic-id"))}

        kafka = ModuleType("confluent_kafka")
        kafka.Consumer, kafka.Producer, kafka.TopicPartition = Consumer, lambda config: object(), TopicPartition
        kafka.TopicCollection, kafka.KafkaError = lambda values: values, SimpleNamespace(_PARTITION_EOF=-191)
        admin = ModuleType("confluent_kafka.admin")
        admin.AdminClient = Admin
        arrow = ModuleType("pyarrow")
        arrow.__path__ = []
        arrow_fs = ModuleType("pyarrow.fs")
        arrow_fs.HadoopFileSystem = start_jvm
        modules = {"confluent_kafka": kafka, "confluent_kafka.admin": admin,
                   "pyarrow": arrow, "pyarrow.fs": arrow_fs}
        with tempfile.TemporaryDirectory() as folder, patch.dict(writer.sys.modules, modules), \
                patch.object(writer.signal, "signal", side_effect=install), \
                patch.object(writer, "ArrowStore", return_value=self.store):
            args = SimpleNamespace(bootstrap="localhost:9092", group="group", topic="news.raw",
                dlq_topic="pipeline.dlq", hdfs_root="/data-lake/raw/realtime/news", state_dir=folder,
                expected_partitions=3, batch_records=10, flush_seconds=1, max_bytes=1024, max_records=None)
            self.assertEqual(writer.run(args), 0)
            status = json.loads((Path(folder) / "status.json").read_text())
            self.assertEqual(status["status"], "stopped")
        self.assertLess(lifecycle.index("jvm_started"), lifecycle.index("python_handler_installed"))
        self.assertIn("sigterm_received", lifecycle)
        self.assertEqual(lifecycle[-1], "consumer_closed")

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "requires deployment pyarrow")
    def test_actual_parquet_roundtrip(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        row, _ = writer.normalize(article(), "news.raw", 2, 42)
        data = writer.parquet_bytes([row])
        restored = pq.read_table(pa.BufferReader(data)).to_pylist()
        self.assertEqual(restored[0], row)


if __name__ == "__main__":
    unittest.main()
