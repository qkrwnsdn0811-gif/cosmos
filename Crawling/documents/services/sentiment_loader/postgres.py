"""Commit per-company AI sentiment and its evidence onto company_document rows.

The mention loader owns which (document, company) pairs exist; this loader only
fills the derived columns on pairs that are already there. A mention that the
analyzer later retracts must not come back to life through an enrichment batch,
so unmatched rows are counted and reported rather than inserted.

This is also where an article becomes visible. company_document.is_service_visible
is the gate all four news queries already filter on, and the mention loader now
inserts new pairs with it FALSE. Flipping it here, in the same transaction that
writes the sentiment and the evidence, is what makes "analysed" and "on screen"
the same event: a reader never sees a headline whose sentiment badge is a
placeholder, and a half-written batch shows nothing rather than something wrong.

Evidence sentences are replaced per (document, company) rather than merged. The
sentences are derived from the article text, so a re-run with a changed article
or a changed guard must not leave last run's sentences behind next to this
run's verdict.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

TEMP_TABLE = "sentiment_enrichment_batch"
EVIDENCE_TEMP_TABLE = "sentiment_evidence_batch"


class SentimentLoadError(RuntimeError):
    pass


def _rows_tuple(rows: Iterable[Mapping[str, Any]]):
    for row in rows:
        yield (row["url_hash"], row["market"], row["stock_code"], row["sentiment"],
               row["relevance_score"], row["impact_score"], row["confidence"])


def _evidence_tuple(rows: Iterable[Mapping[str, Any]]):
    for row in rows:
        for item in row.get("evidence") or []:
            yield (row["url_hash"], row["market"], row["stock_code"],
                   item["sentence_text"], item["sentence_order"], item["confidence"])


def load_sentiment(connection, rows: Iterable[Mapping[str, Any]], *,
                   model_version: str, commit: bool = False) -> dict[str, Any]:
    """Apply one enrichment batch; replaying identical rows is a no-op.

    No connection commit or rollback is attempted when the caller already has a
    transaction in progress, matching the document loader's contract.
    """
    if not isinstance(commit, bool):
        raise SentimentLoadError("commit must be a boolean")
    if not model_version or not model_version.strip():
        raise SentimentLoadError("model_version is required")
    if connection.info.transaction_status != 0:
        raise SentimentLoadError(
            "sentiment loading requires an idle connection; caller transaction was left untouched")

    rows = list(rows)
    payload = list(_rows_tuple(rows))
    evidence_payload = list(_evidence_tuple(rows))
    if not payload:
        return {"incoming": 0, "updated": 0, "unchanged": 0, "unmatched": 0,
                "evidence": 0, "published": 0, "status": "empty"}

    try:
        with connection.transaction():
            cursor = connection.cursor()
            cursor.execute(f"""
                CREATE TEMP TABLE {TEMP_TABLE} (
                    url_hash CHAR(64) NOT NULL, market VARCHAR(30) NOT NULL,
                    stock_code VARCHAR(30) NOT NULL, sentiment VARCHAR(30) NOT NULL,
                    relevance_score NUMERIC(7,6) NOT NULL, impact_score NUMERIC(7,6) NOT NULL,
                    confidence NUMERIC(7,6) NOT NULL
                ) ON COMMIT DROP
                """)
            with cursor.copy(f"COPY {TEMP_TABLE} "
                             "(url_hash, market, stock_code, sentiment, relevance_score, "
                             "impact_score, confidence) FROM STDIN") as copy:
                for record in payload:
                    copy.write_row(record)

            cursor.execute(f"""
                CREATE TEMP TABLE {EVIDENCE_TEMP_TABLE} (
                    url_hash CHAR(64) NOT NULL, market VARCHAR(30) NOT NULL,
                    stock_code VARCHAR(30) NOT NULL, sentence_text TEXT NOT NULL,
                    sentence_order INTEGER NOT NULL, confidence NUMERIC(7,6)
                ) ON COMMIT DROP
                """)
            with cursor.copy(f"COPY {EVIDENCE_TEMP_TABLE} "
                             "(url_hash, market, stock_code, sentence_text, sentence_order, "
                             "confidence) FROM STDIN") as copy:
                for record in evidence_payload:
                    copy.write_row(record)

            # Resolving through news_article keeps this to news documents: a
            # disclosure never carries a canonical_url_hash.
            cursor.execute(f"""
                CREATE TEMP TABLE {TEMP_TABLE}_resolved ON COMMIT DROP AS
                SELECT article.document_id, company.company_id, batch.sentiment,
                       batch.relevance_score, batch.impact_score, batch.confidence
                  FROM {TEMP_TABLE} batch
                  JOIN news_article article ON article.canonical_url_hash = batch.url_hash
                  JOIN company company ON company.market = batch.market
                                      AND company.stock_code = batch.stock_code
                                      AND company.status = 'ACTIVE'
                """)
            cursor.execute(f"SELECT count(*) FROM {TEMP_TABLE}_resolved")
            resolved = cursor.fetchone()[0]

            # model_version stays with the mention analyzer that owns this row: it
            # drives the loader's freshness guard and the preservation rule that
            # keeps this sentiment alive across a republish. Overwriting it here
            # would make both compare against the wrong model and defeat them.
            cursor.execute(f"""
                UPDATE company_document AS target
                   SET sentiment = resolved.sentiment,
                       relevance_score = resolved.relevance_score,
                       impact_score = resolved.impact_score,
                       confidence = resolved.confidence
                  FROM {TEMP_TABLE}_resolved AS resolved
                 WHERE target.document_id = resolved.document_id
                   AND target.company_id = resolved.company_id
                   AND (target.sentiment, target.relevance_score, target.impact_score,
                        target.confidence)
                       IS DISTINCT FROM
                       (resolved.sentiment, resolved.relevance_score, resolved.impact_score,
                        resolved.confidence)
                """)
            updated = cursor.rowcount

            # Replace, do not merge: the sentences are derived from the article,
            # so a re-run must not leave a previous run's sentences beside this
            # run's verdict. Only pairs present in this batch are touched.
            #
            # Scoped to this producer's own rows. Other producers put sentences
            # here too, and relationship_evidence holds a foreign key onto
            # (evidence_id, document_id) with no cascade - deleting a sentence a
            # relation cites fails the whole batch. The relation loader's rows
            # and the backfill's 18,400 both live in this table under their own
            # model_version.
            cursor.execute(f"""
                DELETE FROM document_evidence AS old
                 USING {TEMP_TABLE}_resolved AS resolved
                 WHERE old.document_id = resolved.document_id
                   AND old.company_id = resolved.company_id
                   AND old.model_version = %s
                """, (model_version,))
            # The join to company_document is load-bearing, not redundant with
            # the resolved table: resolving only proves the article and the
            # company exist. A mention the analyzer has since retracted has no
            # pair row left, and document_evidence is keyed on that pair, so
            # inserting without this check raises a foreign key violation and
            # takes the whole batch down with it.
            cursor.execute(f"""
                INSERT INTO document_evidence
                    (document_id, company_id, sentence_text, sentence_order,
                     confidence, model_version)
                SELECT resolved.document_id, resolved.company_id, batch.sentence_text,
                       batch.sentence_order, batch.confidence, %s
                  FROM {EVIDENCE_TEMP_TABLE} batch
                  JOIN news_article article ON article.canonical_url_hash = batch.url_hash
                  JOIN company company ON company.market = batch.market
                                      AND company.stock_code = batch.stock_code
                  JOIN {TEMP_TABLE}_resolved AS resolved
                    ON resolved.document_id = article.document_id
                   AND resolved.company_id = company.company_id
                  JOIN company_document pair
                    ON pair.document_id = resolved.document_id
                   AND pair.company_id = resolved.company_id
                """, (model_version,))
            evidence = cursor.rowcount

            # The gate. Only pairs that now carry a verdict become visible, and
            # only after the evidence beside them exists.
            cursor.execute(f"""
                UPDATE company_document AS target
                   SET is_service_visible = TRUE
                  FROM {TEMP_TABLE}_resolved AS resolved
                 WHERE target.document_id = resolved.document_id
                   AND target.company_id = resolved.company_id
                   AND target.sentiment IS NOT NULL
                   AND target.is_service_visible IS DISTINCT FROM TRUE
                """)
            published = cursor.rowcount

            cursor.execute(f"""
                SELECT count(*) FROM {TEMP_TABLE}_resolved AS resolved
                 WHERE NOT EXISTS (
                    SELECT 1 FROM company_document target
                     WHERE target.document_id = resolved.document_id
                       AND target.company_id = resolved.company_id)
                """)
            missing_link = cursor.fetchone()[0]

            if not commit:
                raise _DryRun()
    except _DryRun:
        return {"incoming": len(payload), "resolved": resolved, "updated": updated,
                "unchanged": resolved - missing_link - updated,
                "unmatched": len(payload) - resolved, "missing_link": missing_link,
                "evidence": evidence, "published": published,
                "sentiment_model_version": model_version, "mode": "dry-run", "status": "rolled_back"}

    return {"incoming": len(payload), "resolved": resolved, "updated": updated,
            "unchanged": resolved - missing_link - updated,
            "unmatched": len(payload) - resolved, "missing_link": missing_link,
            "evidence": evidence, "published": published,
            "sentiment_model_version": model_version, "mode": "commit", "status": "committed"}


class _DryRun(Exception):
    """Unwinds the transaction so a dry run exercises every write and keeps none."""
