"""Mine '이름(티커)' patterns from Korean news to collect alias candidates.

Korean financial news often writes company names with the ticker in parentheses,
e.g. "삼성전자(005930)", "엔비디아(NVDA)". Counting (name, ticker) pairs gives
alias candidates with frequencies for free.

Run on the additional EC2 (read-only on HDFS, writes a local CSV):
  spark-submit --master 'local[2]' --driver-memory 3g mine_aliases.py \
    --years 2023 2024 2025 2026 --out /home/ubuntu/ner-work/alias_candidates.csv
"""
import argparse
import csv
import os

INPUT_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
# name: Korean/Latin/digits and a few joiners, 2..25 chars; ticker: 6 digits or 1-5 uppercase letters
PATTERN = r"([가-힣A-Za-z0-9&·\-\.]{2,25})\s?\(((?:\d{6})|(?:[A-Z]{1,5}))\)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", required=True)
    ap.add_argument("--region", default="domestic")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-count", type=int, default=2)
    args = ap.parse_args()

    from pyspark.sql import SparkSession, functions as F

    spark = (SparkSession.builder.appName("ner-mine-aliases")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    try:
        df = spark.read.option("basePath", INPUT_URI).parquet(INPUT_URI)
        df = df.filter((F.col("region") == args.region) & F.col("year").isin(args.years))
        df = df.filter(F.col("text_eligible") & F.col("is_body_representative"))
        text = F.concat_ws("\n", F.coalesce(F.col("title"), F.lit("")), F.coalesce(F.col("body"), F.lit("")))
        names = F.regexp_extract_all(text, F.lit(PATTERN), 1)
        codes = F.regexp_extract_all(text, F.lit(PATTERN), 2)
        pairs = (df.select(F.arrays_zip(names.alias("name"), codes.alias("code")).alias("p"), "record_id")
                   .select(F.explode("p").alias("p"), "record_id")
                   .select(F.col("p.name").alias("name"), F.col("p.code").alias("code"), "record_id"))
        agg = (pairs.groupBy("name", "code")
                    .agg(F.count("*").alias("n_mentions"), F.countDistinct("record_id").alias("n_docs"))
                    .filter(F.col("n_docs") >= args.min_count)
                    .orderBy(F.col("n_docs").desc()))
        rows = agg.collect()
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["name", "code", "n_mentions", "n_docs"])
            for r in rows:
                w.writerow([r["name"], r["code"], r["n_mentions"], r["n_docs"]])
        print({"pairs": len(rows), "out": args.out})
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
