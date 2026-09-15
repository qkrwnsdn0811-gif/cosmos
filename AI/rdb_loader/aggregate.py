"""Reference aggregation of normalized, per-document relationship features.

This does not infer scores from articles, ownership, or a graph-wide seed. The
producer must supply comparable 0..100 scores linked to real company/document
UUIDs. ``evidence-mean-v1`` averages distinct documents within each source and
then gives the two observed source means equal weight. Windows are UTC
``[as_of_at - days, as_of_at)``; missing evidence stays NULL, including empty
shorter windows of a relationship observed in the 90-day window.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Iterable, Mapping
from uuid import UUID

FORMULA_VERSION = "evidence-mean-v1"
WINDOWS = {"7D": 7, "30D": 30, "90D": 90}
PRECISION = Decimal("0.000001")
RELATIONSHIP_FIELDS = ("source_company_id", "target_company_id", "relationship_type")
DOCUMENT_KEY_FIELDS = (*RELATIONSHIP_FIELDS, "document_id")
FEATURE_FIELDS = (*DOCUMENT_KEY_FIELDS, "document_type", "score", "published_at",
                  "confidence", "impact_direction")


def utc_timestamp(value: str | datetime, field: str = "as_of_at") -> datetime:
    """Reject timezone-free dates rather than silently depending on host time."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _number(value: Any, field: str, maximum: int) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} must be a finite number between 0 and {maximum}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not parsed.is_finite() or not 0 <= parsed <= maximum:
        raise ValueError(f"{field} must be a finite number between 0 and {maximum}")
    return parsed.quantize(PRECISION, rounding=ROUND_HALF_UP)


def normalize_feature(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one evidence row; canonicalize known undirected relationships."""
    required = FEATURE_FIELDS[:-2]
    missing = [field for field in required if field not in record]
    if missing:
        raise ValueError(f"Missing feature fields: {', '.join(missing)}")
    result = {}
    for field in ("source_company_id", "target_company_id", "document_id"):
        try:
            result[field] = str(UUID(str(record[field])))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"{field} must be a UUID") from exc
    if result["source_company_id"] == result["target_company_id"]:
        raise ValueError("Self relationships are not allowed")
    relation = record["relationship_type"]
    if not isinstance(relation, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,49}", relation):
        raise ValueError("relationship_type must be an uppercase relationship code")
    result["relationship_type"] = relation
    if relation in {"PARTNER", "COMPETE"}:
        result["source_company_id"], result["target_company_id"] = sorted(
            (result["source_company_id"], result["target_company_id"]))
    if record["document_type"] not in {"NEWS", "DISCLOSURE"}:
        raise ValueError("document_type must be NEWS or DISCLOSURE")
    result["document_type"] = record["document_type"]
    result["score"] = _number(record["score"], "score", 100)
    result["published_at"] = utc_timestamp(record["published_at"], "published_at")
    confidence = record.get("confidence")
    result["confidence"] = None if confidence is None else _number(confidence, "confidence", 1)
    direction = record.get("impact_direction")
    if direction is not None and direction not in {"POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"}:
        raise ValueError("impact_direction must be POSITIVE, NEGATIVE, NEUTRAL, MIXED, or null")
    result["impact_direction"] = direction
    return result


def _mean(values: list[Decimal]) -> Decimal | None:
    return (sum(values) / len(values)).quantize(PRECISION, rounding=ROUND_HALF_UP) if values else None


def aggregate_records(records: Iterable[Mapping[str, Any]], *, as_of_at: str | datetime) -> list[dict[str, Any]]:
    """Pure reference implementation; Spark uses distributed equivalents.

    Duplicate document/relationship features must agree after normalization.
    Rejecting conflicts avoids arbitrary Spark partition order changing a score.
    Documents on or after the cutoff are excluded to prevent future leakage.
    """
    cutoff = utc_timestamp(as_of_at)
    oldest = cutoff - timedelta(days=90)
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    document_identity: dict[str, tuple[Any, ...]] = {}
    for raw in records:
        row = normalize_feature(raw)
        # A source_document UUID must retain its source and publication time,
        # including when it provides evidence for several relationships.
        document_identity_value = (row["document_type"], row["published_at"])
        prior_identity = document_identity.setdefault(row["document_id"], document_identity_value)
        if prior_identity != document_identity_value:
            raise ValueError(f"Conflicting identity for document {row['document_id']}")
        key = tuple(row[field] for field in DOCUMENT_KEY_FIELDS)
        if key in unique and unique[key] != row:
            raise ValueError(f"Conflicting duplicate feature for document {row['document_id']}")
        unique[key] = row

    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in unique.values():
        if oldest <= row["published_at"] < cutoff:
            groups[tuple(row[field] for field in RELATIONSHIP_FIELDS)].append(row)

    output = []
    for key, evidence in sorted(groups.items()):
        for window, days in WINDOWS.items():
            start = cutoff - timedelta(days=days)
            observed = [r for r in evidence if start <= r["published_at"]]
            news = _mean([r["score"] for r in observed if r["document_type"] == "NEWS"])
            disclosure = _mean([r["score"] for r in observed if r["document_type"] == "DISCLOSURE"])
            common = _mean([s for s in (news, disclosure) if s is not None])
            confidence = _mean([r["confidence"] for r in observed if r["confidence"] is not None])
            directions = {r["impact_direction"] for r in observed if r["impact_direction"] is not None}
            output.append({
                **dict(zip(RELATIONSHIP_FIELDS, key)), "window_type": window,
                "period_start": start.isoformat(), "period_end": cutoff.isoformat(),
                "as_of_at": cutoff.isoformat(), "formula_version": FORMULA_VERSION,
                "news_score": news, "disclosure_score": disclosure, "score": common,
                "confidence": confidence, "evidence_count": len(observed),
                "impact_direction": next(iter(directions)) if len(directions) == 1 else None,
            })
    return output
