from datetime import datetime, timedelta, timezone

import pytest

from nexus.galaxy.build import as_of_for, comention_edges, evidence_edges, in_window
from nexus.models import Article, Company, Edge, EvidenceSnippet

KST = timezone(timedelta(hours=9))
AS_OF = datetime(2026, 10, 14, tzinfo=KST)


def co(key, name, market="KOSDAQ"):
    return Company(key, market, key.split(":")[1], name, "SEMICONDUCTOR", "장비")


A, B, C, N = co("KOSPI:000001", "가전자", "KOSPI"), co("KOSDAQ:000002", "나장비"), co("KOSDAQ:000003", "다소재"), \
    co("NASDAQ:NV", "엔비", "NASDAQ")


def art(news_id, at, segments):
    return Article(news_id, "제목", "본문", at.date(), at, "테스트일보", "", ("경제>반도체",), (), "", (), segments, ())


def pair_arts(x, y, n, days_ago=1, tag=""):
    """n articles where companies x and y share one segment, newest first a minute apart."""
    return [art(f"{tag}{x.key}-{y.key}-{i}", AS_OF - timedelta(days=days_ago, minutes=i),
                (((x.name, "OG"), (y.name, "OG")),)) for i in range(n)]


# Unrelated articles: with only the pair articles every pair's NPMI is <= 0 and gets filtered before the rule
# under test; real data (355k articles) has the same effect.
FILLER = [art(f"filler-{i}", AS_OF - timedelta(days=2, minutes=i), ()) for i in range(2000)]


def setup(*companies):
    by_key = {c.key: c for c in companies}
    names = {c.name: c.key for c in companies}
    return by_key, names.get


def test_as_of_is_day_after_last_release_kst():
    assert as_of_for([art("1", datetime(2026, 10, 13, 23, 30, tzinfo=KST), ())]) == AS_OF


def test_window_bounds():
    assert in_window(AS_OF - timedelta(days=30), AS_OF, 30) and not in_window(AS_OF, AS_OF, 30)
    assert not in_window(AS_OF - timedelta(days=30, seconds=1), AS_OF, 30)
    assert in_window(datetime(2020, 1, 1, tzinfo=KST), AS_OF, None)


def test_comention_needs_three_articles_and_scores_up_to_sixty():
    companies, key_of = setup(A, B, C)
    arts = pair_arts(A, B, 4) + pair_arts(A, C, 3) + pair_arts(B, C, 2) + FILLER
    edges = [e for e in comention_edges(arts, key_of, companies, AS_OF) if e.window == "30D"]
    assert {frozenset((e.src, e.dst)) for e in edges} == {frozenset((A.key, B.key)), frozenset((A.key, C.key))}
    assert max(e.score for e in edges) == pytest.approx(60.0) and all(0 < e.score <= 60 for e in edges)
    assert all(e.rel == "CO_MENTIONED" and e.source == "COMENTION" and e.confidence is None and e.src < e.dst
               for e in edges)


def test_comention_keeps_top_five_per_company():
    bs = [co(f"KOSDAQ:1000{i}", f"비{i}") for i in range(1, 8)]
    ds = [co(f"KOSDAQ:2000{j}", f"디{j}") for j in range(1, 6)]
    companies, key_of = setup(A, *bs, *ds)
    arts = [a for i, b in enumerate(bs, 1) for a in pair_arts(A, b, i + 2)]          # A–B1..B7: 3..9건
    arts += [a for b in bs for d in ds for a in pair_arts(b, d, 10, tag=b.key)]        # B마다 더 강한 상대 5개
    arts += FILLER
    tenY = [e for e in comention_edges(arts, key_of, companies, AS_OF) if e.window == "10Y"]
    partners = {({e.src, e.dst} - {A.key}).pop() for e in tenY if A.key in (e.src, e.dst)}
    assert partners == {b.key for b in bs[2:]}                                          # A 쪽 상위 5개(B3..B7)


def test_comention_drops_non_domestic_pairs():
    companies, key_of = setup(A, N)
    assert comention_edges(pair_arts(A, N, 5), key_of, companies, AS_OF) == []


def test_comention_docs_are_recent_and_capped():
    companies, key_of = setup(A, B)
    e = next(e for e in comention_edges(pair_arts(A, B, 25), key_of, companies, AS_OF) if e.window == "30D")
    assert len(e.docs) == 20 and all(d.sentence is None for d in e.docs)
    assert [d.published for d in e.docs] == sorted((d.published for d in e.docs), reverse=True)


def test_evidence_edge_scores_and_docs():
    companies, _ = setup(A, B)
    src, dst = sorted((A.key, B.key))
    snip = lambda nid, days, conf: EvidenceSnippet(nid, dst, src, "PARTNER", f"문장 {nid}", "enum+kw", conf, "제목",
                                                   "테스트일보", AS_OF - timedelta(days=days), "https://x/" + nid)
    snippets = [snip("n1", 1, 0.7), snip("n1", 1, 0.7), snip("n2", 2, 0.9), snip("n3", 3, 0.6),
                snip("n4", 4, 0.8), snip("old", 40, 0.95)]
    edges = evidence_edges([Edge(src, dst, "PARTNER", 0.85, False, "NEWS")], snippets, companies, AS_OF)
    e30 = next(e for e in edges if e.window == "30D")
    assert e30.score == pytest.approx(100.0)                    # 조각 5개 → 60 + 10 × 4
    assert e30.confidence == pytest.approx(0.9)
    assert sorted(d.news_id for d in e30.docs) == ["n1", "n2", "n3", "n4"] and all(d.sentence for d in e30.docs)
    assert next(e for e in edges if e.window == "1Y").confidence == pytest.approx(0.95)


def test_empty_window_is_not_an_error():
    companies, key_of = setup(A, B)
    edges = comention_edges(pair_arts(A, B, 4, days_ago=40), key_of, companies, AS_OF)
    assert not [e for e in edges if e.window == "30D"] and [e for e in edges if e.window == "1Y"]

