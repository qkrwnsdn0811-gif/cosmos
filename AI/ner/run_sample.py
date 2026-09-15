"""Run the matcher over local JSONL samples and print hit statistics for manual review.

    python run_sample.py data/sample_domestic_2025.jsonl [--review 30] [--out data/review_domestic.jsonl]
"""
import argparse
import collections
import json
import random
from pathlib import Path

from matcher import CompanyMatcher

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample")
    ap.add_argument("--aliases", default=HERE / "data" / "aliases.csv")
    ap.add_argument("--review", type=int, default=30)
    ap.add_argument("--out")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    m = CompanyMatcher.from_csv(args.aliases)
    docs = [json.loads(l) for l in open(args.sample, encoding="utf-8")]

    ticker_docs = collections.Counter()
    alias_hits = collections.Counter()
    blocked = collections.Counter()
    unresolved = collections.Counter()
    method_docs = collections.Counter()
    docs_with_hit = 0
    per_doc = []
    for d in docs:
        r = m.match(d.get("title"), d.get("body"))
        rows = r.by_ticker()
        if rows:
            docs_with_hit += 1
        for row in rows:
            ticker_docs[(row["ticker"], row["name_official"])] += 1
            method_docs[row["method"]] += 1
        for mm in r.mentions:
            alias_hits[mm.alias] += 1
        for b in r.blocked:
            blocked[b[0]] += 1
        for u in r.unresolved:
            unresolved[u.alias] += 1
        per_doc.append((d, r, rows))

    print(f"docs={len(docs)} docs_with_company={docs_with_hit} ({docs_with_hit/len(docs):.1%})")
    print("method:", dict(method_docs))
    print("\n== top tickers by docs ==")
    for (t, n), c in ticker_docs.most_common(40):
        print(f"  {t:>7} {n:<14} {c}")
    print("\n== top aliases ==", alias_hits.most_common(50))
    print("\n== blocked ==", blocked.most_common(30))
    print("\n== unresolved group names ==", unresolved.most_common(20))

    # review set: random docs with hits, with a compact context per mention
    random.seed(args.seed)
    hits = [x for x in per_doc if x[2]]
    sample = random.sample(hits, min(args.review, len(hits)))
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    for d, r, rows in sample:
        print("\n" + "=" * 100)
        print("TITLE:", (d.get("title") or "")[:120])
        for mm in r.mentions[:12]:
            ctx = r.text[max(0, mm.start - 25):mm.end + 25].replace("\n", " ")
            print(f"  [{mm.ticker} {mm.name_official}] '{mm.alias}' ({mm.method},{mm.confidence}) …{ctx}…")
        if r.unresolved:
            print("  UNRESOLVED:", [(u.alias) for u in r.unresolved])
        if out:
            out.write(json.dumps({
                "record_id": d["record_id"], "title": d.get("title"),
                "mentions": [{"ticker": mm.ticker, "alias": mm.alias, "start": mm.start, "end": mm.end,
                              "method": mm.method, "confidence": mm.confidence} for mm in r.mentions],
                "by_ticker": rows, "unresolved": [u.alias for u in r.unresolved],
            }, ensure_ascii=False, default=list) + "\n")
    if out:
        out.close()


if __name__ == "__main__":
    main()
