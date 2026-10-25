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
    assert client.queries[0] == "SK하이닉스 AND 마이크론"
    assert client.queries[1].startswith("SK하이닉스 AND 마이크론 AND (") and len(client.queries) == 2


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


def test_predict_impact_refuses_to_run_without_sentiment(tmp_path, monkeypatch):
    import nexus.pipeline as pipeline
    monkeypatch.setattr(pipeline, "_paths", lambda chain: {"analyzed": tmp_path, "data": tmp_path, "prices": tmp_path})
    monkeypatch.setattr(pipeline, "_load_master", lambda chain: ({}, None))
    with pytest.raises(SystemExit) as exc:
        main(["predict-impact", "--chain", "semis", "--database-url", "postgresql://unused"])
    assert "sentiment" in str(exc.value.code)


def test_publish_graph_is_registered():
    with pytest.raises(SystemExit) as exc:
        main(["publish-graph", "--help"])
    assert exc.value.code == 0


def test_publish_graph_needs_an_analyzed_chain(tmp_path, monkeypatch):
    import nexus.pipeline as pipeline
    monkeypatch.setattr(pipeline, "_paths", lambda chain: {"analyzed": tmp_path / chain})
    with pytest.raises(SystemExit) as exc:
        main(["publish-graph", "--database-url", "postgresql://unused"])
    assert "analyze" in str(exc.value.code)


def test_build_graph_searches_relation_words_for_pairs_outside_sentence_comentions(tmp_path):
    from datetime import datetime, timedelta, timezone

    from nexus.models import Article
    from nexus.pipeline import build_graph

    a = Company("KOSDAQ:166090", "KOSDAQ", "166090", "하나머티리얼즈", "SEMICONDUCTOR", "소재")
    b = Company("KOSDAQ:067310", "KOSDAQ", "067310", "하나마이크론", "SEMICONDUCTOR", "후공정")
    recognizer = Recognizer([a, b], alias_rows([a, b], [], []))
    kst = timezone(timedelta(hours=9))
    # both companies in one article but in different segments: no sentence co-mention, still a candidate pair
    article = Article("n1", "t", "", date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=kst), "p", "", (), (), "",
                      ("하나머티리얼즈", "하나마이크론"), ((("하나머티리얼즈", "OG"),), (("하나마이크론", "OG"),)), ())
    hit = {"news_id": "x1", "title": "제목", "dateline": "2026-07-01T10:00:00.000+09:00", "provider": "p",
           "provider_link_page": "", "hilight": "하나마이크론, 종속회사 하나머티리얼즈 1000억 교환사채"}

    class Client:
        queries = []

        def post(self, path, argument, *, cache):
            self.queries.append(argument["query"])
            return {"documents": [hit, hit]}          # the same article twice must not double the evidence

    cosmos_csv = tmp_path / "rel.csv"
    cosmos_csv.write_text("source_market,source_stock_code,target_market,target_stock_code,relationship_type_code\n",
                          encoding="utf-8")
    client = Client()
    _, edges, snippets = build_graph([article], recognizer, {c.key: c for c in (a, b)}, client,
                                     start=date(2025, 1, 1), end=date(2026, 10, 14), today=date(2026, 10, 14),
                                     cosmos_csv=cosmos_csv)
    assert len(client.queries) == 1 and " AND (" in client.queries[0]
    assert len(snippets) == 1 and snippets[0].rel == "INVEST"
    assert [(e.src, e.dst, e.rel, e.source) for e in edges if e.source == "NEWS"] == [(b.key, a.key, "INVEST", "NEWS")]


def test_build_graph_skips_a_pair_the_api_keeps_rejecting(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    import nexus.pipeline as pipeline
    from nexus.bigkinds.client import BigKindsError
    from nexus.models import Article
    from nexus.pipeline import build_graph

    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    a = Company("KOSDAQ:166090", "KOSDAQ", "166090", "하나머티리얼즈", "SEMICONDUCTOR", "소재")
    b = Company("KOSDAQ:067310", "KOSDAQ", "067310", "하나마이크론", "SEMICONDUCTOR", "후공정")
    c = Company("KOSPI:005930", "KOSPI", "005930", "삼성전자", "SEMICONDUCTOR", "제조")
    recognizer = Recognizer([a, b, c], alias_rows([a, b, c], [], []))
    kst = timezone(timedelta(hours=9))
    article = Article("n1", "t", "", date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=kst), "p", "", (), (), "",
                      ("하나머티리얼즈", "하나마이크론", "삼성전자"), (), ())
    hit = {"news_id": "x1", "title": "제목", "dateline": "2026-07-01T10:00:00.000+09:00", "provider": "p",
           "provider_link_page": "", "hilight": "하나마이크론, 종속회사 하나머티리얼즈 1000억 교환사채"}

    class Client:
        queries = []

        def post(self, path, argument, *, cache):
            self.queries.append(argument["query"])
            if "삼성전자" in argument["query"]:
                raise BigKindsError(-1, "Parameter Error!: 1990-01-01 이전 날짜는 조회할 수 없습니다.")
            return {"documents": [hit]}

    cosmos_csv = tmp_path / "rel.csv"
    cosmos_csv.write_text("source_market,source_stock_code,target_market,target_stock_code,relationship_type_code" + chr(10),
                          encoding="utf-8")
    client = Client()
    _, edges, snippets = build_graph([article], recognizer, {x.key: x for x in (a, b, c)}, client,
                                     start=date(2025, 1, 1), end=date(2026, 10, 14), today=date(2026, 10, 14),
                                     cosmos_csv=cosmos_csv)
    assert [s.rel for s in snippets] == ["INVEST"]                       # the healthy pair still yields evidence
    assert sum("삼성전자" in q for q in client.queries) == 2 * 3          # two rejected pairs, three tries each


def test_build_graph_adds_model_snippets_without_double_counting(tmp_path):
    from datetime import datetime, timedelta, timezone

    from nexus.models import Article
    from nexus.pipeline import build_graph

    a = Company("KOSPI:009150", "KOSPI", "009150", "삼성전기", "SEMICONDUCTOR", "부품")
    b = Company("KOSDAQ:357780", "KOSDAQ", "357780", "솔브레인", "SEMICONDUCTOR", "소재")
    recognizer = Recognizer([a, b], alias_rows([a, b], [], []))
    kst = timezone(timedelta(hours=9))
    article = Article("n1", "t", "", date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=kst), "p", "", (), (), "",
                      ("삼성전기", "솔브레인"), (), ())
    rule_text = "이를 위해 삼성전기는 국내 재료 회사인 솔브레인과 협력해 연구에 착수했다."
    model_text = "삼성전기와 솔브레인이 유리기판 소재를 함께 만든다."
    hits = [{"news_id": f"x{i}", "title": "제목", "dateline": "2026-07-01T10:00:00.000+09:00", "provider": "p",
             "provider_link_page": "", "hilight": t} for i, t in enumerate((rule_text, model_text))]

    class Client:
        def post(self, path, argument, *, cache):
            return {"documents": hits}

    def predictor(frags):
        return [("PARTNER", 0.97) for _ in frags]                       # 규칙 문장도 같은 관계로 판별

    cosmos_csv = tmp_path / "rel.csv"
    cosmos_csv.write_text("source_market,source_stock_code,target_market,target_stock_code,relationship_type_code" + chr(10),
                          encoding="utf-8")
    _, edges, snippets = build_graph([article], recognizer, {c.key: c for c in (a, b)}, Client(),
                                     start=date(2025, 1, 1), end=date(2026, 10, 14), today=date(2026, 10, 14),
                                     cosmos_csv=cosmos_csv, predictor=(predictor, 0.9))
    assert sorted((s.snippet, s.pattern) for s in snippets) == [(model_text, "kpfbert"), (rule_text, "clause+kw")]


def test_build_graph_points_a_model_supply_ba_from_b_to_a(tmp_path):
    from datetime import datetime, timedelta, timezone

    from nexus.models import Article
    from nexus.pipeline import build_graph

    a = Company("KOSPI:009150", "KOSPI", "009150", "삼성전기", "SEMICONDUCTOR", "부품")
    b = Company("KOSDAQ:357780", "KOSDAQ", "357780", "솔브레인", "SEMICONDUCTOR", "소재")
    recognizer = Recognizer([a, b], alias_rows([a, b], [], []))
    kst = timezone(timedelta(hours=9))
    article = Article("n1", "t", "", date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=kst), "p", "", (), (), "",
                      ("삼성전기", "솔브레인"), (), ())
    hits = [{"news_id": "x0", "title": "제목", "dateline": "2026-07-01T10:00:00.000+09:00", "provider": "p",
             "provider_link_page": "", "hilight": "삼성전기를 고객사로 둔 솔브레인의 실적이 좋아졌다."}]

    class Client:
        def post(self, path, argument, *, cache):
            return {"documents": hits}

    def predictor(frags):
        # pair order is by key: A = KOSDAQ:357780 (솔브레인), B = KOSPI:009150 (삼성전기); SUPPLY_BA = B supplies A
        assert [(f.a_key, f.b_key) for f in frags] == [(b.key, a.key)]
        return [("SUPPLY_AB", 0.95) for _ in frags]

    cosmos_csv = tmp_path / "rel.csv"
    cosmos_csv.write_text("source_market,source_stock_code,target_market,target_stock_code,relationship_type_code" + chr(10),
                          encoding="utf-8")
    _, edges, _ = build_graph([article], recognizer, {c.key: c for c in (a, b)}, Client(),
                              start=date(2025, 1, 1), end=date(2026, 10, 14), today=date(2026, 10, 14),
                              cosmos_csv=cosmos_csv, predictor=(predictor, 0.9))
    assert [(e.src, e.dst) for e in edges if e.rel == "SUPPLY"] == [(b.key, a.key)]   # 솔브레인 → 삼성전기


def _train_relations_setup(tmp_path, monkeypatch, precision):
    import argparse
    import json

    import nexus.pipeline as pipeline
    import nexus.relations.kpfbert as kpfbert
    from nexus.relations.fragments import Fragment, write_fragments

    rel = tmp_path / "relations"
    frags = [Fragment(f"{i:016x}", f"KOSDAQ:9000{i % 4}", "KOSPI:900009", "가", "나", f"문장 {i}번은 서로 다르다 {i * 7}",
                      f"N{i}", "", "", None, "", None, None) for i in range(40)]
    write_fragments(frags, rel / "fragments.parquet")
    (rel / "labels.jsonl").write_text("\n".join(json.dumps({"fragment_id": f.fragment_id, "label": "NONE"})
                                                for f in frags), encoding="utf-8")
    (rel / "model").mkdir()
    (rel / "model" / "old.txt").write_text("applied", encoding="utf-8")

    def fake_train(train, valid, test, out_dir, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "new.txt").write_text("trained", encoding="utf-8")
        return {"tau": 0.9, "test_precision": precision, "test_recall": 0.5, "rule_precision": 1.0,
                "rule_recall": 0.1, "per_class": {}, "confusion": [], "sizes": {}}

    monkeypatch.setattr(pipeline, "_relations_dir", lambda: rel)
    monkeypatch.setattr(pipeline, "RELATION_REPORT", tmp_path / "report.md")
    monkeypatch.setattr(kpfbert, "train_relation_model", fake_train)
    assert pipeline.cmd_train_relations(argparse.Namespace()) == 0
    return rel


def test_train_relations_keeps_the_applied_model_when_a_new_one_fails(tmp_path, monkeypatch):
    rel = _train_relations_setup(tmp_path, monkeypatch, precision=0.7)
    assert (rel / "model" / "old.txt").is_file() and not (rel / "model" / "new.txt").exists()
    assert (rel / "model-rejected" / "new.txt").is_file()


def test_train_relations_replaces_the_applied_model_and_keeps_the_previous_one(tmp_path, monkeypatch):
    rel = _train_relations_setup(tmp_path, monkeypatch, precision=0.9)
    assert (rel / "model" / "new.txt").is_file() and not (rel / "model" / "old.txt").exists()
    assert (rel / "model-prev" / "old.txt").is_file()
    assert not (rel / "model-new").exists()


@pytest.mark.parametrize("command", ["relation-fragments", "train-relations"])
def test_relation_commands_are_registered(command):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
