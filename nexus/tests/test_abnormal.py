from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from nexus.impact.abnormal import abnormal_returns, event_trading_date
from nexus.impact.prices import PriceCache

KST = timezone(timedelta(hours=9))
DAYS = [date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19)]


def series(values, days=DAYS):
    return pd.Series(values, index=days[:len(values)], dtype=float)


def test_before_close_same_day():
    assert event_trading_date(datetime(2026, 10, 13, 15, 29, tzinfo=KST), DAYS) == date(2026, 10, 13)


def test_after_close_next_day():
    assert event_trading_date(datetime(2026, 10, 13, 15, 30, tzinfo=KST), DAYS) == date(2026, 10, 14)
    utc_evening = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)   # 16:00 KST
    assert event_trading_date(utc_evening, DAYS) == date(2026, 10, 14)


def test_weekend_next_trading_day():
    assert event_trading_date(datetime(2026, 10, 17, 10, 0, tzinfo=KST), DAYS) == date(2026, 10, 19)
    assert event_trading_date(datetime(2026, 10, 19, 16, 0, tzinfo=KST), DAYS) is None


def test_ar_window_1_and_3():
    stock = series([100, 110, 121, 121])
    index = series([1000, 1010, 1020, 1030])
    result = {r.window_days: r for r in abnormal_returns("KOSPI:1", date(2026, 10, 13), stock, index, "KOSPI")}
    assert result[1].stock_return == pytest.approx(0.10)
    assert result[1].benchmark_return == pytest.approx(0.01)
    assert result[1].abnormal_return == pytest.approx(0.09)
    assert result[3].abnormal_return == pytest.approx(0.18)
    assert result[3].benchmark == "KOSPI" and result[3].key == "KOSPI:1"


def test_future_window_skipped():
    stock = series([100, 110, 121, 121, 120, 130])
    index = series([1000, 1010, 1020, 1030, 1040, 1050])
    result = abnormal_returns("KOSPI:1", date(2026, 10, 19), stock, index, "KOSPI")
    assert [r.window_days for r in result] == [1]


def test_price_cache_fetches_missing_only(tmp_path):
    calls = []

    def fetch(symbol, start, end):
        calls.append((symbol, start, end))
        days = [d for d in DAYS if start <= d <= end]
        return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [float(d.day) for d in days],
                             "Volume": 10}, index=pd.to_datetime(days))

    cache = PriceCache(tmp_path, fetch=fetch)
    cache.closes("KS11", date(2026, 10, 12), date(2026, 10, 14))
    closes = cache.closes("KS11", date(2026, 10, 12), date(2026, 10, 16))
    assert calls == [("KS11", date(2026, 10, 12), date(2026, 10, 14)), ("KS11", date(2026, 10, 15), date(2026, 10, 16))]
    assert list(closes.index) == DAYS[:5]
    assert closes[date(2026, 10, 16)] == 16.0
