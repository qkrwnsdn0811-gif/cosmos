from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from nexus.impact.features import CATEGORICAL, META_COLUMNS, feature_columns
from nexus.impact.model import (METHODS, ImpactModel, choose_tau, outputs, predict_proba, rule_baseline,
                                sample_weights, scores, split, train_and_evaluate)

VOCAB = ["경제>반도체"]


def synthetic_frame(n=3000, seed=0):
    """감성(긍정−부정) × 경로 부호에 신호를 심은 데이터. 2025-01 ~ 2026-09에 고르게 퍼뜨린다."""
    rng = np.random.default_rng(seed)
    pos = rng.uniform(0, 1, n)
    neg = rng.uniform(0, 1 - pos)
    sign = rng.choice([1, -1], n, p=[0.8, 0.2])
    signal = (pos - neg) * sign + rng.normal(0, 0.15, n)
    label = np.where(signal > 0.25, "POSITIVE", np.where(signal < -0.25, "NEGATIVE", "NEUTRAL"))
    days = [date(2025, 1, 1) + timedelta(days=int(d)) for d in rng.integers(0, 630, n)]
    f = pd.DataFrame({c: 0.0 for c in feature_columns(VOCAB)}, index=range(n))
    f["sent_pos"], f["sent_neg"], f["sent_neu"], f["path_sign"] = pos, neg, 1 - pos - neg, sign
    f["is_inferred"] = (sign == -1).astype(float)
    for col, values in CATEGORICAL.items():
        f[col] = pd.Categorical([values[0]] * n, categories=list(values))
    f["news_id"] = [f"N{i}" for i in range(n)]
    f["company_key"] = [f"KOSPI:{i % 50}" for i in range(n)]
    f["basis"] = np.where(sign == -1, "INFERRED", "REPORTED")
    f["trading_date"], f["z"], f["label"] = days, signal, label
    return f[META_COLUMNS + feature_columns(VOCAB)]


def test_split_by_trading_date():
    train, valid, test = split(synthetic_frame())
    assert train["trading_date"].max() <= date(2025, 12, 31)
    assert valid["trading_date"].min() >= date(2026, 1, 1) and valid["trading_date"].max() <= date(2026, 6, 30)
    assert test["trading_date"].min() >= date(2026, 7, 1)


def test_sample_weights_one_over_group_size():
    f = pd.DataFrame({"company_key": ["A", "A", "A", "B"], "trading_date": [date(2026, 1, 2)] * 3 + [date(2026, 1, 2)]})
    assert list(sample_weights(f)) == pytest.approx([1 / 3, 1 / 3, 1 / 3, 1.0])


def test_model_beats_always_neutral_on_signal():
    model, ev = train_and_evaluate(synthetic_frame(), VOCAB, sentiment_revision="r", now=datetime(2026, 10, 16, tzinfo=timezone.utc))
    full, neutral = ev.comparison["전체 모델"], ev.comparison["항상 중립"]
    assert set(ev.comparison) == set(METHODS)
    assert full["macro_f1"] > neutral["macro_f1"] + 0.2 and full["accuracy"] > neutral["accuracy"]
    assert neutral["log_loss"] is None and full["log_loss"] is not None
    assert model.tau in [round(0.40 + 0.05 * i, 2) for i in range(11)]
    assert set(ev.split_counts) == {"학습", "검증", "시험"}


def test_choose_tau_picks_smallest_passing_value():
    wrong = np.tile([0.30, 0.25, 0.45], (300, 1))                       # 신뢰도 0.45, 상승 예측, 정답 중립
    right = np.tile([0.10, 0.10, 0.80], (300, 1))                       # 신뢰도 0.80, 상승 예측, 정답 상승
    y = ["NEUTRAL"] * 300 + ["POSITIVE"] * 300
    assert choose_tau(np.vstack([wrong, right]), y, np.ones(600)) == (0.5, True)


def test_choose_tau_needs_200_samples_and_falls_back():
    right = np.tile([0.10, 0.10, 0.80], (150, 1))
    assert choose_tau(right, ["POSITIVE"] * 150, np.ones(150)) == (0.6, False)
    neutral = np.tile([0.10, 0.80, 0.10], (500, 1))
    assert choose_tau(neutral, ["NEUTRAL"] * 500, np.ones(500)) == (0.6, False)


def test_outputs_hide_direction_below_tau():
    assert outputs(np.array([[0.2, 0.3, 0.5], [0.1, 0.1, 0.8]]), 0.6) == [(None, 0.5, 0.3), ("POSITIVE", 0.8, 0.7)]


def test_rule_baseline():
    f = pd.DataFrame({"sent_neg": [0.8, 0.1, 0.1, np.nan], "sent_neu": [0.1, 0.1, 0.8, np.nan],
                      "sent_pos": [0.1, 0.8, 0.1, np.nan], "path_sign": [-1, 1, 1, 1]})
    assert list(rule_baseline(f)) == ["POSITIVE", "POSITIVE", "NEUTRAL", "NEUTRAL"]


def test_scores_use_weights_and_survive_missing_class():
    assert scores(["POSITIVE", "NEGATIVE"], ["POSITIVE", "POSITIVE"], [3, 1])["accuracy"] == pytest.approx(0.75)
    s = scores(["NEUTRAL", "POSITIVE"], ["NEUTRAL", "NEUTRAL"], [1, 1], proba=np.array([[0.1, 0.8, 0.1], [0.1, 0.6, 0.3]]))
    assert np.isfinite(s["macro_f1"]) and np.isfinite(s["log_loss"])


def test_save_and_load_give_same_predictions(tmp_path):
    frame = synthetic_frame()
    model, _ = train_and_evaluate(frame, VOCAB, sentiment_revision="r", now=datetime(2026, 10, 16, tzinfo=timezone.utc))
    model.save(tmp_path / "m" / "model.joblib")
    loaded = ImpactModel.load(tmp_path / "m" / "model.joblib")
    np.testing.assert_allclose(predict_proba(loaded.estimator, frame, loaded.columns),
                               predict_proba(model.estimator, frame, model.columns))
    assert (loaded.tau, loaded.vocab) == (model.tau, model.vocab)

