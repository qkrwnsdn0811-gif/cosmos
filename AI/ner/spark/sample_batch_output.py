"""Pull an auditable sample of the dict-v1.2 batch output, joined with the source article text.

Two artefacts:
  sample_v12.jsonl  - 200 articles with what the module extracted, for human/agent review
  alias_freq.csv    - which alias STRING drove how many documents (this is how "Strategy" was found)
"""
import json
from pyspark.sql import SparkSession, functions as F

INPUT_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
BASE = "hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-mentions/model_version=dict-v1.2"
RUNS = [f"{BASE}/run_id=dict-v1.2-domestic-20260910-1442",
        f"{BASE}/run_id=dict-v1.2-overseas-20260910-1442"]
OUT = "/home/ubuntu/ner-work"

spark = (SparkSession.builder.appName("sample-v12")
         .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false").getOrCreate())
spark.sparkContext.setLogLevel("ERROR")


def union(sub, cols):
    parts = [spark.read.parquet(f"{r}/{sub}").select(*cols) for r in RUNS]
    out = parts[0]
    for p in parts[1:]:
        out = out.unionByName(p)
    return out


data = union("data", ["record_id", "ticker", "name_official", "n_mentions", "first_pos",
                      "confidence", "method", "aliases", "is_sports"])
inds = union("industries", ["record_id", "industry_id", "industry_ko", "n_mentions", "has_company"])
spans = union("spans", ["record_id", "ticker", "alias"])

# ---- 1. alias frequency: the single most revealing table for systemic false positives ----
freq = (spans.groupBy("alias", "ticker").agg(F.countDistinct("record_id").alias("n_docs"))
        .orderBy(F.col("n_docs").desc()).limit(500).toPandas()
        if False else
        spans.groupBy("alias", "ticker").agg(F.countDistinct("record_id").alias("n_docs"))
        .orderBy(F.col("n_docs").desc()).limit(500).collect())
with open(f"{OUT}/alias_freq.csv", "w", encoding="utf-8") as f:
    f.write("alias,ticker,n_docs\n")
    for r in freq:
        a = r["alias"].replace('"', '""')
        f.write(f'"{a}",{r["ticker"]},{r["n_docs"]}\n')
print(f"alias_freq.csv: {len(freq)} rows")

# ---- 2. article sample ----
news = spark.read.option("basePath", INPUT_URI).parquet(INPUT_URI) \
    .filter(F.col("text_eligible") & F.col("is_body_representative")) \
    .select("record_id", "title", "body", "region", "published_date")

with_co = data.select("record_id").distinct().sample(False, 0.0002, seed=7).limit(150)
ind_only = inds.filter(~F.col("has_company")).select("record_id").distinct().sample(False, 0.0005, seed=7).limit(50)
picked = with_co.unionByName(ind_only).distinct()

art = news.join(picked, "record_id")
co_rows = data.join(picked, "record_id").collect()
in_rows = inds.join(picked, "record_id").collect()

co_by, in_by = {}, {}
for r in co_rows:
    co_by.setdefault(r["record_id"], []).append(
        {"ticker": r["ticker"], "name": r["name_official"], "n_mentions": r["n_mentions"],
         "first_pos": r["first_pos"], "confidence": float(r["confidence"]), "method": r["method"],
         "aliases": list(r["aliases"]), "is_sports": r["is_sports"]})
for r in in_rows:
    in_by.setdefault(r["record_id"], []).append(
        {"industry_id": r["industry_id"], "industry_ko": r["industry_ko"], "n_mentions": r["n_mentions"]})

n = 0
with open(f"{OUT}/sample_v12.jsonl", "w", encoding="utf-8") as f:
    for r in art.collect():
        rid = r["record_id"]
        f.write(json.dumps({
            "record_id": rid, "region": r["region"], "published_date": str(r["published_date"]),
            "title": r["title"], "body": (r["body"] or "")[:2500],
            "extracted_companies": sorted(co_by.get(rid, []), key=lambda x: -x["confidence"]),
            "extracted_industries": in_by.get(rid, []),
        }, ensure_ascii=False) + "\n")
        n += 1
print(f"sample_v12.jsonl: {n} articles")
spark.stop()
