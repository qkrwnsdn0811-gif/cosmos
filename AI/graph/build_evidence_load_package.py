"""근거 문장을 운영 public 스키마에 넣을 CSV 6종을 만든다.

  PYTHONUTF8=1 python build_evidence_load_package.py

입력
  data/rel_hits_all_v2_sent.jsonl      관계 근거 10,354행 (문장 포함)
  data/relcand_sentstart.json          (문서, 문장) -> 문자 오프셋
  data/evidence_doc_meta.json          기사 메타 8,935건 (spark/fetch_evidence_document_meta.py)
  data/evidence_company_sentiment.json (문서, 기업) 감성 11,297행
  data/company_document_scores_v1.csv  (문서, 기업) 관련도·영향도
  ../ner/data/db/company.csv           티커 -> 시장

════════════════════════════════════════════════════════════════════════════
식별자는 수집기와 같은 규칙으로 만든다
════════════════════════════════════════════════════════════════════════════
Crawling/documents/services/document_loader/postgres.py 가 쓰는 규칙 그대로다.

  document_id = uuid5(NAMESPACE_URL, "cosmos:document:NEWS:" + sha256(canonical_url))
  source_id   = uuid5(NAMESPACE_URL, "cosmos:data-source:" + name)

그런데 **이 규칙만 믿으면 안 된다.** 실측으로 근거 문서 8,926건 중 3,591건이 이미
news_article 에 같은 canonical_url_hash 로 들어가 있는데 document_id 가 다르다
(예전 적재가 다른 규칙을 썼다). 그래서 CSV 는 document_id 대신 **canonical_url_hash**
를 문서 키로 내보내고, 실제 document_id 는 적재 SQL 이 정한다:
기존 news_article 행이 있으면 그 id 를, 없으면 위 uuid5 값을.

evidence_id 도 document_id 가 아니라 url_hash 로 만든다 — 그래야 어느 쪽으로 풀리든
같은 값이 나온다.

════════════════════════════════════════════════════════════════════════════
content_hash 를 비워 두는 이유
════════════════════════════════════════════════════════════════════════════
채우면 나중에 수집기가 같은 URL 을 다른 렌더링으로 가져올 때
"changed news content_hash requires an explicit revision/reanalysis workflow" 로
배치 전체가 실패한다(postgres.py). status 도 COLLECTED 로, analysis_version 은 NULL 로
둔다 — 그래야 수집기가 이 문서를 정상적으로 넘겨받는다.

is_service_visible 은 false 다. 근거 패널은 이 값을 안 보지만, 2020~2024년 기사
8,935건이 뉴스 목록에 갑자기 끼어들지 않게 한다. 보이게 하려면 UPDATE 한 줄이면 된다.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = DATA / "db_evidence_20260922"
MODEL_VERSION = "dict-v1.3+finbert-evidence-v1"
FORMULA_VERSION = "rel-v0.5-full"
HDFS_RAW_URI = "hdfs://localhost:9000/datasets/news/snapshots/20260908-prepared/news"
# 원천 published_at 은 대부분 오프셋이 없다 — 실측 8,935건 중 국내 1,047건(mysql_domestic)
# 만 이미 +00:00 을 달고 있다. 없는 것만 채운다: 국내는 KST, 해외는 UTC 로 읽는다.
# 해외 아카이브의 실제 발행 시간대는 원천에 없어서 UTC 로 두는 것이 가장 덜 틀린다
# (published_date 는 어차피 원천이 정규화해 둔 값을 그대로 쓴다).
OFFSET = {"domestic": "+09:00", "overseas": "+00:00"}
HAS_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
UNDIRECTED = {"PARTNER", "COMPETE"}


def headline(value: str) -> str:
    """제목의 첫 줄만 쓴다. 원천 파서가 본문 첫 문단을 제목에 흘린 기사가 276건 있다."""
    return value.splitlines()[0].strip() if value else value


def byline(value):
    """작성자란에 본문이 들어온 것은 버린다 — 실측 720건이 줄바꿈을 품고 있고
    4건은 400자가 넘는다. news_article.author 는 varchar(200) 이다."""
    if not value:
        return None
    line = value.splitlines()[0].strip()
    return line if 0 < len(line) <= 200 else None


def jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def main() -> None:
    markets = {r["stock_code"]: r["market"]
               for r in csv.DictReader((HERE.parent / "ner/data/db/company.csv")
                                       .open(encoding="utf-8-sig"))}

    meta = {r["record_id"]: r for r in jsonl(DATA / "evidence_doc_meta.json")}
    offsets = json.loads((DATA / "relcand_sentstart.json").read_text(encoding="utf-8"))
    hits = list(jsonl(DATA / "rel_hits_all_v2_sent.jsonl"))

    # 문서 키는 canonical_url_hash 다. 같은 URL 을 가진 record_id 는 한 문서로 합쳐진다
    # (실측 9쌍). 실제 document_id 는 적재 SQL 이 기존 행을 보고 정한다.
    doc_of = {rid: m["url_sha256"] for rid, m in meta.items()}
    fallback_id = {h: str(uuid5(NAMESPACE_URL, "cosmos:document:NEWS:" + h))
                   for h in set(doc_of.values())}

    sentiment = {(r["record_id"], r["ticker"]): r["sentiment"]
                 for r in jsonl(DATA / "evidence_company_sentiment.json")}
    scores: dict[tuple[str, str], dict] = {}
    with (DATA / "company_document_scores_v1.csv").open(encoding="utf-8-sig", newline="") as s:
        for row in csv.DictReader(s):
            key = (row["document_id"], row["company_id"])
            if key[0] in doc_of:
                scores[key] = row

    # ── 문장 순서: (문서, 기업) 안에서 문자 오프셋 순으로 0부터.
    # 오프셋은 record_id 기준이라, URL 이 같아 한 문서로 합쳐진 경우 둘 중 작은 값을 쓴다.
    by_pair: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for h in hits:
        off = offsets.get(f"{h['record_id']}\t{h['sentence']}")
        for ticker in (h["src_ticker"], h["dst_ticker"]):
            key = (str(doc_of[h["record_id"]]), ticker)
            prev = by_pair[key].get(h["sentence"])
            if prev is None or (off is not None and off < prev):
                by_pair[key][h["sentence"]] = off if off is not None else 1 << 30
    order_of: dict[tuple[str, str, str], int] = {}
    for (doc, ticker), texts in by_pair.items():
        for i, text in enumerate(sorted(texts, key=lambda t: (texts[t], t))):
            order_of[(doc, ticker, text)] = i

    def evidence_id(url_hash: str, ticker: str, order: int) -> str:
        return str(uuid5(NAMESPACE_URL, f"cosmos:evidence:{url_hash}:{ticker}:{order}"))

    # ── CSV 1 · data_source
    source_name = {ds: f"news:archive:{ds}" for ds in {m["source_dataset"] for m in meta.values()}}
    source_id = {n: str(uuid5(NAMESPACE_URL, "cosmos:data-source:" + n))
                 for n in source_name.values()}
    sources = [{"source_id": source_id[n], "name": n, "source_type": "NEWS"}
               for n in sorted(source_name.values())]

    # ── CSV 2·3 · source_document / news_article  (문서 단위, URL 중복 합침)
    documents, articles = {}, {}
    for rid, m in meta.items():
        doc = str(doc_of[rid])
        if doc in documents:
            continue
        raw = m.get("published_at")
        stamp = None if not raw else (raw if HAS_OFFSET.search(raw)
                                      else raw + OFFSET[m["region"]])
        documents[doc] = {"canonical_url_hash": doc, "fallback_document_id": fallback_id[doc],
                          "source_id": source_id[source_name[m["source_dataset"]]],
                          "title": headline(m["title"]), "original_url": m["url"],
                          "published_at": stamp, "hdfs_raw_uri": HDFS_RAW_URI,
                          "status": "COLLECTED"}
        articles[doc] = {"canonical_url_hash": doc, "publisher": m.get("publisher"),
                         "canonical_url": m["canonical_url"], "author": byline(m.get("author"))}

    # ── CSV 4·5 · company_document / document_evidence
    company_documents, evidences = {}, {}
    unknown = set()
    for h in hits:
        rid = h["record_id"]
        doc = str(doc_of[rid])
        for ticker in (h["src_ticker"], h["dst_ticker"]):
            if ticker not in markets:
                unknown.add(ticker)
                continue
            key = (doc, ticker)
            if key not in company_documents:
                sc = scores.get((rid, ticker), {})
                company_documents[key] = {
                    "canonical_url_hash": doc, "market": markets[ticker], "stock_code": ticker,
                    "mention_type": "MENTION",
                    "relevance_score": sc.get("relevance_score"),
                    "sentiment": sentiment.get((rid, ticker)),
                    "impact_score": sc.get("impact_score"),
                    "confidence": sc.get("confidence"),
                    "model_version": MODEL_VERSION, "is_service_visible": "false",
                    "analyzed_at": documents[doc]["published_at"]}
            order = order_of[(doc, ticker, h["sentence"])]
            evidences[(doc, ticker, order)] = {
                "evidence_id": evidence_id(doc, ticker, order),
                "canonical_url_hash": doc, "market": markets[ticker],
                "stock_code": ticker, "sentence_text": h["sentence"],
                "sentence_order": order, "confidence": h["confidence"],
                "model_version": MODEL_VERSION}

    # ── CSV 6 · relationship_evidence  (무방향 관계는 점수 적재와 같게 정렬해야 이어진다)
    links = {}
    for h in hits:
        src, dst, rel = h["src_ticker"], h["dst_ticker"], h["rel_type"]
        if src not in markets or dst not in markets:
            continue
        doc = str(doc_of[h["record_id"]])
        key_src, key_dst = (sorted((src, dst)) if rel in UNDIRECTED else (src, dst))
        order = order_of[(doc, src, h["sentence"])]
        links[(key_src, key_dst, rel, doc)] = {
            "source_market": markets[key_src], "source_stock_code": key_src,
            "target_market": markets[key_dst], "target_stock_code": key_dst,
            "relationship_type_code": rel, "canonical_url_hash": doc,
            "evidence_id": evidence_id(doc, src, order),
            "contribution_score": h["confidence"],
            "model_version": MODEL_VERSION, "formula_version": FORMULA_VERSION}

    OUT.mkdir(parents=True, exist_ok=True)

    def write(name: str, fields: list[str], values) -> None:
        with (OUT / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)

    write("data_source.csv", ["source_id", "name", "source_type"], sources)
    write("source_document.csv",
          ["canonical_url_hash", "fallback_document_id", "source_id", "title",
           "original_url", "published_at", "hdfs_raw_uri", "status"], documents.values())
    write("news_article.csv",
          ["canonical_url_hash", "publisher", "canonical_url", "author"], articles.values())
    write("company_document.csv",
          ["canonical_url_hash", "market", "stock_code", "mention_type", "relevance_score",
           "sentiment", "impact_score", "confidence", "model_version",
           "is_service_visible", "analyzed_at"], company_documents.values())
    write("document_evidence.csv",
          ["evidence_id", "canonical_url_hash", "market", "stock_code", "sentence_text",
           "sentence_order", "confidence", "model_version"], evidences.values())
    write("relationship_evidence.csv",
          ["source_market", "source_stock_code", "target_market", "target_stock_code",
           "relationship_type_code", "canonical_url_hash", "evidence_id", "contribution_score",
           "model_version", "formula_version"], links.values())

    print(f"{OUT}")
    print(f"  data_source            {len(sources):>7,}")
    print(f"  source_document        {len(documents):>7,}")
    print(f"  news_article           {len(articles):>7,}")
    print(f"  company_document       {len(company_documents):>7,}")
    print(f"  document_evidence      {len(evidences):>7,}")
    print(f"  relationship_evidence  {len(links):>7,}")
    if unknown:
        print(f"  기업 사전에 없는 티커 {sorted(unknown)}")


if __name__ == "__main__":
    main()
