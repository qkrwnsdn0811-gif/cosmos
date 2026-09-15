"""Real PostgreSQL integration checks; opt in with TEST_DATABASE_URL.

Each test creates and drops its own random schema and applies unmodified V1-V5.
Never set this variable to a production database.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import threading
import unittest
from uuid import UUID, uuid4

from AI.rdb_loader.contract import Manifest, WINDOWS, default_score
from AI.rdb_loader.postgres import (
    PublishError, SnapshotConflictError, StaleSnapshotError, publish_snapshot,
)

try:
    import psycopg
    from psycopg import sql
except ImportError:
    psycopg = None


@unittest.skipUnless(psycopg is not None and os.getenv("TEST_DATABASE_URL"),
                     "set TEST_DATABASE_URL and install psycopg for PostgreSQL integration tests")
class PostgresPublicationTests(unittest.TestCase):
    def setUp(self):
        self.schema = "loader_test_" + uuid4().hex
        self.connection = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        migrations = Path(__file__).resolve().parents[3] / "BackEnd/src/main/resources/db/migration"
        for migration in sorted(migrations.glob("V*.sql")):
            self.connection.execute(migration.read_text(encoding="utf-8"))
        self.a, self.b, self.c = UUID(int=1), UUID(int=2), UUID(int=3)
        with self.connection.cursor() as cursor:
            cursor.executemany("INSERT INTO company (company_id, name) VALUES (%s, %s)",
                               [(self.a, "A"), (self.b, "B"), (self.c, "C")])
        self.connection.execute(
            """INSERT INTO relationship_type (code, name, directionality)
               VALUES ('SUPPLY', 'Supply', 'DIRECTED'), ('PARTNER', 'Partner', 'UNDIRECTED')"""
        )

    def tearDown(self):
        self.connection.rollback()
        self.connection.execute("SET search_path TO public")
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.connection.close()

    def manifest(self, *, day=15, record_count=3, allow_empty=False):
        snapshot_id = uuid4()
        counts = {window: record_count // 3 for window in WINDOWS}
        counts["90D"] += record_count % 3
        return Manifest(
            snapshot_id=snapshot_id,
            as_of_at=datetime(2026, 9, day, tzinfo=timezone.utc),
            formula_version="relationship-v1", model_version="model-v1",
            hdfs_uri=f"hdfs://localhost:9000/aggregated/{snapshot_id}",
            windows=WINDOWS, record_count=record_count, window_counts=counts,
            files=(("part-00000.jsonl", "0" * 64),),
            content_hash=hashlib.sha256(snapshot_id.bytes).hexdigest(),
            allow_empty=allow_empty,
        )

    def rows(self, manifest, *, target=None, relation="SUPPLY", news="80", disclosure="20"):
        news_score = Decimal(news) if news is not None else None
        disclosure_score = Decimal(disclosure) if disclosure is not None else None
        return [dict(
            source_company_id=self.a, target_company_id=target or self.b,
            relationship_type=relation, window_type=window,
            news_score=news_score, disclosure_score=disclosure_score,
            score=default_score(news_score, disclosure_score), confidence=Decimal("0.8"),
            evidence_count=2, impact_direction="POSITIVE",
            period_start=manifest.as_of_at - timedelta(days=int(window[:-1])),
            period_end=manifest.as_of_at,
        ) for window in WINDOWS]

    def count(self, table):
        return self.connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]

    def assert_current(self, snapshot_id, count=3):
        self.assertEqual(self.count("relationship_score_current"), count)
        if count:
            self.assertEqual(self.connection.execute(
                "SELECT DISTINCT snapshot_id FROM relationship_score_current"
            ).fetchall(), [(snapshot_id,)])

    def test_migrations_and_null_zero_scores_reach_both_tables(self):
        self.assertIsNone(self.connection.execute("SELECT to_regclass('user_weight_setting')").fetchone()[0])
        manifest = self.manifest()
        rows = self.rows(manifest)
        rows[1].update(news_score=None, disclosure_score=Decimal("30"), score=Decimal("30"))
        rows[2].update(news_score=Decimal("0"), disclosure_score=None, score=Decimal("0"))
        result = publish_snapshot(self.connection, manifest, rows)
        self.assertEqual(result.stored_count, 3)
        self.assertFalse(result.already_published)
        self.assert_current(manifest.snapshot_id)
        for table in ("relationship_score_current", "relationship_score_history"):
            values = self.connection.execute(sql.SQL(
                "SELECT window_type, news_score, disclosure_score, score FROM {} ORDER BY window_type"
            ).format(sql.Identifier(table))).fetchall()
            self.assertEqual(values, [
                ("30D", None, Decimal("30"), Decimal("30")),
                ("7D", Decimal("80"), Decimal("20"), Decimal("50")),
                ("90D", Decimal("0"), None, Decimal("0")),
            ])
        self.assertEqual(self.connection.execute(
            "SELECT status, published_at IS NOT NULL FROM graph_snapshot"
        ).fetchone(), ("PUBLISHED", True))

    def test_utc_windows_are_independent_of_session_daylight_saving_time(self):
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        # The seven-day interval crosses the 2026-03-08 spring DST transition.
        manifest = replace(self.manifest(), as_of_at=datetime(2026, 3, 15, tzinfo=timezone.utc))
        publish_snapshot(self.connection, manifest, self.rows(manifest))
        self.assert_current(manifest.snapshot_id)
        for window, start, end in self.connection.execute(
            "SELECT window_type, period_start, period_end FROM relationship_score_history"
        ).fetchall():
            self.assertEqual(start, manifest.as_of_at - timedelta(days=int(window[:-1])))
            self.assertEqual(end, manifest.as_of_at)
        self.assertEqual(self.connection.execute("SHOW TIME ZONE").fetchone()[0], "America/New_York")

    def test_zero_evidence_for_visible_score_is_rejected_without_replacement(self):
        initial = self.manifest()
        publish_snapshot(self.connection, initial, self.rows(initial))
        replacement = self.manifest(day=16)
        rows = self.rows(replacement)
        rows[0]["evidence_count"] = 0
        with self.assertRaises(psycopg.errors.CheckViolation):
            publish_snapshot(self.connection, replacement, rows)
        self.assert_current(initial.snapshot_id)
        self.assertEqual(self.count("graph_snapshot"), 1)
        self.assertEqual(self.count("relationship_score_history"), 3)

    def test_exact_retry_is_noop_and_conflicting_id_is_rejected(self):
        manifest = self.manifest()
        publish_snapshot(self.connection, manifest, self.rows(manifest))
        result = publish_snapshot(self.connection, manifest, iter(()))
        self.assertTrue(result.already_published)
        self.assertEqual(self.count("relationship_score_history"), 3)
        for changed in (replace(manifest, content_hash="f" * 64),
                        replace(manifest, model_version="different-model")):
            with self.assertRaises(SnapshotConflictError):
                publish_snapshot(self.connection, changed, self.rows(changed))
        self.assert_current(manifest.snapshot_id)

    def test_new_snapshot_replaces_all_current_but_preserves_history_and_ids(self):
        first = self.manifest(record_count=6)
        publish_snapshot(self.connection, first, self.rows(first) + self.rows(first, target=self.c))
        relationship_ids = self.connection.execute(
            "SELECT relationship_id FROM company_relationship ORDER BY relationship_id"
        ).fetchall()
        second = self.manifest(day=16)
        publish_snapshot(self.connection, second, self.rows(second, target=self.c))
        self.assert_current(second.snapshot_id)
        self.assertEqual(self.count("relationship_score_history"), 9)
        self.assertEqual(self.connection.execute(
            "SELECT relationship_id FROM company_relationship ORDER BY relationship_id"
        ).fetchall(), relationship_ids)
        # An older exact replay must not restore the disappeared edge.
        self.assertTrue(publish_snapshot(self.connection, first, []).already_published)
        self.assert_current(second.snapshot_id)

    def test_stale_and_equal_as_of_new_ids_are_rejected(self):
        initial = self.manifest()
        publish_snapshot(self.connection, initial, self.rows(initial))
        for day in (14, 15):
            stale = self.manifest(day=day)
            with self.assertRaises(StaleSnapshotError):
                publish_snapshot(self.connection, stale, self.rows(stale))
        self.assert_current(initial.snapshot_id)
        self.assertEqual(self.count("graph_snapshot"), 1)

    def test_missing_company_or_type_preserves_previous_publication(self):
        initial = self.manifest()
        publish_snapshot(self.connection, initial, self.rows(initial))
        next_snapshot = self.manifest(day=16)
        for rows in (self.rows(next_snapshot, target=uuid4()),
                     self.rows(next_snapshot, relation="UNREGISTERED")):
            with self.assertRaises(PublishError):
                publish_snapshot(self.connection, next_snapshot, rows)
        self.assert_current(initial.snapshot_id)
        self.assertEqual(self.count("graph_snapshot_load"), 1)
        self.assertEqual(self.count("relationship_score_history"), 3)

    def test_failure_at_final_publish_rolls_back_every_table(self):
        initial = self.manifest()
        publish_snapshot(self.connection, initial, self.rows(initial))
        self.connection.execute("""
            CREATE FUNCTION reject_publication() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected final publication failure'; END; $$;
            CREATE TRIGGER reject_publication BEFORE UPDATE ON graph_snapshot
            FOR EACH ROW EXECUTE FUNCTION reject_publication();
        """)
        next_snapshot = self.manifest(day=16)
        with self.assertRaises(psycopg.Error):
            publish_snapshot(self.connection, next_snapshot, self.rows(next_snapshot, target=self.c))
        self.assert_current(initial.snapshot_id)
        self.assertEqual(self.count("company_relationship"), 1)
        self.assertEqual(self.count("graph_snapshot"), 1)
        self.assertEqual(self.count("graph_snapshot_load"), 1)
        self.assertEqual(self.count("relationship_score_history"), 3)

    def test_copy_source_failure_rolls_back_and_connection_is_reusable(self):
        initial = self.manifest()
        publish_snapshot(self.connection, initial, self.rows(initial))
        next_snapshot = self.manifest(day=16)

        def failing_rows():
            yield self.rows(next_snapshot)[0]
            raise OSError("source interrupted")

        with self.assertRaises(OSError):
            publish_snapshot(self.connection, next_snapshot, failing_rows())
        self.assert_current(initial.snapshot_id)
        publish_snapshot(self.connection, next_snapshot, self.rows(next_snapshot))
        self.assert_current(next_snapshot.snapshot_id)

    def test_reverse_undirected_legacy_relationship_id_is_preserved(self):
        existing_id = uuid4()
        self.connection.execute(
            """INSERT INTO company_relationship
                (relationship_id, source_company_id, target_company_id, relationship_type_id)
                SELECT %s, %s, %s, relationship_type_id FROM relationship_type WHERE code='PARTNER'""",
            (existing_id, self.b, self.a),
        )
        manifest = self.manifest()
        publish_snapshot(self.connection, manifest, self.rows(manifest, relation="PARTNER"))
        self.assertEqual(self.count("company_relationship"), 1)
        self.assertEqual(self.connection.execute(
            "SELECT DISTINCT relationship_id FROM relationship_score_current"
        ).fetchall(), [(existing_id,)])

    def test_undirected_opposite_input_duplicates_are_rejected(self):
        manifest = self.manifest(record_count=6)
        forward = self.rows(manifest, relation="PARTNER")
        reverse = [dict(row, source_company_id=self.b, target_company_id=self.a) for row in forward]
        with self.assertRaises(psycopg.errors.UniqueViolation):
            publish_snapshot(self.connection, manifest, forward + reverse)
        self.assertEqual(self.count("graph_snapshot"), 0)
        self.assertEqual(self.count("company_relationship"), 0)

    def test_empty_snapshot_requires_opt_in_and_retains_history(self):
        initial = self.manifest()
        publish_snapshot(self.connection, initial, self.rows(initial))
        empty = self.manifest(day=16, record_count=0)
        with self.assertRaises(PublishError):
            publish_snapshot(self.connection, empty, [])
        self.assert_current(initial.snapshot_id)
        result = publish_snapshot(self.connection, replace(empty, allow_empty=True), [])
        self.assertEqual(result.stored_count, 0)
        self.assert_current(empty.snapshot_id, count=0)
        self.assertEqual(self.count("relationship_score_history"), 3)

    def test_receipt_counts_raw_rows_excluded_before_copy(self):
        manifest = self.manifest(record_count=6)
        publish_snapshot(self.connection, manifest, self.rows(manifest))
        self.assertEqual(self.connection.execute(
            "SELECT record_count, stored_count FROM graph_snapshot_load"
        ).fetchone(), (6, 3))

    def test_repeatable_read_keeps_in_flight_graph_consistent_during_publish(self):
        initial = self.manifest()
        publish_snapshot(self.connection, initial, self.rows(initial))
        replacement = self.manifest(day=16)
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as reader:
            reader.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            with reader.transaction():
                reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                selected_id = reader.execute(
                    """SELECT snapshot_id FROM graph_snapshot WHERE status='PUBLISHED'
                       ORDER BY as_of_at DESC, snapshot_id DESC LIMIT 1"""
                ).fetchone()[0]
                self.assertEqual(selected_id, initial.snapshot_id)
                publish_snapshot(self.connection, replacement, self.rows(replacement, target=self.c))
                self.assert_current(replacement.snapshot_id)
                # This is the API's second SELECT in the same read transaction.
                self.assertEqual(reader.execute(
                    "SELECT count(*) FROM relationship_score_current WHERE snapshot_id=%s",
                    (selected_id,),
                ).fetchone()[0], 3)
                self.assertEqual(reader.execute(
                    "SELECT count(*) FROM relationship_score_current WHERE snapshot_id=%s",
                    (replacement.snapshot_id,),
                ).fetchone()[0], 0)
            self.assertEqual(reader.execute(
                "SELECT DISTINCT snapshot_id FROM relationship_score_current"
            ).fetchall(), [(replacement.snapshot_id,)])

    def test_concurrent_equal_timestamp_publications_have_one_winner(self):
        barrier = threading.Barrier(2)

        def publish_one():
            with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as connection:
                connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
                connection.execute("SET statement_timeout = '15s'")
                manifest = self.manifest()
                barrier.wait(timeout=10)
                try:
                    return publish_snapshot(connection, manifest, self.rows(manifest))
                except StaleSnapshotError:
                    return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: publish_one(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.count("graph_snapshot"), 1)
        self.assertEqual(self.count("relationship_score_history"), 3)
        self.assertEqual(self.count("relationship_score_current"), 3)


if __name__ == "__main__":
    unittest.main()
