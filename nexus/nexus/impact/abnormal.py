"""Event trading day and market-adjusted (abnormal) returns."""
from __future__ import annotations

import bisect
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

from ..models import AbnormalReturn

KST = timezone(timedelta(hours=9))
MARKET_CLOSE = time(15, 30)


def event_trading_date(dateline: datetime, trading_days: list[date]) -> date | None:
    """Before 15:30 KST → first trading day on/after the release date; at/after 15:30 → strictly after."""
    local = dateline.astimezone(KST)
    days = sorted(trading_days)
    if local.time() >= MARKET_CLOSE:
        i = bisect.bisect_right(days, local.date())
    else:
        i = bisect.bisect_left(days, local.date())
    return days[i] if i < len(days) else None


def _window_return(prices: pd.Series, base_day: date, end_day: date) -> float | None:
    if base_day not in prices.index or end_day not in prices.index:
        return None
    base = float(prices[base_day])
    return float(prices[end_day]) / base - 1.0 if base else None


def abnormal_returns(key: str, t0: date, stock: pd.Series, index: pd.Series, benchmark: str,
                     windows: tuple[int, ...] = (1, 3)) -> list[AbnormalReturn]:
    """Base = close of the trading day before t0; end = close of the n-th trading day counting t0 as 1."""
    calendar = sorted(index.index)
    if t0 not in calendar:
        return []
    i0 = calendar.index(t0)
    if i0 == 0:
        return []
    base_day = calendar[i0 - 1]
    results = []
    for n in windows:
        if i0 + n - 1 >= len(calendar):
            continue
        end_day = calendar[i0 + n - 1]
        r_stock, r_index = _window_return(stock, base_day, end_day), _window_return(index, base_day, end_day)
        if r_stock is None or r_index is None:
            continue
        results.append(AbnormalReturn(key, n, benchmark, r_stock, r_index, r_stock - r_index))
    return results
