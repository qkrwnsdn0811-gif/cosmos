"""Markdown validation report for the impact model, rewritten on every `train-impact` run (spec §7)."""
from __future__ import annotations

from pathlib import Path

from .model import CLASSES, METHODS, MODEL_VERSION, Evaluation
from .sentiment import MODEL_ID

CLASS_KO = {"NEGATIVE": "하락", "NEUTRAL": "중립", "POSITIVE": "상승"}
BASIS_KO = {"REPORTED": "근거(기사 언급)", "INFERRED": "추정(공급망 전이)"}
LIMITATIONS = [
    "관계 그래프(동시언급 통계·근거 조각·COSMOS 관계)를 전체 기간 정보로 만들었다. 전파 점수와 간선 출처 특징에 "
    "미래 정보가 섞여 시험 성적이 부풀었을 수 있다 (slice1-run §3.4).",
    "지도에 보이는 학습 구간(2025년) 이벤트의 값은 학습에 쓰인 사례라 시험 성적보다 좋아 보일 수 있다. "
    "데모는 시험 구간(2026-07 이후) 이벤트로 한다.",
    "같은 날 같은 기업을 다룬 기사가 여러 건이면 어느 기사 때문에 주가가 움직였는지 구분할 수 없다. "
    "표본 가중치(1/기사 수)로 영향만 줄였다.",
    "감성 점수는 기사 전체(제목 + 본문 200자)의 감성이라 특정 기업에 대한 논조와 다를 수 있다.",
    "일별 주가에는 잡음이 많다. 중립 구간(±0.5σ)을 두고 macro-F1과 보정표를 함께 본다.",
]


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _tau_line(ev: Evaluation) -> str:
    if ev.tau_passed:
        return f"τ = {ev.tau:.2f} (검증 구간에서 항상 중립보다 10%p 이상 나은 가장 작은 값)"
    return f"τ = {ev.tau:.2f} (기준 미달: 항상 중립보다 10%p 이상 나은 기준을 찾지 못해 기본값을 씀)"


def _score_table(head: str, groups: dict[str, dict], names: dict[str, str] | None = None) -> list[str]:
    lines = [f"| {head} | 표본 수 | 정확도 | macro-F1 | log-loss |", "|---|---:|---:|---:|---:|"]
    for key, s in groups.items():
        label = (names or {}).get(key, key)
        lines.append(f"| {label} | {int(s.get('n', 0)):,} | {_num(s['accuracy'])} | {_num(s['macro_f1'])} | "
                     f"{_num(s['log_loss'])} |")
    return lines


def render_report(ev: Evaluation) -> str:
    lines = [
        "# NEXUS 영향 모델 검증 보고서",
        "",
        "> `nexus train-impact`가 생성한 파일이다. 다시 돌리면 덮어쓰므로 손으로 고치지 않는다.",
        "",
        "## 1. 실행 정보",
        "",
        f"- 생성 시각: {ev.generated_at.isoformat(timespec='seconds')}",
        f"- 데이터 기간(정답이 있는 이벤트 거래일): {ev.data_start} ~ {ev.data_end}",
        f"- 모델: `{MODEL_VERSION}` (HistGradientBoosting + 검증 구간 시그모이드 보정)",
        f"- 감성 모델: `{MODEL_ID}` 리비전 `{ev.sentiment_revision}`",
        "- 분할: 학습 ~ 2025-12-31, 검증 2026-01-01 ~ 2026-06-30, 시험 2026-07-01 ~",
        f"- {_tau_line(ev)}",
        "",
        "## 2. 표본",
        "",
        "비율은 표본 가중치(같은 기업·같은 거래일 표본이 n개면 각 1/n)를 반영했다.",
        "",
        "| 구간 | 표본 수 | " + " | ".join(CLASS_KO[c] for c in CLASSES) + " |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, counts in ev.split_counts.items():
        lines.append(f"| {name} | {int(counts['n']):,} | " + " | ".join(_pct(counts[c]) for c in CLASSES) + " |")
    lines += [
        "",
        "## 3. 시험 구간 비교",
        "",
        "정확도는 가중 정확도, macro-F1은 세 클래스 F1의 단순 평균, log-loss는 확률을 내는 방법만 계산한다(낮을수록 좋다).",
        "",
        "| 방법 | 정확도 | macro-F1 | log-loss |",
        "|---|---:|---:|---:|",
    ]
    for method in METHODS:
        s = ev.comparison[method]
        lines.append(f"| {method} | {_num(s['accuracy'])} | {_num(s['macro_f1'])} | {_num(s['log_loss'])} |")
    lines += ["", "## 4. 나눠 보기", "", "전체 모델, 시험 구간 기준.", "", "### 근거·추정별", ""]
    lines += _score_table("구분", ev.by_basis, BASIS_KO)
    lines += ["", "### 경로 첫 관계별 (근거 기업은 NONE)", ""]
    lines += _score_table("관계", ev.by_first_rel)
    lines += [
        "",
        "## 5. 표시 기준(τ)",
        "",
        f"- {_tau_line(ev)}",
        f"- 시험 구간에서 방향을 표시한 비율(커버리지): {_pct(ev.coverage)}",
        f"- 그 표본의 정확도: {_num(ev.covered_accuracy)}",
        "- 신뢰도가 τ 미만이면 지도에는 속이 빈 원(방향 보류)으로 표시한다.",
        "",
        "## 6. 보정표",
        "",
        "신뢰도가 정직하다면 '평균 신뢰도'와 '실제 정확도'가 비슷해야 한다.",
        "",
        "| 신뢰도 구간 | 표본 수 | 평균 신뢰도 | 실제 정확도 |",
        "|---|---:|---:|---:|",
    ]
    for label, n, mean_conf, accuracy in ev.calibration:
        lines.append(f"| {label} | {n:,} | {_num(mean_conf)} | {_num(accuracy)} |")
    lines += [
        "",
        "## 7. 중요한 입력",
        "",
        "시험 구간에서 입력 하나를 뒤섞었을 때 log-loss가 늘어난 정도(클수록 판단에 많이 쓰인 입력).",
        "",
        "| 순위 | 입력 | log-loss 증가 |",
        "|---:|---|---:|",
    ]
    for rank, (name, delta) in enumerate(ev.importance, 1):
        lines.append(f"| {rank} | `{name}` | {delta:.4f} |")
    lines += ["", "## 8. 한계", ""] + [f"- {item}" for item in LIMITATIONS] + [""]
    return "\n".join(lines)


def write_report(ev: Evaluation, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(ev), encoding="utf-8")
