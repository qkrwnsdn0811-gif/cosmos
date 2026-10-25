from datetime import date, datetime, timedelta, timezone

from nexus.models import Article
from nexus.relations.activation import active_relations

KST = timezone(timedelta(hours=9))


def art(title="", content="", nouns=()):
    return Article("N1", title, content, date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=KST), "p", "",
                   (), (), "", (), (), tuple(nouns))


def test_supply_keyword_in_title_activates_supply():
    rels = active_relations(art(title="가상전자, 하늘반도체에 장비 납품"))
    assert "SUPPLY" in rels and "CO_MENTIONED" in rels
    assert "COMPETE" not in rels


def test_full_body_nouns_activate_relations():
    assert "COMPETE" in active_relations(art(nouns=("점유율 경쟁", "메모리")))


def test_supply_chain_word_alone_is_not_supply():
    assert "SUPPLY" not in active_relations(art(nouns=("공급망", "반도체")))


def test_invest_keywords():
    assert "INVEST" in active_relations(art(content="가상전자가 하늘반도체 지분을 인수했다"))
    assert active_relations(art(title="메모리 업황 점검")) == {"CO_MENTIONED"}
