"""Prepare a new local DART snapshot excluding a verified HDFS receipt baseline.

Read-only for historical collection and baseline inputs. Only the dedicated
filtered source, new snapshot and external audit report are written. No network.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile
import zlib

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from scripts.prepare_dart_hdfs_snapshot import prepare, check_secret

RECEIPT = re.compile(r"\d{14}")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def signature(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def receipt_id(value):
    if not isinstance(value, str) or not RECEIPT.fullmatch(value):
        raise ValueError("Invalid receipt identifier")
    return value


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_baseline(path, expected_sha256=None, expected_count=None):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Baseline index must be a regular file")
    before = signature(path)
    receipts = set()
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for line in stream:
            hasher.update(line)
            if not line.strip():
                continue
            if not line.endswith(b"\n"):
                raise ValueError("Baseline index has an incomplete final line")
            row = json.loads(line)
            receipt = receipt_id(row.get("rcept_no", row.get("receipt")))
            if "receipt" in row and row["receipt"] != receipt:
                raise ValueError("Baseline receipt fields disagree")
            if receipt in receipts:
                raise ValueError("Duplicate baseline receipt")
            receipts.add(receipt)
    actual_hash = hasher.hexdigest()
    if signature(path) != before:
        raise ValueError("Baseline changed while being read")
    if expected_sha256 is not None and actual_hash != expected_sha256.lower():
        raise ValueError("Baseline SHA256 differs from the verified value")
    if expected_count is not None and len(receipts) != expected_count:
        raise ValueError("Baseline count differs from the verified value")
    return receipts, {"path": str(path), "bytes": before[0], "sha256": actual_hash,
                      "unique_receipts": len(receipts), "mtime_ns": before[1]}


def validate_zip(path, detail):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 5000 or sum(item.file_size for item in entries) > 300 * 1024**2:
            raise ValueError("zip_expanded_size_limit")
        if any(item.flag_bits & 1 for item in entries):
            raise ValueError("encrypted_zip")
        names = {item.filename for item in entries if not item.is_dir()}
        if any(item["filename"] not in names for item in detail["files"]):
            raise ValueError("detail_attachment_missing_from_zip")
        if archive.testzip() is not None:
            raise ValueError("zip_crc_error")


def copy_raw(source, destination, detail):
    if source.is_symlink() or not source.is_file():
        raise ValueError("raw_zip_missing_or_symlink")
    before = signature(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    with source.open("rb") as src, destination.open("xb") as target:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            target.write(block)
            hasher.update(block)
    if signature(source) != before or destination.stat().st_size != before[0]:
        raise ValueError("raw_zip_changed_during_copy")
    validate_zip(destination, detail)
    return {"bytes": before[0], "sha256": hasher.hexdigest()}


def valid_detail(row):
    files = row.get("files")
    return (isinstance(files, list) and bool(files)
            and all(isinstance(item, dict) and isinstance(item.get("filename"), str)
                    and bool(item["filename"]) and isinstance(item.get("text"), str) for item in files)
            and any(item["text"].strip() for item in files))


def prepare_delta(source, baseline_index, output, report_path, *, begin="20160101", end="20260909",
                  filtered_source=None, expected_baseline_sha256=None, expected_baseline_count=None,
                  universe=None, secrets=(), bundle_bytes=256 * 1024**2):
    source, baseline_index, output, report_path = map(lambda p: Path(p).resolve(),
                                                    (source, baseline_index, output, report_path))
    filtered = Path(filtered_source).resolve() if filtered_source else output.with_name(output.name + ".source")
    if not source.is_dir():
        raise ValueError("Source collection does not exist")
    for path in (output, filtered, report_path):
        if path == source or source in path.parents or path in source.parents or path == baseline_index:
            raise ValueError("Output must not modify historical or baseline inputs")
        if path.exists():
            raise ValueError("Output, filtered source and report must all be new paths")
    if (output == filtered or output in filtered.parents or filtered in output.parents
            or report_path == output or output in report_path.parents
            or report_path == filtered or filtered in report_path.parents):
        raise ValueError("Snapshot, filtered source and external report must be separate")
    if not re.fullmatch(r"\d{8}", begin) or not re.fullmatch(r"\d{8}", end) or begin > end:
        raise ValueError("Invalid date range")
    datetime.strptime(begin, "%Y%m%d"); datetime.strptime(end, "%Y%m%d")
    baseline, baseline_info = load_baseline(baseline_index, expected_baseline_sha256, expected_baseline_count)
    report = {"version": 1, "status": "scanning", "started_at": timestamp(),
              "source": str(source), "filtered_source": str(filtered), "snapshot": str(output),
              "begin": begin, "end": end, "baseline": baseline_info, "counts": {},
              "excluded": [], "source_files": [], "notes": [
                  "Only complete captured detail lines are read; later appends are outside this snapshot.",
                  "Baseline receipt identifiers are excluded before raw ZIP copying or CRC checks.",
                  "Existing historical collection and baseline files are never modified."]}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(report_path, report)
    filtered.mkdir(parents=True)
    selected, csv_files = {}, []
    counts = Counter()
    seen_detail, valid_receipts, included, raw_hashes = {}, set(), set(), {}
    try:
        for path in sorted(source.glob("shard-*/companies/*.csv")):
            if path.is_symlink():
                raise ValueError("Source CSV symlinks are not accepted")
            before = signature(path)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                names = reader.fieldnames
                rows = list(reader)
            if signature(path) != before:
                raise ValueError("Selected CSV changed during capture")
            relative = path.relative_to(source)
            csv_files.append((relative, names, rows))
            for row in rows:
                receipt = receipt_id(row.get("rcept_no"))
                if not begin <= row.get("rcept_dt", "") <= end:
                    continue
                if receipt in selected:
                    raise ValueError("Duplicate selected CSV receipt")
                selected[receipt] = row
        captures = [(path, path.stat().st_size) for path in sorted(source.glob("shard-*/companies/*_detail.jsonl"))]
        if not csv_files or not captures:
            raise ValueError("No selected CSV or collected detail files")
        for path, cutoff in captures:
            if path.is_symlink():
                raise ValueError("Detail symlinks are not accepted")
            relative = path.relative_to(source)
            output_detail = filtered / relative
            info = {"path": relative.as_posix(), "captured_bytes": cutoff, "complete_lines": 0,
                    "excluded_tail_bytes": 0, "exported_rows": 0}
            report["source_files"].append(info)
            with ExitStack() as stack:
                stream = stack.enter_context(path.open("rb"))
                detail_target = None
                remaining = cutoff
                while remaining:
                    line = stream.readline(remaining)
                    if not line:
                        raise ValueError("Detail source shrank during capture")
                    remaining -= len(line)
                    if not line.endswith(b"\n"):
                        info["excluded_tail_bytes"] = len(line)
                        counts["incomplete_tail_files"] += 1
                        break
                    info["complete_lines"] += 1
                    counts["detail_rows_read"] += 1
                    try:
                        row = json.loads(line)
                        receipt = receipt_id(row.get("rcept_no"))
                    except (ValueError, AttributeError, UnicodeError):
                        report["excluded"].append({"source": relative.as_posix(), "line": info["complete_lines"],
                                                   "reason": "invalid_detail_json_or_receipt"})
                        counts["invalid_detail_rows"] += 1
                        continue
                    line_hash = hashlib.sha256(line).digest()
                    if receipt in seen_detail:
                        if seen_detail[receipt] != line_hash:
                            raise ValueError("Conflicting duplicate detail receipt")
                        counts["identical_duplicate_detail_rows"] += 1
                        continue
                    seen_detail[receipt] = line_hash
                    expected = selected.get(receipt)
                    reason = None
                    if expected is None:
                        reason = "outside_selected_receipts"
                    elif any(str(row.get(k, "")) != str(expected.get(k, "")) for k in ("corp_code", "rcept_dt", "report_nm")):
                        reason = "detail_metadata_mismatch"
                    elif not valid_detail(row):
                        reason = "missing_or_empty_collected_detail"
                    if reason:
                        counts[reason] += 1
                        report["excluded"].append({"receipt": receipt, "reason": reason})
                        continue
                    valid_receipts.add(receipt)
                    if receipt in baseline:
                        counts["baseline_receipts_skipped"] += 1
                        continue
                    counts["candidate_delta_receipts"] += 1
                    raw = path.parent.parent / "raw/documents" / (receipt + ".zip")
                    raw_relative = raw.relative_to(source)
                    copied = filtered / raw_relative
                    try:
                        raw_info = copy_raw(raw, copied, row)
                    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as error:
                        counts["raw_zip_exclusions"] += 1
                        # This file was created by this attempt, never the original.
                        if copied.is_file() and not copied.is_symlink():
                            copied.unlink()
                        report["excluded"].append({"receipt": receipt, "reason": "raw_zip_unavailable_or_invalid",
                                                   "error_class": type(error).__name__})
                        continue
                    check_secret(line, secrets)
                    if detail_target is None:
                        output_detail.parent.mkdir(parents=True, exist_ok=True)
                        detail_target = stack.enter_context(output_detail.open("xb"))
                    detail_target.write(line)
                    included.add(receipt)
                    raw_hashes[receipt] = raw_info
                    info["exported_rows"] += 1
                    if len(included) % 250 == 0:
                        report.update(updated_at=timestamp(), counts={**dict(counts), "included_delta_receipts": len(included)})
                        write_report(report_path, report)
            report.update(updated_at=timestamp(), counts={**dict(counts), "included_delta_receipts": len(included)})
            write_report(report_path, report)
        for relative, names, rows in csv_files:
            kept = [row for row in rows if row["rcept_no"] in included]
            if kept:
                target = filtered / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=names)
                    writer.writeheader(); writer.writerows(kept)
        report["selected_without_valid_detail"] = sorted(set(selected) - valid_receipts)
        report["baseline_absent_from_valid_source"] = sorted(baseline - valid_receipts)
        report["included_receipts"] = sorted(included)
        report["counts"] = {**dict(counts), "selected_csv_receipts": len(selected),
            "valid_collected_receipts": len(valid_receipts), "included_delta_receipts": len(included),
            "selected_without_valid_detail": len(set(selected) - valid_receipts),
            "baseline_absent_from_valid_source": len(baseline - valid_receipts)}
        if included & baseline or included != valid_receipts - baseline - {
                item["receipt"] for item in report["excluded"] if item.get("reason") == "raw_zip_unavailable_or_invalid"}:
            raise ValueError("Recovery delta set arithmetic failed")
        issue_count = sum(counts[name] for name in ("raw_zip_exclusions", "invalid_detail_rows",
                         "detail_metadata_mismatch", "missing_or_empty_collected_detail"))
        if not included:
            report.update(status="empty_with_exclusions" if issue_count else "empty", finished_at=timestamp())
            write_report(report_path, report)
            return report
        report["status"] = "preparing_snapshot"
        write_report(report_path, report)
        manifest = prepare(filtered, output, begin, end, bundle_bytes, secrets, universe=universe)
        if (manifest["snapshot_documents"] != len(included) or manifest["selected_documents"] != len(included)
                or manifest["not_in_snapshot"] or manifest["rejected_rows"]):
            raise ValueError("Prepared delta snapshot counts disagree")
        actual = {}
        with (output / "metadata/document_index.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                receipt = receipt_id(row["rcept_no"])
                if receipt in actual or row["raw_sha256"] != raw_hashes[receipt]["sha256"]:
                    raise ValueError("Prepared raw ZIP identity or hash disagrees")
                actual[receipt] = row
        if set(actual) != included:
            raise ValueError("Prepared receipt set differs from the delta")
        if sha256(baseline_index) != baseline_info["sha256"]:
            raise ValueError("Baseline changed during snapshot preparation")
        report.update(status="ready_with_exclusions" if issue_count else "ready",
                      finished_at=timestamp(), snapshot_documents=manifest["snapshot_documents"],
                      ready_sha256=sha256(output / "ready.json"), manifest_sha256=sha256(output / "manifest.json"))
        write_report(report_path, report)
        return report
    except Exception as error:
        report.update(status="failed", finished_at=timestamp(), error_class=type(error).__name__)
        write_report(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline-index", type=Path, required=True)
    parser.add_argument("--baseline-sha256")
    parser.add_argument("--baseline-count", type=int)
    parser.add_argument("--output", type=Path, required=True, help="New prepared snapshot directory")
    parser.add_argument("--filtered-source", type=Path, help="New separate filtered source; defaults to OUTPUT.source")
    parser.add_argument("--report", type=Path, required=True, help="New report outside source and snapshot directories")
    parser.add_argument("--begin", default="20160101")
    parser.add_argument("--end", required=True)
    parser.add_argument("--universe", type=Path, default=PROJECT / "config/universe-20260907.jsonl")
    parser.add_argument("--key-file", type=Path, action="append", default=[])
    parser.add_argument("--bundle-mib", type=int, default=256)
    args = parser.parse_args()
    try:
        if args.bundle_mib < 1:
            raise ValueError("Bundle size must be positive")
        secrets = set()
        for path in args.key_file:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                match = re.match(r"\s*(?:export\s+)?DART_API_KEY\s*=\s*(.*?)\s*$", line)
                if match:
                    value = match.group(1).strip().strip("\"'")
                    if value:
                        secrets.add(value.encode())
        result = prepare_delta(args.source, args.baseline_index, args.output, args.report,
            begin=args.begin, end=args.end, filtered_source=args.filtered_source,
            expected_baseline_sha256=args.baseline_sha256, expected_baseline_count=args.baseline_count,
            universe=args.universe if args.universe.exists() else None,
            secrets=tuple(secrets), bundle_bytes=args.bundle_mib * 1024**2)
        print(json.dumps({"status": result["status"], "counts": result["counts"], "baseline": result["baseline"]}))
        return 0 if result["status"] in {"ready", "empty"} else 2
    except Exception as error:
        print(json.dumps({"status": "failed", "error_class": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
