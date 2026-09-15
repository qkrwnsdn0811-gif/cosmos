"""Knowledge graph step 3 (first cut) — ownership edges from DART annual reports on HDFS.

Runs on the additional EC2 with plain python3 (no Spark, no pip):
  python3 extract_ownership.py --root /datasets/opendart/imports/20260909-dart-b7d53ef432b6/data \
                               --root /datasets/opendart/snapshots/20260909-133530-kospi-rank51-100/data \
                               --companies companies.csv --out ownership_raw.csv

For every company file (<corp_code>_detail.jsonl.gz) it takes the latest 사업보고서 and parses the table
"최대주주 및 그 특수관계인의 주식소유 현황": rows of
    성명 | 관계 | 주식종류 | 기초 주식수 | 기초 지분율 | 기말 주식수 | 기말 지분율 | 비고
The text extraction keeps this table intact because every cell is filled. (The "계열회사간 출자현황" matrix
is NOT usable from text — empty cells are dropped, so columns cannot be aligned; that one needs the raw XML.)

Output rows (one per shareholder line, 보통주 only):
    filer_corp_code, filer_name, filer_ticker, holder_name, holder_ticker, relation, share_pct_end, as_of_date, rcept_no
holder_ticker is filled when the holder name matches one of our companies (after stripping ㈜/(주)/주식회사),
so downstream can turn it into an edge  holder --ownership(weight=pct/100)--> filer.
"""
import argparse
import csv
import gzip
import io
import json
import re
import subprocess
import sys

HDFS = "/opt/hadoop/bin/hdfs"
RELATIONS = ("최대주주 본인", "최대주주의 특수관계인", "특수관계인", "계열회사 임원", "계열회사", "출연 재단", "임원",
             "최대주주", "본인", "친인척", "발행회사 임원", "우리사주조합", "자기주식")
NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")


def hdfs_ls(root):
    out = subprocess.run([HDFS, "dfs", "-ls", "-R", root], capture_output=True, text=True).stdout
    return [line.split()[-1] for line in out.splitlines() if line.strip().endswith("_detail.jsonl.gz")]


def hdfs_cat_gz_lines(path):
    p = subprocess.Popen([HDFS, "dfs", "-cat", path], stdout=subprocess.PIPE)
    with gzip.open(io.BufferedReader(p.stdout), "rt", encoding="utf-8") as f:
        for line in f:
            yield line
    p.wait()


def norm_name(s):
    s = re.sub(r"\(주\)|㈜|주식회사|\(株\)|株式會社", "", s)
    return re.sub(r"\s+", "", s).strip()


def parse_holders(text):
    """Return (as_of_date, rows) from the 최대주주 table; rows are dicts."""
    i = text.find("최대주주 및 그 특수관계인의 주식소유 현황")
    if i < 0:
        i = text.find("최대주주 및 특수관계인의 주식소유 현황")
    if i < 0:
        return None, []
    seg = text[i:i + 20000]
    m = re.search(r"기준일\s*:?\s*\|?\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", seg)
    as_of = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
    cells = [c.strip() for c in re.split(r"\s*\|\s*|\n", seg) if c.strip()]
    rows = []
    j = 0
    while j < len(cells) - 6:
        name, rel, kind = cells[j], cells[j + 1], cells[j + 2]
        if kind in ("보통주", "우선주", "의결권 있는 주식", "의결권있는 주식") and any(rel.startswith(r) or rel == r for r in RELATIONS):
            nums = cells[j + 3:j + 7]
            if all(NUM.match(n.replace("△", "-")) for n in nums):
                try:
                    pct_end = float(nums[3].replace(",", ""))
                except ValueError:
                    pct_end = None
                rows.append({"holder_name": name, "relation": rel, "share_kind": kind, "share_pct_end": pct_end})
                j += 7
                continue
        if name.startswith("계") and rel in ("보통주", "우선주", "-"):
            break  # 계 (total) row ends the table
        j += 1
    return as_of, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True)
    ap.add_argument("--companies", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    with open(args.companies, encoding="utf-8-sig", newline="") as f:
        comps = list(csv.DictReader(f))
    by_code = {}
    by_norm = {}
    for c in comps:
        code = (c.get("dart_corp_code") or "").replace(".0", "").zfill(8)
        if code != "00000000":
            by_code[code] = c
        by_norm[norm_name(c["name_official"])] = c["ticker"]
    # extra spellings seen in DART tables
    extra = {"삼성생명보험": "032830", "삼성화재해상보험": "000810", "삼성에스디아이": "006400", "에스케이": "034730",
             "에스케이하이닉스": "000660", "엘지": "003550", "엘지전자": "066570", "엘지화학": "051910",
             "현대자동차": "005380", "포스코홀딩스": "005490", "한국전력공사": "015760", "케이티": "030200",
             "한화에어로스페이스": "012450", "에이치디현대": "267250", "HD현대": "267250", "지에스": "078930",
             "엘에스": "006260", "두산에너빌리티": "034020", "현대모비스": "012330", "삼성물산": "028260",
             "삼성전기": "009150", "삼성에스디에스": "018260", "삼성카드": "029780", "삼성증권": "016360",
             "한화": "000880", "한화솔루션": "009830", "한화생명보험": "088350", "한화시스템": "272210",
             "카카오": "035720", "네이버": "035420", "에스케이스퀘어": "402340", "에스케이텔레콤": "017670",
             "에스케이이노베이션": "096770", "엘지에너지솔루션": "373220", "엘지이노텍": "011070", "엘지유플러스": "032640"}
    by_norm.update({norm_name(k): v for k, v in extra.items()})

    files = []
    for root in args.root:
        files += hdfs_ls(root)
    if args.limit:
        files = files[: args.limit]
    print(f"files={len(files)}", file=sys.stderr)

    by_ticker = {c["ticker"]: c for c in comps}

    def code_of(path):
        base = path.rsplit("/", 1)[-1]
        m = re.match(r"(\d{8})_detail", base)                 # imports: <corp_code>_detail.jsonl.gz
        if m:
            return m.group(1)
        m = re.match(r"\d{3}_(\d{6})_.*_detail", base)        # snapshot: <rank>_<ticker>_<name>_detail.jsonl.gz
        if m and m.group(1) in by_ticker:
            return (by_ticker[m.group(1)].get("dart_corp_code") or "").replace(".0", "").zfill(8)
        return "ticker:" + (m.group(1) if m else base)

    # latest annual report per company across all files (imports split one company into before/after 2016)
    latest_by_code = {}
    for path in files:
        code = code_of(path)
        for line in hdfs_cat_gz_lines(path):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            nm = r.get("report_nm", "").strip()
            if nm.startswith("사업보고서") and (code not in latest_by_code or r["rcept_dt"] > latest_by_code[code]["rcept_dt"]):
                latest_by_code[code] = r
        print(f"scanned {path.rsplit('/', 1)[-1]} -> code {code}", file=sys.stderr)

    out_rows, seen_filers, no_table = [], set(), []
    for code in sorted(latest_by_code):
        latest = latest_by_code[code]
        text = "\n".join(f.get("text", "") for f in latest.get("files", []))
        text = re.sub(r"[ \t]+", " ", text)
        as_of, rows = parse_holders(text)
        if not rows:
            no_table.append((code, "table not parsed"))
            continue
        filer = by_code.get(code, {})
        seen_filers.add(code)
        for row in rows:
            if row["share_kind"] != "보통주" or row["share_pct_end"] in (None, 0.0):
                continue
            out_rows.append({
                "filer_corp_code": code, "filer_name": latest.get("corp_name", filer.get("name_official", "")),
                "filer_ticker": filer.get("ticker", ""), "holder_name": row["holder_name"],
                "holder_ticker": by_norm.get(norm_name(row["holder_name"]), ""), "relation": row["relation"],
                "share_pct_end": row["share_pct_end"], "as_of_date": as_of, "rcept_no": latest["rcept_no"],
            })
        print(f"{code} {latest.get('corp_name','')}: {len(rows)} holder rows, as_of={as_of}", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["filer_corp_code", "filer_name", "filer_ticker", "holder_name", "holder_ticker",
                                          "relation", "share_pct_end", "as_of_date", "rcept_no"])
        w.writeheader()
        w.writerows(out_rows)
    linked = sum(1 for r in out_rows if r["holder_ticker"])
    print(json.dumps({"files": len(files), "companies_with_annual_report": len(latest_by_code), "filers_parsed": len(seen_filers),
                      "holder_rows": len(out_rows), "rows_linked_to_our_tickers": linked, "failed": no_table, "out": args.out},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
