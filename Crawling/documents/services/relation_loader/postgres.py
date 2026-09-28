"""Add news-derived relations to the published graph without rebuilding it.

The backend serves one snapshot: the PUBLISHED graph_snapshot with the latest
as_of_at, and edges filtered by that snapshot_id. Publishing a *new* snapshot
therefore replaces the whole graph, and anything not carried into it disappears
- 1,390 relations and 9,188 pieces of evidence, in one transaction. That is the
reason the reprocess pipeline rebuilds everything every time.

This loader does the opposite: it writes into the snapshot that is already
serving. New pairs get an edge, pairs that already exist get another piece of
evidence, and no existing row is deleted or rewritten. The graph only grows, and
rolling back means removing the rows this loader added.

What that costs: the snapshot's as_of_at stays where it was, so the relative
windows (30D, 1Y, 10Y) keep the boundary they were computed against. They drift
as evidence ages. Correcting that needs a full recompute and a new snapshot,
which is a separate, deliberate operation - not something a routine batch should
do while the site is being read.

Evidence is written to both tables it has to live in. relationship_evidence
carries a foreign key onto (evidence_id, document_id) in document_evidence, so
the sentence row goes first and both get the same deterministic id: the panel
reads the sentence through that key, and a relation whose sentence is missing
shows "N documents" with nothing underneath it, which is the defect this
pipeline was warned about.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Iterable, Mapping

TEMP_TABLE = "relation_batch"
MODEL_VERSION = "news-relations-1.0"
FORMULA_VERSION = "news-relations-1.0"

# One namespace so the same sentence always yields the same evidence_id: a
# replayed batch collides with itself and does nothing, instead of inserting a
# second copy of a sentence under a fresh uuid.
EVIDENCE_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


class RelationLoadError(RuntimeError):
    pass


def evidence_id(url_hash: str, stock_code: str, sentence_order: int) -> uuid.UUID:
    """Deterministic id for (document, company, sentence).

    Keyed the same way the reprocess package keys it, so the two producers
    cannot mint different ids for the same sentence.
    """
    name = f"{url_hash}:{stock_code}:{sentence_order}"
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return uuid.UUID(bytes=digest[:16], version=5)


def _rows_tuple(rows: Iterable[Mapping[str, Any]]):
    for row in rows:
        yield (row["url_hash"],
               row["source_market"], row["source_stock_code"],
               row["target_market"], row["target_stock_code"],
               row["relation_type"], row["sentence_order"], row["text"],
               float(row.get("contribution", 1.0)),
               str(evidence_id(row["url_hash"], row["source_stock_code"],
                               row["sentence_order"])))


def load_relations(connection, rows: Iterable[Mapping[str, Any]], *,
                   commit: bool = False) -> dict[str, Any]:
    """Add relations and their evidence to the currently published snapshot."""
    if not isinstance(commit, bool):
        raise RelationLoadError("commit must be a boolean")
    if connection.info.transaction_status != 0:
        raise RelationLoadError(
            "relation loading requires an idle connection; caller transaction was left untouched")

    payload = list(_rows_tuple(rows))
    if not payload:
        return {"incoming": 0, "relations_added": 0, "evidence_added": 0,
                "scores_added": 0, "status": "empty"}

    counts = {"resolved": 0, "relations_added": 0, "sentences_added": 0,
              "evidence_added": 0, "scores_added": 0, "unmatched": 0}
    try:
        with connection.transaction():
            cursor = connection.cursor()
            cursor.execute(f"""
                CREATE TEMP TABLE {TEMP_TABLE} (
                    url_hash CHAR(64) NOT NULL,
                    source_market VARCHAR(30) NOT NULL, source_stock_code VARCHAR(30) NOT NULL,
                    target_market VARCHAR(30) NOT NULL, target_stock_code VARCHAR(30) NOT NULL,
                    relation_type VARCHAR(30) NOT NULL, sentence_order INTEGER NOT NULL,
                    sentence_text TEXT NOT NULL, contribution NUMERIC(7,6) NOT NULL,
                    evidence_id UUID NOT NULL
                ) ON COMMIT DROP
                """)
            with cursor.copy(f"COPY {TEMP_TABLE} (url_hash, source_market, source_stock_code, "
                             "target_market, target_stock_code, relation_type, sentence_order, "
                             "sentence_text, contribution, evidence_id) FROM STDIN") as copy:
                for record in payload:
                    copy.write_row(record)

            # Everything downstream needs the document and both companies to
            # already exist. A relation is not a reason to invent either.
            cursor.execute(f"""
                CREATE TEMP TABLE {TEMP_TABLE}_resolved ON COMMIT DROP AS
                SELECT batch.*, article.document_id,
                       src.company_id AS source_company_id,
                       dst.company_id AS target_company_id,
                       rt.relationship_type_id
                  FROM {TEMP_TABLE} batch
                  JOIN news_article article ON article.canonical_url_hash = batch.url_hash
                  JOIN company src ON src.market = batch.source_market
                                  AND src.stock_code = batch.source_stock_code
                                  AND src.status = 'ACTIVE'
                  JOIN company dst ON dst.market = batch.target_market
                                  AND dst.stock_code = batch.target_stock_code
                                  AND dst.status = 'ACTIVE'
                  JOIN relationship_type rt ON rt.code = batch.relation_type
                  JOIN company_document cd_src ON cd_src.document_id = article.document_id
                                              AND cd_src.company_id = src.company_id
                """)
            cursor.execute(f"SELECT count(*) FROM {TEMP_TABLE}_resolved")
            counts["resolved"] = cursor.fetchone()[0]
            counts["unmatched"] = len(payload) - counts["resolved"]

            cursor.execute(f"""
                INSERT INTO company_relationship
                    (source_company_id, target_company_id, relationship_type_id)
                SELECT DISTINCT source_company_id, target_company_id, relationship_type_id
                  FROM {TEMP_TABLE}_resolved
                ON CONFLICT ON CONSTRAINT uk_company_relationship_identity DO NOTHING
                """)
            counts["relations_added"] = cursor.rowcount

            # The sentence row first: relationship_evidence has a foreign key
            # onto it and there is no cascade to fall back on.
            cursor.execute(f"""
                INSERT INTO document_evidence
                    (evidence_id, document_id, company_id, sentence_text, sentence_order,
                     model_version)
                SELECT DISTINCT ON (resolved.evidence_id)
                       resolved.evidence_id, resolved.document_id, resolved.source_company_id,
                       resolved.sentence_text, resolved.sentence_order, %s
                  FROM {TEMP_TABLE}_resolved AS resolved
                ON CONFLICT (evidence_id) DO NOTHING
                """, (MODEL_VERSION,))
            counts["sentences_added"] = cursor.rowcount

            cursor.execute(f"""
                INSERT INTO relationship_evidence
                    (relationship_id, document_id, evidence_id, contribution_score,
                     model_version, formula_version)
                SELECT DISTINCT cr.relationship_id, resolved.document_id, resolved.evidence_id,
                       resolved.contribution, %s, %s
                  FROM {TEMP_TABLE}_resolved AS resolved
                  JOIN company_relationship cr
                    ON cr.source_company_id = resolved.source_company_id
                   AND cr.target_company_id = resolved.target_company_id
                   AND cr.relationship_type_id = resolved.relationship_type_id
                  JOIN document_evidence de ON de.evidence_id = resolved.evidence_id
                                           AND de.document_id = resolved.document_id
                ON CONFLICT ON CONSTRAINT uk_relationship_evidence_identity DO NOTHING
                """, (MODEL_VERSION, FORMULA_VERSION))
            counts["evidence_added"] = cursor.rowcount

            # Score only the relations this batch touched. Everything else keeps
            # the numbers it was published with - that is the whole point of
            # adding to the live snapshot instead of rebuilding it.
            #
            # score = 100 * (1 - exp(-sum(contribution) / 5)), the formula
            # relationship_score_components.component() owns: one document at
            # full contribution is 18.1, which is what the panel shows today.
            # confidence follows recompute_confidence.sql's base term.
            # evidence_count has to move with the list or the panel says
            # "N documents" over a list of a different length.
            cursor.execute(f"""
                INSERT INTO relationship_score_current
                    (relationship_id, window_type, snapshot_id, score, news_score,
                     confidence, evidence_count, formula_version, as_of_at)
                SELECT touched.relationship_id, w.window_type, snap.snapshot_id,
                       scored.score, scored.score, scored.confidence,
                       scored.evidence_count, %s, snap.as_of_at
                  FROM (SELECT DISTINCT cr.relationship_id
                          FROM {TEMP_TABLE}_resolved r
                          JOIN company_relationship cr
                            ON cr.source_company_id = r.source_company_id
                           AND cr.target_company_id = r.target_company_id
                           AND cr.relationship_type_id = r.relationship_type_id) touched
                  CROSS JOIN (VALUES ('30D', 30), ('1Y', 365), ('10Y', 3650))
                             AS w(window_type, days)
                  CROSS JOIN (SELECT snapshot_id, as_of_at FROM graph_snapshot
                               WHERE status = 'PUBLISHED'
                               ORDER BY as_of_at DESC, snapshot_id DESC LIMIT 1) snap
                  CROSS JOIN LATERAL (
                      SELECT round((100 * (1 - exp(-coalesce(sum(re.contribution_score), 0) / 5)))::numeric, 6) AS score,
                             round((least(1.0, 0.50 + 0.35 * (1 - exp(-count(*)::numeric / 8))))::numeric, 6) AS confidence,
                             count(*)::int AS evidence_count
                        FROM relationship_evidence re
                        JOIN source_document sd ON sd.document_id = re.document_id
                       WHERE re.relationship_id = touched.relationship_id
                         AND sd.published_at IS NOT NULL
                         AND sd.published_at > snap.as_of_at - make_interval(days => w.days)
                  ) scored
                 WHERE scored.evidence_count > 0
                ON CONFLICT (relationship_id, window_type) DO UPDATE
                    SET score = EXCLUDED.score,
                        news_score = EXCLUDED.news_score,
                        confidence = EXCLUDED.confidence,
                        evidence_count = EXCLUDED.evidence_count,
                        snapshot_id = EXCLUDED.snapshot_id,
                        as_of_at = EXCLUDED.as_of_at
                """, (FORMULA_VERSION,))
            counts["scores_added"] = cursor.rowcount

            if not commit:
                raise _Rollback()
    except _Rollback:
        return {**counts, "incoming": len(payload), "mode": "dry-run", "status": "rolled_back"}

    return {**counts, "incoming": len(payload), "mode": "commit", "status": "committed"}


class _Rollback(Exception):
    """Unwinds the transaction so a dry run exercises every write and keeps none."""
