"""감성이 비어 있는 71,977쌍에 FinBERT 를 돌린다.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe infer_missing_sentiment.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe infer_missing_sentiment.py --limit 2000   # 시험

왜 이 행들만 비어 있나
────────────────────────────────────────────────────────────────────────────
서비스 스냅샷의 감성 174,537건은 kg-target-context-finbert-canonical-v1 이 만들었다.
비어 있는 72,800건은 grounded-integrated-relation-v3 — 관계추출이 붙인 기업-기사 쌍이라
감성 배치를 거치지 않았다. 감성이 없으면 impact_score 의 부호를 만들 수 없고,
프론트(lib/newsImpact.ts)가 그 기업을 통째로 건너뛴다.

입력이 기존 배치와 다르다 — 반드시 구분할 것
────────────────────────────────────────────────────────────────────────────
기존 배치는 기사 원문의 title+lead 를 넣었다. 여기는 **근거 문장**만 넣는다. 본문이
HDFS 에만 있고 DB 에 없기 때문이다. 같은 모델이어도 입력 범위가 달라 값의 기준이
미묘하게 다르다. 그래서 model_version 을 따로 쓴다 (MODEL_VERSION 참조).

귀속 가드 — 중립을 지어내지 않는다
────────────────────────────────────────────────────────────────────────────
kg_target_sentiment 의 원칙을 그대로 따른다.
  - 문장에 대상 기업명이 없으면 버린다.
  - 문장에 다른 유니버스 기업명이 있으면 버린다 (누구에 대한 감성인지 모름).
  - 한 쌍의 문장들이 서로 다른 라벨을 내면 NULL 이다. 다수결로 밀지 않는다.
모호하면 NULL 이다. 없는 값을 채우는 것보다 비어 있는 게 낫다.
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import re
import sys
from pathlib import Path

import pandas as pd

from lake_sentiment import LABELS, label_indexes

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "sentiment_inputs.csv"
OUT = HERE / "data" / "sentiment_filled.csv"
ALIASES = HERE.parent / "ner" / "data" / "aliases.csv"
MODEL_VERSION = "evidence-sentence-finbert-v1"
BATCH = 64
HANGUL = re.compile(r"[가-힣]")


def _name_pattern(name):
    # 공백 변형과 대소문자만 허용한다. 영문은 경계를 붙여 'Appleton' 이 'Apple' 로 잡히지 않게 한다.
    parts = re.split(r"\s+", name.strip())
    pattern = r"\s+".join(re.escape(x) for x in parts)
    if re.match(r"[A-Za-z0-9]", name):
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if re.search(r"[A-Za-z0-9]$", name):
        pattern += r"(?![A-Za-z0-9])"
    return re.compile(pattern, flags=re.IGNORECASE)


def _surface(name, text):
    """문장에 그 기업명이 글자 그대로 나오는가. 문장부호는 지우지 않는다."""
    return _name_pattern(name).search(text) is not None


def norm(s: str) -> str:
    return re.sub(r"[()（）\[\]주식회사㈜\s\.,'\"-]", "", s or "").lower()


COMPANIES = HERE.parent / "ner" / "data" / "companies.csv"


def load_alias_by_name() -> dict[str, set[str]]:
    """정식 기업명 -> 그 기업의 표기 변형들.

    company_document 는 company_id 만 주므로 정식명으로 잇는다. 별칭이 없으면
    정식명 하나만 쓴다 — 매칭이 느슨해지는 것보다 문장을 버리는 편이 낫다.
    """
    # aliases.csv 가 alias 와 name_official 을 같은 행에 들고 있어 한 번에 묶을 수 있다.
    # companies.csv 는 컬럼이 name 이 아니라 name_official 이라 예전에 전부 빗나갔다.
    by_official = collections.defaultdict(set)
    with ALIASES.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            a = (r.get("alias") or "").strip()
            official = (r.get("name_official") or "").strip()
            if official and len(a) >= 2:
                by_official[official].add(a)
    return {k: v | {k} for k, v in by_official.items()}


class Classifier:
    def __init__(self, path: Path, device: str):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        self.device = device
        torch.set_num_threads(min(8, max(1, os.cpu_count() or 1)))
        self.tok = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            str(path), local_files_only=True).to(device).eval()
        idx = label_indexes(self.model.config.to_dict())
        if set(idx) != set(LABELS):
            raise ValueError(f"라벨 집합 불일치: {idx}")
        self.order = [idx[l] for l in LABELS]     # negative, neutral, positive 순

    def predict(self, texts: list[str]) -> list[str]:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), BATCH):
                chunk = texts[i:i + BATCH]
                enc = self.tok(chunk, truncation=True, max_length=128,
                               padding=True, return_tensors="pt").to(self.device)
                logits = self.model(**enc).logits[:, self.order]
                out += [LABELS[j].upper() for j in logits.argmax(1).tolist()]
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC, type=Path)
    ap.add_argument("--out", default=OUT, type=Path)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    df = pd.read_csv(args.src)
    if args.limit:
        keep = df.groupby(["document_id", "company_id"]).ngroup() < args.limit
        df = df[keep]
    df["sentence_text"] = df["sentence_text"].fillna("")

    # 가드는 kg_target_sentiment 의 것을 그대로 쓴다. 직접 만들면 공백을 지워
    # '삼성' 이 '삼성전자' 안에서 걸리고 '반도체' 같은 별칭까지 매칭돼 97% 가 버려진다.


    alias = load_alias_by_name()
    names_of = lambda n: alias.get(n, {n} if n else set())
    name_of = dict(zip(df["company_id"], df["company_name"]))
    # '다른 기업' 은 전체 유니버스가 아니라 **같은 기사에 붙은 다른 기업**이다 (원본과 동일).
    doc_companies = df.groupby("document_id")["company_id"].apply(set).to_dict()

    kept, dropped = [], collections.Counter()
    for row in df.itertuples(index=False):
        text = row.sentence_text or ""
        if not any(_surface(n, text) for n in names_of(row.company_name)):
            dropped["대상 기업명 없음"] += 1
            continue
        others = doc_companies.get(row.document_id, set()) - {row.company_id}
        if any(_surface(n, text) for o in others for n in names_of(name_of.get(o, ""))):
            dropped["같은 문장에 다른 기업"] += 1
            continue
        kept.append(row)
    print(f"문장 {len(df):,} -> 가드 통과 {len(kept):,} | 제외 {dict(dropped)}")
    if not kept:
        print("남은 문장이 없다.")
        return

    ko = [r for r in kept if HANGUL.search(r.sentence_text or "")]
    en = [r for r in kept if not HANGUL.search(r.sentence_text or "")]
    print(f"한국어 {len(ko):,} / 영어 {len(en):,}")

    preds = {}
    for name, rows in (("sentiment_ko", ko), ("sentiment_en", en)):
        if not rows:
            continue
        print(f"  {name} 추론 중 ({len(rows):,}문장)...", flush=True)
        clf = Classifier(HERE / "local_models" / name, args.device)
        labels = clf.predict([r.sentence_text for r in rows])
        for r, lab in zip(rows, labels):
            preds.setdefault((r.document_id, r.company_id), []).append(lab)
        del clf

    # 한 쌍 안에서 라벨이 갈리면 NULL. 다수결로 밀지 않는다.
    rows, conflict = [], 0
    for (doc, comp), labs in preds.items():
        uniq = set(labs)
        if len(uniq) > 1:
            conflict += 1
            continue
        rows.append({"document_id": doc, "company_id": comp,
                     "sentiment": uniq.pop(), "n_sent": len(labs),
                     "model_version": MODEL_VERSION})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out, index=False)
    dist = collections.Counter(r["sentiment"] for r in rows)
    print(f"\n확정 {len(rows):,}쌍  {dict(dist)}")
    print(f"라벨 충돌로 NULL 유지 {conflict:,}쌍")
    print(f"-> {args.out}")
    print(f"model_version = {MODEL_VERSION} (입력이 근거 문장이라 기존 배치와 구분한다)")


if __name__ == "__main__":
    main()
