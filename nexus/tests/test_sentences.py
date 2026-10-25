from datetime import date, datetime, timedelta, timezone

import pytest

from nexus.entities.sentences import news_evidence, split_sentences
from nexus.models import Article, CompanyMention, EventBundle

KST = timezone(timedelta(hours=9))
NAMES = {"삼성전자": "KOSPI:005930", "SK하이닉스": "KOSPI:000660", "한미반도체": "KOSPI:042700"}


def keys_in(text: str) -> set[str]:
    return {key for name, key in NAMES.items() if name in text}


def _bundle(title: str, content: str, mentions: list[tuple[str, float]]) -> EventBundle:
    day = date(2026, 10, 13)
    article = Article("N1", title, content, day, datetime(2026, 10, 13, 9, tzinfo=KST), "p", "", (), (), "", (), (), ())
    return EventBundle(article, "SEMICONDUCTOR", [CompanyMention(k, s, ("title",)) for k, s in mentions], [],
                       trading_date=day)


def test_split_sentences_keeps_a_cut_tail_marked():
    assert split_sentences("삼성전자가 웃었다. SK하이닉스도 올랐다! 한미반도체는") == \
        ["삼성전자가 웃었다.", "SK하이닉스도 올랐다!", "한미반도체는…"]
    assert split_sentences("") == []
    assert split_sentences("  한 줄\n둘째 줄.  ") == ["한 줄", "둘째 줄."]


def test_each_sentence_once_under_its_most_salient_company():
    b = _bundle("삼성전자·SK하이닉스 실적 호조",
                "[매체] 삼성전자가 최대 실적을 냈다. SK하이닉스도 웃었다. 시장은 차분했다.",
                [("KOSPI:005930", 1.0), ("KOSPI:000660", 1.0)])
    rows, confidence = news_evidence(b, keys_in)
    # the title names both: it goes once, to the first of the most salient (key order breaks the tie)
    assert [(r.order, r.company_key, r.sentence) for r in rows] == [
        (0, "KOSPI:000660", "삼성전자·SK하이닉스 실적 호조"),
        (1, "KOSPI:005930", "[매체] 삼성전자가 최대 실적을 냈다."),
        (2, "KOSPI:000660", "SK하이닉스도 웃었다."),
    ]
    # COSMOS rule: confidence = min(1, 0.40 + 0.15 × sentences that mention the company) — the shared title counts for both
    assert confidence == {"KOSPI:005930": pytest.approx(0.70), "KOSPI:000660": pytest.approx(0.70)}
    assert [r.confidence for r in rows] == [pytest.approx(0.70)] * 3


def test_companies_outside_the_mentions_and_without_sentences_get_nothing():
    b = _bundle("반도체 업황 점검", "한미반도체 장비가 주목받았다.", [("KOSPI:005930", 0.6)])   # 삼성전자 from full-body orgs only
    rows, confidence = news_evidence(b, keys_in)
    assert rows == [] and confidence == {}


def test_confidence_caps_at_one():
    b = _bundle("삼성전자", "삼성전자 1. 삼성전자 2. 삼성전자 3. 삼성전자 4. 삼성전자 5.", [("KOSPI:005930", 1.0)])
    _, confidence = news_evidence(b, keys_in)
    assert confidence["KOSPI:005930"] == pytest.approx(1.0)
