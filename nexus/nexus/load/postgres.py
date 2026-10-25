"""Write NEXUS results into the service database (public schema, V10 tables).

BigKinds articles also go into source_document / news_article / company_document so the existing
COSMOS news page and company panel show them without backend changes.
"""
from __future__ import annotations

import hashlib
import math
import re
import uuid
from datetime import datetime, time, timezone
from pathlib import Path
from uuid import UUID

import pandas as pd
from psycopg.types.json import Jsonb

from ..config import REPO_ROOT
from ..models import Company, Edge, EventBundle, EvidenceSnippet

MIGRATIONS_DIR = REPO_ROOT / "BackEnd" / "src" / "main" / "resources" / "db" / "migration"
NAMESPACE = uuid.NAMESPACE_URL
PIPELINE_VERSION = "nexus-slice-v1"
MODEL_VERSION = "rule-propagation-v1"
IMPACT_MODEL_VERSION = f"{MODEL_VERSION}|impact-hgb-v1"
RELATIONSHIP_TYPES = [("SUPPLY", "공급", "DIRECTED"), ("INVEST", "투자", "DIRECTED"),
                      ("PARTNER", "협력", "UNDIRECTED"), ("COMPETE", "경쟁", "UNDIRECTED")]


def document_id(news_id: str) -> UUID:
    return uuid.uuid5(NAMESPACE, "bigkinds:document:" + news_id)


# COSMOS 규칙(AI/graph/fill_company_document_scores.py): 영향도 = 기사 논조의 부호
TONE_SIGN = {"POSITIVE": 1, "NEGATIVE": -1, "NEUTRAL": 0}


def write_sentiment_labels(conn, labels: dict[str, str]) -> int:
    """news_id → label 을 이미 적재된 기사의 company_document 에만 쓴다(영향도는 그 부호). `sentiment` 뒤에 `load` 를
    다시 돌리지 않고 뉴스 화면의 긍정·중립·부정만 고칠 때 쓴다(라벨 규칙은 `load` 와 같은 impact.sentiment.sentiment_labels)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pg_temp.sentiment_in")
        cur.execute("CREATE TEMP TABLE sentiment_in (document_id UUID PRIMARY KEY, sentiment VARCHAR(30)) ON COMMIT DROP")
        with cur.copy("COPY sentiment_in (document_id, sentiment) FROM STDIN") as copy:
            for news_id, label in labels.items():
                copy.write_row((document_id(news_id), label))
        cur.execute("UPDATE company_document cd SET sentiment = s.sentiment, "
                    "impact_score = CASE s.sentiment WHEN 'POSITIVE' THEN 1 WHEN 'NEGATIVE' THEN -1 ELSE 0 END "
                    "FROM sentiment_in s WHERE cd.document_id = s.document_id AND cd.model_version LIKE %s", ("nexus%",))
        return cur.rowcount


NEWS_EVIDENCE_VERSION = "nexus-news-v1"


def write_news_evidence(conn, rows: list, confidence: dict[tuple[str, str], float], company_ids: dict[str, UUID]) -> int:
    """뉴스 화면의 대표 근거 문장(document_evidence)과 분석 신뢰도(company_document.confidence)를 바꿔 쓴다.
    rows: entities.sentences.NewsEvidence, confidence: (news_id, company key) → 값. 이 버전의 문장은 모두 지우고 다시 넣고,
    이미 적재된 기사-기업 행에만 단다(적재 안 된 기사·기사 기업이 아닌 회사의 문장은 버린다). `load` 가 기사-기업 행을
    지우면 문장도 함께 지워지므로(FK ON DELETE CASCADE) `load` 뒤에 다시 부른다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM document_evidence WHERE model_version = %s", (NEWS_EVIDENCE_VERSION,))
        cur.execute("DROP TABLE IF EXISTS pg_temp.evidence_in")
        cur.execute("CREATE TEMP TABLE evidence_in (document_id UUID, company_id UUID, sentence_text TEXT, "
                    "sentence_order INTEGER, confidence NUMERIC(7, 6)) ON COMMIT DROP")
        with cur.copy("COPY evidence_in FROM STDIN") as copy:
            for r in rows:
                if r.company_key in company_ids:
                    copy.write_row((document_id(r.news_id), company_ids[r.company_key], r.sentence, r.order,
                                    round(r.confidence, 6)))
        cur.execute("INSERT INTO document_evidence (document_id, company_id, sentence_text, sentence_order, confidence, "
                    "model_version) SELECT e.document_id, e.company_id, e.sentence_text, e.sentence_order, e.confidence, %s "
                    "FROM evidence_in e JOIN company_document cd ON cd.document_id = e.document_id "
                    "AND cd.company_id = e.company_id AND cd.model_version LIKE %s", (NEWS_EVIDENCE_VERSION, "nexus%"))
        written = cur.rowcount
        cur.execute("DROP TABLE IF EXISTS pg_temp.confidence_in")
        cur.execute("CREATE TEMP TABLE confidence_in (document_id UUID, company_id UUID, confidence NUMERIC(7, 6)) "
                    "ON COMMIT DROP")
        with cur.copy("COPY confidence_in FROM STDIN") as copy:
            for (news_id, key), value in confidence.items():
                if key in company_ids:
                    copy.write_row((document_id(news_id), company_ids[key], round(value, 6)))
        cur.execute("UPDATE company_document SET confidence = NULL WHERE model_version LIKE %s AND confidence IS NOT NULL",
                    ("nexus%",))
        cur.execute("UPDATE company_document cd SET confidence = c.confidence FROM confidence_in c "
                    "WHERE cd.document_id = c.document_id AND cd.company_id = c.company_id AND cd.model_version LIKE %s",
                    ("nexus%",))
        return written


def event_id(news_id: str) -> UUID:
    return uuid.uuid5(NAMESPACE, "bigkinds:event:" + news_id)


def snippet_id(s: EvidenceSnippet) -> UUID:
    return uuid.uuid5(NAMESPACE, f"bigkinds:snippet:{s.news_id}:{s.src}:{s.dst}:{s.rel}")


def apply_migrations_for_tests(conn, migration_dir: Path) -> None:
    """Throwaway test databases only. Real databases are migrated by Flyway (keeps flyway_schema_history)."""
    files = sorted(Path(migration_dir).glob("V*__*.sql"), key=lambda p: int(re.match(r"V(\d+)__", p.name).group(1)))
    for path in files:
        conn.execute(path.read_text(encoding="utf-8"))


def ensure_relationship_types(conn) -> None:
    for code, name, directionality in RELATIONSHIP_TYPES:
        conn.execute("INSERT INTO relationship_type (code, name, directionality) VALUES (%s, %s, %s) "
                     "ON CONFLICT (code) DO NOTHING", (code, name, directionality))


def _relationship_type_ids(conn) -> dict[str, int]:
    return dict(conn.execute("SELECT code, relationship_type_id FROM relationship_type").fetchall())


def ensure_companies(conn, companies: list[Company]) -> dict[str, UUID]:
    ids = {}
    for c in companies:
        row = conn.execute(
            "INSERT INTO company (name, stock_code, market, status) VALUES (%s, %s, %s, 'ACTIVE') "
            "ON CONFLICT (market, stock_code) DO UPDATE SET market = EXCLUDED.market RETURNING company_id",
            (c.name, c.ticker, c.market)).fetchone()
        ids[c.key] = row[0]
    conn.commit()
    return ids


def _source_id(conn) -> UUID:
    return conn.execute(
        "INSERT INTO data_source (name, source_type, base_url) VALUES ('bigkinds', 'NEWS_API', 'https://tools.kinds.or.kr') "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING source_id").fetchone()[0]


SQL_DOCUMENT = (
    "INSERT INTO source_document (document_id, source_id, document_type, title, summary, original_url, "
    "published_at, status, analysis_version) VALUES (%s, %s, 'NEWS', %s, %s, %s, %s, 'ANALYZED', %s) "
    "ON CONFLICT (document_id) DO UPDATE SET title = EXCLUDED.title, summary = EXCLUDED.summary, "
    "original_url = EXCLUDED.original_url, published_at = EXCLUDED.published_at, "
    "analysis_version = EXCLUDED.analysis_version, updated_at = CURRENT_TIMESTAMP")
SQL_ARTICLE = (
    "INSERT INTO news_article (document_id, publisher, canonical_url, canonical_url_hash, author) "
    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (document_id) DO UPDATE SET publisher = EXCLUDED.publisher, "
    "author = EXCLUDED.author")
SQL_COMPANY_DOCUMENT = (
    "INSERT INTO company_document (document_id, company_id, mention_type, relevance_score, sentiment, impact_score, "
    "model_version, is_service_visible) VALUES (%s, %s, 'REPORTED', %s, %s, %s, %s, TRUE) "
    "ON CONFLICT (document_id, company_id) DO UPDATE SET mention_type = EXCLUDED.mention_type, "
    "relevance_score = EXCLUDED.relevance_score, sentiment = EXCLUDED.sentiment, impact_score = EXCLUDED.impact_score, "
    "model_version = EXCLUDED.model_version, "
    "is_service_visible = TRUE, analyzed_at = CURRENT_TIMESTAMP")
SQL_EVENT = (
    "INSERT INTO news_event (event_id, document_id, bigkinds_news_id, value_chain, categories, incidents, "
    "dateline, event_trading_date, pipeline_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (event_id) DO UPDATE SET value_chain = EXCLUDED.value_chain, "
    "categories = EXCLUDED.categories, incidents = EXCLUDED.incidents, dateline = EXCLUDED.dateline, "
    "event_trading_date = EXCLUDED.event_trading_date, pipeline_version = EXCLUDED.pipeline_version")
SQL_IMPACT = (
    "INSERT INTO event_company_impact (event_id, company_id, basis, hop, path, propagation_score, "
    "status, hold_reason, model_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")
SQL_RETURN = (
    "INSERT INTO event_abnormal_return (event_id, company_id, window_days, benchmark, stock_return, "
    "benchmark_return, abnormal_return) VALUES (%s, %s, %s, %s, %s, %s, %s)")


def _clear_sentence_evidence(executor, document_ids: list[UUID]) -> None:
    """Galaxy evidence (publish-graph) may point at sentences under these company-document rows; the FK from
    relationship_evidence has no ON DELETE, so detach the sentence first. The article link stays, so the galaxy's
    evidence_count still matches what its evidence API serves; the next publish-graph restores the sentence."""
    executor.execute("UPDATE relationship_evidence SET evidence_id = NULL WHERE evidence_id IN "
                     "(SELECT evidence_id FROM document_evidence WHERE document_id = ANY(%s))", (document_ids,))


def load_events(conn, bundles: list[EventBundle], company_ids: dict[str, UUID],
                sentiment: dict[str, str] | None = None) -> int:
    """Upsert documents and events; replace each event's impacts and returns (re-runs stay idempotent).
    `sentiment` (news_id → POSITIVE/NEUTRAL/NEGATIVE) fills the news feed's tone; unscored articles stay NULL."""
    sentiment = sentiment or {}
    documents, articles, company_docs, events, impacts, returns = [], [], [], [], [], []
    with conn.transaction():
        source = _source_id(conn)
        for b in bundles:
            a = b.article
            doc, evt = document_id(a.news_id), event_id(a.news_id)
            canonical = "bigkinds://news/" + a.news_id
            documents.append((doc, source, a.title, a.content, a.link, a.dateline, PIPELINE_VERSION))
            articles.append((doc, a.provider, canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                             a.byline or None))
            tone = sentiment.get(a.news_id)
            company_docs += [(doc, company_ids[m.key], round(m.salience, 6), tone, TONE_SIGN.get(tone),
                              PIPELINE_VERSION)
                             for m in b.mentions if m.key in company_ids]
            events.append((evt, doc, a.news_id, b.value_chain, list(a.categories), list(a.incidents), a.dateline,
                           b.trading_date, PIPELINE_VERSION))
            impacts += [(evt, company_ids[i.key], i.basis, i.hop,
                         Jsonb([{"src": s.src, "dst": s.dst, "rel": s.rel, "source": s.source,
                                 "strength": round(s.strength, 6)} for s in i.path]),
                         round(i.score, 6), i.status, i.hold_reason, MODEL_VERSION)
                        for i in b.impacts if i.key in company_ids]
            returns += [(evt, company_ids[r.key], r.window_days, r.benchmark, round(r.stock_return, 8),
                         round(r.benchmark_return, 8), round(r.abnormal_return, 8))
                        for r in b.returns if r.key in company_ids]
        with conn.cursor() as cur:
            cur.executemany(SQL_DOCUMENT, documents)
            cur.executemany(SQL_ARTICLE, articles)
            # Mentions removed by a re-analysis must disappear from the existing news feed too.
            _clear_sentence_evidence(cur, [d[0] for d in documents])
            cur.execute("DELETE FROM company_document WHERE document_id = ANY(%s) AND model_version = %s",
                        ([d[0] for d in documents], PIPELINE_VERSION))
            cur.executemany(SQL_COMPANY_DOCUMENT, company_docs)
            cur.executemany(SQL_EVENT, events)
            event_ids = [e[0] for e in events]
            cur.execute("DELETE FROM event_company_impact WHERE event_id = ANY(%s)", (event_ids,))
            cur.execute("DELETE FROM event_abnormal_return WHERE event_id = ANY(%s)", (event_ids,))
            cur.executemany(SQL_IMPACT, impacts)
            cur.executemany(SQL_RETURN, returns)
    return len(bundles)


def prune_events(conn, value_chain: str, keep_news_ids: set[str]) -> int:
    """Remove this value chain's events that a new analysis no longer produces (impacts/returns cascade)."""
    with conn.transaction():
        rows = conn.execute("SELECT event_id, document_id, bigkinds_news_id FROM news_event WHERE value_chain = %s",
                            (value_chain,)).fetchall()
        drop = [(evt, doc) for evt, doc, news_id in rows if news_id not in keep_news_ids]
        if drop:
            _clear_sentence_evidence(conn, [doc for _, doc in drop])
            conn.execute("DELETE FROM company_document WHERE document_id = ANY(%s) AND model_version = %s",
                         ([doc for _, doc in drop], PIPELINE_VERSION))
            conn.execute("DELETE FROM news_event WHERE event_id = ANY(%s)", ([evt for evt, _ in drop],))
    return len(drop)


def load_relations(conn, edges: list[Edge], snippets: list[EvidenceSnippet], company_ids: dict[str, UUID]) -> int:
    """NEWS edges become company_relationship rows (existing rows are left as they are) plus their snippets."""
    written = 0
    with conn.transaction():
        types = _relationship_type_ids(conn)
        scope = list(company_ids.values())
        keep = [(company_ids[e.src], company_ids[e.dst], types[e.rel]) for e in edges
                if e.source == "NEWS" and e.rel in types and e.src in company_ids and e.dst in company_ids]
        # Replace, not accumulate: evidence that a re-analysis no longer finds must not stay visible.
        conn.execute("DELETE FROM relation_evidence_snippet WHERE source_company_id = ANY(%s) "
                     "AND target_company_id = ANY(%s)", (scope, scope))
        conn.execute(
            "DELETE FROM company_relationship WHERE relation_source = 'NEWS' AND source_company_id = ANY(%s) "
            "AND target_company_id = ANY(%s) AND (source_company_id, target_company_id, relationship_type_id) "
            "NOT IN (SELECT * FROM unnest(%s::uuid[], %s::uuid[], %s::smallint[]))",
            (scope, scope, [k[0] for k in keep], [k[1] for k in keep], [k[2] for k in keep]))
        for e in edges:
            # COSMOS edges are loaded too, so every step of an inferred path resolves to a relationship row.
            if e.source not in ("NEWS", "COSMOS") or e.rel not in types or e.src not in company_ids                     or e.dst not in company_ids:
                continue
            cur = conn.execute(
                "INSERT INTO company_relationship (source_company_id, target_company_id, relationship_type_id, "
                "relation_source) VALUES (%s, %s, %s, %s) ON CONFLICT ON CONSTRAINT uk_company_relationship_identity "
                "DO NOTHING", (company_ids[e.src], company_ids[e.dst], types[e.rel], e.source))
            written += cur.rowcount
        for s in snippets:
            if s.rel not in types or s.src not in company_ids or s.dst not in company_ids:
                continue
            conn.execute(
                "INSERT INTO relation_evidence_snippet (snippet_id, source_company_id, target_company_id, "
                "relationship_type_id, bigkinds_news_id, title, provider, published_at, original_url, snippet, "
                "pattern, confidence) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (snippet_id) DO UPDATE SET snippet = EXCLUDED.snippet, pattern = EXCLUDED.pattern, "
                "confidence = EXCLUDED.confidence",
                (snippet_id(s), company_ids[s.src], company_ids[s.dst], types[s.rel], s.news_id, s.title,
                 s.provider, s.published, s.link, s.snippet[:300], s.pattern, round(s.confidence, 6)))
            written += 1
    return written


def _valid_bar(o, h, l, c, v) -> bool:
    values = (o, h, l, c, v)
    return all(x is not None and not (isinstance(x, float) and math.isnan(x)) and x >= 0 for x in values) and h >= l


def load_prices(conn, company_ids: dict[str, UUID], bars_by_key: dict[str, pd.DataFrame]) -> int:
    written = 0
    with conn.transaction():
        for key, frame in bars_by_key.items():
            if key not in company_ids or frame is None or frame.empty:
                continue
            for ts, row in frame.iterrows():
                o, h, l, c, v = (float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]),
                                 float(row["Volume"]))
                if not _valid_bar(o, h, l, c, v):
                    continue
                # COSMOS convention (Crawling/prices loader): exchange trading date at UTC midnight.
                trading_at = datetime.combine(pd.Timestamp(ts).date(), time(0, 0), tzinfo=timezone.utc)
                cur = conn.execute(
                    "INSERT INTO stock_price_history (company_id, trading_at, interval_type, open_price, high_price, "
                    "low_price, close_price, trading_volume) VALUES (%s, %s, '1D', %s, %s, %s, %s, %s) "
                    "ON CONFLICT (company_id, trading_at, interval_type) DO UPDATE SET open_price = EXCLUDED.open_price, "
                    "high_price = EXCLUDED.high_price, low_price = EXCLUDED.low_price, close_price = EXCLUDED.close_price, "
                    "trading_volume = EXCLUDED.trading_volume", (company_ids[key], trading_at, o, h, l, c, int(v)))
                written += cur.rowcount
    return written


SQL_RESET_PREDICTIONS = (
    "UPDATE event_company_impact i SET impact_direction = NULL, impact_magnitude = NULL, confidence = NULL, "
    "model_version = %s FROM news_event e WHERE e.event_id = i.event_id AND e.value_chain = %s")
SQL_APPLY_PREDICTIONS = (
    "UPDATE event_company_impact i SET impact_direction = p.impact_direction, impact_magnitude = p.impact_magnitude, "
    "confidence = p.confidence, model_version = %s FROM impact_prediction p, news_event e "
    "WHERE i.event_id = p.event_id AND i.company_id = p.company_id AND e.event_id = i.event_id "
    "AND e.value_chain = %s AND i.status = 'OK'")


def write_predictions(conn, value_chain: str, rows: list[tuple[str, UUID, str | None, float, float]]) -> int:
    """Replace the impact model's output for one value chain: rows are (news_id, company_id, direction or None,
    confidence, magnitude). Everything is reset first, so a rerun gives the same table; HOLD rows stay empty."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(SQL_RESET_PREDICTIONS, (MODEL_VERSION, value_chain))
        # Called inside an outer transaction the ON COMMIT DROP table may still exist from a previous call.
        cur.execute("DROP TABLE IF EXISTS pg_temp.impact_prediction")
        cur.execute("CREATE TEMP TABLE impact_prediction (event_id UUID, company_id UUID, impact_direction VARCHAR(10), "
                    "confidence NUMERIC(7, 6), impact_magnitude NUMERIC(7, 6)) ON COMMIT DROP")
        with cur.copy("COPY impact_prediction (event_id, company_id, impact_direction, confidence, impact_magnitude) "
                      "FROM STDIN") as copy:
            for news_id, company_id, direction, confidence, magnitude in rows:
                copy.write_row((event_id(news_id), company_id, direction, confidence, magnitude))
        cur.execute(SQL_APPLY_PREDICTIONS, (IMPACT_MODEL_VERSION, value_chain))
        return cur.rowcount
