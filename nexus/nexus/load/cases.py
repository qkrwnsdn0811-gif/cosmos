"""Write the investor view (V12·V13 tables): news types, abnormal returns for every window, past-case statistics,
per-event direction labels, their hit rate, expected move sizes and their check, and the market index closes the
price chart compares against.

Every writer replaces one value chain's rows, so `nexus cases` can be rerun. Run it after `load`: `load` rewrites
each event's abnormal returns with its own windows (1, 3) and would drop the 5 and 20 trading-day rows.
"""
from __future__ import annotations

import math
from datetime import date
from uuid import UUID

import pandas as pd

from ..models import AbnormalReturn
from .postgres import event_id


def _num(value, digits: int):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), digits)


def write_news_types(conn, value_chain: str, rows: list[tuple[str, str, float]]) -> int:
    """rows: (news_id, type code, confidence)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pg_temp.news_type_in")
        cur.execute("CREATE TEMP TABLE news_type_in (event_id UUID, event_type VARCHAR(50), confidence NUMERIC(7, 6)) "
                    "ON COMMIT DROP")
        with cur.copy("COPY news_type_in (event_id, event_type, confidence) FROM STDIN") as copy:
            for news_id, code, confidence in rows:
                copy.write_row((event_id(news_id), code, round(confidence, 6)))
        cur.execute("UPDATE news_event e SET event_type = t.event_type, event_type_confidence = t.confidence "
                    "FROM news_type_in t WHERE e.event_id = t.event_id AND e.value_chain = %s", (value_chain,))
        return cur.rowcount


def write_feed_times(conn, value_chain: str, times: dict, mains: dict, company_ids: dict[str, UUID]) -> int:
    """times: news_id → feed time (cases.feed.feed_times), mains: news_id → main company key (cases.feed.main_companies).
    The first screen's feed sorts by the time, newest first, and lists the main company first among the article's."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pg_temp.feed_in")
        cur.execute("CREATE TEMP TABLE feed_in (event_id UUID, feed_at TIMESTAMPTZ, main_company_id UUID) ON COMMIT DROP")
        with cur.copy("COPY feed_in (event_id, feed_at, main_company_id) FROM STDIN") as copy:
            for news_id, feed_at in times.items():
                copy.write_row((event_id(news_id), feed_at, company_ids.get(mains.get(news_id) or "")))
        cur.execute("UPDATE news_event e SET feed_at = f.feed_at, main_company_id = f.main_company_id FROM feed_in f "
                    "WHERE e.event_id = f.event_id AND e.value_chain = %s", (value_chain,))
        return cur.rowcount


def write_abnormal_returns(conn, rows: list[tuple[str, UUID, AbnormalReturn]]) -> int:
    """rows: (news_id, company_id, return). Upserts, so the existing 1 and 3 trading-day rows stay valid."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pg_temp.ar_in")
        cur.execute("CREATE TEMP TABLE ar_in (event_id UUID, company_id UUID, window_days SMALLINT, benchmark VARCHAR(20), "
                    "stock_return NUMERIC(12, 8), benchmark_return NUMERIC(12, 8), abnormal_return NUMERIC(12, 8)) "
                    "ON COMMIT DROP")
        with cur.copy("COPY ar_in FROM STDIN") as copy:
            for news_id, company_id, r in rows:
                copy.write_row((event_id(news_id), company_id, r.window_days, r.benchmark, round(r.stock_return, 8),
                                round(r.benchmark_return, 8), round(r.abnormal_return, 8)))
        cur.execute(
            "INSERT INTO event_abnormal_return (event_id, company_id, window_days, benchmark, stock_return, "
            "benchmark_return, abnormal_return) SELECT a.event_id, a.company_id, a.window_days, a.benchmark, "
            "a.stock_return, a.benchmark_return, a.abnormal_return FROM ar_in a "
            "JOIN event_company_impact i ON i.event_id = a.event_id AND i.company_id = a.company_id "
            "ON CONFLICT (event_id, company_id, window_days) DO UPDATE SET benchmark = EXCLUDED.benchmark, "
            "stock_return = EXCLUDED.stock_return, benchmark_return = EXCLUDED.benchmark_return, "
            "abnormal_return = EXCLUDED.abnormal_return")
        return cur.rowcount


def write_stats(conn, value_chain: str, stats: pd.DataFrame, company_ids: dict[str, UUID], *,
                period_start: date, period_end: date) -> int:
    rows = []
    for r in stats.itertuples(index=False):
        company = r.company_key if isinstance(r.company_key, str) else None
        if company is not None and company not in company_ids:
            continue
        rows.append((value_chain, r.news_type, r.position, company_ids.get(company) if company else None,
                     int(r.window_days), int(r.case_count), _num(r.mean_ar, 8), _num(r.median_ar, 8),
                     int(r.down_count), int(r.up_count), period_start, period_end))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM case_reaction_stat WHERE value_chain = %s", (value_chain,))
        with cur.copy("COPY case_reaction_stat (value_chain, news_type, position, company_id, window_days, case_count, "
                      "mean_ar, median_ar, down_count, up_count, period_start, period_end) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
    return len(rows)


def write_signals(conn, value_chain: str, rows: list[tuple]) -> int:
    """rows: (news_id, company_id, horizon, position, label, hold_reason, scope, case_count, same_share, mean_ar,
    down_share). Replaces this value chain's labels."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM event_company_signal s USING news_event e WHERE e.event_id = s.event_id "
                    "AND e.value_chain = %s", (value_chain,))
        cur.execute("DROP TABLE IF EXISTS pg_temp.signal_in")
        cur.execute("CREATE TEMP TABLE signal_in (LIKE event_company_signal INCLUDING DEFAULTS) ON COMMIT DROP")
        with cur.copy("COPY signal_in (event_id, company_id, horizon, position, label, hold_reason, case_scope, "
                      "case_count, same_share, mean_ar, down_share) FROM STDIN") as copy:
            for news_id, *rest in rows:
                copy.write_row((event_id(news_id), *rest))
        cur.execute("INSERT INTO event_company_signal SELECT s.* FROM signal_in s "
                    "JOIN event_company_impact i ON i.event_id = s.event_id AND i.company_id = s.company_id "
                    "ON CONFLICT DO NOTHING")
        return cur.rowcount


def write_backtest(conn, value_chain: str, frame: pd.DataFrame, *, train_end: date, test_start: date,
                   test_end: date) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM signal_backtest WHERE value_chain = %s", (value_chain,))
        for r in frame.itertuples(index=False):
            news_type = r.news_type if isinstance(r.news_type, str) else None
            cur.execute(
                "INSERT INTO signal_backtest (value_chain, horizon, news_type, case_count, labeled_count, hit_count, "
                "up_labels, down_labels, base_down_share, train_end, test_start, test_end) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (value_chain, r.horizon, news_type, int(r.case_count), int(r.labeled_count), int(r.hit_count),
                 int(r.up_labels), int(r.down_labels), _num(r.base_down_share, 6), train_end, test_start, test_end))
    return len(frame)


def write_moves(conn, value_chain: str, book, company_ids: dict[str, UUID], *, period_end: date) -> int:
    """book: reaction.MoveBook. Replaces this value chain's expected moves."""
    rows = [(value_chain, company_ids[key], horizon, m.expected, m.case_count, m.grade, period_end)
            for (key, horizon), m in book.items() if key in company_ids]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM company_expected_move WHERE value_chain = %s", (value_chain,))
        with cur.copy("COPY company_expected_move (value_chain, company_id, horizon, expected_move, case_count, grade, "
                      "period_end) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
    return len(rows)


def write_move_backtest(conn, value_chain: str, frame: pd.DataFrame, *, train_end: date, test_start: date,
                        test_end: date) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM move_backtest WHERE value_chain = %s", (value_chain,))
        for r in frame.itertuples(index=False):
            cur.execute(
                "INSERT INTO move_backtest (value_chain, horizon, grade, case_count, predicted_move, realized_move, "
                "train_end, test_start, test_end) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (value_chain, r.horizon, r.grade, int(r.case_count), _num(r.predicted_move, 8), _num(r.realized_move, 8),
                 train_end, test_start, test_end))
    return len(frame)


def write_index_prices(conn, closes: dict[str, pd.Series]) -> int:
    """closes: index code (KOSPI | KOSDAQ) → daily closes indexed by date."""
    written = 0
    with conn.transaction(), conn.cursor() as cur:
        for code, series in closes.items():
            for day, close in series.items():
                if close is None or (isinstance(close, float) and math.isnan(close)):
                    continue
                cur.execute("INSERT INTO market_index_price (index_code, trading_date, close_price) VALUES (%s, %s, %s) "
                            "ON CONFLICT (index_code, trading_date) DO UPDATE SET close_price = EXCLUDED.close_price",
                            (code, day, round(float(close), 4)))
                written += 1
    return written
