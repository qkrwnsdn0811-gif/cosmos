"""Read-only HDFS export for the news-conditioned Story 3 experiment.

One deterministic hash sample per region/year (including 2016 for graph warmup).
Select only body representatives with high-confidence linked companies, then
join ORIGINAL article text. No outcome-based selection. Existing HDFS data is
never modified. Output is a new local JSONL plus a manifest of source counts.
The prepared source has dates, not publication times: downstream labels must
not use the same-date return as an unseen outcome.
"""
import argparse
import json
from pathlib import Path

NEWS = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
ROOT = "hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-mentions/model_version=dict-v1.3"


def main():
    from pyspark.sql import SparkSession, Window, functions as F
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-year-region", type=int, default=1500)
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        raise FileExistsError(out)
    spark = (SparkSession.builder.appName("cosmos-news-impact-export")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    try:
        source_counts, exported = [], 0
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf8") as f:
            for region in ("domestic", "overseas"):
                root = f"{ROOT}/run_id=dict-v1.3-{region}-20260911-0655"
                mentions = spark.read.parquet(root + "/data").filter(
                    (F.col("confidence") >= 0.9) & (~F.col("is_sports")) &
                    F.col("year").between("2016", "2026"))
                docs = mentions.groupBy("record_id", "year").agg(F.collect_list(F.struct(
                    "ticker", "n_mentions", "first_pos", "confidence", "method")).alias("companies"))
                source_counts.extend([dict(region=region, **r.asDict())
                                      for r in docs.groupBy("year").count().collect()])
                window = Window.partitionBy("year").orderBy(F.sha2(F.concat(F.lit("impact-v1:"), F.col("record_id")), 256))
                chosen = docs.withColumn("sample_rank", F.row_number().over(window)).filter(
                    F.col("sample_rank") <= args.per_year_region).drop("sample_rank", "year")
                news = spark.read.option("basePath", NEWS).parquet(NEWS).filter(
                    (F.col("region") == region) & F.col("year").between("2016", "2026") &
                    F.col("text_eligible") & F.col("is_body_representative"))
                rows = news.join(F.broadcast(chosen), "record_id").select(
                    "record_id", "published_date", "title", "body", "publisher", "region", "year", "companies")
                for row in rows.toLocalIterator():
                    f.write(json.dumps(row.asDict(recursive=True), ensure_ascii=False, default=str) + "\n")
                    exported += 1
                print(json.dumps({"region": region, "exported_so_far": exported}), flush=True)
        out.with_suffix(".manifest.json").write_text(json.dumps({
            "news_source": NEWS, "mentions_source": ROOT, "sampling": "sha256 impact-v1:record_id per region/year",
            "per_year_region": args.per_year_region, "eligible_linked_docs": source_counts,
            "exported": exported, "timestamp_precision": "date", "labels_not_used_in_sampling": True
        }, indent=2), encoding="utf8")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
