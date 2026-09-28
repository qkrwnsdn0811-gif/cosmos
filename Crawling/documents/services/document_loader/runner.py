"""One bounded discovery/load pass for a systemd oneshot service.

Only committed PostgreSQL receipts permit fast skipping. The local state stores
attempt order and failures, never substitutes for a DB receipt. An immutable
batch is not reread on ordinary scheduled passes: use the single-input CLI for
content/configuration revalidation. Configuration and normalization-code changes
refuse an existing state file. After auditing earlier receipts with that CLI,
select a new state file to establish the new configuration baseline.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from .contract import normalize_batch
from .discovery import discover_all, marker_glob
from .postgres import load_documents
from .sources import load_source


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _json_object(path, label):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(label + " must contain a JSON object")
    return value


def load_config(path):
    path = Path(path).resolve(strict=True)
    value = _json_object(path, "runner config")
    if value.get("version", 1) != 1 or not isinstance(value.get("roots"), list) or not value["roots"]:
        raise ValueError("Runner config requires version 1 and nonempty roots")
    allowed = {"version", "roots", "company_map", "company_map_file", "sources", "sources_file", "register_sources"}
    if set(value) - allowed:
        raise ValueError("Unknown runner configuration fields")
    roots = []
    for root in value["roots"]:
        if not isinstance(root, dict) or set(root) != {"kind", "glob"}:
            raise ValueError("Each discovery root requires exactly kind and glob")
        roots.append({"kind": root["kind"], "glob": marker_glob(root["kind"], root["glob"])})
    config = {"version": 1, "roots": roots, "register_sources": value.get("register_sources", False)}
    if type(config["register_sources"]) is not bool:
        raise ValueError("register_sources must be boolean")
    for key, file_key in (("company_map", "company_map_file"), ("sources", "sources_file")):
        if key in value and file_key in value:
            raise ValueError("Choose inline configuration or a file for " + key)
        if file_key in value:
            supplied = value[file_key]
            if not isinstance(supplied, str) or not supplied:
                raise ValueError(file_key + " must be a path")
            config[key] = _json_object(path.parent / supplied, file_key)
        else:
            config[key] = value.get(key)
        if config[key] is not None and not isinstance(config[key], dict):
            raise ValueError(key + " must be a JSON object")
    return config


def configuration_digest(config):
    directory = Path(__file__).parent
    code = {}
    for name in ("contract.py", "sources.py", "postgres.py", "hdfs.py", "discovery.py"):
        code[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    data = {"config": config, "loader_code_sha256": code}
    return hashlib.sha256(_canonical(data)).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Runner state/status must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def read_state(path, digest):
    path = Path(path)
    if not path.exists():
        return {"version": 1, "configuration_sha256": digest, "cycle": 0, "attempts": {}, "prefer_retry": False,
                "root_cursor": 0}
    if path.is_symlink():
        raise ValueError("Runner state must not be a symlink")
    value = _json_object(path, "runner state")
    if (value.get("version") != 1 or not isinstance(value.get("attempts"), dict)
            or type(value.get("cycle")) is not int or value["cycle"] < 0
            or type(value.get("root_cursor", 0)) is not int or value.get("root_cursor", 0) < 0
            or type(value.get("prefer_retry", False)) is not bool):
        raise ValueError("Invalid runner state")
    for uri, entry in value["attempts"].items():
        if (not isinstance(uri, str) or not isinstance(entry, dict)
                or type(entry.get("last_attempt")) is not int or entry["last_attempt"] < 0
                or type(entry.get("failures")) is not int or entry["failures"] < 0):
            raise ValueError("Invalid runner attempt state")
    if value.get("configuration_sha256") != digest:
        raise ValueError("Runner configuration changed; revalidate existing receipts with the single-input CLI before selecting a new state file")
    return value


def committed_receipts(connection):
    from psycopg.rows import tuple_row
    if connection.info.transaction_status != 0:
        raise ValueError("Runner requires an idle database connection")
    with connection.transaction():
        with connection.cursor(row_factory=tuple_row) as cursor:
            cursor.execute("SELECT batch_id, source_uri FROM document_load_batch")
            rows = cursor.fetchall()
    receipts = {}
    for batch_id, uri in rows:
        if batch_id != hashlib.sha256(uri.encode()).hexdigest():
            raise ValueError("Stored document receipt ID differs from its input URI")
        receipts[batch_id] = uri
    return receipts


def _fair_new(candidates, attempts, cursor):
    """Round-robin kinds and news partitions; latest news offset goes first."""
    groups = {}
    for item in candidates:
        groups.setdefault(item.kind, []).append(item)
    def age(item):
        return attempts.get(item.input_uri, {}).get("last_attempt", 0)
    for kind, items in list(groups.items()):
        if kind not in {"news", "news-analyzed", "news-historical-analyzed"}:
            groups[kind] = deque(sorted(items, key=lambda item: (age(item), item.input_uri)))
            continue
        partitions = {}
        for item in items:
            match = re.search(r"/partition=(\d+)/start=(\d+)$", item.input_uri)
            partition = item.input_uri.rsplit("/start=", 1)[0] if match else item.input_uri
            partitions.setdefault(partition, []).append((item, int(match[2]) if match else -1))
        for key, values in list(partitions.items()):
            partitions[key] = deque(item for item, offset in sorted(values, key=lambda pair: (age(pair[0]), -pair[1], pair[0].input_uri)))
        keys = sorted(partitions)
        offset = cursor % len(keys)
        keys = keys[offset:] + keys[:offset]
        ordered = []
        while any(partitions.values()):
            for key in keys:
                if partitions[key]:
                    ordered.append(partitions[key].popleft())
        groups[kind] = deque(ordered)
    kinds = sorted(groups)
    if not kinds:
        return []
    offset = cursor % len(kinds)
    kinds = kinds[offset:] + kinds[:offset]
    result = []
    while any(groups.values()):
        for kind in kinds:
            if groups[kind]:
                result.append(groups[kind].popleft())
    return result


def select_pending(candidates, state, limit):
    if type(limit) is not int or limit < 1:
        raise ValueError("max_batches must be a positive integer")
    attempts = state["attempts"]
    failed = sorted((item for item in candidates if attempts.get(item.input_uri, {}).get("failures", 0)),
                    key=lambda item: (attempts[item.input_uri]["last_attempt"], item.input_uri))
    fresh = _fair_new([item for item in candidates if not attempts.get(item.input_uri, {}).get("failures", 0)],
                      attempts, state.get("root_cursor", 0))
    state["root_cursor"] = state.get("root_cursor", 0) + 1
    if failed and fresh and limit == 1:
        selected = failed[:1] if state.get("prefer_retry", False) else fresh[:1]
        state["prefer_retry"] = not state.get("prefer_retry", False)
        return selected
    retry_slots = min(len(failed), max(1, limit // 4)) if fresh and failed else min(limit, len(failed))
    selected = fresh[:limit - retry_slots] + failed[:retry_slots]
    if len(selected) < limit:
        seen = {item.input_uri for item in selected}
        selected += [item for item in fresh + failed if item.input_uri not in seen][:limit - len(selected)]
    return selected


def run_once(connection, config, *, state_path, commit=False, max_batches=25,
             hdfs_bin="/opt/hadoop/bin/hdfs", on_event=None):
    if type(commit) is not bool:
        raise ValueError("commit must be boolean")
    state = read_state(state_path, configuration_digest(config))
    receipts = committed_receipts(connection)
    candidates, discovery_errors = discover_all(config["roots"], hdfs_bin=hdfs_bin)
    pending, skipped = [], 0
    for item in candidates:
        if item.batch_id in receipts:
            if receipts[item.batch_id] != item.input_uri:
                raise ValueError("Receipt URI mismatch")
            skipped += 1
            state["attempts"].pop(item.input_uri, None)
        else:
            pending.append(item)
    selected = select_pending(pending, state, max_batches)
    state["cycle"] += 1
    result = {"mode": "commit" if commit else "dry_run", "configuration_sha256": state["configuration_sha256"],
              "discovered": len(candidates), "skipped_committed": skipped, "pending": len(pending),
              "attempted": 0, "succeeded": 0, "failed": 0, "deferred": len(pending) - len(selected),
              "discovery_errors": discovery_errors, "inputs": []}
    # Bind config before work; a crash never permits silently switching its skip policy.
    atomic_json(state_path, state)
    for item in selected:
        entry = state["attempts"].get(item.input_uri, {"last_attempt": 0, "failures": 0})
        event = {"kind": item.kind, "input_uri": item.input_uri, "batch_id": item.batch_id}
        result["attempted"] += 1
        try:
            batch = load_source(item.kind, item.input_uri, hdfs_bin=hdfs_bin)
            if batch["batch_id"] != item.batch_id or batch["input_uri"] != item.input_uri:
                raise ValueError("Loaded input identity differs from discovery")
            normalized = normalize_batch(batch, company_map=config.get("company_map"))
            sources = normalized["sources"] if config.get("sources") is None else config["sources"]
            loaded = load_documents(connection, normalized["records"], batch_id=batch["batch_id"],
                        manifest_sha256=batch["manifest_sha256"], source_uri=batch["input_uri"], sources=sources,
                        register_sources=config.get("register_sources", False), commit=commit)
            event.update(status="loaded" if commit else "dry_run", records=len(normalized["records"]), load=loaded)
            result["succeeded"] += 1
            entry["failures"] = 0
            if commit:
                state["attempts"].pop(item.input_uri, None)
        except Exception as error:
            entry["failures"] += 1
            event.update(status="failed", error_type=type(error).__name__, sqlstate=getattr(error, "sqlstate", None))
            if isinstance(error, ValueError):
                event["message"] = str(error)
            result["failed"] += 1
            # Loader normally rolls back itself. Recover an interrupted client
            # transaction before attempting the next independent input.
            try:
                if connection.info.transaction_status != 0:
                    connection.rollback()
            except Exception:
                pass
        entry["last_attempt"] = state["cycle"]
        if event["status"] == "failed" or not commit:
            state["attempts"][item.input_uri] = entry
        result["inputs"].append(event)
        atomic_json(state_path, state)
        if on_event:
            on_event(event)
    result["status"] = "partial_failure" if result["failed"] or discovery_errors else "complete"
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--max-batches", type=int, default=25)
    parser.add_argument("--hdfs-bin", default=os.environ.get("HDFS_BIN", "/opt/hadoop/bin/hdfs"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="Default: exercise all writes then roll them back")
    args = parser.parse_args(argv)
    try:
        if args.max_batches < 1:
            raise ValueError("max-batches must be positive")
        config = load_config(args.config)
        dsn = os.environ.get("DOCUMENT_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not dsn:
            raise ValueError("DOCUMENT_DATABASE_URL must be set outside the repository")
        import psycopg
        with psycopg.connect(dsn, connect_timeout=10) as connection:
            result = run_once(connection, config, state_path=args.state_file, commit=args.commit,
                              max_batches=args.max_batches, hdfs_bin=args.hdfs_bin,
                              on_event=lambda event: print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True))
        if args.status_file:
            atomic_json(args.status_file, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1 if result["status"] == "partial_failure" else 0
    except Exception as error:
        result = {"status": "error", "error_type": type(error).__name__,
                  "message": str(error) if isinstance(error, ValueError) else "Runner failed; committed receipts remain authoritative",
                  "sqlstate": getattr(error, "sqlstate", None)}
        if args.status_file:
            atomic_json(args.status_file, result)
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
