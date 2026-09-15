"""Knowledge graph step 1 — same-industry (sector) edges from the company→industry assignment.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_sector_edges.py [--out data/edges_sector.csv]

Edge schema (지식그래프 설계 6장 확정안):
  src_ticker, dst_ticker, edge_type, weight, as_of_date, evidence
Sector edges are undirected; each pair is stored once with src < dst (string order).
Weight: 1.0 when both companies share a primary industry. The 지주회사 industry produces no edges
(holding companies connect through ownership edges, not through "same sector").
"""
import argparse
import csv
import datetime as dt
import itertools
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
NER_DATA = HERE.parent / "ner" / "data"
NO_EDGE_INDUSTRIES = {"HOLDING"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company-industry", default=NER_DATA / "company_industry.csv", type=Path)
    ap.add_argument("--out", default=HERE / "data" / "edges_sector.csv", type=Path)
    ap.add_argument("--as-of", default=dt.date.today().isoformat())
    args = ap.parse_args()

    with Path(args.company_industry).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    by_ind = defaultdict(list)
    for r in rows:
        if r["industry_id"] and r["industry_id"] not in NO_EDGE_INDUSTRIES:
            by_ind[r["industry_id"]].append(r["ticker"])

    edges = []
    for ind, tickers in sorted(by_ind.items()):
        for a, b in itertools.combinations(sorted(tickers), 2):
            edges.append({"src_ticker": a, "dst_ticker": b, "edge_type": "sector", "weight": 1.0,
                          "as_of_date": args.as_of, "evidence": f"industry:{ind}"})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["src_ticker", "dst_ticker", "edge_type", "weight", "as_of_date", "evidence"])
        w.writeheader()
        w.writerows(edges)
    print(f"companies={len(rows)} industries_with_edges={len(by_ind)} edges={len(edges)} -> {args.out}")
    for ind, tickers in sorted(by_ind.items(), key=lambda x: -len(x[1])):
        n = len(tickers)
        print(f"  {ind:9} {n:3} companies -> {n * (n - 1) // 2:4} edges")


if __name__ == "__main__":
    main()
