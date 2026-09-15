"""Knowledge graph step 3 — ownership edges (확정 스키마) from the parsed DART shareholder table.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_ownership_edges.py [--raw data/ownership_raw.csv] [--out data/edges_ownership.csv]

Input : ownership_raw.csv from extract_ownership.py (one row per shareholder line of the latest 사업보고서)
Output: src_ticker (holder) -> dst_ticker (filer), edge_type=ownership, weight=share_pct/100, as_of_date, evidence
Only rows where BOTH sides are companies in our universe become edges; the rest (people, funds, unlisted
holdings) are kept in ownership_unlinked.csv for review / future node types.
Directed: 삼성생명 --ownership 0.0851--> 삼성전자. Self-holdings (treasury shares) are dropped.
"""
import argparse
import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=HERE / "data" / "ownership_raw.csv", type=Path)
    ap.add_argument("--out", default=HERE / "data" / "edges_ownership.csv", type=Path)
    ap.add_argument("--min-pct", type=float, default=1.0, help="ignore holdings below this percent")
    args = ap.parse_args()

    with args.raw.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    edges, unlinked, skipped_self = {}, [], 0
    for r in rows:
        pct = float(r["share_pct_end"] or 0)
        src, dst = r["holder_ticker"].strip(), r["filer_ticker"].strip()
        if not src or not dst:
            unlinked.append(r)
            continue
        if src == dst:
            skipped_self += 1
            continue
        if pct < args.min_pct:
            continue
        key = (src, dst)
        # the same holder can appear on several lines (보통주 rows for 본인 / 특별계정): sum them
        if key in edges:
            edges[key]["weight"] = round(edges[key]["weight"] + pct / 100, 6)
            edges[key]["evidence"] += f";pct+={pct}"
        else:
            edges[key] = {"src_ticker": src, "dst_ticker": dst, "edge_type": "ownership", "weight": round(pct / 100, 6),
                          "as_of_date": r["as_of_date"] or "", "evidence": f"dart:{r['rcept_no']};{r['relation']};pct={pct}"}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["src_ticker", "dst_ticker", "edge_type", "weight", "as_of_date", "evidence"])
        w.writeheader()
        w.writerows(sorted(edges.values(), key=lambda e: -e["weight"]))
    unl_path = args.out.with_name("ownership_unlinked.csv")
    with unl_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(unlinked)
    print(f"raw_rows={len(rows)} ownership_edges={len(edges)} self_skipped={skipped_self} unlinked_rows={len(unlinked)} -> {args.out}")


if __name__ == "__main__":
    main()
