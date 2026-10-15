"""Impact-model labels: how unusual the day-one abnormal return was for that stock.

AR_d = stock daily return − index daily return on consecutive index trading days.
σ = standard deviation of the 60 ARs before the event day; z = day-one AR / σ.
z > 0.5 is POSITIVE, z < −0.5 NEGATIVE, otherwise NEUTRAL (spec §3.2).
"""
from __future__ import annotations

import bisect
import statistics
from datetime import date

import pandas as pd

from ..models import Company
from .prices import BENCHMARK, PriceCache


def daily_abnormal(stock: pd.Series, index: pd.Series) -> pd.Series:
    """AR per index trading day whose own and previous-day stock closes both exist (a halt drops two days)."""
    calendar = sorted(index.index)
    days, values = [], []
    for prev, day in zip(calendar, calendar[1:]):
        if prev not in stock.index or day not in stock.index:
            continue
        s_prev, s_day = float(stock[prev]), float(stock[day])
        i_prev, i_day = float(index[prev]), float(index[day])
        if not s_prev or not i_prev:
            continue
        days.append(day)
        values.append((s_day / s_prev - 1.0) - (i_day / i_prev - 1.0))
    return pd.Series(values, index=days, dtype=float)


class PriceContext:
    """One stock against its market index. Every method returns None when t0 is not an index trading day."""

    def __init__(self, stock: pd.Series, index: pd.Series):
        self.calendar: list[date] = sorted(index.index)
        self.closes = stock
        self.ar = daily_abnormal(stock, index)
        # Plain dicts and a σ cache: the feature table asks for hundreds of thousands of (stock, t0) pairs.
        self._close = {d: float(v) for d, v in stock.items()}
        self._ar = {d: float(v) for d, v in self.ar.items()}
        self._sigma: dict[tuple[date, int, int], float | None] = {}

    def _pos(self, t0: date) -> int | None:
        i = bisect.bisect_left(self.calendar, t0)
        return i if i < len(self.calendar) and self.calendar[i] == t0 else None

    def sigma(self, t0: date, window: int = 60, min_obs: int = 50) -> float | None:
        key = (t0, window, min_obs)
        if key not in self._sigma:
            self._sigma[key] = self._compute_sigma(t0, window, min_obs)
        return self._sigma[key]

    def _compute_sigma(self, t0: date, window: int, min_obs: int) -> float | None:
        i0 = self._pos(t0)
        if i0 is None:
            return None
        values = [self._ar[d] for d in self.calendar[max(0, i0 - window):i0] if d in self._ar]
        if len(values) < min_obs:
            return None
        sd = statistics.stdev(values)
        return sd if sd > 0 else None

    def day_one(self, t0: date) -> float | None:
        return self._ar.get(t0) if self._pos(t0) is not None else None

    def z(self, t0: date) -> float | None:
        ar, sd = self.day_one(t0), self.sigma(t0)
        return None if ar is None or sd is None else ar / sd

    def ret_5d(self, t0: date) -> float | None:
        i0 = self._pos(t0)
        if i0 is None or i0 < 6:
            return None
        base, earlier = self._close.get(self.calendar[i0 - 1]), self._close.get(self.calendar[i0 - 6])
        if base is None or not earlier:
            return None
        return base / earlier - 1.0


def label_for(z: float | None, threshold: float = 0.5) -> str | None:
    if z is None:
        return None
    if z > threshold:
        return "POSITIVE"
    if z < -threshold:
        return "NEGATIVE"
    return "NEUTRAL"


def build_price_contexts(companies: dict[str, Company], prices: PriceCache, start: date, end: date) -> dict[str, PriceContext]:
    """KOSPI/KOSDAQ companies only, each against its own market index; companies without closes are left out."""
    indexes = {market: prices.closes(symbol, start, end) for market, symbol in BENCHMARK.items()}
    contexts = {}
    for key, company in companies.items():
        if company.market not in BENCHMARK:
            continue
        closes = prices.closes(company.ticker, start, end)
        if closes.empty or indexes[company.market].empty:
            continue
        contexts[key] = PriceContext(closes, indexes[company.market])
    return contexts
