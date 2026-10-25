from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import psycopg
import pytest

from nexus.entities.sentences import NewsEvidence
from nexus.ingest.normalize import to_article
from nexus.load.postgres import (IMPACT_MODEL_VERSION, document_id, ensure_companies, event_id, load_events,
                                 load_prices, load_relations, write_news_evidence, write_predictions,
                                 write_sentiment_labels)
from nexus.models import (AbnormalReturn, Company, CompanyMention, Edge, EventBundle, EvidenceSnippet, Impact,
                          PathStep)

KST = timezone(timedelta(hours=9))
A = Company("KOSPI:900001", "KOSPI", "900001", "가상전자", "SEMICONDUCTOR", "제조")
B = Company("KOSDAQ:900002", "KOSDAQ", "900002", "하늘반도체", "SEMICONDUCTOR", "장비")
C = Company("KOSDAQ:900003", "KOSDAQ", "900003", "바다소재", "SEMICONDUCTOR", "소재")


def bundle(synthetic_docs):
    article = to_article(synthetic_docs[0])
    mentions = [CompanyMention(A.key, 1.0, ("title",)), CompanyMention(B.key, 0.8, ("content",))]
    impacts = [
        Impact(A.key, "REPORTED", 0, (), 1.0, "OK", None),
        Impact(B.key, "REPORTED", 0, (), 0.8, "OK", None),
        Impact(C.key, "INFERRED", 1, (PathStep(C.key, B.key, "SUPPLY", "COSMOS", 1.0),), 0.64, "OK", None),
    ]
    returns = [AbnormalReturn(A.key, 1, "KOSPI", 0.02, 0.01, 0.01),
               AbnormalReturn(A.key, 3, "KOSPI", 0.05, 0.02, 0.03)]
    return EventBundle(article, "SEMICONDUCTOR", mentions, impacts, returns, date(2026, 10, 13))


def count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_v10_applies_after_v9(pg):
    assert count(pg, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'news_event'") == 1


def test_load_events_writes_chain(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    assert load_events(pg, [bundle(synthetic_docs)], ids) == 1
    doc = document_id("SYN.20261013100000001")
    assert count(pg, "SELECT count(*) FROM source_document WHERE document_id = %s", doc) == 1
    assert count(pg, "SELECT count(*) FROM news_article WHERE document_id = %s", doc) == 1
    assert count(pg, "SELECT count(*) FROM company_document WHERE document_id = %s", doc) == 2
    assert count(pg, "SELECT count(*) FROM news_event") == 1
    assert count(pg, "SELECT count(*) FROM event_company_impact") == 3
    assert count(pg, "SELECT count(*) FROM event_abnormal_return") == 2


def test_load_events_writes_article_sentiment(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    doc = document_id("SYN.20261013100000001")
    load_events(pg, [bundle(synthetic_docs)], ids, sentiment={"SYN.20261013100000001": "NEGATIVE"})
    rows = pg.execute("SELECT sentiment, impact_score FROM company_document WHERE document_id = %s", (doc,)).fetchall()
    assert rows == [("NEGATIVE", Decimal("-1")), ("NEGATIVE", Decimal("-1"))]   # COSMOS rule: impact = tone sign
    load_events(pg, [bundle(synthetic_docs)], ids)                      # not scored yet: shown as before (NULL)
    rows = pg.execute("SELECT sentiment, impact_score FROM company_document WHERE document_id = %s", (doc,)).fetchall()
    assert rows == [(None, None), (None, None)]


def test_write_sentiment_labels_updates_loaded_documents_only(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    load_events(pg, [bundle(synthetic_docs)], ids)
    doc = document_id("SYN.20261013100000001")
    assert write_sentiment_labels(pg, {"SYN.20261013100000001": "POSITIVE", "SYN.NOT.LOADED": "NEGATIVE"}) == 2
    rows = pg.execute("SELECT sentiment, impact_score FROM company_document WHERE document_id = %s", (doc,)).fetchall()
    assert rows == [("POSITIVE", Decimal("1")), ("POSITIVE", Decimal("1"))]
    assert count(pg, "SELECT count(*) FROM company_document WHERE document_id = %s", document_id("SYN.NOT.LOADED")) == 0


def test_write_news_evidence_replaces_sentences_and_confidence(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    load_events(pg, [bundle(synthetic_docs)], ids)
    news_id = "SYN.20261013100000001"
    doc = document_id(news_id)
    rows = [NewsEvidence(news_id, A.key, 0, "가상전자 제목 문장", 0.7), NewsEvidence(news_id, B.key, 1, "하늘반도체 문장", 0.55),
            NewsEvidence(news_id, C.key, 2, "바다소재는 기사 기업이 아니라 버린다", 0.55),
            NewsEvidence("SYN.NOT.LOADED", A.key, 0, "적재 안 된 기사", 0.55)]
    confidence = {(news_id, A.key): 0.7, (news_id, B.key): 0.55}
    assert write_news_evidence(pg, rows, confidence, ids) == 2
    got = pg.execute("SELECT sentence_text, sentence_order, confidence FROM document_evidence WHERE document_id = %s "
                     "ORDER BY sentence_order", (doc,)).fetchall()
    assert got == [("가상전자 제목 문장", 0, Decimal("0.700000")), ("하늘반도체 문장", 1, Decimal("0.550000"))]
    conf = dict(pg.execute("SELECT company_id, confidence FROM company_document WHERE document_id = %s", (doc,)).fetchall())
    assert conf == {ids[A.key]: Decimal("0.700000"), ids[B.key]: Decimal("0.550000")}
    # a rerun replaces, it does not pile up; a company that lost its sentences loses its confidence too
    assert write_news_evidence(pg, rows[:1], {(news_id, A.key): 0.55}, ids) == 1
    assert count(pg, "SELECT count(*) FROM document_evidence WHERE document_id = %s", doc) == 1
    assert pg.execute("SELECT confidence FROM company_document WHERE document_id = %s AND company_id = %s",
                      (doc, ids[B.key])).fetchone()[0] is None


def test_reload_is_idempotent(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    load_events(pg, [bundle(synthetic_docs)], ids)
    assert ensure_companies(pg, [A, B, C]) == ids
    load_events(pg, [bundle(synthetic_docs)], ids)
    assert count(pg, "SELECT count(*) FROM source_document") == 1
    assert count(pg, "SELECT count(*) FROM company_document") == 2
    assert count(pg, "SELECT count(*) FROM event_company_impact") == 3
    assert count(pg, "SELECT count(*) FROM event_abnormal_return") == 2


def test_inferred_row_has_path_and_no_document(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    load_events(pg, [bundle(synthetic_docs)], ids)
    basis, hop, path = pg.execute("SELECT basis, hop, path FROM event_company_impact WHERE company_id = %s",
                                  (ids[C.key],)).fetchone()
    assert (basis, hop) == ("INFERRED", 1)
    assert path == [{"src": C.key, "dst": B.key, "rel": "SUPPLY", "source": "COSMOS", "strength": 1.0}]
    assert count(pg, "SELECT count(*) FROM company_document WHERE company_id = %s", ids[C.key]) == 0


def test_hold_requires_reason(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    load_events(pg, [bundle(synthetic_docs)], ids)
    pg.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("UPDATE event_company_impact SET status = 'HOLD', hold_reason = NULL WHERE company_id = %s",
                   (ids[C.key],))
    pg.rollback()


def test_news_visible_in_existing_query(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    load_events(pg, [bundle(synthetic_docs)], ids)
    visible = count(pg, """
        SELECT count(*) FROM source_document sd
        JOIN news_article na ON na.document_id = sd.document_id
        JOIN company_document cd ON cd.document_id = sd.document_id AND cd.is_service_visible = TRUE
        JOIN company c ON c.company_id = cd.company_id AND c.status = 'ACTIVE'
        WHERE sd.document_type = 'NEWS' AND sd.document_id = %s""", document_id("SYN.20261013100000001"))
    assert visible == 2
    assert count(pg, "SELECT count(*) FROM news_event WHERE event_id = %s", event_id("SYN.20261013100000001")) == 1


def test_load_relations_and_prices(pg):
    ids = ensure_companies(pg, [A, B, C])
    edges = [Edge(A.key, B.key, "SUPPLY", 0.85, True, "NEWS"), Edge(A.key, C.key, "PARTNER", 1.0, False, "COSMOS")]
    snippets = [EvidenceSnippet("N9", A.key, B.key, "SUPPLY", "가상전자는 하늘반도체에 납품한다.", "subj-target", 0.7,
                                "제목", "합성일보", datetime(2026, 10, 13, 10, tzinfo=KST), "")]
    load_relations(pg, edges, snippets, ids)
    load_relations(pg, edges, snippets, ids)
    assert count(pg, "SELECT count(*) FROM company_relationship WHERE relation_source = 'NEWS'") == 1
    assert count(pg, "SELECT count(*) FROM company_relationship WHERE relation_source = 'COSMOS'") == 1
    assert count(pg, "SELECT count(*) FROM relation_evidence_snippet") == 1
    bars = pd.DataFrame({"Open": [10.0, 11.0], "High": [12.0, 12.0], "Low": [9.0, 10.0], "Close": [11.0, 11.5],
                         "Volume": [100, 200]}, index=pd.to_datetime(["2026-10-12", "2026-10-13"]))
    load_prices(pg, ids, {B.key: bars})
    load_prices(pg, ids, {B.key: bars})
    assert count(pg, "SELECT count(*) FROM stock_price_history WHERE company_id = %s", ids[B.key]) == 2


def test_prices_use_utc_midnight_and_update(pg):
    ids = ensure_companies(pg, [A, B, C])
    bars = pd.DataFrame({"Open": [10.0], "High": [12.0], "Low": [9.0], "Close": [11.0], "Volume": [100]},
                        index=pd.to_datetime(["2026-10-12"]))
    load_prices(pg, ids, {B.key: bars})
    load_prices(pg, ids, {B.key: bars.assign(Close=11.5)})
    trading_at, close = pg.execute("SELECT trading_at, close_price FROM stock_price_history WHERE company_id = %s",
                                   (ids[B.key],)).fetchone()
    assert trading_at == datetime(2026, 10, 12, tzinfo=timezone.utc)   # COSMOS convention: trading date at UTC midnight
    assert float(close) == 11.5


def test_reload_drops_removed_mention(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    load_events(pg, [bundle(synthetic_docs)], ids)
    narrowed = bundle(synthetic_docs)
    narrowed.mentions = narrowed.mentions[:1]
    narrowed.impacts = narrowed.impacts[:1]
    load_events(pg, [narrowed], ids)
    doc = document_id("SYN.20261013100000001")
    assert count(pg, "SELECT count(*) FROM company_document WHERE document_id = %s", doc) == 1
    assert count(pg, "SELECT count(*) FROM event_company_impact") == 1


def test_prune_events_removes_events_not_in_new_run(pg, synthetic_docs):
    from nexus.load.postgres import prune_events
    ids = ensure_companies(pg, [A, B, C])
    other = bundle(synthetic_docs)
    other.article = to_article(synthetic_docs[2])
    load_events(pg, [bundle(synthetic_docs), other], ids)
    assert prune_events(pg, "SEMICONDUCTOR", {"SYN.20261013100000001"}) == 1
    assert count(pg, "SELECT count(*) FROM news_event") == 1
    assert count(pg, "SELECT count(*) FROM company_document WHERE document_id = %s",
                 document_id("SYN.20261013154000003")) == 0
    assert count(pg, "SELECT count(*) FROM event_company_impact WHERE event_id = %s",
                 event_id("SYN.20261013154000003")) == 0


def test_load_relations_replaces_previous_news_rows(pg):
    ids = ensure_companies(pg, [A, B, C])
    first = [Edge(A.key, B.key, "SUPPLY", 0.85, True, "NEWS")]
    snip = EvidenceSnippet("N9", A.key, B.key, "SUPPLY", "s", "subj-target", 0.7, "t", "p",
                           datetime(2026, 10, 13, 10, tzinfo=KST), "")
    load_relations(pg, first, [snip], ids)
    load_relations(pg, [Edge(A.key, C.key, "SUPPLY", 0.9, True, "NEWS")], [], ids)
    pairs = pg.execute("SELECT source_company_id, target_company_id FROM company_relationship "
                       "WHERE relation_source = 'NEWS'").fetchall()
    assert pairs == [(ids[A.key], ids[C.key])]
    assert count(pg, "SELECT count(*) FROM relation_evidence_snippet") == 0


D = Company("KOSDAQ:900004", "KOSDAQ", "900004", "숲부품", "SEMICONDUCTOR", "부품")


def bundle_with_hold(synthetic_docs):
    b = bundle(synthetic_docs)
    b.impacts.append(Impact(D.key, "INFERRED", 1, (PathStep(D.key, A.key, "SUPPLY", "COSMOS", 1.0),), 0.1, "HOLD",
                            "전이 강도 낮음"))
    return b


def impact_row(conn, company_id):
    return conn.execute("SELECT impact_direction, impact_magnitude, confidence, model_version FROM event_company_impact "
                        "WHERE company_id = %s", (company_id,)).fetchone()


def test_write_predictions_sets_values_and_version(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C, D])
    load_events(pg, [bundle_with_hold(synthetic_docs)], ids)
    news = "SYN.20261013100000001"
    n = write_predictions(pg, "SEMICONDUCTOR", [(news, ids[A.key], "POSITIVE", 0.62, 0.31),
                                                (news, ids[B.key], None, 0.41, 0.12),
                                                (news, ids[D.key], "NEGATIVE", 0.9, 0.8)])
    assert n == 2                                                      # HOLD 행은 갱신하지 않는다
    assert impact_row(pg, ids[A.key]) == ("POSITIVE", Decimal("0.310000"), Decimal("0.620000"), IMPACT_MODEL_VERSION)
    assert impact_row(pg, ids[B.key])[:3] == (None, Decimal("0.120000"), Decimal("0.410000"))
    assert impact_row(pg, ids[D.key]) == (None, None, None, "rule-propagation-v1")


def test_write_predictions_is_idempotent_and_resets_old_values(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C, D])
    load_events(pg, [bundle_with_hold(synthetic_docs)], ids)
    news = "SYN.20261013100000001"
    write_predictions(pg, "SEMICONDUCTOR", [(news, ids[A.key], "POSITIVE", 0.62, 0.31),
                                            (news, ids[B.key], "NEGATIVE", 0.7, 0.5)])
    write_predictions(pg, "SEMICONDUCTOR", [(news, ids[A.key], "POSITIVE", 0.62, 0.31)])
    write_predictions(pg, "SEMICONDUCTOR", [(news, ids[A.key], "POSITIVE", 0.62, 0.31)])
    assert impact_row(pg, ids[A.key])[0] == "POSITIVE"
    assert impact_row(pg, ids[B.key]) == (None, None, None, "rule-propagation-v1")
