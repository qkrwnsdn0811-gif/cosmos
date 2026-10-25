"""Past-case reactions (F3), direction labels (F4), their hit rate (F11) and the expected move size.

A case is one (trading day, company, news type, position) after an event: articles about the same company on
the same day with the same type count once (the abnormal return only depends on the company and the day).
Statistics come from the training period only (trading day <= TRAIN_END); events after it are scored with
those statistics and checked against what the price actually did, so the hit rate never sees its own answers.

A label is a summary of past cases, not a forecast: UP/DOWN only when there are at least MIN_CASES cases,
at least MIN_SHARE of them moved the same way and the mean abnormal return is large enough; otherwise HOLD
with the first condition that failed. The company's own cases are used when it has MIN_CASES of them,
otherwise every company in the same position.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable

import pandas as pd

from ..models import EventBundle, PathStep

WINDOWS = (1, 3, 5, 20)
HORIZONS = {"SHORT": 3, "LONG": 20}
TRAIN_END = date(2026, 6, 30)
MIN_CASES = 10
MIN_SHARE = 0.65
MIN_MEAN = {"SHORT": 0.01, "LONG": 0.02}

POSITIONS = {"REPORTED": "기사에 나온 기업", "SUPPLIER": "공급사", "CUSTOMER": "고객사", "COMPETITOR": "경쟁사",
             "PARTNER": "협력사", "INVEST": "지분 관계", "COMENTION": "함께 언급"}
HOLD_FEW = "사례 부족"
HOLD_SPLIT = "오른 경우와 내린 경우가 비슷함"
HOLD_SMALL = "반응이 작음"
HOLD_NO_TYPE = "비슷한 유형 없음"
HOLD_WEAK_LINK = "연결이 약함"
NO_TYPE = "OTHER"

AR_COLUMNS = [f"ar_{w}" for w in WINDOWS]
CASE_COLUMNS = ["news_id", "company_key", "trading_date", "news_type", "position", "status", *AR_COLUMNS]


def position_of(key: str, basis: str, path: tuple[PathStep, ...]) -> str:
    """Where the company sits relative to the company before it on the path (the reported company for one hop).
    SUPPLY edges point supplier → customer, so the company is the supplier when it is the edge's source."""
    if basis == "REPORTED" or not path:
        return "REPORTED"
    last = path[-1]
    if last.rel == "SUPPLY":
        return "SUPPLIER" if last.src == key else "CUSTOMER"
    return {"COMPETE": "COMPETITOR", "PARTNER": "PARTNER", "INVEST": "INVEST"}.get(last.rel, "COMENTION")


ReturnsFor = Callable[[str, date], dict[int, float]]


def case_frame(bundles: Iterable[EventBundle], news_types: dict[str, str], returns_for: ReturnsFor) -> pd.DataFrame:
    """One row per (event, company impact) with the event's type, the company's position and its abnormal returns
    (NaN where the window has no price yet). Rows without a trading day are skipped."""
    rows = []
    for b in bundles:
        if b.trading_date is None:
            continue
        news_type = news_types.get(b.article.news_id, NO_TYPE)
        for i in b.impacts:
            ars = returns_for(i.key, b.trading_date)
            rows.append((b.article.news_id, i.key, b.trading_date, news_type, position_of(i.key, i.basis, i.path),
                         i.status, *(ars.get(w, float("nan")) for w in WINDOWS)))
    return pd.DataFrame(rows, columns=CASE_COLUMNS)


def unique_cases(frame: pd.DataFrame) -> pd.DataFrame:
    """Usable cases: OK impacts, one per (day, company, type, position)."""
    ok = frame[frame["status"] == "OK"]
    return ok.drop_duplicates(["trading_date", "company_key", "news_type", "position"]).reset_index(drop=True)


def _summary(values: pd.Series) -> tuple[int, float, float, int, int]:
    v = values.dropna()
    if v.empty:
        return 0, float("nan"), float("nan"), 0, 0
    return int(len(v)), float(v.mean()), float(v.median()), int((v < 0).sum()), int((v > 0).sum())


STAT_COLUMNS = ["news_type", "position", "company_key", "window_days", "case_count", "mean_ar", "median_ar",
                "down_count", "up_count"]


def reaction_stats(cases: pd.DataFrame, train_end: date = TRAIN_END) -> pd.DataFrame:
    """Per (type, position) — company_key None — and per (type, position, company), for every window."""
    train = cases[cases["trading_date"] <= train_end]
    rows = []
    for keys, group in train.groupby(["news_type", "position"], sort=True):
        for w in WINDOWS:
            rows.append((*keys, None, w, *_summary(group[f"ar_{w}"])))
    for keys, group in train.groupby(["news_type", "position", "company_key"], sort=True):
        for w in WINDOWS:
            rows.append((keys[0], keys[1], keys[2], w, *_summary(group[f"ar_{w}"])))
    stats = pd.DataFrame(rows, columns=STAT_COLUMNS)
    return stats[stats["case_count"] > 0].reset_index(drop=True)


@dataclass(frozen=True)
class Signal:
    label: str              # UP | DOWN | HOLD
    hold_reason: str | None
    scope: str | None       # COMPANY | POSITION (None when there is nothing to compare with)
    case_count: int
    same_share: float | None   # share of cases that moved in the majority direction
    mean_ar: float | None
    down_share: float | None


class SignalBook:
    """Labels from reaction_stats; one lookup per (type, position, company, horizon)."""

    def __init__(self, stats: pd.DataFrame, *, min_cases: int = MIN_CASES, min_share: float = MIN_SHARE,
                 min_mean: dict[str, float] | None = None):
        self.min_cases, self.min_share, self.min_mean = min_cases, min_share, min_mean or MIN_MEAN
        self._by = {}
        for r in stats.itertuples(index=False):
            company = r.company_key if isinstance(r.company_key, str) else None
            self._by[(r.news_type, r.position, company, int(r.window_days))] = r

    def signal(self, news_type: str, position: str, company_key: str, horizon: str, *, status: str = "OK") -> Signal:
        if status != "OK":
            return Signal("HOLD", HOLD_WEAK_LINK, None, 0, None, None, None)
        if news_type == NO_TYPE:
            return Signal("HOLD", HOLD_NO_TYPE, None, 0, None, None, None)
        window = HORIZONS[horizon]
        own = self._by.get((news_type, position, company_key, window))
        row, scope = (own, "COMPANY") if own is not None and own.case_count >= self.min_cases else \
            (self._by.get((news_type, position, None, window)), "POSITION")
        if row is None or row.case_count == 0:
            return Signal("HOLD", HOLD_FEW, scope, 0, None, None, None)
        n = int(row.case_count)
        down, up = int(row.down_count), int(row.up_count)
        same = max(down, up) / n
        mean = float(row.mean_ar)
        base = dict(scope=scope, case_count=n, same_share=round(same, 6), mean_ar=round(mean, 8),
                    down_share=round(down / n, 6))
        if n < self.min_cases:
            return Signal("HOLD", HOLD_FEW, **base)
        if same < self.min_share:
            return Signal("HOLD", HOLD_SPLIT, **base)
        if abs(mean) < self.min_mean[horizon]:
            return Signal("HOLD", HOLD_SMALL, **base)
        if down > up and mean < 0:
            return Signal("DOWN", None, **base)
        if up > down and mean > 0:
            return Signal("UP", None, **base)
        return Signal("HOLD", HOLD_SPLIT, **base)


MOVE_GRADES = ("SMALL", "MID", "LARGE")


@dataclass(frozen=True)
class Move:
    expected: float     # mean |abnormal return| over the company's training-period news days
    case_count: int
    grade: str          # SMALL | MID | LARGE — the company's third among companies with a move for this horizon


class MoveBook:
    """Expected move size: how far the company usually moved after its news, whichever way.

    Direction is not predictable from these cases (hit rate below the majority baseline), size is: a company that
    moved a lot after past news keeps doing so. The news type is left out on purpose — per company and type did not
    rank the test period better than per company alone (2026-10-25 check). Each (day, company) counts once, since
    the abnormal return only depends on the company and the day. Grades split the companies into thirds.
    """

    def __init__(self, cases: pd.DataFrame, train_end: date = TRAIN_END, *, min_cases: int = MIN_CASES):
        days = cases[cases["trading_date"] <= train_end].drop_duplicates(["trading_date", "company_key"])
        self._by: dict[tuple[str, str], Move] = {}
        for horizon, window in HORIZONS.items():
            sizes = {}
            for key, group in days.groupby("company_key", sort=True):
                moves = group[f"ar_{window}"].dropna().abs()
                if len(moves) >= min_cases:
                    sizes[key] = (float(moves.mean()), int(len(moves)))
            ranked = sorted(sizes, key=lambda k: sizes[k][0])
            for rank, key in enumerate(ranked):
                grade = MOVE_GRADES[min(rank * len(MOVE_GRADES) // len(ranked), len(MOVE_GRADES) - 1)]
                self._by[(key, horizon)] = Move(round(sizes[key][0], 8), sizes[key][1], grade)

    def move(self, company_key: str, horizon: str) -> Move | None:
        return self._by.get((company_key, horizon))

    def items(self):
        return self._by.items()


MOVE_BACKTEST_COLUMNS = ["horizon", "grade", "case_count", "predicted_move", "realized_move"]


def move_backtest(cases: pd.DataFrame, book: MoveBook, train_end: date = TRAIN_END) -> pd.DataFrame:
    """Per grade, the expected move against the realized |abnormal return| of news days after train_end."""
    test = cases[cases["trading_date"] > train_end].drop_duplicates(["trading_date", "company_key"])
    rows = []
    for horizon, window in HORIZONS.items():
        col = f"ar_{window}"
        scored = [(m.grade, m.expected, abs(r)) for key, r in zip(test["company_key"], test[col])
                  if not pd.isna(r) and (m := book.move(key, horizon)) is not None]
        frame = pd.DataFrame(scored, columns=["grade", "predicted", "realized"])
        for grade in MOVE_GRADES:
            group = frame[frame["grade"] == grade]
            rows.append((horizon, grade, int(len(group)), float(group["predicted"].mean()) if len(group) else float("nan"),
                         float(group["realized"].mean()) if len(group) else float("nan")))
    return pd.DataFrame(rows, columns=MOVE_BACKTEST_COLUMNS)


BACKTEST_COLUMNS = ["horizon", "news_type", "case_count", "labeled_count", "hit_count", "up_labels", "down_labels",
                    "base_down_share"]


def backtest(cases: pd.DataFrame, book: SignalBook, train_end: date = TRAIN_END) -> pd.DataFrame:
    """Hit rate on cases after train_end: a labeled case hits when its realized abnormal return has the label's sign.
    news_type None is the all-types row. base_down_share is the share of realized drops among those cases."""
    test = cases[cases["trading_date"] > train_end]
    rows = []
    for horizon, window in HORIZONS.items():
        col = f"ar_{window}"
        scored = test[test[col].notna()]
        labels = [book.signal(r.news_type, r.position, r.company_key, horizon).label
                  for r in scored.itertuples(index=False)]
        frame = scored.assign(label=labels)
        for news_type, group in [(None, frame), *frame.groupby("news_type", sort=True)]:
            labeled = group[group["label"] != "HOLD"]
            hits = ((labeled["label"] == "UP") & (labeled[col] > 0)) | ((labeled["label"] == "DOWN") & (labeled[col] < 0))
            rows.append((horizon, news_type, int(len(group)), int(len(labeled)), int(hits.sum()),
                         int((labeled["label"] == "UP").sum()), int((labeled["label"] == "DOWN").sum()),
                         float((group[col] < 0).mean()) if len(group) else float("nan")))
    return pd.DataFrame(rows, columns=BACKTEST_COLUMNS)
