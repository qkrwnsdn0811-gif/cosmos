"""INVEST 관계의 지분 공시를 한 문장으로 요약한다 (GMS · Gemini 2.5 Flash-Lite).

  PYTHONUTF8=1 python summarize_invest_disclosures.py            # 전체 40건
  PYTHONUTF8=1 python summarize_invest_disclosures.py --limit 3  # 시험

입력
  data/edges_ownership.csv   DART 지분 44행 (src, dst, weight, evidence)
  data/dart_meta.csv         접수번호 -> 보고서명·공시일·제출사·원문 URL
  ../ner/data/db/company.csv 티커 -> 회사명
  .gms_key                   API 키 (gitignore)

산출
  data/invest_sentences.json

════════════════════════════════════════════════════════════════════════════
왜 LLM 을 쓰는가 — 그리고 어디까지만 맡기는가
════════════════════════════════════════════════════════════════════════════
INVEST 관계는 뉴스가 아니라 DART 지분 공시에서 나오므로 인용할 기사 문장이 없다.
화면에는 "사업보고서 (2025.12)" 같은 보고서명만 띄울 수 있는데, 그것만으로는
무슨 관계인지 읽히지 않는다.

그래서 **설명 문장만** 모델에 맡긴다. 숫자와 회사명은 우리가 주고, 모델이 돌려준
문장에 그 값들이 **글자 그대로 들어 있는지 확인**한다(_verify). 하나라도 어긋나면
그 응답을 버리고 템플릿 문장으로 대체한다.

지분율을 11.69 -> 11.7 로 반올림하거나 회사명을 바꿔 쓰는 순간 금융 정보로서
신뢰를 잃는다. 검증을 통과하지 못한 문장은 화면에 올리지 않는다.

한 번만 돌리고 DB 에 저장한다. 조회할 때마다 부르지 않는다 (40건 고정).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ENDPOINT = ("https://gms.ssafy.io/gmsapi/generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.5-flash-lite:generateContent")
MODEL_VERSION = "dart-ownership-gemini-2.5-flash-lite"

PROMPT = """다음은 한국 금융감독원 DART 에 공시된 지분 보유 사실입니다.

- 보유 회사: {src}
- 대상 회사: {dst}
- 지분율: {pct}%
- 관계 구분: {kind}
- 근거 문서: {report} ({filer} 제출, {filed} 공시)

이 사실을 주식 투자를 막 시작한 사람도 바로 이해할 수 있게 **한 문장**으로 써 주세요.

규칙:
- 지분율은 반드시 {pct}% 로, 회사명은 반드시 "{src}" 와 "{dst}" 로 적습니다. 바꾸지 마세요.
- 주가 전망, 투자 판단, 추측을 넣지 마세요. 공시에 적힌 사실만 씁니다.
- 한 문장, 100자 이내, 존댓말로 끝냅니다.
- 문장만 출력하세요. 따옴표나 머리말을 붙이지 마세요."""


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def template(src: str, dst: str, pct: str, kind: str) -> str:
    """검증 실패 시 쓰는 문장. 틀릴 수 없지만 읽히지는 않는다."""
    return f"{src}가 {dst}의 {kind}로서 지분 {pct}%를 보유하고 있습니다."


def verify(sentence: str, src: str, dst: str, pct: str) -> str | None:
    """숫자와 회사명이 글자 그대로 있는지. 하나라도 없으면 쓰지 않는다."""
    if not sentence or len(sentence) > 200:
        return "길이"
    for token in (src, dst, pct):
        if token not in sentence:
            return f"'{token}' 없음"
    # 우리가 준 것 말고 다른 퍼센트 숫자가 끼어들면 버린다
    others = [p for p in re.findall(r"\d+(?:\.\d+)?(?=\s*%)", sentence) if p != pct]
    if others:
        return f"다른 지분율 {others}"
    return None


def ask(key: str, prompt: str, timeout: int = 30) -> str:
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return payload["candidates"][0]["content"]["parts"][0]["text"].strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-pct", type=float, default=0.05, help="법정 신고 기준")
    args = ap.parse_args()

    key_file = HERE / ".gms_key"
    if not key_file.exists():
        sys.exit(f"API 키 파일이 없다: {key_file}")
    key = key_file.read_text(encoding="utf-8").strip()

    names = {r["stock_code"]: r["name"]
             for r in read_csv(HERE.parent / "ner/data/db/company.csv")}
    meta = {r["dart_receipt_no"]: r for r in read_csv(DATA / "dart_meta.csv")}

    rows = []
    for edge in read_csv(DATA / "edges_ownership.csv"):
        src, dst = edge["src_ticker"], edge["dst_ticker"]
        if src not in names or dst not in names or float(edge["weight"]) < args.min_pct:
            continue
        receipt = re.search(r"dart:(\d{14})", edge["evidence"])
        kind = re.search(r"dart:\d{14};([^;]*)", edge["evidence"])
        rows.append({"src": src, "dst": dst,
                     "src_name": names[src], "dst_name": names[dst],
                     "pct": f"{float(edge['weight']) * 100:.2f}".rstrip("0").rstrip("."),
                     "kind": (kind[1].strip() if kind else "주요 주주") or "주요 주주",
                     "receipt": receipt[1] if receipt else None})
    if args.limit:
        rows = rows[:args.limit]

    out, fallback = [], 0
    for i, row in enumerate(rows, 1):
        info = meta.get(row["receipt"], {})
        prompt = PROMPT.format(src=row["src_name"], dst=row["dst_name"], pct=row["pct"],
                               kind=row["kind"], report=info.get("report_name", "지분 공시"),
                               filer=info.get("filer", "-"), filed=info.get("filing_date", "-"))
        sentence, problem = None, "호출 실패"
        for attempt in range(2):
            try:
                candidate = ask(key, prompt).replace("\n", " ").strip().strip('"')
            except (urllib.error.URLError, KeyError, IndexError, TimeoutError) as error:
                problem = f"{type(error).__name__}"
                time.sleep(1)
                continue
            problem = verify(candidate, row["src_name"], row["dst_name"], row["pct"])
            if problem is None:
                sentence = candidate
                break
        if sentence is None:
            sentence = template(row["src_name"], row["dst_name"], row["pct"], row["kind"])
            fallback += 1
        out.append(row | {"sentence": sentence, "generated": problem is None,
                          "report_name": info.get("report_name"),
                          "filing_date": info.get("filing_date"),
                          "original_url": info.get("original_url"),
                          "model_version": MODEL_VERSION})
        print(f"[{i}/{len(rows)}] {row['src_name']} -> {row['dst_name']} "
              f"{'' if problem is None else '(템플릿: ' + str(problem) + ') '}{sentence}",
              flush=True)

    path = DATA / "invest_sentences.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{path}\n  {len(out)}건 · 모델 문장 {len(out) - fallback} · 템플릿 대체 {fallback}")


if __name__ == "__main__":
    main()
