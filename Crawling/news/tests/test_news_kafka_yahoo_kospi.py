"""Yahoo Finance KOSPI-100 quote-news collector tests."""

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from services.news_pipeline.yahoo_kospi import (
    YahooKospiCollector,
    yahoo_kospi_news_url,
)


class FakeClient:
    def __init__(self, pages=None, errors=None):
        self.pages = pages or {}
        self.errors = errors or {}
        self.urls = []

    def assert_robots_allowed(self, url):
        return None

    def get_text(self, url):
        self.urls.append(url)
        if url in self.errors:
            raise self.errors[url]
        return self.pages[url]


class FakeSource:
    minimum_content_chars = 120

    def parse_listing(self, html):
        if html == "empty":
            return []
        return [SimpleNamespace(
            source="yahoo_finance",
            title="Samsung Electronics expands AI chip production",
            url="https://finance.yahoo.com/technology/articles/samsung-ai-chips.html",
            organization="Yahoo Finance",
        )]

    def parse_article(self, html, candidate):
        return SimpleNamespace(
            source="yahoo_finance", organization=candidate.organization,
            title=candidate.title, url=candidate.url,
            content="Samsung Electronics announced new semiconductor investment. " * 5,
            published_at="2026-09-18T01:00:00+00:00",
            crawled_at="2026-09-18T01:01:00+00:00",
        )


class FakeExtractor:
    def extract(self, article):
        return {
            "companies": [{
                "company_id": "DART_00126380", "name": "삼성전자", "market": "KRX",
                "ticker": "005930", "tickers": ["005930"],
                "explicit_tickers": [], "resolved_level": "company", "n_mentions": 1,
            }],
            "universe_version": "test", "registry_sha256": "a" * 64,
            "extractor_version": "test-v1",
            "membership_policy": "snapshot_universe_not_historical_membership",
        }


class FakeOutbox:
    def __init__(self):
        self.events = []
        self.urls = set()
        self.fail = False

    def seen_url(self, url):
        return url in self.urls

    def enqueue(self, event):
        if self.fail:
            raise OSError("outbox unavailable")
        self.events.append(event)
        self.urls.add(event["url"])
        return True


class YahooKospiCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "kospi100.json"
        companies = [{"ticker": "005930", "name": "삼성전자"}]
        companies.extend({"ticker": f"{index:06d}", "name": f"회사 {index}"}
                         for index in range(1, 100))
        self.config.write_text(json.dumps({
            "as_of": "2026-09-18", "companies": companies,
        }, ensure_ascii=False), encoding="utf-8")
        self.outbox = FakeOutbox()

    def collector(self, client, **kwargs):
        collector = YahooKospiCollector(
            SimpleNamespace(), self.temp.name, self.outbox,
            companies_config=str(self.config), company_extractor=FakeExtractor(),
            client=client, source=FakeSource(), cooldown_seconds=60, **kwargs,
        )
        self.addCleanup(collector.close)
        return collector

    def test_kospi_symbol_builds_yahoo_quote_news_url(self):
        self.assertEqual(
            yahoo_kospi_news_url("005930"),
            "https://finance.yahoo.com/quote/005930.KS/news/",
        )

    def test_collect_targets_one_of_100_feeds_and_emits_company_metadata(self):
        feed = yahoo_kospi_news_url("000001")
        article = "https://finance.yahoo.com/technology/articles/samsung-ai-chips.html"
        client = FakeClient({feed: "listing", article: "article"})
        collector = self.collector(client)
        result = collector.collect(limit=1)

        self.assertEqual(result["feed_count"], 100)
        self.assertEqual(result["discovery"]["ticker"], "000001")
        self.assertEqual(result["enqueued"], 1)
        event = self.outbox.events[0]
        self.assertEqual(event["region"], "overseas")
        self.assertEqual(event["language"], "en")
        self.assertEqual(event["metadata"]["discovery_method"],
                         "yahoo_kospi100_quote_news")
        self.assertEqual(event["metadata"]["discovery_tickers"], ["000001"])
        self.assertEqual(event["metadata"]["kospi100_companies"][0]["ticker"],
                         "005930")

    def test_existing_url_is_skipped_before_article_fetch(self):
        feed = yahoo_kospi_news_url("000001")
        article = "https://finance.yahoo.com/technology/articles/samsung-ai-chips.html"
        client = FakeClient({feed: "listing"})
        self.outbox.urls.add(article)
        collector = self.collector(client)
        result = collector.collect(limit=1)

        self.assertEqual(result["existing"], 1)
        self.assertNotIn(article, client.urls)

    def test_rate_limit_sets_durable_cooldown(self):
        feed = yahoo_kospi_news_url("000001")
        client = FakeClient(errors={feed: RuntimeError("HTTP 429 Too Many Requests")})
        collector = self.collector(client)
        first = collector.collect(limit=1)
        second = collector.collect(limit=1)

        self.assertIn("rate_limited_until", first)
        self.assertIn("rate_limited_until", second)
        self.assertEqual(client.urls, [feed])

    def test_outbox_failure_keeps_article_retryable(self):
        feed = yahoo_kospi_news_url("000001")
        article = "https://finance.yahoo.com/technology/articles/samsung-ai-chips.html"
        client = FakeClient({feed: "listing", article: "article"})
        self.outbox.fail = True
        collector = self.collector(client)
        with self.assertRaisesRegex(OSError, "outbox unavailable"):
            collector.collect(limit=1)
        row = collector.db.execute(
            "SELECT status,attempts FROM yahoo_kospi_candidates WHERE url=?", (article,)
        ).fetchone()
        self.assertEqual(tuple(row), ("pending", 0))


if __name__ == "__main__":
    unittest.main()
