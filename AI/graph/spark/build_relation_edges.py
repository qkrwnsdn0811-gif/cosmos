"""Knowledge graph step 6-4 — aggregate sentence-level relations into SUPPLY/PARTNER/COMPETE edges.

  spark-submit --master 'local[2]' --driver-memory 4g \
    --py-files extract_relations.py build_relation_edges.py \
    --candidates /home/ubuntu/ner-work/relation_candidates \
    --ownership /home/ubuntu/ner-work/edges_ownership.csv \
    --min-docs 3 --out /home/ubuntu/ner-work/edges_relation.csv

문장 단위 추출은 표본 28건 검수에서 정밀도 79%였다. 남은 오탐은 거의 전부 "두 회사가 제3자를
상대로 같은 편"인 경우다 — "인텔과 삼성전자도 잠재적 파트너"에서 둘은 서로의 파트너가 아니다.
한 문장만 보고는 구분이 어렵지만, 이런 오독은 **반복되지 않는다**. 같은 쌍이 서로 다른 기사
여러 건에서 같은 관계로 나오면 그건 실제 관계다. 그래서 --min-docs 로 엣지를 거른다
(공동언급 엣지가 min-docs 5 를 쓰는 것과 같은 이유).

엣지 스키마는 다른 엣지들과 같은 확정안 + 진단 컬럼:
  src_ticker, dst_ticker, edge_type, weight, as_of_date, evidence,
  rel_type, n_docs, n_patterns, mean_conf, first_date, last_date
SUPPLY 는 방향이 있으므로 (src=공급자, dst=수요자) 순서를 유지하고, PARTNER·COMPETE 는
무방향이라 src < dst 로 정규화해 쌍마다 한 번만 남긴다.
"""
import argparse
import csv
import datetime as dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="extract_relation_candidates.py 산출 parquet")
    ap.add_argument("--ownership", help="계열사 배제용 edges_ownership.csv")
    ap.add_argument("--min-docs", type=int, default=3)
    ap.add_argument("--min-conf", type=float, default=0.6)
    ap.add_argument("--out", required=True)
    ap.add_argument("--as-of", default=dt.date.today().isoformat())
    args = ap.parse_args()

    from pyspark.sql import SparkSession, functions as F, types as T
    from extract_relations import classify, load_affiliates
    from pathlib import Path

    spark = (SparkSession.builder.appName("kg-relation-edges").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    affil = load_affiliates(Path(args.ownership)) if args.ownership else set()
    affil_b = spark.sparkContext.broadcast({tuple(sorted(p)) for p in affil})
    min_conf = args.min_conf

    schema = T.StructType([
        T.StructField("src", T.StringType()), T.StructField("dst", T.StringType()),
        T.StructField("rel_type", T.StringType()), T.StructField("confidence", T.DoubleType()),
        T.StructField("pattern", T.StringType()),
    ])

    @F.udf(returnType=schema)
    def extract(sentence, tickers, aliases):
        if not sentence or not tickers or len(tickers) != 2 or len(aliases or []) < 2:
            return None
        got = classify(sentence, aliases[0], aliases[1])
        if not got:
            return None
        src_alias, _dst_alias, rel, conf, pat = got
        if conf < min_conf:
            return None
        src = tickers[0] if src_alias == aliases[0] else tickers[1]
        dst = tickers[1] if src_alias == aliases[0] else tickers[0]
        if rel == "COMPETE" and tuple(sorted((src, dst))) in affil_b.value:
            return None
        return (src, dst, rel, float(conf), pat)

    cand = spark.read.parquet(args.candidates).filter(F.size("tickers") == 2)
    hits = (cand.withColumn("r", extract("sentence", "tickers", "aliases"))
            .filter(F.col("r").isNotNull())
            .select("record_id", "published_date",
                    F.col("r.src").alias("src"), F.col("r.dst").alias("dst"),
                    F.col("r.rel_type").alias("rel_type"),
                    F.col("r.confidence").alias("confidence"),
                    F.col("r.pattern").alias("pattern")))

    # PARTNER·COMPETE 는 무방향 -> src < dst 로 정규화. SUPPLY 는 방향이 의미이므로 그대로.
    undirected = F.col("rel_type").isin("PARTNER", "COMPETE")
    hits = hits.withColumn("a", F.when(undirected & (F.col("src") > F.col("dst")), F.col("dst")).otherwise(F.col("src"))) \
               .withColumn("b", F.when(undirected & (F.col("src") > F.col("dst")), F.col("src")).otherwise(F.col("dst")))

    agg = (hits.groupBy("a", "b", "rel_type")
           .agg(F.countDistinct("record_id").alias("n_docs"),
                F.countDistinct("pattern").alias("n_patterns"),
                F.avg("confidence").alias("mean_conf"),
                F.min("published_date").alias("first_date"),
                F.max("published_date").alias("last_date"),
                F.slice(F.collect_set("record_id"), 1, 5).alias("docs"))
           .filter(F.col("n_docs") >= args.min_docs))

    rows = agg.collect()
    out = []
    for r in rows:
        # weight: 기사 수가 늘수록 1에 수렴. 5건이면 0.74, 10건이면 0.90.
        n = r["n_docs"]
        weight = round(1.0 - 0.75 ** (n - args.min_docs + 1), 4)
        out.append({
            "src_ticker": r["a"], "dst_ticker": r["b"], "edge_type": "relation",
            "weight": weight, "as_of_date": args.as_of,
            "evidence": (f"relation:{r['rel_type']};n_docs={n};conf={r['mean_conf']:.2f};"
                         f"docs={','.join(r['docs'])}"),
            "rel_type": r["rel_type"], "n_docs": n, "n_patterns": r["n_patterns"],
            "mean_conf": round(r["mean_conf"], 3),
            "first_date": r["first_date"], "last_date": r["last_date"],
        })
    out.sort(key=lambda x: -x["n_docs"])
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()) if out else ["src_ticker"])
        w.writeheader()
        w.writerows(out)

    from collections import Counter
    by = Counter(x["rel_type"] for x in out)
    print({"sentence_hits": hits.count(), "edges": len(out), "by_type": dict(by), "out": args.out})
    for x in out[:20]:
        print(f"  {x['src_ticker']:>7} -{x['rel_type']:<8}-> {x['dst_ticker']:<7} "
              f"n_docs={x['n_docs']:>5} w={x['weight']:.3f}")
    spark.stop()


if __name__ == "__main__":
    main()
