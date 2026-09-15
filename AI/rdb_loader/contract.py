"""Versioned, full-snapshot contract shared by HDFS producers and the loader."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

WINDOWS = ("7D", "30D", "90D")
SCALE = Decimal("0.000001")


class ContractError(ValueError):
    """An incomplete or inconsistent input must never replace the live graph."""


def timestamp(value: Any, field: str) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ContractError(f"{field} must be an ISO timestamp with a timezone") from exc


def identifier(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContractError(f"{field} must be a UUID") from exc


def count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2147483647:
        raise ContractError(f"{field} must be a nonnegative 32-bit integer")
    return value


def number(value: Any, field: str, maximum: int = 100) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not 0 <= result <= maximum:
            raise ValueError("out of range")
        return result.quantize(SCALE, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ContractError(f"{field} must be NULL or a finite number in [0, {maximum}]") from exc


def default_score(news: Decimal | None, disclosure: Decimal | None) -> Decimal | None:
    if news is None:
        return disclosure
    if disclosure is None:
        return news
    return ((news + disclosure) / 2).quantize(SCALE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Manifest:
    snapshot_id: UUID
    as_of_at: datetime
    formula_version: str
    model_version: str
    hdfs_uri: str
    windows: tuple[str, ...]
    record_count: int
    window_counts: dict[str, int]
    files: tuple[tuple[str, str], ...]
    content_hash: str
    allow_empty: bool = False


def parse_manifest(data: dict, *, allow_empty: bool = False) -> Manifest:
    if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
        raise ContractError("manifest schema_version must be 1")
    if data.get("status") != "SUCCEEDED" or data.get("snapshot_mode") != "FULL":
        raise ContractError("only SUCCEEDED FULL snapshots can be published")
    if data.get("windows") != list(WINDOWS):
        raise ContractError("manifest must cover 7D, 30D, 90D in that order")
    for field in ("formula_version", "model_version"):
        if not isinstance(data.get(field), str) or not 1 <= len(data[field].strip()) <= 50:
            raise ContractError(f"{field} must contain 1..50 characters")
    uri = data.get("hdfs_uri", "")
    parsed = urlsplit(uri)
    if parsed.scheme != "hdfs" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.startswith("/") or parsed.path == "/":
        raise ContractError("hdfs_uri must identify an absolute HDFS snapshot directory")
    total = count(data.get("record_count"), "record_count")
    counts = data.get("window_counts")
    if not isinstance(counts, dict) or set(counts) != set(WINDOWS):
        raise ContractError("window_counts must explicitly include every window, including zero counts")
    counts = {window: count(counts[window], f"window_counts.{window}") for window in WINDOWS}
    if sum(counts.values()) != total:
        raise ContractError("window_counts sum differs from record_count")
    files = data.get("files")
    if not isinstance(files, list):
        raise ContractError("manifest files must be a list")
    normalized = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ContractError("manifest file entries must be objects")
        path, digest = entry.get("path"), entry.get("sha256")
        if not isinstance(path, str) or "\\" in path or ":" in path:
            raise ContractError("manifest file path must be relative POSIX syntax")
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or str(relative) != path or relative.suffix not in (".parquet", ".jsonl"):
            raise ContractError("manifest file path must be a normalized relative .parquet or .jsonl path")
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ContractError("manifest file sha256 must be 64 lowercase hex characters")
        normalized.append((path, digest))
    if len({path for path, _ in normalized}) != len(normalized):
        raise ContractError("manifest includes duplicate files")
    if total and not normalized:
        raise ContractError("nonempty snapshots must list data files")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    return Manifest(identifier(data.get("snapshot_id"), "snapshot_id"), timestamp(data.get("as_of_at"), "as_of_at"), data["formula_version"], data["model_version"], uri.rstrip("/"), WINDOWS, total, counts, tuple(normalized), digest, allow_empty)


def validate_row(data: dict, manifest: Manifest) -> dict:
    required = {"source_company_id", "target_company_id", "relationship_type", "window_type", "news_score", "disclosure_score", "score", "evidence_count", "period_start", "period_end"}
    if not isinstance(data, dict) or required - data.keys():
        raise ContractError("row missing required score, identity, count or period fields")
    row = {field: identifier(data[field], field) for field in ("source_company_id", "target_company_id")}
    if row["source_company_id"] == row["target_company_id"]:
        raise ContractError("self relationships cannot be published")
    relation_type = data["relationship_type"]
    if not isinstance(relation_type, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,49}", relation_type):
        raise ContractError("relationship_type must be a reference-table code")
    row["relationship_type"] = relation_type
    window = data["window_type"]
    if window not in WINDOWS:
        raise ContractError("window_type must be 7D, 30D or 90D")
    row["window_type"] = window
    row["period_start"] = timestamp(data["period_start"], "period_start")
    row["period_end"] = timestamp(data["period_end"], "period_end")
    if row["period_end"] != manifest.as_of_at or row["period_start"] != manifest.as_of_at - timedelta(days=int(window[:-1])):
        raise ContractError("row period must match the snapshot time and window")
    if "as_of_at" in data and timestamp(data["as_of_at"], "as_of_at") != manifest.as_of_at:
        raise ContractError("row as_of_at differs from manifest")
    if "formula_version" in data and data["formula_version"] != manifest.formula_version:
        raise ContractError("row formula_version differs from manifest")
    for field in ("news_score", "disclosure_score", "score"):
        row[field] = number(data[field], field)
    if row["score"] != default_score(row["news_score"], row["disclosure_score"]):
        raise ContractError("score must be the 50:50 mean, single-source value, or NULL when both are absent")
    row["evidence_count"] = count(data["evidence_count"], "evidence_count")
    if (row["score"] is None) != (row["evidence_count"] == 0):
        raise ContractError("evidence_count must be zero exactly when both component scores are absent")
    for source in ("news", "disclosure"):
        field = f"{source}_evidence_count"
        if field in data:
            n = count(data[field], field)
            if (n == 0) != (row[f"{source}_score"] is None) or n > row["evidence_count"]:
                raise ContractError(f"{field} is inconsistent with its component score")
    row["confidence"] = number(data.get("confidence"), "confidence", 1)
    direction = data.get("impact_direction")
    if direction not in (None, "POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED", "UNKNOWN"):
        raise ContractError("invalid impact_direction")
    row["impact_direction"] = direction
    return row
