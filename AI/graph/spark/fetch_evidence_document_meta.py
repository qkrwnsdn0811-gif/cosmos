"""근거 문서의 기사 메타데이터를 원천 스냅샷에서 뽑는다.

  spark-submit --master local[3] --driver-memory 4g \
    fetch_evidence_document_meta.py --ids rel_evidence_record_ids.txt --out ~/evidence_meta

관계 근거로 쓰인 문서만(실측 8,935건) 추려 PostgreSQL 적재용 필드를 낸다.
운영 DB 의 source_document / news_article 은 이 값들을 요구한다:

  title · url · canonical_url · url_sha256 · publisher · author · published_at

url_sha256 은 그대로 news_article.canonical_url_hash 가 되고, 수집기가 문서를
알아보는 자연키다(Crawling/documents/services/document_loader/postgres.py `_identity`).
그래서 직접 만들지 않고 원천 값을 쓴다 — 우리가 다시 해싱하면 정규화 차이로 어긋난다.

body_sha256 은 **일부러 안 내보낸다.** source_document.content_hash 를 채우면
나중에 수집기가 같은 URL 을 다른 렌더링으로 다시 가져올 때
"changed news content_hash requires an explicit revision/reanalysis workflow" 로
배치가 통째로 실패한다(postgres.py 의 protected_news_revision 분기). 근거 문장을
띄우자고 실시간 수집을 깨뜨릴 이유가 없다.
"""
from __future__ import annotations

import argparse

from pyspark.sql import SparkSession, functions as F

INPUT_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
FIELDS = ["record_id", "source_dataset", "title", "url", "canonical_url", "url_sha256",
          "publisher", "author", "published_at", "published_date", "region"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="record_id 한 줄에 하나")
    ap.add_argument("--out", required=True, help="출력 디렉터리 (JSON)")
    args = ap.parse_args()

    spark = SparkSession.builder.appName("evidence-document-meta").getOrCreate()
    with open(args.ids, encoding="utf-8") as stream:
        ids = [line.strip() for line in stream if line.strip()]
    wanted = spark.createDataFrame([(i,) for i in ids], "record_id string").distinct()

    news = spark.read.parquet(INPUT_URI).select(*FIELDS)
    # 같은 record_id 가 파티션에 두 번 있을 이유는 없지만, 있으면 하나만 쓴다.
    hit = (news.join(F.broadcast(wanted), "record_id", "inner")
           .dropDuplicates(["record_id"]))

    found = hit.count()
    print("RESULT 요청 %d · 찾음 %d" % (len(ids), found))
    for column in ("title", "canonical_url", "url_sha256", "published_at", "publisher"):
        print("RESULT 빈값 %s %d" % (column, hit.where(F.col(column).isNull()).count()))
    hit.coalesce(1).write.mode("overwrite").json(args.out)
    spark.stop()


if __name__ == "__main__":
    main()
