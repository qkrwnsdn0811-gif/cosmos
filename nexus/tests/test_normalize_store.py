from datetime import timedelta

from nexus.ingest.normalize import to_article
from nexus.ingest.store import ArticleStore


def doc(**overrides):
    base = {
        "news_id": "N1",
        "title": "제목",
        "content": "본문",
        "published_at": "2026-10-13T00:00:00.000+09:00",
        "dateline": "2026-10-13T23:14:42.000+09:00",
        "provider": "합성일보",
        "byline": "",
        "category": ["경제>반도체"],
        "category_incident": [],
        "provider_link_page": "https://example.com/1",
    }
    base.update(overrides)
    return base


def test_parses_segments():
    article = to_article(doc(tms_ne_stream="A사:OG,3%:QT\nB사:OG"))
    assert article.segments == ((("A사", "OG"), ("3%", "QT")), (("B사", "OG"),))


def test_name_with_colon_uses_last_colon():
    article = to_article(doc(tms_ne_stream="최태원#SK그룹 회장:PS_CLUE"))
    assert article.segments == ((("최태원#SK그룹 회장", "PS_CLUE"),),)


def test_empty_entity_fields(synthetic_docs):
    empty = next(d for d in synthetic_docs if d["news_id"] == "SYN.20261013110000002")
    article = to_article(empty)
    assert article.orgs == ()
    assert article.segments == ()
    assert article.nouns == ()
    assert to_article(doc(tms_ne_organization="", tms_ne_stream="\n\n")).segments == ()


def test_dateline_kst():
    article = to_article(doc())
    assert article.dateline.hour == 23
    assert article.dateline.utcoffset() == timedelta(hours=9)


def test_missing_link_is_empty_string(synthetic_docs):
    no_link = next(d for d in synthetic_docs if "provider_link_page" not in d)
    assert to_article(no_link).link == ""


def test_store_empty_partition(tmp_path, synthetic_docs):
    store = ArticleStore(tmp_path)
    assert store.write([], partition="semis-2025-01") == 0
    store.write([to_article(synthetic_docs[0])], partition="semis-2025-02")
    assert [a.news_id for a in store.read()] == ["SYN.20261013100000001"]


def test_store_roundtrip_and_dedup(tmp_path, synthetic_docs):
    store = ArticleStore(tmp_path)
    articles = [to_article(d) for d in synthetic_docs]
    assert store.write(articles, partition="semis-2026-09") == 4
    renamed = to_article(dict(synthetic_docs[0], title="바뀐 제목"))
    store.write([renamed], partition="semis-2026-10")
    read = store.read()
    assert len(read) == 4
    by_id = {a.news_id: a for a in read}
    assert by_id[renamed.news_id].title == "바뀐 제목"
    assert by_id["SYN.20261013100000001"].segments == renamed.segments
    assert read[1] == articles[1]
