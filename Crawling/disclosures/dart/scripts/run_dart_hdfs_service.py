"""Resume the fixed DART collection on a server and archive its terminal snapshot.

Intended for one systemd service. Credentials never enter command arguments or logs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import zipfile

from collect_dart_rank_range import FileLock, KST, load_key, now, read_json, write_json

SHARDS = ((51, 68), (69, 78), (79, 100))
KNOWN_SOURCE_ERRORS = {"20200305000488", "20200305000062", "20190531001980"}
# 2026-09-15: a fresh document.xml download matched the stored ZIP byte-for-byte;
# _20200305000085.xml fails its CRC. Preserve it as a source exception, not a
# successfully collected document. Only the verified bytes qualify.
VERIFIED_SOURCE_ZIP_ERRORS = {
    "20200305000085": "ffb8173a09e5410c62f63130708b26ad82689f8c2805de2e186c7cc60b8a505d",
}
AUTH_STOPS = {"opendart_status_" + value for value in ("010", "011", "012", "901", "800")}
QUOTA_STOPS = {"local_daily_request_limit_reached", "opendart_status_020"}


def next_quota_retry(current):
    current = current.astimezone(KST)
    return (current + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)


def is_source_exception(receipt, state, raw):
    if state.get("status") not in {"error", "parse_error"}:
        return False
    if receipt in KNOWN_SOURCE_ERRORS:
        return True
    expected = VERIFIED_SOURCE_ZIP_ERRORS.get(receipt)
    return bool(expected and raw.is_file()
                and hashlib.sha256(raw.read_bytes()).hexdigest() == expected)


def assess_collection(active, begin="20160101", end="20260909"):
    selected, collected, unavailable, expected_errors = set(), set(), set(), set()
    company_files, reasons, lists_complete = 0, [], True
    for first, last in SHARDS:
        shard = Path(active) / f"shard-{first}-{last}"
        progress = read_json(shard / "progress.json", {})
        lists_complete = lists_complete and bool(progress.get("lists_complete"))
        reasons.append(progress.get("stop_reason"))
        csv_paths = sorted((shard / "companies").glob("*.csv"))
        if len(csv_paths) != last - first + 1:
            lists_complete = False
        for path in csv_paths:
            company_files += 1
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = {row["rcept_no"]: row for row in csv.DictReader(stream)}
            if selected.intersection(rows):
                raise ValueError("Receipt occurs in multiple company selections")
            if any(not begin <= row["rcept_dt"] <= end for row in rows.values()):
                raise ValueError("Receipt is outside the fixed collection period")
            selected.update(rows)
            details = path.with_name(path.stem + "_detail.jsonl")
            if details.exists():
                with details.open("rb") as stream:
                    for line in stream:
                        if not line.endswith(b"\n"):
                            break  # The collector repairs an interrupted last line on restart.
                        row = json.loads(line)
                        receipt = row["rcept_no"]
                        raw = shard / "raw/documents" / (receipt + ".zip")
                        if (receipt in rows and row.get("files") and row.get("rcept_dt") == rows[receipt]["rcept_dt"]
                                and raw.is_file() and zipfile.is_zipfile(raw)):
                            collected.add(receipt)
            for receipt in set(rows) - collected:
                state = read_json(shard / "state/documents" / (receipt + ".json"), {})
                if state.get("status") == "unavailable":
                    unavailable.add(receipt)
                elif is_source_exception(receipt, state, shard / "raw/documents" / (receipt + ".zip")):
                    expected_errors.add(receipt)
    pending = selected - collected - unavailable
    actionable = pending - expected_errors
    return {"selected": len(selected), "collected": len(collected), "unavailable": len(unavailable),
            "pending": len(pending), "actionable_pending": len(actionable),
            "source_exception_receipts": sorted(expected_errors), "company_csv_files": company_files,
            "lists_complete": lists_complete, "stop_reasons": reasons,
            "terminal": bool(selected) and lists_complete and company_files == 50 and not actionable}


class Service:
    def __init__(self, project, key_file, end, universe=None):
        self.project, self.end = Path(project).resolve(), end
        self.key_file = Path(key_file)
        if not self.key_file.is_absolute():
            self.key_file = self.project / self.key_file
        self.universe = Path(universe) if universe else self.project / "config/universe-20260907.jsonl"
        if not self.universe.is_absolute():
            self.universe = self.project / self.universe
        self.base = self.project / f"output/opendart-rank51-100-20160101-{end}"
        self.active = self.base / "parallel-3"
        self.state_path = self.base / "server-service.json"
        self.state = read_json(self.state_path, {})
        self.stopping = threading.Event()
        self.children = []
        self.env = os.environ.copy()
        self.env.pop("DART_API_KEY", None)
        self.env["PYTHONUTF8"] = "1"

    def save(self, status, **fields):
        self.state.update(status=status, updated_at=now(), **fields)
        write_json(self.state_path, self.state)
        print(json.dumps({"status": status, "updated_at": self.state["updated_at"]}, ensure_ascii=False), flush=True)

    def stop(self, *_):
        self.stopping.set()

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 20
        for child in self.children:
            if child.poll() is None:
                try:
                    child.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)

    def run_commands(self, commands, stage):
        if self.stopping.is_set():
            raise InterruptedError("Service stopping")
        directory = self.base / "server-logs"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
        handles = []
        self.children = []
        try:
            for name, command in commands:
                handle = (directory / f"{stamp}-{name}.log").open("ab", buffering=0)
                handles.append(handle)
                self.children.append(subprocess.Popen(command, cwd=self.project, env=self.env,
                                                       stdout=handle, stderr=subprocess.STDOUT))
            self.save(stage, child_pids=[child.pid for child in self.children])
            while any(child.poll() is None for child in self.children):
                if self.stopping.wait(2):
                    self.stop_children()
                    raise InterruptedError("Service stopping")
            return [child.returncode for child in self.children]
        finally:
            self.stop_children()
            self.children = []
            for handle in handles:
                handle.close()

    def wait_until(self, moment, status):
        self.save(status, retry_at=moment.isoformat(), child_pids=[])
        while not self.stopping.is_set():
            seconds = (moment - datetime.now(KST)).total_seconds()
            if seconds <= 0:
                return
            self.stopping.wait(min(seconds, 30))
        raise InterruptedError("Service stopping")

    def archive(self, assessment):
        identifier = self.state.get("snapshot_id")
        if not identifier:
            identifier = datetime.now(KST).strftime("%Y%m%d-%H%M%S") + "-kospi-rank51-100-final"
            self.save("preparing_archive", snapshot_id=identifier, assessment=assessment)
        snapshot = self.base / "hdfs-snapshots" / identifier
        # Preserve interrupted build artifacts, then construct a new complete directory.
        if snapshot.exists() and not (snapshot / "ready.json").exists():
            snapshot.rename(snapshot.with_name(snapshot.name + ".interrupted." + str(time.time_ns())))
        universe = self.universe
        if not (snapshot / "ready.json").exists():
            codes = self.run_commands([("prepare-snapshot", [sys.executable, "-u", "scripts/prepare_dart_hdfs_snapshot.py",
                "--source", str(self.active), "--output", str(snapshot), "--begin", "20160101", "--end", self.end,
                "--universe", str(universe), "--key-file", str(self.key_file)])], "building_archive")
            if codes != [0]:
                raise RuntimeError("Snapshot preparation failed; see server logs")
        manifest = read_json(snapshot / "manifest.json", {})
        if (manifest.get("snapshot_documents") != assessment["collected"]
                or manifest.get("selected_documents") != assessment["selected"] or manifest.get("rejected_rows") != 0):
            raise RuntimeError("Final snapshot counts differ from the completed collection")
        destination = "/datasets/opendart/snapshots/" + identifier
        report = self.base / (identifier + "-upload-report.json")
        codes = self.run_commands([("upload-snapshot", [sys.executable, "-u", "scripts/upload_dart_hdfs_snapshot.py",
            "--source", str(snapshot), "--destination", destination, "--report", str(report)])], "uploading_archive")
        if codes != [0]:
            raise RuntimeError("HDFS upload failed; staging and logs preserved")
        uploaded = read_json(report, {})
        if uploaded.get("status") not in {"uploaded_verified", "already_verified"}:
            raise RuntimeError("Missing successful HDFS verification report")
        result = "complete_with_source_exceptions" if assessment["source_exception_receipts"] else "complete"
        self.save(result, assessment=assessment, hdfs_destination=destination, upload_report=str(report), child_pids=[])

    def run(self):
        if self.state.get("status") in {"complete", "complete_with_source_exceptions", "needs_attention"}:
            return 0
        # Validate the key without printing it. Child processes read the same private file.
        load_key(self.key_file)
        failures = int(self.state.get("transient_failures", 0))
        if self.state.get("status") in {"waiting_for_quota", "waiting_for_retry"} and self.state.get("retry_at"):
            self.wait_until(datetime.fromisoformat(self.state["retry_at"]), self.state["status"])
        while not self.stopping.is_set():
            assessment = assess_collection(self.active, end=self.end)
            if assessment["terminal"]:
                self.archive(assessment)
                return 0
            commands = []
            for first, last in SHARDS:
                name = f"shard-{first}-{last}"
                commands.append((name, [sys.executable, "-u", "scripts/collect_dart_rank_range.py",
                    "--rank-from", str(first), "--rank-to", str(last), "--begin", "20160101", "--end", self.end,
                    "--output", str(self.active / name), "--key-file", str(self.key_file), "--universe", str(self.universe),
                    "--workers", "4", "--interval", "0.3", "--daily-limit", "19500"]))
            codes = self.run_commands(commands, "collecting")
            assessment = assess_collection(self.active, end=self.end)
            self.save("collection_checked", assessment=assessment, child_exit_codes=codes)
            if assessment["terminal"]:
                self.archive(assessment)
                return 0
            reasons = set(assessment["stop_reasons"])
            if reasons & AUTH_STOPS:
                self.save("needs_attention", reason="api_credentials_or_access", assessment=assessment)
                return 0
            if reasons & QUOTA_STOPS:
                failures = 0
                self.state["transient_failures"] = 0
                self.wait_until(next_quota_retry(datetime.now(KST)), "waiting_for_quota")
                continue
            failures += 1
            self.state["transient_failures"] = failures
            if failures > 3:
                self.save("needs_attention", reason="retries_exhausted", assessment=assessment)
                return 0
            self.wait_until(datetime.now(KST) + timedelta(minutes=5), "waiting_for_retry")
        raise InterruptedError("Service stopping")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--key-file", type=Path, default=Path(".env.dart.local"))
    parser.add_argument("--universe", type=Path, help="Fixed ranked company JSONL; defaults to bundled 2026-09-07 universe")
    parser.add_argument("--end", required=True, help="Fixed collection end date YYYYMMDD; changing it creates a separate output")
    args = parser.parse_args()
    try:
        datetime.strptime(args.end, "%Y%m%d")
        if len(args.end) != 8 or args.end < "20160101":
            raise ValueError("invalid end date")
    except ValueError:
        parser.error("--end must be YYYYMMDD and at least 20160101")
    service = Service(args.project, args.key_file, args.end, args.universe)
    signal.signal(signal.SIGTERM, service.stop)
    signal.signal(signal.SIGINT, service.stop)
    with FileLock(service.base / ".server-service.lock"):
        try:
            return service.run()
        except InterruptedError:
            service.save("interrupted", child_pids=[])
            return 1
        except Exception as error:
            # Fixed exception class avoids accidental network URLs or credential text.
            failures = int(service.state.get("service_failures", 0)) + 1
            terminal = failures > 3
            service.save("needs_attention" if terminal else "failed", error_type=type(error).__name__,
                         service_failures=failures, reason="service_retries_exhausted" if terminal else "service_error", child_pids=[])
            return 0 if terminal else 1


if __name__ == "__main__":
    raise SystemExit(main())
