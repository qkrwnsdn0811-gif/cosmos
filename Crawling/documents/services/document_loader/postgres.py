"""Atomically publish normalized news and filings to the existing service schema.

The caller owns an idle psycopg connection. Dry runs execute the real writes
inside a transaction and roll them back, including sources and the checkpoint.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
import hashlib
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .contract import validate_records


class LoadError(ValueError):
    """A batch cannot be safely mapped to the service database."""


class CompanyMappingError(LoadError):
    """A source-confirmed company has no exact active company mapping."""


class BatchConflictError(LoadError):
    """An immutable batch ID was reused with different input or configuration."""


def _text(value: Any, name: str, maximum: int | None = None) -> str:
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or "\x00" in value or (maximum is not None and len(value) > maximum)):
        raise LoadError(f"{name} must be a nonempty trimmed string" +
                        (f" of at most {maximum} characters" if maximum else ""))
    return value


def _sources(config: Mapping[str, Any], used: set[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(config, Mapping):
        raise LoadError("sources must be an explicit source configuration mapping")
    result = {}
    for key in sorted(used):
        if key not in config or not isinstance(config[key], Mapping):
            raise LoadError(f"missing explicit source configuration: {key}")
        source = config[key]
        base_url = source.get("base_url")
        if base_url is not None:
            _text(base_url, "base_url")
        result[key] = {
            "name": _text(source.get("name"), "source name", 100),
            "source_type": _text(source.get("source_type"), "source_type", 50),
            "base_url": base_url,
        }
    return result


def _identity(record: Mapping[str, Any]) -> tuple[str, str]:
    if record["document_type"] == "NEWS":
        return "NEWS", hashlib.sha256(record["canonical_url"].encode("utf-8")).hexdigest()
    system = record["filing_system"]
    # Co-registrants can publish the same accession under different issuer CIKs.
    # Both components use fixed, validated formats that cannot contain a colon.
    return (system, record["dart_receipt_no"] if system == "DART"
            else record["sec_cik"] + ":" + record["sec_accession_no"])


def _company_key(reference: Mapping[str, str]) -> tuple[str, str]:
    return reference["market"], reference["stock_code"]


def _company_mapping(cursor, records: list[dict[str, Any]]) -> dict[tuple[str, str], Any]:
    keys = {_company_key(ref) for record in records for ref in record.get("company_refs", [])}
    keys.update(_company_key(record["filing_company"]) for record in records
                if record["document_type"] == "DISCLOSURE")
    mapping = {}
    if keys:
        ordered = sorted(keys)
        cursor.execute("""
            SELECT company_id, market, stock_code FROM company
            WHERE status = 'ACTIVE' AND (market, stock_code) IN (
                SELECT * FROM unnest(%s::text[], %s::text[])
            ) ORDER BY market, stock_code FOR SHARE
            """, ([key[0] for key in ordered], [key[1] for key in ordered]))
        for company_id, market, stock_code in cursor.fetchall():
            key = market, stock_code
            if key in mapping:
                raise CompanyMappingError(f"ambiguous company: {market}/{stock_code}")
            mapping[key] = company_id
        missing = sorted(keys - mapping.keys())
        if missing:
            raise CompanyMappingError("unregistered or inactive companies: " +
                                      ", ".join(f"{market}/{code}" for market, code in missing))
    return mapping


def _source_mapping(cursor, config: dict[str, dict[str, Any]], register: bool):
    mapping, created = {}, 0
    for key, source in sorted(config.items()):
        cursor.execute("SELECT source_id, source_type, base_url, is_active FROM data_source "
                       "WHERE name = %s FOR SHARE", (source["name"],))
        row = cursor.fetchone()
        if row is None:
            if not register:
                raise LoadError(f"source is not registered: {source['name']}; explicitly enable source registration")
            source_id = uuid5(NAMESPACE_URL, "cosmos:data-source:" + source["name"])
            cursor.execute("""INSERT INTO data_source (source_id, name, source_type, base_url)
                              VALUES (%s, %s, %s, %s)""",
                           (source_id, source["name"], source["source_type"], source["base_url"]))
            created += 1
        else:
            source_id, source_type, base_url, active = row
            if not active or (source_type, base_url) != (source["source_type"], source["base_url"]):
                raise LoadError(f"source metadata or active status conflicts: {source['name']}")
        mapping[key] = source_id
    return mapping, created


def _existing_document(cursor, record):
    kind, key = _identity(record)
    if kind == "NEWS":
        cursor.execute("SELECT document_id, canonical_url FROM news_article WHERE canonical_url_hash = %s "
                       "FOR UPDATE", (key,))
        existing = cursor.fetchone()
        if existing and existing[1] != record["canonical_url"]:
            raise LoadError("canonical URL hash conflicts with an existing different URL")
    else:
        if kind == "DART":
            cursor.execute("SELECT document_id, filing_system FROM disclosure WHERE dart_receipt_no = %s FOR UPDATE", (key,))
        else:
            cursor.execute("""SELECT document_id, filing_system FROM disclosure
                              WHERE sec_cik = %s AND sec_accession_no = %s FOR UPDATE""",
                           (record["sec_cik"], record["sec_accession_no"]))
        existing = cursor.fetchone()
        if existing and existing[1] != kind:
            raise LoadError("filing identity conflicts with an existing filing system")
    return existing[0] if existing else None


COMMON_UPDATE = """
UPDATE source_document SET
    title = %s, summary = COALESCE(%s, summary),
    published_at = COALESCE(%s::timestamptz, published_at),
    first_collected_at = LEAST(first_collected_at, %s::timestamptz),
    content_hash = COALESCE(%s, content_hash), hdfs_raw_uri = %s, last_collected_at = %s,
    updated_at = CURRENT_TIMESTAMP
WHERE document_id = %s AND
    (title, summary, published_at, first_collected_at, content_hash, hdfs_raw_uri, last_collected_at)
    IS DISTINCT FROM
    (%s, COALESCE(%s, summary), COALESCE(%s::timestamptz, published_at),
     LEAST(first_collected_at, %s::timestamptz), COALESCE(%s, content_hash), %s, %s::timestamptz)
"""


def _write_document(cursor, record, source_id, companies):
    kind, natural_key = _identity(record)
    document_id = _existing_document(cursor, record)
    inserted = document_id is None
    updated = False
    accept_content = True
    accept_links = True
    latest_analysis = None
    analyzed_revision = False
    if inserted:
        document_id = uuid5(NAMESPACE_URL, f"cosmos:document:{kind}:{natural_key}")
        cursor.execute("""
            INSERT INTO source_document
              (document_id, source_id, document_type, title, summary, original_url,
               published_at, first_collected_at, content_hash, hdfs_raw_uri, last_collected_at,
               status, analysis_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (document_id, source_id, record["document_type"], record["title"],
                  record.get("summary"), record["original_url"], record.get("published_at"),
                  record["collected_at"], record.get("content_hash"), record["hdfs_raw_uri"], record["collected_at"],
                  record["status"], record.get("analysis_version")))
    else:
        cursor.execute("""SELECT document_type, last_collected_at, content_hash, status, analysis_version, hdfs_clean_uri
                          FROM source_document WHERE document_id = %s FOR UPDATE""", (document_id,))
        stored_type, last_collected_at, stored_hash, status, analysis_version, clean_uri = cursor.fetchone()
        if stored_type != record["document_type"]:
            raise LoadError("existing document type conflicts with its detail table")
        collected_at = datetime.fromisoformat(record["collected_at"])
        version_conflict = (kind == "NEWS" and status == "ANALYZED"
                            and record.get("status") == "ANALYZED"
                            and analysis_version != record.get("analysis_version"))
        historical_duplicate = (version_conflict
                                and str(record.get("analysis_version", "")).startswith("news-historical-"))
        if version_conflict and not historical_duplicate:
            raise LoadError("a different analysis_version requires an explicit NEWS reanalysis workflow")
        if historical_duplicate:
            # A historical backfill can rediscover a URL that the live
            # pipeline already analyzed under its realtime contract. Archive
            # and live renderings may have different hashes, while the schema
            # intentionally keeps one canonical document per URL.
            # Preserve that authoritative analysis and checkpoint this input;
            # do not silently turn the historical contract into reanalysis.
            accept_content = False
            accept_links = False
            cursor.execute("""UPDATE source_document SET first_collected_at = %s,
                              updated_at = CURRENT_TIMESTAMP
                              WHERE document_id = %s AND first_collected_at > %s""",
                           (collected_at, document_id, collected_at))
            updated = cursor.rowcount > 0
        if kind == "NEWS" and status == "ANALYZED" and record.get("status") == "COLLECTED":
            # Collection hints must never recreate links removed by the
            # authoritative analyzed company set.
            accept_links = False
        accept_content = accept_content and collected_at >= last_collected_at
        if accept_content:
            incoming_hash = record.get("content_hash")
            protected_news_revision = (
                kind == "NEWS"
                and record.get("status") == "COLLECTED"
                and incoming_hash is not None
                and stored_hash is not None
                and incoming_hash != stored_hash
                and (status != "COLLECTED" or analysis_version is not None or clean_uri is not None)
            )
            if protected_news_revision:
                # A raw collector is not an authoritative reanalysis.  It may
                # rediscover an older/different rendering of a URL after the
                # article has already been analyzed.  Keep the analyzed body
                # and links, but allow this immutable input batch to receive a
                # successful checkpoint so unrelated rows are not retried
                # forever with it.
                accept_content = False
                accept_links = False
                cursor.execute("""UPDATE source_document SET first_collected_at = %s,
                                  updated_at = CURRENT_TIMESTAMP
                                  WHERE document_id = %s AND first_collected_at > %s""",
                               (collected_at, document_id, collected_at))
                updated = cursor.rowcount > 0
            if not accept_content:
                incoming_hash = None
            if (accept_content and collected_at == last_collected_at and incoming_hash is not None
                    and stored_hash is not None and incoming_hash != stored_hash):
                raise LoadError("same collected_at has conflicting content_hash; batch requires a consistent source revision")
            analyzed_revision = (accept_content and kind == "NEWS" and incoming_hash is not None and stored_hash is not None
                                 and incoming_hash != stored_hash and record.get("status") == "ANALYZED")
            if analyzed_revision:
                cursor.execute("""SELECT max(analyzed_at) FROM company_document
                                  WHERE document_id = %s AND model_version IS NOT NULL""", (document_id,))
                latest_analysis = cursor.fetchone()[0]
                incoming_analysis = datetime.fromisoformat(record["analyzed_at"])
                if latest_analysis is not None and latest_analysis > incoming_analysis:
                    raise LoadError("an older analysis cannot replace the current analyzed NEWS revision")
            if (accept_content and incoming_hash is not None and incoming_hash != stored_hash
                    and (status != "COLLECTED" or analysis_version is not None or clean_uri is not None)
                    and not analyzed_revision):
                raise LoadError("changed content_hash on an analyzed document requires reanalysis before replacement")
            if (accept_content and kind == "NEWS" and incoming_hash is not None and stored_hash is not None
                    and incoming_hash != stored_hash and not analyzed_revision):
                # Existing company links describe the stored body. Replacing it
                # requires a revision workflow that also rebuilds those links.
                raise LoadError("changed news content_hash requires an explicit revision/reanalysis workflow before replacement")
            if accept_content:
                values = (record["title"], record.get("summary"), record.get("published_at"),
                          record["collected_at"], record.get("content_hash"), record["hdfs_raw_uri"], record["collected_at"])
                # Keep the original source and URL. A newer analyzed revision
                # replaces its derived links in the same transaction below.
                cursor.execute(COMMON_UPDATE, (*values, document_id, *values))
            if accept_content and analyzed_revision and clean_uri is not None:
                # A clean artifact belongs to the previous body. The incoming
                # analyzed record carries a complete authoritative link set for
                # the new body, so publish both changes in this transaction.
                cursor.execute("""UPDATE source_document SET hdfs_clean_uri = NULL,
                                  updated_at = CURRENT_TIMESTAMP WHERE document_id = %s""", (document_id,))
        else:
            if kind == "NEWS":
                # A mention from an old revision is evidence for the current
                # article only when both revisions have the same verified body.
                # An older analyzed copy is never authoritative over the newer
                # raw collection, including when the newer AI result was empty
                # and therefore left no company_document timestamp tombstone.
                incoming_hash = record.get("content_hash")
                accept_links = record.get("status") != "ANALYZED" and status != "ANALYZED" and (
                    incoming_hash is not None and stored_hash is not None and incoming_hash == stored_hash)
            cursor.execute("""UPDATE source_document SET first_collected_at = %s, updated_at = CURRENT_TIMESTAMP
                              WHERE document_id = %s AND first_collected_at > %s""",
                           (collected_at, document_id, collected_at))
        updated = cursor.rowcount > 0

    if kind == "NEWS" and accept_content:
        cursor.execute("""
            INSERT INTO news_article AS old (document_id, publisher, canonical_url, canonical_url_hash, author)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE SET
                publisher = COALESCE(EXCLUDED.publisher, old.publisher),
                author = COALESCE(EXCLUDED.author, old.author)
            WHERE (old.publisher, old.author) IS DISTINCT FROM
                (COALESCE(EXCLUDED.publisher, old.publisher), COALESCE(EXCLUDED.author, old.author))
            """, (document_id, record.get("publisher"), record["canonical_url"], natural_key, record.get("author")))
        updated = updated or cursor.rowcount > 0
    elif kind != "NEWS":
        filing_id = companies[_company_key(record["filing_company"])]
        if not inserted:
            cursor.execute("SELECT filing_company_id, sec_cik FROM disclosure WHERE document_id = %s", (document_id,))
            existing_company, existing_cik = cursor.fetchone()
            if existing_company != filing_id or (kind == "SEC" and existing_cik != record["sec_cik"]):
                raise CompanyMappingError("existing filing issuer conflicts with the supplied company or CIK")
        if accept_content:
            cursor.execute("""
            INSERT INTO disclosure AS old
                (document_id, filing_company_id, filing_system, dart_receipt_no, sec_accession_no, sec_cik,
                 report_code, report_name, filing_date, disclosure_type, correction_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE SET
                report_code = COALESCE(EXCLUDED.report_code, old.report_code),
                report_name = EXCLUDED.report_name, filing_date = EXCLUDED.filing_date,
                disclosure_type = COALESCE(EXCLUDED.disclosure_type, old.disclosure_type),
                correction_status = COALESCE(EXCLUDED.correction_status, old.correction_status)
            WHERE (old.report_code, old.report_name, old.filing_date, old.disclosure_type, old.correction_status)
              IS DISTINCT FROM
                (COALESCE(EXCLUDED.report_code, old.report_code), EXCLUDED.report_name, EXCLUDED.filing_date,
                 COALESCE(EXCLUDED.disclosure_type, old.disclosure_type),
                 COALESCE(EXCLUDED.correction_status, old.correction_status))
                """, (document_id, filing_id, kind, record.get("dart_receipt_no"), record.get("sec_accession_no"),
                      record.get("sec_cik"), record.get("report_code"), record["report_name"], record["filing_date"],
                      record.get("disclosure_type"), record.get("correction_status")))
            updated = updated or cursor.rowcount > 0

    refs = {_company_key(ref) for ref in record.get("company_refs", [])} if accept_links else set()
    issuer = _company_key(record["filing_company"]) if kind != "NEWS" else None
    if issuer:
        refs.add(issuer)
    links, link_updates, deleted = 0, 0, 0
    if kind == "NEWS" and record.get("status") == "ANALYZED" and accept_links:
        if latest_analysis is None:
            cursor.execute("""SELECT max(analyzed_at) FROM company_document
                              WHERE document_id = %s AND model_version IS NOT NULL""", (document_id,))
            latest_analysis = cursor.fetchone()[0]
        incoming_analysis = datetime.fromisoformat(record["analyzed_at"])
        if latest_analysis is not None and latest_analysis > incoming_analysis:
            return document_id, inserted, (updated and not inserted), 0, 0, 0
        cursor.execute("""
            UPDATE source_document SET status = 'ANALYZED', analysis_version = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE document_id = %s AND (status, analysis_version)
                IS DISTINCT FROM ('ANALYZED', %s)
            """, (record["analysis_version"], document_id, record["analysis_version"]))
        updated = updated or cursor.rowcount > 0
        incoming_ids = []
        for link in record["company_links"]:
            key = _company_key(link)
            company_id = companies[key]
            incoming_ids.append(company_id)
            # Derived scores belong to the mention analysis that produced them, so a
            # genuinely newer analysis invalidates them. Republishing the same analysis
            # under a new batch id must not erase AI sentiment the enrichment loader
            # already committed against that identical (model_version, analyzed_at).
            #
            # is_service_visible moves with those scores. A matched pair is not yet
            # a publishable pair: the news API filters on this flag, so inserting
            # TRUE here is what put headlines on screen with no sentiment behind
            # them. The enrichment loader sets it once a verdict exists, and a
            # newer analysis that invalidates the verdict takes it away again -
            # otherwise a row would stay visible while its sentiment went NULL.
            cursor.execute("""
                INSERT INTO company_document AS old
                    (document_id, company_id, mention_type, confidence, model_version,
                     is_service_visible, analyzed_at)
                VALUES (%s, %s, 'MENTION', %s, %s, FALSE, %s)
                ON CONFLICT (document_id, company_id) DO UPDATE SET
                    mention_type = 'MENTION',
                    confidence = CASE WHEN old.analyzed_at = EXCLUDED.analyzed_at
                        AND old.model_version = EXCLUDED.model_version
                        THEN old.confidence ELSE EXCLUDED.confidence END,
                    relevance_score = CASE WHEN old.analyzed_at = EXCLUDED.analyzed_at
                        AND old.model_version = EXCLUDED.model_version
                        THEN old.relevance_score END,
                    sentiment = CASE WHEN old.analyzed_at = EXCLUDED.analyzed_at
                        AND old.model_version = EXCLUDED.model_version
                        THEN old.sentiment END,
                    impact_score = CASE WHEN old.analyzed_at = EXCLUDED.analyzed_at
                        AND old.model_version = EXCLUDED.model_version
                        THEN old.impact_score END,
                    model_version = EXCLUDED.model_version,
                    is_service_visible = CASE WHEN old.analyzed_at = EXCLUDED.analyzed_at
                        AND old.model_version = EXCLUDED.model_version
                        THEN old.is_service_visible ELSE FALSE END,
                    analyzed_at = EXCLUDED.analyzed_at
                WHERE old.model_version IS NULL OR old.analyzed_at <= EXCLUDED.analyzed_at
                RETURNING (xmax = 0) AS inserted
                """, (document_id, company_id, link["confidence"],
                      record["model_version"], record["analyzed_at"]))
            changed = cursor.fetchone()
            if changed is not None:
                if changed[0]:
                    links += 1
                else:
                    link_updates += 1
        # The analyzed list is authoritative for this NEWS article body. The
        # document type check above means disclosure FILER links cannot reach here.
        cursor.execute("""
            DELETE FROM company_document
            WHERE document_id = %s
              AND NOT (company_id = ANY(%s::uuid[]))
            """, (document_id, incoming_ids))
        deleted = cursor.rowcount
    else:
        for key in sorted(refs):
            cursor.execute("""
                INSERT INTO company_document (document_id, company_id, mention_type)
                VALUES (%s, %s, %s) ON CONFLICT (document_id, company_id) DO NOTHING
                """, (document_id, companies[key], "FILER" if key == issuer else "MENTION"))
            links += cursor.rowcount
    return document_id, inserted, (updated and not inserted), links, link_updates, deleted


def load_documents(connection, records: Iterable[Mapping[str, Any]], *, batch_id: str,
                   manifest_sha256: str, source_uri: str, sources: Mapping[str, Any],
                   register_sources: bool = False, commit: bool = False) -> dict[str, Any]:
    """Load one immutable batch; exact replay is a no-op, failures roll back all.

    Company rows must already exist and be ACTIVE. Explicit source definitions
    may be inserted only when ``register_sources=True``. No connection commit or
    rollback is attempted when the caller already has a transaction in progress.
    """
    if not isinstance(commit, bool) or not isinstance(register_sources, bool):
        raise LoadError("commit and register_sources must be booleans")
    if connection.info.transaction_status != 0:
        raise LoadError("document loading requires an idle connection; caller transaction was left untouched")
    _text(batch_id, "batch_id")
    _text(source_uri, "source_uri")
    if not isinstance(manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise LoadError("manifest_sha256 must be 64 lowercase hexadecimal characters")
    normalized = validate_records(records)
    source_config = _sources(sources, {row["source_key"] for row in normalized})
    digest = hashlib.sha256(json.dumps({"records": normalized, "sources": source_config},
                                      ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode("utf-8")).hexdigest()
    unique_count = len({_identity(row) for row in normalized})
    result = {"batch_id": batch_id, "dry_run": not commit, "already_loaded": False,
              "input_records": len(normalized), "document_count": unique_count,
              "inserted_documents": 0, "updated_documents": 0, "company_links_added": 0,
              "company_links_updated": 0, "company_links_deleted": 0,
              "sources_created": 0, "committed_documents": 0, "normalization_sha256": digest}
    from psycopg.rows import tuple_row

    with connection.transaction(force_rollback=not commit):
        with connection.cursor(row_factory=tuple_row) as cursor:
            # Shared identity keys can cross source streams. Serialize Loader
            # writers in this schema rather than racing unique-key upserts.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':cosmos.document_loader', 0))")
            cursor.execute("""SELECT manifest_sha256, normalization_sha256, source_uri, record_count, document_count
                              FROM document_load_batch WHERE batch_id = %s""", (batch_id,))
            receipt = cursor.fetchone()
            expected = (manifest_sha256, digest, source_uri, len(normalized), unique_count)
            if receipt is not None:
                if receipt != expected:
                    raise BatchConflictError(f"batch_id already committed with different input: {batch_id}")
                result["already_loaded"] = True
                return result
            companies = _company_mapping(cursor, normalized)
            source_ids, result["sources_created"] = _source_mapping(cursor, source_config, register_sources)
            inserted_ids, updated_ids = set(), set()
            for record in normalized:
                document_id, inserted, updated, links, link_updates, deleted = _write_document(
                    cursor, record, source_ids[record["source_key"]], companies)
                if inserted:
                    inserted_ids.add(document_id)
                if updated:
                    updated_ids.add(document_id)
                result["company_links_added"] += links
                result["company_links_updated"] += link_updates
                result["company_links_deleted"] += deleted
            result["inserted_documents"] = len(inserted_ids)
            result["updated_documents"] = len(updated_ids - inserted_ids)
            cursor.execute("""
                INSERT INTO document_load_batch
                  (batch_id, manifest_sha256, normalization_sha256, source_uri, record_count, document_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                """, (batch_id, *expected))
            result["committed_documents"] = unique_count if commit else 0
    return result
