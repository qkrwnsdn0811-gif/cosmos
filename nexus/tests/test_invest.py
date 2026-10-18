from nexus.models import Company
from nexus.relations.evidence import extract_evidence
from nexus.relations.invest import classify_invest

HOLD = "하나머티리얼즈가 보유한 하나마이크론 보통주 290만7991주가 교환 대상이다."
SUB = "하나마이크론, 종속회사 하나머티리얼즈 1000억 교환사채 발행"


def rel(hit):
    return hit[:3] if hit else None


def test_holding_a_stake():
    assert rel(classify_invest(HOLD, "하나머티리얼즈", "하나마이크론")) == ("하나머티리얼즈", "하나마이크론", "INVEST")
    assert classify_invest(HOLD, "하나마이크론", "하나머티리얼즈") is None          # 방향은 문장이 정한다


def test_subsidiary_after_comma_or_possessive():
    assert rel(classify_invest(SUB, "하나마이크론", "하나머티리얼즈")) == ("하나마이크론", "하나머티리얼즈", "INVEST")
    assert rel(classify_invest("삼성전자의 자회사인 세메스가 장비를 늘린다", "삼성전자", "세메스")) == \
        ("삼성전자", "세메스", "INVEST")


def test_parent_named_after_child():
    assert rel(classify_invest("SK하이닉스의 최대주주인 SK스퀘어는 배당을 늘렸다", "SK스퀘어", "SK하이닉스")) == \
        ("SK스퀘어", "SK하이닉스", "INVEST")


def test_completed_acquisition():
    assert rel(classify_invest("삼성전자가 레인보우로보틱스를 인수했다", "삼성전자", "레인보우로보틱스")) == \
        ("삼성전자", "레인보우로보틱스", "INVEST")
    assert classify_invest("삼성전자가 레인보우로보틱스를 인수할 계획이다", "삼성전자", "레인보우로보틱스") is None


def test_hedged_or_plain_mentions_are_not_invest():
    assert classify_invest("삼성전자의 자회사인 세메스 매각설이 돌았다", "삼성전자", "세메스") is None
    assert classify_invest("삼성전자와 SK하이닉스 지분 가치가 올랐다", "삼성전자", "SK하이닉스") is None


def test_extract_evidence_falls_back_to_invest():
    a = Company("KOSDAQ:166090", "KOSDAQ", "166090", "하나머티리얼즈", "SEMICONDUCTOR", "소재")
    b = Company("KOSDAQ:067310", "KOSDAQ", "067310", "하나마이크론", "SEMICONDUCTOR", "후공정")
    doc = {"news_id": "N1", "title": "제목", "hilight": f"..<b>{SUB}</b>..", "dateline": "2026-07-01T10:00:00.000+09:00",
           "provider": "합성일보", "provider_link_page": ""}
    [snippet] = extract_evidence([doc], a, b, lambda key: {a.key: [a.name], b.key: [b.name]}[key])
    assert (snippet.src, snippet.dst, snippet.rel, snippet.pattern) == (b.key, a.key, "INVEST", "invest-sub")
    assert snippet.confidence >= 0.5
