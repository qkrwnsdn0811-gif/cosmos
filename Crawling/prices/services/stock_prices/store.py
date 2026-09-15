"""Atomic per-company price snapshots. A failed refresh preserves good data."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import sqlite3
import time

from .loader import validate_rows


def json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=json_value, allow_nan=False)


def is_won_range_mismatch(row):
    """Quarantine the observed one-won source inconsistency without correcting it."""
    if row.get("market") != "KOSPI":
        return False
    prices = [row.get(field) for field in ("open_price", "high_price", "low_price", "close_price")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               and v > 0 and v == int(v) for v in prices):
        return False
    opened, high, low, closed = prices
    volume = row.get("trading_volume")
    return (isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume < 2**63
            and low <= high and max(0, opened - high, closed - high, low - opened, low - closed) == 1)


def is_incomplete_ohlcv(row):
    """Keep incomplete source fields for diagnosis without making a candle."""
    fields = ("open_price", "high_price", "low_price", "close_price", "trading_volume")
    if not all(field in row for field in (*fields, "adj_close")):
        return False
    missing = [row[field] is None for field in fields]
    if not any(missing) or all(missing):
        return False
    for field in (*fields[:4], "adj_close"):
        value = row[field]
        if value is not None and not (isinstance(value, (int, float)) and not isinstance(value, bool)
                                      and math.isfinite(value) and value > 0):
            return False
    volume = row["trading_volume"]
    return volume is None or (isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume < 2**63)


def is_inconsistent_ohlc(row):
    """Recognize observed historical KRW range errors; never repair their prices."""
    if row.get("market") != "KOSPI" or is_won_range_mismatch(row):
        return False
    prices = [row.get(field) for field in ("open_price", "high_price", "low_price", "close_price")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               and v > 0 and v == int(v) for v in prices):
        return False
    opened, high, low, closed = prices
    volume = row.get("trading_volume")
    return (isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume < 2**63
            and not (low <= opened <= high and low <= closed <= high))


def is_zero_ohl_with_volume(row):
    """Keep observed zero-OHL source rows distinct from zero-volume halts."""
    closed, volume = row.get("close_price"), row.get("trading_volume")
    return (row.get("market") == "KOSPI"
            and all(isinstance(row.get(k), (int, float)) and not isinstance(row.get(k), bool)
                    and row[k] == 0 for k in ("open_price", "high_price", "low_price"))
            and isinstance(closed, (int, float)) and not isinstance(closed, bool)
            and math.isfinite(closed) and closed > 0 and closed == int(closed)
            and isinstance(volume, int) and not isinstance(volume, bool) and 0 < volume < 2**63)


def is_supported_quarantine(row):
    """Use the same raw-data quarantine contract in the store and HDFS publisher."""
    flags = row.get("quality_flags", [])
    if flags == ["halted_ohl_zero"]:
        closed = row.get("close_price")
        return (all(isinstance(row.get(k), (int, float)) and not isinstance(row.get(k), bool)
                    and row[k] == 0 for k in ("open_price", "high_price", "low_price", "trading_volume"))
                and isinstance(closed, (int, float)) and not isinstance(closed, bool)
                and math.isfinite(closed) and closed > 0)
    if flags == ["ohlc_range_mismatch"]:
        return is_won_range_mismatch(row)
    if flags == ["source_inconsistent_ohlc"]:
        return is_inconsistent_ohlc(row)
    if flags == ["source_zero_ohl_with_volume"]:
        return is_zero_ohl_with_volume(row)
    if flags == ["source_missing_ohlcv"]:
        return all(field in row and row[field] is None for field in
                   ("open_price", "high_price", "low_price", "close_price", "trading_volume", "adj_close"))
    return flags == ["source_incomplete_ohlcv"] and is_incomplete_ohlcv(row)


class PriceStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS coverage (
              market TEXT, ticker TEXT, start_date TEXT, end_date TEXT,
              last_attempt REAL NOT NULL, last_success REAL, status TEXT, error TEXT,
              PRIMARY KEY(market,ticker));
            CREATE TABLE IF NOT EXISTS bars (
              market TEXT, ticker TEXT, trading_date TEXT, payload TEXT NOT NULL,
              PRIMARY KEY(market,ticker,trading_date));
            CREATE TABLE IF NOT EXISTS quarantine (
              market TEXT, ticker TEXT, trading_date TEXT, reason TEXT, payload TEXT NOT NULL,
              PRIMARY KEY(market,ticker,trading_date));
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def coverage(self, company):
        row = self.db.execute("SELECT * FROM coverage WHERE market=? AND ticker=?",
                              (company["market"], company["ticker"])).fetchone()
        return dict(row) if row else None

    def begin(self, company):
        with self.db:
            self.db.execute("""INSERT INTO coverage(market,ticker,last_attempt,status) VALUES(?,?,?,'running')
                ON CONFLICT(market,ticker) DO UPDATE SET last_attempt=excluded.last_attempt,status='running',error=NULL""",
                (company["market"], company["ticker"], time.time()))

    def failed(self, company, kind):
        # Error classes/categories only; no arbitrary network diagnostics or DSNs.
        with self.db:
            self.db.execute("UPDATE coverage SET status='failed',error=?,last_attempt=? WHERE market=? AND ticker=?",
                            (kind[:100], time.time(), company["market"], company["ticker"]))

    def replace(self, company, start, end, rows):
        if not rows:
            raise ValueError("empty_source_response")
        good, excluded, seen = [], [], set()
        for row in rows:
            if (row.get("market"), row.get("ticker")) != (company["market"], company["ticker"]):
                raise ValueError("source_symbol_mismatch")
            day = date.fromisoformat(row["trading_date"])
            if not start <= day <= end or day in seen:
                raise ValueError("source_date_out_of_range_or_duplicate")
            seen.add(day)
            flags = row.get("quality_flags", [])
            if is_supported_quarantine(row):
                excluded.append(row)
            elif flags:
                raise ValueError("unsupported_source_quality_flag")
            else:
                good.append(row)
        normalized = validate_rows(good)
        if not normalized:
            raise ValueError("no_chart_eligible_bars")
        old = self.coverage(company)
        if old and old["start_date"] and (start.isoformat() > old["start_date"] or end.isoformat() < old["end_date"]):
            raise ValueError("refresh_must_cover_retained_history")
        retained_dates = {row[0] for row in self.db.execute("""SELECT trading_date FROM bars WHERE market=? AND ticker=?
            UNION SELECT trading_date FROM quarantine WHERE market=? AND ticker=?""",
            (company["market"], company["ticker"], company["market"], company["ticker"]))}
        if retained_dates - {day.isoformat() for day in seen}:
            raise ValueError("partial_response_would_remove_retained_dates")
        with self.db:
            key = company["market"], company["ticker"]
            self.db.execute("DELETE FROM bars WHERE market=? AND ticker=?", key)
            self.db.execute("DELETE FROM quarantine WHERE market=? AND ticker=?", key)
            self.db.executemany("INSERT INTO bars VALUES(?,?,?,?)", [
                (*key, row["trading_date"], dumps({**row, "quality_flags": []})) for row in normalized])
            self.db.executemany("INSERT INTO quarantine VALUES(?,?,?,?,?)", [
                (*key, row["trading_date"], row["quality_flags"][0], dumps(row)) for row in excluded])
            self.db.execute("""UPDATE coverage SET start_date=?,end_date=?,last_success=?,status='complete',error=NULL
                WHERE market=? AND ticker=?""", (start.isoformat(), end.isoformat(), time.time(), *key))
        return {"rows": len(normalized), "quarantined_rows": len(excluded),
                "first_date": min(r["trading_date"] for r in normalized),
                "last_date": max(r["trading_date"] for r in normalized)}

    def rows(self, company, start, end):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM bars WHERE market=? AND ticker=? AND trading_date BETWEEN ? AND ? ORDER BY trading_date",
            (company["market"], company["ticker"], start.isoformat(), end.isoformat()))]

    def quarantine_count(self, company, start, end):
        return self.db.execute("SELECT count(*) FROM quarantine WHERE market=? AND ticker=? AND trading_date BETWEEN ? AND ?",
            (company["market"], company["ticker"], start.isoformat(), end.isoformat())).fetchone()[0]

    def quarantined_rows(self, company, start, end):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM quarantine WHERE market=? AND ticker=? AND trading_date BETWEEN ? AND ? ORDER BY trading_date",
            (company["market"], company["ticker"], start.isoformat(), end.isoformat()))]
