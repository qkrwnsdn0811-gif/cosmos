"""Step 8 — score the extractor against the gold labels (document × ticker level).

  PYTHONUTF8=1 .venv/Scripts/python.exe evaluate.py [--merge]  [--no-ner]

--merge   : merge data/eval/batches/labels_*.jsonl into data/eval/eval_set_200.jsonl (fills `gold`, `annotator`)
Then runs two systems over the 200 articles and prints precision / recall / F1:
  dict      : CompanyMatcher only (confidence >= 1.0 dictionary hits, plus rule/product hits reported separately)
  dict+ner  : full pipeline (CompanyExtractor with models/ner-company-v2)
A prediction counts as correct when (eval_id, ticker) is in the gold set. GOOG is folded into GOOGL on both sides.
Also prints the false positives / false negatives so the alias dictionary can be fixed.
"""
import argparse
import collections
import glob
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE / "data" / "eval" / "eval_set_200.jsonl"


def fold(t):
    return "GOOGL" if t == "GOOG" else t


def merge_labels():
    docs = [json.loads(l) for l in open(EVAL, encoding="utf-8")]
    by_id = {d["eval_id"]: d for d in docs}
    n = 0
    for path in sorted(glob.glob(str(HERE / "data" / "eval" / "batches" / "labels_*.jsonl"))):
        ann = Path(path).stem.replace("labels_", "agent-")
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            d = by_id.get(r["eval_id"])
            if d is None:
                continue
            d["gold"] = [{"ticker": fold(g["ticker"]), "alias": g.get("alias", "")} for g in r.get("gold", [])]
            d["annotator"] = ann
            d["note"] = r.get("note", "")
            n += 1
    with open(EVAL, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"merged labels for {n} articles; labelled={sum(1 for d in docs if d.get('annotator'))}/{len(docs)}")


def prf(pred: set, gold: set):
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return tp, p, r, f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--no-ner", action="store_true", help="skip the dict+ner system (no GPU / model)")
    ap.add_argument("--min-conf", type=float, default=0.5, help="minimum confidence for a prediction to count")
    args = ap.parse_args()
    if args.merge:
        merge_labels()

    docs = [json.loads(l) for l in open(EVAL, encoding="utf-8")]
    docs = [d for d in docs if d.get("annotator") and d.get("note") != "skip"]
    gold = {(d["eval_id"], g["ticker"]) for d in docs for g in d["gold"]}
    print(f"articles with labels: {len(docs)} | gold (article, ticker) pairs: {len(gold)} | articles with >=1 company: "
          f"{sum(1 for d in docs if d['gold'])}")

    from matcher import CompanyMatcher
    from pipeline import CompanyExtractor
    systems = {}
    m = CompanyMatcher.from_csv(HERE / "data" / "aliases.csv")
    pred_dict, pred_dict_all = set(), set()
    for d in docs:
        r = m.match(d.get("title"), d.get("body"))
        for row in r.by_ticker():
            if row["confidence"] >= 1.0:
                pred_dict.add((d["eval_id"], fold(row["ticker"])))
            if row["confidence"] >= args.min_conf:
                pred_dict_all.add((d["eval_id"], fold(row["ticker"])))
    systems["dict (conf 1.0 only)"] = pred_dict
    systems[f"dict (conf >= {args.min_conf}: +rule/product)"] = pred_dict_all
    if not args.no_ner:
        ex = CompanyExtractor()
        pred_full = set()
        for d in docs:
            o = ex.extract(d["eval_id"], d.get("title"), d.get("body"))
            for c in o["companies"]:
                if c["confidence"] >= args.min_conf:
                    pred_full.add((d["eval_id"], fold(c["ticker"])))
        systems[f"dict+ner (conf >= {args.min_conf})"] = pred_full

    print("\n== document x ticker scores ==")
    print(f"{'system':40} {'pred':>5} {'tp':>5} {'P':>7} {'R':>7} {'F1':>7}")
    for name, pred in systems.items():
        tp, p, r, f = prf(pred, gold)
        print(f"{name:40} {len(pred):5} {tp:5} {p:7.3f} {r:7.3f} {f:7.3f}")

    # error analysis on the best-covered system
    name, pred = list(systems.items())[-1]
    names = {}
    import csv
    for c in csv.DictReader(open(HERE / "data" / "companies.csv", encoding="utf-8-sig")):
        names[c["ticker"]] = c["name_official"]
    fp = collections.Counter(names.get(t, t) for (_, t) in pred - gold)
    fn = collections.Counter(names.get(t, t) for (_, t) in gold - pred)
    print(f"\n== {name}: false positives (predicted, not in gold) ==", fp.most_common(20))
    print(f"== {name}: false negatives (in gold, missed) ==", fn.most_common(20))
    gold_by_doc = collections.defaultdict(set)
    for e, t in gold:
        gold_by_doc[e].add(t)
    print("\n== per-article examples of misses ==")
    shown = 0
    for d in docs:
        pset = {t for (e, t) in pred if e == d["eval_id"]}
        gset = gold_by_doc.get(d["eval_id"], set())
        if pset != gset and shown < 12:
            shown += 1
            print(f"  {d['eval_id']} | {(d.get('title') or '')[:50]} | gold={sorted(names.get(t, t) for t in gset)} pred={sorted(names.get(t, t) for t in pset)} | note={d.get('note','')[:60]}")


if __name__ == "__main__":
    main()
