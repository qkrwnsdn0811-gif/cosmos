"""Verify one immutable raw-news batch and publish company mentions to HDFS.

The unit of work is the Kafka writer's final ``start=<20 digits>`` directory.
Every valid article is represented in the output, including articles for which
the matcher found no company (``companies=[]``).  Publication is an atomic
staging-directory rename and an existing result is accepted only when its
complete identity and data digest still match.

This module intentionally has no dependency on the RDB/document loader.  It can
run on the Hadoop host with ``pyarrow`` and the existing ``matcher.py`` module.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import uuid


CONTRACT_VERSION = "news-company-mentions-1.0"
DATASET = "news-company-mentions"
MINIMUM_CONFIDENCE = 0.5
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SAFE_COMPONENT_RE = re.compile(r"[A-Za-z0-9._-]+")
PARQUET_PATH_RE = re.compile(
    r"region=(domestic|overseas)/date=(\d{4}-\d{2}-\d{2})/part\.parquet"
)


class AnalysisError(RuntimeError):
    """Input, immutable-output, or publication contract violation."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise AnalysisError(f"invalid nonnegative integer: {label}")
    return value


def strict_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise AnalysisError(f"invalid SHA-256: {label}")
    return value


def safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AnalysisError("unsafe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise AnalysisError("unsafe relative path")
    return value


def normalize_location(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisError("location is required")
    value = value.rstrip("/")
    # ``urlsplit('C:\\...')`` treats the drive letter as a URI scheme.
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return str(Path(value).resolve())
    parts = urlsplit(value)
    if parts.scheme:
        if parts.scheme != "hdfs" or not parts.netloc or not parts.path.startswith("/"):
            raise AnalysisError("only hdfs:// locations and local paths are supported")
        path = PurePosixPath(parts.path)
        if ".." in path.parts or str(path) != parts.path:
            raise AnalysisError("unsafe HDFS path")
        return urlunsplit(("hdfs", parts.netloc, parts.path, "", ""))
    return str(Path(value).resolve())


def join_location(root: str, relative: str) -> str:
    relative = safe_relative(relative)
    if root.startswith("hdfs://"):
        return root.rstrip("/") + "/" + relative
    return str(Path(root) / Path(*PurePosixPath(relative).parts))


def parent_location(value: str) -> str:
    if value.startswith("hdfs://"):
        return value.rsplit("/", 1)[0]
    return str(Path(value).parent)


class LocalStore:
    def read(self, location: str) -> bytes:
        return Path(location).read_bytes()

    def exists(self, location: str) -> bool:
        return Path(location).exists()

    def mkdir(self, location: str) -> None:
        Path(location).mkdir(parents=True, exist_ok=False)

    def write_new(self, location: str, data: bytes) -> None:
        path = Path(location)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def rename_new(self, source: str, destination: str) -> None:
        if Path(destination).exists():
            raise FileExistsError("immutable destination exists")
        Path(source).rename(destination)

    def remove_tree(self, location: str) -> None:
        path = Path(location)
        if path.exists():
            shutil.rmtree(path)


class HdfsStore:
    """One persistent PyArrow/libhdfs client for all files in a batch.

    Starting ``hdfs dfs`` launches a JVM.  Doing that once per file makes tiny
    realtime batches disproportionately slow, so data reads, verification,
    writes, and the final move all share this client.
    """

    def __init__(self, root_uri: str):
        from pyarrow import fs as pafs

        self.authority = urlsplit(root_uri).netloc
        try:
            self.fs, _path = pafs.FileSystem.from_uri(root_uri)
        except Exception as error:
            raise AnalysisError("could not initialize PyArrow HDFS client") from error
        self._not_found = pafs.FileType.NotFound

    def _path(self, location: str) -> str:
        parts = urlsplit(location)
        if parts.scheme != "hdfs" or parts.netloc != self.authority or not parts.path.startswith("/"):
            raise AnalysisError("HDFS location escaped the configured authority")
        return parts.path

    def read(self, location: str) -> bytes:
        try:
            with self.fs.open_input_stream(self._path(location)) as stream:
                return stream.read()
        except Exception as error:
            raise AnalysisError("could not read HDFS input") from error

    def exists(self, location: str) -> bool:
        try:
            return self.fs.get_file_info(self._path(location)).type != self._not_found
        except Exception as error:
            raise AnalysisError("could not inspect HDFS location") from error

    def mkdir(self, location: str) -> None:
        try:
            self.fs.create_dir(self._path(location), recursive=True)
        except Exception as error:
            raise AnalysisError("could not create HDFS staging directory") from error

    def write_new(self, location: str, data: bytes) -> None:
        if self.exists(location):
            raise FileExistsError("immutable file exists")
        try:
            with self.fs.open_output_stream(self._path(location)) as stream:
                stream.write(data)
        except Exception as error:
            raise AnalysisError("could not write HDFS staging file") from error

    def rename_new(self, source: str, destination: str) -> None:
        # PyArrow move can replace on some filesystems.  The explicit check is a
        # required immutability guard; deployment also enforces a singleton lock.
        if self.exists(destination):
            raise FileExistsError("immutable destination exists")
        try:
            self.fs.move(self._path(source), self._path(destination))
        except Exception as error:
            raise AnalysisError("could not publish HDFS analysis directory") from error

    def remove_tree(self, location: str) -> None:
        if self.exists(location):
            try:
                self.fs.delete_dir(self._path(location))
            except Exception as error:
                raise AnalysisError("could not remove HDFS staging directory") from error


_HDFS_STORES: dict[str, HdfsStore] = {}


def store_for(location: str, hdfs_bin: str):
    # ``hdfs_bin`` remains part of the public call contract because discovery
    # uses it; batch I/O deliberately uses the persistent libhdfs client.
    if not location.startswith("hdfs://"):
        return LocalStore()
    authority = urlsplit(location).netloc
    if authority not in _HDFS_STORES:
        _HDFS_STORES[authority] = HdfsStore(location)
    return _HDFS_STORES[authority]


def parse_json_object(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError(f"invalid JSON: {label}") from error
    if not isinstance(value, dict):
        raise AnalysisError(f"expected JSON object: {label}")
    return value


def parse_json_lines(data: bytes, label: str) -> list[dict]:
    if data and not data.endswith(b"\n"):
        raise AnalysisError(f"incomplete JSONL final line: {label}")
    rows = []
    for index, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        rows.append(parse_json_object(line, f"{label}:{index}"))
    return rows


def parse_timestamp(value: Any, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise AnalysisError(f"invalid timestamp: {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AnalysisError(f"invalid timestamp: {label}") from error
    if parsed.tzinfo is None:
        raise AnalysisError(f"timestamp requires timezone: {label}")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RawHeader:
    manifest: dict
    manifest_sha256: str
    topic: str
    partition: int
    start: int
    end: int
    records: int
    valid_records: int
    invalid_records: int


@dataclass(frozen=True)
class RawArticle:
    payload: dict
    raw_json: str
    raw_file_uri: str
    kafka_topic: str
    kafka_partition: int
    kafka_offset: int
    published_at: str | None
    collected_at: str


def read_raw_header(store, input_uri: str) -> RawHeader:
    data = store.read(join_location(input_uri, "_manifest.json"))
    manifest = parse_json_object(data, "raw _manifest.json")
    if manifest.get("version") != 1:
        raise AnalysisError("unsupported raw news manifest version")
    topic = manifest.get("topic")
    if not isinstance(topic, str) or SAFE_COMPONENT_RE.fullmatch(topic) is None:
        raise AnalysisError("invalid Kafka topic")
    partition = strict_nonnegative_int(manifest.get("partition"), "partition")
    start = strict_nonnegative_int(manifest.get("start"), "start")
    end = strict_nonnegative_int(manifest.get("end"), "end")
    records = strict_nonnegative_int(manifest.get("records"), "records")
    valid = strict_nonnegative_int(manifest.get("valid_records"), "valid_records")
    invalid = strict_nonnegative_int(manifest.get("invalid_records"), "invalid_records")
    if end <= start or records != end - start or records != valid + invalid:
        raise AnalysisError("raw offset/count mismatch")
    for key in ("cluster_id", "topic_id", "group"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise AnalysisError(f"missing raw batch identity: {key}")
    expected = f"/topic={topic}/partition={partition}/start={start:020d}"
    if not input_uri.replace("\\", "/").endswith(expected):
        raise AnalysisError("input is not the manifest's final raw batch directory")
    if not isinstance(manifest.get("files"), list) or not manifest["files"]:
        raise AnalysisError("raw file inventory is empty")
    return RawHeader(manifest, sha256(data), topic, partition, start, end, records, valid, invalid)


def _raw_inventory(header: RawHeader) -> list[dict]:
    seen: set[str] = set()
    entries = []
    total = 0
    for entry in header.manifest["files"]:
        if not isinstance(entry, dict):
            raise AnalysisError("invalid raw file inventory entry")
        path = safe_relative(entry.get("path"))
        if path in seen or path.startswith("_"):
            raise AnalysisError("duplicate or control file in raw inventory")
        seen.add(path)
        size = strict_nonnegative_int(entry.get("bytes"), f"{path}.bytes")
        count = strict_nonnegative_int(entry.get("records"), f"{path}.records")
        digest = strict_sha256(entry.get("sha256"), f"{path}.sha256")
        total += count
        entries.append({"path": path, "bytes": size, "records": count, "sha256": digest})
    if total != header.records:
        raise AnalysisError("raw inventory record count mismatch")
    return entries


def _validate_event(payload: dict, row: dict, header: RawHeader, path: str) -> RawArticle:
    text_fields = (
        "event_id", "source", "region", "language", "url", "title", "content",
        "organization", "run_id",
    )
    if payload.get("schema_version") != 1:
        raise AnalysisError("unsupported raw event schema")
    for key in text_fields:
        value = payload.get(key)
        if not isinstance(value, str) or (key in {"event_id", "source", "url", "title", "content", "run_id"} and not value.strip()):
            raise AnalysisError(f"invalid raw event field: {key}")
        if row.get(key) != value:
            raise AnalysisError(f"raw JSON differs from Parquet projection: {key}")
    if payload["region"] not in {"domestic", "overseas"}:
        raise AnalysisError("invalid raw event region")
    if PARQUET_PATH_RE.fullmatch(path).group(1) != payload["region"]:
        raise AnalysisError("raw event region partition mismatch")
    if not payload["url"].startswith(("http://", "https://")):
        raise AnalysisError("invalid article URL")
    content_hash = strict_sha256(payload.get("content_hash"), "content_hash")
    if sha256(payload["content"].encode("utf-8")) != content_hash:
        raise AnalysisError("article content hash mismatch")
    url_hash = strict_sha256(payload.get("url_hash"), "url_hash")
    if sha256(payload["url"].encode("utf-8")) != url_hash:
        raise AnalysisError("article URL hash mismatch")
    expected_event = sha256(f"{payload['source']}\n{payload['url']}\n{content_hash}".encode("utf-8"))
    if payload["event_id"] != expected_event:
        raise AnalysisError("article event_id mismatch")
    if not isinstance(payload.get("metadata"), dict):
        raise AnalysisError("article metadata must be an object")
    topic = row.get("kafka_topic")
    partition = row.get("kafka_partition")
    offset = row.get("kafka_offset")
    if topic != header.topic or partition != header.partition:
        raise AnalysisError("Parquet Kafka identity mismatch")
    strict_nonnegative_int(offset, "kafka_offset")
    published = parse_timestamp(row.get("published_at"), "published_at", nullable=True)
    collected = parse_timestamp(row.get("collected_at"), "collected_at")
    date = PARQUET_PATH_RE.fullmatch(path).group(2)
    if datetime.fromisoformat(collected).date().isoformat() != date:
        raise AnalysisError("article collection date partition mismatch")
    return RawArticle(
        payload=payload,
        raw_json=row["raw_json"],
        raw_file_uri="",  # caller attaches the physical file URI
        kafka_topic=topic,
        kafka_partition=partition,
        kafka_offset=offset,
        published_at=published,
        collected_at=collected,
    )


def read_and_verify_raw_batch(store, input_uri: str, header: RawHeader) -> list[RawArticle]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    articles: list[RawArticle] = []
    offsets: set[int] = set()
    valid_count = invalid_count = 0
    for entry in _raw_inventory(header):
        path = entry["path"]
        data = store.read(join_location(input_uri, path))
        if len(data) != entry["bytes"] or sha256(data) != entry["sha256"]:
            raise AnalysisError(f"raw file size or SHA-256 mismatch: {path}")
        if path == "quarantine/invalid.jsonl":
            values = parse_json_lines(data, path)
            invalid_count += len(values)
            for value in values:
                if value.get("source_topic") != header.topic or value.get("source_partition") != header.partition:
                    raise AnalysisError("quarantine Kafka identity mismatch")
                offset = strict_nonnegative_int(value.get("source_offset"), "quarantine source_offset")
                if offset in offsets or not header.start <= offset < header.end:
                    raise AnalysisError("duplicate or out-of-range Kafka offset")
                offsets.add(offset)
        else:
            match = PARQUET_PATH_RE.fullmatch(path)
            if match is None:
                raise AnalysisError(f"unexpected raw news file: {path}")
            try:
                parquet = pq.ParquetFile(pa.BufferReader(data))
                if parquet.metadata.num_rows != entry["records"]:
                    raise AnalysisError("raw Parquet metadata row count mismatch")
                values = parquet.read().to_pylist()
            except AnalysisError:
                raise
            except Exception as error:
                raise AnalysisError(f"could not read raw Parquet: {path}") from error
            valid_count += len(values)
            for row in values:
                raw_json = row.get("raw_json")
                if not isinstance(raw_json, str):
                    raise AnalysisError("raw_json projection is missing")
                payload = parse_json_object(raw_json.encode("utf-8"), "raw_json")
                article = _validate_event(payload, row, header, path)
                offset = article.kafka_offset
                if offset in offsets or not header.start <= offset < header.end:
                    raise AnalysisError("duplicate or out-of-range Kafka offset")
                offsets.add(offset)
                articles.append(RawArticle(**{**article.__dict__, "raw_file_uri": join_location(input_uri, path)}))
        if len(values) != entry["records"]:
            raise AnalysisError(f"raw file record count mismatch: {path}")
    if valid_count != header.valid_records or invalid_count != header.invalid_records:
        raise AnalysisError("raw batch valid/invalid count mismatch")
    if offsets != set(range(header.start, header.end)):
        raise AnalysisError("raw batch does not cover its complete Kafka offset range")
    return sorted(articles, key=lambda article: article.kafka_offset)


def deduplicate_articles(articles: list[RawArticle]) -> tuple[list[RawArticle], int]:
    """Keep the newest delivery for an at-least-once repeated event.

    Kafka offsets remain fully verified by ``read_and_verify_raw_batch`` before
    this step.  The event identity fields must agree even if a producer bug or
    an astronomically unlikely hash collision presents the same event_id twice.
    """
    selected: dict[str, RawArticle] = {}
    identities: dict[str, tuple[str, str, str]] = {}
    for article in articles:
        payload = article.payload
        event_id = payload["event_id"]
        identity = (payload["source"], payload["url"], payload["content_hash"])
        if event_id in identities and identities[event_id] != identity:
            raise AnalysisError("duplicate event_id has conflicting source/url/content_hash")
        identities[event_id] = identity
        current = selected.get(event_id)
        incoming_key = (datetime.fromisoformat(article.collected_at), article.kafka_offset)
        if current is None:
            selected[event_id] = article
            continue
        current_key = (datetime.fromisoformat(current.collected_at), current.kafka_offset)
        if incoming_key > current_key:
            selected[event_id] = article
    result = sorted(selected.values(), key=lambda article: article.kafka_offset)
    return result, len(articles) - len(result)


def analysis_identity(
    input_uri: str,
    input_manifest_sha256: str,
    aliases_sha256: str,
    companies_sha256: str,
    model_version: str,
    analysis_version: str,
) -> tuple[dict, str]:
    identity = {
        "contract_version": CONTRACT_VERSION,
        "input_uri": input_uri,
        "input_manifest_sha256": input_manifest_sha256,
        "aliases_sha256": aliases_sha256,
        "companies_sha256": companies_sha256,
        "model_version": model_version,
        "analysis_version": analysis_version,
    }
    return identity, sha256(canonical_json(identity))


def output_uri(output_base: str, header: RawHeader, model_version: str) -> str:
    if SAFE_COMPONENT_RE.fullmatch(model_version) is None:
        raise AnalysisError("unsafe model_version")
    relative = (
        f"model_version={model_version}/topic={header.topic}/partition={header.partition}/"
        f"start={header.start:020d}"
    )
    return join_location(output_base, relative)


def encode_output(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    company = pa.struct([
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("market", pa.string(), nullable=False),
        pa.field("stock_code", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=False),
        pa.field("confidence", pa.float64(), nullable=False),
        pa.field("method", pa.string(), nullable=False),
        pa.field("aliases", pa.list_(pa.string()), nullable=False),
        pa.field("n_mentions", pa.int32(), nullable=False),
        pa.field("first_pos", pa.string(), nullable=False),
    ])
    fields = [
        pa.field("schema_version", pa.int32(), nullable=False),
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("region", pa.string(), nullable=False),
        pa.field("language", pa.string(), nullable=False),
        pa.field("url", pa.string(), nullable=False),
        pa.field("url_hash", pa.string(), nullable=False),
        pa.field("title", pa.string(), nullable=False),
        pa.field("content", pa.string(), nullable=False),
        pa.field("content_hash", pa.string(), nullable=False),
        pa.field("organization", pa.string(), nullable=False),
        pa.field("published_at", pa.string(), nullable=True),
        pa.field("collected_at", pa.string(), nullable=False),
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("metadata_json", pa.string(), nullable=False),
        pa.field("raw_json", pa.string(), nullable=False),
        pa.field("kafka_topic", pa.string(), nullable=False),
        pa.field("kafka_partition", pa.int32(), nullable=False),
        pa.field("kafka_offset", pa.int64(), nullable=False),
        pa.field("raw_input_uri", pa.string(), nullable=False),
        pa.field("raw_file_uri", pa.string(), nullable=False),
        pa.field("raw_manifest_sha256", pa.string(), nullable=False),
        pa.field("raw_batch_valid_records", pa.int64(), nullable=False),
        pa.field("raw_batch_invalid_records", pa.int64(), nullable=False),
        pa.field("analysis_version", pa.string(), nullable=False),
        pa.field("model_version", pa.string(), nullable=False),
        pa.field("aliases_sha256", pa.string(), nullable=False),
        pa.field("companies_sha256", pa.string(), nullable=False),
        pa.field("analyzed_at", pa.string(), nullable=False),
        pa.field("companies", pa.list_(company), nullable=False),
    ]
    schema = pa.schema(fields, metadata={b"pipeline_schema": CONTRACT_VERSION.encode("ascii")})
    output = pa.BufferOutputStream()
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, output, compression="snappy")
    data = output.getvalue().to_pybytes()
    if pq.ParquetFile(pa.BufferReader(data)).metadata.num_rows != len(rows):
        raise AnalysisError("output Parquet row count mismatch")
    return data


def validate_existing_output(store, final_uri: str, analysis_id: str) -> dict:
    manifest_path = join_location(final_uri, "_manifest.json")
    success_path = join_location(final_uri, "_SUCCESS")
    if not store.exists(manifest_path) or not store.exists(success_path):
        raise AnalysisError("immutable analyzed output exists without complete markers")
    if store.read(success_path) != b"":
        raise AnalysisError("invalid analyzed _SUCCESS marker")
    manifest = parse_json_object(store.read(manifest_path), "analyzed _manifest.json")
    if manifest.get("analysis_id") != analysis_id:
        raise AnalysisError("analyzed destination conflicts with a different input or analysis version")
    if manifest.get("version") != 1 or manifest.get("dataset") != DATASET or manifest.get("schema_version") != CONTRACT_VERSION:
        raise AnalysisError("unsupported analyzed output manifest")
    identity = manifest.get("identity")
    if not isinstance(identity, dict) or sha256(canonical_json(identity)) != analysis_id:
        raise AnalysisError("analyzed output identity is inconsistent")
    for key in ("model_version", "analysis_version", "aliases_sha256", "companies_sha256"):
        if manifest.get(key) != identity.get(key):
            raise AnalysisError(f"analyzed output identity field mismatch: {key}")
    source = manifest.get("input")
    if (not isinstance(source, dict) or source.get("uri") != identity.get("input_uri")
            or source.get("manifest_sha256") != identity.get("input_manifest_sha256")):
        raise AnalysisError("analyzed output input identity mismatch")
    output = manifest.get("output")
    if not isinstance(output, dict) or not isinstance(output.get("files"), list) or len(output["files"]) != 1:
        raise AnalysisError("invalid analyzed output inventory")
    entry = output["files"][0]
    if entry.get("path") != "data.parquet":
        raise AnalysisError("unexpected analyzed output file")
    expected_size = strict_nonnegative_int(entry.get("bytes"), "output bytes")
    expected_rows = strict_nonnegative_int(entry.get("records"), "output records")
    expected_hash = strict_sha256(entry.get("sha256"), "output sha256")
    data = store.read(join_location(final_uri, "data.parquet"))
    if len(data) != expected_size or sha256(data) != expected_hash:
        raise AnalysisError("analyzed output data digest mismatch")
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        rows = pq.ParquetFile(pa.BufferReader(data)).metadata.num_rows
    except Exception as error:
        raise AnalysisError("could not verify analyzed output Parquet") from error
    duplicate_records = strict_nonnegative_int(output.get("duplicate_records"), "duplicate records")
    if (rows != expected_rows or output.get("records") != expected_rows
            or source.get("valid_records") != expected_rows + duplicate_records):
        raise AnalysisError("analyzed output row count mismatch")
    return manifest


def analyze_batch(
    *,
    input_uri: str,
    output_base: str,
    aliases_path: str | Path,
    companies_path: str | Path | None = None,
    model_version: str,
    analysis_version: str,
    hdfs_bin: str = "hdfs",
    analyzed_at: str | None = None,
) -> dict:
    from matcher import CompanyMatcher

    input_uri = normalize_location(input_uri)
    output_base = normalize_location(output_base)
    if input_uri.startswith("hdfs://") != output_base.startswith("hdfs://"):
        raise AnalysisError("input and output must use the same filesystem")
    if SAFE_COMPONENT_RE.fullmatch(model_version or "") is None or len(model_version) > 50:
        raise AnalysisError("model_version must be a safe nonempty component")
    if (not isinstance(analysis_version, str) or not analysis_version.strip()
            or len(analysis_version) > 50):
        raise AnalysisError("analysis_version is required")
    aliases_path = Path(aliases_path)
    aliases_bytes = aliases_path.read_bytes()
    aliases_digest = sha256(aliases_bytes)
    companies_path = Path(companies_path or Path(__file__).parent / "data" / "companies.csv")
    companies_bytes = companies_path.read_bytes()
    companies_digest = sha256(companies_bytes)
    with companies_path.open(encoding="utf-8-sig", newline="") as stream:
        company_rows = list(csv.DictReader(stream))
    company_market: dict[str, str] = {}
    for company in company_rows:
        ticker, market = company.get("ticker"), company.get("market")
        if not ticker or market not in {"KOSPI", "NASDAQ"}:
            raise AnalysisError("invalid companies.csv ticker/market")
        if ticker in company_market:
            raise AnalysisError("duplicate ticker in companies.csv")
        company_market[ticker] = market
    store = store_for(input_uri, hdfs_bin)
    header = read_raw_header(store, input_uri)
    identity, analysis_id = analysis_identity(
        input_uri, header.manifest_sha256, aliases_digest, companies_digest,
        model_version, analysis_version
    )
    final_uri = output_uri(output_base, header, model_version)
    if store.exists(final_uri):
        manifest = validate_existing_output(store, final_uri, analysis_id)
        return {"status": "already_complete", "output_uri": final_uri, "manifest": manifest}

    articles = read_and_verify_raw_batch(store, input_uri, header)
    articles, duplicate_records = deduplicate_articles(articles)
    matcher = CompanyMatcher.from_csv(aliases_path)
    timestamp = parse_timestamp(
        analyzed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"), "analyzed_at"
    )
    rows = []
    for article in articles:
        payload = article.payload
        matches = matcher.match(payload["title"], payload["content"])
        companies = []
        for company in sorted(matches.by_ticker(), key=lambda value: value["ticker"]):
            confidence = float(company["confidence"])
            if confidence < MINIMUM_CONFIDENCE:
                continue
            market = company_market.get(company["ticker"])
            if market is None:
                raise AnalysisError(f"matcher ticker missing from companies.csv: {company['ticker']}")
            companies.append({
                "ticker": company["ticker"],
                "market": market,
                "stock_code": company["ticker"],
                "name": company["name_official"],
                "confidence": confidence,
                "method": company["method"],
                "aliases": company["aliases"],
                "n_mentions": int(company["n_mentions"]),
                "first_pos": company["first_pos"],
            })
        rows.append({
            "schema_version": 1,
            "event_id": payload["event_id"],
            "source": payload["source"],
            "region": payload["region"],
            "language": payload["language"],
            "url": payload["url"],
            "url_hash": payload["url_hash"],
            "title": payload["title"],
            "content": payload["content"],
            "content_hash": payload["content_hash"],
            "organization": payload["organization"],
            "published_at": article.published_at,
            "collected_at": article.collected_at,
            "run_id": payload["run_id"],
            "metadata_json": canonical_json(payload["metadata"]).decode("utf-8"),
            "raw_json": article.raw_json,
            "kafka_topic": article.kafka_topic,
            "kafka_partition": article.kafka_partition,
            "kafka_offset": article.kafka_offset,
            "raw_input_uri": input_uri,
            "raw_file_uri": article.raw_file_uri,
            "raw_manifest_sha256": header.manifest_sha256,
            "raw_batch_valid_records": header.valid_records,
            "raw_batch_invalid_records": header.invalid_records,
            "analysis_version": analysis_version,
            "model_version": model_version,
            "aliases_sha256": aliases_digest,
            "companies_sha256": companies_digest,
            "analyzed_at": timestamp,
            "companies": companies,
        })

    data = encode_output(rows)
    docs_with = sum(bool(row["companies"]) for row in rows)
    company_links = sum(len(row["companies"]) for row in rows)
    manifest = {
        "version": 1,
        "dataset": DATASET,
        "schema_version": CONTRACT_VERSION,
        "analysis_id": analysis_id,
        "model_name": "dictionary-matcher",
        "model_version": model_version,
        "analysis_version": analysis_version,
        "aliases_sha256": aliases_digest,
        "companies_sha256": companies_digest,
        "minimum_confidence": MINIMUM_CONFIDENCE,
        "created_at": timestamp,
        "identity": identity,
        "input": {
            "uri": input_uri,
            "manifest_sha256": header.manifest_sha256,
            "topic": header.topic,
            "partition": header.partition,
            "start": header.start,
            "end": header.end,
            "records": header.records,
            "valid_records": header.valid_records,
            "invalid_records": header.invalid_records,
        },
        "output": {
            "records": len(rows),
            "duplicate_records": duplicate_records,
            "docs_with_companies": docs_with,
            "company_links": company_links,
            "files": [{
                "path": "data.parquet",
                "bytes": len(data),
                "sha256": sha256(data),
                "records": len(rows),
            }],
        },
        "verification": {
            "raw_manifest_identity": True,
            "raw_file_hashes_and_sizes": True,
            "raw_parquet_row_counts": True,
            "complete_kafka_offset_range": True,
        },
    }
    manifest_bytes = canonical_json(manifest) + b"\n"
    staging_uri = join_location(
        parent_location(final_uri), f".staging-{header.start:020d}-{uuid.uuid4().hex}"
    )
    store.mkdir(staging_uri)
    try:
        for name, payload in (
            ("data.parquet", data),
            ("_manifest.json", manifest_bytes),
            ("_SUCCESS", b""),
        ):
            location = join_location(staging_uri, name)
            store.write_new(location, payload)
            if store.read(location) != payload:
                raise AnalysisError(f"staged analyzed output verification failed: {name}")
        try:
            store.rename_new(staging_uri, final_uri)
        except FileExistsError:
            # A concurrent identical publisher may have won. Never merge or overwrite.
            store.remove_tree(staging_uri)
            existing = validate_existing_output(store, final_uri, analysis_id)
            return {"status": "already_complete", "output_uri": final_uri, "manifest": existing}
    except Exception:
        store.remove_tree(staging_uri)
        raise
    validated = validate_existing_output(store, final_uri, analysis_id)
    return {"status": "published", "output_uri": final_uri, "manifest": validated}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="final raw news start=<20 digits> directory")
    parser.add_argument(
        "--output-base",
        default="hdfs://localhost:9000/data-lake/analyzed/news/company-mentions",
    )
    parser.add_argument("--aliases", default=str(Path(__file__).parent / "data" / "aliases.csv"))
    parser.add_argument("--companies", default=str(Path(__file__).parent / "data" / "companies.csv"))
    parser.add_argument("--model-version", default="dict-v1.3")
    parser.add_argument("--analysis-version", required=True, help="deployed AI/ner revision or release ID")
    parser.add_argument("--hdfs-bin", default=os.environ.get("HDFS_BIN", "hdfs"))
    args = parser.parse_args(argv)
    result = analyze_batch(
        input_uri=args.input,
        output_base=args.output_base,
        aliases_path=args.aliases,
        companies_path=args.companies,
        model_version=args.model_version,
        analysis_version=args.analysis_version,
        hdfs_bin=args.hdfs_bin,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
