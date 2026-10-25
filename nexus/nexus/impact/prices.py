"""Daily bars with a per-symbol Parquet cache that only fetches uncovered date ranges.

Only finished trading days are cached: bars dated today or later are dropped (today's candle is still
moving during market hours), coverage never extends past yesterday, and a fetch that returns nothing
does not count as covered (FDR returns an empty frame on network errors).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

BENCHMARK = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}   # NASDAQ: no abnormal returns in the first slice
KST = timezone(timedelta(hours=9))

Fetch = Callable[[str, date, date], pd.DataFrame]


def _fdr_fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
    import FinanceDataReader as fdr
    return fdr.DataReader(symbol, start.isoformat(), end.isoformat())


def _today_kst() -> date:
    return datetime.now(KST).date()


def _dates(frame: pd.DataFrame) -> list[date]:
    return [ts.date() for ts in pd.to_datetime(frame.index)]


class PriceCache:
    def __init__(self, root: Path, fetch: Fetch | None = None, today: Callable[[], date] = _today_kst):
        self.root = Path(root)
        self._fetch = fetch or _fdr_fetch
        self._today = today

    def _paths(self, symbol: str) -> tuple[Path, Path]:
        return self.root / f"{symbol}.parquet", self.root / f"{symbol}.coverage.json"

    def bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """FinanceDataReader-shaped frame (Open/High/Low/Close/Volume) indexed by trading date."""
        last_final = self._today() - timedelta(days=1)
        end = min(end, last_final)
        if end < start:
            return pd.DataFrame()
        data_path, cover_path = self._paths(symbol)
        frame = pd.read_parquet(data_path) if data_path.is_file() else pd.DataFrame()
        cover = json.loads(cover_path.read_text()) if cover_path.is_file() else None
        lo, hi = (date.fromisoformat(cover["start"]), date.fromisoformat(cover["end"])) if cover else (None, None)

        if cover is None:
            gaps = [(start, end)]
        else:
            gaps = []
            if start < lo:
                gaps.append((start, lo - timedelta(days=1)))
            if end > hi:
                gaps.append((hi + timedelta(days=1), end))
        fetched_any = False
        for gap_start, gap_end in gaps:
            part = self._fetch(symbol, gap_start, gap_end)
            if part is None or part.empty:
                continue
            part = part[[d <= last_final for d in _dates(part)]]
            fetched_any = True
            frame = part if frame.empty else pd.concat([frame, part])
            lo = gap_start if lo is None else min(lo, gap_start)
            hi = gap_end if hi is None else max(hi, gap_end)
        if fetched_any:
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
            self.root.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(data_path)
            cover_path.write_text(json.dumps({"start": lo.isoformat(), "end": hi.isoformat()}))
        if frame.empty:
            return frame
        days = _dates(frame)
        return frame[[start <= d <= end for d in days]]

    def closes(self, symbol: str, start: date, end: date) -> pd.Series:
        frame = self.bars(symbol, start, end)
        if frame.empty:
            return pd.Series(dtype=float)
        return pd.Series(frame["Close"].to_numpy(dtype=float), index=_dates(frame))
