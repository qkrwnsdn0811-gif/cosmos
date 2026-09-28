"""Emit PostgreSQL-ready seed CSVs for the team DB from the AI-side source files.

    PYTHONUTF8=1 .venv/Scripts/python.exe build_db_seed.py            # -> data/db/*.csv

The DB generates every id (``UUID PRIMARY KEY DEFAULT gen_random_uuid()``), so these files carry
**natural keys** instead and the loader resolves them:

    company           (market, stock_code)   -- uk_company_market_stock_code
    industry          (name)                 -- uk_industry_name
    company_industry  both of the above
    company_alias     (market, stock_code) + normalized_name + alias_type

Output files map 1:1 onto tables in BackEnd/src/main/resources/db/migration/V1__create_initial_schema.sql:

    industry.csv          name, description, parent_name          (parent_name empty = top level)
    company.csv           name, name_en, stock_code, market, description
    company_industry.csv  market, stock_code, industry_name, is_primary
    company_alias.csv     market, stock_code, alias_name, alias_type, normalized_name

`normalized_name` is produced by linker.normalize() so the DB stores exactly the form the matcher
compares against (㈜/漢字/spaces/punctuation stripped, casefolded).

Industry taxonomy is the team-agreed 18 (2026-09-11). All 18 are top level: `industry` has
`parent_industry_id`, so a coarser display grouping can be added later as data, without a migration.
"""
from __future__ import annotations

import csv
from pathlib import Path

from linker import normalize

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "db"

# company_alias holds names that resolve to one company; industry phrases and the exclusion
# list are matcher-internal and have no company to attach to.
ALIAS_TYPES_SKIP = {"industry", "blocker"}


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(name: str, fields: list[str], rows: list[dict]) -> None:
    p = OUT / name
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  {name:24} {len(rows):>6} rows")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    companies = read(HERE / "data" / "companies.csv")
    industries = read(HERE / "data" / "seeds" / "industries.csv")
    assigned = read(HERE / "data" / "company_industry.csv")
    aliases = read(HERE / "data" / "aliases.csv")
    by_ticker = {c["ticker"]: c for c in companies}
    ind_ko = {i["industry_id"]: i["name_ko"] for i in industries}

    errors = []

    # --- industry ---------------------------------------------------------
    ind_rows = [{"name": i["name_ko"], "description": i["description"], "parent_name": ""} for i in industries]
    if len({r["name"] for r in ind_rows}) != len(ind_rows):
        errors.append("industry name is not unique (uk_industry_name would fail)")
    for r in ind_rows:
        if len(r["name"]) > 100:
            errors.append(f"industry name over VARCHAR(100): {r['name']}")

    # --- company ----------------------------------------------------------
    co_rows = []
    for c in companies:
        co_rows.append({"name": c["name_official"], "name_en": c["name_en"],
                        "stock_code": c["ticker"], "market": c["market"],
                        "description": (c.get("main_product") or "").strip()})
        if len(c["name_official"]) > 200 or len(c["name_en"] or "") > 200:
            errors.append(f"company name over VARCHAR(200): {c['ticker']}")
        if len(c["ticker"]) > 30:
            errors.append(f"stock_code over VARCHAR(30): {c['ticker']}")
    if len({(r["market"], r["stock_code"]) for r in co_rows}) != len(co_rows):
        errors.append("(market, stock_code) is not unique (uk_company_market_stock_code would fail)")

    # --- company_industry -------------------------------------------------
    ci_rows = []
    for a in assigned:
        if a["ticker"] not in by_ticker:
            errors.append(f"company_industry: unknown ticker {a['ticker']}")
            continue
        if a["industry_id"] not in ind_ko:
            errors.append(f"company_industry: unknown industry_id {a['industry_id']}")
            continue
        ci_rows.append({"market": a["market"], "stock_code": a["ticker"],
                        "industry_name": ind_ko[a["industry_id"]], "is_primary": "true"})
    # build_industries.py assigns exactly one industry per company, so every row is the primary one
    if len({(r["market"], r["stock_code"]) for r in ci_rows}) != len(ci_rows):
        errors.append("a company is assigned more than one industry; is_primary must not be true for all")

    # --- company_alias ----------------------------------------------------
    al_rows, seen = [], set()
    for a in aliases:
        if a["alias_type"] in ALIAS_TYPES_SKIP or not a["ticker"]:
            continue
        c = by_ticker.get(a["ticker"])
        if not c:
            errors.append(f"company_alias: unknown ticker {a['ticker']} for {a['alias']!r}")
            continue
        norm = normalize(a["alias"])
        if not norm:
            continue
        key = (c["market"], a["ticker"], norm, a["alias_type"])
        if key in seen:  # uk_company_alias_normalized
            continue
        seen.add(key)
        al_rows.append({"market": c["market"], "stock_code": a["ticker"], "alias_name": a["alias"],
                        "alias_type": a["alias_type"], "normalized_name": norm})
        if len(a["alias"]) > 200 or len(norm) > 200:
            errors.append(f"alias over VARCHAR(200): {a['alias']!r}")

    if errors:
        for e in errors:
            print("ERROR", e)
        raise SystemExit(1)

    print(f"-> {OUT}")
    write("industry.csv", ["name", "description", "parent_name"], ind_rows)
    write("company.csv", ["name", "name_en", "stock_code", "market", "description"], co_rows)
    write("company_industry.csv", ["market", "stock_code", "industry_name", "is_primary"], ci_rows)
    write("company_alias.csv", ["market", "stock_code", "alias_name", "alias_type", "normalized_name"], al_rows)


if __name__ == "__main__":
    main()
