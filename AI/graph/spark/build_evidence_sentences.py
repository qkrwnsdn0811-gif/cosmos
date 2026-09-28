"""Step 5 — 멘션 스팬을 근거 문장으로 바꾼다 (Spark batch).

batch_mentions.py 가 낸 spans/ 는 **글자 위치**만 들고 있다. 화면과 FinBERT 는 문장이
필요하다. 이 잡이 원문을 다시 읽어 그 위치가 속한 문장을 잘라낸다.

  spark-submit --master 'local[3]' --driver-memory 6g \
    --py-files matcher.py,relevance_sentence_evidence.py \
    build_evidence_sentences.py --mentions-run <company-mentions run 경로> --region domestic

출력 (덮어쓰지 않는다. run_id 마다 새 디렉터리)
  /data-lake/sandbox/news/<owner>/evidence-sentences/model_version=<v>/run_id=<uuid>/
      data/        record_id · ticker · sentence_order · sentence_text · n_mentions_in_sentence
      manifest.json, _VERIFIED

════════════════════════════════════════════════════════════════════════════
오프셋 기준 — 여기서 틀리면 전부 어긋난다
════════════════════════════════════════════════════════════════════════════
matcher.py:639 가 `text = title + "\\n" + body` 로 붙인 뒤 그 문자열 기준으로 오프셋을
낸다(실측: title 8자면 body 첫 글자가 9). **본문만으로 문장을 자르면 제목 길이 + 1 만큼
전부 밀린다.** 그래서 여기서도 같은 방식으로 다시 붙인다.

trim_boilerplate 도 같이 적용해야 한다. 매칭은 잘라낸 뒤의 텍스트에서 했으므로,
자르지 않고 문장을 만들면 사이드바 문장이 근거로 올라온다
(실측: APR 오탐 18,097건 중 97.8%가 `[뉴스핌 베스트 기사]` 하나였다).

════════════════════════════════════════════════════════════════════════════
중복 처리
════════════════════════════════════════════════════════════════════════════
한 문장에 같은 기업이 두 번 나오면 spans 는 2행이지만 근거 문장은 1건이다.
(record_id, ticker, sentence_order) 로 묶고 멘션 수를 n_mentions_in_sentence 에 남긴다.

문장 분할은 relevance_sentence_evidence._segments 를 쓴다. 원문 손실이 없고
한국어 인용 어미(…라고/…라며)를 따옴표와 붙여 둔다. 새로 만들지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import uuid

INPUT_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
SANDBOX_BASE = "hdfs://localhost:9000/data-lake/sandbox/news/{owner}/evidence-sentences"
INPUT_DATASET_VERSION = "20260908-prepared"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mentions-run", required=True, help="company-mentions run 디렉터리(HDFS)")
    ap.add_argument("--region", choices=("domestic", "overseas"), required=True)
    ap.add_argument("--years", nargs="+", required=True)
    ap.add_argument("--model-version", default="evidence-v1")
    ap.add_argument("--owner", default="junwoo")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--partitions", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0, help="디버그: 입력 행 제한")
    args = ap.parse_args()

    from pyspark.sql import SparkSession, functions as F, types as T

    spark = SparkSession.builder.appName("build-evidence-sentences").getOrCreate()
    run_id = args.run_id or str(uuid.uuid4())
    out_root = f"{SANDBOX_BASE.format(owner=args.owner)}/model_version={args.model_version}/run_id={run_id}"

    spans = (spark.read.parquet(f"{args.mentions_run}/spans")
             .where(F.col("region") == args.region)
             .where(F.col("year").isin(*args.years))
             .select("record_id", "ticker", "alias", "start", "end"))

    news = (spark.read.parquet(INPUT_URI)
            .where(F.col("region") == args.region)
            .where(F.col("year").isin(*args.years))
            .where(F.col("text_eligible") & F.col("is_body_representative"))
            .select("record_id", "title", "body"))
    if args.limit:
        news = news.limit(args.limit)

    # 기사 한 건에 그 기사의 스팬을 모아 붙인다. 원문을 한 번만 읽고 문장을 한 번만 자른다.
    joined = (news.join(spans, "record_id")
              .groupBy("record_id", "title", "body")
              .agg(F.collect_list(F.struct("ticker", "start", "end")).alias("spans"))
              .repartition(args.partitions))

    out_schema = T.StructType([
        T.StructField("record_id", T.StringType()),
        T.StructField("ticker", T.StringType()),
        T.StructField("sentence_order", T.IntegerType()),
        T.StructField("sentence_text", T.StringType()),
        T.StructField("n_mentions_in_sentence", T.IntegerType()),
    ])

    def run_partition(rows):
        # --py-files 로 실려온다
        from matcher import trim_boilerplate
        from relevance_sentence_evidence import _segments
        import collections

        for r in rows:
            title = r["title"] or ""
            body = r["body"] or ""
            text = title + "\n" + body          # matcher.py:639 와 같은 방식이어야 한다
            cut = trim_boilerplate(text)        # 매칭도 잘라낸 뒤의 텍스트에서 했다
            scan = text[:cut]

            bounds = list(_segments(scan))      # [(start, end), ...] 원문 손실 없음
            if not bounds:
                continue

            # 스팬 위치 -> 문장 번호. 문장은 정렬돼 있으므로 이분 탐색으로 찾는다.
            starts = [b[0] for b in bounds]
            import bisect
            hits = collections.Counter()
            for sp in r["spans"]:
                pos = sp["start"]
                if pos is None or pos >= cut:   # 잘려나간 구간의 멘션은 버린다
                    continue
                i = bisect.bisect_right(starts, pos) - 1
                if i < 0 or not (bounds[i][0] <= pos < bounds[i][1]):
                    continue
                hits[(sp["ticker"], i)] += 1

            for (ticker, i), n in hits.items():
                s, e = bounds[i]
                sentence = scan[s:e].strip()
                if not sentence:
                    continue
                yield (r["record_id"], ticker, i, sentence, int(n))

    out = spark.createDataFrame(joined.rdd.mapPartitions(run_partition), out_schema)
    out.write.mode("errorifexists").parquet(f"{out_root}/data")

    n_rows = spark.read.parquet(f"{out_root}/data").count()
    n_pairs = spark.read.parquet(f"{out_root}/data").select("record_id", "ticker").distinct().count()
    manifest = {
        "run_id": run_id, "model_version": args.model_version,
        "schema_version": "evidence-sentences-0.1",
        "input_dataset_version": INPUT_DATASET_VERSION,
        "mentions_run": args.mentions_run,
        "input_filter": {"region": args.region, "years": args.years,
                         "text_eligible": True, "is_body_representative": True,
                         "limit": args.limit or None},
        "offset_basis": 'title + "\\n" + body, trim_boilerplate applied',
        "counts": {"sentence_rows": n_rows, "pairs_with_evidence": n_pairs},
        "execution_host": socket.gethostname(),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "notes": [
            "sentence_order 는 trim 후 텍스트의 문장 색인이다 (원문 문장 번호가 아니다)",
            "같은 문장에 같은 기업이 여러 번 나오면 1행으로 합치고 n_mentions_in_sentence 에 센다",
        ],
    }
    sc = spark.sparkContext
    sc.parallelize([json.dumps(manifest, ensure_ascii=False, indent=2)], 1) \
      .saveAsTextFile(f"{out_root}/manifest")
    sc.parallelize([""], 1).saveAsTextFile(f"{out_root}/_VERIFIED")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    spark.stop()


if __name__ == "__main__":
    main()
