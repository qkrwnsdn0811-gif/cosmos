"""Roll per-document company sentiment up into company_metric_history windows.

``sentiment_score`` stays on the signed -1..1 scale the existing
``lake-window-v1`` rows already use, so one company's history stays comparable
across metric versions instead of jumping when the producer changes:

   -1.0  every mention in the window was negative
    0.0  neutral, or positives and negatives cancelling out
    1.0  every mention was positive

impact_score is already damped by how central the company is to each article,
so averaging it keeps headline coverage weightier than passing mentions.
Windows are measured back from one ``measured_at`` so 7D/30D/90D stay
comparable, and the upsert matches the rdb_loader contract on
(company_id, measured_at, window_type).
"""
from __future__ import annotations

from typing import Any

# The windows the API will actually serve. MetricWindow dropped 7D and 90D in
# V9/S15P21C205-177 and now answers 400 for them, so producing those rows would
# be work nothing can read.
WINDOWS = {"30D": 30, "1Y": 365, "10Y": 3650}
METRIC_VERSION = "news-company-sentiment-1.0"


class MetricsError(RuntimeError):
    pass


AGGREGATE_SQL = """
INSERT INTO company_metric_history
    (company_id, measured_at, window_type, news_mention_count,
     positive_count, negative_count, sentiment_score, relationship_count, metric_version)
SELECT link.company_id,
       %(measured_at)s::timestamptz,
       %(window_type)s,
       count(*),
       count(*) FILTER (WHERE link.sentiment = 'POSITIVE'),
       count(*) FILTER (WHERE link.sentiment = 'NEGATIVE'),
       round(avg(link.impact_score), 6),
       coalesce(max(edges.total), 0),
       %(metric_version)s
  FROM company_document link
  JOIN source_document document ON document.document_id = link.document_id
  LEFT JOIN LATERAL (
      SELECT count(*) AS total FROM company_relationship related
       WHERE related.source_company_id = link.company_id
          OR related.target_company_id = link.company_id
  ) edges ON TRUE
 WHERE link.sentiment IS NOT NULL
   AND link.is_service_visible
   AND document.document_type = 'NEWS'
   AND document.published_at >  %(measured_at)s::timestamptz - %(days)s * INTERVAL '1 day'
   AND document.published_at <= %(measured_at)s::timestamptz
 GROUP BY link.company_id
ON CONFLICT (company_id, measured_at, window_type) DO UPDATE SET
    news_mention_count = EXCLUDED.news_mention_count,
    positive_count = EXCLUDED.positive_count,
    negative_count = EXCLUDED.negative_count,
    sentiment_score = EXCLUDED.sentiment_score,
    relationship_count = EXCLUDED.relationship_count,
    metric_version = EXCLUDED.metric_version
"""


def rebuild_metrics(connection, *, measured_at: str, commit: bool = False,
                    metric_version: str = METRIC_VERSION) -> dict[str, Any]:
    """Recompute every window at one measurement time.

    No connection commit or rollback is attempted when the caller already has a
    transaction in progress, matching the document loader's contract.
    """
    if not isinstance(commit, bool):
        raise MetricsError("commit must be a boolean")
    if connection.info.transaction_status != 0:
        raise MetricsError(
            "metric rebuild requires an idle connection; caller transaction was left untouched")

    written: dict[str, int] = {}
    try:
        with connection.transaction():
            cursor = connection.cursor()
            for window_type, days in WINDOWS.items():
                cursor.execute(AGGREGATE_SQL, {"measured_at": measured_at,
                                               "window_type": window_type, "days": days,
                                               "metric_version": metric_version})
                written[window_type] = cursor.rowcount
            if not commit:
                raise _DryRun()
    except _DryRun:
        return {"measured_at": measured_at, "rows": written, "metric_version": metric_version,
                "mode": "dry-run", "status": "rolled_back"}
    return {"measured_at": measured_at, "rows": written, "metric_version": metric_version,
            "mode": "commit", "status": "committed"}


class _DryRun(Exception):
    """Unwinds the transaction so a dry run exercises every write and keeps none."""
