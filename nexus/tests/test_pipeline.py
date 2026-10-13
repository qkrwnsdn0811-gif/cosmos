from datetime import date

import pandas as pd

from nexus.entities.master import alias_rows
from nexus.entities.recognize import Recognizer
from nexus.impact.prices import PriceCache
from nexus.ingest.normalize import to_article
from nexus.models import Company, Edge
from nexus.pipeline import run_analyze
from nexus.relations.graph import BaseGraph

GAGSANG = Company("KOSPI:900001", "KOSPI", "900001", "가상전자", "SEMICONDUCTOR", "제조")
HANEUL = Company("KOSDAQ:900002", "KOSDAQ", "900002", "하늘반도체", "SEMICONDUCTOR", "장비")
BADA = Company("KOSDAQ:900003", "KOSDAQ", "900003", "바다소재", "SEMICONDUCTOR", "소재")
COMPANIES = {c.key: c for c in (GAGSANG, HANEUL, BADA)}
DAYS = [date(2026, 10, d) for d in (8, 9, 12, 13, 14, 15, 16, 19)]


def fake_fetch(symbol, start, end):
    days = [d for d in DAYS if start <= d <= end]
    closes = [100.0 + i for i, _ in enumerate(days)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1},
                        index=pd.to_datetime(days))


def analyze(docs, tmp_path):
    companies = list(COMPANIES.values())
    recognizer = Recognizer(companies, alias_rows(companies, [], []))
    graph = BaseGraph([Edge(BADA.key, HANEUL.key, "SUPPLY", 1.0, True, "NEWS")])
    prices = PriceCache(tmp_path, fetch=fake_fetch, today=lambda: date(2026, 10, 25))
    return run_analyze([to_article(d) for d in docs], recognizer, COMPANIES, graph, prices,
                       value_chain="SEMICONDUCTOR")


def test_run_analyze_end_to_end_synthetic(synthetic_docs, tmp_path):
    bundles = {b.article.news_id: b for b in analyze(synthetic_docs, tmp_path)}
    assert set(bundles) == {"SYN.20261013100000001", "SYN.20261013110000002", "SYN.20261013154000003"}
    for b in bundles.values():
        assert b.impacts[0].basis == "REPORTED"
        assert b.value_chain == "SEMICONDUCTOR"

    first = bundles["SYN.20261013100000001"]
    assert [m.key for m in first.mentions] == [HANEUL.key, GAGSANG.key]   # both salience 1.0 → key order
    inferred = [i for i in first.impacts if i.basis == "INFERRED"]
    assert [(i.key, i.hop) for i in inferred] == [(BADA.key, 1)]
    assert first.trading_date == date(2026, 10, 13)
    assert {(r.key, r.window_days, r.benchmark) for r in first.returns} >= {
        (GAGSANG.key, 1, "KOSPI"), (GAGSANG.key, 3, "KOSPI"), (BADA.key, 1, "KOSDAQ")}

    after_close = bundles["SYN.20261013154000003"]
    assert after_close.trading_date == date(2026, 10, 14)
    assert [m.key for m in after_close.mentions] == [HANEUL.key]


def test_articles_without_master_company_are_not_events(synthetic_docs, tmp_path):
    unrelated = [d for d in synthetic_docs if d["news_id"] == "SYN.20261013160000004"]
    assert analyze(unrelated, tmp_path) == []


def test_month_windows_always_start_at_month_start():
    from nexus.pipeline import _months
    # A partial-month request must still rewrite the whole month partition, never just a slice of it.
    assert list(_months(date(2026, 10, 10), date(2026, 10, 15))) == [(date(2026, 10, 1), date(2026, 10, 15), "2026-10")]
    assert list(_months(date(2026, 9, 20), date(2026, 10, 3))) == [
        (date(2026, 9, 1), date(2026, 10, 1), "2026-09"), (date(2026, 10, 1), date(2026, 10, 3), "2026-10")]
