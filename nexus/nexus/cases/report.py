"""Markdown report for past-case reactions and the direction labels' hit rate, rewritten on every `cases` run."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .newstype import LABELS
from .reaction import HORIZONS, MIN_CASES, MIN_MEAN, MIN_SHARE, POSITIONS

HORIZON_KO = {"SHORT": "단기(3거래일)", "LONG": "장기(20거래일)"}
SHOWN_POSITIONS = ("REPORTED", "SUPPLIER", "CUSTOMER", "COMPETITOR")


def _pct(value: float | None, digits: int = 1, sign: bool = False) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value * 100:+.{digits}f}%" if sign else f"{value * 100:.{digits}f}%"


def _reaction_table(stats: pd.DataFrame, window: int) -> list[str]:
    pooled = stats[stats["company_key"].isna() & (stats["window_days"] == window)]
    lines = ["| 뉴스 유형 | " + " | ".join(POSITIONS[p] for p in SHOWN_POSITIONS) + " |",
             "|---|" + "---:|" * len(SHOWN_POSITIONS)]
    order = pooled[pooled["position"] == "REPORTED"].sort_values("case_count", ascending=False)["news_type"].tolist()
    for news_type in order:
        cells = []
        for position in SHOWN_POSITIONS:
            row = pooled[(pooled["news_type"] == news_type) & (pooled["position"] == position)]
            if row.empty:
                cells.append("-")
                continue
            r = row.iloc[0]
            cells.append(f"{_pct(r.mean_ar, sign=True)} · 하락 {_pct(r.down_count / r.case_count, 0)} · {int(r.case_count)}건")
        lines.append(f"| {LABELS.get(news_type, news_type)} | " + " | ".join(cells) + " |")
    return lines


def _backtest_table(backtest: pd.DataFrame) -> list[str]:
    lines = ["| 기간 | 뉴스 유형 | 시험 사례 | 딱지 붙음 | 맞음 | 적중률 | '항상 상승' 기준선 | '항상 하락' 기준선 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in backtest.itertuples(index=False):
        news_type = r.news_type if isinstance(r.news_type, str) else None
        if news_type is not None and r.labeled_count < 20:
            continue
        hit = r.hit_count / r.labeled_count if r.labeled_count else None
        lines.append(f"| {HORIZON_KO[r.horizon]} | {LABELS.get(news_type, '전체') if news_type else '**전체**'} | "
                     f"{r.case_count:,} | {r.labeled_count:,} | {r.hit_count:,} | {_pct(hit)} | "
                     f"{_pct(1 - r.base_down_share)} | {_pct(r.base_down_share)} |")
    return lines


def write_report(path: Path, *, types: Counter, stats: pd.DataFrame, backtest: pd.DataFrame, cases: int,
                 train_end: date, test_end: date | None, now: datetime) -> None:
    total = sum(types.values())
    overall = backtest[backtest["news_type"].isna()]
    verdict = []
    for r in overall.itertuples(index=False):
        hit = r.hit_count / r.labeled_count if r.labeled_count else None
        base = max(r.base_down_share, 1 - r.base_down_share)
        verdict.append(f"- {HORIZON_KO[r.horizon]}: 딱지 {r.labeled_count:,}건 중 {_pct(hit)}가 맞았다. 무조건 많은 쪽"
                       f"(상승 또는 하락)으로 답하는 기준선은 {_pct(base)}다.")
    lines = [
        "# 과거 사례 반응표와 방향 딱지 적중률",
        "",
        f"`python -m nexus cases --chain semis` 가 실행할 때마다 다시 쓴다. 마지막 실행: {now:%Y-%m-%d %H:%M} (KST).",
        "",
        "## 방법",
        "",
        "- 뉴스 유형: 제목(가중치 3)과 본문 앞 200자(가중치 1)의 키워드 규칙 (`nexus/nexus/cases/newstype.py`).",
        "- 기업 위치: 기사에 나온 기업, 또는 추정 경로 마지막 단계의 관계(공급사·고객사·경쟁사·협력사).",
        "- 초과수익률: 종목 수익률 − 시장 지수(코스피 또는 코스닥) 수익률. 기준가는 뉴스 거래일 전날 종가, "
        "기간은 뉴스 거래일을 1일로 센 1·3·5·20거래일.",
        "- 사례: 같은 날 같은 기업·같은 유형·같은 위치는 1건으로 센다. 전파 단계에서 판단 보류된 기업은 사례에서 뺀다.",
        f"- 통계 기간은 {train_end} 까지의 뉴스, 적중률은 그 뒤 뉴스" + (f"({test_end} 까지)" if test_end else "") + "로 잰다.",
        f"- 딱지 규칙(명세서 초안, 시험 결과를 보기 전에 정한 값): 사례 {MIN_CASES}건 이상, 같은 방향 {MIN_SHARE:.0%} 이상, "
        f"평균 초과수익률 단기 {MIN_MEAN['SHORT']:.0%}·장기 {MIN_MEAN['LONG']:.0%} 이상. 그 종목 사례가 {MIN_CASES}건 이상이면 "
        "종목 사례를, 아니면 같은 위치 기업 전체 사례를 쓴다.",
        "",
        "## 뉴스 유형 분포",
        "",
        "| 유형 | 기사 수 | 비율 |",
        "|---|---:|---:|",
        *[f"| {LABELS.get(code, code)} | {n:,} | {n / total:.1%} |" for code, n in types.most_common()],
        "",
        f"사례(중복 제거): {cases:,}건",
        "",
    ]
    for horizon, window in HORIZONS.items():
        lines += [f"## 과거 반응: {HORIZON_KO[horizon]} 평균 초과수익률 · 하락 비율 · 사례 수 (통계 기간)", "",
                  *_reaction_table(stats, window), ""]
    lines += [
        "## 적중률 (통계 기간 뒤 뉴스)",
        "",
        *_backtest_table(backtest),
        "",
        "## 해석",
        "",
        *verdict,
        "- 통계 기간(2025-01 ~ 2026-06)은 반도체 강세장이라 거의 모든 유형에서 평균 초과수익률이 양수다. "
        "그래서 딱지는 대부분 '상승 우세'이고, 유형별 차이보다 시장 흐름을 더 많이 담는다.",
        "- 실적 부진처럼 평균이 음수인 유형도 있지만 사례가 적다. 유형 × 위치 단위 딱지가 새 뉴스의 방향을 맞힌다는 근거는 아직 없다.",
        "- 그래서 서비스는 딱지를 '과거 사례 경향'으로만 쓰고, 화면에 이 적중률과 기준선을 함께 보여 준다. 예측이 아니다.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
