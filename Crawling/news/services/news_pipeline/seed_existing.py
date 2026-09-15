"""Read preserved HDFS URL columns into the collector's deduplication index.

Only local SQLite/status files are written. Article content is never read or
logged, and no old HDFS paths are rewritten or sent back through Kafka.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import signal
import time

from .common import Outbox, atomic_json, canonical_url, url_hash, utcnow


DEFAULT_ROOTS = (
    "/datasets/news/live/mysql_overseas/data",
    "/datasets/news/snapshots/20260908-prepared/news",
)
URL_NAMES = ("url", "article_url", "original_url", "news_url", "source_url", "web_url", "link", "canonical_url")
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def select_columns(names: list[str], path: str) -> tuple[list[str], str | None]:
    """Only the verified original overseas mirror has known hash semantics."""
    lookup = {name.lower(): name for name in names}
    urls = [lookup[name] for name in URL_NAMES if name in lookup]
    known_mirror = path.startswith(DEFAULT_ROOTS[0].rstrip("/") + "/")
    fallback = lookup.get("url_hash") if known_mirror else None
    return urls, fallback


def resolve_hash(row: dict, urls: list[str], fallback: str | None) -> str | None:
    for column in urls:
        value = row[column]
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                continue
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            return url_hash(canonical_url(value))
        except ValueError:
            continue
    if fallback:
        value = row[fallback]
        if isinstance(value, bytes):
            try:
                value = value.decode("ascii")
            except UnicodeDecodeError:
                return None
        if isinstance(value, str) and HASH_RE.fullmatch(value):
            return value.lower()
    return None


def run_seed(outbox, hdfs, roots, status_path, *, max_files=None, batch_size=100000, parquet_module=None, fs_module=None):
    if parquet_module is None:
        import pyarrow.parquet as parquet_module
    if fs_module is None:
        import pyarrow.fs as fs_module
    if batch_size < 1 or (max_files is not None and max_files < 1):
        raise ValueError("batch-size and max-files must be positive")
    status = {
        "started_at": utcnow(), "updated_at": utcnow(), "state": "running",
        "roots": list(roots), "inventory_files": 0, "scanned_files": 0,
        "completed_files": 0, "skipped_files": 0, "scanned_rows": 0,
        "inserted_unique": 0, "unresolved_rows": 0, "unresolved_files": [],
        "errors": [], "limited": False,
    }
    status["unique"] = outbox.db.execute("SELECT count(*) FROM seen_urls").fetchone()[0]
    baseline_unique = status["unique"]
    last_report = 0.0

    def report(force=False):
        nonlocal last_report
        if force or time.monotonic() - last_report >= 5:
            status["unique"] = baseline_unique + status["inserted_unique"]
            status["updated_at"] = utcnow()
            atomic_json(status_path, status)
            last_report = time.monotonic()

    report(True)
    files = {}
    for root in roots:
        try:
            for item in hdfs.get_file_info(fs_module.FileSelector(root, recursive=True)):
                if item.type == fs_module.FileType.File and item.path.lower().endswith(".parquet"):
                    files[item.path] = item
        except Exception as exc:
            # Avoid printing server errors/URLs; the root and exception type are
            # sufficient to identify the failed inventory without leaking data.
            status["errors"].append({"path": root, "stage": "inventory", "error": type(exc).__name__})
    status["inventory_files"] = len(files)
    report(True)
    for path, info in sorted(files.items()):
        existing = outbox.db.execute("SELECT size,rows FROM seed_files WHERE path=?", (path,)).fetchone()
        if existing is not None and existing["size"] == info.size:
            status["skipped_files"] += 1
            continue
        if max_files is not None and status["scanned_files"] >= max_files:
            status["limited"] = True
            break
        status["current_file"] = path
        status["scanned_files"] += 1
        rows_read = 0
        unresolved = 0
        try:
            with hdfs.open_input_file(path) as stream:
                parquet = parquet_module.ParquetFile(stream)
                urls, fallback = select_columns(parquet.schema_arrow.names, path)
                columns = [*urls, *([fallback] if fallback else [])]
                if not columns:
                    status["unresolved_files"].append({"path": path, "reason": "no_known_url_column", "rows": parquet.metadata.num_rows})
                    status["unresolved_rows"] += parquet.metadata.num_rows
                    continue
                for batch in parquet.iter_batches(batch_size=batch_size, columns=columns, use_threads=False):
                    hashes = []
                    values = batch.to_pydict()
                    for index in range(batch.num_rows):
                        hashed = resolve_hash({name: values[name][index] for name in columns}, urls, fallback)
                        if hashed is None:
                            unresolved += 1
                        else:
                            # Per-file provenance lives in seed_files; repeating
                            # the long path in millions of B-tree rows needlessly
                            # increases the working set and checkpoint writes.
                            hashes.append((hashed, "hdfs"))
                    # SHA-256 keys arrive randomly. Sorted bulk insertion visits
                    # nearby B-tree pages together and avoids cache spill churn.
                    hashes.sort(key=lambda item: item[0])
                    before = outbox.db.total_changes
                    with outbox.db:
                        outbox.db.executemany("INSERT OR IGNORE INTO seen_urls(url_hash,origin) VALUES (?,?)", hashes)
                    status["inserted_unique"] += outbox.db.total_changes - before
                    rows_read += batch.num_rows
                    status["scanned_rows"] += batch.num_rows
                    status["current_file_rows"] = rows_read
                    report()
                if rows_read != parquet.metadata.num_rows:
                    raise ValueError("Parquet row count changed")
            if hdfs.get_file_info(path).size != info.size:
                raise ValueError("HDFS file changed while reading")
            status["unresolved_rows"] += unresolved
            if unresolved:
                status["unresolved_files"].append({"path": path, "reason": "missing_or_invalid_urls", "rows": unresolved})
            else:
                # Only a completely scanned, unambiguous file becomes skippable.
                # A crash before this transaction safely repeats INSERT OR IGNORE.
                with outbox.db:
                    outbox.db.execute("""INSERT INTO seed_files(path,size,rows,seeded_at) VALUES (?,?,?,?)
                        ON CONFLICT(path) DO UPDATE SET size=excluded.size,rows=excluded.rows,seeded_at=excluded.seeded_at""",
                        (path, info.size, rows_read, utcnow()))
                status["completed_files"] += 1
        except Exception as exc:
            status["errors"].append({"path": path, "stage": "read", "error": type(exc).__name__})
        report(True)
    if status["errors"] or status["unresolved_files"]:
        status["state"] = "partial"
    elif status["limited"]:
        status["state"] = "limited"
    elif not files:
        status["state"] = "empty"
    else:
        status["state"] = "complete"
    status.pop("current_file", None)
    status.pop("current_file_rows", None)
    status["finished_at"] = utcnow()
    # Final count also observes inserts from any other process using the outbox.
    baseline_unique = outbox.db.execute("SELECT count(*) FROM seen_urls").fetchone()[0] - status["inserted_unique"]
    report(True)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--hdfs-host", default=os.environ.get("HDFS_HOST", "default"))
    parser.add_argument("--hdfs-port", type=int, default=int(os.environ.get("HDFS_PORT", "0")))
    parser.add_argument("--hdfs-user", default=os.environ.get("HADOOP_USER_NAME", "ubuntu"))
    parser.add_argument("--root", action="append", dest="roots")
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--batch-size", type=int, default=100000)
    args = parser.parse_args(argv)
    import pyarrow.fs as fs
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    outbox = Outbox(state_dir / "outbox.db")
    # Millions of hash keys otherwise thrash SQLite's tiny default page cache.
    # Set a separate process memory limit with room for the JVM and page cache.
    # Leave headroom for libhdfs/JVM, Arrow's batches and the cgroup page cache.
    # Transaction durability remains synchronous=FULL with the existing WAL.
    outbox.db.execute("PRAGMA cache_size=-393216")
    try:
        hdfs = fs.HadoopFileSystem(args.hdfs_host, args.hdfs_port, user=args.hdfs_user)
        # libhdfs starts a JVM that can replace native signal handlers.
        def stop(signum, _frame):
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, stop)
        result = run_seed(outbox, hdfs, args.roots or DEFAULT_ROOTS,
                          state_dir / "seed-status.json", max_files=args.max_files,
                          batch_size=args.batch_size)
    finally:
        outbox.close()
    print(f"seed state={result['state']} files={result['scanned_files']} rows={result['scanned_rows']} unique={result['unique']} unresolved={result['unresolved_rows']} errors={len(result['errors'])}", flush=True)
    return 0 if result["state"] in {"complete", "limited"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
