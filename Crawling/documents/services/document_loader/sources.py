"""Read published news, DART and SEC metadata without copying disclosure bodies.

``manifest_sha256`` in the returned batch is a digest of ALL consumed metadata
file names and SHA256s (not merely the producer manifest). Physical manifest
digests and the exact verification coverage are recorded separately. Legacy SEC
batches did not publish an index checksum; an optional trusted index digest can
be supplied, otherwise only structure, counts and raw size references are checked.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
from pathlib import Path
import re
import math
from urllib.parse import quote, unquote, urlsplit

from .hdfs import SnapshotReader, safe_relative


def digest(data):
    return hashlib.sha256(data).hexdigest()


def integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid nonnegative count: " + label)
    return value


def hash_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid SHA256")
    return value


def object_json(data):
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def json_lines(data):
    if data and not data.endswith(b"\n"):
        raise ValueError("Incomplete JSONL final line")
    rows = [object_json(line) for line in data.splitlines() if line.strip()]
    return rows


class Input:
    def __init__(self, reader):
        self.reader, self.consumed, self.cache = reader, {}, {}
        self.verification = {"raw_body_hashes_verified": False, "raw_bodies_downloaded": False}

    def read(self, name, info=None, limit=None):
        if name not in self.cache:
            self.cache[name] = self.reader.read(name, limit=limit)
        data = self.cache[name]
        self.consumed[name] = digest(data)
        if info is not None:
            if len(data) != integer(info.get("bytes"), "bytes") or digest(data) != hash_value(info.get("sha256")):
                raise ValueError("Metadata size or SHA256 mismatch: " + name)
        return data

    def json(self, name, info=None):
        return object_json(self.read(name, info))

    def raw_size(self, name, expected):
        if self.reader.size(safe_relative(name)) != integer(expected, "raw bytes"):
            raise ValueError("Missing or incomplete raw file: " + name)

    def envelope(self, kind, record, raw, **provenance):
        return {"kind": kind, "record": record, "raw_uri": self.reader.uri(raw),
                "provenance": {"source_uri": self.reader.source_uri, **provenance}}


def inventory(entries):
    if not isinstance(entries, list):
        raise ValueError("Expected manifest file inventory")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid manifest file entry")
        name = safe_relative(entry.get("path"))
        if name in result:
            raise ValueError("Duplicate manifest file")
        integer(entry.get("bytes"), "bytes")
        hash_value(entry.get("sha256"))
        result[name] = entry
    return result


def news(source):
    import pyarrow as pa
    import pyarrow.parquet as pq
    manifest = source.json("_manifest.json")
    if manifest.get("version") != 1:
        raise ValueError("Unsupported news manifest")
    topic, partition, start, end = (manifest.get(k) for k in ("topic", "partition", "start", "end"))
    if not isinstance(topic, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", topic):
        raise ValueError("Invalid news topic")
    for value in (partition, start, end):
        integer(value, "Kafka range")
    expected_suffix = f"/topic={topic}/partition={partition}/start={start:020d}"
    if not source.reader.source_uri.endswith(expected_suffix):
        raise ValueError("News input must identify its final committed Kafka batch directory")
    total, valid, invalid = (integer(manifest.get(k), k) for k in ("records", "valid_records", "invalid_records"))
    if end <= start or total != end - start or total != valid + invalid:
        raise ValueError("News manifest offset/count mismatch")
    for key in ("cluster_id", "topic_id", "group"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError("Missing Kafka batch identity")
    entries = inventory(manifest.get("files"))
    records, offsets, valid_count, invalid_count = [], set(), 0, 0
    for name, info in entries.items():
        data = source.read(name, info)
        count = integer(info.get("records"), "file records")
        if name == "quarantine/invalid.jsonl":
            values = json_lines(data)
            invalid_count += len(values)
            for row in values:
                if row.get("source_topic") != topic or row.get("source_partition") != partition:
                    raise ValueError("Quarantine Kafka identity mismatch")
                offset = row.get("source_offset")
                integer(offset, "Kafka offset")
                if offset in offsets or not start <= offset < end:
                    raise ValueError("Duplicate or out-of-range Kafka offset")
                offsets.add(offset)
        else:
            match = re.fullmatch(r"region=(domestic|overseas)/date=(\d{4}-\d{2}-\d{2})/part\.parquet", name)
            if not match:
                raise ValueError("Unexpected news batch file")
            parquet = pq.ParquetFile(pa.BufferReader(data))
            if parquet.metadata.num_rows != count:
                raise ValueError("Parquet record count mismatch")
            values = parquet.read().to_pylist()
            valid_count += len(values)
            for row in values:
                if (row.get("schema_version") != 1 or row.get("region") != match[1]
                        or row.get("kafka_topic") != topic or row.get("kafka_partition") != partition):
                    raise ValueError("Parquet source identity mismatch")
                offset = row.get("kafka_offset")
                integer(offset, "Kafka offset")
                if offset in offsets or not start <= offset < end:
                    raise ValueError("Duplicate or out-of-range Kafka offset")
                offsets.add(offset)
                collected = datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00"))
                if collected.tzinfo is None or collected.date().isoformat() != match[2]:
                    raise ValueError("News date partition mismatch")
                payload = object_json(row["raw_json"])
                for key in ("event_id", "source", "region", "language", "url", "title", "content", "organization", "run_id"):
                    if payload.get(key) != row.get(key):
                        raise ValueError("News payload differs from Parquet projection")
                # Preserve the richer original event (company hints, hashes) and
                # use the writer's UTC-normalized timestamp projection.
                payload.update({k: row.get(k) for k in ("published_at", "collected_at", "kafka_topic", "kafka_partition", "kafka_offset")})
                records.append(source.envelope("news", payload, name, metadata_sha256=info["sha256"],
                                               region=match[1], kafka_offset=offset, event_id=row["event_id"],
                                               collected_at=row["collected_at"]))
        if len(values) != count:
            raise ValueError("News file record count mismatch")
    if (valid_count, invalid_count, len(offsets)) != (valid, invalid, total):
        raise ValueError("News batch record count mismatch")
    source.verification.update(completion="atomic_final_directory", metadata_hashes_verified=True,
                               valid_records=valid, quarantined_records=invalid,
                               physical_manifest_sha256=source.consumed["_manifest.json"])
    return "news", records


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _analyzed_company(value, region):
    if not isinstance(value, dict):
        raise ValueError("Analyzed news company must be an object")
    required = {"ticker", "market", "stock_code", "name", "confidence", "method",
                "aliases", "n_mentions", "first_pos"}
    if set(value) != required:
        raise ValueError("Analyzed news company schema mismatch")
    ticker, market, stock_code, name, confidence, method = (
        value.get(k) for k in ("ticker", "market", "stock_code", "name", "confidence", "method"))
    if market not in {"KOSPI", "NASDAQ"}:
        raise ValueError("Invalid analyzed news company market")
    pattern = r"[0-9A-Z]{6}" if market == "KOSPI" else r"[A-Z0-9][A-Z0-9.-]{0,29}"
    if (ticker != stock_code or not isinstance(stock_code, str)
            or not re.fullmatch(pattern, stock_code)):
        raise ValueError("Invalid analyzed news company identity")
    if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 300:
        raise ValueError("Invalid analyzed news company name")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0.5 <= confidence <= 1):
        raise ValueError("Invalid analyzed news confidence")
    tokens = method.split("+") if isinstance(method, str) else []
    if not tokens or tokens != sorted(set(tokens)) or not set(tokens) <= {"dict", "ellipsis", "product", "rule"}:
        raise ValueError("Invalid analyzed news match method")
    aliases = value.get("aliases")
    if (not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip()
            or alias != alias.strip() or len(alias) > 300 for alias in aliases)
            or aliases != sorted(set(aliases))):
        raise ValueError("Invalid analyzed news aliases")
    mentions, first = value.get("n_mentions"), value.get("first_pos")
    if type(mentions) is not int or mentions < 1 or first not in {"title", "lead", "body"}:
        raise ValueError("Invalid analyzed news mention position/count")
    return {"ticker": ticker, "market": market, "stock_code": stock_code,
            "name": name, "confidence": float(confidence), "method": method,
            "aliases": aliases, "n_mentions": mentions, "first_pos": first}


def news_analyzed(source):
    """Read one immutable AI company-mention output for a raw Kafka news batch."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if source.read("_SUCCESS", limit=1) != b"":
        raise ValueError("Invalid analyzed news completion marker")
    manifest = source.json("_manifest.json")
    if (manifest.get("version") != 1 or manifest.get("dataset") != "news-company-mentions"
            or manifest.get("schema_version") != "news-company-mentions-1.0"
            or manifest.get("minimum_confidence") != 0.5):
        raise ValueError("Unsupported analyzed news manifest")
    model = manifest.get("model_version")
    analysis = manifest.get("analysis_version")
    aliases_hash = hash_value(manifest.get("aliases_sha256"))
    companies_hash = hash_value(manifest.get("companies_sha256"))
    if (not isinstance(model, str) or not model or len(model) > 50
            or not isinstance(analysis, str) or not analysis or len(analysis) > 50):
        raise ValueError("Invalid analyzed news model identity")
    analyzed_at = manifest.get("created_at")
    try:
        parsed_at = datetime.fromisoformat(analyzed_at.replace("Z", "+00:00"))
        if parsed_at.tzinfo is None:
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Invalid analyzed news creation timestamp") from None

    raw_input, output = manifest.get("input"), manifest.get("output")
    if not isinstance(raw_input, dict) or not isinstance(output, dict):
        raise ValueError("Analyzed news manifest requires input and output objects")
    input_uri = raw_input.get("uri")
    input_manifest = hash_value(raw_input.get("manifest_sha256"))
    local_input = (isinstance(input_uri, str) and not input_uri.startswith("hdfs://")
                   and Path(input_uri).is_absolute())
    if (not isinstance(input_uri, str) or input_uri.endswith("/")
            or (not input_uri.startswith("hdfs://") and not local_input)):
        raise ValueError("Invalid analyzed news raw input URI")
    topic, partition, start, end = (raw_input.get(k) for k in ("topic", "partition", "start", "end"))
    if not isinstance(topic, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", topic):
        raise ValueError("Invalid analyzed news topic")
    for value in (partition, start, end):
        integer(value, "analyzed news Kafka range")
    total, valid, invalid = (integer(raw_input.get(k), k) for k in
                             ("records", "valid_records", "invalid_records"))
    if end <= start or total != end - start or total != valid + invalid:
        raise ValueError("Analyzed news input offset/count mismatch")
    expected_raw_suffix = f"/topic={topic}/partition={partition}/start={start:020d}"
    if not input_uri.replace("\\", "/").endswith(expected_raw_suffix):
        raise ValueError("Analyzed news raw input URI identity mismatch")
    # The writer percent-escapes path partition values. quote(..., safe='') is
    # deterministic and leaves only URI-safe unreserved bytes unchanged.
    expected_output_suffix = (f"/model_version={quote(model, safe='')}/topic={quote(topic, safe='')}"
                              f"/partition={partition}/start={start:020d}")
    output_identity_path = unquote(urlsplit(source.reader.source_uri).path)
    if not output_identity_path.endswith(expected_output_suffix):
        raise ValueError("Analyzed news output directory identity mismatch")

    identity = {"contract_version": "news-company-mentions-1.0", "input_uri": input_uri,
                "input_manifest_sha256": input_manifest, "aliases_sha256": aliases_hash,
                "companies_sha256": companies_hash, "model_version": model,
                "analysis_version": analysis}
    if manifest.get("identity") != identity or manifest.get("analysis_id") != digest(_canonical_json(identity)):
        raise ValueError("Analyzed news analysis identity mismatch")

    if set(output) != {"records", "duplicate_records", "docs_with_companies", "company_links", "files"}:
        raise ValueError("Analyzed news output manifest schema mismatch")
    output_records = integer(output.get("records"), "analyzed news records")
    duplicate_records = integer(output.get("duplicate_records"), "analyzed news duplicate records")
    docs_with_companies = integer(output.get("docs_with_companies"), "documents with companies")
    company_links = integer(output.get("company_links"), "analyzed news company links")
    if output_records + duplicate_records != valid or docs_with_companies > output_records:
        raise ValueError("Analyzed news output count mismatch")
    entries = inventory(output.get("files"))
    if set(entries) != {"data.parquet"} or integer(entries["data.parquet"].get("records"), "file records") != output_records:
        raise ValueError("Analyzed news output must contain one declared Parquet file")
    data = source.read("data.parquet", entries["data.parquet"])
    parquet = pq.ParquetFile(pa.BufferReader(data))
    if parquet.metadata.num_rows != output_records:
        raise ValueError("Analyzed news Parquet record count mismatch")
    rows = parquet.read().to_pylist()

    records, event_ids, offsets, link_count, with_companies = [], set(), set(), 0, 0
    projection = ("event_id", "source", "region", "language", "url", "title", "content",
                  "organization", "run_id")
    row_fields = {"schema_version", *projection, "url_hash", "content_hash", "published_at",
                  "collected_at", "metadata_json", "raw_json", "kafka_topic", "kafka_partition",
                  "kafka_offset", "raw_input_uri", "raw_file_uri", "raw_manifest_sha256",
                  "raw_batch_valid_records", "raw_batch_invalid_records", "analysis_version",
                  "model_version", "aliases_sha256", "companies_sha256", "analyzed_at", "companies"}
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_fields or row.get("schema_version") != 1:
            raise ValueError("Analyzed news row schema mismatch")
        if (row.get("kafka_topic"), row.get("kafka_partition")) != (topic, partition):
            raise ValueError("Analyzed news Kafka identity mismatch")
        offset = row.get("kafka_offset")
        integer(offset, "analyzed news Kafka offset")
        if offset in offsets or not start <= offset < end:
            raise ValueError("Duplicate or out-of-range analyzed news Kafka offset")
        offsets.add(offset)
        event_id = row.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in event_ids:
            raise ValueError("Invalid or duplicate analyzed news event ID")
        event_ids.add(event_id)
        if (row.get("raw_input_uri") != input_uri or row.get("raw_manifest_sha256") != input_manifest
                or row.get("analysis_version") != analysis or row.get("model_version") != model
                or row.get("aliases_sha256") != aliases_hash
                or row.get("companies_sha256") != companies_hash
                or row.get("raw_batch_valid_records") != valid
                or row.get("raw_batch_invalid_records") != invalid
                or row.get("analyzed_at") != analyzed_at):
            raise ValueError("Analyzed news row provenance differs from manifest")
        raw_file_uri = row.get("raw_file_uri")
        separator = "\\" if local_input and "\\" in input_uri else "/"
        if (not isinstance(raw_file_uri, str) or not raw_file_uri.startswith(input_uri + separator)
                or not raw_file_uri.endswith(".parquet")):
            raise ValueError("Invalid analyzed news raw file URI")
        stored_raw_uri = Path(raw_file_uri).resolve(strict=True).as_uri() if local_input else raw_file_uri
        payload = object_json(row.get("raw_json", "").encode("utf-8"))
        for key in projection:
            if payload.get(key) != row.get(key):
                raise ValueError("Analyzed news payload differs from Parquet projection")
        metadata = object_json(row.get("metadata_json", "").encode("utf-8"))
        if payload.get("metadata", {}) != metadata:
            raise ValueError("Analyzed news metadata differs from raw payload")
        url = row.get("url")
        content = row.get("content")
        if (not isinstance(url, str) or row.get("url_hash") != digest(url.encode("utf-8"))
                or not isinstance(content, str) or not content
                or row.get("content_hash") != digest(content.encode("utf-8"))):
            raise ValueError("Analyzed news URL/content hash mismatch")
        expected_event = digest(f"{row.get('source')}\n{url}\n{row.get('content_hash')}".encode("utf-8"))
        if event_id != expected_event:
            raise ValueError("Analyzed news event identity mismatch")
        region = row.get("region")
        if region not in {"domestic", "overseas"}:
            raise ValueError("Invalid analyzed news region")
        try:
            collected = datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00"))
            if collected.tzinfo is None:
                raise ValueError()
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Invalid analyzed news collection timestamp") from None
        partition_match = re.search(r"/region=(domestic|overseas)/date=(\d{4}-\d{2}-\d{2})/part\.parquet$",
                                    raw_file_uri.replace("\\", "/"))
        if (not partition_match or partition_match[1] != region
                or partition_match[2] != collected.date().isoformat()):
            raise ValueError("Analyzed news raw partition mismatch")
        companies = row.get("companies")
        if not isinstance(companies, list):
            raise ValueError("Analyzed news companies must be a list")
        normalized_companies = [_analyzed_company(item, region) for item in companies]
        tickers = [item["stock_code"] for item in normalized_companies]
        if tickers != sorted(set(tickers)):
            raise ValueError("Analyzed news companies must be unique and sorted")
        if normalized_companies:
            with_companies += 1
        link_count += len(normalized_companies)
        record = dict(row)
        record["metadata"] = metadata
        record["companies"] = normalized_companies
        records.append(source.envelope("news-analyzed", record, "data.parquet",
                                      input_manifest_sha256=input_manifest,
                                      event_id=event_id, kafka_offset=offset))
        records[-1]["raw_uri"] = stored_raw_uri
    if len(offsets) != output_records or with_companies != docs_with_companies or link_count != company_links:
        raise ValueError("Analyzed news aggregate counts differ from manifest")
    source.verification.update(completion="_SUCCESS", metadata_hashes_verified=True,
                               raw_input_manifest_bound=True, records=output_records,
                               duplicate_records=duplicate_records,
                               docs_with_companies=with_companies, company_links=link_count,
                               physical_manifest_sha256=source.consumed["_manifest.json"])
    return "news-analyzed", records


def news_historical_analyzed(source):
    """Read one bounded historical company-mention output batch."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if source.read("_SUCCESS", limit=1) != b"":
        raise ValueError("Invalid historical analyzed completion marker")
    manifest = source.json("_manifest.json")
    contract = "news-historical-company-mentions-1.0"
    if (manifest.get("version") != 1 or manifest.get("dataset") != "news-historical-company-mentions"
            or manifest.get("schema_version") != contract or manifest.get("minimum_confidence") != 0.5):
        raise ValueError("Unsupported historical analyzed manifest")
    identity, raw_input, output = manifest.get("identity"), manifest.get("input"), manifest.get("output")
    if not all(isinstance(value, dict) for value in (identity, raw_input, output)):
        raise ValueError("Historical analyzed manifest objects are missing")
    model, analysis, run_id = manifest.get("model_version"), manifest.get("analysis_version"), raw_input.get("run_id")
    batch_index, start_row, count = raw_input.get("batch_index"), raw_input.get("start_row"), raw_input.get("records")
    for value, label in ((batch_index, "batch_index"), (start_row, "start_row"), (count, "records")):
        integer(value, label)
    if (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", model)
            or analysis != contract or not isinstance(run_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]+", run_id)):
        raise ValueError("Historical analyzed identity is invalid")
    expected_suffix = f"/model_version={quote(model, safe='')}/run_id={quote(run_id, safe='')}/batch={batch_index:05d}"
    if not unquote(urlsplit(source.reader.source_uri).path).endswith(expected_suffix):
        raise ValueError("Historical analyzed output path identity mismatch")
    aliases_hash, companies_hash = hash_value(manifest.get("aliases_sha256")), hash_value(manifest.get("companies_sha256"))
    expected_identity = {"contract_version": contract, "input_uri": raw_input.get("uri"),
                         "input_manifest_sha256": raw_input.get("manifest_sha256"),
                         "input_success_sha256": raw_input.get("success_sha256"),
                         "raw_parquet_sha256": raw_input.get("raw_file_sha256"), "run_id": run_id,
                         "batch_index": batch_index, "start_row": start_row, "records": count,
                         "aliases_sha256": aliases_hash, "companies_sha256": companies_hash,
                         "model_version": model, "analysis_version": analysis}
    for field in ("input_manifest_sha256", "input_success_sha256", "raw_parquet_sha256"):
        hash_value(expected_identity[field])
    if identity != expected_identity or manifest.get("analysis_id") != digest(_canonical_json(identity)):
        raise ValueError("Historical analyzed manifest identity mismatch")
    files = inventory(output.get("files"))
    if set(files) != {"data.parquet"} or integer(files["data.parquet"].get("records"), "file records") != count:
        raise ValueError("Historical analyzed output inventory is invalid")
    data = source.read("data.parquet", files["data.parquet"])
    parquet = pq.ParquetFile(pa.BufferReader(data))
    if parquet.metadata.num_rows != count or output.get("records") != count:
        raise ValueError("Historical analyzed Parquet row count mismatch")
    rows, records, ids, links, with_companies = parquet.read().to_pylist(), [], set(), 0, 0
    analyzed_at = manifest.get("created_at")
    for offset, row in enumerate(rows):
        if row.get("schema_version") != 1 or row.get("raw_row_number") != start_row + offset:
            raise ValueError("Historical analyzed row position mismatch")
        article_id, url, content = row.get("article_id"), row.get("url"), row.get("content")
        if (not isinstance(url, str) or row.get("url_hash") != digest(url.encode())
                or article_id != row.get("url_hash") or article_id in ids
                or not isinstance(content, str) or not content
                or row.get("content_hash") != digest(content.encode())):
            raise ValueError("Historical analyzed article identity/hash mismatch")
        ids.add(article_id)
        if (row.get("run_id") != run_id or row.get("raw_manifest_sha256") != raw_input.get("manifest_sha256")
                or row.get("raw_file_uri") != raw_input.get("raw_file_uri")
                or row.get("analysis_version") != analysis or row.get("model_version") != model
                or row.get("aliases_sha256") != aliases_hash or row.get("companies_sha256") != companies_hash
                or row.get("analyzed_at") != analyzed_at or row.get("region") != "overseas"):
            raise ValueError("Historical analyzed row provenance differs from manifest")
        companies = [_analyzed_company(item, "overseas") for item in row.get("companies", [])]
        tickers = [item["stock_code"] for item in companies]
        if tickers != sorted(set(tickers)):
            raise ValueError("Historical analyzed companies must be unique and sorted")
        with_companies += bool(companies)
        links += len(companies)
        record = dict(row)
        record["companies"] = companies
        record["metadata"] = {"archive_collection": row.get("archive_collection"),
                              "archive_timestamp": row.get("archive_timestamp"),
                              "discovery_tickers": row.get("discovery_tickers"),
                              "discovery_company_ids": row.get("discovery_company_ids"),
                              "historical_time_basis": "gdelt_first_seen"}
        records.append(source.envelope("news-historical-analyzed", record, "data.parquet",
                                      article_id=article_id, raw_row_number=row["raw_row_number"]))
        records[-1]["raw_uri"] = row["raw_file_uri"]
    if output.get("docs_with_companies") != with_companies or output.get("company_links") != links:
        raise ValueError("Historical analyzed aggregate counts differ from manifest")
    source.verification.update(completion="_SUCCESS", metadata_hashes_verified=True,
                               records=count, docs_with_companies=with_companies,
                               company_links=links, physical_manifest_sha256=source.consumed["_manifest.json"])
    return "news-historical-analyzed", records


def dart(source):
    ready = source.json("ready.json")
    if ready.get("status") != "ready" or ready.get("credential_text_check") != "passed":
        raise ValueError("DART snapshot is not ready")
    manifest = source.json("manifest.json")
    checksums = source.json("checksums.json")
    for name, field in (("manifest.json", "manifest_sha256"), ("checksums.json", "checksums_sha256")):
        if source.consumed[name] != hash_value(ready.get(field)):
            raise ValueError("DART control checksum mismatch")
    if manifest.get("format_version") != 1 or checksums.get("algorithm") != "sha256":
        raise ValueError("Unsupported DART manifest")
    entries = inventory(checksums.get("files"))
    if any(name not in entries for name in ("manifest.json", "metadata/document_index.jsonl")):
        raise ValueError("DART metadata checksums are missing")
    if any(name in entries for name in ("checksums.json", "ready.json")):
        raise ValueError("Invalid DART checksum inventory")
    source.read("manifest.json", entries["manifest.json"])
    rows = json_lines(source.read("metadata/document_index.jsonl", entries["metadata/document_index.jsonl"]))
    count = integer(manifest.get("snapshot_documents"), "snapshot documents")
    if (len(rows) != count or ready.get("snapshot_documents") != count
            or integer(manifest.get("selected_documents"), "selected documents") - count
            != integer(manifest.get("not_in_snapshot"), "pending documents")):
        raise ValueError("DART snapshot count mismatch")
    receipts, records = set(), []
    if rows:
        # A baseline can reference hundreds of archives. Fetch each tree's
        # size inventory once; every row still checks its declared file sizes.
        source.reader.prime_sizes("raw")
        source.reader.prime_sizes("data")
    for row in rows:
        receipt = row.get("rcept_no")
        if not isinstance(receipt, str) or not re.fullmatch(r"\d{14}", receipt) or receipt in receipts:
            raise ValueError("Invalid or duplicate DART receipt")
        if row.get("receipt", receipt) != receipt:
            raise ValueError("DART receipt fields differ")
        receipts.add(receipt)
        bundle, member, detail = (safe_relative(row.get(k)) for k in ("raw_bundle", "raw_member", "snapshot_detail"))
        if (not re.fullmatch(r"raw/documents-part-\d{5}\.tar", bundle)
                or member != f"documents/{receipt}.zip" or not detail.startswith("data/")
                or bundle not in entries or detail not in entries):
            raise ValueError("Invalid DART body reference")
        hash_value(row.get("raw_sha256"))
        if not integer(row.get("raw_bytes"), "raw bytes"):
            raise ValueError("Empty DART body reference")
        source.raw_size(bundle, entries[bundle]["bytes"])
        source.raw_size(detail, entries[detail]["bytes"])
        envelope = source.envelope("dart", row, bundle, raw_member=member,
                                   raw_sha256_declared=row["raw_sha256"],
                                   metadata_sha256=entries["metadata/document_index.jsonl"]["sha256"],
                                   collected_at=manifest.get("captured_at") or manifest.get("completed_at"))
        envelope["raw_uri"] += "#" + member
        records.append(envelope)
    source.verification.update(completion="ready.json", metadata_hashes_verified=True,
                               raw_sizes_verified=True, records=count,
                               physical_manifest_sha256=source.consumed["manifest.json"])
    return "dart", records


def sec_identity(row):
    cik, accession, filing_date = str(row.get("cik", "")), row.get("accessionNumber"), row.get("filingDate")
    if (not re.fullmatch(r"[1-9]\d{0,9}", cik) or not isinstance(accession, str)
            or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)):
        raise ValueError("Invalid SEC filing identity")
    if datetime.strptime(filing_date, "%Y-%m-%d").date().isoformat() != filing_date:
        raise ValueError("Invalid SEC filing date")
    return {"cik": cik, "accession": accession, "filing_date": filing_date}


def sec_record(source, row, relative, **provenance):
    identity = sec_identity(row)
    hash_value(row.get("sha256"))
    source.raw_size(relative, row.get("bytes"))
    if not row["bytes"]:
        raise ValueError("Empty SEC raw body")
    accession = identity["accession"]
    bases = [f"https://www.sec.gov/Archives/edgar/data/{cik}/"
             for cik in (identity["cik"], identity["cik"].zfill(10))]
    urls = [base + suffix for base in bases for suffix in
            (accession.replace("-", "") + "/" + accession + ".txt", accession + ".txt")]
    if row.get("sourceUrl") not in urls:
        raise ValueError("Unexpected SEC source URL")
    return source.envelope("sec", row, relative, raw_sha256_declared=row["sha256"], **provenance)


def sec_incremental(source):
    if source.read("_SUCCESS", limit=1) != b"":
        raise ValueError("Invalid SEC completion marker")
    manifest = source.json("_manifest.json")
    files = manifest.get("files", {})
    if (manifest.get("version") != 1 or manifest.get("dataset") != "sec-edgar-incremental"
            or not isinstance(files, dict) or set(files) != {"metadata.json", "raw.txt"}):
        raise ValueError("Unsupported SEC incremental manifest")
    row = source.json("metadata.json", files["metadata.json"])
    identity = sec_identity(row)
    if manifest.get("identity") != identity:
        raise ValueError("SEC manifest identity mismatch")
    expected = f"/filing_date={identity['filing_date']}/cik={identity['cik']}/accession={identity['accession']}"
    if not source.reader.source_uri.endswith(expected):
        raise ValueError("SEC input must identify a final filing directory")
    if (row.get("bytes") != files["raw.txt"].get("bytes")
            or row.get("sha256") != files["raw.txt"].get("sha256")):
        raise ValueError("SEC raw hash reference differs from manifest")
    record = sec_record(source, row, "raw.txt", metadata_sha256=files["metadata.json"]["sha256"],
                        collected_at=row.get("collectedAt"))
    source.verification.update(completion="_SUCCESS", metadata_hashes_verified=True,
                               raw_sizes_verified=True, records=1,
                               physical_manifest_sha256=source.consumed["_manifest.json"])
    return "sec-incremental", [record]


def sec_batch(source, expected_index_sha256):
    if source.read("_SUCCESS", limit=1) != b"":
        raise ValueError("Invalid SEC completion marker")
    manifest = source.json("manifest.json")
    if (manifest.get("dataset") != "sec-edgar-nasdaq100" or manifest.get("metadataOnly") is not False
            or not manifest.get("completedAt")):
        raise ValueError("SEC batch is not a completed body snapshot")
    raw = source.read("filings.jsonl")
    if expected_index_sha256 is not None and digest(raw) != hash_value(expected_index_sha256):
        raise ValueError("SEC index checksum mismatch")
    rows = json_lines(raw)
    if len(rows) != integer(manifest.get("filingCount"), "filings"):
        raise ValueError("SEC batch filing count mismatch")
    records, identities, paths, total = [], set(), set(), 0
    index_sha256 = digest(raw)
    if rows:
        source.reader.prime_sizes("raw")
    for row in rows:
        identity = sec_identity(row)
        key = (identity["cik"], identity["accession"])
        path = safe_relative(row.get("localPath"))
        if not path.startswith("raw/") or not path.endswith(".txt") or path in paths or key in identities:
            raise ValueError("Duplicate or invalid SEC filing reference")
        identities.add(key)
        paths.add(path)
        records.append(sec_record(source, row, path, metadata_sha256=index_sha256,
                                  collected_at=manifest["completedAt"]))
        total += row["bytes"]
    if total != integer(manifest.get("totalBytes"), "total bytes"):
        raise ValueError("SEC batch total bytes mismatch")
    source.verification.update(completion="_SUCCESS", metadata_hashes_verified=expected_index_sha256 is not None,
                               metadata_hash_note="Legacy producer manifest does not bind filings.jsonl SHA256",
                               raw_sizes_verified=True, records=len(records),
                               physical_manifest_sha256=source.consumed["manifest.json"])
    return "sec-batch", records


def load_source(kind, root, *, hdfs_bin=None, expected_index_sha256=None, source_uri=None):
    """Load one published unit; ``sec`` denotes the incremental filing format.

    Kinds: news, news-analyzed, news-historical-analyzed, dart,
    sec/sec-incremental, sec-batch. For local fixture copies,
    source_uri can retain their original HDFS provenance and final-directory name.
    """
    reader = SnapshotReader(root, hdfs_bin=hdfs_bin, source_uri=source_uri)
    source = Input(reader)
    try:
        if kind == "news":
            format_name, records = news(source)
        elif kind == "news-analyzed":
            format_name, records = news_analyzed(source)
        elif kind == "news-historical-analyzed":
            format_name, records = news_historical_analyzed(source)
        elif kind == "dart":
            format_name, records = dart(source)
        elif kind in ("sec", "sec-incremental"):
            format_name, records = sec_incremental(source)
        elif kind == "sec-batch":
            format_name, records = sec_batch(source, expected_index_sha256)
        else:
            raise ValueError("Unsupported document input kind")
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError("Malformed " + str(kind) + " metadata (" + type(error).__name__ + ")") from None
    composite = digest(json.dumps(source.consumed, sort_keys=True, separators=(",", ":")).encode())
    return {"format": format_name, "input_uri": reader.source_uri,
            "batch_id": digest(reader.source_uri.encode()), "manifest_sha256": composite,
            "records": records, "verification": {**source.verification,
                "consumed_metadata_sha256": dict(source.consumed),
                "manifest_sha256_definition": "SHA256 of canonical filename-to-SHA256 map for all consumed metadata"}}
