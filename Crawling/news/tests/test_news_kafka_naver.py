"""Naver collector tests: fake HTTP, temporary SQLite, no external requests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from services.news_pipeline.naver import (
    API_URL, HUB_API_URL, NaverApiError, NaverSearchClient, NaverStore, load_companies,
)

START = datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc).timestamp()
COMPANIES = [{"ticker": "005930", "name": "삼성전자"}, {"ticker": "000660", "name": "SK하이닉스"}]


class Clock:
    def __init__(self, value=START):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def item(index=1, *, stamp=START - 3600, **changes):
    result = {"title": "<b>검색 후보</b> &amp; 소식", "description": "회사명은 본문에만 있습니다.",
              "originallink": f"https://publisher.example/news?id={index}",
              "link": f"https://n.news.naver.com/mnews/article/001/{index:010d}",
              "pubDate": format_datetime(datetime.fromtimestamp(stamp, timezone.utc))}
    result.update(changes)
    return result


class FakeClient:
    application_fingerprint = "mock-app-fingerprint"

    def __init__(self, pages=None):
        self.pages = pages if pages is not None else []
        self.calls = []

    def search(self, query, *, start=1, display=100):
        self.calls.append((query, start, display))
        result = self.pages(query, start, display) if callable(self.pages) else self.pages.pop(0) if self.pages else {"items": [], "total": 0}
        if isinstance(result, Exception):
            raise result
        return {"start": start, "display": display, **result}


class Response:
    def __init__(self, body=None, *, status=200, headers=None):
        self.status_code, self.headers, self.closed = status, headers or {}, False
        payload = {"start": 1, "display": 100, "total": 1, "items": [item()]} if body is None else body
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), 17):
            yield self.body[offset:offset + 17]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, response):
        self.response, self.calls, self.closed = response, [], False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self):
        self.closed = True


class SearchClientTests(unittest.TestCase):
    def test_explicit_modes_have_fixed_endpoints_headers_and_unicode_query(self):
        for mode, endpoint, id_header, secret_header in (
            ("openapi", API_URL, "X-Naver-Client-Id", "X-Naver-Client-Secret"),
            ("hub", HUB_API_URL, "X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"),
        ):
            with self.subTest(mode=mode):
                response = Response()
                session = Session(response)
                client = NaverSearchClient("mock-id", "mock-secret", mode=mode, session=session)
                got = client.search("삼성전자")
                self.assertEqual(got["items"][0]["title"], "<b>검색 후보</b> &amp; 소식")
                url, request = session.calls[0]
                self.assertEqual(url, endpoint)
                self.assertEqual(request["headers"][id_header], "mock-id")
                self.assertEqual(request["headers"][secret_header], "mock-secret")
                self.assertEqual(request["params"]["query"], "삼성전자")
                self.assertEqual(request["params"]["sort"], "date")
                self.assertEqual(request["params"].get("format"), "json" if mode == "hub" else None)
                self.assertFalse(request["allow_redirects"])
                self.assertTrue(request["stream"])
                self.assertTrue(response.closed)
                client.close()
                self.assertTrue(session.closed)

    def test_redirects_never_forward_credentials_or_retry_another_provider(self):
        response = Response(status=302, headers={"Location": "https://untrusted.example/collect"})
        session = Session(response)
        with self.assertRaisesRegex(NaverApiError, "redirect_rejected"):
            NaverSearchClient("mock-id", "mock-secret", session=session).search("삼성전자")
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(response.closed)

    def test_http_auth_and_rate_limit_errors_are_sanitized(self):
        for status, expected in [(401, "authentication_failed"), (403, "authentication_failed"), (429, "rate_limited"), (503, "api_unavailable")]:
            with self.subTest(status=status):
                response = Response(b"mock-secret echoed by untrusted server", status=status, headers={"Retry-After": "123"})
                with self.assertRaises(NaverApiError) as raised:
                    NaverSearchClient("mock-id", "mock-secret", session=Session(response)).search("삼성전자")
                self.assertEqual(raised.exception.kind, expected)
                self.assertEqual(raised.exception.retry_after, 123)
                self.assertNotIn("mock-secret", str(raised.exception))
                self.assertTrue(response.closed)

    def test_transport_exception_does_not_expose_secrets(self):
        with self.assertRaises(NaverApiError) as raised:
            NaverSearchClient("mock-id", "mock-secret", session=Session(RuntimeError("mock-secret and headers"))).search("삼성전자")
        self.assertEqual(str(raised.exception), "naver_request_failed")

    def test_response_size_and_schema_are_bounded(self):
        responses = [Response(b" " * 1025), Response(headers={"Content-Length": "999999"}),
                     Response(b"not json"), Response({"items": [], "start": 2, "display": 100, "total": 0}),
                     Response({"items": [], "start": 1, "display": 100, "total": True}),
                     Response({"items": ["not an object"], "start": 1, "display": 100, "total": 1})]
        for response in responses:
            with self.subTest(body=response.body[:30]), self.assertRaises(NaverApiError):
                NaverSearchClient("mock-id", "mock-secret", session=Session(response), max_response_bytes=1024).search("삼성전자")
            self.assertTrue(response.closed)

    def test_parameters_reject_unsupported_mode_and_search_bounds(self):
        with self.assertRaises(ValueError):
            NaverSearchClient("mock-id", "mock-secret", mode="auto")
        client = NaverSearchClient("mock-id", "mock-secret", session=Session(Response()))
        for parameters in [{"start": 1001}, {"start": 0}, {"start": True}, {"display": 101}, {"display": 0}]:
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                client.search("삼성전자", **parameters)
        self.assertEqual(client.session.calls, [])


class CompanyConfigTests(unittest.TestCase):
    def test_repository_snapshot_loads_alphanumeric_krx_ticker(self):
        companies = load_companies(Path(__file__).resolve().parents[1] / "config" / "kospi100.json")
        self.assertEqual(len(companies), 100)
        self.assertIn({"ticker": "0126Z0", "name": "삼성에피스홀딩스"}, companies)
        with tempfile.TemporaryDirectory() as directory:
            store = NaverStore(Path(directory) / "naver.sqlite3", companies, now=Clock())
            try:
                self.assertEqual(len(store.stats()["companies"]), 100)
            finally:
                store.close()

    def test_alphanumeric_tickers_preserve_format_and_uniqueness_checks(self):
        companies = [{"ticker": f"{i:06d}", "name": f"기업{i}"} for i in range(100)]
        companies[0] = {"ticker": "0126Z0", "name": "삼성에피스홀딩스"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kospi100.json"
            payload = {"as_of": "2026-09-15", "companies": companies}
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(load_companies(path), companies)
            for ticker in ["0126z0", "012Z0", "00126Z0", "012-Z0", " 126Z0", "0126Z0 ",
                           "０126Z0", 126000, None, True, "000001"]:
                with self.subTest(ticker=ticker):
                    invalid = [{"ticker": ticker, "name": "기업"}] + companies[1:]
                    path.write_text(json.dumps({**payload, "companies": invalid}), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "company tickers"):
                        load_companies(path)
            duplicate = companies[:1] + [{"ticker": "0126Z0", "name": "다른기업"}] + companies[2:]
            path.write_text(json.dumps({**payload, "companies": duplicate}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "company tickers"):
                load_companies(path)

    def test_complete_utf8_company_snapshot_is_validated(self):
        companies = [{"ticker": f"{i:06d}", "name": f"기업{i}"} for i in range(100)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kospi100.json"
            payload = {"as_of": "2026-09-15", "companies": companies}
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(load_companies(path), companies)
            for invalid in [companies[:99], companies + [companies[0]], [companies[0]] * 100,
                            [{"ticker": "000001", "name": ""}] + companies[1:]]:
                path.write_text(json.dumps({**payload, "companies": invalid}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_companies(path)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "naver.sqlite3"
        self.clock = Clock()
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.temporary.cleanup()

    def store(self, companies=None, **kwargs):
        result = NaverStore(self.path, companies or COMPANIES[:1], now=self.clock, **kwargs)
        self.stores.append(result)
        return result

    def restart(self, store, companies=None, **kwargs):
        store.close()
        self.stores.remove(store)
        return self.store(companies, **kwargs)

    def test_candidates_without_company_words_are_kept_and_urls_deduplicated_across_queries(self):
        store = self.store(COMPANIES, poll_seconds=2)
        client = FakeClient([{"items": [item(originallink="https://publisher.example/news?id=1&utm_source=naver#part")], "total": 1},
                             {"items": [item()], "total": 1}])
        self.assertEqual(store.discover_one(client)["enqueued"], 1)
        self.clock.advance(1)
        self.assertEqual(store.discover_one(client)["enqueued"], 0)
        row = store.pending()[0]
        self.assertEqual(row["url"], "https://publisher.example/news?id=1")
        self.assertEqual(row["title"], "검색 후보 & 소식")
        self.assertEqual(row["query_tickers"], ["000660", "005930"])
        self.assertEqual(len(store.pending()), 1)
        store.mark_done(row["url"], status="filtered")
        self.assertEqual(store.pending(), [])

    def test_pagination_cursor_and_watermark_survive_restart(self):
        store = self.store()
        client = FakeClient(lambda query, start, display: {"items": [item(i) for i in range(start, min(151, start + display))], "total": 150})
        self.assertEqual(store.discover_one(client)["status"], "paging")
        self.assertIsNone(store.stats()["companies"][0]["watermark"])
        store = self.restart(store)
        self.clock.advance(1)
        self.assertEqual(store.discover_one(client)["status"], "complete")
        self.assertEqual([call[1] for call in client.calls], [1, 101])
        self.assertEqual(store.stats()["candidates"], {"pending": 150})
        self.assertEqual(store.stats()["companies"][0]["watermark"], START)

    def test_bootstrap_24h_and_overlap_6h_preserve_missing_dates(self):
        store = self.store(poll_seconds=10)
        client = FakeClient([{"items": [item(1, stamp=START - 23 * 3600), item(2, stamp=START - 25 * 3600), item(3, pubDate="unknown")], "total": 3},
                             {"items": [item(4, stamp=START - 5 * 3600), item(5, stamp=START - 7 * 3600)], "total": 2}])
        store.discover_one(client)
        self.assertEqual({row["url"] for row in store.pending()}, {item(1)["originallink"], item(3)["originallink"]})
        self.assertIsNone(next(row for row in store.pending() if row["url"] == item(3)["originallink"])["published_at"])
        self.clock.advance(10)
        store.discover_one(client)
        self.assertIn(item(4)["originallink"], {row["url"] for row in store.pending()})
        self.assertNotIn(item(5)["originallink"], {row["url"] for row in store.pending()})

    def test_older_page_finishes_window_without_scanning_all_search_history(self):
        store = self.store()
        records = [item(i, stamp=START - i * 3600) for i in range(1, 101)]
        result = store.discover_one(FakeClient([{"items": records, "total": 100_000}]))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["enqueued"], 24)
        self.assertEqual(store.stats()["companies"][0]["watermark"], START)

    def test_1000_result_overflow_keeps_watermark_and_original_gap_floor(self):
        store = self.store()
        client = FakeClient(lambda query, start, display: {"items": [item(i) for i in range(start, start + display)], "total": 5000})
        for _ in range(10):
            result = store.discover_one(client)
            self.clock.advance(1)
        self.assertEqual(result["status"], "overflow")
        search = store.stats()["companies"][0]
        self.assertIsNone(search["watermark"])
        self.assertEqual(search["gap_floor"], START - 24 * 3600)
        self.assertEqual(store.stats()["candidates"], {"pending": 1000})
        self.assertTrue(all(start <= 1000 and start + display - 1 <= 1000 for _, start, display in client.calls))
        store = self.restart(store)
        self.clock.advance(600)
        store.discover_one(client)
        active = store.db.execute("SELECT cycle_lower FROM searches").fetchone()[0]
        self.assertEqual(active, START - 24 * 3600)

    def test_due_company_is_not_starved_by_pagination(self):
        store = self.store(COMPANIES, poll_seconds=2)
        client = FakeClient(lambda query, start, display: {"items": [item(i) for i in range(start, start + display)], "total": 5000})
        for _ in range(3):
            store.discover_one(client)
            self.clock.advance(1)
        self.assertEqual([query for query, _, _ in client.calls], ["삼성전자", "삼성전자", "SK하이닉스"])

    def test_candidates_and_cursor_are_one_transaction(self):
        store = self.store()
        store.db.execute("CREATE TRIGGER reject_query BEFORE INSERT ON candidate_queries BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        client = FakeClient(lambda query, start, display: {"items": [item()], "total": 1})
        with self.assertRaises(sqlite3.IntegrityError):
            store.discover_one(client)
        self.assertEqual(store.pending(), [])
        self.assertEqual(store.stats()["companies"][0]["next_start"], 1)
        self.assertIsNone(store.stats()["companies"][0]["watermark"])
        self.assertEqual(store.stats()["requests_today"], 1)
        store.db.execute("DROP TRIGGER reject_query")
        self.clock.advance(151)  # An uncommitted/crashed request's lease expires.
        self.assertEqual(store.discover_one(client)["status"], "complete")
        self.assertEqual(len(store.pending()), 1)

    def test_failed_request_consumes_quota_and_retry_after_survives_restart(self):
        store = self.store()
        client = FakeClient([NaverApiError("rate_limited", status=429, retry_after=123), {"items": [], "total": 0}])
        result = store.discover_one(client)
        self.assertEqual(result["retry_at"], START + 123)
        self.assertEqual(store.stats()["requests_today"], 1)
        store = self.restart(store)
        self.clock.advance(122)
        self.assertEqual(store.discover_one(client)["status"], "throttled")
        self.assertEqual(len(client.calls), 1)
        self.clock.advance(1)
        self.assertEqual(store.discover_one(client)["status"], "complete")
        self.assertEqual(store.stats()["requests_today"], 2)

    def test_authentication_error_cools_down_without_advancing_cursor(self):
        store = self.store()
        result = store.discover_one(FakeClient([NaverApiError("authentication_failed", status=403)]))
        self.assertEqual(result["retry_at"], START + 3600)
        self.assertIsNone(store.stats()["companies"][0]["watermark"])
        self.assertEqual(store.stats()["companies"][0]["next_start"], 1)

    def test_budget_is_shared_across_instances_and_resets_at_kst_midnight(self):
        store = self.store(COMPANIES, poll_seconds=2, daily_budget=2)
        second = self.store(COMPANIES, poll_seconds=2, daily_budget=2)
        client = FakeClient()
        store.discover_one(client)
        self.assertEqual(second.discover_one(client)["status"], "throttled")
        self.clock.advance(1)
        second.discover_one(client)
        result = store.discover_one(client)
        self.assertEqual(result["status"], "quota_exhausted")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result["retry_at"], datetime(2026, 9, 15, 15, tzinfo=timezone.utc).timestamp())
        self.clock.value = result["retry_at"]
        self.assertEqual(store.discover_one(client)["status"], "complete")
        self.assertEqual(second.stats()["requests_today"], 1)

    def test_parallel_process_equivalent_connections_cannot_overspend(self):
        initial = self.store(daily_budget=1)
        barrier = threading.Barrier(2)
        client = FakeClient()

        def request_once():
            peer = NaverStore(self.path, COMPANIES[:1], now=self.clock, daily_budget=1)
            try:
                barrier.wait(timeout=10)
                return peer.discover_one(client)["status"]
            finally:
                peer.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: request_once(), range(2)))
        self.assertEqual(sorted(outcomes), ["complete", "quota_exhausted"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(initial.stats()["requests_today"], 1)

    def test_reopening_with_higher_budget_does_not_reset_existing_daily_limit(self):
        store = self.store(daily_budget=1)
        client = FakeClient()
        store.discover_one(client)
        store = self.restart(store, daily_budget=24000)
        self.assertEqual(store.discover_one(client)["status"], "quota_exhausted")
        another = FakeClient()
        another.application_fingerprint = "different-application"
        with self.assertRaisesRegex(ValueError, "different Naver application"):
            store.discover_one(another)

    def test_delivery_failures_retry_durably_and_are_sanitized(self):
        store = self.store()
        store.discover_one(FakeClient([{"items": [item()], "total": 1}]))
        url = store.pending()[0]["url"]
        store.mark_failed(url, "mock-secret should not be persisted")
        self.assertEqual(store.pending(), [])
        store = self.restart(store)
        self.clock.advance(60)
        pending = store.pending()[0]
        self.assertEqual(pending["attempts"], 1)
        self.assertEqual(pending["last_error"], "article_fetch_failed")
        for _ in range(4):
            store.mark_failed(url, "body_unavailable")
            self.clock.advance(3600)
        self.assertEqual(store.pending(), [])
        self.assertEqual(store.stats()["exhausted_candidates"], 1)

    def test_new_article_overtakes_older_bootstrap_backlog(self):
        store = self.store(poll_seconds=10)
        client = FakeClient([{"items": [item(1, stamp=START - 23 * 3600)], "total": 1},
                             {"items": [item(2, stamp=START + 5)], "total": 1}])
        store.discover_one(client)
        self.clock.advance(10)
        store.discover_one(client)
        self.assertEqual(store.pending(limit=1)[0]["url"], item(2)["originallink"])
        self.assertEqual(store.stats()["candidates"], {"pending": 2})

    def test_batch_reserves_due_retry_slots_without_blocking_new_articles(self):
        store = self.store()
        store.discover_one(FakeClient([{"items": [item(i, stamp=START - i * 3600) for i in range(1, 9)], "total": 8}]))
        store.mark_failed(item(1)["originallink"], "body_unavailable")
        self.clock.advance(60)
        selected = store.pending(limit=4)
        self.assertEqual([row["url"] for row in selected], [item(i)["originallink"] for i in (2, 3, 4, 1)])
        self.assertEqual(selected[-1]["status"], "failed")

    def test_single_item_batches_alternate_retries_across_restart(self):
        store = self.store()
        store.discover_one(FakeClient([{"items": [item(1), item(2)], "total": 2}]))
        store.mark_failed(item(1)["originallink"], "body_unavailable")
        self.clock.advance(60)
        self.assertEqual(store.pending(limit=1)[0]["status"], "pending")
        store = self.restart(store)
        self.assertEqual(store.pending(limit=1)[0]["status"], "failed")
        self.assertEqual(store.pending(limit=1)[0]["status"], "pending")

    def test_private_source_link_uses_safe_naver_fallback(self):
        store = self.store()
        store.discover_one(FakeClient([{"items": [item(originallink="http://[fd00::1]/article")], "total": 1}]))
        self.assertEqual(store.pending()[0]["url"], item()["link"])

    def test_incomplete_page_does_not_mark_scan_complete(self):
        store = self.store()
        result = store.discover_one(FakeClient([{"items": [], "total": 5}]))
        self.assertEqual(result["error"], "incomplete_response")
        self.assertIsNone(store.stats()["companies"][0]["watermark"])


if __name__ == "__main__":
    unittest.main()
