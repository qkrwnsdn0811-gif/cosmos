"""One bounded OpenDART daily polling pass; publish only verified new HDFS receipts.

Run from a systemd timer. Historical collector directories are never changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from scripts import collect_dart_rank_range as core
from scripts.prepare_dart_hdfs_snapshot import prepare
from scripts.upload_dart_hdfs_snapshot import Hdfs, digest, upload, verify_hdfs


def date_value(value):
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("Dates must be YYYYMMDD")
    return datetime.strptime(value, "%Y%m%d").date()


def read_key_file(path):
    # This runner deliberately ignores inherited DART_API_KEY values.
    text = Path(path).read_text(encoding="utf-8-sig")
    match = re.search(r"^\s*(?:export\s+)?DART_API_KEY\s*=\s*(.*?)\s*$", text, re.M)
    key = match.group(1).strip().strip("\"'") if match else ""
    if not re.fullmatch(r"[A-Za-z0-9]{40}", key):
        raise ValueError("Key file must contain a valid DART_API_KEY")
    return key


def dates_to_check(start, today, days, overlap=3, maximum=5):
    start_date, today_date = date_value(start), date_value(today)
    recent = [(today_date - timedelta(days=n)).strftime("%Y%m%d") for n in range(overlap)]
    recent = [day for day in recent if day >= start]
    remaining = []
    cursor = start_date
    while cursor <= today_date:
        day = cursor.strftime("%Y%m%d")
        record = days.get(day)
        if day not in recent and (record is None or record.get("pending_count", 0) or record.get("status") == "retry"):
            remaining.append(day)
        cursor += timedelta(days=1)
    remaining.sort(key=lambda day: (days.get(day, {}).get("checked_at", ""), day))
    return (recent + remaining)[:maximum]


class Runner:
    def __init__(self, output, universe, start, ledger_dir, key_file, *, hdfs=None,
                 overlap=3, maximum_days=5, interval=0.5, daily_limit=19500,
                 request_budget=2500, api_factory=core.Api):
        self.output = Path(output).resolve()
        self.universe_path = Path(universe).resolve()
        self.universe = core.load_universe(self.universe_path, 1, 100)
        self.universe_hash = digest(self.universe_path)
        self.prefix = "daily-dart-v1-" + self.universe_hash[:12] + "-"
        self.start = start
        self.ledger_dir, self.key_file = Path(ledger_dir).resolve(), Path(key_file).resolve()
        self.hdfs = hdfs or Hdfs()
        self.overlap, self.maximum_days = overlap, maximum_days
        self.api_settings = dict(interval=interval, daily_limit=daily_limit,
                                 max_new_requests=request_budget, ledger_dir=self.ledger_dir)
        self.api_factory = api_factory
        self.state_path = self.output / "checkpoint.json"
        self.cache_path = self.output / "raw-cache.json"

    def save(self):
        self.state["updated_at"] = core.now()
        core.write_json(self.state_path, self.state)

    def recover_hdfs_index(self):
        """Recover receipt IDs from our verified delta snapshots if local state was lost."""
        root = "/datasets/opendart/snapshots"
        if not self.hdfs.exists(root):
            return
        for line in self.hdfs.run("-ls", root).splitlines():
            fields = line.split(None, 7)
            if len(fields) != 8 or not fields[0].startswith("d"):
                continue
            destination = fields[7]
            name = destination.rsplit("/", 1)[-1]
            if not name.startswith(self.prefix):
                continue
            if not re.fullmatch(re.escape(self.prefix) + r"\d{8}-[a-f0-9]{32}", name):
                raise ValueError("Unexpected daily snapshot name")
            ready = json.loads(self.hdfs.run("-cat", destination + "/ready.json"))
            manifests = {name: self.hdfs.run("-cat", destination + "/" + name).encode("utf-8")
                         for name in ("manifest.json", "checksums.json")}
            if (ready.get("status") != "ready" or ready.get("credential_text_check") != "passed"
                    or any(hashlib.sha256(raw).hexdigest() != ready.get(field) for field, raw in
                           (("manifest_sha256", manifests["manifest.json"]), ("checksums_sha256", manifests["checksums.json"])))):
                raise ValueError("Daily HDFS control files failed verification")
            manifest = json.loads(manifests["manifest.json"])
            checksums = json.loads(manifests["checksums.json"])
            if checksums.get("algorithm") != "sha256":
                raise ValueError("Unsupported daily snapshot checksum")
            entries = {e["path"]: e for e in checksums["files"]}
            for filename in ("checksums.json", "ready.json"):
                raw = self.hdfs.run("-cat", destination + "/" + filename).encode("utf-8")
                entries[filename] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            verify_hdfs(self.hdfs, destination, entries, workers=2)
            rows = [json.loads(line) for line in self.hdfs.run("-cat", destination + "/metadata/document_index.jsonl").splitlines()]
            if len(rows) != manifest["snapshot_documents"] or len({r["rcept_no"] for r in rows}) != len(rows):
                raise ValueError("Daily HDFS receipt count mismatch")
            for row in rows:
                self.state["published"][row["rcept_no"]] = row["rcept_dt"]
            self.state["snapshots"][destination] = len(rows)

    def pending_upload(self):
        pending = self.state.get("pending_upload")
        if not pending:
            return
        source = Path(pending["source"]).resolve()
        if self.output not in source.parents:
            raise ValueError("Pending snapshot must stay inside its output directory")
        report = upload(source, pending["destination"], self.hdfs, workers=2)
        if report["status"] not in {"uploaded_verified", "already_verified"}:
            raise RuntimeError("Daily snapshot upload was not verified")
        core.write_json(source.parent / "upload-report.json", report)
        for receipt in pending["receipts"]:
            self.state["published"][receipt] = pending["day"]
        self.state["snapshots"][pending["destination"]] = len(pending["receipts"])
        self.state["days"][pending["day"]] = pending["summary"]
        self.state["pending_upload"] = None
        self.save()  # ACK only after final HDFS byte verification; crash retries same destination.

    def collect_day(self, day, api, key):
        attempt = self.output / "attempts" / (day + "-" + uuid.uuid4().hex)
        shard = attempt / "input/shard-1-100"
        shard.mkdir(parents=True)
        api.output = shard  # Brand-new list cache: never reuse a completed same-day list.
        cache = core.read_json(self.cache_path, {})
        selected, collected, unavailable, errors = {}, {}, [], []
        listed = 0
        # Complete all 100 company lists before any day is marked checked/published.
        for company in self.universe:
            listing = core.collect_company_list(api, company, day, day, [], on_page=None)
            if not listing["complete"]:
                if str(listing.get("error", "")).startswith("network_error_"):
                    api.stop("daily_list_network_error")
                raise core.CollectorError(api.stop_reason or "daily_list_incomplete")
            rows = [row for row in listing["rows"] if not core.exclusion_code(row)]
            listed += len(rows)
            delta = [row for row in rows if row["rcept_no"] not in self.state["published"]]
            if delta:
                core.save_company_csv(shard, company, delta)
                for row in delta:
                    if row["rcept_no"] in selected and selected[row["rcept_no"]] != row:
                        raise ValueError("Conflicting company metadata for one receipt")
                    selected[row["rcept_no"]] = row
        for receipt, row in selected.items():
            prior = {}
            cached = cache.get(receipt)
            if cached:
                path = Path(cached["path"]).resolve()
                if self.output in path.parents and path.is_file() and digest(path) == cached["sha256"]:
                    prior[receipt] = path
            result = core.document_one(api, row, prior)
            if result["status"] == "collected":
                detail = result["detail"]
                company = {"universe_rank": row["universe_rank"], "stock_code": row["stock_code"], "corp_name": row["corp_name"]}
                target = shard / "companies" / (core.company_stem(company) + "_detail.jsonl")
                with target.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(detail, ensure_ascii=False) + "\n")
                raw = shard / "raw/documents" / (receipt + ".zip")
                cache[receipt] = {"path": str(raw), "sha256": digest(raw)}
                core.write_json(self.cache_path, cache)
                collected[receipt] = detail
            elif result["status"] == "unavailable":
                # A fresh attempt retries temporary 013/014; never add these to published.
                unavailable.append(receipt)
            else:
                errors.append(receipt)
                if str(result.get("error", "")).startswith("network_error_"):
                    api.stop("daily_document_network_error")
                if api.stop_reason:
                    break
        pending = sorted(set(selected) - set(collected))
        summary = {"checked_at": core.now(), "status": "checked", "listed": listed,
                   "new_documents": len(collected), "pending_count": len(pending),
                   "pending_receipts": pending, "source_unavailable_count": len(unavailable),
                   "source_error_count": len(errors)}
        core.write_json(attempt / "assessment.json", summary)
        if not collected:
            self.state["days"][day] = summary
            self.save()  # Empty scans have no fake HDFS data snapshot; recent days remain eligible.
            return
        snapshot = attempt / "snapshot"
        manifest = prepare(attempt / "input", snapshot, day, day, secrets=(key.encode(),),
                           universe=self.universe_path, rank_from=1, rank_to=100)
        if (manifest["snapshot_documents"] != len(collected) or manifest["selected_documents"] != len(selected)
                or manifest["rejected_rows"] or manifest["not_in_snapshot"] != len(pending)):
            raise ValueError("Daily package differs from collected receipts")
        destination = "/datasets/opendart/snapshots/" + self.prefix + day + "-" + uuid.uuid4().hex
        self.state["pending_upload"] = {"day": day, "source": str(snapshot), "destination": destination,
                                        "receipts": sorted(collected), "summary": summary}
        self.save()
        self.pending_upload()

    def run(self, today=None):
        today = today or datetime.now(core.KST).strftime("%Y%m%d")
        date_value(today)
        if date_value(self.start) > date_value(today):
            raise ValueError("Start date cannot be in the future")
        self.output.mkdir(parents=True, exist_ok=True)
        with core.FileLock(self.output / ".daily-run.lock"):
            key = read_key_file(self.key_file)
            previous = core.read_json(self.state_path)
            self.state = previous or {"version": 1, "universe_sha256": self.universe_hash,
                                      "start_date": self.start, "published": {}, "days": {}, "snapshots": {}}
            if self.state["universe_sha256"] != self.universe_hash or self.state["start_date"] != self.start:
                raise ValueError("Output belongs to another universe or start date")
            try:
                if previous is None:
                    self.recover_hdfs_index()
                    self.save()
                self.pending_upload()
                api = self.api_factory(key, self.output / "scratch", **self.api_settings)
                failures = []
                for day in dates_to_check(self.start, today, self.state["days"], self.overlap, self.maximum_days):
                    self.state.update(status="collecting", active_day=day)
                    self.save()
                    try:
                        self.collect_day(day, api, key)
                        if api.stop_reason:
                            failures.append({"day": day, "error_class": "RequestStopped"})
                            break
                    except Exception as error:
                        failures.append({"day": day, "error_class": type(error).__name__})
                        self.state["days"][day] = {"status": "retry", "checked_at": core.now()}
                        self.save()
                        if api.stop_reason or self.state.get("pending_upload"):
                            break
                self.state.update(status="retry" if failures else "idle", failures=failures,
                                  stop_reason=api.stop_reason or None, requests_this_pass=api.used,
                                  local_requests_today=api.daily_used, active_day=None)
                self.save()
                return 75 if failures else 0
            except Exception as error:
                self.state.update(status="retry", last_error_class=type(error).__name__)
                self.save()
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New dedicated daily output/state directory; keep it across releases")
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--ledger-dir", type=Path, required=True, help="EXACT same key quota ledger directory used by historical collection")
    parser.add_argument("--universe", type=Path, default=PROJECT / "config/universe-20260907.jsonl")
    parser.add_argument("--start-date", required=True, help="First date this daily pipeline owns, YYYYMMDD")
    parser.add_argument("--overlap-days", type=int, default=3)
    parser.add_argument("--max-days-per-run", type=int, default=5)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--daily-limit", type=int, default=19500)
    parser.add_argument("--request-budget", type=int, default=2500)
    parser.add_argument("--hdfs", default="/opt/hadoop/bin/hdfs")
    args = parser.parse_args()
    try:
        if (args.overlap_days < 1 or args.max_days_per_run < 1
                or args.max_days_per_run > 31 or args.interval < 0.3 or args.daily_limit < 1 or args.request_budget < 1):
            raise ValueError("Invalid polling bounds")
        date_value(args.start_date)
        runner = Runner(args.output, args.universe, args.start_date, args.ledger_dir, args.key_file,
                        hdfs=Hdfs(args.hdfs), overlap=args.overlap_days, maximum_days=args.max_days_per_run,
                        interval=args.interval, daily_limit=args.daily_limit, request_budget=args.request_budget)
        code = runner.run()
        print(json.dumps({"status": runner.state["status"], "published_receipts": len(runner.state["published"]),
                          "pending_receipts": sum(day.get("pending_count", 0) for day in runner.state["days"].values()),
                          "stop_reason": runner.state.get("stop_reason"), "requests_this_pass": runner.state.get("requests_this_pass")}), flush=True)
        return code
    except Exception as error:
        # Do not disclose URLs, private key values or arbitrary malformed input text.
        print(json.dumps({"status": "retry", "error_class": type(error).__name__}), flush=True)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
