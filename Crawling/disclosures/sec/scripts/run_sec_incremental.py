#!/usr/bin/env python3
"""One bounded SEC collection cycle using the preserved company universe."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import sqlite3
import subprocess
import sys
import time

from collect_sec_edgar_nasdaq100 import (
    DEFAULT_FORMS, CollectorError, HttpClient, SEC_SUBMISSIONS_URL,
    complete_submission_urls, download_complete_submission, load_company_filings,
    parse_forms, validate_user_agent, valid_iso_date,
)

ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}\Z")
HDFS_ROOT = "/data-lake/raw/realtime/sec-edgar"
FILES = ("raw.txt", "metadata.json", "_manifest.json", "_SUCCESS")


def now():
    return datetime.now(timezone.utc).isoformat()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def load_universe(path):
    values = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(values, list) or not values:
        raise ValueError("Existing companies.json must be a nonempty list")
    companies = []
    seen = set()
    for row in values:
        cik = str(int(row["cik"]))
        if not re.fullmatch(r"[1-9]\d{0,9}", cik) or cik in seen:
            raise ValueError("Company CIK is invalid or duplicated")
        seen.add(cik)
        companies.append({
            "cik": cik, "cik10": cik.zfill(10), "name": str(row.get("name") or row.get("sec_name") or ""),
            "symbols": row.get("nasdaq100Symbols", row.get("nasdaq_symbols", row.get("tickers", []))),
        })
    return sorted(companies, key=lambda row: int(row["cik"]))


def record_identity(record):
    cik, accession = str(record["cik"]), str(record["accessionNumber"])
    if not re.fullmatch(r"[1-9]\d{0,9}", cik) or not ACCESSION.fullmatch(accession):
        raise ValueError("Invalid SEC record identity")
    filing_date = date.fromisoformat(record["filingDate"]).isoformat()
    return {"cik": cik, "accession": accession, "filing_date": filing_date}


def validate_filing(body, accession):
    header = body[:131072]
    if (len(body) < 100 or b"<SEC-DOCUMENT>" not in header or b"<SEC-HEADER>" not in header
            or accession.encode("ascii") not in header):
        raise ValueError("SEC response is not the expected complete-submission document")


def validate_filings_block(block):
    if not isinstance(block, dict) or not isinstance(block.get("accessionNumber"), list):
        raise ValueError("SEC filing list shape is invalid")
    count = len(block["accessionNumber"])
    if any(not isinstance(block.get(field), list) or len(block[field]) != count for field in ("filingDate", "form")):
        raise ValueError("SEC filing list columns are incomplete")
    for accession, filing_date, form in zip(block["accessionNumber"], block["filingDate"], block["form"]):
        if not isinstance(accession, str) or not ACCESSION.fullmatch(accession) or not isinstance(form, str) or not form:
            raise ValueError("SEC filing identity or form is invalid")
        date.fromisoformat(filing_date)


class SubmissionPages:
    """Validate historical pages before the legacy selector can treat them as empty."""
    def __init__(self, client):
        self.client = client

    def get_json(self, url):
        payload = self.client.get_json(url)
        validate_filings_block(payload)
        return payload


class State:
    def __init__(self, root, contract):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "incremental.db", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS filings (
                cik TEXT NOT NULL, accession TEXT NOT NULL, filing_date TEXT NOT NULL,
                record TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                hdfs_path TEXT, failures INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                discovered_at TEXT NOT NULL, published_at TEXT,
                PRIMARY KEY(cik,accession)
            );
        """)
        saved = self.db.execute("SELECT value FROM config WHERE key='contract'").fetchone()
        encoded = json_bytes(contract).decode()
        if saved and saved["value"] != encoded:
            self.db.close()
            raise ValueError("State contract changed: preserve the original universe/start/forms/HDFS root")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO config VALUES ('contract',?)", (encoded,))

    def enqueue(self, record):
        identity = record_identity(record)
        with self.db:
            old = self.db.execute("SELECT filing_date FROM filings WHERE cik=? AND accession=?",
                                  (identity["cik"], identity["accession"])).fetchone()
            if old and old["filing_date"] != identity["filing_date"]:
                raise ValueError("Previously seen accession has a different filing date")
            result = self.db.execute("""INSERT OR IGNORE INTO filings
                (cik,accession,filing_date,record,discovered_at) VALUES (?,?,?,?,?)""",
                (identity["cik"], identity["accession"], identity["filing_date"], json_bytes(record).decode(), now()))
        return result.rowcount

    def pending(self, limit):
        return [json.loads(row["record"]) for row in self.db.execute(
            "SELECT record FROM filings WHERE status='pending' ORDER BY failures,filing_date,cik,accession LIMIT ?", (limit,))]

    def published(self, record, path):
        with self.db:
            self.db.execute("""UPDATE filings SET status='published',hdfs_path=?,last_error=NULL,published_at=?
                WHERE cik=? AND accession=?""", (path, now(), record["cik"], record["accessionNumber"]))

    def failed(self, record, error):
        with self.db:
            self.db.execute("""UPDATE filings SET failures=failures+1,last_error=?
                WHERE cik=? AND accession=?""", (type(error).__name__, record["cik"], record["accessionNumber"]))

    def counts(self):
        return dict(self.db.execute("SELECT status,count(*) FROM filings GROUP BY status").fetchall())

    def close(self):
        self.db.close()


class HdfsCli:
    def __init__(self, executable, timeout=300):
        self.executable, self.timeout = executable, timeout

    def call(self, *args, allowed=(0,)):
        process = subprocess.run([self.executable, "dfs", *args], capture_output=True, timeout=self.timeout)
        if process.returncode not in allowed:
            raise RuntimeError(f"HDFS command {args[0]} failed (exit {process.returncode})")
        return process

    def exists(self, path):
        process = self.call("-test", "-e", path, allowed=(0, 1))
        # A transport failure must not be interpreted as a nonexistent object.
        errors = [line for line in process.stderr.decode(errors="replace").splitlines() if line.strip()
                  and not ("NativeCodeLoader" in line and "Unable to load native-hadoop library" in line
                           and "using builtin-java classes" in line)]
        if process.returncode == 1 and errors:
            raise RuntimeError("HDFS existence check failed")
        return process.returncode == 0

    def mkdir(self, path):
        self.call("-mkdir", "-p", path)

    def put(self, local, target):
        self.call("-put", "-f", str(local), target)

    def read(self, path):
        return self.call("-cat", path).stdout

    def checksum(self, path):
        # Stream the raw filing rather than buffering a second copy in Python.
        process = subprocess.Popen([self.executable, "dfs", "-cat", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        size, hasher = 0, hashlib.sha256()
        try:
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                    raise TimeoutError("HDFS content verification timed out")
                chunk = os.read(process.stdout.fileno(), 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                hasher.update(chunk)
            if process.wait(timeout=max(0.1, deadline - time.monotonic())):
                raise RuntimeError("HDFS content verification failed")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
        return size, hasher.hexdigest()

    def count(self, path):
        fields = self.call("-count", path).stdout.decode().split()
        return int(fields[0]), int(fields[1]), int(fields[2])

    def rename(self, source, target):
        # No overwrite: an existing destination is reconciled separately.
        if self.exists(target):
            raise RuntimeError("HDFS final filing already exists before rename")
        self.call("-mv", source, target)


class Publisher:
    def __init__(self, store, root):
        if (not any(root == base or root.startswith(base + "/") for base in (HDFS_ROOT, "/datasets/sec-edgar/live"))
                or str(PurePosixPath(root)) != root or ".." in PurePosixPath(root).parts
                or not re.fullmatch(r"/[A-Za-z0-9_/=.-]+", root)):
            raise ValueError("HDFS root must be a dedicated SEC incremental path")
        self.store, self.root = store, root

    def target(self, record):
        item = record_identity(record)
        return f"{self.root}/filing_date={item['filing_date']}/cik={item['cik']}/accession={item['accession']}"

    def verify(self, path, record, expected=None):
        if not self.store.exists(path + "/_SUCCESS"):
            raise ValueError("HDFS filing is not complete")
        raw_manifest = self.store.read(path + "/_manifest.json")
        manifest = json.loads(raw_manifest)
        if (manifest.get("version") != 1 or manifest.get("identity") != record_identity(record)
                or set(manifest.get("files", {})) != {"raw.txt", "metadata.json"}):
            raise ValueError("HDFS filing manifest identity or file set mismatch")
        if expected is not None and manifest != expected:
            raise ValueError("HDFS staged manifest differs from local manifest")
        total = len(raw_manifest)
        for name, info in manifest["files"].items():
            size, sha = self.store.checksum(path + "/" + name)
            if size != info["bytes"] or sha != info["sha256"]:
                raise ValueError("HDFS filing content mismatch")
            total += size
        metadata = json.loads(self.store.read(path + "/metadata.json"))
        if record_identity(metadata) != record_identity(record):
            raise ValueError("HDFS metadata identity mismatch")
        if metadata.get("sourceUrl") not in complete_submission_urls(record["cik"], record["accessionNumber"]):
            raise ValueError("HDFS metadata SEC source URL mismatch")
        if self.store.checksum(path + "/_SUCCESS")[0] != 0 or self.store.count(path) != (1, 4, total):
            raise ValueError("HDFS filing contains unexpected files or marker content")
        return manifest

    def recover(self, record):
        target = self.target(record)
        if not self.store.exists(target):
            return None
        self.verify(target, record)
        return target

    def publish(self, record, local, manifest):
        recovered = self.recover(record)
        if recovered:
            return recovered
        target = self.target(record)
        staging = str(PurePosixPath(target).parent / ("." + PurePosixPath(target).name + ".inprogress"))
        self.store.mkdir(staging)
        for name in FILES:
            path = staging + "/" + name
            expected = (Path(local) / name).stat().st_size, file_digest(Path(local) / name)
            if self.store.exists(path):
                if self.store.checksum(path) != expected:
                    raise ValueError("Existing HDFS staging file differs; preserved for investigation")
            else:
                self.store.put(Path(local) / name, path)
        self.verify(staging, record, manifest)
        self.store.rename(staging, target)
        # Recheck the small final marker/manifest after atomic directory publication.
        if not self.store.exists(target + "/_SUCCESS") or json.loads(self.store.read(target + "/_manifest.json")) != manifest:
            raise ValueError("HDFS publication verification failed")
        return target


def prepare_local(root, record, client):
    identity = record_identity(record)
    local = Path(root) / "spool" / identity["cik"] / identity["accession"]
    manifest_path = local / "_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("identity") != identity or set(manifest.get("files", {})) != {"raw.txt", "metadata.json"}:
            raise ValueError("Local immutable filing manifest mismatch")
        for name, info in manifest["files"].items():
            path = local / name
            if path.stat().st_size != info["bytes"] or file_digest(path) != info["sha256"]:
                raise ValueError("Local filing is corrupt; refusing to bless a new checksum")
        # A crash after the manifest and before the marker is recoverable.
        write_atomic(local / "_SUCCESS", b"")
        return local, manifest
    body, source_url = download_complete_submission(client, record["cik"], record["accessionNumber"])
    validate_filing(body, identity["accession"])
    metadata = {**record, "sourceUrl": source_url, "collectedAt": now(), "bytes": len(body), "sha256": digest(body)}
    payloads = {"raw.txt": body, "metadata.json": json_bytes(metadata)}
    manifest = {"version": 1, "dataset": "sec-edgar-incremental", "identity": identity,
                "files": {name: {"bytes": len(data), "sha256": digest(data)} for name, data in payloads.items()}}
    for name, data in payloads.items():
        write_atomic(local / name, data)
    write_atomic(manifest_path, json_bytes(manifest))
    write_atomic(local / "_SUCCESS", b"")
    return local, manifest


def cleanup_local(root, record):
    identity = record_identity(record)
    local = Path(root).resolve() / "spool" / identity["cik"] / identity["accession"]
    if not local.exists():
        return
    if local.is_symlink() or not local.resolve().is_relative_to(Path(root).resolve() / "spool"):
        raise ValueError("Unexpected local spool location")
    for name in FILES:
        (local / name).unlink(missing_ok=True)
    local.rmdir()  # Only known files are removed; unknown contents make cleanup fail.


def run_cycle(state, companies, client, publisher, *, start, end, forms, limit=200):
    result = {"started_at": now(), "state": "running", "start_date": start, "end_date": end,
              "companies_expected": len(companies), "companies_checked": 0, "discovered_new": 0,
              "published_this_run": 0, "recovered_this_run": 0, "discovery_errors": [], "filing_errors": []}
    try:
        for company in companies:
            try:
                submission = client.get_json(SEC_SUBMISSIONS_URL.format(cik10=company["cik10"]))
                if int(submission.get("cik", 0)) != int(company["cik"]):
                    raise ValueError("SEC submissions CIK mismatch")
                validate_filings_block(submission.get("filings", {}).get("recent"))
                rows = load_company_filings(SubmissionPages(client), submission, forms, start, end, None)
                for row in rows:
                    record = {**row, "cik": company["cik"], "company": company["name"], "symbols": company["symbols"]}
                    result["discovered_new"] += state.enqueue(record)
                result["companies_checked"] += 1
            except Exception as error:
                result["discovery_errors"].append({"cik": company["cik"], "error_type": type(error).__name__})
                if isinstance(error, CollectorError) and "403" in str(error):
                    result["state"] = "blocked_source_access"
                    return result
        for record in state.pending(limit):
            try:
                target = publisher.recover(record)
                if target:
                    result["recovered_this_run"] += 1
                else:
                    local, manifest = prepare_local(state.root, record, client)
                    target = publisher.publish(record, local, manifest)
                    result["published_this_run"] += 1
                state.published(record, target)
                cleanup_local(state.root, record)
            except Exception as error:
                state.failed(record, error)
                result["filing_errors"].append({**record_identity(record), "error_type": type(error).__name__})
                if isinstance(error, CollectorError) and "403" in str(error):
                    result["state"] = "blocked_source_access"
                    return result
        result["state"] = "needs_attention" if result["discovery_errors"] or result["filing_errors"] else "complete"
        return result
    finally:
        result["counts"] = state.counts()
        if result["state"] == "complete" and result["counts"].get("pending", 0):
            result["state"] = "pending_backlog"
        result["finished_at"] = now()
        write_atomic(state.root / "status.json", json_bytes(result))


@contextmanager
def locked(path):
    import fcntl
    with Path(path).open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True, help="Preserved companies.json from the original SEC batch")
    parser.add_argument("--user-agent-file", type=Path)
    parser.add_argument("--start-date", type=valid_iso_date, default="2026-09-09")
    parser.add_argument("--end-date", type=valid_iso_date, default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--forms", default=",".join(DEFAULT_FORMS))
    parser.add_argument("--max-filings", type=int, default=200)
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--hdfs-root", default=HDFS_ROOT)
    parser.add_argument("--hdfs-bin", default=os.environ.get("HDFS_BIN", "/opt/hadoop/bin/hdfs"))
    args = parser.parse_args()
    if args.start_date > args.end_date or args.max_filings < 1 or args.request_delay < 0.1:
        parser.error("Invalid date range, max-filings, or request delay")
    state = None
    try:
        user_agent = (args.user_agent_file.read_text().strip() if args.user_agent_file
                      else os.environ.get("SEC_EDGAR_USER_AGENT", "").strip())
        user_agent = validate_user_agent(user_agent)
        companies = load_universe(args.universe)
        forms = parse_forms(args.forms)
        contract = {"version": 1, "universe_sha256": digest(json_bytes(companies)), "start_date": args.start_date,
                    "forms": sorted(forms) if forms is not None else "all", "hdfs_root": args.hdfs_root}
        args.state_dir.mkdir(parents=True, exist_ok=True)
        publisher = Publisher(HdfsCli(args.hdfs_bin), args.hdfs_root)
        with locked(args.state_dir / "incremental.lock"):
            state = State(args.state_dir, contract)
            result = run_cycle(state, companies, HttpClient(user_agent, request_delay=args.request_delay), publisher,
                               start=args.start_date, end=args.end_date, forms=forms, limit=args.max_filings)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["state"] in {"complete", "pending_backlog"} else 1
    except Exception as error:
        # The contact file and authenticated environment never appear in logs.
        print(json.dumps({"state": "failed", "error_type": type(error).__name__}), file=sys.stderr)
        return 1
    finally:
        if state is not None:
            state.close()


if __name__ == "__main__":
    sys.exit(main())
