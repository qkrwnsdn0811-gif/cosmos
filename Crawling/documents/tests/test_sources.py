from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from services.document_loader.hdfs import SnapshotReader, safe_relative
from services.document_loader.contract import normalize_batch
from services.document_loader.sources import load_source


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def info(data, **extra):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), **extra}


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def news_fixture(self, region="domestic", *, quarantine=False):
        source, url, content = "naver_news_search", "https://example.test/article", "article " * 20
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        payload = {"schema_version": 1,
                   "event_id": hashlib.sha256(f"{source}\n{url}\n{content_hash}".encode()).hexdigest(),
                   "source": source,
                   "region": region, "language": "ko" if region == "domestic" else "en",
                   "url": url, "url_hash": hashlib.sha256(url.encode()).hexdigest(),
                   "title": "삼성전자 신제품", "content": content, "content_hash": content_hash,
                   "organization": "publisher", "run_id": "run-1",
                   "published_at": None, "collected_at": "2026-09-16T00:00:00+00:00",
                   "metadata": {"query_companies": [{"ticker": "005930", "name": "삼성전자"}]}}
        row = {**payload, "kafka_topic": "news.raw", "kafka_partition": 0, "kafka_offset": 0,
               "raw_json": encoded(payload).decode()}
        row.pop("metadata")
        output = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist([row]), output)
        data = output.getvalue().to_pybytes()
        name = f"region={region}/date=2026-09-16/part.parquet"
        self.put(name, data)
        entries = [info(data, path=name, records=1)]
        if quarantine:
            bad = encoded({"source_topic": "news.raw", "source_partition": 0, "source_offset": 1})
            self.put("quarantine/invalid.jsonl", bad)
            entries.append(info(bad, path="quarantine/invalid.jsonl", records=1))
        manifest = {"version": 1, "topic": "news.raw", "partition": 0, "start": 0,
                    "end": 2 if quarantine else 1, "records": 2 if quarantine else 1,
                    "valid_records": 1, "invalid_records": int(quarantine), "cluster_id": "cluster",
                    "topic_id": "topic-id", "group": "writer", "files": entries}
        self.put("_manifest.json", encoded(manifest))
        return manifest, "hdfs://cluster:9000/data/news/topic=news.raw/partition=0/start=00000000000000000000"

    def analyzed_news_fixture(self, *, companies=None, region="domestic"):
        companies = [] if companies is None else companies
        topic, model, analysis = "news.raw", "dict-v1.3", "news-company-mentions-1.0"
        raw_uri = "hdfs://cluster:9000/data-lake/raw/realtime/news/topic=news.raw/partition=0/start=00000000000000000000"
        output_uri = ("hdfs://cluster:9000/data-lake/analyzed/news/company-mentions/"
                      "model_version=dict-v1.3/topic=news.raw/partition=0/start=00000000000000000000")
        content, url = "Samsung and Apple announced a partnership.", "https://example.test/analyzed"
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        event_id = hashlib.sha256(f"fixture-news\n{url}\n{content_hash}".encode()).hexdigest()
        payload = {"schema_version": 1, "event_id": event_id, "source": "fixture-news",
                   "region": region, "language": "ko", "url": url,
                   "url_hash": hashlib.sha256(url.encode()).hexdigest(), "title": "Partnership",
                   "content": content, "content_hash": content_hash,
                   "organization": "Fixture Publisher", "published_at": "2026-09-16T00:00:00+00:00",
                   "collected_at": "2026-09-16T01:00:00+00:00", "run_id": "run-analyzed",
                   "metadata": {"collector": "fixture"}}
        raw_manifest_hash, aliases_hash, companies_hash = "a" * 64, "b" * 64, "c" * 64
        analyzed_at = "2026-09-16T02:00:00+00:00"
        row = {**{key: payload[key] for key in ("schema_version", "event_id", "source", "region",
               "language", "url", "url_hash", "title", "content", "content_hash", "organization",
               "published_at", "collected_at", "run_id")},
               "metadata_json": json.dumps(payload["metadata"], ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")),
               "raw_json": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
               "kafka_topic": topic, "kafka_partition": 0, "kafka_offset": 0,
               "raw_input_uri": raw_uri,
               "raw_file_uri": raw_uri + "/region=domestic/date=2026-09-16/part.parquet",
               "raw_manifest_sha256": raw_manifest_hash, "raw_batch_valid_records": 1,
               "raw_batch_invalid_records": 0, "analysis_version": analysis,
               "model_version": model, "aliases_sha256": aliases_hash,
               "companies_sha256": companies_hash, "analyzed_at": analyzed_at,
               "companies": companies}
        output = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist([row]), output)
        data = output.getvalue().to_pybytes()
        identity = {"contract_version": analysis, "input_uri": raw_uri,
                    "input_manifest_sha256": raw_manifest_hash, "aliases_sha256": aliases_hash,
                    "companies_sha256": companies_hash, "model_version": model,
                    "analysis_version": analysis}
        analysis_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":")).encode()).hexdigest()
        manifest = {"version": 1, "dataset": "news-company-mentions", "schema_version": analysis,
                    "analysis_id": analysis_id, "identity": identity, "model_version": model,
                    "analysis_version": analysis, "aliases_sha256": aliases_hash,
                    "companies_sha256": companies_hash, "minimum_confidence": 0.5,
                    "created_at": analyzed_at,
                    "input": {"uri": raw_uri, "manifest_sha256": raw_manifest_hash, "topic": topic,
                              "partition": 0, "start": 0, "end": 1, "records": 1,
                              "valid_records": 1, "invalid_records": 0},
                    "output": {"records": 1, "duplicate_records": 0,
                               "docs_with_companies": int(bool(companies)),
                               "company_links": len(companies),
                               "files": [info(data, path="data.parquet", records=1)]}}
        self.put("data.parquet", data)
        self.put("_manifest.json", encoded(manifest))
        self.put("_SUCCESS", b"")
        return manifest, output_uri

    def dart_fixture(self):
        receipt = "20260916000001"
        row = {"receipt": receipt, "rcept_no": receipt, "corp_code": "00126380", "corp_name": "삼성전자",
               "stock_code": "005930", "rcept_dt": "20260916", "report_nm": "사업보고서",
               "raw_bundle": "raw/documents-part-00001.tar", "raw_member": f"documents/{receipt}.zip",
               "raw_bytes": 3, "raw_sha256": hashlib.sha256(b"zip").hexdigest(),
               "snapshot_detail": "data/company_detail.jsonl.gz"}
        self.put("raw/documents-part-00001.tar", b"fake tar with original ZIP")
        self.put("data/company_detail.jsonl.gz", b"fake gzip")
        self.put("metadata/document_index.jsonl", encoded(row))
        manifest = {"format_version": 1, "snapshot_documents": 1, "selected_documents": 2,
                    "not_in_snapshot": 1, "captured_at": "2026-09-16T00:00:00+00:00"}
        self.put("manifest.json", encoded(manifest))
        self.reseal_dart()
        return row

    def reseal_dart(self):
        entries = [info(path.read_bytes(), path=path.relative_to(self.root).as_posix())
                   for path in self.root.rglob("*") if path.is_file() and path.name not in ("ready.json", "checksums.json")]
        self.put("checksums.json", encoded({"algorithm": "sha256", "files": entries}))
        self.put("ready.json", encoded({"status": "ready", "credential_text_check": "passed",
                 "snapshot_documents": 1, "manifest_sha256": info((self.root / "manifest.json").read_bytes())["sha256"],
                 "checksums_sha256": info((self.root / "checksums.json").read_bytes())["sha256"]}))

    def sec_fixture(self, *, batch=False):
        row = {"cik": "1069183", "accessionNumber": "0001193125-26-391320", "filingDate": "2026-09-15",
               "reportDate": "2026-09-15", "form": "8-K", "company": "AXON ENTERPRISE, INC.",
               "symbols": ["AXON"], "sourceUrl": "https://www.sec.gov/Archives/edgar/data/1069183/000119312526391320/0001193125-26-391320.txt",
               "collectedAt": "2026-09-16T00:00:00+00:00", **info(b"original filing")}
        self.put("_SUCCESS", b"")
        if batch:
            row["localPath"] = "raw/001_AXON/2026-09-15_8-K_0001193125-26-391320.txt"
            self.put(row["localPath"], b"original filing")
            self.put("filings.jsonl", encoded(row))
            self.put("manifest.json", encoded({"dataset": "sec-edgar-nasdaq100", "metadataOnly": False,
                     "completedAt": "2026-09-16T00:00:00+00:00", "filingCount": 1, "totalBytes": row["bytes"]}))
        else:
            self.put("metadata.json", encoded(row))
            self.put("raw.txt", b"original filing")
            self.put("_manifest.json", encoded({"version": 1, "dataset": "sec-edgar-incremental",
                     "identity": {"cik": row["cik"], "accession": row["accessionNumber"], "filing_date": row["filingDate"]},
                     "files": {"metadata.json": info(encoded(row)), "raw.txt": info(b"original filing")}}))
        uri = "hdfs://cluster:9000/data/sec/filing_date=2026-09-15/cik=1069183/accession=0001193125-26-391320"
        return row, uri

    def test_domestic_and_overseas_news_keep_original_company_hints(self):
        for region in ("domestic", "overseas"):
            _, uri = self.news_fixture(region)
            batch = load_source("news", self.root, source_uri=uri)
            self.assertEqual(batch["records"][0]["record"]["region"], region)
            self.assertEqual(batch["records"][0]["record"]["metadata"]["query_companies"][0]["ticker"], "005930")
            self.assertTrue(batch["verification"]["metadata_hashes_verified"])

    def test_news_quarantine_accounted_without_loading_invalid_documents(self):
        _, uri = self.news_fixture(quarantine=True)
        batch = load_source("news", self.root, source_uri=uri)
        self.assertEqual(len(batch["records"]), 1)
        self.assertEqual(batch["verification"]["quarantined_records"], 1)

    def test_news_rejects_changed_parquet(self):
        manifest, uri = self.news_fixture()
        name = manifest["files"][0]["path"]
        data = (self.root / name).read_bytes()
        self.put(name, bytes([data[0] ^ 1]) + data[1:])
        with self.assertRaisesRegex(ValueError, "SHA256"):
            load_source("news", self.root, source_uri=uri)

    def test_news_rejects_bad_counts(self):
        manifest, uri = self.news_fixture()
        manifest["files"][0]["records"] = 2
        self.put("_manifest.json", encoded(manifest))
        with self.assertRaisesRegex(ValueError, "record count"):
            load_source("news", self.root, source_uri=uri)

    def test_news_requires_final_batch_directory(self):
        _, uri = self.news_fixture()
        with self.assertRaises(ValueError):
            load_source("news", self.root, source_uri=uri.replace("start=", "wrong="))

    def test_news_refuses_traversal_inventory(self):
        manifest, uri = self.news_fixture()
        manifest["files"][0]["path"] = "../outside.parquet"
        self.put("_manifest.json", encoded(manifest))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            load_source("news", self.root, source_uri=uri)

    def test_analyzed_news_accepts_cross_market_mentions_and_empty_company_sets(self):
        apple = {"ticker": "AAPL", "market": "NASDAQ", "stock_code": "AAPL", "name": "Apple",
                 "confidence": 0.9, "method": "dict+product", "aliases": ["Apple"],
                 "n_mentions": 1, "first_pos": "body"}
        _, uri = self.analyzed_news_fixture(companies=[apple], region="domestic")
        batch = load_source("news-analyzed", self.root, source_uri=uri)
        record = normalize_batch(batch)["records"][0]
        self.assertEqual(record["status"], "ANALYZED")
        self.assertEqual(record["company_refs"], [{"market": "NASDAQ", "stock_code": "AAPL"}])
        self.assertEqual(record["company_links"][0]["confidence"], 0.9)
        self.assertEqual(batch["verification"]["company_links"], 1)

        self.tearDown()
        self.setUp()
        _, uri = self.analyzed_news_fixture(companies=[])
        record = normalize_batch(load_source("news-analyzed", self.root, source_uri=uri))["records"][0]
        self.assertEqual(record["company_refs"], [])
        self.assertEqual(record["company_links"], [])

    def test_loader_reads_the_actual_realtime_analyzer_contract(self):
        manifest, _ = self.news_fixture()
        parquet_path = self.root / manifest["files"][0]["path"]
        original = pq.read_table(parquet_path).to_pylist()[0]
        duplicate = {**original, "kafka_offset": 1}
        output = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist([original, duplicate]), output)
        parquet = output.getvalue().to_pybytes()
        parquet_path.write_bytes(parquet)
        manifest.update(end=2, records=2, valid_records=2)
        manifest["files"][0] = info(parquet, path=manifest["files"][0]["path"], records=2)
        self.put("_manifest.json", encoded(manifest))
        raw = self.root / "raw" / "topic=news.raw" / "partition=0" / "start=00000000000000000000"
        raw.mkdir(parents=True)
        for path in list(self.root.iterdir()):
            if path.name != "raw":
                path.rename(raw / path.name)
        repo = Path(__file__).resolve().parents[3]
        ner = repo / "AI" / "ner"
        sys.path.insert(0, str(ner))
        try:
            from realtime_analyzer import analyze_batch
            result = analyze_batch(input_uri=str(raw), output_base=str(self.root / "analyzed"),
                                   aliases_path=ner / "data" / "aliases.csv",
                                   companies_path=ner / "data" / "companies.csv",
                                   model_version="dict-v1.3", analysis_version="fixture-release",
                                   analyzed_at="2026-09-16T02:00:00+00:00")
        finally:
            sys.path.remove(str(ner))
        batch = load_source("news-analyzed", Path(result["output_uri"]))
        record = normalize_batch(batch)["records"][0]
        self.assertEqual(result["manifest"]["output"]["duplicate_records"], 1)
        self.assertEqual(len(batch["records"]), 1)
        self.assertEqual(record["status"], "ANALYZED")
        self.assertEqual(record["analysis_version"], "fixture-release")
        self.assertEqual(record["company_refs"], [{"market": "KOSPI", "stock_code": "005930"}])

    def test_analyzed_news_rejects_changed_data_count_and_invalid_company_identity(self):
        company = {"ticker": "005930", "market": "KOSPI", "stock_code": "005930", "name": "Samsung",
                   "confidence": 0.9, "method": "dict", "aliases": ["Samsung"],
                   "n_mentions": 1, "first_pos": "title"}
        manifest, uri = self.analyzed_news_fixture(companies=[company])
        data = (self.root / "data.parquet").read_bytes()
        self.put("data.parquet", data[:-1] + bytes([data[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            load_source("news-analyzed", self.root, source_uri=uri)

        self.tearDown()
        self.setUp()
        manifest, uri = self.analyzed_news_fixture(companies=[company])
        manifest["output"]["records"] = 2
        self.put("_manifest.json", encoded(manifest))
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            load_source("news-analyzed", self.root, source_uri=uri)

        self.tearDown()
        self.setUp()
        company.update(market="NASDAQ", stock_code="AAPL")
        _, uri = self.analyzed_news_fixture(companies=[company])
        with self.assertRaisesRegex(ValueError, "company identity"):
            load_source("news-analyzed", self.root, source_uri=uri)

    def test_dart_reads_index_without_raw_or_parsed_bodies(self):
        row = self.dart_fixture()
        original = SnapshotReader.read
        read_paths = []
        def read(reader, relative, **kwargs):
            read_paths.append(relative)
            return original(reader, relative, **kwargs)
        with patch.object(SnapshotReader, "read", read):
            batch = load_source("dart", self.root)
        self.assertEqual(set(read_paths), {"ready.json", "manifest.json", "checksums.json", "metadata/document_index.jsonl"})
        self.assertTrue(batch["records"][0]["raw_uri"].endswith("#" + row["raw_member"]))
        self.assertFalse(batch["verification"]["raw_body_hashes_verified"])

    def test_dart_refuses_unready_snapshot(self):
        self.dart_fixture()
        self.put("ready.json", encoded({"status": "inprogress"}))
        with self.assertRaisesRegex(ValueError, "not ready"):
            load_source("dart", self.root)

    def dart_hdfs_reader(self, *, wrong_size=None):
        uri = "hdfs://cluster:9000/datasets/dart/snapshot"
        remote = SnapshotReader(uri)
        local = SnapshotReader(self.root, source_uri=uri)
        def command(*args):
            self.assertEqual(args[:2], ("-ls", "-R"), "Body references must use cached sizes, not per-file HDFS stat")
            prefix = args[2].rsplit("/", 1)[1]
            rows = []
            for path in sorted((self.root / prefix).rglob("*")):
                if path.is_file():
                    relative = path.relative_to(self.root).as_posix()
                    size = path.stat().st_size + (1 if relative == wrong_size else 0)
                    rows.append(f"-rw-r--r-- 2 user group {size} 2026-09-16 00:00 /datasets/dart/snapshot/{relative}\n")
            return "".join(rows).encode()
        return uri, remote, local, command

    def test_dart_hdfs_primes_two_trees_and_preserves_exact_batch_identity(self):
        self.dart_fixture()
        uri, remote, local, command = self.dart_hdfs_reader()
        baseline = load_source("dart", self.root, source_uri=uri)
        with patch("services.document_loader.sources.SnapshotReader", return_value=remote), \
             patch.object(remote, "read", side_effect=local.read) as read, \
             patch.object(remote, "_call", side_effect=command) as call:
            actual = load_source("dart", uri)
        self.assertEqual(actual, baseline)
        self.assertEqual([args.args for args in call.call_args_list], [
            ("-ls", "-R", uri + "/raw"), ("-ls", "-R", uri + "/data")])
        self.assertEqual({args.args[0] for args in read.call_args_list},
                         {"ready.json", "manifest.json", "checksums.json", "metadata/document_index.jsonl"})

    def test_dart_hdfs_cached_archive_and_detail_sizes_still_reject_mismatch(self):
        row = self.dart_fixture()
        for field in ("raw_bundle", "snapshot_detail"):
            with self.subTest(field=field):
                uri, remote, local, command = self.dart_hdfs_reader(wrong_size=row[field])
                with patch("services.document_loader.sources.SnapshotReader", return_value=remote), \
                     patch.object(remote, "read", side_effect=local.read), \
                     patch.object(remote, "_call", side_effect=command), \
                     self.assertRaisesRegex(ValueError, "incomplete raw file"):
                    load_source("dart", uri)

    def test_empty_dart_snapshot_does_not_require_body_directories(self):
        self.dart_fixture()
        self.put("metadata/document_index.jsonl", b"")
        manifest = json.loads((self.root / "manifest.json").read_bytes())
        manifest.update(snapshot_documents=0, selected_documents=0, not_in_snapshot=0)
        self.put("manifest.json", encoded(manifest))
        self.reseal_dart()
        ready = json.loads((self.root / "ready.json").read_bytes())
        ready["snapshot_documents"] = 0
        self.put("ready.json", encoded(ready))
        with patch.object(SnapshotReader, "prime_sizes") as prime:
            actual = load_source("dart", self.root)
        self.assertEqual(actual["records"], [])
        prime.assert_not_called()

    def test_dart_refuses_changed_index(self):
        self.dart_fixture()
        self.put("metadata/document_index.jsonl", b"{}\n")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            load_source("dart", self.root)

    def test_dart_refuses_changed_control(self):
        self.dart_fixture()
        self.put("manifest.json", encoded({"format_version": 1}))
        with self.assertRaisesRegex(ValueError, "control checksum"):
            load_source("dart", self.root)

    def test_dart_refuses_duplicate_receipts(self):
        row = self.dart_fixture()
        self.put("metadata/document_index.jsonl", encoded(row) * 2)
        manifest = json.loads((self.root / "manifest.json").read_bytes())
        manifest.update(snapshot_documents=2, not_in_snapshot=0)
        self.put("manifest.json", encoded(manifest))
        self.reseal_dart()
        ready = json.loads((self.root / "ready.json").read_bytes())
        ready["snapshot_documents"] = 2
        self.put("ready.json", encoded(ready))
        with self.assertRaisesRegex(ValueError, "duplicate DART"):
            load_source("dart", self.root)

    def test_dart_refuses_raw_archive_member_traversal(self):
        row = self.dart_fixture()
        row["raw_member"] = "../outside.zip"
        self.put("metadata/document_index.jsonl", encoded(row))
        self.reseal_dart()
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            load_source("dart", self.root)

    def test_dart_refuses_missing_raw_bundle(self):
        row = self.dart_fixture()
        (self.root / row["raw_bundle"]).unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            load_source("dart", self.root)

    def test_sec_incremental_reads_only_metadata(self):
        _, uri = self.sec_fixture()
        original = SnapshotReader.read
        names = []
        def read(reader, relative, **kwargs):
            names.append(relative)
            return original(reader, relative, **kwargs)
        with patch.object(SnapshotReader, "read", read):
            batch = load_source("sec", self.root, source_uri=uri)
        self.assertEqual(names, ["_SUCCESS", "_manifest.json", "metadata.json"])
        self.assertEqual(batch["records"][0]["record"]["symbols"], ["AXON"])

    def test_sec_requires_empty_completion_marker(self):
        _, uri = self.sec_fixture()
        self.put("_SUCCESS", b"x")
        with self.assertRaisesRegex(ValueError, "completion marker"):
            load_source("sec", self.root, source_uri=uri)

    def test_sec_rejects_missing_completion_marker(self):
        _, uri = self.sec_fixture()
        (self.root / "_SUCCESS").unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            load_source("sec", self.root, source_uri=uri)

    def test_sec_rejects_metadata_hash_corruption(self):
        row, uri = self.sec_fixture()
        row["form"] = "10-K"
        self.put("metadata.json", encoded(row))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            load_source("sec", self.root, source_uri=uri)

    def test_sec_rejects_wrong_final_identity_path(self):
        _, uri = self.sec_fixture()
        with self.assertRaisesRegex(ValueError, "final filing"):
            load_source("sec", self.root, source_uri=uri.replace("cik=1069183", "cik=9999"))

    def test_sec_rejects_raw_size_mismatch(self):
        _, uri = self.sec_fixture()
        self.put("raw.txt", b"truncated")
        with self.assertRaisesRegex(ValueError, "incomplete raw"):
            load_source("sec", self.root, source_uri=uri)

    def test_sec_batch_documents_lack_of_index_digest(self):
        self.sec_fixture(batch=True)
        batch = load_source("sec-batch", self.root)
        self.assertEqual(len(batch["records"]), 1)
        self.assertFalse(batch["verification"]["metadata_hashes_verified"])
        self.assertIn("Legacy", batch["verification"]["metadata_hash_note"])

    def test_sec_batch_accepts_correct_pinned_hash_and_rejects_wrong_hash(self):
        self.sec_fixture(batch=True)
        expected = info((self.root / "filings.jsonl").read_bytes())["sha256"]
        self.assertTrue(load_source("sec-batch", self.root, expected_index_sha256=expected)["verification"]["metadata_hashes_verified"])
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            load_source("sec-batch", self.root, expected_index_sha256="0" * 64)

    def test_sec_batch_composite_digest_detects_index_change(self):
        row, _ = self.sec_fixture(batch=True)
        before = load_source("sec-batch", self.root)
        row["company"] = "Changed company name"
        self.put("filings.jsonl", encoded(row))
        after = load_source("sec-batch", self.root)
        self.assertNotEqual(before["manifest_sha256"], after["manifest_sha256"])
        self.assertEqual(before["batch_id"], after["batch_id"])
        self.assertEqual(before["verification"]["physical_manifest_sha256"], after["verification"]["physical_manifest_sha256"])

    def test_sec_batch_rejects_unfinished_jsonl(self):
        self.sec_fixture(batch=True)
        path = self.root / "filings.jsonl"
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        with self.assertRaisesRegex(ValueError, "Incomplete JSONL"):
            load_source("sec-batch", self.root)

    def test_sec_batch_rejects_raw_path_traversal(self):
        row, _ = self.sec_fixture(batch=True)
        row["localPath"] = "raw/../../outside.txt"
        self.put("filings.jsonl", encoded(row))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            load_source("sec-batch", self.root)

    def test_sec_batch_rejects_wrong_total_bytes(self):
        self.sec_fixture(batch=True)
        manifest = json.loads((self.root / "manifest.json").read_bytes())
        manifest["totalBytes"] += 1
        self.put("manifest.json", encoded(manifest))
        with self.assertRaisesRegex(ValueError, "total bytes"):
            load_source("sec-batch", self.root)

    def test_staging_root_is_always_rejected(self):
        path = self.root / "snapshot.inprogress"
        path.mkdir()
        with self.assertRaisesRegex(ValueError, "Staging"):
            load_source("dart", path)

    def test_relative_path_rejects_escape_glob_and_url(self):
        for value in ("../x", "x/../y", "x\\y", "/tmp/x", "file:///x", "a//b", "a/*", "a/[1]", "a?x", ".staging/x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative(value)

    def test_local_reader_refuses_symlink(self):
        outside = self.root / "outside"
        outside.write_bytes(b"secret")
        link = self.root / "link"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("OS account cannot create symlinks")
        with self.assertRaisesRegex(ValueError, "symlink"):
            SnapshotReader(self.root).read("link")

    def test_reader_enforces_byte_bound(self):
        self.put("large.json", b"123456")
        with self.assertRaisesRegex(ValueError, "read limit"):
            SnapshotReader(self.root, max_bytes=5).read("large.json")

    def test_hdfs_reader_caches_legacy_raw_inventory_with_one_call(self):
        reader = SnapshotReader("hdfs://cluster:9000/datasets/sec/snapshot")
        listing = (b"drwxr-xr-x - user group 0 2026-09-16 00:00 /datasets/sec/snapshot/raw/001_AXON\n"
                   b"-rw-r--r-- 2 user group 12 2026-09-16 00:00 /datasets/sec/snapshot/raw/001_AXON/filing.txt\n")
        with patch.object(reader, "_call", return_value=listing) as call:
            reader.prime_sizes("raw")
            self.assertEqual(reader.size("raw/001_AXON/filing.txt"), 12)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.args, ("-ls", "-R", "hdfs://cluster:9000/datasets/sec/snapshot/raw"))

    def test_hdfs_inventory_rejects_external_path_and_symlink(self):
        reader = SnapshotReader("hdfs://cluster:9000/datasets/sec/snapshot")
        for listing in (b"-rw-r--r-- 2 user group 12 2026-09-16 00:00 /other/filing.txt\n",
                        b"lrwxr-xr-x 2 user group 12 2026-09-16 00:00 /datasets/sec/snapshot/raw/filing.txt\n"):
            with self.subTest(listing=listing), patch.object(reader, "_call", return_value=listing), self.assertRaises(ValueError):
                reader.prime_sizes("raw")

    def test_hdfs_read_uses_argv_and_bounded_binary_stream(self):
        class Process:
            def __init__(self):
                self.stdout = io.BytesIO(b"{}")
            def __enter__(self): return self
            def __exit__(self, *_): self.stdout.close()
            def wait(self, **_): return 0
            def poll(self): return 0
            def kill(self): pass
        reader = SnapshotReader("hdfs://cluster:9000/snapshot", hdfs_bin="/opt/hadoop/bin/hdfs")
        with patch.object(reader, "_call", return_value=b"2\n"), patch("services.document_loader.hdfs.subprocess.Popen", return_value=Process()) as popen:
            self.assertEqual(reader.read("manifest.json"), b"{}")
            self.assertEqual(popen.call_args.args[0], ["/opt/hadoop/bin/hdfs", "dfs", "-cat", "hdfs://cluster:9000/snapshot/manifest.json"])
            self.assertNotIn("shell", popen.call_args.kwargs)

    def test_hdfs_uri_rejects_credentials_query_and_encoded_traversal(self):
        for uri in ("hdfs://user:secret@cluster/snapshot", "hdfs://cluster/snapshot?bad=1",
                    "hdfs://cluster/a/%2e%2e/outside", "hdfs://cluster/a/../outside"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                SnapshotReader(uri)

    def test_hdfs_provenance_cannot_claim_a_different_snapshot(self):
        with self.assertRaisesRegex(ValueError, "provenance cannot be overridden"):
            SnapshotReader("hdfs://cluster:9000/snapshot-a", source_uri="hdfs://cluster:9000/snapshot-b")
        reader = SnapshotReader("hdfs://cluster:9000/snapshot-a", source_uri="hdfs://cluster:9000/snapshot-a")
        self.assertEqual(reader.source_uri, reader.root)

    def test_malformed_metadata_has_a_validation_error_without_payload(self):
        _, uri = self.sec_fixture()
        manifest = json.loads((self.root / "_manifest.json").read_bytes())
        manifest["files"]["metadata.json"] = None
        self.put("_manifest.json", encoded(manifest))
        with self.assertRaisesRegex(ValueError, "Malformed"):
            load_source("sec", self.root, source_uri=uri)


if __name__ == "__main__":
    unittest.main()
