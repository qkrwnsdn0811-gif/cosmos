"""Step 5 — build the distant-supervision NER corpus from Step 4 spans (Spark, on the additional EC2).

  spark-submit --master 'local[2]' --driver-memory 4g make_bio_dataset.py \
    --run-root hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-mentions/model_version=dict-v1/run_id=<id> \
    [--run-root <second run root>] --max-positive 150000 --negative-ratio 0.25 \
    --out /home/ubuntu/ner-work/ner_corpus_v1.jsonl

Each output line is one sentence with character-offset entity spans (converted to token BIO at training time):
  {"record_id", "region", "year", "text", "entities": [{"start", "end", "label": "COMPANY", "ticker"}], "split": "train|dev|test"}

Label-quality rules (distant supervision):
  - only dictionary mentions with confidence 1.0 become entities (ticker-only 0.9 and group 0.5 are excluded)
  - sentences that contain an unresolved group name ("삼성", "현대") are dropped: their label is unknown
  - sentences from documents with zero mentions AND zero unresolved names are negatives (label O everywhere)
  - split is a deterministic hash of record_id so sentences of one article never leak across splits
"""
import argparse
import hashlib
import json
import re

INPUT_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")
MIN_LEN, MAX_LEN = 12, 400


def split_sentences(text):
    """Yield (start, end) offsets of sentences in `text`."""
    pos = 0
    for m in SENT_SPLIT.finditer(text):
        if m.start() > pos:
            yield pos, m.start()
        pos = m.end()
    if pos < len(text):
        yield pos, len(text)


def split_of(record_id):
    h = int(hashlib.sha1(record_id.encode()).hexdigest()[:8], 16) % 100
    return "test" if h < 5 else "dev" if h < 10 else "train"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", action="append", required=True)
    ap.add_argument("--max-positive", type=int, default=150000)
    ap.add_argument("--negative-ratio", type=float, default=0.25)
    ap.add_argument("--pos-docs", type=int, default=1_600_000, help="approx. documents with a company mention (manifests)")
    ap.add_argument("--neg-docs", type=int, default=8_000_000, help="approx. eligible documents without any hit")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    from pyspark.sql import SparkSession, functions as F

    spark = (SparkSession.builder.appName("ner-make-bio-dataset")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    spans = None
    unres = None
    for root in args.run_root:
        s = spark.read.parquet(f"{root}/spans").filter(F.col("confidence") >= 1.0)
        u = spark.read.parquet(f"{root}/unresolved")
        spans = s if spans is None else spans.unionByName(s)
        unres = u if unres is None else unres.unionByName(u)

    span_docs = spans.groupBy("record_id").agg(
        F.collect_list(F.struct("start", "end", "ticker")).alias("ents"))
    unres_docs = unres.groupBy("record_id").agg(F.collect_list(F.struct("start", "end")).alias("unres"))
    doc_labels = span_docs.join(unres_docs, "record_id", "outer")

    news = (spark.read.option("basePath", INPUT_URI).parquet(INPUT_URI)
            .filter(F.col("text_eligible") & F.col("is_body_representative"))
            .select("record_id", "region", "year", "title", "body"))

    # positives: documents with at least one confident mention
    pos_docs = news.join(doc_labels.filter(F.col("ents").isNotNull()), "record_id")
    # negatives: documents the matcher found nothing in, not even an unresolved group name
    neg_docs = news.join(doc_labels, "record_id", "left_anti")
    n_neg_target = int(args.max_positive * args.negative_ratio)
    # Sample documents BEFORE the expensive sentence work. Document counts come from the run manifests
    # (--pos-docs / --neg-docs) so we avoid extra full passes over the 13M-row join; each labelled document
    # yields ~4 usable sentences, so we over-sample 1.5x and trim locally.
    pos_frac = min(1.0, (args.max_positive / 4) * 1.5 / max(args.pos_docs, 1))
    neg_frac = min(1.0, (n_neg_target / 4) * 1.5 / max(args.neg_docs, 1))
    pos_docs = pos_docs.sample(False, pos_frac, args.seed)
    neg_docs = neg_docs.sample(False, neg_frac, args.seed)

    def to_sentences(row, negative):
        text = (row["title"] or "") + "\n" + (row["body"] or "")
        ents = sorted((e["start"], e["end"], e["ticker"]) for e in (row["ents"] or [])) if not negative else []
        unres_spans = [(u["start"], u["end"]) for u in (row["unres"] or [])] if not negative else []
        split = split_of(row["record_id"])
        for s, e in split_sentences(text):
            if not (MIN_LEN <= e - s <= MAX_LEN):
                continue
            if any(us < e and ue > s for us, ue in unres_spans):
                continue  # ambiguous group name inside: label unknown
            inside = [(a, b, t) for a, b, t in ents if a >= s and b <= e]
            crossing = [1 for a, b, t in ents if (a < s < b) or (a < e < b)]
            if crossing:
                continue
            if negative or not inside:
                if not negative:
                    continue  # positive docs contribute only labelled sentences
                yield {"record_id": row["record_id"], "region": row["region"], "year": row["year"],
                       "text": text[s:e], "entities": [], "split": split}
            else:
                yield {"record_id": row["record_id"], "region": row["region"], "year": row["year"],
                       "text": text[s:e], "split": split,
                       "entities": [{"start": a - s, "end": b - s, "label": "COMPANY", "ticker": t} for a, b, t in inside]}

    pos_rdd = pos_docs.rdd.flatMap(lambda r: to_sentences(r, False))
    neg_rdd = neg_docs.rdd.flatMap(lambda r: to_sentences(r, True))

    import random
    rnd = random.Random(args.seed)
    pos_all = pos_rdd.collect()          # one pass; bounded by the document sample above
    neg_all = neg_rdd.collect()
    pos_rows = rnd.sample(pos_all, min(args.max_positive, len(pos_all)))
    neg_rows = rnd.sample(neg_all, min(n_neg_target, len(neg_all)))
    rows = pos_rows + neg_rows
    stats = {"positive": len(pos_rows), "negative": len(neg_rows),
             "entities": sum(len(r["entities"]) for r in pos_rows),
             "by_split": {k: sum(1 for r in rows if r["split"] == k) for k in ("train", "dev", "test")},
             "by_region": {k: sum(1 for r in rows if r["region"] == k) for k in ("domestic", "overseas")}}
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(args.out + ".stats.json", "w", encoding="utf-8") as f:
        json.dump({"run_roots": args.run_root, **stats}, f, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False))
    spark.stop()


if __name__ == "__main__":
    main()
