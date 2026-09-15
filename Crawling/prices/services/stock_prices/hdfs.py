"""Validate and publish immutable, single-market daily-price HDFS snapshots.

No HDFS access occurs unless ``publish=True``. Published snapshots contain the
four original export files, a derived verification report and a success marker.
Consumers must require _SUCCESS, never scan .staging directories.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit
from uuid import uuid4

from .loader import validate_row
from .store import dumps, is_supported_quarantine
from .universe import MARKETS, completed_date


DEFAULT_ROOT = "/data-lake/raw/stock-prices/daily"
SOURCE_FILES = ("prices_daily.parquet", "manifest.json", "selection.json", "quarantine.jsonl")


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _reject_nonfinite(_value):
    raise ValueError("nonfinite_json")


def _read_json(path, maximum=8 * 1024 * 1024):
    if path.stat().st_size > maximum:
        raise ValueError("snapshot_control_file_too_large")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite)


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("invalid_snapshot_" + name)
    return value


def _instant(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("snapshot_timestamp_requires_timezone")
    return result


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("invalid_snapshot_date")
    return date.fromisoformat(value)


def validate_snapshot(source, *, market=None):
    """Validate all rows in bounded batches and return the publication metadata.

    An export must contain one market and all selected companies must have
    succeeded. ``as_of`` is the requested complete-day boundary; actual source
    availability is recorded separately in each company's first/last dates.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError("snapshot_source_must_be_directory")
    for name in SOURCE_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != source:
            raise ValueError("missing_or_external_snapshot_file")
    entries = {name: {"bytes": (source / name).stat().st_size, "sha256": _digest(source / name)}
               for name in SOURCE_FILES}
    manifest = _read_json(source / "manifest.json")
    selection = _read_json(source / "selection.json")
    if (manifest.get("schema_version") != 1 or manifest.get("status") != "complete"
            or manifest.get("interval_type") != "1D"):
        raise ValueError("only_complete_daily_snapshots_can_be_published")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", run_id):
        raise ValueError("invalid_snapshot_run_id")
    if (manifest.get("parquet") != "prices_daily.parquet" or manifest.get("quarantine") != "quarantine.jsonl"
            or manifest.get("sha256") != entries["prices_daily.parquet"]["sha256"]
            or manifest.get("quarantine_sha256") != entries["quarantine.jsonl"]["sha256"]
            or manifest.get("parquet_bytes") != entries["prices_daily.parquet"]["bytes"]):
        raise ValueError("snapshot_checksum_or_filename_mismatch")
    if not isinstance(selection, list) or not 1 <= len(selection) <= 100:
        raise ValueError("snapshot_requires_1_to_100_companies_in_one_market")
    keys = [(item["market"], item["ticker"]) for item in selection]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate_snapshot_selection")
    markets = {key[0] for key in keys}
    if len(markets) != 1 or not markets <= set(MARKETS):
        raise ValueError("snapshot_requires_single_market")
    selected_market = next(iter(markets))
    if market is not None and market != selected_market:
        raise ValueError("snapshot_market_mismatch")
    if (_integer(manifest.get("selected_companies"), "selected_companies", 1) != len(keys)
            or _integer(manifest.get("successful_companies"), "successful_companies", 1) != len(keys)):
        raise ValueError("snapshot_company_count_mismatch")
    outcomes = manifest.get("outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != len(keys):
        raise ValueError("snapshot_outcome_count_mismatch")
    expected, ends = {}, set()
    finished = _instant(manifest["completed_at"])
    for item in outcomes:
        key = (item["market"], item["ticker"])
        if key not in keys or key in expected or item.get("status") not in {"collected", "cached"}:
            raise ValueError("incomplete_or_duplicate_snapshot_outcome")
        start, end = _day(item["requested_start"]), _day(item["requested_end"])
        if start > end or end > completed_date(selected_market, finished):
            raise ValueError("snapshot_contains_incomplete_requested_day")
        _integer(item.get("rows"), "company_rows", 1)
        _integer(item.get("quarantined_rows"), "company_quarantined_rows")
        if _instant(item["source_checked_at"]) > finished:
            raise ValueError("source_checked_after_snapshot_completion")
        expected[key] = item
        ends.add(item["requested_end"])
    if len(ends) != 1:
        raise ValueError("snapshot_requires_common_market_as_of_date")

    # Export order is chronological within each company. Keeping its last date
    # rejects duplicates without retaining millions of daily natural keys.
    counts, first, last, providers, bases = Counter(), {}, {}, set(), set()
    parquet = pq.ParquetFile(source / "prices_daily.parquet")
    if parquet.metadata.num_rows != _integer(manifest.get("rows"), "rows", 1):
        raise ValueError("snapshot_parquet_row_count_mismatch")
    for batch in parquet.iter_batches(batch_size=8192):
        timestamps = batch.column("trading_at")
        dates = batch.column("trading_date")
        if (timestamps.type != pa.timestamp("us", tz="UTC") or timestamps.null_count or dates.null_count
                or not pc.all(pc.equal(timestamps, pc.cast(pc.strptime(dates, format="%Y-%m-%d", unit="us"),
                                                         pa.timestamp("us", tz="UTC")))).as_py()):
            raise ValueError("snapshot_trading_at_not_utc_midnight")
        # Arrow checks timestamps vectorially; omit this derived column from
        # Python conversion, especially expensive for long histories on Windows.
        for raw in batch.select([name for name in batch.schema.names if name != "trading_at"]).to_pylist():
            row = validate_row(raw)
            key = (row["market"], row["ticker"])
            if key not in expected:
                raise ValueError("unselected_company_in_snapshot")
            day = row["trading_date"]
            item = expected[key]
            if not item["requested_start"] <= day <= item["requested_end"]:
                raise ValueError("snapshot_row_outside_requested_range")
            if key in last and day <= last[key]:
                raise ValueError("snapshot_dates_not_strictly_increasing")
            first.setdefault(key, day)
            last[key] = day
            counts[key] += 1
            providers.add(row["provider"])
            bases.add(row["price_basis"])
    quarantined, previous = Counter(), {}
    with (source / "quarantine.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if len(line) > 1024 * 1024:
                raise ValueError("quarantine_record_too_large")
            row = json.loads(line, parse_constant=_reject_nonfinite)
            key = (row["market"], row["ticker"])
            day = _day(row["trading_date"]).isoformat()
            if key not in expected or not expected[key]["requested_start"] <= day <= expected[key]["requested_end"]:
                raise ValueError("quarantine_row_outside_selection")
            if key in previous and day <= previous[key]:
                raise ValueError("quarantine_dates_not_strictly_increasing")
            if not is_supported_quarantine(row):
                raise ValueError("invalid_quarantine_record")
            previous[key] = day
            quarantined[key] += 1
    if sum(quarantined.values()) != _integer(manifest.get("quarantined_rows"), "quarantined_rows"):
        raise ValueError("snapshot_quarantine_row_count_mismatch")
    for key, item in expected.items():
        if (counts[key] != item["rows"] or first.get(key) != item["first_date"]
                or last.get(key) != item["last_date"] or quarantined[key] != item["quarantined_rows"]):
            raise ValueError("snapshot_company_coverage_mismatch")
    # Recheck after parsing: the publisher never certifies a source being edited.
    if any(_digest(source / name) != entry["sha256"] for name, entry in entries.items()):
        raise ValueError("snapshot_changed_during_validation")
    return {"schema_version": 1, "dataset": "stock_prices_daily", "source": str(source),
            "snapshot_id": run_id, "market": selected_market, "as_of": next(iter(ends)),
            "as_of_meaning": "requested completed-day boundary; actual availability is first_date/last_date",
            "interval_type": "1D", "rows": sum(counts.values()), "companies": len(keys),
            "quarantined_rows": sum(quarantined.values()), "sha": entries["prices_daily.parquet"]["sha256"],
            "first_date": min(first.values()), "last_date": max(last.values()),
            "providers": sorted(providers), "price_bases": sorted(bases), "completed_at": manifest["completed_at"],
            "outcomes": outcomes, "versions": manifest.get("versions", {}), "files": entries}


class HdfsCli:
    """Hadoop CLI adapter; subprocess diagnostics never expose configuration."""
    def __init__(self, executable=None, timeout=600):
        self.executable = executable or os.environ.get("HDFS_BIN", "hdfs")
        self.timeout = timeout

    def _run(self, *arguments):
        try:
            result = subprocess.run([self.executable, "dfs", *arguments], capture_output=True,
                                    timeout=self.timeout, encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("hdfs_command_unavailable_or_timed_out") from None
        if result.returncode:
            raise RuntimeError("hdfs_command_failed_" + arguments[0].lstrip("-"))
        return result.stdout

    def exists(self, path):
        try:
            result = subprocess.run([self.executable, "dfs", "-test", "-e", path], capture_output=True,
                                    timeout=self.timeout, encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("hdfs_existence_check_unavailable") from None
        errors = "\n".join(line for line in result.stderr.splitlines()
                           if "Unable to load native-hadoop library" not in line)
        if result.returncode not in (0, 1) or (result.returncode and errors.strip()):
            raise RuntimeError("hdfs_existence_check_failed")
        return result.returncode == 0

    def mkdir(self, path, *, parents=False):
        self._run("-mkdir", *(["-p"] if parents else []), path)

    def put(self, source, destination):
        self._run("-put", str(source), destination)  # Deliberately no -f.

    def rename_new(self, source, destination):
        # Caller holds the destination's exclusive HDFS publication lock.
        if self.exists(destination):
            raise RuntimeError("hdfs_destination_already_exists")
        self._run("-mv", source, destination)

    def rmdir(self, path):
        self._run("-rmdir", path)  # Only our empty reservation; never snapshots.

    def sizes(self, root):
        found = {}
        prefix = urlsplit(root).path.rstrip("/") + "/"
        for line in self._run("-ls", "-R", root).splitlines():
            if not line.startswith("-"):
                continue
            fields = line.split(None, 7)
            if len(fields) != 8:
                raise RuntimeError("unexpected_hdfs_listing")
            path = urlsplit(fields[7]).path
            if not path.startswith(prefix):
                raise RuntimeError("hdfs_listing_outside_snapshot")
            found[path[len(prefix):]] = int(fields[4])
        return found

    def sha256(self, path):
        result = hashlib.sha256()
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen([self.executable, "dfs", "-cat", path],
                                           stdout=subprocess.PIPE, stderr=errors)
            except OSError:
                raise RuntimeError("hdfs_read_unavailable") from None
            timer = threading.Timer(self.timeout, process.kill)
            timer.daemon = True
            timer.start()
            try:
                for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
                    result.update(block)
                if process.wait():
                    raise RuntimeError("hdfs_read_failed_or_timed_out")
            finally:
                timer.cancel()
                process.stdout.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
        return result.hexdigest()


def _location(root, uri):
    if not isinstance(root, str) or not re.fullmatch(r"/data-lake/raw/stock-prices/daily(?:/[A-Za-z0-9._-]+)*", root):
        raise ValueError("hdfs_root_must_be_within_stock_prices_daily")
    if any(part in {".", ".."} for part in root.split("/")):
        raise ValueError("invalid_hdfs_root")
    if uri is None or uri == "":
        return root
    parsed = urlsplit(uri)
    if (parsed.scheme != "hdfs" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9.:-]+", parsed.netloc)):
        raise ValueError("hdfs_uri_must_be_hdfs_authority_without_credentials")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("invalid_hdfs_port")
    return uri.rstrip("/") + root


def _verify(hdfs, root, entries):
    if hdfs.sizes(root) != {name: item["bytes"] for name, item in entries.items()}:
        raise RuntimeError("hdfs_snapshot_inventory_mismatch")
    for name, item in entries.items():
        if hdfs.sha256(root + "/" + name) != item["sha256"]:
            raise RuntimeError("hdfs_snapshot_checksum_mismatch")


@contextmanager
def _reservation(hdfs, path):
    # Atomic mkdir without -p prevents two cooperating publishers from moving
    # different staging directories into the same destination. A hard crash
    # leaves this empty lock for an operator to inspect; no automatic stealing.
    hdfs.mkdir(path)
    try:
        yield
    finally:
        hdfs.rmdir(path)


def publish_snapshot(source, *, market=None, hdfs_root=DEFAULT_ROOT, hdfs_uri=None,
                     hdfs_bin=None, publish=False, hdfs=None):
    """Plan by default; explicitly publish one complete immutable snapshot.

    Failed staging directories are retained. Replaying a published run verifies
    every remote byte and returns already_verified; mismatches fail closed.
    HDFS_URI must explicitly identify the destination cluster when publishing.
    """
    if type(publish) is not bool:
        raise ValueError("publish_must_be_boolean")
    report = validate_snapshot(source, market=market)
    uri = hdfs_uri if hdfs_uri is not None else os.environ.get("HDFS_URI")
    root = _location(hdfs_root, uri)
    destination = f"{root}/market={report['market']}/as_of={report['as_of']}/snapshot={report['snapshot_id']}"
    report.update(destination=destination, manifest_path=destination + "/manifest.json")
    if not publish:
        return {"status": "dry_run", **report}
    if not uri:
        raise ValueError("set_HDFS_URI_for_explicit_publication")
    hdfs = hdfs or HdfsCli(hdfs_bin)
    # Local absolute paths are operational metadata, not part of the portable
    # HDFS contract; two copies of the same export have identical publications.
    publication = {key: value for key, value in report.items() if key != "source"}
    entries = dict(report["files"])
    with tempfile.TemporaryDirectory(prefix="stock-prices-publication-") as temporary:
        control = Path(temporary)
        (control / "publication.json").write_text(dumps(publication) + "\n", encoding="utf-8")
        success = {"schema_version": 1, "snapshot_id": report["snapshot_id"],
                   "publication_sha256": _digest(control / "publication.json")}
        (control / "_SUCCESS").write_text(dumps(success) + "\n", encoding="utf-8")
        for name in ("publication.json", "_SUCCESS"):
            entries[name] = {"bytes": (control / name).stat().st_size, "sha256": _digest(control / name)}
        parent = destination.rsplit("/", 1)[0]
        hdfs.mkdir(parent, parents=True)
        hdfs.mkdir(root + "/.locks", parents=True)
        lock = root + "/.locks/" + report["market"] + "-" + report["as_of"] + "-" + report["snapshot_id"]
        with _reservation(hdfs, lock):
            if hdfs.exists(destination):
                _verify(hdfs, destination, entries)
                return {"status": "already_verified", **report}
            hdfs.mkdir(root + "/.staging", parents=True)
            staging = root + "/.staging/" + report["snapshot_id"] + "-" + uuid4().hex
            hdfs.mkdir(staging)
            for name in SOURCE_FILES:
                hdfs.put(Path(report["source"]) / name, staging + "/" + name)
            hdfs.put(control / "publication.json", staging + "/publication.json")
            # The remote Parquet SHA256 is identical to the locally validated
            # bytes, including its footer row count and all decoded row values.
            _verify(hdfs, staging, {name: entry for name, entry in entries.items() if name != "_SUCCESS"})
            hdfs.put(control / "_SUCCESS", staging + "/_SUCCESS")
            _verify(hdfs, staging, entries)
            hdfs.rename_new(staging, destination)
            if hdfs.sizes(destination) != {name: item["bytes"] for name, item in entries.items()}:
                raise RuntimeError("hdfs_final_inventory_mismatch")
    return {"status": "published", **report}
