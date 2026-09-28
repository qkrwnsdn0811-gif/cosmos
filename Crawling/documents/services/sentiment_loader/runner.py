"""Walk the enriched-sentiment HDFS tree and commit each batch once.

    python -m services.sentiment_loader.runner \
        --root hdfs://.../analyzed/news/historical-ai-sentiment/model_version=finbert-article-v1 \
        --state-file /var/lib/cosmos-sentiment-loader/state.json --commit

Each ``batch=NNNNN`` directory is one unit: it is read whole, expanded to
per-company rows, and applied in one transaction. Completed batch ids are kept
in the state file so an interrupted run resumes instead of restarting, and a
finished batch is never re-applied.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys

import pyarrow.parquet as pq

from ..hdfs_reader import discover_batches, open_reader
from .postgres import load_sentiment
from .weighting import company_rows

MODEL_VERSION_PREFIX = "model_version="
COLUMNS = ["url_hash", "ai_company_sentiment"]


class RunnerError(RuntimeError):
    pass


def model_version_of(root: str) -> str:
    for part in root.rstrip("/").split("/"):
        if part.startswith(MODEL_VERSION_PREFIX):
            return part[len(MODEL_VERSION_PREFIX):]
    raise RunnerError(f"root does not name a model_version partition: {root}")


def read_batch(reader, batch: str) -> list[dict]:
    blob = reader.read(f"{batch}/data.parquet")
    table = pq.read_table(io.BytesIO(blob), columns=COLUMNS)
    rows = []
    for record in table.to_pylist():
        rows.extend(company_rows(record))
    return rows


CONNECT_ATTEMPTS = 4
CONNECT_BACKOFF_SECONDS = 15


def connect_when_free(dsn: str, *, attempts: int = CONNECT_ATTEMPTS,
                      backoff: float = CONNECT_BACKOFF_SECONDS):
    """Connect, waiting out a busy role rather than losing the whole tick.

    The loader shares a connection-limited role with the document loader, which
    runs every two minutes; when the two overlap Postgres answers "too many
    connections for role" and the run dies. Measured 8 of 64 runs. Nothing is
    lost - the state file only records finished batches, so the next tick
    retries - but the batches sit unloaded for another ten minutes.

    A few seconds of waiting covers the overlap, because the other loader's
    connection is short-lived. This is a courtesy, not a fix: the role's limit
    is deliberately 1 and the real answer is a dedicated cosmos_sentiment_loader
    role, which is written up in deploy/news-enrich/README.md.
    """
    import time

    import psycopg

    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(dsn)
        except psycopg.OperationalError as error:
            if "too many connections" not in str(error) or attempt == attempts:
                raise
            print(json.dumps({"event": "waiting_for_connection", "attempt": attempt,
                              "sleep_seconds": backoff}, ensure_ascii=False), flush=True)
            time.sleep(backoff)


def read_state(path: Path) -> dict:
    if not path.exists():
        return {"completed": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state.get("completed"), list):
        raise RunnerError(f"state file is not a sentiment-loader state: {path}")
    return state


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def run(connection, *, root: str, state_path: Path, hdfs_bin: str,
        commit: bool, max_batches: int) -> dict:
    model_version = model_version_of(root)
    state = read_state(state_path)
    done = set(state["completed"])
    reader = open_reader(root, hdfs_bin)
    pending = [batch for batch in discover_batches(reader, root) if batch not in done]
    if max_batches:
        pending = pending[:max_batches]

    totals = {"batches": 0, "incoming": 0, "resolved": 0, "updated": 0,
              "unchanged": 0, "unmatched": 0, "missing_link": 0}
    for batch in pending:
        report = load_sentiment(connection, read_batch(reader, batch),
                                model_version=model_version, commit=commit)
        totals["batches"] += 1
        for key in ("incoming", "resolved", "updated", "unchanged", "unmatched", "missing_link"):
            totals[key] += report.get(key, 0)
        print(json.dumps({"batch": batch.rsplit("/", 1)[-1], **report}, ensure_ascii=False),
              flush=True)
        if commit:
            done.add(batch)
            write_state(state_path, {"completed": sorted(done)})
    return {**totals, "model_version": model_version, "pending_at_start": len(pending),
            "transport": reader.kind, "mode": "commit" if commit else "dry-run"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--dsn", default=os.environ.get("COSMOS_DSN"))
    parser.add_argument("--hdfs-bin", default=os.environ.get("HDFS_BIN", "/opt/hadoop/bin/hdfs"))
    parser.add_argument("--max-batches", type=int, default=0, help="0 loads every pending batch")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--dry-run", action="store_true",
                      help="Default: exercise all writes then roll them back")
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn or COSMOS_DSN is required")

    import psycopg
    with connect_when_free(args.dsn) as connection:
        connection.autocommit = True
        summary = run(connection, root=args.root, state_path=args.state_file,
                      hdfs_bin=args.hdfs_bin, commit=args.commit,
                      max_batches=args.max_batches)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
