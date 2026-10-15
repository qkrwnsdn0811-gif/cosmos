from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from nexus.entities.master import alias_rows
from nexus.entities.recognize import Recognizer
from nexus.impact.features import META_COLUMNS, feature_columns
from nexus.impact.prices import PriceCache
from nexus.ingest.normalize import to_article
from nexus.models import Company, CompanyMention, Edge, EventBundle, Impact
from nexus.pipeline import impact_dataset, main, run_analyze
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

    plant = bundles["SYN.20261013110000002"]           # "신규 공장 준공": no supply/partner/compete words
    assert [i.basis for i in plant.impacts] == ["REPORTED"]

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


def test_build_graph_searches_by_most_frequent_org_alias(tmp_path):
    from datetime import datetime, timedelta, timezone

    from nexus.models import Article
    from nexus.pipeline import build_graph

    hynix = Company("KOSPI:000660", "KOSPI", "000660", "SK하이닉스", "SEMICONDUCTOR", "제조")
    micron = Company("NASDAQ:MU", "NASDAQ", "MU", "Micron Technology", "SEMICONDUCTOR", "제조")
    seed = [{"name": "Micron Technology", "aliases": "마이크론|마이크론 테크놀로지"}]
    recognizer = Recognizer([hynix, micron], alias_rows([hynix, micron], seed, []))
    kst = timezone(timedelta(hours=9))
    articles = [Article(f"n{i}", "t", "", date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=kst), "p", "", (), (),
                        "", ("SK하이닉스", "마이크론"), ((("SK하이닉스", "OG"), ("마이크론", "OG")),), ())
                for i in range(3)]

    class Client:
        queries = []

        def post(self, path, argument, *, cache):
            self.queries.append(argument["query"])
            return {"documents": []}

    cosmos_csv = tmp_path / "rel.csv"
    cosmos_csv.write_text("source_market,source_stock_code,target_market,target_stock_code,relationship_type_code\n",
                          encoding="utf-8")
    client = Client()
    build_graph(articles, recognizer, {c.key: c for c in (hynix, micron)}, client, start=date(2026, 10, 1),
                end=date(2026, 10, 14), today=date(2026, 10, 14), cosmos_csv=cosmos_csv)
    assert client.queries == ["SK하이닉스 AND 마이크론"]


def test_impact_dataset_builds_vocab_from_training_events(synthetic_docs, tmp_path):
    kospi = Company("KOSPI:900001", "KOSPI", "900001", "가상전자", "SEMICONDUCTOR", "제조")
    days = pd.to_datetime([date(2025, 6, 1) + timedelta(days=i) for i in range(300)])
    cache = PriceCache(tmp_path, fetch=lambda s, a, b: pd.DataFrame({"Close": [100.0 + i for i in range(300)]}, index=days),
                       today=lambda: date(2026, 4, 1))
    article = to_article(synthetic_docs[0])
    old = EventBundle(replace(article, news_id="OLD", categories=("경제>반도체",),
                              dateline=datetime(2025, 11, 3, 10, tzinfo=timezone(timedelta(hours=9)))),
                      "SEMICONDUCTOR", [CompanyMention(kospi.key, 1.0, ("title",))],
                      [Impact(kospi.key, "REPORTED", 0, (), 1.0, "OK", None)], [], date(2025, 11, 3))
    new = replace(old, article=replace(old.article, news_id="NEW", categories=("경제>자동차",)), trading_date=date(2026, 2, 2))
    frame, vocab = impact_dataset([old, new], {kospi.key: kospi}, pd.DataFrame(columns=["p_neg", "p_neu", "p_pos"]),
                                  cache, None, today=date(2026, 4, 1))
    assert vocab == ["경제>반도체"]
    assert list(frame.columns) == META_COLUMNS + feature_columns(vocab) and len(frame) == 2


@pytest.mark.parametrize("command", ["sentiment", "train-impact", "predict-impact"])
def test_impact_commands_are_registered(command):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
