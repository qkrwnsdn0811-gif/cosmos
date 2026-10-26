"""Company indicators without the KRX listing (`nexus fundamentals-dart`).

Net income, equity and dividend come from OpenDART as in `nexus fundamentals`; the market cap is the issued common
shares (OpenDART, same report as the equity figure) × the last daily close already in the database. Nothing is
scraped, so market-cap rank and size class stay empty.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from .dart import ANNUAL, interim_candidates, trailing_net_income
from .metrics import Fundamental, compute_from_shares


@dataclass(frozen=True)
class ListedClose:
    company_id: UUID
    name: str
    stock_code: str
    close: float
    price_date: date


def dart_fundamentals(listed: list[ListedClose], dart, corp_codes: dict[str, str],
                      today: date) -> dict[UUID, Fundamental]:
    """One row per listed company. Companies without a DART corp code, or whose DART calls fail, keep only the close."""
    rows = {}
    for c in listed:
        net_income = equity = dps = shares = None
        basis = ""
        code = corp_codes.get(c.stock_code)
        if code:
            try:
                annual = dart.statement(code, today.year - 1, ANNUAL)
                interim = next((s for y, r in interim_candidates(today) if (s := dart.statement(code, y, r))), None)
                net_income, basis = trailing_net_income(annual, interim)
                latest = interim or annual
                if latest:
                    equity = latest.equity
                    shares = dart.shares(code, latest.year, latest.report)
                dps = dart.dividend(code, today.year - 1)
            except (OSError, ValueError) as e:
                # requests errors quote the URL, and the URL carries the key (crtfc_key): print the type only.
                print(f"  {c.name}: OpenDART failed ({type(e).__name__})", flush=True)
                net_income = equity = dps = shares = None
                basis = ""
        rows[c.company_id] = compute_from_shares(shares, c.close, net_income, equity, dps, basis)
    return rows
