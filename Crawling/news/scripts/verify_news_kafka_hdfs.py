"""Run isolated Kafka/HDFS integration verification on the Hadoop Master.

This creates only unique ``cosmos.news.verify.*`` topics and an HDFS tree below
``/data-lake/raw/realtime/news/_verification/``. It never publishes verification
records to news.raw and preserves all evidence. Run using the deployed venv with
JAVA_HOME, HADOOP_CONF_DIR, CLASSPATH, and libhdfs already configured.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_json(path, value):
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def load_writer(app_root):
    candidates = (app_root / "services/news_pipeline/writer.py", app_root / "news_pipeline/writer.py",
                  app_root / "writer.py")
    path = next((value for value in candidates if value.is_file()), None)
    if path is None:
        raise RuntimeError("writer_script_not_found_in_app_root")
    spec = importlib.util.spec_from_file_location("news_writer_verification_target", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path, module


def terminate_child(process, timeout=10):
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError("verification_writer_did_not_stop_within_ten_seconds")


def run(args, result):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyarrow.fs import HadoopFileSystem
    from confluent_kafka import Consumer, Producer, TopicPartition, TopicCollection, KafkaError
    from confluent_kafka.admin import AdminClient, NewTopic

    app_root = Path(args.app_root).resolve(strict=True)
    script, implementation = load_writer(app_root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
    topic, dlq = "cosmos.news.verify." + run_id, "cosmos.news.verify." + run_id + ".dlq"
    group = "cosmos-news-hdfs-verify-" + run_id
    hdfs_root = "/data-lake/raw/realtime/news/_verification/" + run_id
    require(topic.startswith("cosmos.news.verify.") and dlq.startswith("cosmos.news.verify."),
            "verification_topic_name_guard_failed")
    require(topic != "news.raw" and dlq != "pipeline.dlq", "production_topic_guard_failed")
    local = Path(args.state_dir).resolve() / run_id
    local.mkdir(parents=True, exist_ok=False)
    writer_state = local / "writer-state"
    writer_state.mkdir()
    result.update(run_id=run_id, topic=topic, dlq_topic=dlq, group=group, hdfs_root=hdfs_root,
                  local_evidence=str(local), verification_only=True, writer_script=str(script))

    admin = AdminClient({"bootstrap.servers": args.bootstrap})
    metadata = admin.list_topics(timeout=20)
    require(topic not in metadata.topics and dlq not in metadata.topics, "verification_topic_already_exists")
    configurations = {"cleanup.policy": "delete", "retention.ms": "86400000", "retention.bytes": "8388608"}
    requests = [NewTopic(topic, num_partitions=3, replication_factor=1, config=configurations),
                NewTopic(dlq, num_partitions=1, replication_factor=1, config=configurations)]
    for future in admin.create_topics(requests, request_timeout=20).values():
        future.result(timeout=25)
    result["topics_created"] = True
    description = admin.describe_topics(TopicCollection([topic]), request_timeout=20)[topic].result(timeout=25)
    sink = implementation.BatchSink(implementation.ArrowStore(HadoopFileSystem("default", 0, user="ubuntu", replication=2)),
                                    hdfs_root, topic, group, metadata.cluster_id,
                                    topic_id=str(description.topic_id))
    now = datetime.now(timezone.utc).isoformat()

    def event(region):
        return {"schema_version": 1, "event_id": f"verification:{run_id}:{region}",
                "source": "cosmos_pipeline_verification", "region": region,
                "language": "ko" if region == "domestic" else "en",
                "url": f"https://example.invalid/cosmos-pipeline-verification/{run_id}/{region}",
                "title": "COSMOS PIPELINE VERIFICATION ONLY " + region,
                "content": "COSMOS PIPELINE VERIFICATION ONLY. 실제 뉴스가 아닌 검증용 본문입니다. 🌍 " * 3,
                "organization": "COSMOS verification", "published_at": None,
                "collected_at": now, "run_id": run_id}

    domestic, overseas = event("domestic"), event("overseas")
    malformed = b"\xffCOSMOS_PIPELINE_VERIFICATION_INVALID_UTF8:" + run_id.encode()
    payloads = ((0, json_bytes(domestic)), (1, json_bytes(overseas)), (2, malformed))
    producer = Producer({"bootstrap.servers": args.bootstrap, "enable.idempotence": True,
                         "acks": "all", "delivery.timeout.ms": 30000})
    delivery = []
    for partition, payload in payloads:
        producer.produce(topic, partition=partition, key=f"verification:{partition}".encode(), value=payload,
                         on_delivery=lambda error, message: delivery.append(
                             (error is None, message.partition(), message.offset())))
    require(producer.flush(35) == 0 and len(delivery) == 3 and all(row[0] for row in delivery),
            "verification_message_delivery_failed")
    require(sorted((row[1], row[2]) for row in delivery) == [(0, 0), (1, 0), (2, 0)],
            "verification_topic_did_not_start_empty")
    result["produced_records"] = 3

    command = [sys.executable, str(script), "--bootstrap", args.bootstrap, "--topic", topic,
               "--group", group, "--dlq-topic", dlq, "--hdfs-root", hdfs_root,
               "--state-dir", str(writer_state), "--expected-partitions", "3",
               "--batch-records", "2000", "--flush-seconds", "1"]
    with (local / "first-writer.stdout.log").open("wb") as output, \
            (local / "first-writer.stderr.log").open("wb") as errors:
        process = subprocess.Popen(command + ["--max-records", "3"], cwd=app_root, stdout=output, stderr=errors)
        try:
            exit_code = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            terminate_child(process)
            raise RuntimeError("first_verification_writer_timed_out")
    require(exit_code == 0, "first_verification_writer_failed_see_evidence_logs")
    status = json.loads((writer_state / "status.json").read_text())
    require(status["status"] == "stopped" and status["committed_records_this_run"] == 3,
            "first_writer_did_not_confirm_three_records")

    def read_evidence():
        fingerprints, manifests, parquet_rows, invalid_rows = {}, [], [], []
        for partition in range(3):
            next_offset, recovered = sink.recover(partition)
            require(next_offset == 1 and len(recovered) == 1, "unexpected_partition_checkpoint_or_batch_count")
            for manifest in recovered:
                directory = sink.destination(partition, manifest["start"])
                sink.manifest(directory, verify_hashes=True)
                control = sink.store.read(directory + "/_manifest.json")
                fingerprints[directory + "/_manifest.json"] = hashlib.sha256(control).hexdigest()
                manifests.append(manifest)
                for entry in manifest["files"]:
                    path = directory + "/" + entry["path"]
                    payload = sink.store.read(path)
                    fingerprints[path] = hashlib.sha256(payload).hexdigest()
                    require(fingerprints[path] == entry["sha256"], "hdfs_payload_sha_mismatch")
                    if entry["path"].endswith(".parquet"):
                        rows = pq.ParquetFile(pa.BufferReader(payload)).read().to_pylist()
                        require(len(rows) == entry["records"], "parquet_row_count_mismatch")
                        parquet_rows.extend(rows)
                    elif entry["path"] == "quarantine/invalid.jsonl":
                        invalid_rows.extend(json.loads(line) for line in payload.splitlines())
                    else:
                        raise RuntimeError("unexpected_verification_data_file")
        require(sum(value["records"] for value in manifests) == 3, "manifest_record_sum_mismatch")
        require(len(parquet_rows) == 2 and len(invalid_rows) == 1, "valid_invalid_record_count_mismatch")
        by_region = {value["region"]: value for value in parquet_rows}
        require(set(by_region) == {"domestic", "overseas"}, "region_routing_mismatch")
        for expected, partition in ((domestic, 0), (overseas, 1)):
            actual = by_region[expected["region"]]
            require(actual["event_id"] == expected["event_id"] and actual["content"] == expected["content"],
                    "parquet_article_content_mismatch")
            require(actual["kafka_topic"] == topic and actual["kafka_partition"] == partition
                    and actual["kafka_offset"] == 0, "parquet_source_offset_mismatch")
        require(base64.b64decode(invalid_rows[0]["raw_base64"]) == malformed,
                "quarantined_original_not_preserved")
        return fingerprints, manifests

    before, manifests = read_evidence()
    result.update(valid_rows=2, quarantined_rows=1, batches=len(manifests),
                  payload_sha256_verified=True, region_content_verified=True)
    # Check the actual HDFS file policy, not only the client or site defaults:
    # PyArrow's default replication=3 can override dfs.replication=2 otherwise.
    data_paths = sorted(sink.destination(manifest["partition"], manifest["start"]) + "/" + entry["path"]
                        for manifest in manifests for entry in manifest["files"])
    require(data_paths and all(path.startswith(hdfs_root + "/") for path in data_paths),
            "replication_verification_path_guard_failed")
    hdfs_cli = str(Path(os.environ.get("HADOOP_HOME", "/opt/hadoop")) / "bin/hdfs")
    replication_check = subprocess.run([hdfs_cli, "dfs", "-stat", "%r", *data_paths],
                                       capture_output=True, text=True, timeout=60)
    require(replication_check.returncode == 0, "hdfs_replication_stat_failed")
    raw_replications = replication_check.stdout.split()
    require(len(raw_replications) == len(data_paths) and all(value.isdigit() for value in raw_replications),
            "hdfs_replication_stat_output_mismatch")
    replications = [int(value) for value in raw_replications]
    result["hdfs_replication"] = {
        "expected": 2, "verified_files": len(data_paths),
        "files": [{"path": path, "replication": value} for path, value in zip(data_paths, replications)]}
    require(all(value == 2 for value in replications), "hdfs_data_file_replication_is_not_two")
    result["hdfs_replication_verified"] = True
    consumer = Consumer({"bootstrap.servers": args.bootstrap, "group.id": group,
                         "enable.auto.commit": False, "enable.auto.offset.store": False,
                         "allow.auto.create.topics": False, "auto.offset.reset": "error"})
    try:
        def lag_snapshot():
            committed = {value.partition: value.offset for value in consumer.committed(
                [TopicPartition(topic, partition) for partition in range(3)], timeout=20)}
            values = []
            for partition in range(3):
                low, high = consumer.get_watermark_offsets(TopicPartition(topic, partition), timeout=20)
                values.append({"partition": partition, "low": low, "high": high,
                               "committed": committed[partition], "lag": high - committed[partition]})
            require(all(value["high"] == value["committed"] == 1 and value["lag"] == 0 for value in values),
                    "verification_group_has_uncommitted_offsets")
            return values

        result["offsets_before_restart"] = lag_snapshot()
        consumer.assign([TopicPartition(dlq, 0, 0)])
        notice = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            message = consumer.poll(0.5)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise RuntimeError("verification_dlq_read_failed")
            notice = json.loads(message.value())
            break
        require(notice is not None, "verification_dlq_notice_missing")
        require(notice["source_topic"] == topic and notice["source_partition"] == 2
                and notice["source_offset"] == 0 and "raw_base64" not in notice
                and notice["quarantine_uri"].startswith("hdfs://" + hdfs_root), "invalid_dlq_notice")
        require(len(json_bytes(notice)) < 2048, "dlq_notice_unnecessarily_large")
        consumer.unassign()
        result["dlq_metadata_verified"] = True

        # Restart against the same exact HDFS root, group and local lock. No new
        # records are produced. Wait for a fresh running status, then send SIGTERM.
        with (local / "restart-writer.stdout.log").open("wb") as output, \
                (local / "restart-writer.stderr.log").open("wb") as errors:
            started_ns = time.time_ns()
            process = subprocess.Popen(command, cwd=app_root, stdout=output, stderr=errors)
            try:
                ready = False
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("restarted_writer_exited_before_running")
                    path = writer_state / "status.json"
                    if path.exists() and path.stat().st_mtime_ns >= started_ns:
                        try:
                            status = json.loads(path.read_text())
                        except (ValueError, OSError):
                            status = {}
                        if status.get("status") == "running":
                            ready = True
                            break
                    time.sleep(0.2)
                require(ready, "restarted_writer_never_reported_running")
            finally:
                terminate_child(process, timeout=10)
        require(process.returncode == 0, "restarted_writer_did_not_exit_cleanly")
        after, after_manifests = read_evidence()
        require(after == before and len(after_manifests) == len(manifests), "restart_changed_immutable_data")
        result["offsets_after_restart"] = lag_snapshot()
        require(result["offsets_after_restart"] == result["offsets_before_restart"],
                "restart_changed_group_offsets_without_new_messages")
        result["restart_without_duplicates_verified"] = True
    finally:
        consumer.close()
    write_json(local / "verify-result.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", required=True)
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    state = Path(args.state_dir).resolve()
    state.mkdir(parents=True, exist_ok=True)
    result = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "verification_only": True, "preserved_evidence": True}
    try:
        run(args, result)
        result["status"] = "passed"
        exit_code = 0
    except Exception as error:
        result.update(status="failed", error_type=type(error).__name__, error=str(error)[:1500])
        exit_code = 1
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(state / "verify-result.json", result)
    if result.get("local_evidence"):
        write_json(Path(result["local_evidence"]) / "verify-result.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
