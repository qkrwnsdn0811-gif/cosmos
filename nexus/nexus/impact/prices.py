"""Daily bars with a per-symbol Parquet cache that only fetches uncovered date ranges."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

BENCHMARK = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}   # NASDAQ: no abnormal returns in the first slice

Fetch = Callable[[str, date, date], pd.DataFrame]


def _fdr_fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
    import FinanceDataReader as fdr
    return fdr.DataReader(symbol, start.isoformat(), end.isoformat())


class PriceCache:
    def __init__(self, root: Path, fetch: Fetch | None = None):
        self.root = Path(root)
        self._fetch = fetch or _fdr_fetch

    def _paths(self, symbol: str) -> tuple[Path, Path]:
        return self.root / f"{symbol}.parquet", self.root / f"{symbol}.coverage.json"

    def bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """FinanceDataReader-shaped frame (Open/High/Low/Close/Volume) indexed by trading date."""
        data_path, cover_path = self._paths(symbol)
        frame = pd.read_parquet(data_path) if data_path.is_file() else pd.DataFrame()
        cover = json.loads(cover_path.read_text()) if cover_path.is_file() else None
        lo, hi = (date.fromisoformat(cover["start"]), date.fromisoformat(cover["end"])) if cover else (None, None)

        gaps = [(start, end)] if cover is None else []
        if cover is not None and start < lo:
            gaps.append((start, lo - timedelta(days=1)))
        if cover is not None and end > hi:
            gaps.append((hi + timedelta(days=1), end))
        if gaps:
            parts = [frame] + [self._fetch(symbol, s, e) for s, e in gaps]
            frame = pd.concat([p for p in parts if not p.empty])
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
            self.root.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(data_path)
            new_lo = min(start, lo) if lo else start
            new_hi = max(end, hi) if hi else end
            cover_path.write_text(json.dumps({"start": new_lo.isoformat(), "end": new_hi.isoformat()}))
        if frame.empty:
            return frame
        days = pd.Index([ts.date() for ts in pd.to_datetime(frame.index)])
        mask = (days >= start) & (days <= end)
        return frame[mask]

    def closes(self, symbol: str, start: date, end: date) -> pd.Series:
        frame = self.bars(symbol, start, end)
        if frame.empty:
            return pd.Series(dtype=float)
        return pd.Series(frame["Close"].to_numpy(dtype=float), index=[ts.date() for ts in pd.to_datetime(frame.index)])
