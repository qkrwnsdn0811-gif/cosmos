"""Analyze an immutable historical-news Parquet run in bounded HDFS batches.

This adapter intentionally does not replay historical rows through Kafka.  It
verifies the Common Crawl delivery manifest, applies the same dictionary matcher
used by realtime analysis, and publishes independently loadable batches with an
atomic staging-directory rename.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit, urlunsplit
import uuid

from realtime_analyzer import (AnalysisError, LocalStore, HdfsStore, canonical_json,
                               join_location, normalize_location, parse_timestamp,
                               sha256)


CONTRACT_VERSION = "news-historical-company-mentions-1.0"
DATASET = "news-historical-company-mentions"
SAFE = re.compile(r"[A-Za-z0-9._-]+")
SHA256 = re.compile(r"[0-9a-f]{64}")


def _store(location: str):
    return HdfsStore(location) if location.startswith("hdfs://") else LocalStore()


def _url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisError("historical article URL is empty")
    parsed = urlsplit(value.strip())
    try:
        parsed.port
    except ValueError as error:
        raise AnalysisError("historical article URL has an invalid port") from error
    if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or any(ord(c) < 32 or c.isspace() for c in parsed.netloc)):
        raise AnalysisError("historical article URL is not an absolute public HTTP(S) URL")
    path = quote(parsed.path, safe="/%:@-._~!$&'()*+,;=")
    query = quote(parsed.query, safe="=&?/:;+,%@-._~!$'()*")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def _utc(value, label: str) -> str:
    if isinstance(value, datetime):
        value = value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
    return parse_timestamp(value, label)


def _first_seen(value: int) -> str:
    if type(value) is not int or not re.fullmatch(r"\d{14}", str(value)):
        raise AnalysisError("gdelt_first_seen must be YYYYMMDDhhmmss")
    try:
        return datetime.strptime(str(value), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError as error:
        raise AnalysisError("gdelt_first_seen is not a calendar timestamp") from error


def _load_control(store, root: str) -> tuple[dict, str, str]:
    success_bytes = store.read(join_location(root, "_SUCCESS.json"))
    manifest_bytes = store.read(join_location(root, "_manifest.json"))
    try:
        success, stats = json.loads(success_bytes), json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("historical control JSON is invalid") from error
    if (not isinstance(success, dict) or success.get("status") != "complete"
            or success.get("source") != "gdelt-commoncrawl" or success.get("stats") != stats):
        raise AnalysisError("historical completion marker does not match its manifest")
    run_id, file_name = success.get("run_id"), success.get("parquet_file")
    if SAFE.fullmatch(run_id or "") is None or file_name != "part-00000.parquet":
        raise AnalysisError("historical input identity is invalid")
    expected_sha = success.get("parquet_sha256")
    if SHA256.fullmatch(expected_sha or "") is None or type(success.get("parquet_size")) is not int:
        raise AnalysisError("historical Parquet inventory is invalid")
    if int(stats.get("deduped_articles", -1)) < 0:
        raise AnalysisError("historical manifest row count is invalid")
    return success, sha256(manifest_bytes), sha256(success_bytes)


def _company_config(aliases_path: Path, companies_path: Path):
    from matcher import CompanyMatcher

    aliases_bytes, companies_bytes = aliases_path.read_bytes(), companies_path.read_bytes()
    markets, names = {}, {}
    with companies_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            ticker, market = row.get("ticker"), row.get("market")
            if not ticker or market not in {"KOSPI", "NASDAQ"} or ticker in markets:
                raise AnalysisError("companies.csv contains an invalid or duplicate ticker")
            markets[ticker] = market
            names[ticker] = row.get("name_official") or row.get("name") or ticker
    return CompanyMatcher.from_csv(aliases_path), markets, names, sha256(aliases_bytes), sha256(companies_bytes)


def _companies(matches, markets, names):
    result = []
    for item in sorted(matches.by_ticker(), key=lambda value: value["ticker"]):
        confidence, ticker = float(item["confidence"]), item["ticker"]
        if confidence < 0.5:
            continue
        if ticker not in markets:
            raise AnalysisError("matcher ticker is missing from companies.csv")
        result.append({"ticker": ticker, "market": markets[ticker], "stock_code": ticker,
                       "name": item.get("name_official") or names[ticker],
                       "confidence": confidence, "method": item["method"],
                       "aliases": item["aliases"], "n_mentions": int(item["n_mentions"]),
                       "first_pos": item["first_pos"]})
    return result


def _schema():
    import pyarrow as pa
    company = pa.struct([
        pa.field("ticker", pa.string(), False), pa.field("market", pa.string(), False),
        pa.field("stock_code", pa.string(), False), pa.field("name", pa.string(), False),
        pa.field("confidence", pa.float64(), False), pa.field("method", pa.string(), False),
        pa.field("aliases", pa.list_(pa.string()), False), pa.field("n_mentions", pa.int32(), False),
        pa.field("first_pos", pa.string(), False),
    ])
    return pa.schema([
        pa.field("schema_version", pa.int32(), False), pa.field("article_id", pa.string(), False),
        pa.field("source", pa.string(), False), pa.field("region", pa.string(), False),
        pa.field("language", pa.string(), False), pa.field("url", pa.string(), False),
        pa.field("url_hash", pa.string(), False), pa.field("title", pa.string(), False),
        pa.field("content", pa.string(), False), pa.field("content_hash", pa.string(), False),
        pa.field("organization", pa.string(), True), pa.field("published_at", pa.string(), False),
        pa.field("collected_at", pa.string(), False), pa.field("run_id", pa.string(), False),
        pa.field("raw_file_uri", pa.string(), False), pa.field("raw_manifest_sha256", pa.string(), False),
        pa.field("raw_row_number", pa.int64(), False), pa.field("analysis_version", pa.string(), False),
        pa.field("model_version", pa.string(), False), pa.field("aliases_sha256", pa.string(), False),
        pa.field("companies_sha256", pa.string(), False), pa.field("analyzed_at", pa.string(), False),
        pa.field("archive_collection", pa.string(), True), pa.field("archive_timestamp", pa.int64(), True),
        pa.field("discovery_tickers", pa.list_(pa.string()), False),
        pa.field("discovery_company_ids", pa.list_(pa.string()), False),
        pa.field("companies", pa.list_(company), False),
    ], metadata={b"pipeline_schema": CONTRACT_VERSION.encode()})


def _encode(rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    output = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=_schema()), output, compression="snappy")
    return output.getvalue().to_pybytes()


def _publish(store, final_uri: str, manifest: dict, data: bytes):
    if store.exists(final_uri):
        existing = json.loads(store.read(join_location(final_uri, "_manifest.json")))
        if existing != manifest or store.read(join_location(final_uri, "_SUCCESS")) != b"":
            raise AnalysisError("historical analyzed destination conflicts with existing output")
        return "already_complete"
    staging = final_uri.rsplit("/", 1)[0] + "/.staging-" + uuid.uuid4().hex
    store.mkdir(staging)
    try:
        store.write_new(join_location(staging, "data.parquet"), data)
        store.write_new(join_location(staging, "_manifest.json"), canonical_json(manifest) + b"\n")
        store.write_new(join_location(staging, "_SUCCESS"), b"")
        store.rename_new(staging, final_uri)
    except Exception:
        store.remove_tree(staging)
        raise
    return "published"


def analyze(*, input_root: str, output_base: str, aliases_path: Path,
            companies_path: Path, model_version: str, analysis_version: str,
            batch_rows: int = 1000, max_batches: int | None = None,
            analyzed_at: str | None = None) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    input_root, output_base = normalize_location(input_root), normalize_location(output_base)
    if input_root.startswith("hdfs://") != output_base.startswith("hdfs://"):
        raise AnalysisError("historical input and output must use the same filesystem")
    if (SAFE.fullmatch(model_version or "") is None or SAFE.fullmatch(analysis_version or "") is None
            or not 1 <= batch_rows <= 10000 or (max_batches is not None and max_batches < 1)):
        raise AnalysisError("invalid historical analysis configuration")
    store = _store(input_root)
    control, raw_manifest_sha, success_sha = _load_control(store, input_root)
    matcher, markets, names, aliases_sha, companies_sha = _company_config(aliases_path, companies_path)
    raw_file = join_location(input_root, control["parquet_file"])
    raw_bytes = store.read(raw_file)
    if len(raw_bytes) != control["parquet_size"] or sha256(raw_bytes) != control["parquet_sha256"]:
        raise AnalysisError("historical Parquet size or SHA256 mismatch")
    parquet = pq.ParquetFile(pa.BufferReader(raw_bytes))
    if parquet.metadata.num_rows != int(control["stats"]["deduped_articles"]):
        raise AnalysisError("historical Parquet row count differs from manifest")
    timestamp = _utc(analyzed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"), "analyzed_at")
    published, already, processed, links = 0, 0, 0, 0
    for batch_index, batch in enumerate(parquet.iter_batches(batch_size=batch_rows)):
        if max_batches is not None and batch_index >= max_batches:
            break
        rows = []
        for offset, raw in enumerate(batch.to_pylist()):
            row_number = batch_index * batch_rows + offset
            url, content, title = _url(raw.get("canonical_url") or raw.get("representative_url")), raw.get("content"), raw.get("title")
            if not isinstance(content, str) or not content or not isinstance(title, str) or not title.strip():
                raise AnalysisError("historical article title/content is empty")
            content_hash = sha256(content.encode("utf-8"))
            if raw.get("content_hash") != content_hash:
                raise AnalysisError("historical article content hash mismatch")
            companies = _companies(matcher.match(title, content), markets, names)
            links += len(companies)
            rows.append({"schema_version": 1, "article_id": sha256(url.encode()),
                         "source": "gdelt-commoncrawl", "region": "overseas", "language": "en",
                         "url": url, "url_hash": sha256(url.encode()), "title": title.strip(),
                         "content": content, "content_hash": content_hash,
                         "organization": raw.get("source_domain"),
                         "published_at": _first_seen(raw.get("gdelt_first_seen")),
                         "collected_at": _utc(raw.get("processed_at"), "processed_at"),
                         "run_id": control["run_id"], "raw_file_uri": raw_file,
                         "raw_manifest_sha256": raw_manifest_sha, "raw_row_number": row_number,
                         "analysis_version": analysis_version, "model_version": model_version,
                         "aliases_sha256": aliases_sha, "companies_sha256": companies_sha,
                         "analyzed_at": timestamp, "archive_collection": raw.get("archive_collection"),
                         "archive_timestamp": raw.get("archive_timestamp"),
                         "discovery_tickers": sorted(set(raw.get("discovery_tickers") or [])),
                         "discovery_company_ids": sorted(set(raw.get("discovery_company_ids") or [])),
                         "companies": companies})
        data = _encode(rows)
        final_uri = join_location(output_base, f"model_version={model_version}/run_id={control['run_id']}/batch={batch_index:05d}")
        identity = {"contract_version": CONTRACT_VERSION, "input_uri": input_root,
                    "input_manifest_sha256": raw_manifest_sha, "input_success_sha256": success_sha,
                    "raw_parquet_sha256": control["parquet_sha256"], "run_id": control["run_id"],
                    "batch_index": batch_index, "start_row": batch_index * batch_rows,
                    "records": len(rows), "aliases_sha256": aliases_sha,
                    "companies_sha256": companies_sha, "model_version": model_version,
                    "analysis_version": analysis_version}
        manifest = {"version": 1, "dataset": DATASET, "schema_version": CONTRACT_VERSION,
                    "analysis_id": sha256(canonical_json(identity)), "identity": identity,
                    "model_version": model_version, "analysis_version": analysis_version,
                    "aliases_sha256": aliases_sha, "companies_sha256": companies_sha,
                    "minimum_confidence": 0.5, "created_at": timestamp,
                    "input": {"uri": input_root, "manifest_sha256": raw_manifest_sha,
                              "success_sha256": success_sha, "raw_file_uri": raw_file,
                              "raw_file_sha256": control["parquet_sha256"], "run_id": control["run_id"],
                              "batch_index": batch_index, "start_row": batch_index * batch_rows,
                              "records": len(rows)},
                    "output": {"records": len(rows), "docs_with_companies": sum(bool(r["companies"]) for r in rows),
                               "company_links": sum(len(r["companies"]) for r in rows),
                               "files": [{"path": "data.parquet", "bytes": len(data),
                                          "sha256": sha256(data), "records": len(rows)}]}}
        status = _publish(store, final_uri, manifest, data)
        published += status == "published"
        already += status == "already_complete"
        processed += len(rows)
    return {"status": "complete", "input_rows": parquet.metadata.num_rows,
            "processed_rows": processed, "published_batches": published,
            "already_complete_batches": already, "company_links": links}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-base", required=True)
    parser.add_argument("--aliases", type=Path, default=Path(__file__).parent / "data" / "aliases.csv")
    parser.add_argument("--companies", type=Path, default=Path(__file__).parent / "data" / "companies.csv")
    parser.add_argument("--model-version", default="dict-v1.3")
    parser.add_argument("--analysis-version", default=CONTRACT_VERSION)
    parser.add_argument("--batch-rows", type=int, default=1000)
    parser.add_argument("--max-batches", type=int)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(analyze(input_root=args.input_root, output_base=args.output_base,
                                 aliases_path=args.aliases, companies_path=args.companies,
                                 model_version=args.model_version, analysis_version=args.analysis_version,
                                 batch_rows=args.batch_rows, max_batches=args.max_batches), sort_keys=True))
        return 0
    except (AnalysisError, OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error_type": type(error).__name__, "message": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
