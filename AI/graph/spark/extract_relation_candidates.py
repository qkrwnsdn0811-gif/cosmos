"""Knowledge graph step 6-1 — sentences that name two or more companies (relation-extraction candidates).

  spark-submit --master 'local[2]' --driver-memory 4g extract_relation_candidates.py \
    --run-root hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-mentions/model_version=dict-v1.3/run_id=dict-v1.3-domestic-20260911-0655 \
    --run-root hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-mentions/model_version=dict-v1.3/run_id=dict-v1.3-overseas-20260911-0655 \
    --out-parquet /home/ubuntu/ner-work/relation_candidates \
    --sample-out /home/ubuntu/ner-work/relation_sample.jsonl --sample-n 20000

공동언급(step 4)은 "같은 기사/600자 안에 같이 나왔다"까지만 본다. 그건 관계가 있다는 신호일
뿐 무슨 관계인지는 말해주지 않는다. 관계 유형(SUPPLY/PARTNER/COMPETE)은 술어가 있어야
정해지고, 술어는 문장 단위로 봐야 한다 — "A가 B에 납품한다"의 '납품'을 읽어야 공급 관계다.
그래서 두 기업이 **같은 문장** 안에 있는 경우만 후보로 뽑는다.

오프셋 기준 (중요)
------------------
matcher.match() 는 `text = title + "\n" + body` 로 스캔하므로 spans 의 start/end 는
**제목을 포함한 문자열** 기준이다. 본문만 놓고 자르면 제목 길이만큼 전부 어긋난다.
여기서도 같은 방식으로 text 를 재조립한 뒤 문장을 자른다.

필터
----
* confidence >= 1.0 : 사전 확정 히트만. 그룹명 추정(0.5)·스포츠 강등(0.4)은 제외한다.
* 한 문장에 서로 다른 티커 2개 이상.
* 문장 길이 <= --max-sent-chars : 문장 분리가 실패한 덩어리(목록·표)를 버린다.

method 를 티커별로 같이 실어 보낸다. `ellipsis` 는 나열문에서 접미사를 전개한 멘션이라
("신한, KB국민, 하나, 우리은행"), 그런 문장은 관계가 아니라 목록일 가능성이 높다 —
로컬에서 술어 분포를 볼 때 이 표시로 걸러낸다.
"""
import argparse
import json
import re

NEWS_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"

# 문장 경계. 소수점("3.5%")에서 끊기지 않도록 숫자 뒤 마침표는 제외한다.
#
# 마침표 뒤 '공백'만 경계로 보면 안 된다: 한국어 기사는 "…있다.이번 계약은"처럼 공백 없이
# 이어 붙는 경우가 흔하다(후보 문장의 20.5%가 이 패턴을 품고 있었다). 그러면 두 문장이 한
# 덩어리가 되어 (1) 서로 다른 문장의 두 기업이 한 문장에 있는 것처럼 보이고 (2) 덩어리가
# 길어져 --max-sent-chars 상한에 걸려 통째로 버려진다. 그래서 한국어 종결어미(다/요/음/임/죠)
# 뒤의 마침표 + 한글도 경계로 본다.
SENT_BOUNDARY = re.compile(
    r"\n+"                             # 줄바꿈
    r"|(?<![0-9])[.!?]+[\s\u00a0]+"     # 문장부호 + 공백(줄바꿈 없는 공백 포함)
    r"|(?<=[다요음임죠])\.(?=[가-힣A-Za-z0-9])"   # "있다.이번" / "의미한다.LG" — 흔한 붙임
    r"|(?<=[a-z])\.(?=[A-Z])"          # "ended.The" — 영문 기사의 같은 현상
)


def split_sentences(text: str):
    """[(start, end, sentence)] — 원문 오프셋을 유지한 채 문장을 자른다."""
    out = []
    pos = 0
    for m in SENT_BOUNDARY.finditer(text):
        if m.start() > pos:
            out.append((pos, m.start(), text[pos:m.start()]))
        pos = m.end()
    if pos < len(text):
        out.append((pos, len(text), text[pos:]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", action="append", required=True)
    ap.add_argument("--min-confidence", type=float, default=1.0)
    ap.add_argument("--max-sent-chars", type=int, default=400)
    ap.add_argument("--min-sent-chars", type=int, default=10)
    ap.add_argument("--out-parquet", required=True)
    ap.add_argument("--sample-out")
    ap.add_argument("--sample-n", type=int, default=20000)
    args = ap.parse_args()

    from pyspark.sql import SparkSession, functions as F, types as T

    spark = (SparkSession.builder.appName("kg-relation-candidates")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    spans = None
    for root in args.run_root:                      # 여러 run_root 를 한 번에 읽으면
        s = spark.read.parquet(f"{root}/spans")     # CONFLICTING_DIRECTORY_STRUCTURES 가 난다
        spans = s if spans is None else spans.unionByName(s, allowMissingColumns=True)
    spans = (spans.filter(F.col("confidence") >= args.min_confidence)
             .select("record_id", "ticker", "alias", "start", "end", "method",
                     "published_date", "region"))

    per_doc = spans.groupBy("record_id", "published_date", "region").agg(
        F.collect_list(F.struct("ticker", "alias", "start", "end", "method")).alias("spans"))

    news = (spark.read.option("basePath", NEWS_URI).parquet(NEWS_URI)
            .select("record_id", "title", "body"))
    joined = per_doc.join(news, "record_id", "inner")

    schema = T.ArrayType(T.StructType([
        T.StructField("sent_start", T.IntegerType()),
        T.StructField("sentence", T.StringType()),
        T.StructField("tickers", T.ArrayType(T.StringType())),
        T.StructField("aliases", T.ArrayType(T.StringType())),
        T.StructField("methods", T.ArrayType(T.StringType())),
        T.StructField("n_companies", T.IntegerType()),
    ]))

    lo, hi = args.min_sent_chars, args.max_sent_chars

    @F.udf(returnType=schema)
    def sentences_with_two(title, body, spans_):
        text = (title or "") + "\n" + (body or "")     # matcher.match 와 동일한 재조립
        if not spans_:
            return []
        bounds = split_sentences(text)
        if not bounds:
            return []
        out = []
        for s_start, s_end, sent in bounds:
            n = len(sent)
            if n < lo or n > hi:
                continue
            hits = [r for r in spans_ if r["start"] >= s_start and r["end"] <= s_end]
            if len(hits) < 2:
                continue
            seen, tickers, aliases, methods = set(), [], [], []
            for r in sorted(hits, key=lambda x: x["start"]):
                if r["ticker"] in seen:
                    continue
                seen.add(r["ticker"])
                tickers.append(r["ticker"])
                aliases.append(r["alias"])
                methods.append(r["method"])
            if len(tickers) < 2:
                continue
            out.append((s_start, sent.strip(), tickers, aliases, methods, len(tickers)))
        return out

    exploded = (joined
                .withColumn("s", F.explode(sentences_with_two("title", "body", "spans")))
                .select("record_id", "published_date", "region",
                        F.col("s.sent_start").alias("sent_start"),
                        F.col("s.sentence").alias("sentence"),
                        F.col("s.tickers").alias("tickers"),
                        F.col("s.aliases").alias("aliases"),
                        F.col("s.methods").alias("methods"),
                        F.col("s.n_companies").alias("n_companies")))

    exploded.write.mode("overwrite").parquet(args.out_parquet)
    total = spark.read.parquet(args.out_parquet)
    n = total.count()
    print({"candidate_sentences": n})
    total.groupBy("n_companies").count().orderBy("n_companies").show(10)
    total.groupBy("region").count().show()

    if args.sample_out and n:
        frac = min(1.0, args.sample_n / n)
        rows = total.sample(False, frac, seed=42).limit(args.sample_n).collect()
        with open(args.sample_out, "w", encoding="utf-8") as f:
            for r in rows:
                d = r.asDict()
                d["published_date"] = str(d["published_date"])
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print({"sample_rows": len(rows), "sample_out": args.sample_out})
    spark.stop()


if __name__ == "__main__":
    main()
