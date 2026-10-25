from datetime import date, datetime, timedelta, timezone

import pandas as pd

from nexus.entities.master import alias_rows, build_master
from nexus.entities.recognize import Recognizer
from nexus.models import Article, Company

KST = timezone(timedelta(hours=9))
HYNIX = Company("KOSPI:000660", "KOSPI", "000660", "SK하이닉스", "SEMICONDUCTOR", "제조")
HANMI = Company("KOSPI:042700", "KOSPI", "042700", "한미반도체", "SEMICONDUCTOR", "장비")
SEED = [{"name": "SK하이닉스", "market_hint": "KOSPI", "role": "제조", "aliases": "SK하닉|하이닉스"},
        {"name": "한미반도체", "market_hint": "KOSPI", "role": "장비", "aliases": ""}]


def article(title="", content="", orgs=()):
    return Article("N1", title, content, date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=KST),
                   "합성일보", "", ("경제>반도체",), (), "", tuple(orgs), (), ())


def recognizer():
    companies = [HYNIX, HANMI]
    return Recognizer(companies, alias_rows(companies, SEED, []))


def test_build_master_resolves_krx_and_nasdaq():
    listing = pd.DataFrame([{"Code": "042700", "Name": "한미반도체", "Market": "KOSPI"}])
    cosmos = [{"ticker": "MU", "market": "NASDAQ", "name_official": "마이크론"}]
    seed = [{"name": "한미반도체", "market_hint": "KOSPI", "role": "장비", "aliases": ""},
            {"name": "마이크론", "market_hint": "NASDAQ", "role": "제조", "aliases": ""}]
    companies, unresolved = build_master(seed, listing, cosmos, "SEMICONDUCTOR")
    assert [c.key for c in companies] == ["KOSPI:042700", "NASDAQ:MU"]
    assert companies[0] == Company("KOSPI:042700", "KOSPI", "042700", "한미반도체", "SEMICONDUCTOR", "장비")
    assert unresolved == []


def test_build_master_reports_unresolved():
    listing = pd.DataFrame([{"Code": "042700", "Name": "한미반도체", "Market": "KOSPI"}])
    seed = [{"name": "없는회사", "market_hint": "KOSDAQ", "role": "소재", "aliases": ""}]
    companies, unresolved = build_master(seed, listing, [], "SEMICONDUCTOR")
    assert companies == []
    assert unresolved == ["없는회사"]


def test_kosdaq_market_from_listing():
    listing = pd.DataFrame([{"Code": "058470", "Name": "리노공업", "Market": "KOSDAQ GLOBAL"}])
    seed = [{"name": "리노공업", "market_hint": "KOSDAQ", "role": "부품", "aliases": ""}]
    companies, _ = build_master(seed, listing, [], "SEMICONDUCTOR")
    assert companies[0].key == "KOSDAQ:058470"
    assert companies[0].market == "KOSDAQ"


def test_title_beats_orgs():
    mentions = recognizer().recognize(article(title="SK하이닉스 실적 발표", orgs=["SK하이닉스"]))
    assert len(mentions) == 1
    assert mentions[0].key == "KOSPI:000660"
    assert mentions[0].salience == 1.0
    assert mentions[0].sources == ("orgs", "title")


def test_orgs_only_exact_alias():
    r = recognizer()
    mentions = r.recognize(article(title="메모리 업황", orgs=["SK하닉", "중국", "정부"]))
    assert [(m.key, m.salience, m.sources) for m in mentions] == [("KOSPI:000660", 0.6, ("orgs",))]
    assert r.key_of_org("중국") is None
    assert r.aliases_of("KOSPI:000660") == ["SK하이닉스", "SK하닉", "하이닉스"]   # longest first, then by text


def test_empty_entities_still_match_title():
    mentions = recognizer().recognize(article(title="한미반도체, 장비 수주", content="한미반도체가 수주했다"))
    assert [(m.key, m.salience, m.sources) for m in mentions] == [("KOSPI:042700", 1.0, ("content", "title"))]


def test_build_master_keeps_only_requested_markets():
    seed = [{"name": "삼성전자", "market_hint": "KOSPI", "role": "제조"},
            {"name": "하나마이크론", "market_hint": "KOSDAQ", "role": "후공정"},
            {"name": "Nvidia", "market_hint": "NASDAQ", "role": "설계"}]
    listing = pd.DataFrame([{"Name": "삼성전자", "Market": "KOSPI", "Code": "005930"},
                            {"Name": "하나마이크론", "Market": "KOSDAQ GLOBAL", "Code": "067310"}])
    cosmos = [{"name_official": "Nvidia", "market": "NASDAQ", "ticker": "NVDA"}]
    companies, unresolved = build_master(seed, listing, cosmos, "SEMICONDUCTOR", markets=("KOSPI", "KOSDAQ"))
    assert [c.key for c in companies] == ["KOSPI:005930", "KOSDAQ:067310"]
    assert unresolved == []
    assert len(build_master(seed, listing, cosmos, "SEMICONDUCTOR")[0]) == 3      # markets 없으면 기존 동작
