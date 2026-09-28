"""Selection, completed-day cutoffs and durable collection without network I/O."""
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.stock_prices.collector import FetchError, collect, export_run, process_lock
from services.stock_prices.store import PriceStore
from services.stock_prices.universe import completed_date, date_range, load_universe, select_companies
from services.stock_prices import worker


KOREA = {"market": "KOSPI", "ticker": "005930", "name": "삼성전자"}
KOREA_2 = {"market": "KOSPI", "ticker": "0126Z0", "name": "삼성에피스홀딩스"}
US = {"market": "NASDAQ", "ticker": "AAPL", "name": "Apple"}
START, END = date(2026, 9, 10), date(2026, 9, 15)
NOW = 1_800_000_000.0


def bar(company=KOREA, day="2026-09-14", price=100, **updates):
    return {"market": company["market"], "ticker": company["ticker"], "trading_date": day,
            "interval_type": "1D", "open_price": price, "high_price": price + 10,
            "low_price": price - 10, "close_price": price, "trading_volume": 1000,
            "adj_close": None if company["market"] == "KOSPI" else price - 1,
            "currency": "KRW" if company["market"] == "KOSPI" else "USD",
            "provider": "fixture", "price_basis": "fixture_adjusted", "quality_flags": [], **updates}


def missing_bar(company=KOREA, day="2026-09-15"):
    return bar(company, day=day, open_price=None, high_price=None, low_price=None,
               close_price=None, trading_volume=None, adj_close=None, quality_flags=["source_missing_ohlcv"])


class UniverseTests(unittest.TestCase):
    def test_repository_default_has_200_selected_from_202_registered_symbols(self):
        universe = load_universe()
        selected = select_companies(universe)
        self.assertEqual(len(universe), 202)
        self.assertEqual(len(selected), 200)
        self.assertEqual({market: sum(c["market"] == market for c in selected)
                          for market in ("KOSPI", "NASDAQ")}, {"KOSPI": 100, "NASDAQ": 100})

    def test_custom_selection_accepts_krx_alphanumeric_and_registered_nondefault(self):
        universe = load_universe()
        optional = next(c for c in universe if not c["default_selected"])
        selected = select_companies(universe, selection=[
            {"market": "KOSPI", "ticker": "0126Z0"},
            {"market": optional["market"], "ticker": optional["ticker"]},
        ])
        self.assertEqual(selected[0]["name"], "삼성에피스홀딩스")
        self.assertEqual(selected[1], optional)

    def test_market_selection_is_explicit_and_cannot_escape_registered_universe(self):
        universe = load_universe()
        self.assertEqual(len(select_companies(universe, markets=("KOSPI",))), 100)
        for kwargs in [
            {"markets": ("KOSPI",), "tickers": ["AAPL"]},
            {"markets": ("NASDAQ",), "selection": [{"market": "KOSPI", "ticker": "005930"}]},
            {"markets": ("NYSE",)}, {"markets": ("KOSPI", "KOSPI")},
            {"selection": [{"market": "KOSPI", "ticker": "0126z0"}]},
            {"tickers": ["005930", "005930"]},
            {"selection": [{"market": c["market"], "ticker": c["ticker"]}
                           for c in universe if c["market"] == "NASDAQ"]},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                select_companies(universe, **kwargs)

    def test_ambiguous_ticker_requires_explicit_market(self):
        companies = [{**KOREA, "default_selected": True},
                     {**US, "ticker": "005930", "default_selected": True}]
        with self.assertRaises(ValueError):
            select_companies(companies, tickers=["005930"])
        self.assertEqual(select_companies(companies, selection=[{"market": "NASDAQ", "ticker": "005930"}])[0]["market"], "NASDAQ")

    def test_completed_dates_use_market_local_cutoff_and_dst(self):
        examples = [
            ("KOSPI", "2026-09-15T08:59:59+00:00", "2026-09-14"),
            ("KOSPI", "2026-09-15T09:00:00+00:00", "2026-09-15"),
            ("NASDAQ", "2026-09-15T23:59:59+00:00", "2026-09-14"),
            ("NASDAQ", "2026-09-16T00:00:00+00:00", "2026-09-15"),
            ("NASDAQ", "2026-01-16T00:59:59+00:00", "2026-01-14"),
            ("NASDAQ", "2026-01-16T01:00:00+00:00", "2026-01-15"),
        ]
        for market, instant, expected in examples:
            with self.subTest(market=market, instant=instant):
                self.assertEqual(completed_date(market, datetime.fromisoformat(instant)), date.fromisoformat(expected))
        with self.assertRaises(ValueError):
            completed_date("KOSPI", datetime(2026, 9, 15))

    def test_date_range_clamps_leap_day_and_rejects_unfinished_daily_bar(self):
        now = datetime(2026, 9, 15, 8, tzinfo=timezone.utc)
        self.assertEqual(date_range("KOSPI", years=1, end="2024-02-29", now=now),
                         (date(2023, 2, 28), date(2024, 2, 29)))
        with self.assertRaises(ValueError):
            date_range("KOSPI", end="2026-09-15", now=now)

    def test_max_history_and_explicit_long_range_preserve_source_window(self):
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        self.assertEqual(date_range("KOSPI", now=now), (date(1900, 1, 1), date(2026, 9, 14)))
        self.assertEqual(date_range("NASDAQ", start="1962-01-01", end="2026-09-14", now=now),
                         (date(1962, 1, 1), date(2026, 9, 14)))
        self.assertEqual(date_range("NASDAQ", years=50, end="2026-09-14", now=now)[0], date(1976, 9, 14))
        with self.assertRaises(ValueError):
            date_range("KOSPI", start="1899-12-31", now=now)
        with self.assertRaises(ValueError):
            date_range("KOSPI", years=101, now=now)
        with self.assertRaises(ValueError):
            date_range("KOSPI", years=10, start="2010-01-01", now=now)

    def test_cli_defaults_to_max_and_rejects_conflicting_history_choices(self):
        from services.stock_prices.cli import parser
        cli = parser()
        self.assertIsNone(cli.parse_args(["collect"]).years)
        self.assertTrue(cli.parse_args(["collect", "--max-history"]).max_history)
        for arguments in (["--years", "10", "--max-history"],
                          ["--start", "2000-01-01", "--years", "10"]):
            with self.subTest(arguments=arguments), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                cli.parse_args(["collect", *arguments])


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = PriceStore(Path(self.temporary.name) / "prices.db")
        self.addCleanup(self.store.close)
        self.now = NOW
        self.time_patch = patch("services.stock_prices.collector.time.time", side_effect=lambda: self.now)
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)
        self.sleep_patch = patch("services.stock_prices.collector.time.sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def run_collect(self, companies=(KOREA,), *, rows=None, fetch=None, start=START, end=END, refresh=False):
        fetch = fetch or Mock(return_value=rows if rows is not None else [bar()])
        outcomes = collect(companies, self.store, {c["market"]: (start, end) for c in companies},
                           fetch=fetch, refresh=refresh, min_interval=0, attempts=1)
        return outcomes, fetch

    def stored(self, company=KOREA):
        return self.store.rows(company, date(2000, 1, 1), date(2030, 1, 1))

    def test_multi_company_export_preserves_daily_data_and_quarantine_snapshot(self):
        import hashlib
        import pyarrow.parquet as pq
        companies = [KOREA, US]
        excluded = missing_bar(US)
        fetch = Mock(side_effect=lambda company, start, end:
                     [bar(company)] + ([excluded] if company == US else []))
        outcomes, _ = self.run_collect(companies, fetch=fetch)
        directory, manifest = export_run(self.store, companies,
            {c["market"]: (START, END) for c in companies}, outcomes, Path(self.temporary.name) / "export")
        records = pq.read_table(directory / "prices_daily.parquet").to_pylist()
        self.assertEqual({(r["market"], r["ticker"]) for r in records}, {("KOSPI", "005930"), ("NASDAQ", "AAPL")})
        self.assertTrue(all(r["trading_at"] == datetime(2026, 9, 14, tzinfo=timezone.utc) for r in records))
        self.assertEqual((manifest["rows"], manifest["quarantined_rows"]), (2, 1))
        raw = (directory / manifest["quarantine"]).read_bytes()
        self.assertEqual([json.loads(line) for line in raw.decode("utf-8").splitlines()], [excluded])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["quarantine_sha256"])

    def test_refresh_applies_corrected_history_and_short_selection_keeps_earlier_dates(self):
        self.run_collect(rows=[bar(day="2026-09-10"), bar()])
        self.now += 60
        result, fetch = self.run_collect(start=date(2026, 9, 14), refresh=True,
            rows=[bar(day="2026-09-10", price=50), bar(price=50)])
        self.assertEqual(fetch.call_args.args[1:], (START, END))
        self.assertEqual(result[0]["status"], "collected")
        self.assertEqual(result[0]["rows"], 1)
        self.assertEqual((result[0]["first_date"], result[0]["last_date"], result[0]["quarantined_rows"]),
                         ("2026-09-14", "2026-09-14", 0))
        self.assertEqual([(r["trading_date"], r["close_price"]) for r in self.stored()],
                         [("2026-09-10", "50.0000"), ("2026-09-14", "50.0000")])

    def test_cache_is_persisted_and_refresh_waits_exactly_60_seconds(self):
        self.run_collect()
        self.store.close()
        self.store = PriceStore(Path(self.temporary.name) / "prices.db")
        self.addCleanup(self.store.close)
        self.now += 59
        result, fetch = self.run_collect(refresh=True)
        self.assertEqual(result[0]["status"], "cached")
        fetch.assert_not_called()
        self.now += 1
        result, fetch = self.run_collect(refresh=True)
        self.assertEqual(result[0]["status"], "collected")
        fetch.assert_called_once()
        self.now += 120
        result, fetch = self.run_collect()
        self.assertEqual(result[0]["status"], "cached")
        fetch.assert_not_called()

    def test_empty_source_refresh_preserves_previous_rows_and_success_timestamp(self):
        self.run_collect()
        before, last_success = self.stored(), self.store.coverage(KOREA)["last_success"]
        self.now += 60
        result, _ = self.run_collect(rows=[], refresh=True)
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.store.coverage(KOREA)["last_success"], last_success)

    def test_partial_failure_preserves_failed_company_and_continues_other_market(self):
        self.run_collect()
        before = self.stored()
        self.now += 60
        fetch = Mock(side_effect=[FetchError("provider_timeout"), [bar(US)]])
        results, _ = self.run_collect((KOREA, US), fetch=fetch, refresh=True)
        self.assertEqual([r["status"] for r in results], ["failed", "collected"])
        self.assertEqual(self.stored(), before)
        self.assertEqual(len(self.stored(US)), 1)

    def test_access_block_stops_same_market_but_continues_other_market(self):
        fetch = Mock(side_effect=[FetchError("access_denied", halt_source=True), [bar(US)]])
        results, _ = self.run_collect((KOREA, KOREA_2, US), fetch=fetch)
        self.assertEqual([r["status"] for r in results], ["failed", "blocked", "collected"])
        self.assertEqual([call.args[0]["ticker"] for call in fetch.call_args_list], ["005930", "AAPL"])

    def test_failed_attempt_is_deferred_until_60_seconds(self):
        self.run_collect(rows=[])
        self.now += 59
        result, fetch = self.run_collect()
        self.assertEqual(result[0]["status"], "deferred")
        fetch.assert_not_called()
        self.now += 1
        result, fetch = self.run_collect()
        self.assertEqual(result[0]["status"], "collected")
        fetch.assert_called_once()

    def test_long_failed_fetch_restarts_retry_interval_at_failure_time(self):
        def slow_failure(*_):
            self.now += 120
            raise FetchError("provider_timeout")
        self.run_collect(fetch=slow_failure)
        result, fetch = self.run_collect()
        self.assertEqual(result[0]["status"], "deferred")
        fetch.assert_not_called()

    def test_halted_daily_bar_is_quarantined_without_fabricating_ohlc(self):
        halted = bar(day="2026-09-15", open_price=0, high_price=0, low_price=0,
                     trading_volume=0, quality_flags=["halted_ohl_zero"])
        result, _ = self.run_collect(rows=[bar(), halted])
        self.assertEqual((result[0]["rows"], result[0]["quarantined_rows"]), (1, 1))
        raw = self.store.db.execute("SELECT payload FROM quarantine").fetchone()[0]
        self.assertEqual(json.loads(raw), halted)
        self.assertEqual([r["trading_date"] for r in self.stored()], ["2026-09-14"])

    def test_one_won_range_mismatch_keeps_exact_source_prices_and_other_valid_bars(self):
        company = {"market": "KOSPI", "ticker": "012450", "name": "한화에어로스페이스"}
        mismatch = bar(company, day="2025-05-07", open_price=876000, high_price=876387,
                       low_price=870000, close_price=876388, quality_flags=["ohlc_range_mismatch"])
        result, _ = self.run_collect((company,), start=date(2025, 5, 6), rows=[mismatch, bar(company)])
        self.assertEqual(result[0]["status"], "collected")
        self.assertEqual((result[0]["rows"], result[0]["quarantined_rows"]), (1, 1))
        quarantined = self.store.db.execute("SELECT reason,payload FROM quarantine WHERE ticker='012450'").fetchone()
        self.assertEqual(quarantined["reason"], "ohlc_range_mismatch")
        self.assertEqual(json.loads(quarantined["payload"]), mismatch)
        self.assertEqual((json.loads(quarantined["payload"])["high_price"],
                          json.loads(quarantined["payload"])["close_price"]), (876387, 876388))
        self.assertEqual([r["trading_date"] for r in self.stored(company)], ["2026-09-14"])

    def test_false_one_won_flags_reject_refresh_and_preserve_previous_prices(self):
        self.run_collect()
        before = self.stored()
        mismatch = bar(day="2026-09-15", open_price=876000, high_price=876387,
                       low_price=870000, close_price=876388, quality_flags=["ohlc_range_mismatch"])
        for changes in [{"high_price": 876386}, {"close_price": 876388.5}, {"open_price": True},
                        {"low_price": 0}, {"trading_volume": -1}, {"close_price": 876387}]:
            with self.subTest(changes=changes):
                self.now += 60
                result, _ = self.run_collect(refresh=True, rows=[bar(price=50), {**mismatch, **changes}])
                self.assertEqual(result[0]["status"], "failed")
                self.assertEqual(self.stored(), before)
                self.assertEqual(self.store.db.execute("SELECT count(*) FROM quarantine").fetchone()[0], 0)

    def test_historical_source_errors_preserve_raw_rows_and_exclude_them_from_charts(self):
        inconsistent = bar(day="2026-09-11", high_price=110, close_price=113,
                           quality_flags=["source_inconsistent_ohlc"])
        zero_ohl = bar(day="2026-09-15", open_price=0, high_price=0, low_price=0,
                       trading_volume=141, quality_flags=["source_zero_ohl_with_volume"])
        result, _ = self.run_collect(rows=[inconsistent, bar(), zero_ohl])
        self.assertEqual((result[0]["status"], result[0]["rows"], result[0]["quarantined_rows"]),
                         ("collected", 1, 2))
        self.assertEqual(self.store.quarantined_rows(KOREA, START, END), [inconsistent, zero_ohl])
        self.assertEqual([r["trading_date"] for r in self.stored()], ["2026-09-14"])

    def test_invalid_historical_flags_preserve_existing_snapshot(self):
        self.run_collect()
        before = self.stored()
        inconsistent = bar(day="2026-09-15", high_price=110, close_price=113,
                           quality_flags=["source_inconsistent_ohlc"])
        zero_ohl = bar(day="2026-09-15", open_price=0, high_price=0, low_price=0,
                       trading_volume=141, quality_flags=["source_zero_ohl_with_volume"])
        invalid = [
            {**inconsistent, "close_price": 110}, {**inconsistent, "close_price": 111},
            {**inconsistent, "close_price": float("inf")}, {**inconsistent, "low_price": 0},
            {**inconsistent, "trading_volume": -1}, {**inconsistent, "open_price": True},
            {**zero_ohl, "open_price": False}, {**zero_ohl, "close_price": 0},
            {**zero_ohl, "trading_volume": 0}, {**zero_ohl, "trading_volume": 2**63},
            {**zero_ohl, "close_price": 100.5},
            {**zero_ohl, "trading_volume": 0, "close_price": float("inf"),
             "quality_flags": ["halted_ohl_zero"]},
        ]
        for row in invalid:
            with self.subTest(row=row):
                self.now += 60
                result, _ = self.run_collect(refresh=True, rows=[bar(price=50), row])
                self.assertEqual(result[0]["status"], "failed")
                self.assertEqual(self.stored(), before)
                self.assertEqual(self.store.quarantine_count(KOREA, START, END), 0)

    def test_cached_bounds_and_quarantine_count_use_requested_range(self):
        mismatch = bar(day="2026-09-11", high_price=110, close_price=111,
                       quality_flags=["ohlc_range_mismatch"])
        halted = bar(day="2026-09-15", open_price=0, high_price=0, low_price=0,
                     trading_volume=0, quality_flags=["halted_ohl_zero"])
        self.run_collect(rows=[bar(day="2026-09-10"), mismatch, bar(), halted])
        result, fetch = self.run_collect(start=date(2026, 9, 14))
        self.assertEqual(result[0]["status"], "cached")
        self.assertEqual((result[0]["rows"], result[0]["quarantined_rows"]), (1, 1))
        self.assertEqual((result[0]["first_date"], result[0]["last_date"]), ("2026-09-14", "2026-09-14"))
        fetch.assert_not_called()

    def test_all_null_source_row_keeps_nulls_in_quarantine_and_exports_only_valid_bars(self):
        import pyarrow.parquet as pq
        missing = missing_bar(US)
        outcomes, _ = self.run_collect((US,), rows=[bar(US), missing])
        self.assertEqual((outcomes[0]["status"], outcomes[0]["rows"], outcomes[0]["quarantined_rows"]),
                         ("collected", 1, 1))
        raw = self.store.db.execute("SELECT reason,payload FROM quarantine").fetchone()
        self.assertEqual(raw["reason"], "source_missing_ohlcv")
        self.assertEqual(json.loads(raw["payload"]), missing)
        self.assertTrue(all(json.loads(raw["payload"])[key] is None for key in
                            ("open_price", "high_price", "low_price", "close_price", "trading_volume", "adj_close")))
        directory, manifest = export_run(self.store, [US], {"NASDAQ": (START, END)}, outcomes,
                                         Path(self.temporary.name) / "export")
        self.assertEqual((manifest["status"], manifest["rows"]), ("complete", 1))
        self.assertEqual([r["trading_date"] for r in pq.read_table(directory / "prices_daily.parquet").to_pylist()],
                         ["2026-09-14"])
        chart = json.loads((directory / "charts" / "NASDAQ" / "AAPL.json").read_text(encoding="utf-8"))
        self.assertEqual(len(chart["items"]), 1)
        self.assertEqual(chart["items"][0]["closePrice"], 100)
        self.assertEqual(chart["items"][0]["tradingAt"], "2026-09-14T00:00:00+00:00")

    def test_partial_nulls_and_false_missing_flags_preserve_previous_data(self):
        self.run_collect()
        before = self.stored()
        missing = missing_bar()
        invalid_rows = [
            {**missing, "open_price": 100}, {**missing, "close_price": 100},
            {**missing, "trading_volume": 0}, {**missing, "adj_close": 0},
            {**missing, "quality_flags": []},
            {**missing, "quality_flags": ["source_missing_ohlcv", "source_missing_ohlcv"]},
            {**missing, "quality_flags": ["source_missing_ohlcv", "halted_ohl_zero"]},
            bar(day="2026-09-15", quality_flags=["source_missing_ohlcv"]),
            {key: value for key, value in missing.items() if key != "open_price"},
        ]
        for invalid in invalid_rows:
            with self.subTest(invalid=invalid):
                self.now += 60
                result, _ = self.run_collect(rows=[bar(price=50), invalid], refresh=True)
                self.assertEqual(result[0]["status"], "failed")
                self.assertEqual(self.stored(), before)
                self.assertEqual(self.store.db.execute("SELECT count(*) FROM quarantine").fetchone()[0], 0)

    def test_khc_incomplete_source_preserves_present_values_and_exports_normal_days_only(self):
        import pyarrow.parquet as pq
        company = {"market": "NASDAQ", "ticker": "KHC", "name": "Kraft Heinz"}
        incomplete = bar(company, open_price=24.4099998474, high_price=24.9899997711,
                         low_price=24.2700004578, close_price=None, trading_volume=20331188,
                         adj_close=None, quality_flags=["source_incomplete_ohlcv"])
        outcomes, _ = self.run_collect((company,), rows=[bar(company, day="2026-09-11", price=25), incomplete])
        self.assertEqual((outcomes[0]["status"], outcomes[0]["rows"], outcomes[0]["quarantined_rows"]),
                         ("collected", 1, 1))
        self.assertEqual(outcomes[0]["last_date"], "2026-09-11")
        quarantined = self.store.db.execute("SELECT reason,payload FROM quarantine").fetchone()
        self.assertEqual(quarantined["reason"], "source_incomplete_ohlcv")
        self.assertEqual(json.loads(quarantined["payload"]), incomplete)
        directory, manifest = export_run(self.store, [company], {"NASDAQ": (START, END)}, outcomes,
                                         Path(self.temporary.name) / "export")
        self.assertEqual((manifest["status"], manifest["rows"]), ("complete", 1))
        self.assertEqual([r["trading_date"] for r in pq.read_table(directory / "prices_daily.parquet").to_pylist()],
                         ["2026-09-11"])
        chart = json.loads((directory / "charts" / "NASDAQ" / "KHC.json").read_text(encoding="utf-8"))
        self.assertEqual(len(chart["items"]), 1)
        self.assertEqual(chart["items"][0]["closePrice"], 25)
        self.assertEqual(chart["asOfAt"], "2026-09-11T00:00:00+00:00")

    def test_invalid_values_and_false_incomplete_flags_preserve_previous_data(self):
        self.run_collect((US,), rows=[bar(US)])
        before = self.stored(US)
        incomplete = bar(US, day="2026-09-15", close_price=None, adj_close=None,
                         quality_flags=["source_incomplete_ohlcv"])
        invalid_rows = [
            {**incomplete, "open_price": float("inf")}, {**incomplete, "high_price": -1},
            {**incomplete, "low_price": 0}, {**incomplete, "adj_close": -1},
            {**incomplete, "adj_close": float("inf")}, {**incomplete, "trading_volume": 1.5},
            {**incomplete, "trading_volume": -1}, {**incomplete, "trading_volume": True},
            {**incomplete, "trading_volume": 2**63},
            {**incomplete, "quality_flags": ["source_incomplete_ohlcv", "source_missing_ohlcv"]},
            bar(US, day="2026-09-15", quality_flags=["source_incomplete_ohlcv"]),
            {**missing_bar(US), "quality_flags": ["source_incomplete_ohlcv"]},
            {key: value for key, value in incomplete.items() if key != "adj_close"},
        ]
        for invalid in invalid_rows:
            with self.subTest(invalid=invalid):
                self.now += 60
                result, _ = self.run_collect((US,), rows=[bar(US, price=50), invalid], refresh=True)
                self.assertEqual(result[0]["status"], "failed")
                self.assertEqual(self.stored(US), before)
                self.assertEqual(self.store.db.execute("SELECT count(*) FROM quarantine").fetchone()[0], 0)

    def test_only_missing_rows_fail_without_creating_or_replacing_chart_data(self):
        result, _ = self.run_collect(rows=[missing_bar()])
        self.assertEqual(result[0]["status"], "failed")
        self.assertIn(result[0]["error"], {"no_prices_in_selected_range", "no_chart_eligible_bars"})
        self.assertEqual(self.stored(), [])
        self.assertIsNone(self.store.coverage(KOREA)["last_success"])
        self.now += 60
        self.run_collect()
        before = self.stored()
        self.now += 60
        result, _ = self.run_collect(rows=[missing_bar(day="2026-09-14")], refresh=True)
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(self.stored(), before)

    def test_all_halted_response_preserves_existing_chart_rows(self):
        self.run_collect()
        before = self.stored()
        self.now += 60
        result, _ = self.run_collect(refresh=True, rows=[bar(open_price=0, high_price=0, low_price=0,
            trading_volume=0, quality_flags=["halted_ohl_zero"])])
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(self.stored(), before)

    def test_invalid_or_duplicate_refresh_rolls_back_whole_company(self):
        self.run_collect()
        before = self.stored()
        for invalid in [[bar(price=50), bar()], [bar(price=50), bar(day="2026-09-15", close_price=float("nan"))]]:
            self.now += 60
            result, _ = self.run_collect(rows=invalid, refresh=True)
            self.assertEqual(result[0]["status"], "failed")
            self.assertEqual(self.stored(), before)

    def test_truncated_refresh_rejects_loss_of_previously_known_trading_days(self):
        self.run_collect(rows=[bar(day="2026-09-10"), bar()])
        before = self.stored()
        self.now += 60
        result, _ = self.run_collect(rows=[bar(price=50)], refresh=True)
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(self.stored(), before)

    def test_selected_range_with_no_prices_does_not_replace_previous_snapshot(self):
        self.run_collect(rows=[bar(day="2026-09-10"), bar()])
        before = self.stored()
        self.now += 60
        result, _ = self.run_collect(start=date(2026, 9, 14), refresh=True,
                                     rows=[bar(day="2026-09-10", price=50)])
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(self.stored(), before)

    def test_cached_coverage_without_selected_rows_is_not_successful_collection(self):
        self.run_collect(rows=[bar(day="2026-09-10")])
        result, _ = self.run_collect(start=date(2026, 9, 14))
        self.assertNotIn(result[0]["status"], {"collected", "cached"})

    def test_process_lock_rejects_overlap_and_releases_after_exception(self):
        lock = Path(self.temporary.name) / "collector.lock"
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            with process_lock(lock):
                with self.assertRaises(OSError):
                    with process_lock(lock):
                        self.fail("overlapping collector acquired the same lock")
                raise RuntimeError("fixture")
        with process_lock(lock):
            pass

    def test_worker_provider_rows_are_compatible_with_durable_store(self):
        import pandas as pd
        from services.stock_prices import providers
        frame = pd.DataFrame([{"시가": 100., "고가": 110., "저가": 90., "종가": 100., "거래량": 1000}],
                             index=pd.to_datetime(["2026-09-14"]))
        output = io.StringIO()
        with patch.object(providers, "_read_frame", return_value=frame), \
             patch.object(sys, "argv", ["worker", "KOSPI", "005930", START.isoformat(), END.isoformat()]), \
             patch("sys.stdout", output):
            self.assertEqual(worker.main(), 0)
        payload = json.loads(output.getvalue())
        result, _ = self.run_collect(rows=payload["rows"])
        self.assertEqual(result[0]["status"], "collected")


if __name__ == "__main__":
    unittest.main()
