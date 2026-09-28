"""company_document 의 relevance_score / impact_score / confidence 를 채운다.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe fill_company_document_scores.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe fill_company_document_scores.py --emit-sql

왜 주가 지도학습이 아니라 텍스트 규칙인가 — measure_news_salience.py 의 실측
────────────────────────────────────────────────────────────────────────────
뉴스 KG 특징과 |잔차 수익률| 의 Spearman 은 전부 0 근처였다 (n_sent +0.0067,
n_comp +0.0062, first_pos -0.0018, 감성 -0.018~+0.005). test rank-IC 는 기업별
평균 변동성만 쓴 기준선 +0.2056 에 뉴스 특징을 더하면 +0.2032 로 **떨어졌다**.
시각이 정확한 표본만 봐도 -0.0015 였으므로 세션 정렬 탓이 아니다.
방향 예측은 이전에 이미 실패했다 (GNN 39.51% < 기준선 40.82%).

즉 주가로는 relevance 를 만들 수 없다. 그리고 애초에 relevance 는 "이 기사가 이
기업 얘기인가" 라는 **텍스트의 성질**이지 시장의 성질이 아니다. 배경에 이름만 나온
기사는 그날 주가가 어떻든 그 기업 기사가 아니다.

공식
────────────────────────────────────────────────────────────────────────────
  depth = (min(n_sent, 4) - 1) / 3     그 기업을 다룬 근거 문장 수. p99 가 4라 4에서 자른다
  share = 1 / n_comp                   한 기사가 여러 기업을 다루면 각자의 비중이 준다
  relevance_score = 0.25 + 0.55*depth + 0.20*share      (0.26 ~ 1.00)
  impact_score    = +1 / -1 / 0        POSITIVE / NEGATIVE / NEUTRAL
  confidence      = min(1.0, 0.40 + 0.15*n_sent)        근거가 많을수록 높다

first_pos 는 안 쓴다. 247,337건 중 246,514건이 0이라 정보가 없다.

한계 — 반드시 같이 읽을 것
────────────────────────────────────────────────────────────────────────────
- n_sent 는 1~8 이고 99%가 4 이하다. 해상도가 낮아 상위권 변별력이 약하다.
- n_comp 는 **우리 202종목 안의 기업만** 센다. 유니버스 밖 기업이 주인공인 기사는
  관련 기업이 하나뿐인 것처럼 보여 share 가 과대평가된다 (DB하이텍 기사가 그 예다).
- 감성이 NULL 인 72,800건은 impact 를 못 만든다. 그 행은 그대로 NULL 로 남는다.
  이건 FinBERT 를 안 거친 관계추출 파이프라인 산출물이며 별도 작업이다.
- 이 값은 규칙 파생값이지 학습된 모델 출력이 아니다. model_version 에 그렇게 적는다.
  나중에 모델이 기준선을 넘으면 같은 컬럼을 덮어쓰면 된다.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "news_features.csv"
OUT = HERE / "data" / "company_document_scores.csv"
MODEL_VERSION = "rule-salience-v1"

SIGN = {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0}


def build(df: pd.DataFrame) -> pd.DataFrame:
    depth = (df["n_sent"].clip(1, 4) - 1) / 3.0
    share = 1.0 / df["n_comp"].clip(lower=1)
    out = pd.DataFrame({
        "document_id": df["document_id"],
        "company_id": df["company_id"],
        "relevance_score": (0.25 + 0.55 * depth + 0.20 * share).round(6),
        "impact_score": df["sentiment"].map(SIGN),
        "confidence": (0.40 + 0.15 * df["n_sent"]).clip(upper=1.0).round(6),
    })
    # 감성이 없으면 영향 부호를 만들 수 없다. 억지로 0 을 넣지 않고 NULL 로 남긴다.
    out.loc[df["sentiment"].isna(), ["impact_score", "confidence"]] = pd.NA
    out.loc[df["sentiment"].isna(), "relevance_score"] = pd.NA
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC, type=Path)
    ap.add_argument("--out", default=OUT, type=Path)
    args = ap.parse_args()

    df = pd.read_csv(args.src, dtype={"ticker": str})
    out = build(df)
    ok = out["relevance_score"].notna()

    print(f"입력 {len(df):,}행")
    print(f"  채움   {ok.sum():,}")
    print(f"  NULL   {(~ok).sum():,}  (감성 없음 — FinBERT 미적용 행)")

    sub = out[ok]
    print(f"\nrelevance_score  최소 {sub['relevance_score'].min():.3f} "
          f"중앙 {sub['relevance_score'].median():.3f} 최대 {sub['relevance_score'].max():.3f}")
    print(f"impact_score     {dict(sub['impact_score'].value_counts())}")

    # 프론트 표시 점수 = relevance * |impact| * 100 (lib/newsImpact.ts)
    shown = (sub["relevance_score"] * sub["impact_score"].abs() * 100).round()
    nonzero = shown[shown > 0]
    print(f"\n화면 점수 = relevance x |impact| x 100")
    print(f"  0점(중립이라 제외)  {(shown == 0).sum():,}")
    print(f"  점수 있음           {len(nonzero):,}   "
          f"중앙 {nonzero.median():.0f}  범위 {nonzero.min():.0f}~{nonzero.max():.0f}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"\n-> {args.out}")
    print(f"model_version = {MODEL_VERSION} (규칙 파생값, 학습 모델 아님)")


if __name__ == "__main__":
    main()
