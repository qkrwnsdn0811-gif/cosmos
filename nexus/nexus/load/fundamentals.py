"""Write company indicators (V12 company_fundamental), replacing each company's row."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from ..fundamentals.metrics import Fundamental

SQL = (
    "INSERT INTO company_fundamental (company_id, market_cap, market_cap_rank, size_class, net_income_ttm, equity, "
    "dividend_per_share, close_price, per, pbr, dividend_yield, fiscal_basis, price_date, source, updated_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP) "
    "ON CONFLICT (company_id) DO UPDATE SET market_cap = EXCLUDED.market_cap, market_cap_rank = EXCLUDED.market_cap_rank, "
    "size_class = EXCLUDED.size_class, net_income_ttm = EXCLUDED.net_income_ttm, equity = EXCLUDED.equity, "
    "dividend_per_share = EXCLUDED.dividend_per_share, close_price = EXCLUDED.close_price, per = EXCLUDED.per, "
    "pbr = EXCLUDED.pbr, dividend_yield = EXCLUDED.dividend_yield, fiscal_basis = EXCLUDED.fiscal_basis, "
    "price_date = EXCLUDED.price_date, source = EXCLUDED.source, updated_at = CURRENT_TIMESTAMP")


def write_fundamentals(conn, rows: dict[UUID, Fundamental], *, price_date: date, source: str) -> int:
    with conn.transaction(), conn.cursor() as cur:
        for company_id, f in rows.items():
            cur.execute(SQL, (company_id, f.market_cap, f.market_cap_rank, f.size_class, f.net_income_ttm, f.equity,
                              f.dividend_per_share, f.close_price, f.per, f.pbr, f.dividend_yield,
                              f.fiscal_basis or None, price_date, source))
    return len(rows)
