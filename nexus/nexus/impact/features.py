"""Feature table for the impact model — one row per (event, company) the model may score.

Rows: impact rows with status OK whose company trades on KOSPI/KOSDAQ (spec §3.1, §5.4). Company identity is
never a feature. Everything is known at release time: the buzz ratio counts only articles before the
article, prices stop at the close before t0. Missing sentiment, prices or trading day leave NaN, not errors.
"""
from __future__ import annotations

import bisect
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from ..models import Company, EventBundle, PathStep
from ..relations.activation import active_relations
from .labels import PriceContext, label_for

KST = timezone(timedelta(hours=9))
MARKET_CLOSE = time(15, 30)

REL_VALUES = ("NONE", "SUPPLY", "PARTNER", "COMPETE", "INVEST", "CO_MENTIONED")
SOURCE_VALUES = ("NONE", "NEWS", "COSMOS", "COMENTION")
ROLE_VALUES = ("설계", "제조", "장비", "소재", "부품", "후공정", "고객")
MARKET_VALUES = ("KOSPI", "KOSDAQ")
CATEGORICAL = {"first_rel": REL_VALUES, "last_rel": REL_VALUES, "edge_source": SOURCE_VALUES,
               "role": ROLE_VALUES, "market": MARKET_VALUES}
BASE_FEATURES = ["sent_neg", "sent_neu", "sent_pos", "has_incident", "rel_supply", "rel_partner", "rel_compete",
                 "rel_invest", "n_reported", "after_close", "is_inferred", "hop", "score", "in_title", "in_content",
                 "in_orgs", "first_rel", "last_rel", "path_sign", "edge_source", "role", "market", "buzz_ratio",
                 "ret_5d", "vol_60"]
META_COLUMNS = ["news_id", "company_key", "basis", "trading_date", "z", "label"]
REL_SIGN = {"SUPPLY": 1, "PARTNER": 1, "INVEST": 1, "COMPETE": -1, "CO_MENTIONED": 0}
DAY = 86400.0


def feature_columns(vocab: list[str]) -> list[str]:
    return BASE_FEATURES + [f"cat:{c}" for c in vocab]


def path_sign(path: tuple[PathStep, ...]) -> int:
    sign = 1
    for step in path:
        sign *= REL_SIGN.get(step.rel, 0)
    return sign


def after_close(dateline: datetime, calendar: set[date], last_day: date | None) -> int:
    """1 when the market cannot react the same day: released at/after 15:30 KST or on a non-trading day.
    Beyond the price calendar (newest articles) only weekends count as non-trading."""
    local = dateline.astimezone(KST)
    if local.time() >= MARKET_CLOSE:
        return 1
    day = local.date()
    if last_day is not None and day <= last_day:
        return int(day not in calendar)
    return int(day.weekday() >= 5)


class MentionTimeline:
    """Release times of articles mentioning each company, for the 24-hour vs 30-day buzz ratio."""

    def __init__(self, bundles: list[EventBundle]):
        times: dict[str, list[float]] = {}
        for b in bundles:
            ts = b.article.dateline.timestamp()
            for key in {m.key for m in b.mentions}:
                times.setdefault(key, []).append(ts)
        self._times = {k: sorted(v) for k, v in times.items()}

    def buzz_ratio(self, key: str, at: datetime) -> float:
        times = self._times.get(key, [])
        ts = at.timestamp()
        hi = bisect.bisect_left(times, ts)               # strictly before the article itself
        c24 = hi - bisect.bisect_left(times, ts - DAY)
        c30 = hi - bisect.bisect_left(times, ts - 30 * DAY)
        return c24 / (c30 / 30 + 1)


def category_vocab(bundles: list[EventBundle], min_share: float = 0.01) -> list[str]:
    if not bundles:
        return []
    counts = Counter(c for b in bundles for c in set(b.article.categories))
    return sorted(c for c, n in counts.items() if n / len(bundles) >= min_share)


def _sentiment_lookup(sentiment: pd.DataFrame) -> dict[str, tuple[float, float, float]]:
    if sentiment is None or sentiment.empty:
        return {}
    return {str(nid): (float(neg), float(neu), float(pos))
            for nid, neg, neu, pos in zip(sentiment.index, sentiment["p_neg"], sentiment["p_neu"], sentiment["p_pos"])}


def _num(value: float | None) -> float:
    return np.nan if value is None else float(value)


def build_frame(bundles: list[EventBundle], companies: dict[str, Company], sentiment: pd.DataFrame,
                prices: dict[str, PriceContext], vocab: list[str], timeline: MentionTimeline,
                calendar: list[date]) -> pd.DataFrame:
    sent = _sentiment_lookup(sentiment)
    cal, last_day = set(calendar), (max(calendar) if calendar else None)
    rows = []
    for b in bundles:
        a = b.article
        neg, neu, pos = sent.get(a.news_id, (np.nan, np.nan, np.nan))
        rels = active_relations(a)
        cats = set(a.categories)
        article_part = {
            "sent_neg": neg, "sent_neu": neu, "sent_pos": pos, "has_incident": float(bool(a.incidents)),
            "rel_supply": float("SUPPLY" in rels), "rel_partner": float("PARTNER" in rels),
            "rel_compete": float("COMPETE" in rels), "rel_invest": float("INVEST" in rels),
            "n_reported": float(sum(i.basis == "REPORTED" for i in b.impacts)),
            "after_close": float(after_close(a.dateline, cal, last_day)),
            **{f"cat:{c}": float(c in cats) for c in vocab},
        }
        sources = {m.key: m.sources for m in b.mentions}
        t0 = b.trading_date
        for i in b.impacts:
            company = companies.get(i.key)
            if i.status != "OK" or company is None or company.market not in MARKET_VALUES:
                continue
            reported = i.basis == "REPORTED"
            src = sources.get(i.key, ()) if reported else ()
            ctx = prices.get(i.key)
            z = ctx.z(t0) if ctx is not None and t0 is not None else None
            rows.append({
                "news_id": a.news_id, "company_key": i.key, "basis": i.basis, "trading_date": t0,
                "z": _num(z), "label": label_for(z),
                **article_part,
                "is_inferred": float(not reported), "hop": float(i.hop), "score": float(i.score),
                "in_title": float("title" in src), "in_content": float("content" in src), "in_orgs": float("orgs" in src),
                "first_rel": i.path[0].rel if i.path else "NONE", "last_rel": i.path[-1].rel if i.path else "NONE",
                "path_sign": float(path_sign(i.path)),
                "edge_source": i.path[0].source if i.path else "NONE",
                "role": company.role, "market": company.market,
                "buzz_ratio": timeline.buzz_ratio(i.key, a.dateline),
                "ret_5d": _num(ctx.ret_5d(t0) if ctx is not None and t0 is not None else None),
                "vol_60": _num(ctx.sigma(t0) if ctx is not None and t0 is not None else None),
            })
    columns = META_COLUMNS + feature_columns(vocab)
    frame = pd.DataFrame(rows, columns=columns)
    for col in feature_columns(vocab):
        if col in CATEGORICAL:
            frame[col] = pd.Categorical(frame[col], categories=list(CATEGORICAL[col]))
        else:
            frame[col] = frame[col].astype(float)
    frame["z"] = frame["z"].astype(float)
    return frame
