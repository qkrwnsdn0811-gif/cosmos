"""NER corpus v2 — distant-supervision sentences + KLUE-NER gold organization (OG) labels.

Why: v1 (distant labels only) reproduced the dictionary (98.6% overlap, almost no new surface forms) because every
company outside our 202-ticker universe was labelled O. Mixing gold OG spans from KLUE-NER teaches the general
notion of "organization name" so the model can surface 포스코, 현대제철, Micron for Step 7 linking.

  PYTHONUTF8=1 .venv/Scripts/python.exe make_corpus_v2.py --distant data/ner_corpus_v1_dedup.jsonl \
      --out data/ner_corpus_v2.jsonl --klue-repeat 3 --negative-ratio 0.10

Composition (defaults): all distant positives, distant negatives capped at 10% of positives, KLUE train x3 (all
sentences, OG spans -> COMPANY, sentences without OG act as gold negatives), KLUE validation -> dev split.
"""
import argparse
import collections
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent


def klue_to_rows(ds, split_name, names):
    rows = []
    for ex in ds:
        toks, tags = ex["tokens"], ex["ner_tags"]
        text = "".join(toks)
        ents, cur = [], None
        for i, (tok, t) in enumerate(zip(toks, tags)):
            lab = names[t]
            if lab == "B-OG":
                if cur:
                    ents.append(cur)
                cur = {"start": i, "end": i + 1, "label": "COMPANY", "ticker": ""}
            elif lab == "I-OG" and cur is not None:
                cur["end"] = i + 1
            else:
                if cur:
                    ents.append(cur)
                cur = None
        if cur:
            ents.append(cur)
        # trim trailing spaces inside spans
        for e in ents:
            while e["end"] > e["start"] and text[e["end"] - 1] == " ":
                e["end"] -= 1
        rows.append({"record_id": f"klue:{split_name}:{len(rows)}", "region": "klue", "year": "", "text": text,
                     "entities": ents, "split": "dev" if split_name == "validation" else "train", "source": "klue_og"})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--distant", default=HERE / "data" / "ner_corpus_v1_dedup.jsonl")
    ap.add_argument("--out", default=HERE / "data" / "ner_corpus_v2.jsonl")
    ap.add_argument("--klue-repeat", type=int, default=3)
    ap.add_argument("--negative-ratio", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rnd = random.Random(args.seed)

    from datasets import load_dataset
    ds = load_dataset("klue/klue", "ner")
    names = ds["train"].features["ner_tags"].feature.names
    klue_train = klue_to_rows(ds["train"], "train", names)
    klue_dev = klue_to_rows(ds["validation"], "validation", names)

    distant = [json.loads(l) for l in open(args.distant, encoding="utf-8")]
    for r in distant:
        r["source"] = "distant"
    pos = [r for r in distant if r["entities"]]
    neg = [r for r in distant if not r["entities"]]
    neg_train = [r for r in neg if r["split"] == "train"]
    keep_neg = rnd.sample(neg_train, min(len(neg_train), int(sum(1 for r in pos if r["split"] == "train") * args.negative_ratio)))
    neg_eval = [r for r in neg if r["split"] != "train"]

    rows = pos + keep_neg + neg_eval + klue_train * args.klue_repeat + klue_dev
    rnd.shuffle(rows)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats = {
        "distant_pos": len(pos), "distant_neg_kept": len(keep_neg), "distant_neg_eval": len(neg_eval),
        "klue_train_rows": len(klue_train) * args.klue_repeat, "klue_dev_rows": len(klue_dev),
        "klue_og_entities": sum(len(r["entities"]) for r in klue_train),
        "total": len(rows), "by_split": dict(collections.Counter(r["split"] for r in rows)),
        "by_source": dict(collections.Counter(r["source"] for r in rows)),
    }
    print(json.dumps(stats, ensure_ascii=False))
    for r in klue_train[:3]:
        marked = r["text"]
        for e in sorted(r["entities"], key=lambda x: -x["start"]):
            marked = marked[:e["start"]] + "[" + marked[e["start"]:e["end"]] + "]" + marked[e["end"]:]
        print("  KLUE:", marked[:140])


if __name__ == "__main__":
    main()
