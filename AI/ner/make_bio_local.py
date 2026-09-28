"""Local stand-in for spark/make_bio_dataset.py: build a small NER corpus from the local JSONL samples.

Used to smoke-test the Step 5 sentence/label logic and the Step 6 trainer before the full Spark run finishes.
Same rules as the Spark job (confidence 1.0 only, drop sentences with unresolved group names, negatives from
documents without any hit, split by record_id hash).

  PYTHONUTF8=1 .venv/Scripts/python.exe make_bio_local.py --out data/ner_corpus_local.jsonl
"""
import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "spark"))
from make_bio_dataset import MAX_LEN, MIN_LEN, split_of, split_sentences  # noqa: E402
from matcher import CompanyMatcher  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", nargs="+", default=[HERE / "data" / "sample_domestic_2025.jsonl",
                                                     HERE / "data" / "sample_overseas_2025.jsonl"])
    ap.add_argument("--negative-ratio", type=float, default=0.25)
    ap.add_argument("--out", default=HERE / "data" / "ner_corpus_local.jsonl")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    random.seed(args.seed)

    m = CompanyMatcher.from_csv(HERE / "data" / "aliases.csv")
    pos, neg = [], []
    for path in args.samples:
        for line in open(path, encoding="utf-8"):
            d = json.loads(line)
            r = m.match(d.get("title"), d.get("body"))
            text = r.text
            ents = sorted((x.start, x.end, x.ticker) for x in r.mentions if x.confidence >= 1.0)
            unres = [(u.start, u.end) for u in r.unresolved]
            negative = not r.mentions and not r.unresolved
            split = split_of(d["record_id"])
            for s, e in split_sentences(text):
                if not (MIN_LEN <= e - s <= MAX_LEN):
                    continue
                if any(us < e and ue > s for us, ue in unres):
                    continue
                if any((a < s < b) or (a < e < b) for a, b, _ in ents):
                    continue
                inside = [(a, b, t) for a, b, t in ents if a >= s and b <= e]
                row = {"record_id": d["record_id"], "region": d["region"], "year": d["year"],
                       "text": text[s:e], "split": split,
                       "entities": [{"start": a - s, "end": b - s, "label": "COMPANY", "ticker": t} for a, b, t in inside]}
                if inside:
                    pos.append(row)
                elif negative:
                    neg.append(row)
    neg = random.sample(neg, min(len(neg), int(len(pos) * args.negative_ratio)))
    rows = pos + neg
    random.shuffle(rows)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats = {"positive": len(pos), "negative": len(neg), "entities": sum(len(r["entities"]) for r in pos),
             "by_split": {k: sum(1 for r in rows if r["split"] == k) for k in ("train", "dev", "test")}}
    print(json.dumps(stats, ensure_ascii=False))
    for r in random.sample(pos, 3):
        marked = r["text"]
        for e in sorted(r["entities"], key=lambda x: -x["start"]):
            marked = marked[:e["start"]] + "[" + marked[e["start"]:e["end"]] + "|" + e["ticker"] + "]" + marked[e["end"]:]
        print(" ", marked[:200])


if __name__ == "__main__":
    main()
