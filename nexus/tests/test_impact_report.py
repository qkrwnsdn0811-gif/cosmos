from datetime import date, datetime, timezone

from nexus.impact.model import Evaluation
from nexus.impact.report import render_report, write_report

SCORES = {"accuracy": 0.612, "macro_f1": 0.455, "log_loss": 0.931}


def evaluation(passed=True):
    return Evaluation(
        generated_at=datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc), data_start=date(2025, 1, 2),
        data_end=date(2026, 10, 13), sentiment_revision="f858", tau=0.55 if passed else 0.6, tau_passed=passed,
        split_counts={k: {"n": 100, "NEGATIVE": 0.2, "NEUTRAL": 0.6, "POSITIVE": 0.2} for k in ("학습", "검증", "시험")},
        comparison={"항상 중립": {"accuracy": 0.5, "macro_f1": 0.222, "log_loss": None},
                    "규칙": {"accuracy": 0.52, "macro_f1": 0.3, "log_loss": None},
                    "감성만": {"accuracy": 0.55, "macro_f1": 0.35, "log_loss": 0.99}, "전체 모델": SCORES},
        by_basis={"REPORTED": {**SCORES, "n": 70}, "INFERRED": {**SCORES, "n": 30}},
        by_first_rel={"NONE": {**SCORES, "n": 70}, "SUPPLY": {**SCORES, "n": 30}},
        coverage=0.42, covered_accuracy=0.71,
        calibration=[("0.4–0.5", 40, 0.45, 0.43), ("0.5–0.6", 30, 0.55, 0.57)],
        importance=[("sent_pos", 0.031), ("path_sign", 0.012)])


def test_report_has_all_sections_and_table():
    text = render_report(evaluation())
    for head in ("# NEXUS 영향 모델 검증 보고서", "## 1. 실행 정보", "## 3. 시험 구간 비교", "## 6. 보정표", "## 8. 한계"):
        assert head in text
    assert "| 전체 모델 | 0.612 | 0.455 | 0.931 |" in text
    assert "| 항상 중립 | 0.500 | 0.222 | - |" in text
    assert "τ = 0.55" in text and "42.0%" in text and "sent_pos" in text


def test_report_marks_fallback_tau():
    assert "기준 미달" in render_report(evaluation(passed=False))


def test_write_report_creates_file(tmp_path):
    path = tmp_path / "docs" / "report.md"
    write_report(evaluation(), path)
    assert path.read_text(encoding="utf-8").startswith("# NEXUS 영향 모델 검증 보고서")

