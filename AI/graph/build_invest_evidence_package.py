"""INVEST 관계의 공시 근거를 적재할 CSV 2종을 만든다.

  PYTHONUTF8=1 python build_invest_evidence_package.py

입력
  data/invest_sentences.json   summarize_invest_disclosures.py 산출 (40건)
  ../ner/data/db/company.csv   티커 -> 시장

산출
  data/db_invest_evidence_20260923/{document_evidence,relationship_evidence}.csv

════════════════════════════════════════════════════════════════════════════
뉴스 근거와 무엇이 다른가
════════════════════════════════════════════════════════════════════════════
뉴스 근거는 기사 원문에서 오려낸 문장이지만, 여기 문장은 공시 사실을 요약해
**만든 것**이다. model_version 으로 그 사실이 DB 에 남고, 백엔드가 document_type
과 함께 내려보내 화면에서 인용문과 구분해 그린다.

문서(source_document)·공시(disclosure) 행은 이미 DB 에 있다 — DART 수집기가 넣었다.
실측으로 지분 공시 33건 전부 DISCLOSURE 로 존재한다. 그래서 여기서는 문서를 만들지
않고 dart_receipt_no 로 찾아 붙이기만 한다.

company_document 는 (document_id, company_id) 로 document_evidence 의 FK 부모다.
공시 문서에는 지분을 **보유한 쪽**(source) 행이 없을 수 있어 같이 만든다.
mention_type 은 FILER 가 아니라 MENTION 이다 — 제출사가 아니라 공시에 언급된 기업이다.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = DATA / "db_invest_evidence_20260923"
MODEL_VERSION = "dart-ownership-gemini-2.5-flash-lite"
FORMULA_VERSION = "rel-v0.5-full"


def main() -> None:
    markets = {r["stock_code"]: r["market"]
               for r in csv.DictReader((HERE.parent / "ner/data/db/company.csv")
                                       .open(encoding="utf-8-sig"))}
    rows = json.loads((DATA / "invest_sentences.json").read_text(encoding="utf-8"))

    evidences, links, skipped = [], [], []
    for row in rows:
        receipt = row.get("receipt")
        src, dst = row["src"], row["dst"]
        if not receipt or src not in markets or dst not in markets:
            skipped.append((src, dst, "접수번호 또는 기업 없음"))
            continue
        # 근거 문장은 "보유한 쪽" 기업에 붙인다. 지분을 가진 주체가 그 회사이기 때문이다.
        evidence_id = str(uuid5(NAMESPACE_URL, f"cosmos:evidence:dart:{receipt}:{src}:{dst}"))
        evidences.append({
            "evidence_id": evidence_id, "dart_receipt_no": receipt,
            "market": markets[src], "stock_code": src,
            "sentence_text": row["sentence"], "sentence_order": 0,
            "confidence": 1.0, "model_version": MODEL_VERSION})
        links.append({
            "source_market": markets[src], "source_stock_code": src,
            "target_market": markets[dst], "target_stock_code": dst,
            "relationship_type_code": "INVEST", "dart_receipt_no": receipt,
            "evidence_id": evidence_id, "contribution_score": 1.0,
            "model_version": MODEL_VERSION, "formula_version": FORMULA_VERSION})

    OUT.mkdir(parents=True, exist_ok=True)

    def write(name: str, fields: list[str], values) -> None:
        with (OUT / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)

    write("document_evidence.csv",
          ["evidence_id", "dart_receipt_no", "market", "stock_code", "sentence_text",
           "sentence_order", "confidence", "model_version"], evidences)
    write("relationship_evidence.csv",
          ["source_market", "source_stock_code", "target_market", "target_stock_code",
           "relationship_type_code", "dart_receipt_no", "evidence_id",
           "contribution_score", "model_version", "formula_version"], links)

    print(f"{OUT}")
    print(f"  근거 문장 {len(evidences)} · 관계-근거 {len(links)}")
    if skipped:
        print(f"  건너뜀 {len(skipped)}: {skipped}")


if __name__ == "__main__":
    main()
