from datetime import date, datetime, timedelta, timezone

import math
import pandas as pd
import pytest

from nexus.impact.features import (META_COLUMNS, MentionTimeline, after_close, build_frame, category_vocab,
                                   feature_columns, path_sign)
from nexus.impact.labels import PriceContext
from nexus.models import Article, Company, CompanyMention, EventBundle, Impact, PathStep

KST = timezone(timedelta(hours=9))
A = Company("KOSPI:1", "KOSPI", "1", "가전자", "SEMICONDUCTOR", "제조")
B = Company("KOSDAQ:2", "KOSDAQ", "2", "나장비", "SEMICONDUCTOR", "장비")
C = Company("KOSDAQ:3", "KOSDAQ", "3", "다소재", "SEMICONDUCTOR", "소재")
D = Company("KOSDAQ:4", "KOSDAQ", "4", "라부품", "SEMICONDUCTOR", "부품")
N = Company("NASDAQ:NV", "NASDAQ", "NV", "엔비", "SEMICONDUCTOR", "설계")
COMPANIES = {c.key: c for c in (A, B, C, D, N)}


def art(news_id, at, title="제목", content="본문", categories=("경제>반도체",), incidents=()):
    return Article(news_id, title, content, at.date(), at, "테스트일보", "", categories, incidents, "", (), (), ())


def bundle(news_id="E1", at=datetime(2026, 7, 2, 10, 0, tzinfo=KST), t0=date(2026, 7, 2)):
    mentions = [CompanyMention(A.key, 1.0, ("title",)), CompanyMention(B.key, 0.8, ("content",)),
                CompanyMention(N.key, 0.6, ("orgs",))]
    impacts = [Impact(A.key, "REPORTED", 0, (), 1.0, "OK", None),
               Impact(B.key, "REPORTED", 0, (), 0.8, "OK", None),
               Impact(N.key, "REPORTED", 0, (), 0.6, "OK", None),
               Impact(C.key, "INFERRED", 1, (PathStep(C.key, B.key, "COMPETE", "COSMOS", 1.0),), 0.4, "OK", None),
               Impact(D.key, "INFERRED", 1, (PathStep(D.key, A.key, "SUPPLY", "NEWS", 0.5),), 0.1, "HOLD", "전이 강도 낮음")]
    return EventBundle(art(news_id, at, title="가전자 공급 계약"), "SEMICONDUCTOR", mentions, impacts, [], t0)


def frame_for(bundles, prices=None, sentiment=None, calendar=None):
    sentiment = sentiment if sentiment is not None else pd.DataFrame(
        {"p_neg": [0.1], "p_neu": [0.2], "p_pos": [0.7]}, index=pd.Index(["E1"], name="news_id"))
    calendar = calendar or [date(2026, 7, 1), date(2026, 7, 2)]
    return build_frame(bundles, COMPANIES, sentiment, prices or {}, ["경제>반도체"], MentionTimeline(bundles), calendar)


def test_path_sign():
    s = lambda rel: PathStep("x", "y", rel, "COSMOS", 1.0)
    assert path_sign(()) == 1
    assert path_sign((s("SUPPLY"),)) == 1
    assert path_sign((s("COMPETE"),)) == -1
    assert path_sign((s("COMPETE"), s("COMPETE"))) == 1
    assert path_sign((s("SUPPLY"), s("CO_MENTIONED"))) == 0


def test_after_close():
    cal = {date(2026, 7, 2), date(2026, 7, 3)}      # 7/4 토, 7/5 일
    at = lambda d, h, m=0: datetime(2026, 7, d, h, m, tzinfo=KST)
    assert after_close(at(2, 15, 29), cal, date(2026, 7, 3)) == 0
    assert after_close(at(2, 15, 30), cal, date(2026, 7, 3)) == 1
    assert after_close(at(1, 10), cal, date(2026, 7, 3)) == 1          # 달력 안인데 거래일 아님
    assert after_close(at(6, 10), cal, date(2026, 7, 3)) == 0          # 달력 밖 월요일
    assert after_close(at(5, 10), cal, date(2026, 7, 3)) == 1          # 달력 밖 일요일


def test_buzz_ratio_counts_only_before_the_article():
    t = datetime(2026, 7, 20, 12, 0, tzinfo=KST)
    times = [t - timedelta(hours=1), t - timedelta(hours=2), t - timedelta(days=10), t, t + timedelta(hours=1)]
    bundles = [EventBundle(art(f"B{i}", x), "SEMICONDUCTOR", [CompanyMention(A.key, 1.0, ("title",))], [], [], None)
               for i, x in enumerate(times)]
    assert MentionTimeline(bundles).buzz_ratio(A.key, t) == pytest.approx(2 / (3 / 30 + 1))


def test_category_vocab_keeps_frequent_categories():
    at = datetime(2025, 3, 1, 9, tzinfo=KST)
    bundles = [EventBundle(art(f"V{i}", at, categories=("경제>반도체",) + (("IT>보안",) if i == 0 else ())),
                           "SEMICONDUCTOR", [], [], [], None) for i in range(200)]
    assert category_vocab(bundles) == ["경제>반도체"]


def test_rows_columns_and_values():
    raw = frame_for([bundle()])
    assert list(raw.columns) == META_COLUMNS + feature_columns(["경제>반도체"])
    f = raw.set_index("company_key")
    assert set(f.index) == {A.key, B.key, C.key}                       # HOLD·NASDAQ 제외
    assert (f.loc[A.key, "in_title"], f.loc[A.key, "path_sign"], f.loc[A.key, "first_rel"]) == (1, 1, "NONE")
    assert (f.loc[C.key, "is_inferred"], f.loc[C.key, "path_sign"], f.loc[C.key, "edge_source"]) == (1, -1, "COSMOS")
    assert f.loc[C.key, "in_title"] == 0
    assert f.loc[A.key, "n_reported"] == 3 and f.loc[A.key, "rel_supply"] == 1
    assert f.loc[A.key, "sent_pos"] == pytest.approx(0.7) and f.loc[A.key, "cat:경제>반도체"] == 1
    assert isinstance(f["role"].dtype, pd.CategoricalDtype)


def test_missing_sentiment_prices_and_trading_day_give_nan():
    empty = pd.DataFrame(columns=["p_neg", "p_neu", "p_pos"], index=pd.Index([], name="news_id"))
    f = frame_for([bundle(t0=None)], sentiment=empty)
    assert len(f) == 3
    assert f["sent_pos"].isna().all() and f["vol_60"].isna().all() and f["ret_5d"].isna().all()
    assert f["label"].isna().all() and f["z"].isna().all()


def test_label_comes_from_price_context():
    days = [date(2026, 3, 1) + timedelta(days=i) for i in range(124)]
    returns = [0.01 if i % 2 else -0.01 for i in range(122)] + [0.05]
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    ctx = PriceContext(pd.Series(closes, index=days), pd.Series(1000.0, index=days))
    b = bundle(t0=days[-1])
    f = frame_for([b], prices={A.key: ctx}, calendar=days).set_index("company_key")
    assert f.loc[A.key, "label"] == "POSITIVE"
    assert not math.isnan(f.loc[A.key, "vol_60"]) and pd.isna(f.loc[B.key, "label"])
