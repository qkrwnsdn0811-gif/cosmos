"""Extract a small text sample of the prepared news snapshot to a local JSONL file.

Run on the additional EC2:
  spark-submit --master 'local[2]' --driver-memory 3g sample_news.py \
    --region domestic --year 2025 --n 5000 --out /home/ubuntu/ner-work/sample_domestic_2025.jsonl

Read-only against HDFS. Writes only to the local path given by --out.
"""
import argparse
import json
import os

INPUT_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
COLS = ["record_id", "source_dataset", "publisher", "published_date", "language",
        "title", "body", "body_chars", "region", "year", "month",
        "duplicate_body_count", "is_body_representative", "text_eligible"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, choices=("domestic", "overseas"))
    ap.add_argument("--year", required=True)
    ap.add_argument("--month")
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    from pyspark.sql import SparkSession, functions as F

    spark = (SparkSession.builder.appName("ner-sample-news")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    try:
        df = spark.read.option("basePath", INPUT_URI).parquet(INPUT_URI)
        df = df.filter((F.col("region") == args.region) & (F.col("year") == args.year))
        if args.month:
            df = df.filter(F.col("month") == args.month)
        # one dedup criterion only (body representative), plus minimum length
        df = df.filter(F.col("text_eligible") & F.col("is_body_representative"))
        total = df.count()
        frac = min(1.0, (args.n * 1.3) / max(total, 1))
        rows = (df.select(*COLS).sample(withReplacement=False, fraction=frac, seed=args.seed)
                .limit(args.n).collect())
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r.asDict(), ensure_ascii=False, default=str) + "\n")
        print(json.dumps({"eligible_rows": total, "sampled": len(rows), "out": args.out}))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
