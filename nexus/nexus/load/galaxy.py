"""Publish the BigKinds galaxy snapshot into the tables the existing galaxy screen reads (spec §5.2).

Everything happens in one transaction and is replaced wholesale: the snapshot, every
relationship_score_current row and this command's evidence rows. Run it after `load`.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from uuid import UUID

from ..galaxy.build import KST, UNDIRECTED, GalaxyDoc, GalaxyEdge
from .postgres import PIPELINE_VERSION, _relationship_type_ids, _source_id, document_id

FORMULA_VERSION = "nexus-bigkinds-v1"
GRAPH_MODEL_VERSION = "nexus-graph-v1"


def _relationship_id(cur, src: UUID, dst: UUID, type_id: int, rel: str, source: str = "NEWS") -> UUID:
    """Reuse the row in either orientation for undirected types; otherwise insert it."""
    row = cur.execute("SELECT relationship_id FROM company_relationship WHERE source_company_id = %s "
                      "AND target_company_id = %s AND relationship_type_id = %s", (src, dst, type_id)).fetchone()
    if not row and rel in UNDIRECTED:
        row = cur.execute("SELECT relationship_id FROM company_relationship WHERE source_company_id = %s "
                          "AND target_company_id = %s AND relationship_type_id = %s", (dst, src, type_id)).fetchone()
    if row:
        return row[0]
    source = "COMENTION" if rel == "CO_MENTIONED" else source
    return cur.execute("INSERT INTO company_relationship (source_company_id, target_company_id, relationship_type_id, "
                       "relation_source) VALUES (%s, %s, %s, %s) RETURNING relationship_id",
                       (src, dst, type_id, source)).fetchone()[0]


def _snapshot(cur, as_of: datetime) -> UUID:
    hdfs_uri = f"bigkinds://nexus/{as_of.astimezone(KST).date().isoformat()}"
    row = cur.execute("SELECT snapshot_id FROM graph_snapshot WHERE as_of_at = %s AND formula_version = %s",
                      (as_of, FORMULA_VERSION)).fetchone()
    if row:
        snapshot_id = row[0]
        cur.execute("UPDATE graph_snapshot SET status = 'PUBLISHED', model_version = %s, hdfs_uri = %s, "
                    "published_at = now() WHERE snapshot_id = %s", (PIPELINE_VERSION, hdfs_uri, snapshot_id))
    else:
        snapshot_id = cur.execute(
            "INSERT INTO graph_snapshot (as_of_at, formula_version, model_version, status, hdfs_uri, published_at) "
            "VALUES (%s, %s, %s, 'PUBLISHED', %s, now()) RETURNING snapshot_id",
            (as_of, FORMULA_VERSION, PIPELINE_VERSION, hdfs_uri)).fetchone()[0]
    # Two PUBLISHED rows with the same as_of_at would let the galaxy pick the empty one.
    cur.execute("UPDATE graph_snapshot SET status = 'SUPERSEDED' WHERE status = 'PUBLISHED' AND snapshot_id <> %s",
                (snapshot_id,))
    return snapshot_id


def _ensure_document(cur, source_id: UUID, doc: GalaxyDoc) -> UUID:
    """Same document id as `load` uses, so a later event load updates this row instead of duplicating it."""
    doc_id = document_id(doc.news_id)
    canonical = "bigkinds://news/" + doc.news_id
    cur.execute("INSERT INTO source_document (document_id, source_id, document_type, title, original_url, published_at, "
                "status, analysis_version) VALUES (%s, %s, 'NEWS', %s, %s, %s, 'COLLECTED', %s) "
                "ON CONFLICT (document_id) DO NOTHING",
                (doc_id, source_id, doc.title or doc.news_id, doc.link or canonical, doc.published, GRAPH_MODEL_VERSION))
    cur.execute("INSERT INTO news_article (document_id, publisher, canonical_url, canonical_url_hash) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (doc_id, doc.provider or None, canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()))
    return doc_id


def _evidence_id(cur, doc_id: UUID, company_id: UUID, sentence: str) -> UUID:
    # The company-document row may already belong to `load`; then the sentence hangs off that row, and `load`
    # clears this evidence before it deletes the row (see load_events / prune_events).
    cur.execute("INSERT INTO company_document (document_id, company_id, mention_type, model_version, "
                "is_service_visible) VALUES (%s, %s, 'EVIDENCE', %s, FALSE) ON CONFLICT DO NOTHING",
                (doc_id, company_id, GRAPH_MODEL_VERSION))
    return cur.execute("INSERT INTO document_evidence (document_id, company_id, sentence_text, sentence_order, "
                       "model_version) VALUES (%s, %s, %s, 0, %s) RETURNING evidence_id",
                       (doc_id, company_id, sentence, GRAPH_MODEL_VERSION)).fetchone()[0]


def publish_galaxy(conn, as_of: datetime, edges: list[GalaxyEdge], company_ids: dict[str, UUID],
                   roles: dict[str, tuple[str, str]]) -> dict[str, int]:
    edges = [e for e in edges if e.src in company_ids and e.dst in company_ids]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("INSERT INTO relationship_type (code, name, directionality) "
                    "VALUES ('CO_MENTIONED', '함께 언급', 'UNDIRECTED') ON CONFLICT (code) DO NOTHING")
        type_ids = _relationship_type_ids(conn)

        for key, (label, role) in roles.items():
            if key not in company_ids:
                continue
            industry_id = cur.execute("INSERT INTO industry (name) VALUES (%s) ON CONFLICT (name) "
                                      "DO UPDATE SET name = EXCLUDED.name RETURNING industry_id",
                                      (f"{label} {role}",)).fetchone()[0]
            cur.execute("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (%s, %s, "
                        "NOT EXISTS (SELECT 1 FROM company_industry WHERE company_id = %s AND is_primary)) "
                        "ON CONFLICT DO NOTHING", (company_ids[key], industry_id, company_ids[key]))

        for table in ("relationship_evidence", "document_evidence", "company_document"):
            cur.execute(f"DELETE FROM {table} WHERE model_version = %s", (GRAPH_MODEL_VERSION,))

        rel_ids: dict[tuple[str, str, str], UUID] = {}
        for e in edges:
            if (e.src, e.dst, e.rel) not in rel_ids:
                rel_ids[(e.src, e.dst, e.rel)] = _relationship_id(cur, company_ids[e.src], company_ids[e.dst],
                                                                  type_ids[e.rel], e.rel, e.source)
        comention_ids = [rid for (_, _, rel), rid in rel_ids.items() if rel == "CO_MENTIONED"]
        cur.execute("DELETE FROM company_relationship WHERE relation_source = 'COMENTION' "
                    "AND NOT (relationship_id = ANY(%s::uuid[]))", (comention_ids,))

        snapshot_id = _snapshot(cur, as_of)
        cur.execute("DELETE FROM relationship_score_current")
        cur.executemany(
            "INSERT INTO relationship_score_current (relationship_id, window_type, snapshot_id, score, news_score, "
            "disclosure_score, impact_direction, confidence, evidence_count, formula_version, as_of_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s)",
            # 공시(DART) 지분 관계는 뉴스 점수가 아니라 공시 점수(지분율)로 둔다
            [(rel_ids[(e.src, e.dst, e.rel)], e.window, snapshot_id, e.score,
              None if e.source == "DART" else e.score, e.score if e.source == "DART" else None,
              None if e.confidence is None else round(e.confidence, 6), len(e.docs), FORMULA_VERSION, as_of)
             for e in edges])

        # One evidence row per (relationship, article) even when several windows point at it.
        docs: dict[UUID, dict[str, tuple[GalaxyDoc, str]]] = defaultdict(dict)
        for e in edges:
            for d in e.docs:
                docs[rel_ids[(e.src, e.dst, e.rel)]].setdefault(d.news_id, (d, e.src))
        source_id = _source_id(conn)
        evidence_rows = 0
        for rid, by_news in docs.items():
            for d, src_key in by_news.values():
                doc_id = _ensure_document(cur, source_id, d)
                evidence_id = _evidence_id(cur, doc_id, company_ids[src_key], d.sentence) if d.sentence else None
                cur.execute("INSERT INTO relationship_evidence (relationship_id, document_id, evidence_id, "
                            "model_version, formula_version) VALUES (%s, %s, %s, %s, %s)",
                            (rid, doc_id, evidence_id, GRAPH_MODEL_VERSION, FORMULA_VERSION))
                evidence_rows += 1
    counts = {f"edges_{w}": sum(1 for e in edges if e.window == w) for w in ("30D", "1Y", "10Y")}
    return {"snapshot_rows": len(edges), **counts, "evidence_rows": evidence_rows}
