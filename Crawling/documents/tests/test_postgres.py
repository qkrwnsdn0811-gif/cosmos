"""Document Loader integration tests against a disposable PostgreSQL schema."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4

from services.document_loader.postgres import (
    BatchConflictError, CompanyMappingError, LoadError, load_documents,
)


KR = {"market": "KOSPI", "stock_code": "005930"}
US = {"market": "NASDAQ", "stock_code": "AAPL"}
SOURCES = {
    "KR_NEWS": {"name": "Fixture KR news", "source_type": "NEWS", "base_url": "https://kr.example"},
    "US_NEWS": {"name": "Fixture US news", "source_type": "NEWS", "base_url": "https://us.example"},
    "DART": {"name": "Fixture DART", "source_type": "DISCLOSURE", "base_url": "https://dart.example"},
    "SEC": {"name": "Fixture SEC", "source_type": "DISCLOSURE", "base_url": "https://sec.example"},
}


def news(**changes):
    record = {
        "source_key": "KR_NEWS", "document_type": "NEWS", "title": "Fixture domestic news",
        "summary": "Fixture summary", "original_url": "https://kr.example/story/1",
        "canonical_url": "https://kr.example/story/1", "publisher": "Fixture publisher", "author": None,
        "published_at": "2026-09-15T01:00:00+00:00", "collected_at": "2026-09-15T02:00:00+00:00",
        "content_hash": "b" * 64, "hdfs_raw_uri": "hdfs://fixture/data/news/part-1.jsonl",
        "status": "COLLECTED", "company_refs": [dict(KR)],
    }
    record.update(changes)
    return record


def analyzed_news(company_refs=None, confidence=0.9, **changes):
    refs = [dict(KR)] if company_refs is None else company_refs
    record = news(status="ANALYZED", analysis_version="news-company-mentions-1.0",
                  model_version="dict-v1.3", analyzed_at="2026-09-15T03:00:00+00:00",
                  company_refs=refs,
                  company_links=[{**reference, "confidence": confidence} for reference in refs])
    record.update(changes)
    return record


def dart(**changes):
    record = {
        "source_key": "DART", "document_type": "DISCLOSURE", "title": "Fixture DART report",
        "summary": None, "original_url": "https://dart.example/20260915000001",
        "published_at": "2026-09-15T00:00:00+09:00", "collected_at": "2026-09-15T02:00:00+00:00",
        "content_hash": "c" * 64, "hdfs_raw_uri": "hdfs://fixture/data/dart/part-1.jsonl",
        "status": "COLLECTED", "company_refs": [dict(KR)], "filing_company": dict(KR),
        "filing_system": "DART", "dart_receipt_no": "20260915000001", "sec_accession_no": None,
        "sec_cik": None, "report_code": None, "report_name": "Fixture DART report",
        "filing_date": "2026-09-15", "disclosure_type": None, "correction_status": None,
    }
    record.update(changes)
    return record


def sec(**changes):
    record = dart(source_key="SEC", title="Fixture SEC report", filing_system="SEC",
                  original_url="https://sec.example/0000320193-26-000123", filing_company=dict(US),
                  company_refs=[dict(US)], dart_receipt_no=None, sec_accession_no="0000320193-26-000123",
                  sec_cik="0000320193", report_name="10-K", report_code="10-K",
                  hdfs_raw_uri="hdfs://fixture/data/sec/part-1.jsonl")
    record.update(changes)
    return record


def four_records():
    return [news(), news(source_key="US_NEWS", title="Fixture overseas news",
                        original_url="https://us.example/story/1", canonical_url="https://us.example/story/1",
                        company_refs=[dict(US)]), dart(), sec()]


class ConnectionSafetyTests(unittest.TestCase):
    def test_caller_transaction_is_rejected_without_commit_or_rollback(self):
        connection = SimpleNamespace(info=SimpleNamespace(transaction_status=2))
        with self.assertRaisesRegex(LoadError, "idle connection"):
            load_documents(connection, [], batch_id="test", manifest_sha256="a" * 64,
                           source_uri="hdfs://fixture/batch", sources=SOURCES, commit=True)

    def test_commit_flag_must_be_a_boolean(self):
        connection = SimpleNamespace(info=SimpleNamespace(transaction_status=0))
        with self.assertRaisesRegex(LoadError, "booleans"):
            load_documents(connection, [], batch_id="test", manifest_sha256="a" * 64,
                           source_uri="hdfs://fixture/batch", sources=SOURCES, commit="true")


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PostgreSQLTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.schema = "document_loader_test_" + uuid4().hex
        self.connection = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        migration_dir = Path(__file__).resolve().parents[3] / "BackEnd/src/main/resources/db/migration"
        # V1-V6 only. V7 builds cosmos_analysis by reading public.* directly, so
        # it cannot run inside the throwaway schema this suite isolates itself
        # in, and the loader under test writes none of the tables it adds.
        for path in sorted(migration_dir.glob("V[1-6]__*.sql")):
            self.connection.execute(path.read_text(encoding="utf-8"))
        self.kr_id, self.us_id = uuid4(), uuid4()
        self.connection.execute("""INSERT INTO company (company_id, name, market, stock_code)
                                   VALUES (%s, 'Fixture KR', 'KOSPI', '005930'),
                                          (%s, 'Fixture US', 'NASDAQ', 'AAPL')""", (self.kr_id, self.us_id))

    def tearDown(self):
        from psycopg import sql
        self.connection.rollback()
        self.connection.execute("SET search_path TO public")
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.connection.close()

    def load(self, records=None, **changes):
        options = dict(batch_id="fixture-batch", manifest_sha256="a" * 64,
                       source_uri="hdfs://fixture/batches/batch-1", sources=SOURCES, register_sources=True)
        options.update(changes)
        return load_documents(self.connection, four_records() if records is None else records, **options)

    def count(self, table):
        from psycopg import sql
        return self.connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]

    def test_four_streams_dry_run_exercises_writes_and_rolls_back_everything(self):
        result = self.load()
        self.assertEqual(result["inserted_documents"], 4)
        self.assertEqual(result["company_links_added"], 4)
        self.assertEqual(result["committed_documents"], 0)
        for table in ("source_document", "news_article", "disclosure", "company_document", "data_source", "document_load_batch"):
            self.assertEqual(self.count(table), 0, table)

    def test_all_four_streams_commit_and_exact_replay(self):
        first = self.load(commit=True)
        self.assertEqual(first["committed_documents"], 4)
        expected = {"source_document": 4, "news_article": 2, "disclosure": 2,
                    "company_document": 4, "data_source": 4, "document_load_batch": 1}
        for table, count in expected.items():
            self.assertEqual(self.count(table), count, table)
        again = self.load(commit=True)
        self.assertTrue(again["already_loaded"])
        self.assertEqual(again["inserted_documents"], 0)
        self.assertEqual(again["committed_documents"], 0)
        identities = self.connection.execute("SELECT filing_system, dart_receipt_no, sec_accession_no, sec_cik "
                                             "FROM disclosure ORDER BY filing_system").fetchall()
        self.assertEqual(identities, [("DART", "20260915000001", None, None),
                                     ("SEC", None, "0000320193-26-000123", "0000320193")])

    def test_same_batch_with_different_input_manifest_source_or_configuration_fails(self):
        self.load(commit=True)
        variants = [dict(manifest_sha256="d" * 64), dict(source_uri="hdfs://fixture/other"),
                    dict(records=[news(title="Changed title"), *four_records()[1:]]),
                    dict(sources={**SOURCES, "KR_NEWS": {**SOURCES["KR_NEWS"], "base_url": "https://new.example"}})]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(BatchConflictError):
                self.load(commit=True, **changes)
        self.assertEqual(self.count("source_document"), 4)
        self.assertEqual(self.count("document_load_batch"), 1)

    def test_repeat_documents_in_new_batch_do_not_duplicate_or_rewrite_rows(self):
        self.load(commit=True)
        timestamps = self.connection.execute("SELECT document_id, updated_at FROM source_document ORDER BY document_id").fetchall()
        result = self.load(commit=True, batch_id="fixture-batch-2")
        self.assertEqual(result["inserted_documents"], 0)
        self.assertEqual(result["updated_documents"], 0)
        self.assertEqual(result["company_links_added"], 0)
        self.assertEqual(self.count("document_load_batch"), 2)
        self.assertEqual(timestamps, self.connection.execute("SELECT document_id, updated_at FROM source_document ORDER BY document_id").fetchall())

    def test_same_news_discovered_for_two_companies_is_one_document_with_both_links(self):
        result = self.load([news(), news(company_refs=[dict(US)])], commit=True)
        self.assertEqual(result["input_records"], 2)
        self.assertEqual(result["document_count"], 1)
        self.assertEqual(result["inserted_documents"], 1)
        self.assertEqual(self.count("source_document"), 1)
        self.assertEqual(self.count("news_article"), 1)
        self.assertEqual(self.count("company_document"), 2)
        self.assertEqual(self.connection.execute("SELECT record_count, document_count FROM document_load_batch").fetchone(), (2, 1))

    def test_analyzed_news_replaces_collection_hints_with_exact_ai_company_set(self):
        self.load([news()], commit=True)
        result = self.load([analyzed_news(company_refs=[dict(US)], confidence=0.875)],
                           commit=True, batch_id="analyzed")
        self.assertEqual((result["company_links_added"], result["company_links_deleted"]), (1, 1))
        document = self.connection.execute(
            "SELECT status, analysis_version FROM source_document").fetchone()
        self.assertEqual(document, ("ANALYZED", "news-company-mentions-1.0"))
        links = self.connection.execute("""
            SELECT company_id, mention_type, confidence::text, model_version, is_service_visible,
                   (analyzed_at AT TIME ZONE 'UTC')::text FROM company_document
            """).fetchall()
        # FALSE: matching a company is not publishing it. The enrichment loader
        # flips this once the pair has a sentiment and its evidence sentences.
        self.assertEqual(links, [(self.us_id, "MENTION", "0.875000", "dict-v1.3", False,
                                  "2026-09-15 03:00:00")])

    def test_analyzed_news_empty_set_deletes_all_news_company_links(self):
        self.load([news(company_refs=[dict(KR), dict(US)])], commit=True)
        result = self.load([analyzed_news(company_refs=[])], commit=True, batch_id="analyzed-empty")
        self.assertEqual(result["company_links_deleted"], 2)
        self.assertEqual(self.count("company_document"), 0)
        self.assertEqual(self.connection.execute("SELECT status FROM source_document").fetchone()[0], "ANALYZED")

    def test_reanalysis_upserts_confidence_and_exact_replay_is_noop(self):
        self.load([analyzed_news(confidence=0.7)], commit=True)
        result = self.load([analyzed_news(confidence=0.95, model_version="dict-v1.4",
                                          analyzed_at="2026-09-15T04:00:00+00:00")],
                           commit=True, batch_id="reanalyzed")
        self.assertEqual(result["company_links_updated"], 1)
        self.assertEqual(self.connection.execute(
            "SELECT confidence::text, model_version FROM company_document").fetchone(),
            ("0.950000", "dict-v1.4"))
        self.assertTrue(self.load([analyzed_news(confidence=0.95, model_version="dict-v1.4",
                                                 analyzed_at="2026-09-15T04:00:00+00:00")],
                                  commit=True, batch_id="reanalyzed")["already_loaded"])

    def test_collection_replay_cannot_recreate_links_after_analysis(self):
        self.load([analyzed_news(company_refs=[])], commit=True)
        result = self.load([news(company_refs=[dict(KR)])], commit=True, batch_id="raw-after-analysis")
        self.assertEqual(result["company_links_added"], 0)
        self.assertEqual(self.count("company_document"), 0)
        self.assertEqual(self.connection.execute(
            "SELECT status, analysis_version FROM source_document").fetchone(),
            ("ANALYZED", "news-company-mentions-1.0"))

    def test_older_analyzed_batch_cannot_override_newer_empty_result(self):
        self.load([analyzed_news(company_refs=[], collected_at="2026-09-15T05:00:00+00:00",
                                 analyzed_at="2026-09-15T06:00:00+00:00")], commit=True)
        result = self.load([analyzed_news(company_refs=[dict(KR)],
                                          collected_at="2026-09-15T02:00:00+00:00",
                                          analyzed_at="2026-09-15T03:00:00+00:00")],
                           commit=True, batch_id="older-analysis")
        self.assertEqual(result["company_links_added"], 0)
        self.assertEqual(self.count("company_document"), 0)

    def test_different_analysis_version_cannot_override_empty_tombstone(self):
        self.load([analyzed_news(company_refs=[], analysis_version="release-v2",
                                 analyzed_at="2026-09-15T05:00:00+00:00")], commit=True)
        with self.assertRaisesRegex(LoadError, "explicit NEWS reanalysis workflow"):
            self.load([analyzed_news(company_refs=[dict(KR)], analysis_version="release-v1",
                                      analyzed_at="2026-09-15T04:00:00+00:00")],
                      commit=True, batch_id="different-analysis")
        self.assertEqual(self.count("company_document"), 0)
        self.assertEqual(self.count("document_load_batch"), 1)

    def test_historical_duplicate_checkpoints_without_replacing_live_analysis(self):
        self.load([analyzed_news(company_refs=[dict(KR)], confidence=0.8)], commit=True)
        result = self.load([analyzed_news(company_refs=[dict(US)], confidence=0.95,
                                          analysis_version="news-historical-company-mentions-1.0",
                                          analyzed_at="2026-09-20T03:00:00+00:00",
                                          collected_at="2026-09-20T02:00:00+00:00",
                                          content_hash="c" * 64)],
                           commit=True, batch_id="historical-duplicate")
        self.assertEqual((result["company_links_added"], result["company_links_updated"],
                          result["company_links_deleted"]), (0, 0, 0))
        self.assertEqual(self.connection.execute(
            "SELECT analysis_version FROM source_document").fetchone()[0],
            "news-company-mentions-1.0")
        self.assertEqual(self.connection.execute(
            "SELECT company_id, confidence::text FROM company_document").fetchall(),
            [(self.kr_id, "0.800000")])
        self.assertEqual(self.count("document_load_batch"), 2)

    def test_newer_analysis_clears_derived_scores_and_hides_the_pair_again(self):
        """Visibility follows the verdict. A newer analysis invalidates the
        sentiment, so leaving the pair visible would put a headline on screen
        whose sentiment had just gone NULL underneath it."""
        self.load([analyzed_news(confidence=0.7)], commit=True)
        self.connection.execute("""UPDATE company_document SET is_service_visible=TRUE,
                                relevance_score=0.8, sentiment='POSITIVE', impact_score=0.6""")
        self.load([analyzed_news(confidence=0.95, analyzed_at="2026-09-15T04:00:00+00:00")],
                  commit=True, batch_id="same-analysis-new-output")
        stored = self.connection.execute("""
            SELECT confidence::text, is_service_visible, relevance_score, sentiment, impact_score
            FROM company_document
            """).fetchone()
        self.assertEqual(stored, ("0.950000", False, None, None, None))

    def test_republished_identical_analysis_keeps_ai_sentiment_and_visibility(self):
        self.load([analyzed_news(confidence=0.7)], commit=True)
        self.connection.execute("""UPDATE company_document SET relevance_score=0.8,
                                sentiment='POSITIVE', impact_score=0.6, confidence=0.55,
                                is_service_visible=TRUE""")
        self.load([analyzed_news(confidence=0.7)], commit=True, batch_id="republished-same-analysis")
        stored = self.connection.execute("""
            SELECT relevance_score::text, sentiment, impact_score::text, confidence::text,
                   is_service_visible FROM company_document
            """).fetchone()
        self.assertEqual(stored, ("0.800000", "POSITIVE", "0.600000", "0.550000", True))

    def test_parallel_same_batch_commits_once(self):
        from concurrent.futures import ThreadPoolExecutor
        import psycopg
        from psycopg import sql

        def publish():
            with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as connection:
                connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
                connection.execute("SET lock_timeout = '10s'")
                return load_documents(connection, four_records(), batch_id="parallel",
                                      manifest_sha256="a" * 64, source_uri="hdfs://fixture/parallel",
                                      sources=SOURCES, register_sources=True, commit=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: publish(), range(2)))
        self.assertEqual(sorted(result["already_loaded"] for result in results), [False, True])
        self.assertEqual(self.count("source_document"), 4)
        self.assertEqual(self.count("document_load_batch"), 1)

    def test_existing_source_registration_is_required_without_flag(self):
        with self.assertRaisesRegex(LoadError, "not registered"):
            self.load(commit=True, register_sources=False)
        self.assertEqual(self.count("data_source"), 0)
        self.assertEqual(self.count("source_document"), 0)
        self.load(commit=True)
        self.assertEqual(self.load(commit=True, batch_id="next", register_sources=False)["sources_created"], 0)

    def test_source_metadata_conflict_and_inactive_source_fail(self):
        self.load(commit=True)
        with self.assertRaisesRegex(LoadError, "metadata"):
            self.load(commit=True, batch_id="next", sources={**SOURCES,
                      "KR_NEWS": {**SOURCES["KR_NEWS"], "source_type": "OTHER"}})
        self.connection.execute("UPDATE data_source SET is_active = false")
        with self.assertRaisesRegex(LoadError, "active"):
            self.load(commit=True, batch_id="next")
        self.assertEqual(self.count("document_load_batch"), 1)

    def test_missing_inactive_or_wrong_market_company_prevents_writes(self):
        for reference in ({"market": "NASDAQ", "stock_code": "005930"}, {"market": "KOSPI", "stock_code": "999999"}):
            with self.subTest(reference=reference), self.assertRaises(CompanyMappingError):
                self.load([news(company_refs=[reference])], commit=True)
        self.connection.execute("UPDATE company SET status = 'INACTIVE' WHERE company_id = %s", (self.us_id,))
        with self.assertRaises(CompanyMappingError):
            self.load(commit=True)
        for table in ("source_document", "data_source", "document_load_batch"):
            self.assertEqual(self.count(table), 0, table)

    def test_late_database_failure_rolls_back_documents_sources_links_and_receipt(self):
        self.connection.execute("""CREATE FUNCTION reject_sec_fixture() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.filing_system = 'SEC' THEN RAISE EXCEPTION 'late fixture failure'; END IF;
            RETURN NEW; END $$""")
        self.connection.execute("CREATE TRIGGER reject_sec BEFORE INSERT ON disclosure FOR EACH ROW EXECUTE FUNCTION reject_sec_fixture()")
        with self.assertRaisesRegex(Exception, "late fixture failure"):
            self.load(commit=True)
        for table in ("source_document", "news_article", "disclosure", "company_document", "data_source", "document_load_batch"):
            self.assertEqual(self.count(table), 0, table)

    def test_active_real_transaction_is_left_open_with_its_changes_intact(self):
        self.connection.execute("BEGIN")
        self.connection.execute("UPDATE company SET name = 'Caller transaction' WHERE company_id = %s", (self.kr_id,))
        with self.assertRaisesRegex(LoadError, "idle connection"):
            self.load(commit=True)
        self.assertEqual(self.connection.info.transaction_status, 2)
        self.assertEqual(self.connection.execute("SELECT name FROM company WHERE company_id = %s", (self.kr_id,)).fetchone()[0], "Caller transaction")
        self.connection.rollback()

    def test_non_autocommit_idle_connection_has_same_transaction_guarantees(self):
        self.connection.autocommit = False
        self.load()
        self.assertEqual(self.connection.info.transaction_status, 0)
        self.load(commit=True)
        self.assertEqual(self.connection.info.transaction_status, 0)
        self.connection.autocommit = True
        self.assertEqual(self.count("source_document"), 4)

    def test_existing_legacy_news_id_and_analysis_are_preserved(self):
        self.load([news()], commit=True)
        document_id = self.connection.execute("SELECT document_id FROM news_article").fetchone()[0]
        self.connection.execute("UPDATE source_document SET status='ANALYZED', analysis_version='model-7', "
                                "hdfs_clean_uri='hdfs://fixture/clean/1' WHERE document_id=%s", (document_id,))
        self.connection.execute("UPDATE company_document SET relevance_score=0.8, sentiment='POSITIVE', "
                                "model_version='model-7', is_service_visible=false")
        self.load([news(summary=None, author="New author", company_refs=[KR, US])], commit=True, batch_id="next")
        stored = self.connection.execute("SELECT document_id, status, analysis_version, hdfs_clean_uri, summary FROM source_document").fetchone()
        self.assertEqual(stored, (document_id, "ANALYZED", "model-7", "hdfs://fixture/clean/1", "Fixture summary"))
        analyzed = self.connection.execute("SELECT sentiment, model_version, is_service_visible FROM company_document "
                                          "WHERE company_id=%s", (self.kr_id,)).fetchone()
        self.assertEqual(analyzed, ("POSITIVE", "model-7", False))
        self.assertEqual(self.count("company_document"), 1)

    def test_filing_issuer_conflict_rolls_back_other_changes(self):
        self.load([sec()], commit=True)
        with self.assertRaises(CompanyMappingError):
            self.load([news(), sec(filing_company=KR)], commit=True, batch_id="next")
        self.assertEqual(self.count("source_document"), 1)
        self.assertEqual(self.count("document_load_batch"), 1)

    def test_sec_co_registrants_share_accession_with_distinct_cik_identities(self):
        second_company = {"market": "NASDAQ", "stock_code": "MSFT"}
        self.connection.execute("INSERT INTO company (company_id, name, market, stock_code) "
                                "VALUES (%s, 'Fixture second US issuer', 'NASDAQ', 'MSFT')", (uuid4(),))
        shared_accession = "0001109357-22-000001"
        records = [sec(sec_cik="0001109357", sec_accession_no=shared_accession),
                   sec(sec_cik="0001868275", sec_accession_no=shared_accession,
                       filing_company=second_company, company_refs=[second_company])]
        result = self.load(records, commit=True)
        self.assertEqual(result["document_count"], 2)
        self.assertEqual(result["inserted_documents"], 2)
        stored = self.connection.execute("SELECT document_id, sec_cik, sec_accession_no FROM disclosure ORDER BY sec_cik").fetchall()
        self.assertEqual([(row[1], row[2]) for row in stored],
                         [("0001109357", shared_accession), ("0001868275", shared_accession)])
        self.assertNotEqual(stored[0][0], stored[1][0])
        self.assertTrue(self.load(records, commit=True)["already_loaded"])
        again = self.load(records, commit=True, batch_id="second-observation")
        self.assertEqual(again["inserted_documents"], 0)
        self.assertEqual(again["updated_documents"], 0)
        self.assertEqual(self.count("disclosure"), 2)

    def test_older_news_keeps_newer_content_and_ignores_mentions_from_different_body(self):
        newest = news(title="New title", summary="New summary", publisher="New publisher", author="New author",
                      collected_at="2026-09-16T02:00:00+00:00", published_at="2026-09-16T01:00:00+00:00",
                      content_hash="e" * 64, hdfs_raw_uri="hdfs://fixture/data/news/new.jsonl")
        self.load([newest], commit=True)
        self.load([news(company_refs=[US])], commit=True, batch_id="late-history")
        row = self.connection.execute("""SELECT title, summary, content_hash, hdfs_raw_uri,
                         (published_at AT TIME ZONE 'UTC')::text,
                         (first_collected_at AT TIME ZONE 'UTC')::text,
                         (last_collected_at AT TIME ZONE 'UTC')::text,
                         publisher, author FROM source_document JOIN news_article USING(document_id)""").fetchone()
        self.assertEqual(row[:4], ("New title", "New summary", "e" * 64, "hdfs://fixture/data/news/new.jsonl"))
        self.assertTrue(row[4].startswith("2026-09-16 01:00:00"))
        self.assertTrue(row[5].startswith("2026-09-15 02:00:00"))
        self.assertTrue(row[6].startswith("2026-09-16 02:00:00"))
        self.assertEqual(row[7:], ("New publisher", "New author"))
        self.assertEqual(self.count("company_document"), 1)
        self.assertEqual(self.count("document_load_batch"), 2)

    def test_older_news_can_add_mentions_when_verified_body_hash_matches(self):
        self.load([news(collected_at="2026-09-16T02:00:00+00:00", title="Newest metadata")], commit=True)
        self.load([news(company_refs=[US])], commit=True, batch_id="late-discovery")
        self.assertEqual(self.count("company_document"), 2)
        self.assertEqual(self.connection.execute("SELECT title FROM source_document").fetchone()[0], "Newest metadata")

    def test_older_news_cannot_add_mentions_when_either_body_hash_is_unknown(self):
        for number, (stored_hash, incoming_hash) in enumerate(((None, "b" * 64), ("b" * 64, None), (None, None))):
            with self.subTest(stored_hash=stored_hash, incoming_hash=incoming_hash):
                url = f"https://kr.example/unknown-body/{number}"
                self.load([news(collected_at="2026-09-16T02:00:00+00:00", canonical_url=url,
                                original_url=url, content_hash=stored_hash)], commit=True, batch_id=f"new-{number}")
                self.load([news(canonical_url=url, original_url=url, content_hash=incoming_hash,
                                company_refs=[US])], commit=True, batch_id=f"old-{number}")
                links = self.connection.execute("""SELECT count(*) FROM company_document
                                  JOIN news_article USING(document_id) WHERE canonical_url=%s""", (url,)).fetchone()[0]
                self.assertEqual(links, 1)

    def test_older_disclosure_cannot_replace_newer_report_details(self):
        self.load([sec(collected_at="2026-09-16T02:00:00+00:00", report_name="New report",
                       report_code="10-K/A", filing_date="2026-09-16", correction_status="CORRECTED",
                       content_hash="e" * 64)], commit=True)
        self.load([sec()], commit=True, batch_id="late-history")
        row = self.connection.execute("SELECT report_name, report_code, filing_date::text, correction_status FROM disclosure").fetchone()
        self.assertEqual(row, ("New report", "10-K/A", "2026-09-16", "CORRECTED"))

    def test_collector_revision_cannot_replace_protected_body_or_block_batch(self):
        self.load([news()], commit=True)
        protected_states = [("ANALYZED", None, None), ("COLLECTED", "model-7", None),
                            ("COLLECTED", None, "hdfs://fixture/clean/1")]
        for state in protected_states:
            with self.subTest(state=state):
                self.connection.execute("UPDATE source_document SET status=%s, analysis_version=%s, hdfs_clean_uri=%s", state)
                extra = news(canonical_url="https://kr.example/story/2", original_url="https://kr.example/story/2")
                changed = news(collected_at="2026-09-16T02:00:00+00:00", content_hash="e" * 64, title="Changed")
                self.load([extra, changed], commit=True, batch_id="changed-content-" + str(state))
                self.assertEqual(self.count("source_document"), 2)
                self.assertEqual(self.count("document_load_batch"), 2)
                self.assertEqual(self.connection.execute("SELECT content_hash FROM source_document").fetchone()[0], "b" * 64)

    def test_newer_analyzed_revision_atomically_replaces_body_and_company_set(self):
        self.load([analyzed_news(company_refs=[dict(KR)])], commit=True)
        self.connection.execute("""UPDATE source_document SET hdfs_clean_uri='hdfs://fixture/clean/old'
                                   WHERE document_type='NEWS'""")
        self.connection.execute("""UPDATE company_document SET relevance_score=0.8,
                                   sentiment='POSITIVE', impact_score=0.6""")
        revised = analyzed_news(
            title="Revised analyzed body", content_hash="e" * 64,
            hdfs_raw_uri="hdfs://fixture/data/news/revised.parquet",
            collected_at="2026-09-16T02:00:00+00:00",
            analyzed_at="2026-09-16T03:00:00+00:00",
            company_refs=[dict(US)],
            company_links=[{**US, "confidence": 0.95}],
        )
        result = self.load([revised], commit=True, batch_id="analyzed-revision")
        stored = self.connection.execute("""SELECT title, content_hash, hdfs_raw_uri, hdfs_clean_uri,
                                            status, analysis_version FROM source_document""").fetchone()
        self.assertEqual(stored, ("Revised analyzed body", "e" * 64,
                                  "hdfs://fixture/data/news/revised.parquet", None,
                                  "ANALYZED", "news-company-mentions-1.0"))
        links = self.connection.execute("""SELECT company_id, confidence::text, relevance_score,
                                           sentiment, impact_score FROM company_document""").fetchall()
        self.assertEqual(links, [(self.us_id, "0.950000", None, None, None)])
        self.assertEqual((result["company_links_added"], result["company_links_deleted"]), (1, 1))

    def test_older_analysis_cannot_replace_a_newer_analyzed_revision(self):
        self.load([analyzed_news(analyzed_at="2026-09-16T05:00:00+00:00")], commit=True)
        revised = analyzed_news(
            content_hash="e" * 64, collected_at="2026-09-16T06:00:00+00:00",
            analyzed_at="2026-09-16T04:00:00+00:00",
        )
        with self.assertRaisesRegex(LoadError, "older analysis"):
            self.load([revised], commit=True, batch_id="stale-analyzed-revision")
        self.assertEqual(self.connection.execute(
            "SELECT content_hash FROM source_document").fetchone()[0], "b" * 64)

    def test_changed_collected_news_body_preserves_document_mentions_and_receipt(self):
        self.load([news()], commit=True)
        tables = ("source_document", "company_document", "document_load_batch")
        before = {table: self.connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
        extra = news(canonical_url="https://kr.example/story/2", original_url="https://kr.example/story/2")
        changed = news(collected_at="2026-09-16T02:00:00+00:00", content_hash="e" * 64,
                       title="Revised body mentions another company", company_refs=[US],
                       hdfs_raw_uri="hdfs://fixture/data/news/revised.jsonl")
        with self.assertRaisesRegex(LoadError, "explicit revision/reanalysis workflow"):
            self.load([extra, changed], commit=True, batch_id="new-revision")
        for table in tables:
            self.assertEqual(self.connection.execute(f"SELECT * FROM {table}").fetchall(), before[table], table)
        self.assertEqual(self.connection.execute("SELECT company_id, mention_type FROM company_document").fetchall(),
                         [(self.kr_id, "MENTION")])

    def test_equal_collection_time_with_different_body_hash_is_rejected(self):
        self.load([news()], commit=True)
        with self.assertRaisesRegex(LoadError, "same collected_at.*conflicting content_hash"):
            self.load([news(content_hash="e" * 64)], commit=True, batch_id="ambiguous-revision")
        self.assertEqual(self.connection.execute("SELECT content_hash FROM source_document").fetchone()[0], "b" * 64)
        self.assertEqual(self.count("document_load_batch"), 1)

    def test_canonical_news_identity_reuses_legacy_document_uuid(self):
        source_id, document_id = uuid4(), uuid4()
        source = SOURCES["KR_NEWS"]
        self.connection.execute("INSERT INTO data_source (source_id, name, source_type, base_url) VALUES (%s,%s,%s,%s)",
                                (source_id, source["name"], source["source_type"], source["base_url"]))
        self.connection.execute("INSERT INTO source_document (document_id, source_id, document_type, title, original_url, status) "
                                "VALUES (%s,%s,'NEWS','Legacy','https://kr.example/story/1','COLLECTED')", (document_id, source_id))
        self.connection.execute("INSERT INTO news_article (document_id, canonical_url, canonical_url_hash) VALUES (%s,%s,%s)",
                                (document_id, "https://kr.example/story/1", hashlib.sha256(b"https://kr.example/story/1").hexdigest()))
        result = self.load([news()], commit=True)
        self.assertEqual(result["inserted_documents"], 0)
        self.assertEqual(self.connection.execute("SELECT document_id FROM news_article").fetchone()[0], document_id)

    def test_migration_preserves_legacy_dart_insert_and_rejects_invalid_identities(self):
        self.load([dart()], commit=True)
        original = self.connection.execute("SELECT document_id, filing_company_id FROM disclosure").fetchone()
        self.connection.execute("DELETE FROM disclosure")
        self.connection.execute("INSERT INTO disclosure (document_id, filing_company_id, dart_receipt_no, report_name, filing_date) "
                                "VALUES (%s,%s,'20260915000001','Legacy DART','2026-09-15')", original)
        self.assertEqual(self.connection.execute("SELECT filing_system FROM disclosure").fetchone()[0], "DART")
        for statement in ("UPDATE disclosure SET dart_receipt_no=NULL",
                          "UPDATE disclosure SET filing_system='SEC'",
                          "UPDATE disclosure SET filing_system='OTHER'"):
            with self.subTest(statement=statement), self.assertRaises(Exception):
                self.connection.execute(statement)

    def test_empty_verified_batch_can_be_checkpointed(self):
        result = self.load([], commit=True)
        self.assertEqual(result["document_count"], 0)
        self.assertEqual(self.count("document_load_batch"), 1)
        self.assertEqual(self.count("source_document"), 0)


if __name__ == "__main__":
    unittest.main()
