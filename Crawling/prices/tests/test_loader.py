"""Pure validation, transaction doubles, and optional isolated PostgreSQL tests."""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.stock_prices.loader import CompanyMappingError, ValidationError, load_rows, validate_row, validate_rows


COMPANY = UUID("00000000-0000-0000-0000-000000000001")


def row(**updates):
    return {"ticker": "005930", "market": "KOSPI", "trading_date": "2026-09-14", "interval_type": "1D",
            "open_price": "100.12345", "high_price": "110", "low_price": "90", "close_price": "105",
            "trading_volume": 123, "adj_close": "52.5", "currency": "KRW",
            "provider": "fixture", "price_basis": "split_adjusted", **updates}


class ValidationTests(unittest.TestCase):
    def test_decimal_utc_mapping_without_adjusting_close_or_mutating_input(self):
        original = row()
        value = validate_row(original)
        self.assertEqual(value["open_price"], Decimal("100.1235"))
        self.assertEqual(value["close_price"], Decimal("105.0000"))
        self.assertEqual(value["adj_close"], Decimal("52.5000"))
        self.assertEqual(value["trading_at"], datetime(2026, 9, 14, tzinfo=timezone.utc))
        self.assertEqual(original["open_price"], "100.12345")
        self.assertNotIn("trading_at", original)

    def test_float_input_uses_decimal_string_and_null_adjustment_is_valid(self):
        self.assertEqual(validate_row(row(open_price=100.12345, adj_close=None))["open_price"], Decimal("100.1235"))

    def test_actual_kospi_alphanumeric_stock_code_is_preserved(self):
        self.assertEqual(validate_row(row(ticker="0126Z0"))["ticker"], "0126Z0")

    def test_rejects_invalid_contract_values(self):
        invalid = [
            {"market": "NYSE"}, {"ticker": 5930}, {"ticker": " 005930"}, {"ticker": "5930"},
            {"trading_date": "20260914"}, {"trading_date": "2026-02-30"},
            {"interval_type": "1H"}, {"currency": "USD"}, {"provider": ""}, {"price_basis": ""},
            {"open_price": "NaN"}, {"open_price": float("inf")}, {"open_price": "-1"},
            {"open_price": True}, {"close_price": "111"}, {"low_price": "106"},
            {"high_price": "99"}, {"high_price": "1e100000"}, {"adj_close": "NaN"},
            {"trading_volume": -1}, {"trading_volume": True}, {"trading_volume": 1.0},
            {"trading_volume": 2**63},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                validate_row(row(**changes))

    def test_ohlc_range_checked_before_rounding(self):
        with self.assertRaises(ValidationError):
            validate_row(row(open_price="100.00004", high_price="100.00003", close_price="100"))

    def test_storage_boundaries_and_zero_volume(self):
        fields = {name: "9999999999999999.9999" for name in ("open_price", "high_price", "low_price", "close_price")}
        self.assertEqual(validate_row(row(**fields, trading_volume=2**63-1))["high_price"], Decimal(fields["high_price"]))
        self.assertEqual(validate_row(row(trading_volume=0))["trading_volume"], 0)

    def test_zero_and_rounded_zero_close_prices_are_rejected(self):
        for value in (0, "0.00001"):
            with self.subTest(value=value), self.assertRaisesRegex(ValidationError, "positive"):
                validate_row(row(open_price=value, high_price=value, low_price=value, close_price=value))

    def test_duplicate_daily_key_rejected_even_if_identical(self):
        with self.assertRaisesRegex(ValidationError, "row 2: duplicate"):
            validate_rows(iter([row(), row()]))
        self.assertEqual(len(validate_rows([row(), row(trading_date="2026-09-15")])), 2)


class FakeConnection:
    def __init__(self, companies=None, fail_write=False):
        self.info = SimpleNamespace(transaction_status=0)
        self.companies = companies if companies is not None else [(COMPANY, "KOSPI", "005930")]
        self.fail_write = fail_write
        self.writes, self.commands = [], []
        self.committed = self.rolled_back = False
        self.rowcount = 0

    @contextmanager
    def transaction(self, *, force_rollback):
        try:
            yield
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.rolled_back = force_rollback
            self.committed = not force_rollback

    @contextmanager
    def cursor(self, **_):
        yield self

    def execute(self, sql, parameters=None):
        self.commands.append((sql, parameters))

    def fetchall(self):
        return self.companies

    def executemany(self, sql, parameters):
        self.writes = list(parameters)
        if self.fail_write:
            raise RuntimeError("fixture database failure")
        self.rowcount = len(self.writes)


class TransactionTests(unittest.TestCase):
    def test_dry_run_is_default_and_does_not_write_adjusted_close(self):
        connection = FakeConnection()
        result = load_rows(connection, [row()])
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)
        self.assertEqual(result["affected_rows"], 1)
        self.assertEqual(result["committed_rows"], 0)
        self.assertEqual(connection.writes[0][-2], Decimal("105.0000"))
        self.assertEqual(connection.writes[0][:3], (COMPANY, datetime(2026, 9, 14, tzinfo=timezone.utc), "1D"))

    def test_commit_is_explicit(self):
        connection = FakeConnection()
        self.assertEqual(load_rows(connection, [row()], dry_run=False)["committed_rows"], 1)
        self.assertTrue(connection.committed)

    def test_missing_company_rejects_whole_batch_before_first_write(self):
        connection = FakeConnection()
        with self.assertRaisesRegex(CompanyMappingError, "NASDAQ/AAPL"):
            load_rows(connection, [row(), row(ticker="AAPL", market="NASDAQ", currency="USD")], dry_run=False)
        self.assertEqual(connection.writes, [])
        self.assertTrue(connection.rolled_back)

    def test_mapping_requires_exact_market_and_ticker(self):
        connection = FakeConnection(companies=[(COMPANY, "NASDAQ", "005930")])
        with self.assertRaises(CompanyMappingError):
            load_rows(connection, [row()])

    def test_ambiguous_company_rejects_before_price_writes(self):
        connection = FakeConnection(companies=[(COMPANY, "KOSPI", "005930"), (uuid4(), "KOSPI", "005930")])
        with self.assertRaisesRegex(CompanyMappingError, "ambiguous"):
            load_rows(connection, [row()], dry_run=False)
        self.assertEqual(connection.writes, [])

    def test_invalid_later_row_prevents_any_database_access(self):
        connection = FakeConnection()
        with self.assertRaises(ValidationError):
            load_rows(connection, [row(), row(trading_date="2026-09-15", trading_volume=-1)])
        self.assertEqual(connection.commands, [])

    def test_active_transaction_is_not_committed_or_rolled_back(self):
        connection = FakeConnection()
        connection.info.transaction_status = 2
        with self.assertRaisesRegex(ValidationError, "idle connection"):
            load_rows(connection, [row()], dry_run=False)
        self.assertFalse(connection.committed or connection.rolled_back)

    def test_database_failure_rolls_back_whole_batch(self):
        connection = FakeConnection(fail_write=True)
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            load_rows(connection, [row(), row(trading_date="2026-09-15")], dry_run=False)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PostgreSQLTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.schema = "stock_loader_test_" + uuid4().hex
        self.connection = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        migration = Path(__file__).resolve().parents[3] / "BackEnd/src/main/resources/db/migration/V1__create_initial_schema.sql"
        source = migration.read_text(encoding="utf-8")
        # Use the unchanged production table definitions, in this test's unique schema.
        for table in ("company", "stock_price_history"):
            definition = source[source.index(f"CREATE TABLE {table} ("):]
            self.connection.execute(definition[:definition.index("\n);") + 3])
        self.connection.execute("INSERT INTO company(company_id,name,market,stock_code) VALUES(%s,'Fixture','KOSPI','005930')", (COMPANY,))

    def tearDown(self):
        from psycopg import sql
        self.connection.rollback()
        self.connection.execute("SET search_path TO public")
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.connection.close()

    def test_dry_run_commit_replay_and_correction(self):
        self.assertEqual(load_rows(self.connection, [row()])["affected_rows"], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM stock_price_history").fetchone()[0], 0)
        self.assertEqual(load_rows(self.connection, [row()], dry_run=False)["committed_rows"], 1)
        self.assertEqual(load_rows(self.connection, [row()], dry_run=False)["affected_rows"], 0)
        self.assertEqual(load_rows(self.connection, [row(close_price="106")], dry_run=False)["affected_rows"], 1)
        result = self.connection.execute("SELECT trading_at,open_price,close_price,trading_volume FROM stock_price_history").fetchone()
        self.assertEqual(result, (datetime(2026, 9, 14, tzinfo=timezone.utc), Decimal("100.1235"), Decimal("106.0000"), 123))

    def test_missing_company_leaves_existing_prices_unchanged(self):
        load_rows(self.connection, [row()], dry_run=False)
        with self.assertRaises(CompanyMappingError):
            load_rows(self.connection, [row(close_price="106"), row(ticker="AAPL", market="NASDAQ", currency="USD")], dry_run=False)
        self.assertEqual(self.connection.execute("SELECT close_price FROM stock_price_history").fetchone()[0], Decimal("105.0000"))

    def test_inactive_company_is_rejected(self):
        self.connection.execute("UPDATE company SET status='INACTIVE'")
        with self.assertRaisesRegex(CompanyMappingError, "inactive"):
            load_rows(self.connection, [row()], dry_run=False)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM stock_price_history").fetchone()[0], 0)

    def test_later_database_constraint_failure_rolls_back_earlier_upsert(self):
        load_rows(self.connection, [row()], dry_run=False)
        self.connection.execute("ALTER TABLE stock_price_history ADD CONSTRAINT fixture_volume CHECK (trading_volume < 200)")
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            load_rows(self.connection, [row(close_price="106"), row(trading_date="2026-09-15", trading_volume=200)], dry_run=False)
        result = self.connection.execute("SELECT count(*),min(close_price) FROM stock_price_history").fetchone()
        self.assertEqual(result, (1, Decimal("105.0000")))

    def test_idle_non_autocommit_connection_and_non_utc_session(self):
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        self.connection.autocommit = False
        load_rows(self.connection, [row(trading_date="2026-03-15")], dry_run=False)
        value = self.connection.execute("SELECT trading_at FROM stock_price_history").fetchone()[0]
        self.assertEqual(value.astimezone(timezone.utc), datetime(2026, 3, 15, tzinfo=timezone.utc))
        self.connection.rollback()
        self.connection.autocommit = True


if __name__ == "__main__":
    unittest.main()
