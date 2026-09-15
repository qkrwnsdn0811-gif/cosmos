"""Step 4 — run the dictionary matcher over the prepared HDFS news snapshot (Spark batch).

Run on the additional EC2 (matcher.py and aliases.csv are shipped with the job):

  spark-submit --master 'local[2]' --driver-memory 3g --executor-memory 3g \
    --py-files /home/ubuntu/ner-work/matcher.py --files /home/ubuntu/ner-work/aliases.csv \
    batch_mentions.py --region domestic --years 2025 2026 --model-version dict-v1

Output (one run directory, never overwritten; README rule: data/, manifest.json, _VERIFIED).
Default target is the owner's sandbox; --promote switches to the team path after agreement:

  /data-lake/sandbox/news/<owner>/company-mentions/model_version=<v>/run_id=<uuid>/
      data/        one row per (record_id, ticker): n_mentions, first_pos, confidence, method, aliases
      spans/       one row per mention with char offsets into title + "\n" + body  (input for Step 5 BIO)
      unresolved/  group-name hits that could not be attributed (input for Step 9 dictionary growth)
      manifest.json, _VERIFIED

Input rows are filtered to text_eligible AND is_body_representative (one dedup criterion, per DATA_CONTRACT).
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import socket
import sys
import uuid

INPUT_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
# README rule: experiment in sandbox/news/<owner>/ first; promote to analyzed/ only after the team checklist.
SANDBOX_BASE = "hdfs://localhost:9000/data-lake/sandbox/news/{owner}/company-mentions"
ANALYZED_BASE = "hdfs://localhost:9000/data-lake/analyzed/news/company-mentions"
INPUT_DATASET_VERSION = "20260908-prepared"


def load_alias_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", choices=("domestic", "overseas"), required=True)
    ap.add_argument("--years", nargs="+", required=True)
    ap.add_argument("--aliases", default="aliases.csv", help="local path (shipped via --files)")
    ap.add_argument("--model-version", default="dict-v1")
    ap.add_argument("--processing-version", default="unknown", help="git commit of ai/ner")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--owner", default="junwoo", help="sandbox owner folder (letters/digits/_/-)")
    ap.add_argument("--promote", action="store_true",
                    help="write under /data-lake/analyzed instead of the owner's sandbox (team agreement required)")
    ap.add_argument("--partitions", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="debug: cap input rows")
    args = ap.parse_args()

    from pyspark import SparkFiles
    from pyspark.sql import SparkSession, functions as F, types as T

    spark = (SparkSession.builder.appName(f"ner-batch-mentions-{args.region}")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC")
             .getOrCreate())
    sc = spark.sparkContext
    sc.setLogLevel("WARN")

    alias_path = args.aliases if os.path.exists(args.aliases) else SparkFiles.get(os.path.basename(args.aliases))
    alias_rows = load_alias_rows(alias_path)
    alias_bc = sc.broadcast(alias_rows)

    run_id = args.run_id or str(uuid.uuid4())
    base = ANALYZED_BASE if args.promote else SANDBOX_BASE.format(owner=args.owner)
    out_root = f"{base}/model_version={args.model_version}/run_id={run_id}"
    started = dt.datetime.now(dt.timezone.utc).isoformat()

    news = spark.read.option("basePath", INPUT_URI).parquet(INPUT_URI)
    news = news.filter((F.col("region") == args.region) & F.col("year").isin(args.years))
    news = news.filter(F.col("text_eligible") & F.col("is_body_representative"))
    if args.limit:
        news = news.limit(args.limit)
    inp = news.select("record_id", "title", "body", "region", "year", "month", "published_date").repartition(args.partitions)
    input_rows = inp.count()

    def run_partition(rows):
        from matcher import CompanyMatcher  # shipped via --py-files
        m = CompanyMatcher.from_rows(alias_bc.value)
        for r in rows:
            res = m.match(r["title"], r["body"])
            base = (r["record_id"], r["region"], r["year"], r["month"], r["published_date"])
            for a in res.by_ticker():
                yield ("agg", base + (a["ticker"], a["name_official"], a["n_mentions"], a["first_pos"],
                                      float(a["confidence"]), a["method"], a["aliases"], res.is_sports))
            for mm in res.mentions:
                yield ("span", base + (mm.ticker, mm.alias, mm.start, mm.end, mm.method, float(mm.confidence)))
            for u in res.unresolved:
                yield ("unres", base + (u.alias, u.group_id, u.start, u.end, res.is_sports))
            for ind in res.by_industry():
                yield ("ind", base + (ind["industry_id"], ind["industry_ko"], ind["n_mentions"], ind["first_pos"],
                                      ind["aliases"], bool(res.mentions)))

    from pyspark import StorageLevel
    tagged = inp.rdd.mapPartitions(run_partition).persist(StorageLevel.MEMORY_AND_DISK)

    base_fields = [T.StructField("record_id", T.StringType()), T.StructField("region", T.StringType()),
                   T.StructField("year", T.StringType()), T.StructField("month", T.StringType()),
                   T.StructField("published_date", T.StringType())]
    agg_schema = T.StructType(base_fields + [
        T.StructField("ticker", T.StringType()), T.StructField("name_official", T.StringType()),
        T.StructField("n_mentions", T.IntegerType()), T.StructField("first_pos", T.StringType()),
        T.StructField("confidence", T.DoubleType()), T.StructField("method", T.StringType()),
        T.StructField("aliases", T.ArrayType(T.StringType())), T.StructField("is_sports", T.BooleanType())])
    span_schema = T.StructType(base_fields + [
        T.StructField("ticker", T.StringType()), T.StructField("alias", T.StringType()),
        T.StructField("start", T.IntegerType()), T.StructField("end", T.IntegerType()),
        T.StructField("method", T.StringType()), T.StructField("confidence", T.DoubleType())])
    unres_schema = T.StructType(base_fields + [
        T.StructField("alias", T.StringType()), T.StructField("group_id", T.StringType()),
        T.StructField("start", T.IntegerType()), T.StructField("end", T.IntegerType()),
        T.StructField("is_sports", T.BooleanType())])

    ind_schema = T.StructType(base_fields + [
        T.StructField("industry_id", T.StringType()), T.StructField("industry_ko", T.StringType()),
        T.StructField("n_mentions", T.IntegerType()), T.StructField("first_pos", T.StringType()),
        T.StructField("aliases", T.ArrayType(T.StringType())), T.StructField("has_company", T.BooleanType())])

    agg_df = spark.createDataFrame(tagged.filter(lambda x: x[0] == "agg").map(lambda x: x[1]), agg_schema)
    span_df = spark.createDataFrame(tagged.filter(lambda x: x[0] == "span").map(lambda x: x[1]), span_schema)
    unres_df = spark.createDataFrame(tagged.filter(lambda x: x[0] == "unres").map(lambda x: x[1]), unres_schema)
    ind_df = spark.createDataFrame(tagged.filter(lambda x: x[0] == "ind").map(lambda x: x[1]), ind_schema)

    for name, df in (("data", agg_df), ("spans", span_df), ("unresolved", unres_df), ("industries", ind_df)):
        df.write.mode("errorifexists").partitionBy("year").parquet(f"{out_root}/{name}")

    # re-read verification
    agg_n = spark.read.parquet(f"{out_root}/data").count()
    span_n = spark.read.parquet(f"{out_root}/spans").count()
    unres_n = spark.read.parquet(f"{out_root}/unresolved").count()
    ind_n = spark.read.parquet(f"{out_root}/industries").count()
    ind_only_docs = (spark.read.parquet(f"{out_root}/industries").filter(~F.col("has_company"))
                     .select("record_id").distinct().count())
    docs_with = spark.read.parquet(f"{out_root}/data").select("record_id").distinct().count()
    top = (spark.read.parquet(f"{out_root}/data").groupBy("ticker", "name_official")
           .agg(F.countDistinct("record_id").alias("docs")).orderBy(F.col("docs").desc()).limit(30).collect())

    manifest = {
        "run_id": run_id, "model_name": "dictionary-matcher", "model_version": args.model_version,
        "processing_version": args.processing_version, "schema_version": "company-mentions-0.1",
        "input_dataset_version": INPUT_DATASET_VERSION, "input_uri": INPUT_URI,
        "input_filter": {"region": args.region, "years": args.years,
                         "text_eligible": True, "is_body_representative": True, "limit": args.limit or None},
        "input_rows": input_rows, "alias_count": len(alias_rows),
        "output": {"data": f"{out_root}/data", "spans": f"{out_root}/spans", "unresolved": f"{out_root}/unresolved",
                   "industries": f"{out_root}/industries"},
        "counts": {"data_rows": agg_n, "span_rows": span_n, "unresolved_rows": unres_n,
                   "industry_rows": ind_n, "docs_with_industry_only": ind_only_docs,
                   "docs_with_company": docs_with,
                   "docs_with_company_ratio": round(docs_with / input_rows, 4) if input_rows else None},
        "top_tickers": [{"ticker": r["ticker"], "name": r["name_official"], "docs": r["docs"]} for r in top],
        "execution_host": socket.gethostname(), "started_at": started,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "notes": ["record_id is the source-record key, not the final document_id",
                  "confidence 1.0 = dictionary hit, 0.9 = ticker in context, 0.5 = group name -> holding company"],
    }
    # write manifest + _VERIFIED through Hadoop FS API (no local HDFS client needed)
    jvm = sc._jvm
    conf = sc._jsc.hadoopConfiguration()
    fs = jvm.org.apache.hadoop.fs.FileSystem.get(jvm.java.net.URI(out_root), conf)
    for name, payload in (("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)), ("_VERIFIED", "")):
        out = fs.create(jvm.org.apache.hadoop.fs.Path(f"{out_root}/{name}"), True)
        out.write(bytearray(payload.encode("utf-8")))
        out.close()
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    spark.stop()


if __name__ == "__main__":
    main()
