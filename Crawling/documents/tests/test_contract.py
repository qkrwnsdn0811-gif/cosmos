"""Metadata normalization checks; no production data or network is required."""
from copy import deepcopy
import hashlib
import unittest

from services.document_loader.contract import ValidationError, canonical_url, normalize_batch, validate_records


def news(**changes):
    body = "  Example company makes a public announcement.\n"
    raw = {
        "source": "naver_news_search", "region": "domestic", "language": "ko",
        "url": "https://publisher.example/article?id=17&edition=2#comments",
        "title": "Example company announcement", "content": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "organization": "Example Publisher", "published_at": "2026-09-16T09:00:00+09:00",
        "collected_at": "2026-09-16T00:10:00Z", "metadata": {},
    }
    raw.update(changes)
    return {"kind": "news", "record": raw, "raw_uri": "hdfs://namenode:9000/news/part.parquet"}


def dart(**changes):
    raw = {"rcept_no": "20260916000001", "corp_code": "00123456", "stock_code": "005930",
           "corp_name": "Example Company", "rcept_dt": "20260916", "report_nm": "  Example report  ",
           "raw_sha256": "a" * 64}
    raw.update(changes)
    return {"kind": "dart", "record": raw,
            "raw_uri": "hdfs://namenode:9000/dart/raw/documents-part-00001.tar#documents/20260916000001.zip",
            "provenance": {"collected_at": "2026-09-16T00:15:00Z"}}


def sec(**changes):
    raw = {"cik": "1234567", "symbols": ["EXMP"], "company": "Example Incorporated",
           "accessionNumber": "0001234567-26-000001", "form": "8-K", "filingDate": "2026-09-15",
           "primaryDocDescription": "Current report", "acceptanceDateTime": "2026-09-15T20:10:00Z",
           "sourceUrl": "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/0001234567-26-000001.txt",
           "sha256": "b" * 64, "collectedAt": "2026-09-16T00:00:00Z"}
    raw.update(changes)
    return {"kind": "sec", "record": raw, "raw_uri": "hdfs://namenode:9000/sec/raw.txt"}


def normalized(envelope, **kwargs):
    return normalize_batch({"records": [envelope]}, **kwargs)["records"][0]


class NewsNormalizationTests(unittest.TestCase):
    def test_content_hash_uses_preserved_body_without_trimming(self):
        row = news()
        result = normalized(row)
        self.assertEqual(result["content_hash"], hashlib.sha256(row["record"]["content"].encode()).hexdigest())
        self.assertNotEqual(result["content_hash"], hashlib.sha256(row["record"]["content"].strip().encode()).hexdigest())

    def test_wrong_preserved_content_hash_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "content_hash"):
            normalized(news(content_hash="0" * 64))

    def test_embedded_nul_in_hdfs_only_body_keeps_exact_hash(self):
        body = "Before NUL\x00after NUL"
        result = normalized(news(content=body, content_hash=hashlib.sha256(body.encode()).hexdigest()))
        self.assertEqual(result["content_hash"], hashlib.sha256(body.encode()).hexdigest())

    def test_query_ticker_is_not_treated_as_a_company_mention(self):
        result = normalized(news(metadata={"query_tickers": ["005930"]}))
        self.assertEqual(result["company_refs"], [])

    def test_confirmed_mentions_are_deduplicated_without_query_tickers(self):
        result = normalized(news(metadata={"query_tickers": ["000880"], "matched_companies": [
            {"ticker": "042660", "fields": ["title", "content"]},
            {"ticker": "272210", "fields": ["content"]},
            {"ticker": "042660", "fields": ["title"]},
        ]}))
        self.assertEqual(result["company_refs"], [
            {"market": "KOSPI", "stock_code": "042660"}, {"market": "KOSPI", "stock_code": "272210"}])

    def test_company_hint_requires_title_or_body_evidence(self):
        with self.assertRaisesRegex(ValidationError, "evidence"):
            normalized(news(metadata={"matched_companies": [{"ticker": "005930", "fields": ["query"]}]}))

    def test_missing_publication_time_stays_null(self):
        result = normalized(news(published_at=None))
        self.assertIsNone(result["published_at"])
        self.assertEqual(result["collected_at"], "2026-09-16T00:10:00+00:00")

    def test_naive_publication_and_collection_timestamps_are_rejected(self):
        for field in ("published_at", "collected_at"):
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "timezone"):
                normalized(news(**{field: "2026-09-16T09:00:00"}))

    def test_publication_is_normalized_to_utc(self):
        self.assertEqual(normalized(news())["published_at"], "2026-09-16T00:00:00+00:00")

    def test_search_description_does_not_become_an_analysis_summary(self):
        result = normalized(news(metadata={"search_description": "A provider snippet."}))
        self.assertIsNone(result["summary"])
        self.assertEqual(result["status"], "COLLECTED")
        self.assertNotIn("content", result)

    def test_provider_key_is_independent_of_unreliable_publisher_text(self):
        result = normalize_batch({"records": [news(organization="A title mistakenly extracted as publisher")]})
        self.assertEqual(list(result["sources"]), ["news:domestic:naver_news_search"])

    def test_headline_and_oversize_organization_do_not_become_publisher(self):
        for organization in (news()["record"]["title"], "p" * 201):
            with self.subTest(organization=organization):
                self.assertIsNone(normalized(news(organization=organization))["publisher"])
        self.assertEqual(normalized(news())["publisher"], "Example Publisher")


class DisclosureNormalizationTests(unittest.TestCase):
    def test_dart_keeps_date_without_inventing_publication_time(self):
        result = normalized(dart())
        self.assertEqual(result["filing_date"], "2026-09-16")
        self.assertIsNone(result["published_at"])
        self.assertIsNone(result["summary"])
        self.assertEqual(result["filing_company"], {"market": "KOSPI", "stock_code": "005930"})
        self.assertEqual(result["report_name"], "Example report")
        self.assertTrue(result["original_url"].endswith("rcpNo=20260916000001"))
        self.assertTrue(result["hdfs_raw_uri"].endswith("#documents/20260916000001.zip"))

    def test_dart_invalid_calendar_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            normalized(dart(rcept_dt="20260230"))

    def test_dart_source_stock_code_conflict_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "conflicts"):
            normalized(dart(), company_map={"dart": {"00123456": {"market": "KOSPI", "stock_code": "000660"}}})

    def test_sec_single_source_symbol_and_padded_cik(self):
        result = normalized(sec())
        self.assertEqual(result["sec_cik"], "0001234567")
        self.assertEqual(result["filing_company"], {"market": "NASDAQ", "stock_code": "EXMP"})
        self.assertEqual(result["sec_accession_no"], "0001234567-26-000001")
        self.assertEqual(result["report_code"], "8-K")
        self.assertIsNone(result["dart_receipt_no"])

    def test_sec_unknown_or_ambiguous_issuer_requires_explicit_mapping(self):
        for symbols in ([], ["GOOG", "GOOGL"]):
            with self.subTest(symbols=symbols), self.assertRaisesRegex(ValidationError, "explicit company-map"):
                normalized(sec(symbols=symbols))

    def test_sec_explicit_cik_mapping_resolves_multiple_share_classes(self):
        result = normalized(sec(symbols=["GOOG", "GOOGL"]), company_map={
            "sec": {"0001234567": {"market": "NASDAQ", "stock_code": "GOOGL"}}})
        self.assertEqual(result["filing_company"], {"market": "NASDAQ", "stock_code": "GOOGL"})

    def test_sec_null_acceptance_time_remains_null(self):
        self.assertIsNone(normalized(sec(acceptanceDateTime=None))["published_at"])

    def test_zero_or_non_numeric_cik_is_rejected(self):
        for cik in ("0", "abc", "12345678901"):
            with self.subTest(cik=cik), self.assertRaises(ValidationError):
                normalized(sec(cik=cik))

    def test_issuer_map_rejects_wrong_market(self):
        for envelope, company_map in (
            (sec(), {"sec": {"0001234567": {"market": "KOSPI", "stock_code": "005930"}}}),
            (dart(stock_code=None), {"dart": {"00123456": {"market": "NASDAQ", "stock_code": "EXMP"}}}),
        ):
            with self.subTest(kind=envelope["kind"]), self.assertRaises(ValidationError):
                normalized(envelope, company_map=company_map)

    def test_issuer_map_has_a_validated_object_schema(self):
        for company_map in ([], {"sec": ["invalid"]}, {"unknown": {}},
                            {"sec": {"0001234567": "invalid"}}):
            with self.subTest(company_map=company_map), self.assertRaises(ValidationError):
                normalized(sec(), company_map=company_map)


class URLAndRecordValidationTests(unittest.TestCase):
    def test_article_id_query_order_and_duplicates_are_preserved(self):
        url = "HTTPS://Publisher.Example/article?id=17&id=18&edition=2#comments"
        self.assertEqual(canonical_url(url), "https://publisher.example/article?id=17&id=18&edition=2")
        first = normalized(news(url="https://publisher.example/article?id=17"))
        second = normalized(news(url="https://publisher.example/article?id=18"))
        self.assertNotEqual(first["canonical_url_hash"], second["canonical_url_hash"])

    def test_path_whitespace_is_encoded_without_changing_query_identity(self):
        self.assertEqual(
            canonical_url("https://example.test/a b?q=hello world&id=1&id=2"),
            "https://example.test/a%20b?q=hello%20world&id=1&id=2",
        )

    def test_url_credentials_host_whitespace_and_non_web_urls_are_rejected(self):
        for value in ("https://user:secret@example.test/path", "javascript:alert(1)",
                      "https://exa mple.test/path", "https://example.test:bad/path"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                canonical_url(value)

    def test_wrong_canonical_hash_is_rejected(self):
        row = normalized(news())
        row["canonical_url_hash"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "canonical_url_hash mismatch"):
            validate_records([row])

    def test_collection_cannot_assert_analysis_complete(self):
        row = normalized(news())
        row["status"] = "ANALYZED"
        with self.assertRaises(ValidationError):
            validate_records([row])

    def test_mixed_filing_identities_are_rejected(self):
        row = normalized(dart())
        row["sec_accession_no"] = "0001234567-26-000001"
        with self.assertRaises(ValidationError):
            validate_records([row])

    def test_validation_is_idempotent_and_does_not_mutate_records(self):
        row = normalized(news())
        before = deepcopy(row)
        self.assertEqual(validate_records([row]), [row])
        self.assertEqual(row, before)

    def test_raw_uri_requires_absolute_file_or_hdfs_path(self):
        for uri in ("file:relative", "hdfs://namenode:9000", "file://", "hdfs://namenode:9000/"):
            row = normalized(news())
            row["hdfs_raw_uri"] = uri
            with self.subTest(uri=uri), self.assertRaises(ValidationError):
                validate_records([row])


if __name__ == "__main__":
    unittest.main()
