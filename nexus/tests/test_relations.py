import math
from datetime import date, datetime, timedelta, timezone

from nexus.models import Article, Company
from nexus.relations.comention import sentence_comentions
from nexus.relations.evidence import extract_evidence, hilight_fragments

KST = timezone(timedelta(hours=9))
KEYS = {"A사": "KOSPI:000001", "B사": "KOSDAQ:000002", "서울": "KOSPI:999999"}
A = Company("KOSPI:000001", "KOSPI", "000001", "A사", "SEMICONDUCTOR", "장비")
B = Company("KOSDAQ:000002", "KOSDAQ", "000002", "B사", "SEMICONDUCTOR", "제조")


def art(news_id, *segments):
    return Article(news_id, "t", "", date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=KST), "p", "",
                   (), (), "", (), tuple(tuple(s) for s in segments), ())


def og(*names):
    return [(n, "OG") for n in names]


def test_comention_counts_once_per_article():
    articles = [art(f"n{i}", og("A사", "B사"), og("A사", "B사")) for i in range(3)]
    stats = sentence_comentions(articles, KEYS.get)
    assert len(stats) == 1
    assert (stats[0].a, stats[0].b, stats[0].n_ab) == ("KOSDAQ:000002", "KOSPI:000001", 3)


def test_comention_npmi_value():
    articles = [art(f"ab{i}", og("A사", "B사")) for i in range(3)]
    articles += [art("a", og("A사")), art("b", og("B사"))]
    articles += [art(f"x{i}") for i in range(5)]
    [stat] = sentence_comentions(articles, KEYS.get)
    assert (stat.n_ab, stat.n_a, stat.n_b, stat.n_docs) == (3, 4, 4, 10)
    assert round(stat.npmi, 6) == round(math.log(3 * 10 / 16) / -math.log(3 / 10), 6)


def test_comention_ignores_non_og_and_unknown():
    segment = [("A사", "OG"), ("서울", "LC"), ("홍길동", "PS"), ("모르는회사", "OG")]
    articles = [art(f"n{i}", segment) for i in range(5)]
    assert sentence_comentions(articles, KEYS.get) == []


def test_fragments_strip_tags_and_split():
    assert hilight_fragments("앞 문장..<b>A사</b>는 <b>B사</b>에 납품한다. 뒤") == ["앞 문장", "A사는 B사에 납품한다.", "뒤"]


def pair_doc(hilight):
    return {"news_id": "N1", "title": "제목", "hilight": hilight, "dateline": "2026-10-13T10:00:00.000+09:00",
            "provider": "합성일보", "provider_link_page": "https://example.com/1"}


def aliases_of(key):
    return {A.key: ["A사"], B.key: ["B사"]}[key]


def test_extract_supply_direction():
    [snippet] = extract_evidence([pair_doc("<b>A사</b>는 <b>B사</b>에 장비를 납품한다.")], A, B, aliases_of)
    assert (snippet.src, snippet.dst, snippet.rel, snippet.pattern) == (A.key, B.key, "SUPPLY", "subj-target")
    assert snippet.snippet == "A사는 B사에 장비를 납품한다."
    assert snippet.news_id == "N1" and snippet.link == "https://example.com/1"
    assert snippet.published.utcoffset() == timedelta(hours=9)


def test_extract_skips_fragment_without_both():
    docs = [pair_doc("<b>A사</b>는 장비를 납품한다..<b>B사</b>가 증설했다.")]
    assert extract_evidence(docs, A, B, aliases_of) == []
