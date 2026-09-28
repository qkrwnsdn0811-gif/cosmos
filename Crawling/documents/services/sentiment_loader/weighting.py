"""Turn one enriched article into its per-company rows for Postgres.

The formula is the one fill_company_document_scores.py owns, reproduced here
because this loader is the live path and that script is the backfill path; both
must put the same number in the same column or a company's history jumps when
the producer changes.

    depth           = (clamp(n_sentences, 1, 4) - 1) / 3
    share           = 1 / n_companies
    relevance_score = 0.25 + 0.55*depth + 0.20*share        0.26 .. 1.00
    impact_score    = +1 / -1 / 0     POSITIVE / NEGATIVE / NEUTRAL
    confidence      = min(1.0, 0.40 + 0.15*n_sentences)

Why a rule and not a model: news features were measured against residual
returns and the correlation was ~0 (Spearman +0.0067 for sentence count), and
adding them to a volatility baseline lowered test rank-IC from +0.2056 to
+0.2032.  Relevance is a property of the text - is this article about this
company - not a property of the market, so there is nothing for price data to
supervise.

Two deliberate departures from a "fill everything in" loader:

- ``n_companies`` counts every company the matcher attached to the article,
  including ones whose own sentences were all ambiguous.  They still divide the
  article's attention, which is what share measures.
- a company with no attributed sentence yields no row at all.  Its
  relevance would be an extrapolation of a formula whose depth term starts at
  one sentence, and its sentiment is genuinely unknown.  Leaving the columns
  NULL says that; writing 0.45 and NEUTRAL would not.

impact_score is the sign only, not the polarity scaled by relevance, matching
the 174,537 rows already in the service so the two are comparable.
"""
from __future__ import annotations

from typing import Any, Mapping

ANALYZED_STATUS = "analyzed"
LABELS = {"POSITIVE", "NEGATIVE", "NEUTRAL"}
SIGN = {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0}
# p99 of sentences per company is 4, so depth saturates there instead of giving
# a long tail of articles extra credit for repeating a name.
DEPTH_CAP = 4
METRIC_VERSION = "news-company-sentiment-1.0"


class WeightingError(ValueError):
    pass


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def relevance(n_sentences: int, n_companies: int) -> float:
    depth = (_clamp(n_sentences, 1, DEPTH_CAP) - 1) / 3.0
    share = 1.0 / max(n_companies, 1)
    return _clamp(0.25 + 0.55 * depth + 0.20 * share, 0.0, 1.0)


def confidence(n_sentences: int) -> float:
    return _clamp(0.40 + 0.15 * n_sentences, 0.0, 1.0)


def company_rows(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand one enriched article into per-company rows with their evidence.

    Returns an empty list when nothing in the article reached a verdict, so
    those rows keep their existing values instead of being overwritten.
    """
    verdicts = record.get("ai_company_sentiment") or []
    if not verdicts:
        return []
    url_hash = record.get("url_hash")
    if not url_hash:
        raise WeightingError("enriched row is missing url_hash")
    n_companies = len(verdicts)

    rows = []
    for verdict in verdicts:
        if verdict.get("status") != ANALYZED_STATUS:
            continue
        label = (verdict.get("label") or "").strip().upper()
        if label not in LABELS:
            raise WeightingError(f"analyzed verdict has no usable label: {label!r}")
        n_sentences = verdict.get("n_sentences") or 0
        if n_sentences < 1:
            raise WeightingError("analyzed verdict has no evidence sentence")
        rows.append({
            "url_hash": url_hash,
            "market": verdict["market"],
            "stock_code": verdict["stock_code"],
            "sentiment": label,
            "relevance_score": round(relevance(n_sentences, n_companies), 6),
            "impact_score": SIGN[label],
            "confidence": round(confidence(n_sentences), 6),
            "evidence": [
                {
                    "sentence_order": item["sentence_order"],
                    "sentence_text": item["text"],
                    # NULL rather than a guess when the model gave no number.
                    "confidence": (None if item.get("confidence") is None
                                   else round(_clamp(float(item["confidence"]), 0.0, 1.0), 6)),
                }
                for item in verdict.get("evidence") or []
            ],
        })
    return rows
