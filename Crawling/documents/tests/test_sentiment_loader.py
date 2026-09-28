"""Sentiment Loader weighting rules and PostgreSQL enrichment behaviour."""
from __future__ import annotations

import os
from pathlib import Path
import unittest
import unittest.mock
from uuid import uuid4

from services.sentiment_loader.metrics import WINDOWS, rebuild_metrics
from services.sentiment_loader.postgres import SentimentLoadError, load_sentiment
from services.sentiment_loader.weighting import (WeightingError, company_rows, confidence,
                                                 relevance)

HASH = "a" * 64
OTHER_HASH = "b" * 64
MODEL_VERSION = "evidence-sentence-finbert-v1"


def sentences(count, label="POSITIVE"):
    return [{"sentence_order": index + 1, "source": "body", "start": 0, "end": 5,
             "text": f"문장{index}", "language": "ko", "label": label, "confidence": 0.9}
            for index in range(count)]


def verdict(stock_code="005930", n=2, label="POSITIVE", status="analyzed", **changes):
    item = {"market": "KOSPI", "stock_code": stock_code, "label": label,
            "score": 0.8, "probabilities": [0.1, 0.1, 0.8], "status": status,
            "n_sentences": n, "model_role": "sentiment_ko", "model_revision": "k" * 40,
            "evidence": sentences(n, label)}
    item.update(changes)
    return item


def enriched(verdicts=None, **changes):
    record = {"url_hash": HASH,
              "ai_company_sentiment": [verdict()] if verdicts is None else verdicts}
    record.update(changes)
    return record


class WeightingTests(unittest.TestCase):
    def test_more_evidence_raises_relevance_then_saturates(self):
        """Depth saturates at four sentences: p99 is four, so beyond that an
        article is repeating a name rather than saying more about the company."""
        scores = [relevance(n, 1) for n in (1, 2, 3, 4)]
        self.assertEqual(scores, sorted(scores))
        self.assertEqual(relevance(4, 1), relevance(40, 1))

    def test_more_companies_in_one_article_lowers_each_share(self):
        self.assertGreater(relevance(2, 1), relevance(2, 2))
        self.assertGreater(relevance(2, 2), relevance(2, 8))

    def test_relevance_matches_the_published_formula(self):
        self.assertAlmostEqual(relevance(1, 1), 0.45)
        self.assertAlmostEqual(relevance(4, 1), 1.00)
        self.assertAlmostEqual(relevance(1, 8), 0.275)

    def test_confidence_rises_with_evidence_and_is_capped(self):
        self.assertAlmostEqual(confidence(1), 0.55)
        self.assertAlmostEqual(confidence(4), 1.00)
        self.assertEqual(confidence(40), 1.0)

    def test_impact_is_the_sign_only(self):
        """Not polarity scaled by relevance: the 174,537 rows already in the
        service use the sign, and a company's history must stay comparable."""
        for label, expected in (("POSITIVE", 1.0), ("NEGATIVE", -1.0), ("NEUTRAL", 0.0)):
            row = company_rows(enriched([verdict(label=label)]))[0]
            self.assertEqual(row["sentiment"], label)
            self.assertEqual(row["impact_score"], expected)

    def test_scores_stay_inside_the_check_constraints(self):
        for n in (1, 2, 4, 40):
            for count in (1, 2, 9):
                verdicts = [verdict(stock_code=f"{index:06d}", n=n) for index in range(count)]
                for row in company_rows(enriched(verdicts)):
                    self.assertGreaterEqual(row["relevance_score"], 0)
                    self.assertLessEqual(row["relevance_score"], 1)
                    self.assertGreaterEqual(row["impact_score"], -1)
                    self.assertLessEqual(row["impact_score"], 1)
                    self.assertGreaterEqual(row["confidence"], 0)
                    self.assertLessEqual(row["confidence"], 1)

    def test_companies_in_one_article_can_disagree(self):
        rows = company_rows(enriched([verdict("005930", label="POSITIVE"),
                                      verdict("000660", label="NEGATIVE")]))
        self.assertEqual({row["stock_code"]: row["sentiment"] for row in rows},
                         {"005930": "POSITIVE", "000660": "NEGATIVE"})

    def test_unresolved_verdicts_produce_no_row_at_all(self):
        """A conflicting or unattributable company is left NULL rather than
        given a neutral label and an extrapolated relevance."""
        for status in ("conflicting_polarity", "tied_probabilities",
                       "no_attributed_sentence"):
            self.assertEqual(company_rows(enriched([verdict(status=status)])), [])

    def test_a_skipped_company_still_divides_the_share(self):
        rows = company_rows(enriched([verdict("005930", n=2),
                                      verdict("000660", status="conflicting_polarity")]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relevance_score"], round(relevance(2, 2), 6))

    def test_evidence_sentences_are_carried_to_the_loader(self):
        row = company_rows(enriched([verdict(n=3)]))[0]
        self.assertEqual([item["sentence_order"] for item in row["evidence"]], [1, 2, 3])
        self.assertEqual(row["evidence"][0]["sentence_text"], "문장0")
        self.assertAlmostEqual(row["evidence"][0]["confidence"], 0.9)

    def test_article_with_no_verdicts_yields_nothing(self):
        self.assertEqual(company_rows(enriched([])), [])

    def test_missing_url_hash_is_rejected(self):
        with self.assertRaises(WeightingError):
            company_rows(enriched(url_hash=None))

    def test_analyzed_verdict_without_evidence_is_rejected(self):
        with self.assertRaises(WeightingError):
            company_rows(enriched([verdict(n=0, evidence=[])]))


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class SentimentPostgreSQLTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.schema = "sentiment_loader_test_" + uuid4().hex
        self.connection = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        migration = Path(__file__).resolve().parents[3] / "BackEnd/src/main/resources/db/migration"
        # V7 and V8 build cosmos_analysis by reading public.* directly, so they
        # cannot run inside this throwaway schema and the loader writes none of
        # what they add. V9 must run: it is what widens the window_type check to
        # the 1Y and 10Y the API now serves.
        for path in sorted(migration.glob("V[1-6]__*.sql")) + sorted(migration.glob("V9__*.sql")):
            self.connection.execute(path.read_text(encoding="utf-8"))
        self.company_id, self.document_id = uuid4(), uuid4()
        self.source_id = uuid4()
        self.connection.execute("""INSERT INTO company (company_id, name, market, stock_code)
                                   VALUES (%s, 'Fixture KR', 'KOSPI', '005930')""", (self.company_id,))
        self.connection.execute("""INSERT INTO data_source (source_id, name, source_type, base_url)
                                   VALUES (%s, 'Fixture news', 'NEWS', 'https://kr.example')""",
                                (self.source_id,))
        self.connection.execute("""
            INSERT INTO source_document (document_id, source_id, document_type, title,
                                         original_url, published_at, content_hash, status)
            VALUES (%s, %s, 'NEWS', 'Fixture', 'https://kr.example/1',
                    '2026-09-15T01:00:00+00:00', %s, 'ANALYZED')""",
            (self.document_id, self.source_id, "c" * 64))
        self.connection.execute("""
            INSERT INTO news_article (document_id, canonical_url, canonical_url_hash)
            VALUES (%s, 'https://kr.example/1', %s)""", (self.document_id, HASH))
        # FALSE is what the mention loader now inserts: a matched pair is not a
        # published pair until this loader has given it a verdict.
        self.connection.execute("""
            INSERT INTO company_document (document_id, company_id, mention_type, confidence,
                                          model_version, is_service_visible, analyzed_at)
            VALUES (%s, %s, 'MENTION', 0.9, 'dict-v1.3', FALSE, '2026-09-20T22:26:00+00:00')""",
            (self.document_id, self.company_id))

    def tearDown(self):
        from psycopg import sql
        self.connection.rollback()
        self.connection.execute("SET search_path TO public")
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.connection.close()

    def rows(self, **changes):
        row = {"url_hash": HASH, "market": "KOSPI", "stock_code": "005930",
               "sentiment": "POSITIVE", "relevance_score": 0.9, "impact_score": 1.0,
               "confidence": 0.7,
               "evidence": [{"sentence_order": 1, "sentence_text": "삼성전자는 이익이 늘었다.",
                             "confidence": 0.92},
                            {"sentence_order": 4, "sentence_text": "증설도 예고했다.",
                             "confidence": 0.61}]}
        row.update(changes)
        return [row]

    def stored(self):
        columns = ("sentiment", "relevance_score", "impact_score", "confidence",
                   "model_version", "is_service_visible")
        values = self.connection.execute(
            "SELECT sentiment, relevance_score::text, impact_score::text, confidence::text,"
            " model_version, is_service_visible FROM company_document").fetchone()
        return dict(zip(columns, values))

    def evidence(self):
        return self.connection.execute(
            "SELECT sentence_text, sentence_order, confidence::text, model_version "
            "FROM document_evidence ORDER BY sentence_order").fetchall()

    def test_commit_fills_sentiment_on_the_existing_mention_row(self):
        report = load_sentiment(self.connection, self.rows(),
                                model_version=MODEL_VERSION, commit=True)
        self.assertEqual((report["resolved"], report["updated"], report["unmatched"]), (1, 1, 0))
        self.assertEqual(self.stored() | {"model_version": "dict-v1.3"}, {
            "sentiment": "POSITIVE", "relevance_score": "0.900000",
            "impact_score": "1.000000", "confidence": "0.700000",
            "model_version": "dict-v1.3", "is_service_visible": True})

    def test_evidence_sentences_are_written_with_their_own_model_version(self):
        """document_evidence carries the sentiment's contract name; the pair row
        keeps the matcher's, so the two producers stay distinguishable."""
        load_sentiment(self.connection, self.rows(), model_version=MODEL_VERSION, commit=True)
        self.assertEqual(self.evidence(), [
            ("삼성전자는 이익이 늘었다.", 1, "0.920000", MODEL_VERSION),
            ("증설도 예고했다.", 4, "0.610000", MODEL_VERSION)])

    def test_analysis_is_what_makes_the_article_visible(self):
        self.assertIs(self.stored()["is_service_visible"], False)
        report = load_sentiment(self.connection, self.rows(),
                                model_version=MODEL_VERSION, commit=True)
        self.assertEqual(report["published"], 1)
        self.assertIs(self.stored()["is_service_visible"], True)

    def test_a_pair_with_no_verdict_stays_hidden(self):
        """A batch that resolves nothing for this pair must leave it invisible
        rather than publishing a headline with an empty sentiment badge."""
        report = load_sentiment(self.connection, self.rows(url_hash=OTHER_HASH),
                                model_version=MODEL_VERSION, commit=True)
        self.assertEqual(report["published"], 0)
        self.assertIs(self.stored()["is_service_visible"], False)

    def test_another_producers_sentences_are_left_alone(self):
        """relationship_evidence holds a foreign key onto document_evidence with
        no cascade, so deleting a sentence a relation cites would fail the whole
        batch. Only this producer's own rows are replaced."""
        self.connection.execute("""
            INSERT INTO document_evidence
                (document_id, company_id, sentence_text, sentence_order, model_version)
            VALUES (%s, %s, '관계 근거 문장이다.', 99, 'news-relations-1.0')""",
            (self.document_id, self.company_id))
        load_sentiment(self.connection, self.rows(), model_version=MODEL_VERSION, commit=True)
        kept = self.connection.execute(
            "SELECT count(*) FROM document_evidence WHERE model_version='news-relations-1.0'"
        ).fetchone()[0]
        self.assertEqual(kept, 1)
        self.assertEqual(len(self.evidence()), 3)

    def test_rerun_replaces_evidence_instead_of_appending(self):
        load_sentiment(self.connection, self.rows(), model_version=MODEL_VERSION, commit=True)
        load_sentiment(self.connection, self.rows(evidence=[
            {"sentence_order": 2, "sentence_text": "다시 계산한 문장.", "confidence": 0.5}]),
            model_version=MODEL_VERSION, commit=True)
        self.assertEqual(self.evidence(), [("다시 계산한 문장.", 2, "0.500000", MODEL_VERSION)])

    def test_enrichment_leaves_the_mention_model_version_alone(self):
        # Overwriting it would break both the loader's freshness guard and the
        # rule that keeps this sentiment alive when the same analysis republishes.
        load_sentiment(self.connection, self.rows(), model_version=MODEL_VERSION, commit=True)
        self.assertEqual(self.stored()["model_version"], "dict-v1.3")

    def test_replaying_the_same_batch_changes_nothing(self):
        load_sentiment(self.connection, self.rows(), model_version=MODEL_VERSION, commit=True)
        report = load_sentiment(self.connection, self.rows(),
                                model_version=MODEL_VERSION, commit=True)
        self.assertEqual((report["updated"], report["unchanged"], report["published"]), (0, 1, 0))
        self.assertEqual(len(self.evidence()), 2)

    def test_dry_run_exercises_the_write_and_keeps_nothing(self):
        report = load_sentiment(self.connection, self.rows(), model_version=MODEL_VERSION)
        self.assertEqual((report["resolved"], report["updated"], report["evidence"],
                          report["published"], report["status"]), (1, 1, 2, 1, "rolled_back"))
        # confidence still holds the matcher's fixture value: the rollback put
        # the row back exactly as the mention loader left it.
        self.assertEqual(self.stored(), {
            "sentiment": None, "relevance_score": None, "impact_score": None,
            "confidence": "0.900000", "model_version": "dict-v1.3",
            "is_service_visible": False})
        self.assertEqual(self.evidence(), [])

    def test_unknown_article_is_reported_not_inserted(self):
        report = load_sentiment(self.connection, self.rows(url_hash=OTHER_HASH),
                                model_version=MODEL_VERSION, commit=True)
        self.assertEqual((report["resolved"], report["unmatched"]), (0, 1))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM company_document").fetchone()[0], 1)

    def test_retracted_mention_is_not_resurrected(self):
        self.connection.execute("DELETE FROM company_document")
        report = load_sentiment(self.connection, self.rows(),
                                model_version=MODEL_VERSION, commit=True)
        self.assertEqual((report["resolved"], report["missing_link"], report["updated"]), (1, 1, 0))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM company_document").fetchone()[0], 0)
        self.assertEqual(self.evidence(), [])

    def test_inactive_company_is_left_alone(self):
        self.connection.execute("UPDATE company SET status = 'INACTIVE'")
        report = load_sentiment(self.connection, self.rows(),
                                model_version=MODEL_VERSION, commit=True)
        self.assertEqual((report["resolved"], report["unmatched"]), (0, 1))

    def test_empty_batch_and_bad_arguments_are_rejected_early(self):
        self.assertEqual(load_sentiment(self.connection, [], model_version="x")["status"], "empty")
        with self.assertRaises(SentimentLoadError):
            load_sentiment(self.connection, self.rows(), model_version=" ")

    # --- company_metric_history rollup -------------------------------------

    MEASURED_AT = "2026-09-21T12:00:00+00:00"

    def add_article(self, *, url_suffix, published_at, sentiment, impact):
        document_id = uuid4()
        self.connection.execute("""
            INSERT INTO source_document (document_id, source_id, document_type, title,
                                         original_url, published_at, content_hash, status)
            VALUES (%s, %s, 'NEWS', 'Fixture', %s, %s, %s, 'ANALYZED')""",
            (document_id, self.source_id, f"https://kr.example/{url_suffix}",
             published_at, f"{url_suffix:0>64}"))
        self.connection.execute("""
            INSERT INTO company_document (document_id, company_id, mention_type, sentiment,
                                          relevance_score, impact_score, analyzed_at)
            VALUES (%s, %s, 'MENTION', %s, 0.9, %s, %s)""",
            (document_id, self.company_id, sentiment, impact, published_at))

    def metric(self, window_type="30D"):
        return self.connection.execute(
            "SELECT news_mention_count, positive_count, negative_count, sentiment_score::text "
            "FROM company_metric_history WHERE window_type = %s", (window_type,)).fetchone()

    def test_all_positive_window_scores_near_one_and_all_negative_near_minus_one(self):
        self.connection.execute("DELETE FROM company_document")
        self.add_article(url_suffix="p1", published_at="2026-09-20T00:00:00+00:00",
                         sentiment="POSITIVE", impact=0.9)
        rebuild_metrics(self.connection, measured_at=self.MEASURED_AT, commit=True)
        self.assertEqual(self.metric(), (1, 1, 0, "0.900000"))

        self.connection.execute("UPDATE company_document SET sentiment='NEGATIVE', impact_score=-0.9")
        rebuild_metrics(self.connection, measured_at=self.MEASURED_AT, commit=True)
        self.assertEqual(self.metric(), (1, 0, 1, "-0.900000"))

    def test_cancelling_coverage_lands_on_zero(self):
        self.connection.execute("DELETE FROM company_document")
        self.add_article(url_suffix="p2", published_at="2026-09-20T00:00:00+00:00",
                         sentiment="POSITIVE", impact=0.6)
        self.add_article(url_suffix="n2", published_at="2026-09-20T00:00:00+00:00",
                         sentiment="NEGATIVE", impact=-0.6)
        rebuild_metrics(self.connection, measured_at=self.MEASURED_AT, commit=True)
        self.assertEqual(self.metric(), (2, 1, 1, "0.000000"))

    def test_windows_only_count_articles_published_inside_them(self):
        self.connection.execute("DELETE FROM company_document")
        self.add_article(url_suffix="recent", published_at="2026-09-20T00:00:00+00:00",
                         sentiment="POSITIVE", impact=0.5)
        self.add_article(url_suffix="old", published_at="2026-07-01T00:00:00+00:00",
                         sentiment="POSITIVE", impact=0.5)
        self.add_article(url_suffix="ancient", published_at="2024-01-05T00:00:00+00:00",
                         sentiment="POSITIVE", impact=0.5)
        rebuild_metrics(self.connection, measured_at=self.MEASURED_AT, commit=True)
        self.assertEqual(self.metric("30D")[0], 1)
        self.assertEqual(self.metric("1Y")[0], 2)
        self.assertEqual(self.metric("10Y")[0], 3)

    def test_hidden_and_unscored_links_are_excluded(self):
        self.connection.execute("DELETE FROM company_document")
        self.add_article(url_suffix="hidden", published_at="2026-09-20T00:00:00+00:00",
                         sentiment="POSITIVE", impact=0.5)
        self.connection.execute("UPDATE company_document SET is_service_visible = FALSE")
        self.add_article(url_suffix="nulled", published_at="2026-09-20T00:00:00+00:00",
                         sentiment=None, impact=None)
        rebuild_metrics(self.connection, measured_at=self.MEASURED_AT, commit=True)
        self.assertIsNone(self.metric())

    def test_rebuild_is_idempotent_and_dry_run_keeps_nothing(self):
        self.connection.execute("DELETE FROM company_document")
        self.add_article(url_suffix="p3", published_at="2026-09-20T00:00:00+00:00",
                         sentiment="POSITIVE", impact=0.4)
        report = rebuild_metrics(self.connection, measured_at=self.MEASURED_AT)
        self.assertEqual(report["status"], "rolled_back")
        self.assertIsNone(self.metric())

        rebuild_metrics(self.connection, measured_at=self.MEASURED_AT, commit=True)
        first = self.metric()
        rebuild_metrics(self.connection, measured_at=self.MEASURED_AT, commit=True)
        self.assertEqual(self.metric(), first)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM company_metric_history").fetchone()[0], len(WINDOWS))


if __name__ == "__main__":
    unittest.main()


class ConnectRetryTests(unittest.TestCase):
    """A busy shared role must cost seconds, not a whole ten-minute tick."""

    def setUp(self):
        import psycopg
        from services.sentiment_loader import runner
        self.runner, self.psycopg = runner, psycopg

    def test_waits_out_a_busy_role_then_succeeds(self):
        calls = []

        def connect(dsn):
            calls.append(dsn)
            if len(calls) < 3:
                raise self.psycopg.OperationalError(
                    'connection failed: FATAL:  too many connections for role "x"')
            return "connection"

        with unittest.mock.patch.object(self.psycopg, "connect", connect), \
             unittest.mock.patch("time.sleep") as sleep:
            self.assertEqual(
                self.runner.connect_when_free("dsn", attempts=4, backoff=0.01), "connection")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_other_failures_are_not_retried(self):
        def connect(dsn):
            raise self.psycopg.OperationalError("password authentication failed")

        with unittest.mock.patch.object(self.psycopg, "connect", connect), \
             unittest.mock.patch("time.sleep") as sleep:
            with self.assertRaises(self.psycopg.OperationalError):
                self.runner.connect_when_free("dsn", attempts=4, backoff=0.01)
        self.assertEqual(sleep.call_count, 0)

    def test_gives_up_after_the_last_attempt(self):
        def connect(dsn):
            raise self.psycopg.OperationalError("too many connections for role")

        with unittest.mock.patch.object(self.psycopg, "connect", connect), \
             unittest.mock.patch("time.sleep"):
            with self.assertRaises(self.psycopg.OperationalError):
                self.runner.connect_when_free("dsn", attempts=2, backoff=0.01)
