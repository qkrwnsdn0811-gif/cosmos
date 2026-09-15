"""Atomically publish one validated full relationship snapshot to PostgreSQL.

The caller verifies the manifest and every source file before opening the database
connection. This module deliberately does not ingest companies or evidence: their
stable reference IDs must already exist in the service database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class PublishError(ValueError):
    """An input snapshot cannot safely replace the published graph."""


class SnapshotConflictError(PublishError):
    """A snapshot ID has already been used for different or unverifiable input."""


class StaleSnapshotError(PublishError):
    """A new snapshot would move the graph clock backwards or make it ambiguous."""


@dataclass(frozen=True)
class PublishResult:
    snapshot_id: str
    stored_count: int
    already_published: bool


_WINDOWS = ("7D", "30D", "90D")
_COPY_COLUMNS = (
    "source_company_id", "target_company_id", "relationship_type", "window_type",
    "news_score", "disclosure_score", "score", "confidence", "evidence_count",
    "impact_direction", "period_start", "period_end",
)
# One database-wide writer lock, shared by all invocations regardless of snapshot.
_ADVISORY_LOCK = (1129272137, 1380205132)


_CREATE_STAGE = """
CREATE TEMP TABLE _rdb_relationship_stage (
    source_company_id UUID NOT NULL,
    target_company_id UUID NOT NULL,
    relationship_type VARCHAR(50) NOT NULL,
    window_type VARCHAR(30) NOT NULL CHECK (window_type IN ('7D', '30D', '90D')),
    news_score NUMERIC(18,6) CHECK (news_score BETWEEN 0 AND 100),
    disclosure_score NUMERIC(18,6) CHECK (disclosure_score BETWEEN 0 AND 100),
    score NUMERIC(18,6) NOT NULL CHECK (score BETWEEN 0 AND 100),
    confidence NUMERIC(7,6) CHECK (confidence BETWEEN 0 AND 1),
    evidence_count INTEGER NOT NULL CHECK (evidence_count > 0),
    impact_direction VARCHAR(30),
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source_company_id, target_company_id, relationship_type, window_type),
    CHECK (source_company_id <> target_company_id),
    CHECK (news_score IS NOT NULL OR disclosure_score IS NOT NULL),
    CHECK (score = round(COALESCE((news_score + disclosure_score) / 2,
                                  news_score, disclosure_score), 6)),
    CHECK (period_end >= period_start)
) ON COMMIT DROP
"""

_CANONICAL_STAGE = """
CREATE TEMP TABLE _rdb_relationship_canonical ON COMMIT DROP AS
SELECT CASE WHEN t.directionality = 'UNDIRECTED'
            THEN LEAST(s.source_company_id, s.target_company_id)
            ELSE s.source_company_id END AS source_company_id,
       CASE WHEN t.directionality = 'UNDIRECTED'
            THEN GREATEST(s.source_company_id, s.target_company_id)
            ELSE s.target_company_id END AS target_company_id,
       t.relationship_type_id, t.directionality,
       s.window_type, s.news_score, s.disclosure_score, s.score,
       s.confidence, s.evidence_count, s.impact_direction, s.period_start, s.period_end
FROM _rdb_relationship_stage s
JOIN relationship_type t ON t.code = s.relationship_type
"""

# Reuse a pre-existing reverse-oriented undirected relationship instead of
# replacing its UUID and disconnecting relationship_evidence/history references.
_MATCH_IDENTITY = """
r.relationship_type_id = c.relationship_type_id
AND ((r.source_company_id = c.source_company_id
      AND r.target_company_id = c.target_company_id)
     OR (c.directionality = 'UNDIRECTED'
         AND r.source_company_id = c.target_company_id
         AND r.target_company_id = c.source_company_id))
"""


def publish_snapshot(
    connection: Any, manifest: Any, rows: Iterable[Mapping[str, Any]],
) -> PublishResult:
    """Publish and commit CURRENT, HISTORY, and the snapshot marker together.

    ``connection`` is an idle psycopg 3 connection. ``manifest`` provides
    snapshot_id, as_of_at, formula_version, model_version, hdfs_uri,
    content_hash (SHA-256 of the canonical manifest), record_count, windows,
    and optional allow_empty. ``rows`` contains fully validated, visible rows;
    both-NULL rows have already been omitted, but record_count includes them.

    A verified exact retry returns without changing CURRENT, even if a newer
    snapshot was published in the meantime. Every other new snapshot must have
    a strictly newer as_of_at. Exceptions roll back the entire publication.
    """
    from psycopg.pq import TransactionStatus
    from psycopg.rows import tuple_row

    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise PublishError("publish_snapshot requires an idle connection")
    if tuple(manifest.windows) != _WINDOWS:
        raise PublishError("a full snapshot must declare windows 7D, 30D, and 90D")
    if manifest.record_count < 0:
        raise PublishError("record_count must not be negative")

    result = None
    with connection.transaction():
        with connection.cursor(row_factory=tuple_row) as cursor:
            # READ COMMITTED takes a fresh view after waiting for the writer lock.
            # A caller's REPEATABLE READ default must not hide a newer publication.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", _ADVISORY_LOCK)
            cursor.execute(
                """
                SELECT g.status, g.as_of_at, g.formula_version, g.model_version,
                       g.hdfs_uri, l.manifest_sha256, l.record_count,
                       l.stored_count, l.windows
                FROM graph_snapshot g
                LEFT JOIN graph_snapshot_load l ON l.snapshot_id = g.snapshot_id
                WHERE g.snapshot_id = %s
                """, (manifest.snapshot_id,),
            )
            previous = cursor.fetchone()
            if previous is not None:
                expected = (
                    "PUBLISHED", manifest.as_of_at, manifest.formula_version,
                    manifest.model_version, manifest.hdfs_uri,
                    manifest.content_hash, manifest.record_count,
                )
                if previous[:7] != expected or tuple(previous[8] or ()) != _WINDOWS:
                    raise SnapshotConflictError(
                        "snapshot_id already exists with different or unverifiable content"
                    )
                return PublishResult(str(manifest.snapshot_id), previous[7], True)

            cursor.execute("SELECT max(as_of_at) FROM graph_snapshot WHERE status = 'PUBLISHED'")
            latest = cursor.fetchone()[0]
            if latest is not None and manifest.as_of_at <= latest:
                raise StaleSnapshotError(
                    "a new snapshot as_of_at must be later than the latest published snapshot"
                )

            cursor.execute(_CREATE_STAGE)
            stored_count = 0
            with cursor.copy(
                "COPY _rdb_relationship_stage (" + ", ".join(_COPY_COLUMNS) + ") FROM STDIN"
            ) as copy:
                for row in rows:
                    copy.write_row(tuple(row[column] for column in _COPY_COLUMNS))
                    stored_count += 1
            if stored_count > manifest.record_count:
                raise PublishError("stored row count exceeds the manifest record_count")
            if not stored_count and not getattr(manifest, "allow_empty", False):
                raise PublishError("empty visible snapshot requires explicit allow_empty")

            cursor.execute(
                """
                SELECT s.source_company_id, s.target_company_id, s.relationship_type
                FROM _rdb_relationship_stage s
                LEFT JOIN company src ON src.company_id = s.source_company_id
                LEFT JOIN company dst ON dst.company_id = s.target_company_id
                LEFT JOIN relationship_type t ON t.code = s.relationship_type
                WHERE src.company_id IS NULL OR dst.company_id IS NULL
                   OR t.relationship_type_id IS NULL
                LIMIT 1
                """
            )
            if cursor.fetchone() is not None:
                raise PublishError("snapshot references an unknown company or relationship type")
            cursor.execute(
                """
                SELECT 1 FROM _rdb_relationship_stage
                WHERE period_end <> %s
                   OR period_start <> %s - CASE window_type
                       WHEN '7D' THEN interval '7 days'
                       WHEN '30D' THEN interval '30 days'
                       WHEN '90D' THEN interval '90 days' END
                LIMIT 1
                """, (manifest.as_of_at, manifest.as_of_at),
            )
            if cursor.fetchone() is not None:
                raise PublishError("row periods must match the manifest as_of_at and window")

            cursor.execute(_CANONICAL_STAGE)
            # Detect opposite-orientation duplicates in the input after normalizing.
            cursor.execute(
                """ALTER TABLE _rdb_relationship_canonical ADD PRIMARY KEY
                (source_company_id, target_company_id, relationship_type_id, window_type)"""
            )
            cursor.execute(
                """
                SELECT c.source_company_id, c.target_company_id, c.relationship_type_id
                FROM (SELECT DISTINCT source_company_id, target_company_id,
                             relationship_type_id, directionality
                      FROM _rdb_relationship_canonical) c
                JOIN company_relationship r ON """ + _MATCH_IDENTITY + """
                GROUP BY c.source_company_id, c.target_company_id, c.relationship_type_id
                HAVING count(*) > 1 LIMIT 1
                """
            )
            if cursor.fetchone() is not None:
                raise PublishError("existing undirected relationship has ambiguous duplicate identities")

            cursor.execute(
                """
                INSERT INTO company_relationship
                    (source_company_id, target_company_id, relationship_type_id)
                SELECT DISTINCT c.source_company_id, c.target_company_id, c.relationship_type_id
                FROM _rdb_relationship_canonical c
                WHERE NOT EXISTS (SELECT 1 FROM company_relationship r WHERE """
                + _MATCH_IDENTITY + """)
                ON CONFLICT (source_company_id, target_company_id, relationship_type_id)
                DO NOTHING
                """
            )
            cursor.execute(
                """
                CREATE TEMP TABLE _rdb_relationship_resolved ON COMMIT DROP AS
                SELECT r.relationship_id, c.* FROM _rdb_relationship_canonical c
                JOIN company_relationship r ON """ + _MATCH_IDENTITY
            )
            cursor.execute(
                "ALTER TABLE _rdb_relationship_resolved ADD PRIMARY KEY (relationship_id, window_type)"
            )
            cursor.execute("SELECT count(*) FROM _rdb_relationship_resolved")
            if cursor.fetchone()[0] != stored_count:
                raise PublishError("relationship reference resolution changed the row count")

            cursor.execute(
                """
                INSERT INTO graph_snapshot
                    (snapshot_id, as_of_at, formula_version, model_version, status, hdfs_uri)
                VALUES (%s, %s, %s, %s, 'LOADING', %s)
                """,
                (manifest.snapshot_id, manifest.as_of_at, manifest.formula_version,
                 manifest.model_version, manifest.hdfs_uri),
            )
            cursor.execute(
                """
                INSERT INTO relationship_score_history
                    (relationship_id, snapshot_id, window_type, news_score, disclosure_score,
                     score, impact_direction, confidence, period_start, period_end, formula_version)
                SELECT relationship_id, %s, window_type, news_score, disclosure_score,
                       score, impact_direction, confidence, period_start, period_end, %s
                FROM _rdb_relationship_resolved
                """, (manifest.snapshot_id, manifest.formula_version),
            )
            # DELETE is MVCC-safe: readers retain the previously committed graph
            # until commit. Keeping company_relationship preserves evidence/history.
            cursor.execute("DELETE FROM relationship_score_current")
            cursor.execute(
                """
                INSERT INTO relationship_score_current
                    (relationship_id, window_type, snapshot_id, news_score, disclosure_score,
                     score, impact_direction, confidence, evidence_count, formula_version, as_of_at)
                SELECT relationship_id, window_type, %s, news_score, disclosure_score,
                       score, impact_direction, confidence, evidence_count, %s, %s
                FROM _rdb_relationship_resolved
                """, (manifest.snapshot_id, manifest.formula_version, manifest.as_of_at),
            )
            cursor.execute(
                """
                INSERT INTO graph_snapshot_load
                    (snapshot_id, manifest_sha256, record_count, stored_count, windows)
                VALUES (%s, %s, %s, %s, %s)
                """, (manifest.snapshot_id, manifest.content_hash, manifest.record_count,
                        stored_count, list(_WINDOWS)),
            )
            # Publish last, in the same transaction as both score tables and receipt.
            cursor.execute(
                """UPDATE graph_snapshot SET status = 'PUBLISHED', published_at = clock_timestamp()
                   WHERE snapshot_id = %s""", (manifest.snapshot_id,),
            )
            result = PublishResult(str(manifest.snapshot_id), stored_count, False)
    return result
