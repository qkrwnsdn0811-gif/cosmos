"""Publish bounded JSONL shards as one flat, verified Parquet dataset per run.

Run with spark-submit --master local[1] --driver-memory 4g and --run-dir ROOT
--run-id ID. ROOT/backfill_publication.json and finish.done.json are immutable
inputs. Explicit union schemas are supplied by the finalizer; no schema inference
or collection of article rows is used. Only one source shard is persisted at once.

Each shard passes count/key/schema and bidirectional exceptAll checks before its
Parquet files are moved into a flat dataset directory. Immutable prepared/done
receipts recover a crash between file renames. Resume rehashes each local source
and checks every recorded HDFS checksum/length. A narrow keys-only global check
then proves uniqueness across shards. Failed attempts are retained.

All datasets verify before any new canonical run directory is promoted. Partial
promotion resumes only with the identical local and HDFS group manifests. The
metadata/runs/run_id=ID/_VERIFIED marker commits the whole run; per-group markers
do not. No existing data is deleted or overwritten and no database is modified.
Preserve ROOT/publish for resume; losing its checkpoints causes a closed failure.
--sandbox-only mirrors all canonical/commit paths under a validation-only HDFS
prefix and records HDFS_SANDBOX_VERIFIED_ONLY. Omit it for the full backfill.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import traceback
import uuid

from publish_lake_recent import (
    ALLOWED_ROOTS, GRAPH_ROOT, Hdfs, RUNS_ROOT, SHA256, STAGING_ROOT, STATUS,
    _now, _validate_keys, file_sha256, safe_name,
)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8") + b"\n"


def object_sha(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path, value, *, replace=False):
    """Atomic metadata only; immutable checkpoints are never overwritten."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not replace and path.exists():
        if read_json(path) != value:
            raise ValueError(f"Immutable checkpoint conflicts: {path}")
        return
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)  # atomic create-if-absent on the same FS
            except FileExistsError:
                if read_json(path) != value:
                    raise ValueError(f"Immutable checkpoint conflicts: {path}")
    finally:
        temporary.unlink(missing_ok=True)


def local_source(root, value):
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("Source local_file must use a run-relative POSIX path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not value.endswith(".jsonl") or ":" in value:
        raise ValueError(f"Invalid local_file: {value!r}")
    result = (root / value).resolve()
    if not result.is_relative_to(root) or not result.is_file():
        raise ValueError(f"Source missing or outside run directory: {value!r}")
    return result


def nonnegative(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Expected nonnegative integer: {label}")
    return value


def validate_publication(root, run_id, *, extra_graph_paths=()):
    """Validate metadata now; stream-check each shard SHA immediately before use."""
    root = Path(root).resolve()
    extra_graph_paths = frozenset(safe_name(x, "extra graph dataset path") for x in extra_graph_paths)
    safe_name(run_id, "run_id")
    path = root / "backfill_publication.json"
    publication = read_json(path)
    publication_sha = file_sha256(path)
    if (publication.get("schema_version") != "lake-backfill-publication-1"
            or publication.get("run_id") != run_id):
        raise ValueError("Publication version/run_id mismatch")
    if read_json(root / "finish.done.json").get("publication_sha256") != publication_sha:
        raise ValueError("Finalizer completion marker does not match publication SHA")
    if publication.get("source_manifest_sha256") != file_sha256(root / "source_manifest.json"):
        raise ValueError("Frozen source manifest SHA changed")
    timestamp = dt.datetime.fromisoformat(str(publication["as_of"]).replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("as_of must contain a timezone")
    uuid.UUID(publication["snapshot_id"])
    datasets = publication.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("Publication requires datasets")
    names, destinations = set(), set()
    for dataset in datasets:
        name = safe_name(dataset.get("name"), "dataset name")
        if name in names:
            raise ValueError(f"Duplicate dataset name: {name}")
        names.add(name)
        target = dataset.get("target_root")
        subpath = dataset.get("dataset_path", "data")
        if target not in ALLOWED_ROOTS:
            raise ValueError(f"Target root is not allowed: {target}")
        if subpath not in ({"data", "nodes", "edges"} | extra_graph_paths if target == GRAPH_ROOT else {"data"}):
            raise ValueError(f"Invalid dataset_path: {name}")
        if (target, subpath) in destinations:
            raise ValueError("Duplicate dataset destination")
        destinations.add((target, subpath))
        schema = dataset.get("schema")
        if not isinstance(schema, dict) or schema.get("type") != "struct" or not isinstance(schema.get("fields"), list):
            raise ValueError(f"Explicit struct schema required: {name}")
        fields = [field.get("name") for field in schema["fields"] if isinstance(field, dict)]
        if (not fields or len(fields) != len(schema["fields"]) or any(not isinstance(x, str) or not x for x in fields)
                or len(set(fields)) != len(fields)):
            raise ValueError(f"Invalid schema fields: {name}")
        keys = dataset.get("key_columns")
        if (not isinstance(keys, list) or not keys or any(x not in fields for x in keys)
                or len(set(keys)) != len(keys)):
            raise ValueError(f"Invalid key_columns: {name}")
        nonnegative(dataset.get("row_count"), name + " row_count")
        parts = dataset.get("files")
        if not isinstance(parts, list) or not parts:
            raise ValueError(f"At least one explicit JSONL shard is required (also for empty datasets): {name}")
        seen = set()
        for part in parts:
            source = local_source(root, part.get("local_file"))
            if source in seen:
                raise ValueError(f"Repeated source shard: {source}")
            seen.add(source)
            if not isinstance(part.get("sha256"), str) or not SHA256.fullmatch(part["sha256"]):
                raise ValueError(f"Invalid shard SHA: {name}")
            nonnegative(part.get("row_count"), name + " shard row_count")
            if "bytes" in part and nonnegative(part["bytes"], "source bytes") != source.stat().st_size:
                raise ValueError(f"Source length changed: {source}")
        if sum(part["row_count"] for part in parts) != dataset["row_count"]:
            raise ValueError(f"Sum of shard rows differs from dataset count: {name}")
    graph_paths = {path for target, path in destinations if target == GRAPH_ROOT}
    if "data" in graph_paths and len(graph_paths) > 1:
        raise ValueError("Graph data and nodes/edges cannot share one target")
    return publication, publication_sha


def checked_commit_metadata(root, entries):
    """Freeze opt-in JSON provenance copies before writes; never replace commit files."""
    root = Path(root).resolve()
    checked, names = [], set()
    for entry in entries:
        name = safe_name(entry.get("name"), "commit metadata name")
        if not name.endswith(".json") or name in {"receipt.json", "manifest.json"} or name in names:
            raise ValueError("Invalid or duplicate commit metadata name")
        names.add(name)
        value = entry.get("local_file")
        if not isinstance(value, str) or "\\" in value:
            raise ValueError("Commit metadata local_file must be run-relative POSIX")
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or ":" in value or not value.endswith(".json"):
            raise ValueError("Invalid commit metadata local_file")
        path = (root / value).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Commit metadata missing or outside run directory")
        digest = file_sha256(path)
        if digest != entry.get("sha256"):
            raise ValueError("Commit metadata SHA changed")
        # Decode before HDFS writes. ensure_remote_json later compares all fields.
        read_json(path)
        checked.append({"name": name, "local_file": value, "sha256": digest})
    return checked


@contextlib.contextmanager
def publisher_lock(out):
    import fcntl  # production runner is a Linux server
    with (out / "publisher.lock").open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def remote_json(hdfs, path):
    stream = hdfs.fs.open(hdfs.path(path))
    reader = hdfs.jvm.java.io.BufferedReader(hdfs.jvm.java.io.InputStreamReader(stream, "UTF-8"))
    try:
        lines = []
        while True:
            line = reader.readLine()
            if line is None:
                break
            lines.append(str(line))
        return json.loads("\n".join(lines))
    finally:
        reader.close()


def ensure_remote_json(hdfs, path, value):
    if hdfs.exists(path):
        if remote_json(hdfs, path) != value:
            raise ValueError(f"Existing HDFS metadata conflicts: {path}")
    else:
        hdfs.json(path, value)
        if remote_json(hdfs, path) != value:
            raise ValueError(f"Uploaded HDFS metadata differs on reread: {path}")


def file_record(hdfs, path):
    status = hdfs.fs.getFileStatus(hdfs.path(path))
    if not status.isFile():
        raise ValueError(f"Expected an HDFS file: {path}")
    checksum = hdfs.fs.getFileChecksum(hdfs.path(path))
    if checksum is None:
        raise ValueError(f"HDFS checksum missing: {path}")
    return {"length": int(status.getLen()), "checksum_algorithm": str(checksum.getAlgorithmName()),
            "checksum": str(checksum.toString())}


def verify_file(hdfs, path, expected):
    if file_record(hdfs, path) != {k: expected[k] for k in ("length", "checksum_algorithm", "checksum")}:
        raise ValueError(f"HDFS file checksum/length changed: {path}")


def recover_moves(hdfs, prepared, flat_path, *, promoted=False):
    """Two-phase shard commit: complete only checksum-proven, nonoverwriting moves."""
    for item in prepared["files"]:
        source = prepared["attempt_path"] + "/" + item["attempt_relative_path"]
        target = flat_path + "/" + item["relative_path"]
        if hdfs.exists(target):
            verify_file(hdfs, target, item)
            if hdfs.exists(source):
                raise ValueError(f"Both staged and flat shard files exist: {target}")
        elif promoted:
            raise ValueError(f"Promoted dataset lost a shard file: {target}")
        else:
            verify_file(hdfs, source, item)
            hdfs.rename_new(source, target)
            verify_file(hdfs, target, item)


def shard_identity(publication_sha, dataset, part, index):
    return {"publication_sha256": publication_sha, "dataset": dataset["name"], "index": index,
            "schema_sha256": object_sha(dataset["schema"]), "key_columns": dataset["key_columns"],
            "local_file": part["local_file"], "source_sha256": part["sha256"].lower(),
            "row_count": part["row_count"]}


def verify_source(root, part):
    source = local_source(root, part["local_file"])
    if file_sha256(source) != part["sha256"].lower():
        raise ValueError(f"Source shard SHA changed: {source}")
    return source


def checked_checkpoint(path, identity):
    checkpoint = read_json(path)
    if checkpoint.get("identity") != identity or checkpoint.get("state") != "SHARD_VERIFIED":
        raise ValueError(f"Shard checkpoint identity mismatch: {path}")
    files = checkpoint.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError(f"Shard checkpoint has no Parquet files: {path}")
    seen = set()
    for item in files:
        name = item.get("relative_path", "")
        source_name = item.get("attempt_relative_path", "")
        for candidate in (name, source_name):
            if "/" in candidate or "\\" in candidate or not candidate.endswith(".parquet"):
                raise ValueError("Checkpoint file must be a flat Parquet basename")
        if name in seen or not name.startswith(f"chunk-{identity['index']:06d}-"):
            raise ValueError("Checkpoint file index/name collision")
        seen.add(name)
    return checkpoint


def publish_shard(spark, hdfs, root, out, stage, dataset, part, index, publication_sha, flat_path, promoted):
    from pyspark import StorageLevel
    from pyspark.sql import functions as F
    from pyspark.sql.types import StructType

    identity = shard_identity(publication_sha, dataset, part, index)
    source_path = verify_source(root, part)
    prefix = out / "checkpoints" / dataset["name"] / f"chunk-{index:06d}"
    prepared_path, done_path = Path(str(prefix) + ".prepared.json"), Path(str(prefix) + ".done.json")
    if done_path.exists():
        prepared = checked_checkpoint(done_path, identity)
        if not prepared_path.exists() or read_json(prepared_path) != prepared:
            raise ValueError("Completed shard lost its identical prepared receipt")
    elif prepared_path.exists():
        prepared = checked_checkpoint(prepared_path, identity)
    else:
        if promoted:
            raise ValueError("Cannot rebuild a shard inside a promoted group")
        attempt = f"{stage}/_attempts/{dataset['name']}/chunk-{index:06d}/{uuid.uuid4().hex}/data"
        source, reread = None, None
        try:
            schema = StructType.fromJson(dataset["schema"])
            source = (spark.read.schema(schema).option("mode", "FAILFAST").option("multiLine", "false")
                      .json(source_path.as_uri()).persist(StorageLevel.MEMORY_AND_DISK))
            if source.schema != schema:
                raise ValueError(f"Spark altered declared schema: {dataset['name']}")
            if source.count() != part["row_count"]:
                raise ValueError(f"Source shard row count mismatch: {source_path}")
            _validate_keys(source, dataset["key_columns"], F, str(source_path))
            source.coalesce(1).write.mode("errorifexists").option("compression", "snappy").parquet(hdfs.uri(attempt))
            reread = spark.read.parquet(hdfs.uri(attempt)).persist(StorageLevel.MEMORY_AND_DISK)
            if reread.schema != source.schema or reread.count() != part["row_count"]:
                raise ValueError(f"Parquet schema/count mismatch: {source_path}")
            _validate_keys(reread, dataset["key_columns"], F, attempt)
            if source.exceptAll(reread).limit(1).count() or reread.exceptAll(source).limit(1).count():
                raise ValueError(f"Bidirectional exceptAll failed: {source_path}")
            if file_sha256(source_path) != identity["source_sha256"]:
                raise ValueError(f"Source changed during shard ingestion: {source_path}")
            files = []
            for item in hdfs.files(attempt):
                if item["relative_path"].endswith(".parquet"):
                    if "/" in item["relative_path"]:
                        raise ValueError("Unexpected partitioned shard output")
                    files.append({**item, "attempt_relative_path": item["relative_path"],
                                  "relative_path": f"chunk-{index:06d}-" + item["relative_path"]})
            if not files:
                raise ValueError("Spark produced no Parquet file, including for an empty shard")
            prepared = {"identity": identity, "attempt_path": attempt, "files": files,
                        "state": "SHARD_VERIFIED", "checks": {"count": True, "keys": True,
                        "schema": True, "except_all_both_directions": True}}
            save_json(prepared_path, prepared)
        finally:
            if source is not None:
                source.unpersist(blocking=True)
            if reread is not None:
                reread.unpersist(blocking=True)
    attempt_prefix = f"{stage}/_attempts/{dataset['name']}/chunk-{index:06d}/"
    attempt = prepared.get("attempt_path", "")
    if not attempt.startswith(attempt_prefix):
        raise ValueError("Prepared shard attempt escaped its private staging path")
    suffix = attempt[len(attempt_prefix):].split("/")
    if len(suffix) != 2 or suffix[1] != "data" or len(suffix[0]) != 32 or any(x not in "0123456789abcdef" for x in suffix[0]):
        raise ValueError("Invalid private shard attempt path")
    recover_moves(hdfs, prepared, flat_path, promoted=promoted)
    save_json(done_path, prepared)
    return [{key: value for key, value in item.items() if key != "attempt_relative_path"} for item in prepared["files"]]


def group_id(target):
    return hashlib.sha256(target.encode("utf-8")).hexdigest()[:16]


def actual_path(logical, run_id, sandbox_only):
    if sandbox_only:
        return f"/data-lake/sandbox/news/junwoo/backfill-validation/{run_id}" + logical
    return logical


def verify_inventory(hdfs, path, expected):
    expected = sorted(expected, key=lambda item: item["relative_path"])
    if hdfs.files(path) != expected:
        raise ValueError(f"Flat dataset inventory differs from shard receipts: {path}")


def execute(root, run_id, out, *, sandbox_only=False, extra_graph_paths=(), commit_metadata=()):
    publication, publication_sha = validate_publication(root, run_id, extra_graph_paths=extra_graph_paths)
    commit_metadata = checked_commit_metadata(root, commit_metadata)
    lock = {"run_id": run_id, "publication_sha256": publication_sha, "sandbox": sandbox_only,
            "publisher_sha256": file_sha256(Path(__file__)),
            "helper_sha256": file_sha256(Path(__file__).with_name("publish_lake_recent.py"))}
    if extra_graph_paths or commit_metadata:
        lock.update(extra_graph_paths=sorted(extra_graph_paths), commit_metadata=commit_metadata)
    save_json(out / "publication.locked.json", lock)
    from pyspark.sql import SparkSession, functions as F
    from pyspark.sql.types import StructType

    stage = f"{STAGING_ROOT}/{run_id}/backfill"
    commit = actual_path(f"{RUNS_ROOT}/run_id={run_id}", run_id, sandbox_only)
    effective_status = "HDFS_SANDBOX_VERIFIED_ONLY" if sandbox_only else STATUS
    roots = sorted({d["target_root"] for d in publication["datasets"]})
    groups = {target: {"root": target, "staging_path": f"{stage}/_groups/{group_id(target)}",
                       "final_path": actual_path(f"{target}/run_id={run_id}", run_id, sandbox_only)} for target in roots}
    counts = {"shards_completed": 0, "shards_total": sum(len(d["files"]) for d in publication["datasets"]),
              "rows_verified": 0, "datasets_verified": 0, "datasets_total": len(publication["datasets"]),
              "groups_promoted": 0}

    def progress(message, state="RUNNING"):
        save_json(root / "publish_progress.json", {"run_id": run_id, "state": state, "counts": counts,
                                                   "message": message, "updated_at": _now()}, replace=True)
        print(json.dumps({"state": state, "counts": counts, "message": message}), flush=True)

    spark = None
    try:
        # Parse every explicit schema before opening any HDFS output.
        schemas = {d["name"]: StructType.fromJson(d["schema"]) for d in publication["datasets"]}
        spark = (SparkSession.builder.appName("cosmos-backfill-publish-" + run_id)
                 .config("spark.sql.session.timeZone", "UTC")
                 .config("spark.sql.shuffle.partitions", "64")
                 .config("spark.sql.parquet.compression.codec", "snappy")
                 .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false").getOrCreate())
        if spark.sparkContext.master != "local[1]":
            raise ValueError("Use --master local[1] --driver-memory 4g for server-local source shards")
        spark.sparkContext.setLogLevel("WARN")
        hdfs = Hdfs(spark, out)
        claim = {**lock, "schema_version": "lake-backfill-publisher-1"}
        if hdfs.exists(stage) and not hdfs.exists(stage + "/_RUN_CLAIM.json"):
            raise ValueError("Unclaimed staging directory exists; preserve it and use a new run")
        hdfs.mkdir(stage)
        ensure_remote_json(hdfs, stage + "/_RUN_CLAIM.json", claim)
        promoted = set()
        for target, group in groups.items():
            if hdfs.exists(group["final_path"]):
                local_manifest = out / "groups" / (group_id(target) + ".json")
                if not local_manifest.exists():
                    raise ValueError("Canonical group exists without this run's local manifest")
                expected = read_json(local_manifest)
                if expected.get("publication_sha256") != publication_sha or expected.get("run_id") != run_id:
                    raise ValueError("Canonical group belongs to a different publication")
                if remote_json(hdfs, group["final_path"] + "/manifest.json") != expected:
                    raise ValueError("Canonical manifest differs from saved manifest")
                promoted.add(target)
            else:
                hdfs.mkdir(group["staging_path"])
        verified = []
        for dataset in publication["datasets"]:
            name, target = dataset["name"], dataset["target_root"]
            group = groups[target]
            active = group["final_path"] if target in promoted else group["staging_path"]
            subpath = dataset.get("dataset_path", "data")
            flat = active + "/" + subpath
            if target not in promoted:
                hdfs.mkdir(flat)
            expected_files = []
            for index, part in enumerate(dataset["files"]):
                progress(f"Verifying {name} shard {index + 1}/{len(dataset['files'])}")
                expected_files.extend(publish_shard(spark, hdfs, root, out, stage, dataset, part, index,
                                                   publication_sha, flat, target in promoted))
                counts["shards_completed"] += 1
                counts["rows_verified"] += part["row_count"]
            verify_inventory(hdfs, flat, expected_files)
            # Entire wide data is neither collected nor persisted. Spark reads
            # only projected key columns for the cross-shard duplicate shuffle.
            frame = spark.read.parquet(hdfs.uri(flat))
            if frame.schema != schemas[name]:
                raise ValueError(f"Global dataset schema differs: {name}")
            keys = frame.select(*[frame[key] for key in dataset["key_columns"]])
            if keys.count() != dataset["row_count"]:
                raise ValueError(f"Global dataset row count differs: {name}")
            _validate_keys(keys, dataset["key_columns"], F, name + " across all shards")
            info = {"name": name, "target_root": target, "dataset_path": subpath,
                    "final_path": group["final_path"] + "/" + subpath,
                    "row_count": dataset["row_count"], "shard_count": len(dataset["files"]),
                    "schema": dataset["schema"], "schema_sha256": object_sha(dataset["schema"]),
                    "key_columns": dataset["key_columns"], "files": sorted(expected_files, key=lambda x: x["relative_path"]),
                    "total_bytes": sum(x["length"] for x in expected_files), "status": effective_status,
                    "checks": {"shard_exact_content": True, "global_count": True,
                               "global_unique_nonnull_keys": True, "file_checksums": True}}
            save_json(out / "datasets" / (name + ".json"), info)
            verified.append(info)
            counts["datasets_verified"] += 1
            progress(f"Verified all shards and global keys: {name}")
        # Reconfirm control inputs before any canonical rename.
        if file_sha256(root / "backfill_publication.json") != publication_sha:
            raise ValueError("Publication changed while verifying data")
        if file_sha256(root / "source_manifest.json") != publication["source_manifest_sha256"]:
            raise ValueError("Source manifest changed while verifying data")
        checked_commit_metadata(root, commit_metadata)
        marker = {"run_id": run_id, "status": effective_status, "sandbox": sandbox_only, "publication_sha256": publication_sha,
                  "complete_run_marker": commit + "/_VERIFIED"}
        for target, group in groups.items():
            manifest = {**marker, "as_of": publication["as_of"], "snapshot_id": publication["snapshot_id"],
                        "target_root": target, "datasets": [x for x in verified if x["target_root"] == target],
                        "limitations": publication.get("limitations", [])}
            save_json(out / "groups" / (group_id(target) + ".json"), manifest)
            active = group["final_path"] if target in promoted else group["staging_path"]
            ensure_remote_json(hdfs, active + "/manifest.json", manifest)
            ensure_remote_json(hdfs, active + "/_VERIFIED", marker)
        for target, group in groups.items():
            if target not in promoted:
                hdfs.mkdir(group["final_path"].rsplit("/", 1)[0])
                hdfs.rename_new(group["staging_path"], group["final_path"])
            counts["groups_promoted"] += 1
            progress("Promoted " + group["final_path"])
        receipt = {**marker, "as_of": publication["as_of"], "snapshot_id": publication["snapshot_id"],
                   "completion_marker": commit + "/_VERIFIED", "datasets": verified,
                   "groups": list(groups.values()), "dataset_count": len(verified),
                   "total_rows": sum(x["row_count"] for x in verified),
                   "total_bytes": sum(x["total_bytes"] for x in verified),
                   "limitations": publication.get("limitations", [])}
        if commit_metadata:
            receipt["commit_metadata"] = commit_metadata
        save_json(out / "commit.prepared.json", receipt)
        if hdfs.exists(commit):
            if remote_json(hdfs, commit + "/receipt.json") != receipt or remote_json(hdfs, commit + "/_VERIFIED") != marker:
                raise ValueError("Existing complete-run commit differs from this verified run")
            for entry in commit_metadata:
                if remote_json(hdfs, commit + "/" + entry["name"]) != read_json(root / entry["local_file"]):
                    raise ValueError("Existing complete-run provenance metadata differs")
        else:
            commit_stage = stage + "/_commit"
            hdfs.mkdir(commit_stage)
            checked_commit_metadata(root, commit_metadata)
            for entry in commit_metadata:
                ensure_remote_json(hdfs, commit_stage + "/" + entry["name"], read_json(root / entry["local_file"]))
            ensure_remote_json(hdfs, commit_stage + "/receipt.json", receipt)
            ensure_remote_json(hdfs, commit_stage + "/_VERIFIED", marker)
            hdfs.mkdir(commit.rsplit("/", 1)[0])
            hdfs.rename_new(commit_stage, commit)
        save_json(out / "receipt.json", receipt)
        save_json(root / "publish.done.json", {**marker, "receipt_sha256": file_sha256(out / "receipt.json"),
                                              "counts": counts})
        progress("All canonical groups and complete-run marker verified", "COMPLETED")
        return receipt
    except BaseException as error:
        failure = {"run_id": run_id, "publication_sha256": publication_sha, "failed_at": _now(),
                   "error_type": type(error).__name__, "error": str(error), "counts": counts,
                   "traceback": traceback.format_exc(), "automatic_cleanup": False}
        save_json(out / ("failure-" + uuid.uuid4().hex + ".json"), failure)
        progress(str(error), "FAILED")
        raise
    finally:
        if spark is not None:
            spark.stop()


def run(root, run_id, *, sandbox_only=False, extra_graph_paths=(), commit_metadata=()):
    root = Path(root).resolve()
    out = root / "publish"
    out.mkdir(parents=True, exist_ok=True)
    with publisher_lock(out):
        return execute(root, run_id, out, sandbox_only=sandbox_only,
                       extra_graph_paths=extra_graph_paths, commit_metadata=commit_metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sandbox-only", action="store_true", help="Mirror targets and commit marker into validation sandbox")
    args = parser.parse_args()
    run(args.run_dir, args.run_id, sandbox_only=args.sandbox_only)


if __name__ == "__main__":
    main()
