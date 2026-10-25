from datetime import datetime, timedelta, timezone

from nexus.galaxy.build import GalaxyDoc, GalaxyEdge
from nexus.ingest.normalize import to_article
from nexus.load.galaxy import publish_galaxy
from nexus.load.postgres import document_id, ensure_companies, load_events, load_relations
from nexus.models import Company, CompanyMention, Edge, EventBundle, Impact

KST = timezone(timedelta(hours=9))
AS_OF = datetime(2026, 10, 14, tzinfo=KST)
A = Company("KOSPI:900001", "KOSPI", "900001", "가상전자", "SEMICONDUCTOR", "제조")
B = Company("KOSDAQ:900002", "KOSDAQ", "900002", "하늘반도체", "SEMICONDUCTOR", "장비")
C = Company("KOSDAQ:900003", "KOSDAQ", "900003", "바다소재", "SEMICONDUCTOR", "소재")
ROLES = {c.key: ("반도체", c.role) for c in (A, B, C)}
LOADED = "SYN.20261013100000001"          # synthetic_docs[0], load_events로 미리 적재
MISSING = "SYN.NOT.LOADED"


def q(conn, sql, *params):
    return conn.execute(sql, params).fetchall()


def doc(news_id, sentence=None):
    return GalaxyDoc(news_id, "제목", "테스트일보", AS_OF - timedelta(days=1), "", sentence)


def edges(with_comention=True):
    ab = [GalaxyEdge(A.key, B.key, "PARTNER", "NEWS", w, s, 0.8, (doc(LOADED, "협력 문장"), doc(MISSING, "또 다른 문장")))
          for w, s in (("30D", 80.0), ("1Y", 90.0), ("10Y", 90.0))]
    bc = [GalaxyEdge(B.key, C.key, "CO_MENTIONED", "COMENTION", w, 40.0, None, (doc(LOADED),))
          for w in ("1Y", "10Y")]
    return ab + (bc if with_comention else [])


def seed(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    article = to_article(synthetic_docs[0])
    load_events(pg, [EventBundle(article, "SEMICONDUCTOR",
                                 [CompanyMention(A.key, 1.0, ("title",)), CompanyMention(B.key, 0.8, ("content",))],
                                 [Impact(A.key, "REPORTED", 0, (), 1.0, "OK", None),
                                  Impact(B.key, "REPORTED", 0, (), 0.8, "OK", None)], [], None)], ids)
    return ids, article


def test_publish_writes_snapshot_scores_and_evidence(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    assert q(pg, "SELECT status, hdfs_uri FROM graph_snapshot") == [("PUBLISHED", "bigkinds://nexus/2026-10-14")]
    assert dict(q(pg, "SELECT window_type, count(*) FROM relationship_score_current GROUP BY 1")) == \
        {"30D": 1, "1Y": 2, "10Y": 2}
    assert q(pg, "SELECT count(*) FROM relationship_score_current WHERE news_score <> score OR disclosure_score IS NOT NULL") == [(0,)]
    mismatched = q(pg, """SELECT rsc.relationship_id FROM relationship_score_current rsc
                          WHERE rsc.evidence_count <> (SELECT count(*) FROM relationship_evidence re
                                                       WHERE re.relationship_id = rsc.relationship_id)""")
    assert mismatched == []
    assert q(pg, "SELECT count(*) FROM source_document WHERE document_id = %s", document_id(MISSING)) == [(1,)]
    assert q(pg, """SELECT cr.relation_source FROM company_relationship cr JOIN relationship_type rt
                    USING (relationship_type_id) WHERE rt.code = 'CO_MENTIONED'""") == [("COMENTION",)]


def test_republish_same_as_of_reuses_snapshot_and_supersedes_others(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    publish_galaxy(pg, AS_OF - timedelta(days=7), edges(), ids, ROLES)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    assert sorted(q(pg, "SELECT status, count(*) FROM graph_snapshot GROUP BY 1")) == [("PUBLISHED", 1), ("SUPERSEDED", 1)]
    assert q(pg, "SELECT count(*) FROM relationship_score_current") == [(5,)]


def test_reverse_oriented_undirected_relationship_is_reused(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    load_relations(pg, [Edge(B.key, A.key, "PARTNER", 1.0, False, "COSMOS")], [], ids)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    assert q(pg, """SELECT count(*) FROM company_relationship cr JOIN relationship_type rt USING (relationship_type_id)
                    WHERE rt.code = 'PARTNER'""") == [(1,)]


def test_existing_primary_industry_is_kept(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    pg.execute("INSERT INTO industry (name) VALUES ('기존 업종')")
    pg.execute("INSERT INTO company_industry SELECT %s, industry_id, TRUE FROM industry WHERE name = '기존 업종'", (ids[A.key],))
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    assert sorted(q(pg, """SELECT i.name, ci.is_primary FROM company_industry ci JOIN industry i USING (industry_id)
                           WHERE ci.company_id = %s""", ids[A.key])) == [("기존 업종", True), ("반도체 제조", False)]
    assert q(pg, """SELECT i.name FROM company_industry ci JOIN industry i USING (industry_id)
                    WHERE ci.company_id = %s AND ci.is_primary""", ids[B.key]) == [("반도체 장비",)]


def test_reloading_events_after_publish_does_not_fail(pg, synthetic_docs):
    ids, article = seed(pg, synthetic_docs)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    load_events(pg, [EventBundle(article, "SEMICONDUCTOR", [CompanyMention(A.key, 1.0, ("title",))],
                                 [Impact(A.key, "REPORTED", 0, (), 1.0, "OK", None)], [], None)], ids)
    assert q(pg, """SELECT count(*) FROM relationship_evidence re JOIN document_evidence de USING (evidence_id)
                    WHERE de.document_id = %s""", document_id(LOADED)) == [(0,)]      # 적재가 지운 문장의 근거는 정리됨
    short = q(pg, """SELECT rsc.relationship_id FROM relationship_score_current rsc
                     WHERE rsc.evidence_count > (SELECT count(*) FROM relationship_evidence re
                                                 WHERE re.relationship_id = rsc.relationship_id)""")
    assert short == []      # 문장만 빠지고 기사는 남아야 은하 툴팁이 근거를 기다리며 멈추지 않는다


def test_stale_comention_relationships_are_removed(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    publish_galaxy(pg, AS_OF, edges(with_comention=False), ids, ROLES)
    assert q(pg, "SELECT count(*) FROM company_relationship WHERE relation_source = 'COMENTION'") == [(0,)]


def test_dart_ownership_edges_are_disclosure_backed(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    dart = [GalaxyEdge(A.key, C.key, "INVEST", "DART", w, 23.7, None, ()) for w in ("30D", "1Y", "10Y")]
    publish_galaxy(pg, AS_OF, edges() + dart, ids, ROLES)
    assert q(pg, """SELECT cr.relation_source FROM company_relationship cr JOIN relationship_type rt
                    USING (relationship_type_id) WHERE rt.code = 'INVEST'""") == [("DART",)]
    rows = q(pg, """SELECT rsc.window_type, rsc.score, rsc.news_score, rsc.disclosure_score, rsc.evidence_count
                    FROM relationship_score_current rsc JOIN company_relationship cr USING (relationship_id)
                    JOIN relationship_type rt USING (relationship_type_id) WHERE rt.code = 'INVEST' ORDER BY 1""")
    assert [(w, float(s), n, float(d), c) for w, s, n, d, c in rows] == [
        ("10Y", 23.7, None, 23.7, 0), ("1Y", 23.7, None, 23.7, 0), ("30D", 23.7, None, 23.7, 0)]
