"""Company indicators (F8): market cap, size class, PER, PBR and dividend yield.

Size classes follow the exchange's size indices by market-cap rank among common shares of the same market:
KOSPI 1–100 large, 101–300 mid; KOSDAQ (KOSDAQ GLOBAL included) 1–100 large, 101–400 mid; the rest small.
PER and PBR are left empty when net income or equity is not positive (a loss-making company has no PER).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

SIZE_CUTS = {"KOSPI": (100, 300), "KOSDAQ": (100, 400)}


def market_of(listing_market: str) -> str | None:
    if listing_market == "KOSPI":
        return "KOSPI"
    if listing_market.startswith("KOSDAQ"):
        return "KOSDAQ"
    return None


def size_ranks(listing: pd.DataFrame) -> dict[str, tuple[int, str]]:
    """FinanceDataReader KRX listing → stock code → (market-cap rank within its market, size class).
    Preferred shares (codes not ending in 0) are left out of the ranking, as in the size indices."""
    frame = listing.assign(market=listing["Market"].map(market_of))
    frame = frame[frame["market"].notna() & frame["Code"].str.endswith("0") & frame["Marcap"].notna()]
    out = {}
    for market, group in frame.groupby("market"):
        large, mid = SIZE_CUTS[market]
        ranked = group.sort_values("Marcap", ascending=False).reset_index(drop=True)
        for rank, code in enumerate(ranked["Code"], start=1):
            out[code] = (rank, "LARGE" if rank <= large else "MID" if rank <= mid else "SMALL")
    return out


@dataclass(frozen=True)
class Fundamental:
    market_cap: int | None
    market_cap_rank: int | None
    size_class: str | None
    net_income_ttm: int | None
    equity: int | None
    dividend_per_share: int | None
    close_price: float | None
    per: float | None
    pbr: float | None
    dividend_yield: float | None
    fiscal_basis: str


def compute(market_cap: int | None, rank: tuple[int, str] | None, close: float | None, net_income: int | None,
            equity: int | None, dps: int | None, basis: str) -> Fundamental:
    per = round(market_cap / net_income, 4) if market_cap and net_income and net_income > 0 else None
    pbr = round(market_cap / equity, 4) if market_cap and equity and equity > 0 else None
    yield_ = round(dps / close, 6) if dps is not None and close else None
    return Fundamental(market_cap, rank[0] if rank else None, rank[1] if rank else None, net_income, equity, dps, close,
                       per, pbr, yield_, basis)


def compute_from_shares(shares: int | None, close: float | None, net_income: int | None, equity: int | None,
                        dps: int | None, basis: str) -> Fundamental:
    """Without the KRX listing: market cap = issued common shares × close. Rank and size class need the whole market,
    so they stay empty."""
    market_cap = round(shares * close) if shares and close else None
    return compute(market_cap, None, close, net_income, equity, dps, basis)
