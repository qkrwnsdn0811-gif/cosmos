"""Impact direction model: gradient boosting + probability calibration + a direction-hold threshold τ.

Train on events up to 2025-12, calibrate and pick τ on 2026-01~06, report on 2026-07 onward (spec §3.1, §4).
Direction = most likely class, confidence = its probability, magnitude = |p_up − p_down|. Below τ the
direction is withheld (stored as null) while confidence and magnitude are kept.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import accuracy_score, f1_score, log_loss

from .features import feature_columns

CLASSES = ("NEGATIVE", "NEUTRAL", "POSITIVE")
TRAIN_END = date(2025, 12, 31)
VALID_END = date(2026, 6, 30)
TAU_GRID = tuple(round(0.40 + 0.05 * i, 2) for i in range(11))
TAU_MIN_SAMPLES = 200
TAU_MARGIN = 0.10
TAU_FALLBACK = 0.6
MODEL_VERSION = "impact-hgb-v1"
SENTIMENT_COLUMNS = ["sent_neg", "sent_neu", "sent_pos"]
METHODS = ("항상 중립", "규칙", "감성만", "전체 모델")
SPLITS = ("학습", "검증", "시험")
IMPORTANCE_ROWS = 20_000
IMPORTANCE_REPEATS = 3
EPS = 1e-9


def _labels(frame: pd.DataFrame) -> np.ndarray:
    return np.asarray(frame["label"], dtype=object)


def split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    labeled = frame[frame["label"].notna() & frame["trading_date"].notna()]
    days = labeled["trading_date"]
    train = labeled[days.map(lambda d: d <= TRAIN_END)]
    valid = labeled[days.map(lambda d: TRAIN_END < d <= VALID_END)]
    test = labeled[days.map(lambda d: d > VALID_END)]
    return train, valid, test


def sample_weights(frame: pd.DataFrame) -> np.ndarray:
    """1 / (rows sharing the same company and trading day), so a few heavily covered names don't dominate."""
    if frame.empty:
        return np.zeros(0)
    size = frame.groupby(["company_key", "trading_date"], dropna=False)["company_key"].transform("size")
    return 1.0 / size.to_numpy(dtype=float)


def fit_calibrated(train: pd.DataFrame, valid: pd.DataFrame, columns: list[str]) -> CalibratedClassifierCV:
    hgb = HistGradientBoostingClassifier(learning_rate=0.05, max_iter=500, early_stopping=True,
                                         validation_fraction=0.1, l2_regularization=1.0, random_state=0,
                                         categorical_features="from_dtype")
    hgb.fit(train[columns], _labels(train), sample_weight=sample_weights(train))
    calibrated = CalibratedClassifierCV(FrozenEstimator(hgb), method="sigmoid")
    with warnings.catch_warnings():
        # The frozen model is never refit, so weights belong to the calibration step only — which is the intent.
        warnings.filterwarnings("ignore", message="Since FrozenEstimator does not appear to accept sample_weight")
        calibrated.fit(valid[columns], _labels(valid), sample_weight=sample_weights(valid))
    return calibrated


def predict_proba(model, frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    """(n, 3) probabilities in CLASSES order; a class the model never saw gets 0."""
    out = np.zeros((len(frame), len(CLASSES)))
    if frame.empty:
        return out
    proba = model.predict_proba(frame[columns])
    classes = list(model.classes_)
    for j, c in enumerate(CLASSES):
        if c in classes:
            out[:, j] = proba[:, classes.index(c)]
    return out


def _argmax(proba: np.ndarray) -> np.ndarray:
    return np.asarray(CLASSES, dtype=object)[proba.argmax(axis=1)] if len(proba) else np.zeros(0, dtype=object)


def choose_tau(proba: np.ndarray, y, weights) -> tuple[float, bool]:
    """Smallest τ whose confident subset (≥ 200 rows) beats 'always neutral' on that subset by ≥ 10 points."""
    y, w = np.asarray(y, dtype=object), np.asarray(weights, dtype=float)
    conf, pred = proba.max(axis=1), _argmax(proba)
    for tau in TAU_GRID:
        m = conf >= tau - EPS
        if m.sum() < TAU_MIN_SAMPLES or w[m].sum() <= 0:
            continue
        total = w[m].sum()
        accuracy = (w[m] * (pred[m] == y[m])).sum() / total
        neutral = (w[m] * (y[m] == "NEUTRAL")).sum() / total
        if accuracy - neutral >= TAU_MARGIN - EPS:
            return tau, True
    return TAU_FALLBACK, False


def outputs(proba: np.ndarray, tau: float) -> list[tuple[str | None, float, float]]:
    result = []
    for row in proba:
        j = int(np.argmax(row))
        conf = float(row[j])
        magnitude = abs(float(row[2]) - float(row[0]))
        result.append((CLASSES[j] if conf >= tau - EPS else None, round(conf, 6), round(magnitude, 6)))
    return result


def rule_baseline(frame: pd.DataFrame) -> np.ndarray:
    """Article sentiment sign × path sign (competitor flips it); missing sentiment counts as neutral."""
    sent = frame[SENTIMENT_COLUMNS].to_numpy(dtype=float)
    missing = np.isnan(sent).any(axis=1)
    sent_sign = np.array([-1, 0, 1])[np.nan_to_num(sent, nan=-1.0).argmax(axis=1)] if len(sent) else np.zeros(0)
    sign = np.where(missing, 0, sent_sign) * frame["path_sign"].fillna(0).to_numpy(dtype=float)
    return np.where(sign > 0, "POSITIVE", np.where(sign < 0, "NEGATIVE", "NEUTRAL")).astype(object)


def scores(y_true, y_pred, weights, proba: np.ndarray | None = None) -> dict[str, float | None]:
    y_true, y_pred = np.asarray(y_true, dtype=object), np.asarray(y_pred, dtype=object)
    w = np.asarray(weights, dtype=float)
    if len(y_true) == 0 or w.sum() <= 0:
        return {"accuracy": None, "macro_f1": None, "log_loss": None}
    return {
        "accuracy": float(accuracy_score(y_true, y_pred, sample_weight=w)),
        "macro_f1": float(f1_score(y_true, y_pred, labels=list(CLASSES), average="macro", sample_weight=w,
                                   zero_division=0)),
        "log_loss": None if proba is None else float(log_loss(y_true, proba, labels=list(CLASSES), sample_weight=w)),
    }


@dataclass
class Evaluation:
    generated_at: datetime
    data_start: date | None
    data_end: date | None
    sentiment_revision: str
    tau: float
    tau_passed: bool
    split_counts: dict[str, dict[str, float]]
    comparison: dict[str, dict]
    by_basis: dict[str, dict]
    by_first_rel: dict[str, dict]
    coverage: float | None
    covered_accuracy: float | None
    calibration: list[tuple[str, int, float, float]]
    importance: list[tuple[str, float]]


@dataclass
class ImpactModel:
    estimator: object
    columns: list[str]
    vocab: list[str]
    tau: float
    tau_passed: bool

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> "ImpactModel":
        # Written by `train-impact` into the git-ignored local nexus/data directory, never received from outside.
        return joblib.load(path)


def _split_counts(parts: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    counts = {}
    for name, part in parts.items():
        w, y = sample_weights(part), _labels(part)
        total = w.sum()
        counts[name] = {"n": float(len(part)),
                        **{c: (float(w[y == c].sum() / total) if total else 0.0) for c in CLASSES}}
    return counts


def _grouped(keys: np.ndarray, y, pred, w, proba) -> dict[str, dict]:
    out = {}
    for key in sorted(set(keys)):
        m = keys == key
        out[str(key)] = {**scores(y[m], pred[m], w[m], proba[m]), "n": int(m.sum())}
    return out


def _calibration(conf: np.ndarray, correct: np.ndarray, w: np.ndarray) -> list[tuple[str, int, float, float]]:
    rows = []
    for k in range(3, 10):
        lo, hi = k / 10, (k + 1) / 10
        m = (conf >= lo) & ((conf < hi) if k < 9 else (conf <= 1.0 + EPS))
        if not m.any() or w[m].sum() <= 0:
            continue
        rows.append((f"{lo:.1f}–{hi:.1f}", int(m.sum()), float(np.average(conf[m], weights=w[m])),
                     float(np.average(correct[m], weights=w[m]))))
    return rows


def _importance(model, test: pd.DataFrame, columns: list[str]) -> list[tuple[str, float]]:
    """Permutation importance: mean increase in weighted log-loss when one column is shuffled."""
    if test.empty:
        return []
    sample = test.sample(min(len(test), IMPORTANCE_ROWS), random_state=0).reset_index(drop=True)
    y, w = _labels(sample), sample_weights(sample)
    base = log_loss(y, predict_proba(model, sample, columns), labels=list(CLASSES), sample_weight=w)
    rng = np.random.default_rng(0)
    result = []
    for col in columns:
        losses = []
        for _ in range(IMPORTANCE_REPEATS):
            shuffled = sample.copy()
            shuffled[col] = sample[col].iloc[rng.permutation(len(sample))].reset_index(drop=True)
            losses.append(log_loss(y, predict_proba(model, shuffled, columns), labels=list(CLASSES), sample_weight=w))
        result.append((col, float(np.mean(losses) - base)))
    return sorted(result, key=lambda r: r[1], reverse=True)[:10]


def train_and_evaluate(frame: pd.DataFrame, vocab: list[str], *, sentiment_revision: str,
                       now: datetime) -> tuple[ImpactModel, Evaluation]:
    columns = feature_columns(vocab)
    train, valid, test = split(frame)
    model = fit_calibrated(train, valid, columns)
    tau, passed = choose_tau(predict_proba(model, valid, columns), _labels(valid), sample_weights(valid))

    y, w = _labels(test), sample_weights(test)
    proba = predict_proba(model, test, columns)
    pred = _argmax(proba)
    sentiment_model = fit_calibrated(train, valid, SENTIMENT_COLUMNS)
    sentiment_proba = predict_proba(sentiment_model, test, SENTIMENT_COLUMNS)
    comparison = {
        "항상 중립": scores(y, np.full(len(y), "NEUTRAL", dtype=object), w),
        "규칙": scores(y, rule_baseline(test), w),
        "감성만": scores(y, _argmax(sentiment_proba), w, sentiment_proba),
        "전체 모델": scores(y, pred, w, proba),
    }
    conf = proba.max(axis=1) if len(proba) else np.zeros(0)
    covered = conf >= tau - EPS
    labeled_days = pd.concat([train, valid, test])["trading_date"]
    evaluation = Evaluation(
        generated_at=now,
        data_start=min(labeled_days) if len(labeled_days) else None,
        data_end=max(labeled_days) if len(labeled_days) else None,
        sentiment_revision=sentiment_revision, tau=tau, tau_passed=passed,
        split_counts=_split_counts(dict(zip(SPLITS, (train, valid, test)))),
        comparison=comparison,
        by_basis=_grouped(np.asarray(test["basis"], dtype=object), y, pred, w, proba),
        by_first_rel=_grouped(np.asarray(test["first_rel"].astype(str), dtype=object), y, pred, w, proba),
        coverage=float(w[covered].sum() / w.sum()) if w.sum() else None,
        covered_accuracy=float(np.average(pred[covered] == y[covered], weights=w[covered])) if w[covered].sum() else None,
        calibration=_calibration(conf, (pred == y).astype(float), w),
        importance=_importance(model, test, columns),
    )
    return ImpactModel(model, columns, list(vocab), tau, passed), evaluation
