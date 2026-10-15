import statistics
from datetime import date, timedelta

import pandas as pd
import pytest

from nexus.impact.abnormal import abnormal_returns
from nexus.impact.labels import PriceContext, build_price_contexts, daily_abnormal, label_for
from nexus.impact.prices import PriceCache
from nexus.models import Company


def from_returns(returns, start=date(2025, 1, 1)):
    days = [start + timedelta(days=i) for i in range(len(returns) + 1)]
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    return pd.Series(closes, index=days), pd.Series(1000.0, index=days), days


def test_daily_abnormal_subtracts_index():
    d = [date(2026, 1, i) for i in (1, 2, 3)]
    ar = daily_abnormal(pd.Series([100.0, 110.0, 99.0], index=d), pd.Series([1000.0, 1000.0, 1100.0], index=d))
    assert list(ar.index) == d[1:]
    assert ar[d[1]] == pytest.approx(0.10)
    assert ar[d[2]] == pytest.approx(-0.20)


def test_halted_day_removes_that_day_and_the_next():
    d = [date(2026, 1, i) for i in (1, 2, 3, 4)]
    stock = pd.Series([100.0, 101.0, 102.0], index=[d[0], d[2], d[3]])
    assert list(daily_abnormal(stock, pd.Series(1000.0, index=d)).index) == [d[3]]


def test_sigma_z_and_label():
    returns = [0.01 if i % 2 else -0.01 for i in range(61)]
    stock, index, days = from_returns(returns + [0.03])
    ctx, t0 = PriceContext(stock, index), days[-1]
    sigma = statistics.stdev(returns[-60:])
    assert ctx.sigma(t0) == pytest.approx(sigma)
    assert ctx.day_one(t0) == pytest.approx(0.03)
    assert ctx.z(t0) == pytest.approx(0.03 / sigma)
    assert label_for(ctx.z(t0)) == "POSITIVE"


def test_sigma_needs_fifty_observations():
    stock, index, days = from_returns([0.01, -0.01] * 22 + [0.02])   # 45 returns
    assert PriceContext(stock, index).sigma(days[-1]) is None
    assert PriceContext(stock, index).z(days[-1]) is None


def test_label_boundaries():
    assert [label_for(z) for z in (0.51, 0.5, -0.5, -0.51, None)] == ["POSITIVE", "NEUTRAL", "NEUTRAL", "NEGATIVE", None]


def test_ret_5d_uses_closes_before_t0():
    days = [date(2026, 2, 1) + timedelta(days=i) for i in range(12)]
    stock = pd.Series([100.0 + i for i in range(12)], index=days)
    ctx = PriceContext(stock, pd.Series(1000.0, index=days))
    assert ctx.ret_5d(days[10]) == pytest.approx(109.0 / 104.0 - 1)
    assert ctx.ret_5d(days[3]) is None


def test_day_one_matches_existing_abnormal_returns():
    stock, index, days = from_returns([0.01, -0.02, 0.03])
    index = index * pd.Series([1.0, 1.01, 1.0, 0.98], index=days)
    expected = abnormal_returns("K", days[2], stock, index, "KOSPI", windows=(1,))[0].abnormal_return
    assert PriceContext(stock, index).day_one(days[2]) == pytest.approx(expected)


def test_unknown_t0_gives_none():
    stock, index, _ = from_returns([0.01] * 70)
    ctx = PriceContext(stock, index)
    assert (ctx.sigma(date(2030, 1, 1)), ctx.day_one(date(2030, 1, 1)), ctx.ret_5d(date(2030, 1, 1))) == (None, None, None)


def test_build_price_contexts_skips_nasdaq_and_uses_market_index(tmp_path):
    days = pd.to_datetime([date(2026, 1, 1) + timedelta(days=i) for i in range(5)])
    calls = []

    def fetch(symbol, start, end):
        calls.append(symbol)
        return pd.DataFrame({"Close": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=days)

    companies = {c.key: c for c in (Company("KOSPI:1", "KOSPI", "1", "가", "SEMICONDUCTOR", "제조"),
                                    Company("KOSDAQ:2", "KOSDAQ", "2", "나", "SEMICONDUCTOR", "장비"),
                                    Company("NASDAQ:NV", "NASDAQ", "NV", "다", "SEMICONDUCTOR", "설계"))}
    cache = PriceCache(tmp_path, fetch=fetch, today=lambda: date(2026, 2, 1))
    ctx = build_price_contexts(companies, cache, date(2026, 1, 1), date(2026, 1, 31))
    assert set(ctx) == {"KOSPI:1", "KOSDAQ:2"}
    assert {"KS11", "KQ11", "1", "2"} <= set(calls) and "NV" not in calls
