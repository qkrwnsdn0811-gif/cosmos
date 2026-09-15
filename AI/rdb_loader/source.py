"""Download immutable HDFS snapshots and validate all bytes before opening the DB."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .contract import ContractError, Manifest, WINDOWS, parse_manifest, validate_row


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(text: str):
    return json.loads(text, object_pairs_hook=_json_object)


@contextmanager
def local_snapshot(uri: str):
    if uri.startswith("hdfs://"):
        parsed = urlsplit(uri)
        if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path in ("", "/") or any(char in uri for char in "*?[]{}"):
            raise ContractError("--input must identify one HDFS snapshot directory, without globs or credentials")
        with tempfile.TemporaryDirectory(prefix="cosmos-rdb-loader-") as temporary:
            destination = Path(temporary) / "snapshot"
            # An argument vector, never a shell: HDFS_BIN can be an absolute executable path.
            result = subprocess.run([os.environ.get("HDFS_BIN", "hdfs"), "dfs", "-get", uri.rstrip("/"), str(destination)], capture_output=True, text=True, timeout=3600, check=False)
            if result.returncode:
                raise ContractError(f"HDFS snapshot download failed (exit {result.returncode})")
            yield destination
    else:
        if "://" in uri:
            raise ContractError("--input supports local paths or hdfs:// URIs")
        yield Path(uri).resolve()


@dataclass
class PreparedSnapshot:
    manifest: Manifest
    spool: object
    stored_count: int

    def rows(self):
        self.spool.seek(0)
        for line in self.spool:
            yield validate_row(read_json(line), self.manifest)


@contextmanager
def prepare_snapshot(root: Path, *, input_uri: str | None = None, allow_empty: bool = False):
    root = root.resolve(strict=True)
    if not (root / "_SUCCESS").is_file() or not (root / "manifest.json").is_file():
        raise ContractError("snapshot needs root _SUCCESS and manifest.json after job completion")
    data = read_json((root / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ContractError("manifest must be an object")
    manifest = parse_manifest(data, allow_empty=allow_empty)
    if input_uri and input_uri.startswith("hdfs://") and input_uri.rstrip("/") != manifest.hdfs_uri:
        raise ContractError("input HDFS URI differs from manifest hdfs_uri")
    expected = {path for path, _ in manifest.files}
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path.suffix in (".parquet", ".jsonl")}
    if actual != expected:
        raise ContractError("snapshot files differ from the manifest file list")
    counts = dict.fromkeys(WINDOWS, 0)
    stored_count = 0
    # Spill to disk as needed so a validated immutable copy, not a changing source,
    # is used for COPY. JSON retains Decimal values as strings without float loss.
    # Keep the identity index on disk too: a Python set grows with every raw row.
    with (
        tempfile.TemporaryDirectory(prefix="cosmos-rdb-identities-") as identity_dir,
        closing(sqlite3.connect(Path(identity_dir) / "identities.sqlite3")) as identities,
        tempfile.SpooledTemporaryFile(mode="w+t", encoding="utf-8", max_size=8 * 1024 * 1024) as spool,
    ):
        identities.execute("PRAGMA cache_size = -4096")
        identities.execute("PRAGMA temp_store = FILE")
        identities.execute("PRAGMA mmap_size = 0")
        identities.execute("""
            CREATE TABLE relationship_window (
                source_company_id BLOB NOT NULL,
                target_company_id BLOB NOT NULL,
                relationship_type TEXT NOT NULL,
                window_type TEXT NOT NULL,
                PRIMARY KEY (source_company_id, target_company_id, relationship_type, window_type)
            ) WITHOUT ROWID
        """)
        for relative, expected_hash in manifest.files:
            path = root / relative
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or path.is_symlink():
                raise ContractError("snapshot data cannot leave its directory")
            # Verify the exact bytes we parse, avoiding a hash/read race on local inputs.
            with tempfile.TemporaryFile() as verified:
                digest = hashlib.sha256()
                with resolved.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                        verified.write(chunk)
                if digest.hexdigest() != expected_hash:
                    raise ContractError(f"checksum mismatch: {relative}")
                verified.seek(0)
                if path.suffix == ".parquet":
                    import pyarrow.parquet as pq

                    records = (row for batch in pq.ParquetFile(verified).iter_batches(batch_size=8192) for row in batch.to_pylist())
                else:
                    records = (read_json(line.decode("utf-8")) for line in verified)
                for raw in records:
                    row = validate_row(raw, manifest)
                    try:
                        identities.execute("INSERT INTO relationship_window VALUES (?, ?, ?, ?)", (
                            row["source_company_id"].bytes, row["target_company_id"].bytes,
                            row["relationship_type"], row["window_type"],
                        ))
                    except sqlite3.IntegrityError as exc:
                        raise ContractError("duplicate relationship/window in snapshot") from exc
                    counts[row["window_type"]] += 1
                    if row["score"] is not None:
                        spool.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
                        stored_count += 1
        if counts != manifest.window_counts or sum(counts.values()) != manifest.record_count:
            raise ContractError("actual row/window counts differ from manifest")
        # All raw relationships need three rows, even when shorter windows have
        # both components NULL. Manifest counts alone cannot prove completeness.
        if identities.execute("""
            SELECT 1 FROM relationship_window
            GROUP BY source_company_id, target_company_id, relationship_type
            HAVING count(*) <> 3 LIMIT 1
        """).fetchone() is not None:
            raise ContractError("FULL snapshot requires 7D, 30D, and 90D rows for every relationship")
        if stored_count == 0 and not allow_empty:
            raise ContractError("empty visible snapshot requires explicit --allow-empty")
        spool.flush()
        yield PreparedSnapshot(manifest, spool, stored_count)
