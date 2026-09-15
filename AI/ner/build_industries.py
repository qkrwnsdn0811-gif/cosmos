"""Validate the industry taxonomy seeds and emit data/company_industry.csv keyed by ticker.

  PYTHONUTF8=1 .venv/Scripts/python.exe build_industries.py

Checks: every company in companies.csv has exactly one industry; every industry_id exists; no unknown keys.
"""
import csv
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(p):
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    companies = read(HERE / "data" / "companies.csv")
    industries = {r["industry_id"]: r for r in read(HERE / "data" / "seeds" / "industries.csv")}
    mapping = read(HERE / "data" / "seeds" / "company_industry.csv")

    by_name = {c["name_official"]: c for c in companies}
    by_ticker = {c["ticker"]: c for c in companies}
    assigned: dict[str, str] = {}
    errors = []
    for r in mapping:
        key, ind = r["key"].strip(), r["industry_id"].strip()
        c = by_name.get(key) or by_ticker.get(key)
        if not c:
            errors.append(f"unknown company key: {key}")
            continue
        if ind not in industries:
            errors.append(f"unknown industry_id {ind} for {key}")
            continue
        if c["ticker"] in assigned:
            errors.append(f"duplicate assignment for {key}")
        assigned[c["ticker"]] = ind
    missing = [c["name_official"] for c in companies if c["ticker"] not in assigned]
    if missing:
        errors.append(f"unassigned companies ({len(missing)}): {missing}")

    if errors:
        # validate before writing: a failed build must not leave a half-assigned CSV behind
        for e in errors:
            print("ERROR", e)
        raise SystemExit(1)

    out = HERE / "data" / "company_industry.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "market", "name_official", "industry_id", "industry_ko"])
        for c in companies:
            ind = assigned.get(c["ticker"], "")
            w.writerow([c["ticker"], c["market"], c["name_official"], ind, industries.get(ind, {}).get("name_ko", "")])

    counts = Counter(assigned.values())
    by_ind = defaultdict(list)
    for t, ind in assigned.items():
        by_ind[ind].append(by_ticker[t]["name_official"])
    print(f"companies={len(companies)} assigned={len(assigned)} industries={len(industries)} -> {out}")
    for ind in industries:
        print(f"  {ind:9} {industries[ind]['name_ko']:<14} {counts[ind]:3}  {', '.join(by_ind[ind][:8])}{' …' if counts[ind] > 8 else ''}")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
