"""Pull the full article text for the 200 audited record_ids into one JSONL.

  spark-submit --master 'local[2]' --driver-memory 4g fetch_full_bodies.py \
      --ids ids200.txt --out full200.jsonl

검수 표본(sample_v12.jsonl)은 본문을 2500자로 잘라 둔 읽기용 산출물이라 보일러플레이트
절단·나열문 미탐 같은 본문 뒷부분 의존 규칙을 로컬에서 검증할 수 없다. 원본 전문을 받아
v1.3 수정용 회귀셋으로 쓴다.
"""
import argparse
import json

NEWS_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from pyspark.sql import SparkSession, functions as F

    spark = (SparkSession.builder.appName("fetch-full-bodies")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    with open(args.ids) as f:
        ids = [x.strip() for x in f if x.strip()]

    news = spark.read.option("basePath", NEWS_URI).parquet(NEWS_URI)
    print("schema:", news.columns)

    sub = news.filter(F.col("record_id").isin(ids))
    keep = [c for c in ("record_id", "title", "body", "content", "publisher",
                        "published_date", "region", "url") if c in news.columns]
    rows = sub.select(*keep).collect()
    print(f"requested={len(ids)} found={len(rows)}")

    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            d = {k: r[k] for k in keep}
            for k, v in list(d.items()):
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    body_col = "body" if "body" in keep else ("content" if "content" in keep else None)
    if body_col:
        lens = sorted(len(r[body_col] or "") for r in rows)
        print(f"본문 길이 min={lens[0]} p50={lens[len(lens)//2]} max={lens[-1]}")
        print(f"2500자 초과 문서: {sum(1 for x in lens if x > 2500)}/{len(lens)}")
    spark.stop()


if __name__ == "__main__":
    main()
