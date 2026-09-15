"""Export dated shareholder snapshots from archived DART annual reports.

Runs beside extract_ownership.py on the HDFS host using stdlib only. Each
report remains separate: a later amendment must never replace an earlier
snapshot before its own receipt date. An empty/failed table is recorded too,
so downstream cannot silently carry an old stake through an unknown report.
Only the existing unambiguous largest-shareholder table parser is reused.
No current sector classification is backdated. Source reports are read-only.
"""
import argparse
import csv
import json
import re
from pathlib import Path

from extract_ownership import hdfs_ls, hdfs_cat_gz_lines, norm_name, parse_holders


def report_snapshot(report, filer, aliases):
    number = str(report.get("rcept_no", ""))
    received = str(report.get("rcept_dt", "")).replace("-", "")
    if not re.fullmatch(r"\d{8}", received) or not re.fullmatch(r"\d{14}", number):
        return None
    # Receipt number and declared receipt date must agree; otherwise quarantine.
    if number[:8] != received:
        return None
    name = report.get("report_nm", "")
    if "사업보고서" not in name or "분기보고서" in name or "반기보고서" in name:
        return None
    text = re.sub(r"[ \t]+", " ", "\n".join(f.get("text", "") for f in report.get("files", [])))
    as_of, holders = parse_holders(text)
    date = f"{received[:4]}-{received[4:6]}-{received[6:8]}"
    if not as_of or as_of > date:
        holders = []
    positions = []
    for h in holders:
        ticker = aliases.get(norm_name(h["holder_name"]))
        pct = h["share_pct_end"]
        if ticker and ticker != filer and h["share_kind"] == "보통주" and pct is not None and 0 < pct <= 100:
            positions.append({"src": ticker, "dst": filer, "weight": pct / 100,
                              "holder_name": h["holder_name"], "share_pct": pct})
    return {"filer": filer, "report_id": number, "published_date": date,
            "as_of_date": as_of, "report_name": name, "parsed": bool(holders), "positions": positions}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True)
    ap.add_argument("--companies", required=True)
    ap.add_argument("--aliases", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with open(args.companies, encoding="utf-8-sig") as f:
        companies = list(csv.DictReader(f))
    codes = {(c.get("dart_corp_code") or "").replace(".0", "").zfill(8): c["ticker"] for c in companies}
    tickers = {c["ticker"] for c in companies}
    candidates = {}
    with open(args.aliases, encoding="utf-8-sig") as f:
        for a in csv.DictReader(f):
            # Accept only aliases that uniquely identify a company in the universe.
            if a.get("ambiguity") != "safe":
                continue
            name = norm_name(a.get("alias", ""))
            if name and a["ticker"] in tickers:
                candidates.setdefault(name, set()).add(a["ticker"])
    for c in companies:
        candidates.setdefault(norm_name(c["name_official"]), set()).add(c["ticker"])
    aliases = {a: next(iter(ts)) for a, ts in candidates.items() if len(ts) == 1}
    paths = sorted({p for root in args.root for p in hdfs_ls(root)})
    seen, count = set(), 0
    with Path(args.out).open("w", encoding="utf8") as out:
        for i, path in enumerate(paths):
            base = path.rsplit("/", 1)[-1]
            m = re.match(r"(\d{8})_detail", base)
            filer = codes.get(m[1]) if m else None
            if filer is None:
                m = re.match(r"\d{3}_(\d{6})_", base)
                filer = m[1] if m and m[1] in tickers else None
            if not filer:
                continue
            for line in hdfs_cat_gz_lines(path):
                r = json.loads(line)
                if "사업보고서" not in r.get("report_nm", ""):
                    continue
                snapshot = report_snapshot(r, filer, aliases)
                if snapshot and snapshot["report_id"] not in seen:
                    seen.add(snapshot["report_id"])
                    snapshot["source_path"] = path
                    out.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
                    count += 1
            out.flush()
            print(f"{i+1}/{len(paths)} {filer}: total reports={count}", flush=True)


if __name__ == "__main__":
    main()
