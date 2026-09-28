"""Draw the manual-labelling evaluation set (Step 8) from the local samples.

    python make_eval_set.py --domestic 150 --overseas 50 --out data/eval/eval_set_200.jsonl

Each line: record_id, region, publisher, published_date, title, body, and empty `gold` list to be
filled by annotators as [{"ticker": "005930", "alias": "삼성전자"}]. Matcher output is NOT included so the
labels stay independent of the system under test.
"""
import argparse
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domestic", type=int, default=150)
    ap.add_argument("--overseas", type=int, default=50)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=HERE / "data" / "eval" / "eval_set_200.jsonl")
    args = ap.parse_args()

    random.seed(args.seed)
    dom = load(HERE / "data" / "sample_domestic_2025.jsonl")
    ovs = load(HERE / "data" / "sample_overseas_2025.jsonl")
    # keep bodies to a readable size for annotators; favour articles that look like business news
    dom = [d for d in dom if 300 <= int(d["body_chars"]) <= 4000]
    ovs = [d for d in ovs if 300 <= int(d["body_chars"]) <= 6000]
    picked = random.sample(dom, args.domestic) + random.sample(ovs, args.overseas)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for i, d in enumerate(picked, 1):
            f.write(json.dumps({
                "eval_id": f"E{i:03d}", "record_id": d["record_id"], "region": d["region"],
                "publisher": d["publisher"], "published_date": d["published_date"],
                "title": d["title"], "body": d["body"], "gold": [], "annotator": "", "note": "",
            }, ensure_ascii=False) + "\n")
    print(f"wrote {len(picked)} docs -> {out}")


if __name__ == "__main__":
    main()
