"""Singleton Kafka news consumer with immutable, atomic HDFS batch publication.

HDFS manifests, rather than Kafka's group offset alone, are the durable checkpoint.
Only this one service may write its root; a local flock enforces that on the Master.
Each batch directory is named by its first offset and contains all region/date
files and quarantined messages. Readers must read only final ``start=*`` folders,
not hidden staging folders. Article/event deduplication across distinct Kafka
offsets remains a downstream operation; delivery replay never repeats an offset.
"""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import time
import uuid


VERSION = 1
TEXT_FIELDS = ("event_id", "source", "region", "language", "url", "title", "content",
               "organization", "published_at", "collected_at", "run_id")


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_requires_timezone")
    return parsed.astimezone(timezone.utc)


def normalize(raw, topic, partition, offset):
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != VERSION:
        raise ValueError("unsupported_schema")
    for name in TEXT_FIELDS:
        field = value.get(name)
        if name == "published_at" and field is None:
            continue
        if not isinstance(field, str):
            raise ValueError("invalid_field_" + name)
        # JSON escape sequences can represent lone surrogates even when the
        # original Kafka bytes are valid UTF-8. Detect them inside the validation
        # boundary so they are quarantined before Arrow attempts string encoding.
        field.encode("utf-8")
    for name in ("event_id", "source", "url", "title", "content", "run_id"):
        if not value[name].strip():
            raise ValueError("empty_field_" + name)
    if value["region"] not in ("domestic", "overseas"):
        raise ValueError("invalid_region")
    if not value["url"].startswith(("https://", "http://")):
        raise ValueError("invalid_url")
    collected = parse_time(value["collected_at"])
    published = parse_time(value.get("published_at"), nullable=True)
    result = {name: value.get(name) for name in TEXT_FIELDS}
    result.update(schema_version=VERSION, collected_at=collected.isoformat(),
                  published_at=published.isoformat() if published else None,
                  kafka_topic=topic, kafka_partition=partition, kafka_offset=offset,
                  raw_json=raw.decode("utf-8"))
    return result, collected.date().isoformat()


def parquet_bytes(rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    fields = [pa.field("schema_version", pa.int32(), nullable=False)]
    fields += [pa.field(name, pa.string(), nullable=name == "published_at") for name in TEXT_FIELDS]
    fields += [pa.field("kafka_topic", pa.string(), nullable=False),
               pa.field("kafka_partition", pa.int32(), nullable=False),
               pa.field("kafka_offset", pa.int64(), nullable=False),
               pa.field("raw_json", pa.string(), nullable=False)]
    schema = pa.schema(fields, metadata={b"pipeline_schema": b"cosmos-news-v1"})
    output = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), output, compression="snappy")
    payload = output.getvalue().to_pybytes()
    if pq.ParquetFile(pa.BufferReader(payload)).metadata.num_rows != len(rows):
        raise RuntimeError("parquet_row_count_mismatch")
    return payload


class ArrowStore:
    """PyArrow HDFS adapter. All paths are within the configured HDFS root."""

    def __init__(self, filesystem):
        self.fs = filesystem

    def exists(self, path):
        from pyarrow.fs import FileType
        return self.fs.get_file_info(path).type != FileType.NotFound

    def mkdir(self, path):
        self.fs.create_dir(path, recursive=True)

    def write(self, path, data):
        with self.fs.open_output_stream(path) as stream:
            stream.write(data)

    def read(self, path):
        with self.fs.open_input_stream(path) as stream:
            return stream.read()

    def size(self, path):
        return self.fs.get_file_info(path).size

    def children(self, path):
        from pyarrow.fs import FileSelector
        return [item.path for item in self.fs.get_file_info(FileSelector(path, recursive=False))]

    def rename_new(self, source, destination):
        # The singleton lock is a required precondition: pyarrow's move API has
        # replacement semantics for some target types, so never pass an existing
        # destination. Independent writers on another host are unsupported.
        if self.exists(destination):
            raise FileExistsError("immutable_destination_exists")
        self.fs.move(source, destination)


class BatchSink:
    def __init__(self, store, root, topic, group, cluster_id, encode_parquet=parquet_bytes, topic_id=None):
        if (not root.startswith("/data-lake/raw/realtime/news") or
                str(PurePosixPath(root)) != root or ".." in PurePosixPath(root).parts or
                not (root == "/data-lake/raw/realtime/news" or root.startswith("/data-lake/raw/realtime/news/"))):
            raise ValueError("root_must_be_under_new_news_raw_tree")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", topic):
            raise ValueError("unsafe_topic_name")
        if not cluster_id or not topic_id:
            raise ValueError("kafka_cluster_and_topic_ids_required")
        self.store, self.root, self.topic, self.group = store, root, topic, group
        self.cluster_id, self.encode_parquet = cluster_id, encode_parquet
        self.topic_id = topic_id
        self.base = root + "/topic=" + topic
        self.store.mkdir(self.base)

    def parent(self, partition):
        return self.base + "/partition=" + str(partition)

    def destination(self, partition, start):
        return self.parent(partition) + f"/start={start:020d}"

    def manifest(self, directory, verify_hashes=False):
        manifest = json.loads(self.store.read(directory + "/_manifest.json"))
        if (manifest.get("version") != VERSION or manifest.get("topic") != self.topic or
                manifest.get("group") != self.group or manifest.get("cluster_id") != self.cluster_id or
                manifest.get("topic_id") != self.topic_id):
            raise RuntimeError("incompatible_batch_identity")
        partition, start, end = (manifest.get(key) for key in ("partition", "start", "end"))
        if (not all(isinstance(x, int) and x >= 0 for x in (partition, start, end)) or
                end <= start or directory != self.destination(partition, start)):
            raise RuntimeError("invalid_batch_range")
        if manifest.get("records") != end - start:
            raise RuntimeError("incomplete_batch_offset_range")
        entries = manifest.get("files")
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("empty_batch_inventory")
        count, seen = 0, set()
        for entry in entries:
            relative = entry.get("path", "")
            parts = PurePosixPath(relative)
            if (not relative or parts.is_absolute() or ".." in parts.parts or "\\" in relative
                    or str(parts) != relative or relative in seen or relative.startswith("_")):
                raise RuntimeError("unsafe_batch_inventory")
            seen.add(relative)
            file_path = directory + "/" + relative
            if not self.store.exists(file_path) or self.store.size(file_path) != entry["bytes"]:
                raise RuntimeError("missing_or_incomplete_batch_file")
            if verify_hashes and sha256(self.store.read(file_path)) != entry["sha256"]:
                raise RuntimeError("batch_digest_mismatch")
            count += entry["records"]
        if count != manifest["records"]:
            raise RuntimeError("batch_inventory_record_count_mismatch")
        return manifest

    def recover(self, partition):
        parent = self.parent(partition)
        self.store.mkdir(parent)
        next_offset, manifests = 0, []
        for directory in sorted(self.store.children(parent)):
            name = PurePosixPath(directory).name
            if name.startswith("."):
                continue
            if not re.fullmatch(r"start=\d{20}", name):
                raise RuntimeError("unexpected_final_batch_path")
            manifest = self.manifest(directory)
            if manifest["partition"] != partition or manifest["start"] != next_offset:
                raise RuntimeError("gap_or_overlap_in_durable_batches")
            next_offset = manifest["end"]
            manifests.append(manifest)
        return next_offset, manifests

    def publish(self, partition, start, records):
        """Publish every offset [start,end), including invalid originals, together."""
        if not records or records[0][0] != start:
            raise ValueError("batch_start_mismatch")
        for index, (offset, _) in enumerate(records):
            if offset != start + index:
                raise RuntimeError("noncontiguous_input_offsets")
        destination = self.destination(partition, start)
        if self.store.exists(destination):
            # A restart must recover before consuming, not overwrite or merge a
            # new differently-sized batch with an already committed start.
            raise RuntimeError("recover_existing_batch_before_consuming")
        staging = self.parent(partition) + "/.staging-" + uuid.uuid4().hex
        self.store.mkdir(staging)
        routed, invalid = defaultdict(list), []
        for offset, raw in records:
            try:
                row, date = normalize(raw, self.topic, partition, offset)
                routed[(row["region"], date)].append(row)
            except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError) as error:
                invalid.append({"event_id": f"{self.topic}:{partition}:{offset}",
                                "source_topic": self.topic, "source_partition": partition,
                                "source_offset": offset, "error_type": type(error).__name__,
                                "raw_bytes": len(raw), "raw_sha256": sha256(raw),
                                "raw_base64": base64.b64encode(raw).decode("ascii")})
        files = []

        def put(relative, payload, count):
            path = staging + "/" + relative
            self.store.mkdir(str(PurePosixPath(path).parent))
            self.store.write(path, payload)
            digest = sha256(payload)
            if self.store.size(path) != len(payload) or sha256(self.store.read(path)) != digest:
                raise RuntimeError("staged_file_verification_failed")
            files.append({"path": relative, "bytes": len(payload), "sha256": digest, "records": count})

        for (region, date), rows in sorted(routed.items()):
            put(f"region={region}/date={date}/part.parquet", self.encode_parquet(rows), len(rows))
        if invalid:
            put("quarantine/invalid.jsonl", b"".join(encoded(row) for row in invalid), len(invalid))
        manifest = {"version": VERSION, "cluster_id": self.cluster_id, "topic_id": self.topic_id, "topic": self.topic,
                    "group": self.group, "partition": partition, "start": start,
                    "end": records[-1][0] + 1, "records": len(records),
                    "valid_records": len(records) - len(invalid), "invalid_records": len(invalid),
                    "files": files, "created_at": utc_now()}
        control = encoded(manifest)
        self.store.write(staging + "/_manifest.json", control)
        if self.store.read(staging + "/_manifest.json") != control:
            raise RuntimeError("staged_manifest_verification_failed")
        # All outputs and the checkpoint become visible in one HDFS namespace
        # operation. If acknowledgement is lost, recover() sees the final folder.
        self.store.rename_new(staging, destination)
        return self.manifest(destination, verify_hashes=True)

    def quarantine(self, manifest):
        if not manifest["invalid_records"]:
            return []
        path = self.destination(manifest["partition"], manifest["start"]) + "/quarantine/invalid.jsonl"
        return [json.loads(line) for line in self.store.read(path).splitlines()]

    def dlq_done(self, manifest):
        path = self.destination(manifest["partition"], manifest["start"]) + "/_dlq_sent"
        return self.store.exists(path) and self.store.read(path) == b"complete\n"

    def mark_dlq_done(self, manifest):
        # Mutable delivery bookkeeping does not alter original data or manifests.
        # An interrupted marker only causes harmless repeated DLQ notifications.
        path = self.destination(manifest["partition"], manifest["start"]) + "/_dlq_sent"
        self.store.write(path, b"complete\n")


def check_start(next_offset, low, high, committed):
    if next_offset < low:
        raise RuntimeError("kafka_retention_gap_before_hdfs_checkpoint")
    if next_offset > high:
        raise RuntimeError("kafka_log_truncated_or_topic_recreated")
    if committed is not None and committed >= 0 and committed > next_offset:
        raise RuntimeError("kafka_commit_ahead_of_durable_hdfs")
    return next_offset


class Writer:
    """Pure orchestration core; adapters make crash boundaries testable."""

    def __init__(self, sink, commit, send_dlq):
        self.sink, self.commit, self.send_dlq = sink, commit, send_dlq
        self.next_offsets = {}

    def complete_dlq(self, manifest):
        if manifest["invalid_records"] and not self.sink.dlq_done(manifest):
            for value in self.sink.quarantine(manifest):
                # The original is already durably quarantined. Base64 expansion
                # must not turn a valid-size source message into an oversized DLQ
                # message that permanently prevents source-offset acknowledgement.
                notice = {name: value[name] for name in ("event_id", "source_topic", "source_partition",
                                                         "source_offset", "error_type", "raw_bytes", "raw_sha256")}
                notice["quarantine_uri"] = ("hdfs://" + self.sink.destination(manifest["partition"], manifest["start"])
                                            + "/quarantine/invalid.jsonl")
                self.send_dlq(notice)
            self.sink.mark_dlq_done(manifest)

    def recover(self, partition, low, high, committed):
        next_offset, manifests = self.sink.recover(partition)
        check_start(next_offset, low, high, committed)
        for manifest in manifests:
            self.complete_dlq(manifest)
        self.next_offsets[partition] = next_offset
        # Restore group visibility if a prior HDFS commit succeeded but Kafka's
        # acknowledgement failed. Never advance past a durable HDFS batch.
        if next_offset and next_offset != committed:
            self.commit(partition, next_offset)
        return next_offset

    def flush(self, partition, records):
        manifest = self.sink.publish(partition, self.next_offsets[partition], records)
        self.complete_dlq(manifest)
        self.commit(partition, manifest["end"])
        self.next_offsets[partition] = manifest["end"]
        return manifest


def atomic_status(path, value):
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def singleton_lock(path):
    import fcntl
    with Path(path).open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def run(args):
    from confluent_kafka import Consumer, Producer, TopicPartition, TopicCollection, KafkaError
    from confluent_kafka.admin import AdminClient
    from pyarrow.fs import HadoopFileSystem
    consumer = Consumer({"bootstrap.servers": args.bootstrap, "group.id": args.group,
                         "client.id": "cosmos-news-hdfs-writer", "enable.auto.commit": False,
                         "enable.auto.offset.store": False, "auto.offset.reset": "error",
                         "allow.auto.create.topics": False, "enable.partition.eof": True,
                         "queued.max.messages.kbytes": 16384, "fetch.message.max.bytes": 1048576,
                         "fetch.max.bytes": 8388608})
    producer = Producer({"bootstrap.servers": args.bootstrap, "client.id": "cosmos-news-hdfs-dlq",
                         "enable.idempotence": True, "acks": "all", "delivery.timeout.ms": 60000,
                         "queue.buffering.max.kbytes": 8192})
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    try:
        metadata = consumer.list_topics(timeout=20)
        topic_metadata = metadata.topics.get(args.topic)
        if topic_metadata is None or topic_metadata.error:
            raise RuntimeError("source_topic_unavailable")
        partitions = sorted(topic_metadata.partitions)
        if len(partitions) != args.expected_partitions:
            raise RuntimeError("unexpected_topic_partition_count")
        admin = AdminClient({"bootstrap.servers": args.bootstrap})
        description = admin.describe_topics(TopicCollection([args.topic]), request_timeout=20)[args.topic].result()
        topic_id = str(description.topic_id)
        if not topic_id:
            raise RuntimeError("source_topic_id_unavailable")
        # PyArrow defaults to replication=3 even when Hadoop's default is 2.
        # Match this cluster's explicitly chosen two-copy storage policy.
        sink = BatchSink(ArrowStore(HadoopFileSystem("default", 0, user=os.environ.get("HADOOP_USER_NAME", "ubuntu"), replication=2)),
                         args.hdfs_root, args.topic, args.group, metadata.cluster_id, topic_id=topic_id)
        # libhdfs starts an embedded JVM, which installs native signal handlers.
        # Install Python's handlers after the JVM and first filesystem operation,
        # otherwise SIGTERM can bypass stop() and exit without flushing buffers.
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def commit(partition, offset):
            results = consumer.commit(offsets=[TopicPartition(args.topic, partition, offset)], asynchronous=False)
            if not results or any(value.error is not None for value in results):
                raise RuntimeError("kafka_offset_commit_failed")

        def send_dlq(value):
            delivered = []
            producer.produce(args.dlq_topic, key=value["event_id"].encode(), value=encoded(value),
                             on_delivery=lambda error, message: delivered.append(error))
            outstanding = producer.flush(65)
            if outstanding or len(delivered) != 1 or delivered[0] is not None:
                raise RuntimeError("dlq_delivery_not_confirmed")

        writer = Writer(sink, commit, send_dlq)
        assignment = []
        committed = {item.partition: item.offset for item in consumer.committed(
            [TopicPartition(args.topic, partition) for partition in partitions], timeout=20)}
        for partition in partitions:
            low, high = consumer.get_watermark_offsets(TopicPartition(args.topic, partition), timeout=20)
            offset = writer.recover(partition, low, high, committed.get(partition))
            assignment.append(TopicPartition(args.topic, partition, offset))
        consumer.assign(assignment)
        pending, pending_bytes = defaultdict(list), 0
        first_at, accepted, flushed = {}, 0, 0
        last_status = 0.0

        def flush_partition(partition):
            nonlocal pending_bytes, flushed
            rows = pending[partition]
            if not rows:
                return
            manifest = writer.flush(partition, rows)
            pending_bytes -= sum(len(raw) for _, raw in rows)
            flushed += len(rows)
            pending[partition] = []
            first_at.pop(partition, None)
            print(encoded({"status": "batch_committed", "partition": partition,
                           "start": manifest["start"], "end": manifest["end"],
                           "valid_records": manifest["valid_records"],
                           "invalid_records": manifest["invalid_records"]}).decode().strip(), flush=True)

        while not stopping:
            now = time.monotonic()
            for partition in list(first_at):
                if now - first_at[partition] >= args.flush_seconds:
                    flush_partition(partition)
            if now - last_status >= 30:
                atomic_status(Path(args.state_dir) / "status.json", {
                    "status": "running", "checked_at": utc_now(), "next_offsets": writer.next_offsets,
                    "pending_records": sum(len(rows) for rows in pending.values()),
                    "pending_bytes": pending_bytes, "committed_records_this_run": flushed})
                last_status = now
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise RuntimeError("kafka_consume_error")
            partition, offset = message.partition(), message.offset()
            expected = writer.next_offsets[partition] + len(pending[partition])
            if offset != expected:
                # This raw append-only topic does not use transactional producers
                # or compaction. Any unexplained offset gap requires investigation.
                raise RuntimeError("unexpected_kafka_offset_gap_or_replay")
            raw = message.value() or b""
            if len(raw) > args.max_bytes:
                raise RuntimeError("single_message_exceeds_writer_memory_budget")
            if pending_bytes + len(raw) > args.max_bytes:
                for value in list(pending):
                    flush_partition(value)
            first_at.setdefault(partition, time.monotonic())
            pending[partition].append((offset, raw))
            pending_bytes += len(raw)
            accepted += 1
            if len(pending[partition]) >= args.batch_records:
                flush_partition(partition)
            if args.max_records is not None and accepted >= args.max_records:
                break
        for partition in list(pending):
            flush_partition(partition)
        atomic_status(Path(args.state_dir) / "status.json", {
            "status": "stopped", "checked_at": utc_now(), "next_offsets": writer.next_offsets,
            "committed_records_this_run": flushed})
        return 0
    finally:
        # auto commit is disabled, so unflushed messages remain unacknowledged.
        consumer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"))
    parser.add_argument("--topic", default="news.raw")
    parser.add_argument("--group", default="cosmos-news-hdfs-v1")
    parser.add_argument("--dlq-topic", default="pipeline.dlq")
    parser.add_argument("--hdfs-root", default="/data-lake/raw/realtime/news")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--batch-records", type=int, default=2000)
    parser.add_argument("--flush-seconds", type=float, default=300)
    parser.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--expected-partitions", type=int, default=3)
    parser.add_argument("--max-records", type=int)
    args = parser.parse_args()
    if (args.batch_records < 1 or args.flush_seconds <= 0 or args.max_bytes < 1024 or
            args.expected_partitions < 1 or (args.max_records is not None and args.max_records < 1)):
        parser.error("batch, memory, timing and partition limits must be positive")
    Path(args.state_dir).mkdir(parents=True, exist_ok=True)
    try:
        with singleton_lock(Path(args.state_dir) / ".writer.lock"):
            return run(args)
    except BlockingIOError:
        print(encoded({"status": "already_running"}).decode().strip(), flush=True)
        return 1
    except Exception as error:
        code = str(error) if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", str(error)) else "unexpected_failure"
        status = {"status": "failed", "checked_at": utc_now(), "error_type": type(error).__name__,
                  "error_code": code}
        atomic_status(Path(args.state_dir) / "status.json", status)
        print(encoded(status).decode().strip(), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
