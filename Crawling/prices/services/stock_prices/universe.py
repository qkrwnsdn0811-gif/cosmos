"""Explicit market/ticker selection; symbols never determine their own market."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import calendar
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

MARKETS = ("KOSPI", "NASDAQ")
DEFAULT_UNIVERSE = Path(__file__).resolve().parents[2] / "config/universe.json"
HISTORY_FLOOR = date(1900, 1, 1)


def load_universe(path=DEFAULT_UNIVERSE):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    date.fromisoformat(payload["as_of"])
    result, seen = [], set()
    for row in payload["companies"]:
        market, ticker = row["market"], row["ticker"]
        pattern = r"[0-9A-Z]{6}" if market == "KOSPI" else r"[A-Z0-9][A-Z0-9.-]{0,29}"
        if market not in MARKETS or not isinstance(ticker, str) or not re.fullmatch(pattern, ticker):
            raise ValueError("invalid market/ticker in universe")
        if (market, ticker) in seen or not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ValueError("duplicate or unnamed company in universe")
        if not isinstance(row.get("default_selected"), bool):
            raise ValueError("default_selected must be a boolean")
        seen.add((market, ticker))
        result.append(dict(row))
    for market in MARKETS:
        if sum(r["market"] == market and r["default_selected"] for r in result) != 100:
            raise ValueError("default universe must select exactly 100 per market")
    return result


def select_companies(companies, markets=MARKETS, tickers=None, selection=None):
    markets = tuple(markets)
    if not markets or any(m not in MARKETS for m in markets) or len(set(markets)) != len(markets):
        raise ValueError("choose KOSPI and/or NASDAQ once each")
    available = {(c["market"], c["ticker"]): c for c in companies if c["market"] in markets}
    if selection is not None and tickers is not None:
        raise ValueError("use either tickers or a selection file")
    if selection is not None:
        if not isinstance(selection, list):
            raise ValueError("selection must be a list of market/ticker objects")
        keys = [(r["market"], r["ticker"]) for r in selection]
    elif tickers is not None:
        keys = []
        for ticker in tickers:
            matches = [key for key in available if key[1] == ticker]
            if len(matches) != 1:
                raise ValueError(f"unknown or ambiguous ticker: {ticker}")
            keys.append(matches[0])
    else:
        keys = [key for key, row in available.items() if row["default_selected"]]
    if not keys or len(set(keys)) != len(keys) or any(key not in available for key in keys):
        raise ValueError("selection must contain unique registered companies in the chosen markets")
    if any(sum(k[0] == market for k in keys) > 100 for market in MARKETS):
        raise ValueError("select at most 100 companies per market")
    return [available[key] for key in keys]


def completed_date(market, now=None):
    """Conservative final daily bar cutoff, including a provider update buffer.

    Weekends/holidays are filtered by the source, never filled with fake candles.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    zone, cutoff = ("Asia/Seoul", time(18)) if market == "KOSPI" else ("America/New_York", time(20))
    if market not in MARKETS:
        raise ValueError("unsupported market")
    local = now.astimezone(ZoneInfo(zone))
    return local.date() if local.time().replace(tzinfo=None) >= cutoff else local.date() - timedelta(days=1)


def date_range(market, *, years=None, start=None, end=None, now=None):
    ceiling = completed_date(market, now)
    end = date.fromisoformat(end) if isinstance(end, str) else end or ceiling
    if end > ceiling:
        raise ValueError(f"{market}: end must be <= {ceiling}; current daily bars are not final")
    if start is not None and years is not None:
        raise ValueError("choose either years or start")
    if start is None and years is None:
        start = HISTORY_FLOOR
    elif start is None:
        if not isinstance(years, int) or isinstance(years, bool) or not 1 <= years <= 100:
            raise ValueError("years must be 1..100")
        target_year = end.year - years
        start = date(target_year, end.month, min(end.day, calendar.monthrange(target_year, end.month)[1]))
    elif isinstance(start, str):
        start = date.fromisoformat(start)
    if start > end:
        raise ValueError("start must be <= end")
    if start < HISTORY_FLOOR:
        raise ValueError("start must be on or after 1900-01-01")
    return start, end
