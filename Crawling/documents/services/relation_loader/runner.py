"""Walk enriched batches, extract relations, and add them to the live graph.

    python -m services.relation_loader.runner \
        --root hdfs://.../analyzed/news/company-sentiment/model_version=... \
        --state-file /var/lib/cosmos-relation-loader/state.json --commit

Runs once or twice a day rather than every ten minutes: "삼성전자가 SK하이닉스와
경쟁한다" does not change between passes, and each pass rewrites the scores of
every relation it touches.

Reads the same batches the sentiment loader reads. Those carry the article text,
which is all relation extraction needs - no model, no second pass over HDFS.
"""
from __future__ import annotations

import argparse
from collections import Counter
import io
import json
import os
from pathlib import Path
import sys

import pyarrow.parquet as pq

from ..hdfs_reader import discover_batches, open_reader
from .postgres import load_relations

COLUMNS = ["url_hash", "title", "content", "language", "companies"]


class RunnerError(RuntimeError):
    pass


def read_batch(reader, batch: str) -> list[dict]:
    from relations import article_relations

    blob = reader.read(f"{batch}/data.parquet")
    table = pq.read_table(io.BytesIO(blob), columns=COLUMNS)
    rows = []
    for record in table.to_pylist():
        for hit in article_relations(record.get("title") or "", record.get("content") or "",
                                     record.get("companies") or [],
                                     record.get("language") or "ko"):
            rows.append({**hit, "url_hash": record["url_hash"]})
    return rows


def read_state(path: Path) -> dict:
    if not path.exists():
        return {"completed": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state.get("completed"), list):
        raise RunnerError(f"state file is not a relation-loader state: {path}")
    return state


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def run(connection, *, root: str, state_path: Path, hdfs_bin: str,
        commit: bool, max_batches: int, drop_rejected: bool) -> dict:
    state = read_state(state_path)
    done = set(state["completed"])
    reader = open_reader(root, hdfs_bin)
    pending = [b for b in discover_batches(reader, root) if b not in done]
    if max_batches:
        pending = pending[:max_batches]

    totals = {"batches": 0, "incoming": 0, "resolved": 0, "relations_added": 0,
              "sentences_added": 0, "evidence_added": 0, "scores_added": 0, "unmatched": 0}
    rejected_total: Counter = Counter()
    for batch in pending:
        rows = read_batch(reader, batch)
        # relations.py labels every row it doubts with a reason - a roster of
        # three companies, "A·B 등", "A는 B와 달리". Those rows are not relations;
        # they are kept in the report so a pass says what it threw away.
        rejected = Counter(r.get("reject") for r in rows if r.get("reject"))
        if drop_rejected:
            rows = [r for r in rows if not r.get("reject")]
        report = load_relations(connection, rows, commit=commit)
        totals["batches"] += 1
        for key in totals:
            if key != "batches":
                totals[key] += report.get(key, 0)
        rejected_total.update(rejected)
        print(json.dumps({"batch": batch.rsplit("/", 1)[-1], **report,
                          "rejected": dict(rejected)}, ensure_ascii=False), flush=True)
        if commit:
            done.add(batch)
            write_state(state_path, {"completed": sorted(done)})
    return {**totals, "pending_at_start": len(pending), "rejected": dict(rejected_total),
            "transport": reader.kind, "mode": "commit" if commit else "dry-run"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--dsn", default=os.environ.get("COSMOS_DSN"))
    parser.add_argument("--hdfs-bin", default=os.environ.get("HDFS_BIN", "/opt/hadoop/bin/hdfs"))
    parser.add_argument("--max-batches", type=int, default=0, help="0 reads every pending batch")
    parser.add_argument("--keep-rejected", "--keep-enumerations", dest="keep_rejected",
                        action="store_true",
                        help="Load rows relations.py marked with a reject reason too - "
                             "rosters of 3+ companies, 'A·B 등', 'A는 B와 달리'")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--dry-run", action="store_true",
                      help="Default: exercise all writes then roll them back")
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn or COSMOS_DSN is required")

    from services.sentiment_loader.runner import connect_when_free

    with connect_when_free(args.dsn) as connection:
        connection.autocommit = True
        summary = run(connection, root=args.root, state_path=args.state_file,
                      hdfs_bin=args.hdfs_bin, commit=args.commit,
                      max_batches=args.max_batches,
                      drop_rejected=not args.keep_rejected)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
