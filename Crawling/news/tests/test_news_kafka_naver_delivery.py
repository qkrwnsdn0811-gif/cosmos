import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from services.news_pipeline.common import Outbox
from services.news_pipeline.naver_delivery import ArticleFetcher, NaverDelivery, company_mentions
from services.news_pipeline.writer import normalize, parquet_bytes


COMPANIES = [{"ticker": "005930", "name": "삼성전자"}, {"ticker": "003550", "name": "LG"},
             {"ticker": "066570", "name": "LG전자"}, {"ticker": "000660", "name": "SK하이닉스"}]


class Store:
    def __init__(self):
        self.rows = [{"url": "https://example.com/news/1", "title": "삼성전자 실적 발표",
                      "description": "삼성전자와 LG전자의 공급망 소식", "query_tickers": ["005930"],
                      "naver_url": "https://n.news.naver.com/article/1/1", "published_at": "2026-09-15T01:00:00+00:00"}]
        self.done, self.failed = [], []
    def pending(self, limit): return self.rows[:limit]
    def mark_done(self, url, status="done"): self.done.append((url, status))
    def mark_failed(self, url, error): self.failed.append((url, error))


def article(url):
    return {"url": url, "final_url": url, "title": "삼성전자 실적 발표", "organization": "테스트신문",
            "content": "삼성전자는 공급망 투자 계획을 발표했다. LG전자와 협력한다. " * 6,
            "published_at": None, "extraction_method": "json-ld", "truncated": False, "robots": {"allowed": True}}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.outbox = Outbox(Path(self.temp.name) / "outbox.db")
        self.store = Store()
    def tearDown(self):
        self.outbox.close()
        self.temp.cleanup()
    def deliver(self, fetch=article):
        return NaverDelivery(self.store, COMPANIES, self.outbox, fetch).collect()

    def test_full_article_multiple_mentions_existing_writer_contract(self):
        self.assertEqual(self.deliver()["enqueued"], 1)
        payload = self.outbox.db.execute("SELECT payload FROM outbox").fetchone()[0]
        event = json.loads(payload)
        self.assertEqual(event["metadata"]["content_kind"], "full_article")
        self.assertEqual({item["ticker"] for item in event["metadata"]["matched_companies"]}, {"005930", "066570"})
        self.assertEqual(event["content"], article("")["content"])
        self.assertNotEqual(event["content"], self.store.rows[0]["description"])
        row, _ = normalize(payload.encode(), "news.raw", 0, 4)
        self.assertEqual(row["source"], "naver_news_search")
        self.assertTrue(parquet_bytes([row]))

    def test_replay_deduplicates_after_enqueue_before_checkpoint(self):
        self.deliver()
        self.store.done.clear()
        def no_fetch(url): raise AssertionError("must use durable outbox before fetching")
        self.assertEqual(self.deliver(no_fetch)["existing"], 1)
        self.assertEqual(self.outbox.stats()["pending"], 1)

    def test_missing_full_body_never_uses_search_description_as_body(self):
        self.store.rows[0]["description"] = "삼성전자 검색 요약입니다. " * 100
        self.assertEqual(self.deliver(lambda url: {"content": ""})["failed"], 1)
        self.assertEqual(self.outbox.stats()["pending"], 0)
        self.assertEqual(len(self.store.failed), 1)

    def test_api_query_without_company_mention_is_filtered(self):
        self.store.rows[0].update(title="일반 경제 기사", description="산업 소식")
        def unrelated(url):
            item = article(url)
            item.update(title="산업 소식", content="이 기사는 대상 기업을 언급하지 않는 일반 경제 기사입니다. " * 6)
            return item
        self.assertEqual(self.deliver(unrelated)["filtered"], 1)
        self.assertEqual(self.outbox.stats()["pending"], 0)

    def test_company_in_api_metadata_cannot_validate_an_interstitial_body(self):
        def blocked(url):
            return {"title": "Access required", "content": "Please enable cookies and sign in to continue. " * 20}
        self.assertEqual(self.deliver(blocked)["filtered"], 1)
        self.assertEqual(self.outbox.stats()["pending"], 0)

    def test_short_ascii_names_do_not_match_subsidiaries_or_words(self):
        mentions = company_mentions(COMPANIES, title="LG전자는 성장했다. ALG라는 단어. 삼성전자서비스 소식.")
        self.assertEqual([item["ticker"] for item in mentions], ["066570"])
        self.assertEqual([item["ticker"] for item in company_mentions(COMPANIES, title="LG는 투자했다.")], ["003550"])

    def test_normalizes_width_case_and_korean_particles(self):
        matches = company_mentions(COMPANIES, content="ＳＫ하이닉스의 매출과 삼성전자, lg전자의 협력")
        self.assertEqual({item["ticker"] for item in matches}, {"005930", "066570", "000660"})

    def test_outbox_failure_does_not_exhaust_download_retries(self):
        with patch.object(self.outbox, "enqueue", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError): self.deliver()
        self.assertEqual(self.store.failed, [])
        self.assertEqual(self.store.done, [])

    def test_publisher_failure_is_sanitized_and_retryable(self):
        def failed(url): raise RuntimeError("untrusted response contains a secret")
        self.assertEqual(self.deliver(failed)["failed"], 1)
        self.assertEqual(self.store.failed[0][1], "RuntimeError")
        self.assertEqual(self.store.done, [])

    def test_credentials_not_in_publisher_child_environment(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps(article("https://example.com/a")), "")
        with patch.dict(os.environ, {"NAVER_CLIENT_ID": "private-id", "NAVER_CLIENT_SECRET": "private-secret", "NAVER_API_HUB_CLIENT_SECRET": "private-hub"}), patch("subprocess.run", return_value=completed) as run:
            ArticleFetcher("node", "bundle.mjs")("https://example.com/a")
            environment = run.call_args.kwargs["env"]
            self.assertNotIn("NAVER_CLIENT_SECRET", environment)
            self.assertNotIn("NAVER_CLIENT_ID", environment)
            self.assertNotIn("NAVER_API_HUB_CLIENT_SECRET", environment)
            self.assertEqual(run.call_args.args[0], ["node", "bundle.mjs", "--url", "https://example.com/a"])

    def test_uses_syndicated_naver_body_when_original_is_unavailable(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url == self.store.rows[0]["url"]: raise RuntimeError("publisher unavailable")
            return article(url)
        self.assertEqual(self.deliver(fetch)["enqueued"], 1)
        self.assertEqual(calls, [self.store.rows[0]["url"], self.store.rows[0]["naver_url"]])
        payload = json.loads(self.outbox.db.execute("SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(payload["url"], self.store.rows[0]["url"])
        self.assertEqual(payload["metadata"]["final_url"], self.store.rows[0]["naver_url"])

    def test_each_article_can_publish_without_waiting_for_entire_batch(self):
        calls = []
        NaverDelivery(self.store, COMPANIES, self.outbox, article,
                      after_enqueue=lambda: calls.append(self.outbox.stats()["pending"])).collect()
        self.assertEqual(calls, [1])

    def test_invalid_publisher_time_uses_aware_naver_time_with_provenance(self):
        def fetch(url):
            result = article(url)
            result["published_at"] = "2026-09-15 10:00"
            return result
        self.deliver(fetch)
        event = json.loads(self.outbox.db.execute("SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(event["published_at"], self.store.rows[0]["published_at"])
        self.assertEqual(event["metadata"]["publication_time_source"], "naver")
        self.assertEqual(event["metadata"]["publisher_published_at_original"], "2026-09-15 10:00")


if __name__ == "__main__": unittest.main()
