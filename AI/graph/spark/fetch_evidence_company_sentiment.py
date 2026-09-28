"""근거 문서의 (기업, 감성)을 감성 산출물에서 뽑는다.

  spark-submit --master local[2] --driver-memory 3g \
    fetch_evidence_company_sentiment.py --ids rel_evidence_record_ids.txt --out ~/evidence_sentiment

운영 company_document.sentiment 를 채우려고 쓴다. 근거 문서(실측 8,935건)만 추린다.
전체 87만 쌍을 다 내리는 것이 아니다 — 지금 필요한 것은 근거 문장이 붙는 문서뿐이다.
"""
from __future__ import annotations

import argparse

from pyspark.sql import SparkSession, functions as F

SENTIMENT_URI = "hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-sentiment/v1"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    spark = SparkSession.builder.appName("evidence-company-sentiment").getOrCreate()
    with open(args.ids, encoding="utf-8") as stream:
        ids = [line.strip() for line in stream if line.strip()]
    wanted = spark.createDataFrame([(i,) for i in ids], "record_id string").distinct()

    hit = (spark.read.parquet(SENTIMENT_URI)
           .join(F.broadcast(wanted), "record_id", "inner")
           .select("record_id", "ticker", "sentiment", "n_sentences"))
    print("RESULT 문서 %d · 기업쌍 %d" % (len(ids), hit.count()))
    hit.coalesce(1).write.mode("overwrite").json(args.out)
    spark.stop()


if __name__ == "__main__":
    main()
