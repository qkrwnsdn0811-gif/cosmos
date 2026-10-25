"""Write disclosed ownership (V16 company_ownership). The table is replaced wholesale so `nexus ownership` can rerun."""
from __future__ import annotations

from uuid import UUID

from ..fundamentals.ownership import Ownership


def write_ownership(conn, rows: list[Ownership], company_ids: dict[str, UUID]) -> int:
    rows = [r for r in rows if r.investor in company_ids and r.investee in company_ids]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM company_ownership")
        for r in rows:
            cur.execute("INSERT INTO company_ownership (investor_company_id, investee_company_id, stake_pct, purpose, "
                        "settled_on, receipt_no) VALUES (%s, %s, %s, %s, %s, %s)",
                        (company_ids[r.investor], company_ids[r.investee], round(r.stake, 4), r.purpose or None,
                         r.settled or None, r.receipt or None))
    return len(rows)


def read_ownership(conn, company_keys: dict[UUID, str]) -> list[Ownership]:
    """Stored ownership back as company keys (publish-graph puts it into the galaxy snapshot)."""
    rows = conn.execute("SELECT investor_company_id, investee_company_id, stake_pct, purpose, settled_on, receipt_no "
                        "FROM company_ownership").fetchall()
    return [Ownership(company_keys[a], company_keys[b], float(stake), purpose or "", str(settled or ""), receipt or "")
            for a, b, stake, purpose, settled, receipt in rows if a in company_keys and b in company_keys]
