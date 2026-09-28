"""Verify explicit-schema JSONL -> Parquet, then atomically promote new HDFS runs.

Run with spark-submit --master local[2]: source JSONL files reside on the server's
local filesystem. publication.json supplies the complete schemas; inference is
never used. All dataset groups must verify before any canonical group moves.
Multiple group renames are not one HDFS transaction: the final metadata/runs
_VERIFIED marker is the only signal that the complete run is available. A failed
run can leave verified groups without that marker; nothing is deleted, replaced,
or rolled back. Local receipts preserve those partial results for diagnosis.

This publishes HDFS artifacts only. It does not change database visibility or
claim that the resulting graph is a published PostgreSQL service snapshot.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import tempfile
import traceback
import uuid


ALLOWED_ROOTS = frozenset({
    "/data-lake/cleaned/news",
    "/data-lake/features/company",
    "/data-lake/features/relationship",
    "/data-lake/analyzed/news/relationships",
    "/data-lake/aggregated/company-metrics",
    "/data-lake/aggregated/relationship-scores",
    "/data-lake/snapshots/graph",
    "/data-lake/metadata/id-maps/news",
    "/data-lake/analyzed/news/company-mentions",
    "/data-lake/analyzed/disclosures/relationships",
    "/data-lake/analyzed/disclosures/company-mentions",
    "/data-lake/analyzed/news/sentiment",
})
GRAPH_ROOT = "/data-lake/snapshots/graph"
STAGING_ROOT = "/data-lake/sandbox/news/junwoo/staging"
RUNS_ROOT = "/data-lake/metadata/runs"
STATUS = "HDFS_VERIFIED_ONLY"
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value, field: str) -> str:
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise ValueError(f"Invalid {field}: expected a simple alphanumeric run/dataset name")
    return value


def validate_publication(staging: Path, run_id: str) -> dict:
    """Local-only preflight, including every source checksum before HDFS writes."""
    run_id = safe_name(run_id, "run_id")
    staging = staging.resolve()
    publication_path = staging / "publication.json"
    publication = json.loads(publication_path.read_text(encoding="utf-8-sig"))
    if not isinstance(publication, dict) or publication.get("run_id") != run_id:
        raise ValueError("publication.json run_id does not match --run-id")
    try:
        stamp = dt.datetime.fromisoformat(str(publication["as_of"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("as_of requires a timezone")
        uuid.UUID(str(publication["snapshot_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("publication.json requires timezone-aware as_of and UUID snapshot_id") from error
    datasets = publication.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("publication.json requires at least one dataset")
    names, destinations, checked = set(), set(), []
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise ValueError("Every dataset must be an object")
        name = safe_name(dataset.get("name"), "dataset name")
        if name in names:
            raise ValueError(f"Duplicate dataset name: {name}")
        names.add(name)
        root = dataset.get("target_root")
        if root not in ALLOWED_ROOTS:
            raise ValueError(f"Target root is not allowed: {root!r}")
        dataset_path = dataset.get("dataset_path", "data")
        allowed_paths = {"data", "nodes", "edges"} if root == GRAPH_ROOT else {"data"}
        if dataset_path not in allowed_paths:
            raise ValueError(f"Invalid dataset_path for {root}: {dataset_path!r}")
        if (root, dataset_path) in destinations:
            raise ValueError(f"Duplicate destination: {root}/{dataset_path}")
        destinations.add((root, dataset_path))
        local_file = dataset.get("local_file")
        if (not isinstance(local_file, str) or not local_file.lower().endswith(".jsonl")
                or "/" in local_file or "\\" in local_file or Path(local_file).name != local_file):
            raise ValueError("local_file must be a JSONL basename inside --staging")
        source = (staging / local_file).resolve()
        if source.parent != staging or not source.is_file():
            raise ValueError(f"Source is missing or resolves outside staging: {local_file}")
        schema = dataset.get("schema")
        if not isinstance(schema, dict) or schema.get("type") != "struct" or not isinstance(schema.get("fields"), list):
            raise ValueError(f"Explicit Spark StructType JSON schema is required: {name}")
        field_names = [field.get("name") for field in schema["fields"] if isinstance(field, dict)]
        if not field_names or len(field_names) != len(schema["fields"]) or len(set(field_names)) != len(field_names):
            raise ValueError(f"Invalid or duplicate schema field names: {name}")
        keys = dataset.get("key_columns")
        if (not isinstance(keys, list) or not keys or any(not isinstance(key, str) or key not in field_names for key in keys)
                or len(set(keys)) != len(keys)):
            raise ValueError(f"Explicit distinct key_columns from the schema are required: {name}")
        row_count = dataset.get("row_count")
        if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
            raise ValueError(f"row_count must be a nonnegative integer: {name}")
        checksum = dataset.get("source_sha256")
        if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
            raise ValueError(f"source_sha256 must be SHA256 hex: {name}")
        observed = file_sha256(source)
        if observed != checksum.lower():
            raise ValueError(f"Source SHA256 mismatch: {name}")
        checked.append({**dataset, "dataset_path": dataset_path, "source_sha256": observed,
                        "source_local_path": str(source)})
    # A graph root must have either one data dataset or named nodes/edges datasets.
    graph_paths = {path for root, path in destinations if root == GRAPH_ROOT}
    if "data" in graph_paths and len(graph_paths) > 1:
        raise ValueError("Do not combine graph data with graph nodes/edges datasets")
    return {**publication, "datasets": checked, "publication_sha256": file_sha256(publication_path)}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _local_json(path: Path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


class Hdfs:
    def __init__(self, spark, out: Path):
        context = spark.sparkContext
        self.jvm, self.gateway, self.out = context._jvm, context._gateway, out
        self.conf = context._jsc.hadoopConfiguration()
        self.fs = self.jvm.org.apache.hadoop.fs.FileSystem.get(self.conf)
        if str(self.fs.getUri().getScheme()) != "hdfs":
            raise ValueError(f"Default filesystem must be HDFS, received {self.fs.getUri()}")
        self.fc = self.jvm.org.apache.hadoop.fs.FileContext.getFileContext(self.fs.getUri(), self.conf)

    def path(self, value):
        return self.jvm.org.apache.hadoop.fs.Path(value)

    def exists(self, value):
        return bool(self.fs.exists(self.path(value)))

    def mkdir(self, value):
        if not self.fs.mkdirs(self.path(value)) and not self.exists(value):
            raise RuntimeError(f"Unable to create HDFS directory: {value}")

    def uri(self, value):
        return str(self.fs.makeQualified(self.path(value)).toString())

    def json(self, destination: str, value):
        """Exclusive UTF-8 metadata upload through Hadoop's local-file API."""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=self.out,
                                             prefix="metadata-", suffix=".json", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write("\n")
            self.fs.copyFromLocalFile(False, False, self.path(temporary.resolve().as_uri()), self.path(destination))
            if int(self.fs.getFileStatus(self.path(destination)).getLen()) != temporary.stat().st_size:
                raise RuntimeError(f"Metadata upload length mismatch: {destination}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def rename_new(self, source: str, destination: str):
        if self.exists(destination):
            raise FileExistsError(f"Canonical destination already exists: {destination}")
        # FileSystem.rename may move a source INTO an existing directory. The
        # FileContext NONE option instead atomically rejects any existing target.
        options = self.gateway.new_array(self.jvm.org.apache.hadoop.fs.Options.Rename, 1)
        options[0] = self.jvm.org.apache.hadoop.fs.Options.Rename.NONE
        self.fc.rename(self.path(source), self.path(destination), options)
        if self.exists(source) or not self.exists(destination):
            raise RuntimeError(f"Atomic rename did not complete: {source} -> {destination}")

    def files(self, dataset_path: str):
        entries, iterator = [], self.fs.listFiles(self.path(dataset_path), True)
        prefix = str(self.path(dataset_path).toUri().getPath()).rstrip("/") + "/"
        while iterator.hasNext():
            status = iterator.next()
            path = status.getPath()
            absolute = str(path.toUri().getPath())
            if not absolute.startswith(prefix):
                raise RuntimeError("HDFS file listing escaped dataset root")
            checksum = self.fs.getFileChecksum(path)
            if checksum is None:
                raise RuntimeError(f"HDFS checksum unavailable: {path}")
            entries.append({
                "relative_path": absolute[len(prefix):], "length": int(status.getLen()),
                "checksum_algorithm": str(checksum.getAlgorithmName()), "checksum": str(checksum.toString()),
            })
        entries.sort(key=lambda row: row["relative_path"])
        return entries


def _validate_keys(frame, keys, functions, name):
    null_condition = None
    for key in keys:
        # Indexing by the exact column name avoids interpreting dots as traversal.
        term = frame[key].isNull()
        null_condition = term if null_condition is None else null_condition | term
    if frame.filter(null_condition).limit(1).count():
        raise ValueError(f"Null key found in {name}: {keys}")
    grouped = frame.groupBy(*[frame[key] for key in keys]).agg(functions.count(functions.lit(1)).alias("_key_count"))
    if grouped.filter(functions.col("_key_count") > 1).limit(1).count():
        raise ValueError(f"Duplicate key found in {name}: {keys}")


def run(staging: Path, run_id: str, out: Path):
    publication = validate_publication(staging, run_id)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    _local_json(out / "publication_checked.json", publication)
    spark, promoted_groups, validated = None, [], []
    stage_run = f"{STAGING_ROOT}/{run_id}"
    commit_target = f"{RUNS_ROOT}/run_id={run_id}"
    try:
        from pyspark import StorageLevel
        from pyspark.sql import SparkSession, functions as F
        from pyspark.sql.types import StructType

        spark = (SparkSession.builder.appName(f"cosmos-lake-publish-{run_id}")
                 .config("spark.sql.session.timeZone", "UTC")
                 .config("spark.sql.parquet.compression.codec", "snappy")
                 .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
                 .getOrCreate())
        if not spark.sparkContext.master.startswith("local"):
            raise ValueError("Use Spark --master local[2]; file:// JSONL staging is server-local")
        spark.sparkContext.setLogLevel("WARN")
        hdfs = Hdfs(spark, out)
        roots = sorted({dataset["target_root"] for dataset in publication["datasets"]})
        targets = {root: f"{root}/run_id={run_id}" for root in roots}
        for candidate in [stage_run, commit_target, *targets.values()]:
            if hdfs.exists(candidate):
                raise FileExistsError(f"Run path already exists; no overwrite or resume: {candidate}")
        # Validate all StructTypes before writing the first data artifact.
        schemas = {dataset["name"]: StructType.fromJson(dataset["schema"]) for dataset in publication["datasets"]}
        hdfs.mkdir(stage_run)
        hdfs.json(stage_run + "/_RUN_CLAIM.json", {"run_id": run_id, "claimed_at": _now(),
                                                  "publication_sha256": publication["publication_sha256"]})
        for dataset in publication["datasets"]:
            name, root = dataset["name"], dataset["target_root"]
            stage_path = f"{stage_run}/{name}"
            local_path = Path(dataset["source_local_path"])
            # Recheck immediately before ingestion, after preflight/model setup.
            if file_sha256(local_path) != dataset["source_sha256"]:
                raise ValueError(f"Source changed after preflight: {name}")
            source = (spark.read.schema(schemas[name]).option("mode", "FAILFAST")
                      .option("multiLine", "false").json(local_path.as_uri())
                      .persist(StorageLevel.MEMORY_AND_DISK))
            reread = None
            try:
                source_count = source.count()
                if source_count != dataset["row_count"]:
                    raise ValueError(f"Source row_count mismatch for {name}: {source_count} != {dataset['row_count']}")
                _validate_keys(source, dataset["key_columns"], F, name + " source")
                source.coalesce(4 if root == "/data-lake/cleaned/news" else 1).write.mode("errorifexists").option("compression", "snappy").parquet(hdfs.uri(stage_path))
                # Explicit run-local path. Never read the canonical root across runs.
                reread = spark.read.parquet(hdfs.uri(stage_path)).persist(StorageLevel.MEMORY_AND_DISK)
                reread_count = reread.count()
                if reread_count != source_count:
                    raise ValueError(f"Parquet row_count mismatch: {name}")
                if source.schema != reread.schema:
                    raise ValueError(f"Parquet schema mismatch for {name}: {source.schema.json()} != {reread.schema.json()}")
                _validate_keys(reread, dataset["key_columns"], F, name + " parquet")
                if source.exceptAll(reread).limit(1).count() or reread.exceptAll(source).limit(1).count():
                    raise ValueError(f"Exact source/Parquet content mismatch: {name}")
                if file_sha256(local_path) != dataset["source_sha256"]:
                    raise ValueError(f"Source changed during ingestion: {name}")
                files = hdfs.files(stage_path)
                if not any(entry["relative_path"].endswith(".parquet") for entry in files):
                    raise ValueError(f"No Parquet file was generated: {name}")
                verification = {
                    "name": name, "target_root": root, "dataset_path": dataset["dataset_path"],
                    "source_sha256": dataset["source_sha256"], "source_file": dataset["local_file"],
                    "staging_path": stage_path, "final_path": targets[root] + "/" + dataset["dataset_path"],
                    "row_count": source_count, "key_columns": dataset["key_columns"],
                    "declared_schema": dataset["schema"], "schema": reread.schema.jsonValue(),
                    "checks": {"count": True, "nonnull_keys": True, "unique_keys": True,
                               "schema_equal": True, "except_all_both_directions": True},
                    "files": files, "total_bytes": sum(entry["length"] for entry in files),
                    "status": STATUS, "verified_at": _now(),
                }
                validated.append(verification)
                _local_json(out / (name + ".verified.json"), verification)
                print(json.dumps({"dataset": name, "rows": source_count, "status": STATUS}), flush=True)
            finally:
                source.unpersist()
                if reread is not None:
                    reread.unpersist()

        # Assemble every already-verified group before any canonical promotion.
        groups = []
        group_parent = stage_run + "/_groups"
        hdfs.mkdir(group_parent)
        for root in roots:
            group_id = hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]
            group_stage = group_parent + "/" + group_id
            hdfs.mkdir(group_stage)
            datasets = [item for item in validated if item["target_root"] == root]
            for dataset in datasets:
                hdfs.rename_new(dataset["staging_path"], group_stage + "/" + dataset["dataset_path"])
            group_manifest = {
                "run_id": run_id, "as_of": publication["as_of"], "snapshot_id": publication["snapshot_id"],
                "status": STATUS, "target_root": root, "run_path": targets[root],
                "limitations": publication.get("limitations", []), "datasets": datasets,
                "publication_sha256": publication["publication_sha256"],
                "complete_run_marker": commit_target + "/_VERIFIED", "verified_at": _now(),
            }
            hdfs.json(group_stage + "/manifest.json", group_manifest)
            hdfs.json(group_stage + "/_VERIFIED", {"run_id": run_id, "status": STATUS,
                       "complete_run_marker": commit_target + "/_VERIFIED", "verified_at": _now()})
            groups.append({"root": root, "staging_path": group_stage, "final_path": targets[root],
                           "datasets": [dataset["name"] for dataset in datasets]})

        for index, group in enumerate(groups, 1):
            hdfs.mkdir(group["root"])
            hdfs.rename_new(group["staging_path"], group["final_path"])
            promoted = {**group, "promoted_at": _now(), "status": STATUS}
            promoted_groups.append(promoted)
            _local_json(out / f"group_{index:03d}.promoted.json", promoted)
            print(json.dumps({"group": group["final_path"], "status": STATUS}), flush=True)

        receipt = {
            "run_id": run_id, "as_of": publication["as_of"], "snapshot_id": publication["snapshot_id"],
            "status": STATUS, "completion_marker": commit_target + "/_VERIFIED",
            "publication_sha256": publication["publication_sha256"], "datasets": validated,
            "groups": promoted_groups, "limitations": publication.get("limitations", []),
            "dataset_count": len(validated), "total_rows": sum(item["row_count"] for item in validated),
            "total_bytes": sum(item["total_bytes"] for item in validated), "verified_at": _now(),
        }
        commit_stage = stage_run + "/_commit"
        hdfs.mkdir(commit_stage)
        hdfs.json(commit_stage + "/receipt.json", receipt)
        hdfs.json(commit_stage + "/_VERIFIED", {"run_id": run_id, "status": STATUS,
                  "publication_sha256": publication["publication_sha256"], "verified_at": _now()})
        hdfs.mkdir(RUNS_ROOT)
        hdfs.rename_new(commit_stage, commit_target)
        _local_json(out / "receipt.json", receipt)
        print(json.dumps({"run_id": run_id, "completion_marker": receipt["completion_marker"], "status": STATUS}), flush=True)
        return receipt
    except Exception as error:
        failure = {"run_id": run_id, "error_type": type(error).__name__, "error": str(error),
                   "validated_datasets": [item["name"] for item in validated], "promoted_groups": promoted_groups,
                   "completion_marker": commit_target + "/_VERIFIED", "failed_at": _now(),
                   "traceback": traceback.format_exc(), "automatic_cleanup": False}
        _local_json(out / "failure.json", failure)
        raise
    finally:
        if spark is not None:
            spark.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.staging, args.run_id, args.out)


if __name__ == "__main__":
    main()
