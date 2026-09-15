"""Knowledge graph step 4 — news co-mention edges from the Step 4 mentions table (Spark, on the additional EC2).

  spark-submit --master 'local[2]' --driver-memory 4g build_comention_edges.py \
    --run-root hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-mentions/model_version=dict-v1.1/run_id=<domestic> \
    --run-root hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-mentions/model_version=dict-v1.1/run_id=<overseas> \
    --min-docs 5 --out /home/ubuntu/ner-work/edges_co_mention.csv

Reads only `data/` (기사×종목 집계, confidence >= 1.0 rows: dictionary hits, not group-name guesses).
For every article, every pair of distinct tickers is one co-mention. Per pair we output:
  n_docs           articles mentioning both
  pmi              log( P(a,b) / (P(a) P(b)) )   -- corrects for very frequent companies (삼성전자 pairs with everyone)
  npmi             pmi / -log P(a,b), in [-1, 1]; weight = max(npmi, 0)
  first_date, last_date, sample_record_ids (up to 5, for the evidence column)
Edge schema matches the 지식그래프 확정안: src_ticker, dst_ticker, edge_type=co_mention, weight, as_of_date, evidence.
"""
import argparse
import csv
import datetime as dt
import math

NEWS_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", action="append", required=True)
    ap.add_argument("--min-docs", type=int, default=5)
    ap.add_argument("--min-confidence", type=float, default=1.0)
    ap.add_argument("--max-distance", type=int, default=600, help="max char distance between the two mentions")
    ap.add_argument("--max-publisher-share", type=float, default=0.9,
                    help="drop pairs (n_docs>=50) where one publisher accounts for more than this share")
    ap.add_argument("--out", required=True)
    ap.add_argument("--as-of", default=dt.date.today().isoformat())
    ap.add_argument("--until", default=None,
                    help="이 날짜(YYYY-MM-DD) 이전 기사만 집계한다. 모델 평가용으로 학습 구간만 "
                         "써서 엣지를 만들 때 필요하다 — 전 기간으로 만든 엣지를 과거 구간 "
                         "평가에 쓰면 가중치에 미래 기사가 들어간다")
    args = ap.parse_args()

    from pyspark.sql import SparkSession, functions as F, Window

    spark = (SparkSession.builder.appName("kg-comention-edges")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    # Proximity-based co-mention. Article bodies in some sources carry scraped sidebars/footers ("마켓 뉴스" headlines,
    # "오늘의 상승종목", "[뉴스핌 베스트 기사]" full-text of another article), so two companies in the same *document*
    # is not evidence of a relation. We require the two mentions to be within --max-distance characters
    # (same passage) and later drop pairs dominated by a single publisher.
    spans = None
    for root in args.run_root:
        s = spark.read.parquet(f"{root}/spans").filter(F.col("confidence") >= args.min_confidence)
        spans = s if spans is None else spans.unionByName(s)
    spans = spans.select("record_id", "ticker", "start", "published_date")
    if args.until:
        # 전체 개수(n_docs_total)와 종목별 등장수(n_a)도 같이 잘려야 pmi 가 일관된다.
        # spans 에서 자르면 아래 집계가 전부 이 부분집합 위에서 계산되므로 그대로 두면 된다.
        spans = spans.filter(F.col("published_date") < F.lit(args.until))
    docs = spans.select("record_id", "ticker", "published_date").dropDuplicates(["record_id", "ticker"])

    n_docs_total = docs.select("record_id").distinct().count()
    ticker_docs = docs.groupBy("ticker").agg(F.countDistinct("record_id").alias("n_a"))

    news_pub = (spark.read.option("basePath", NEWS_URI).parquet(NEWS_URI).select("record_id", "publisher"))
    a = spans.alias("a")
    b = spans.alias("b")
    pairs = (a.join(b, (F.col("a.record_id") == F.col("b.record_id")) & (F.col("a.ticker") < F.col("b.ticker"))
                    & (F.abs(F.col("a.start") - F.col("b.start")) <= args.max_distance))
             .select(F.col("a.record_id").alias("record_id"), F.col("a.ticker").alias("src"),
                     F.col("b.ticker").alias("dst"), F.col("a.published_date").alias("published_date"))
             .dropDuplicates(["record_id", "src", "dst"])
             .join(news_pub, "record_id", "left"))
    agg = (pairs.groupBy("src", "dst")
           .agg(F.countDistinct("record_id").alias("n_docs"), F.min("published_date").alias("first_date"),
                F.max("published_date").alias("last_date"), F.countDistinct("publisher").alias("n_publishers"),
                F.max(F.col("publisher")).alias("_any_pub"),
                F.slice(F.collect_set("record_id"), 1, 5).alias("sample_record_ids"))
           .filter(F.col("n_docs") >= args.min_docs))
    # share of the single most frequent publisher per pair
    top_pub = (pairs.groupBy("src", "dst", "publisher").agg(F.countDistinct("record_id").alias("n"))
               .groupBy("src", "dst").agg(F.max("n").alias("top_pub_docs")))
    agg = agg.join(top_pub, ["src", "dst"], "left")
    agg = (agg.join(ticker_docs.withColumnRenamed("ticker", "src").withColumnRenamed("n_a", "n_src"), "src")
              .join(ticker_docs.withColumnRenamed("ticker", "dst").withColumnRenamed("n_a", "n_dst"), "dst"))

    rows = agg.collect()
    N = float(n_docs_total)
    out_rows = []
    dropped_single_pub = 0
    for r in rows:
        top_share = (r["top_pub_docs"] or 0) / r["n_docs"]
        if r["n_docs"] >= 50 and top_share > args.max_publisher_share:
            dropped_single_pub += 1   # boilerplate signature: one publisher produces almost all co-mentions
            continue
        p_ab = r["n_docs"] / N
        p_a, p_b = r["n_src"] / N, r["n_dst"] / N
        pmi = math.log(p_ab / (p_a * p_b))
        npmi = pmi / (-math.log(p_ab)) if p_ab < 1 else 1.0
        out_rows.append({
            "src_ticker": r["src"], "dst_ticker": r["dst"], "edge_type": "co_mention",
            "weight": round(max(npmi, 0.0), 4), "as_of_date": args.as_of,
            "evidence": f"co_mention:n_docs={r['n_docs']};pmi={pmi:.3f};docs={','.join(r['sample_record_ids'])}",
            "n_docs": r["n_docs"], "n_src": r["n_src"], "n_dst": r["n_dst"], "pmi": round(pmi, 4), "npmi": round(npmi, 4),
            "n_publishers": r["n_publishers"], "top_publisher_share": round(top_share, 3),
            "first_date": r["first_date"], "last_date": r["last_date"],
        })
    out_rows.sort(key=lambda x: -x["n_docs"])
    print({"pairs_dropped_single_publisher": dropped_single_pub})
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else ["src_ticker"])
        w.writeheader()
        w.writerows(out_rows)
    print({"articles_with_company": n_docs_total, "pairs": len(out_rows), "out": args.out})
    for r in out_rows[:15]:
        print(f"  {r['src_ticker']:>7} - {r['dst_ticker']:<7} docs={r['n_docs']:>7} npmi={r['npmi']:.3f}")
    spark.stop()


if __name__ == "__main__":
    main()
