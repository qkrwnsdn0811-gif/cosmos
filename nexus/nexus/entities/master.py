"""Value-chain company master: hand-curated seed names resolved against listings."""
from __future__ import annotations

import pandas as pd

from ..models import Company

ALIAS_COLUMNS = ["alias", "ticker", "name_official", "market", "alias_type", "ambiguity", "group_id", "source", "note"]


def _krx_market(raw: str) -> str | None:
    raw = (raw or "").upper()
    if raw.startswith("KOSDAQ"):
        return "KOSDAQ"
    if raw == "KOSPI":
        return "KOSPI"
    return None


def build_master(seed_rows: list[dict], listing: pd.DataFrame, cosmos_companies: list[dict],
                 value_chain: str, markets: tuple[str, ...] | None = None) -> tuple[list[Company], list[str]]:
    """Exact-name resolution: KRX names against `listing`, NASDAQ names against COSMOS companies.csv.
    With `markets`, seeds hinted outside them are skipped (not reported as unresolved) and resolved
    companies outside them are dropped — the service is limited to domestic listings."""
    krx = {row["Name"]: row for row in listing.to_dict("records")}
    nasdaq = {c["name_official"]: c for c in cosmos_companies if c.get("market") == "NASDAQ"}
    companies, unresolved = [], []
    for seed in seed_rows:
        name = seed["name"].strip()
        hint = seed.get("market_hint")
        if markets is not None and hint in ("KOSPI", "KOSDAQ", "NASDAQ") and hint not in markets:
            continue
        if hint == "NASDAQ":
            hit = nasdaq.get(name)
            market, ticker = ("NASDAQ", hit["ticker"]) if hit else (None, None)
        else:
            hit = krx.get(name)
            market, ticker = (_krx_market(hit["Market"]), str(hit["Code"]).zfill(6)) if hit else (None, None)
        if not market:
            unresolved.append(name)
            continue
        if markets is not None and market not in markets:
            continue
        companies.append(Company(f"{market}:{ticker}", market, ticker, name, value_chain, seed["role"]))
    return companies, unresolved


def _alias(alias: str, company: Company, alias_type: str, source: str) -> dict:
    return {"alias": alias, "ticker": company.key, "name_official": company.name, "market": company.market,
            "alias_type": alias_type, "ambiguity": "safe", "group_id": "", "source": source, "note": ""}


def alias_rows(companies: list[Company], seed_rows: list[dict], cosmos_alias_rows: list[dict]) -> list[dict]:
    """Rows in COSMOS aliases.csv shape; the ticker column carries the company key."""
    by_name = {c.name: c for c in companies}
    by_code = {(c.market, c.ticker): c for c in companies}
    rows, seen = [], set()

    def add(row):
        if row["alias"] and row["alias"] not in seen:
            seen.add(row["alias"])
            rows.append(row)

    for c in companies:
        add(_alias(c.name, c, "official", "nexus"))
    for seed in seed_rows:
        company = by_name.get(seed["name"].strip())
        if company:
            for alias in filter(None, (a.strip() for a in (seed.get("aliases") or "").split("|"))):
                add(_alias(alias, company, "ko_manual", "nexus_seed"))
    for r in cosmos_alias_rows:
        company = by_code.get((r.get("market"), r.get("ticker")))
        if company and r.get("ambiguity") == "safe":
            add(_alias(r["alias"].strip(), company, r.get("alias_type") or "cosmos", "cosmos"))
    return rows
