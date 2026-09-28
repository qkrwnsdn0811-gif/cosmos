"""Validate and materialize one HDFS_VERIFIED_ONLY analytics run."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .contract import ContractError, Manifest, WINDOWS, default_score, number, timestamp, window_start

RUN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
DATASETS = {
    "company_metrics": "aggregated/company-metrics",
    "relationship_scores": "aggregated/relationship-scores",
    "graph": "snapshots/graph",
}


@dataclass
class VerifiedRun:
    run_id: str
    publication_sha256: str
    manifest: Manifest
    relationship_rows: list[dict[str, Any]]
    metric_rows: list[dict[str, Any]]


def _run(command: list[str], *, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def _hdfs_bin() -> str:
    return os.environ.get("HDFS_BIN", "hdfs")


def discover_latest_run(lake_root: str) -> str:
    result = _run([_hdfs_bin(), "dfs", "-ls", f"{lake_root.rstrip('/')}/metadata/runs"])
    if result.returncode:
        raise ContractError("cannot list verified analytics runs")
    candidates: list[tuple[Any, str]] = []
    for line in result.stdout.splitlines():
        path = line.rsplit(None, 1)[-1] if line.strip() else ""
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if not name.startswith("run_id="):
            continue
        run_id = name[7:]
        if not RUN_PATTERN.fullmatch(run_id):
            continue
        marker = _run([_hdfs_bin(), "dfs", "-cat", f"{path}/_VERIFIED"], timeout=60)
        if marker.returncode:
            continue
        try:
            data = json.loads(marker.stdout)
            if data.get("run_id") == run_id and data.get("status") == "HDFS_VERIFIED_ONLY":
                candidates.append((timestamp(data.get("verified_at"), "verified_at"), run_id))
        except (json.JSONDecodeError, ContractError):
            continue
    if not candidates:
        raise ContractError("no HDFS_VERIFIED_ONLY analytics run exists")
    return max(candidates)[1]


@contextmanager
def local_verified_run(lake_root: str, run_id: str):
    if not RUN_PATTERN.fullmatch(run_id):
        raise ContractError("run_id contains unsupported characters")
    if not lake_root.startswith("hdfs://") or any(char in lake_root for char in "*?[]{}"):
        raise ContractError("lake root must be one fixed hdfs:// URI")
    with tempfile.TemporaryDirectory(prefix="cosmos-lake-run-") as temporary:
        root = Path(temporary)
        targets = [
            f"metadata/runs/run_id={run_id}/_VERIFIED",
            *(f"{path}/run_id={run_id}" for path in DATASETS.values()),
        ]
        for relative in targets:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            result = _run([_hdfs_bin(), "dfs", "-get", f"{lake_root.rstrip('/')}/{relative}", str(destination)])
            if result.returncode:
                raise ContractError(f"HDFS run download failed: {relative}")
        yield root


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {path.name}")
    return value


def _dataset(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [item for item in manifest.get("datasets", []) if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise ContractError(f"manifest must contain exactly one {name} dataset")
    return matches[0]


def _validate_dataset(root: Path, item: dict[str, Any], *, expected_path: str) -> list[dict[str, Any]]:
    if item.get("status") != "HDFS_VERIFIED_ONLY" or item.get("dataset_path") != expected_path:
        raise ContractError("dataset is not an HDFS_VERIFIED_ONLY expected path")
    checks = item.get("checks")
    required_checks = {"count", "nonnull_keys", "unique_keys", "schema_equal", "except_all_both_directions"}
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in required_checks):
        raise ContractError("producer verification checks did not all pass")
    if item.get("schema") != item.get("declared_schema"):
        raise ContractError("declared and stored schemas differ")
    data_root = root / expected_path
    if not (data_root / "_SUCCESS").is_file():
        raise ContractError(f"{expected_path} has no _SUCCESS marker")
    declared = {entry.get("relative_path"): entry for entry in item.get("files", []) if isinstance(entry, dict)}
    actual = {p.relative_to(data_root).as_posix(): p for p in data_root.rglob("*") if p.is_file()}
    if set(declared) != set(actual):
        raise ContractError(f"{expected_path} files differ from manifest")
    for relative, path in actual.items():
        if path.stat().st_size != declared[relative].get("length"):
            raise ContractError(f"file length mismatch: {relative}")
    parquet = [path for relative, path in actual.items() if relative.endswith(".parquet")]
    if item.get("row_count") and not parquet:
        raise ContractError("nonempty dataset has no parquet file")
    import pyarrow.parquet as pq
    rows = [row for path in parquet for batch in pq.ParquetFile(path).iter_batches(batch_size=8192) for row in batch.to_pylist()]
    if len(rows) != item.get("row_count"):
        raise ContractError("actual parquet row count differs from manifest")
    keys = item.get("key_columns")
    if not isinstance(keys, list) or any(row.get(key) is None for row in rows for key in keys):
        raise ContractError("dataset contains a null key")
    identities = [tuple(row[key] for key in keys) for row in rows]
    if len(identities) != len(set(identities)):
        raise ContractError("dataset contains duplicate keys")
    return rows


def _validate_hdfs_checksums(lake_root: str, item: dict[str, Any]) -> None:
    parsed = urlsplit(lake_root)
    authority = f"{parsed.scheme}://{parsed.netloc}"
    final_path = item.get("final_path")
    if not isinstance(final_path, str) or not final_path.startswith("/data-lake/"):
        raise ContractError("dataset final_path must be inside /data-lake")
    for entry in item.get("files", []):
        relative = entry.get("relative_path") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            raise ContractError("manifest contains an unsafe file path")
        result = _run([_hdfs_bin(), "dfs", "-checksum", f"{authority}{final_path}/{relative}"], timeout=60)
        if result.returncode:
            raise ContractError(f"cannot verify HDFS checksum: {relative}")
        fields = result.stdout.strip().split()
        declared_checksum = str(entry.get("checksum", ""))
        declared_digest = declared_checksum.rsplit(":", 1)[-1]
        if (len(fields) < 3 or fields[-2] != entry.get("checksum_algorithm")
                or not fields[-1].endswith(declared_digest)):
            raise ContractError(f"HDFS checksum differs from manifest: {relative}")


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{field} must be a nonnegative integer")
    return value


def _count_or_zero(value: Any, field: str) -> int:
    return 0 if value is None else _count(value, field)


def _sentiment(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not -1 <= result <= 1:
            raise ValueError
        return result.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ContractError("sentiment_score must be NULL or a finite number in [-1, 1]") from exc


def _validate_metric(row: dict[str, Any], as_of) -> dict[str, Any]:
    window = row.get("window_type")
    if window not in WINDOWS:
        raise ContractError("metric window_type must be 30D, 1Y or 10Y")
    measured = timestamp(row.get("measured_at"), "measured_at")
    if measured != as_of or timestamp(row.get("period_end"), "period_end") != as_of:
        raise ContractError("metric measured_at/period_end differs from run as_of")
    if timestamp(row.get("period_start"), "period_start") != window_start(as_of, window):
        raise ContractError("metric period_start differs from its window")
    for field in ("market", "stock_code", "metric_version"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ContractError(f"metric {field} is required")
    return {
        "market": row["market"], "stock_code": row["stock_code"], "window_type": window,
        "measured_at": measured, "news_mention_count": _count_or_zero(row.get("news_mention_count"), "news_mention_count"),
        "positive_count": _count_or_zero(row.get("positive_count"), "positive_count"),
        "negative_count": _count_or_zero(row.get("negative_count"), "negative_count"),
        "sentiment_score": _sentiment(row.get("sentiment_score")),
        "relationship_count": _count_or_zero(row.get("relationship_count"), "relationship_count"),
        "metric_version": row["metric_version"],
    }


def _validate_relationship(row: dict[str, Any], as_of, formula_version: str) -> dict[str, Any]:
    window = row.get("window_type")
    if window not in WINDOWS:
        raise ContractError("relationship window_type must be 30D, 1Y or 10Y")
    if timestamp(row.get("as_of_at"), "as_of_at") != as_of or timestamp(row.get("period_end"), "period_end") != as_of:
        raise ContractError("relationship as_of_at/period_end differs from run as_of")
    start = timestamp(row.get("period_start"), "period_start")
    if start != window_start(as_of, window):
        raise ContractError("relationship period_start differs from its window")
    required = ("source_market", "source_stock_code", "target_market", "target_stock_code", "relationship_type_code")
    if any(not isinstance(row.get(field), str) or not row[field].strip() for field in required):
        raise ContractError("relationship company keys and type code are required")
    if row.get("formula_version") != formula_version:
        raise ContractError("relationship formula_version is inconsistent")
    news = number(row.get("news_score"), "news_score")
    disclosure = number(row.get("disclosure_score"), "disclosure_score")
    score = number(row.get("score"), "score")
    if score != default_score(news, disclosure):
        raise ContractError("relationship score differs from its source components")
    evidence_count = _count(row.get("evidence_count"), "evidence_count")
    if score is None or evidence_count == 0:
        raise ContractError("published relationships require a score and evidence")
    return {
        **{field: row[field] for field in required}, "window_type": window,
        "news_score": news, "disclosure_score": disclosure, "score": score,
        "confidence": number(row.get("confidence"), "confidence", 1),
        "evidence_count": evidence_count, "impact_direction": row.get("impact_direction"),
        "period_start": start, "period_end": as_of,
    }


def prepare_verified_run(root: Path, lake_root: str, run_id: str) -> VerifiedRun:
    marker = _read_object(root / f"metadata/runs/run_id={run_id}/_VERIFIED")
    publication = marker.get("publication_sha256")
    if marker.get("run_id") != run_id or marker.get("status") != "HDFS_VERIFIED_ONLY" or not re.fullmatch(r"[0-9a-f]{64}", str(publication)):
        raise ContractError("invalid global _VERIFIED marker")
    manifests = {}
    for name, path in DATASETS.items():
        manifest_path = root / f"{path}/run_id={run_id}/manifest.json"
        manifest = _read_object(manifest_path)
        expected_run_path = f"/data-lake/{path}/run_id={run_id}"
        if (manifest.get("run_id") != run_id or manifest.get("status") != "HDFS_VERIFIED_ONLY"
                or manifest.get("publication_sha256") != publication or manifest.get("run_path") != expected_run_path):
            raise ContractError(f"{name} manifest does not match the verified run")
        manifests[name] = manifest
    identities = {(m.get("snapshot_id"), m.get("as_of")) for m in manifests.values()}
    if len(identities) != 1:
        raise ContractError("run manifests disagree on snapshot_id or as_of")
    snapshot_id, as_of_text = identities.pop()
    as_of = timestamp(as_of_text, "as_of")
    relation_item = _dataset(manifests["relationship_scores"], "relationship_scores")
    metric_item = _dataset(manifests["company_metrics"], "company_metrics")
    graph_names = {item.get("name") for item in manifests["graph"].get("datasets", []) if isinstance(item, dict)}
    if graph_names != {"graph_nodes", "graph_edges"}:
        raise ContractError("graph manifest must contain nodes and edges")
    _validate_hdfs_checksums(lake_root, relation_item)
    _validate_hdfs_checksums(lake_root, metric_item)
    relation_raw = _validate_dataset(root / f"aggregated/relationship-scores/run_id={run_id}", relation_item, expected_path="data")
    metric_raw = _validate_dataset(root / f"aggregated/company-metrics/run_id={run_id}", metric_item, expected_path="data")
    graph_root = root / f"snapshots/graph/run_id={run_id}"
    graph_rows = {}
    for name in ("graph_nodes", "graph_edges"):
        item = _dataset(manifests["graph"], name)
        _validate_hdfs_checksums(lake_root, item)
        graph_rows[name] = _validate_dataset(graph_root, item, expected_path=item["dataset_path"])
    if any(str(row.get("snapshot_id")) != str(snapshot_id) for row in graph_rows["graph_nodes"] + graph_rows["graph_edges"]):
        raise ContractError("graph rows reference a different snapshot_id")
    relation_keys = {
        (row.get("source_market"), row.get("source_stock_code"), row.get("target_market"),
         row.get("target_stock_code"), row.get("relationship_type_code"), row.get("window_type"))
        for row in relation_raw
    }
    edge_keys = {
        (row.get("source_market"), row.get("source_stock_code"), row.get("target_market"),
         row.get("target_stock_code"), row.get("relationship_type_code"), row.get("window_type"))
        for row in graph_rows["graph_edges"]
    }
    if relation_keys != edge_keys:
        raise ContractError("graph edges and relationship scores have different identities")
    formula_versions = {row.get("formula_version") for row in relation_raw}
    if len(formula_versions) != 1 or not next(iter(formula_versions), None):
        raise ContractError("relationship run needs one formula_version")
    formula_version = next(iter(formula_versions))
    manifest = Manifest(
        snapshot_id=__import__("uuid").UUID(str(snapshot_id)), as_of_at=as_of,
        formula_version=formula_version, model_version="hdfs-verified-v1",
        hdfs_uri=f"{lake_root.rstrip('/')}/snapshots/graph/run_id={run_id}", windows=WINDOWS,
        record_count=len(relation_raw), window_counts={w: sum(r.get("window_type") == w for r in relation_raw) for w in WINDOWS},
        files=(), content_hash=publication,
    )
    return VerifiedRun(run_id, publication, manifest,
                       [_validate_relationship(row, as_of, formula_version) for row in relation_raw],
                       [_validate_metric(row, as_of) for row in metric_raw])
