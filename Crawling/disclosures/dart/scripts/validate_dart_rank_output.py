#!/usr/bin/env python3
"""Read-only, offline QA for a rank-range OpenDART collection.

Usage: python scripts/validate_dart_rank_output.py OUTPUT_DIRECTORY
The only output is a JSON summary on stdout; no validation files are written.
Files may still be growing: an unfinished final JSONL line is a warning.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import sys
import unicodedata
import zipfile
import zlib

PROJECT = Path(__file__).resolve().parents[1]
EXCLUDED_TITLES = {
    "임원주요주주특정증권등소유상황보고서",
    "임원주요주주특정증권등거래계획보고서",
}
MAX_ISSUE_EXAMPLES = 50


def load_secret(path: Path) -> str | None:
    """Read just this run's credential; never return its value in diagnostics."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None
    match = re.search(r"^\s*DART_API_KEY\s*=\s*(.*?)\s*$", content, re.M)
    if not match:
        return None
    candidate = match.group(1).strip().strip("\"'")
    return candidate if re.fullmatch(r"[A-Za-z0-9]{40}", candidate) else None


def excluded(row: dict) -> bool:
    for field in ("pblntf_detail_ty", "disclosure_type", "report_type_code"):
        if row.get(field) in ("D002", "D005"):
            return True
    title = row.get("report_nm", "")
    if not isinstance(title, str):
        return False
    # U+318D is a Korean letter, so isalnum() retains it. NFKC also turns
    # it into U+119E; remove both forms before normalization.
    title = unicodedata.normalize("NFKC", re.sub(r"[ㆍᆞ··∙•⋅]", "", title))
    title = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    return "".join(char for char in title if char.isalnum()) in EXCLUDED_TITLES


class Audit:
    def __init__(self, root: Path, secrets: list[str]):
        self.root = root
        self.secrets = secrets
        self.counts = Counter()
        self.issue_counts = Counter()
        self.issues: list[dict] = []
        self.list_receipts: set[str] = set()
        self.detail_receipts: set[str] = set()

    def issue(self, severity: str, code: str, path: Path | None = None,
              line: int | None = None):
        self.counts[severity + "s"] += 1
        self.issue_counts[code] += 1
        if len(self.issues) >= MAX_ISSUE_EXAMPLES:
            return
        item = {"severity": severity, "code": code}
        if path is not None:
            item["file"] = str(path.relative_to(self.root))
        if line is not None:
            item["line"] = line
        self.issues.append(item)

    def record(self, row: object, path: Path, line: int, seen: set[str],
               detail: bool) -> None:
        if not isinstance(row, dict):
            self.issue("error", "record_not_object", path, line)
            return
        receipt = row.get("rcept_no")
        if not isinstance(receipt, str) or not re.fullmatch(r"\d{14}", receipt):
            self.issue("error", "invalid_rcept_no", path, line)
        elif receipt in seen:
            self.issue("error", "duplicate_rcept_no_within_file", path, line)
        else:
            seen.add(receipt)
        for field in ("corp_code", "corp_name", "report_nm", "rcept_dt"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                self.issue("error", "missing_or_invalid_" + field, path, line)
        if isinstance(row.get("corp_code"), str) and not re.fullmatch(r"\d{8}", row["corp_code"]):
            self.issue("error", "invalid_corp_code", path, line)
        date = row.get("rcept_dt")
        if isinstance(date, str) and date:
            try:
                if len(date) != 8 or not date.isascii() or not date.isdecimal():
                    raise ValueError
                datetime.strptime(date, "%Y%m%d")
            except ValueError:
                self.issue("error", "invalid_rcept_dt", path, line)
        if excluded(row):
            self.issue("error", "excluded_D002_or_D005_present", path, line)
        if not detail:
            return
        files = row.get("files")
        if not isinstance(files, list) or not files:
            self.issue("error", "detail_files_missing_or_empty", path, line)
            return
        has_text = False
        for entry in files:
            if not isinstance(entry, dict):
                self.issue("error", "detail_file_not_object", path, line)
                continue
            if not isinstance(entry.get("filename"), str) or not entry["filename"].strip():
                self.issue("error", "detail_filename_missing", path, line)
            content = entry.get("text")
            if not isinstance(content, str):
                self.issue("error", "detail_text_not_string", path, line)
            elif content.strip():
                has_text = True
                self.counts["text_characters"] += len(content)
        if not has_text:
            self.issue("error", "detail_has_no_nonempty_text", path, line)

    def csv_file(self, path: Path) -> None:
        seen: set[str] = set()
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if "rcept_no" not in (reader.fieldnames or []):
                    self.counts["non_list_csv_skipped"] += 1
                    return
                self.counts["list_csv_files"] += 1
                for line, row in enumerate(reader, 2):
                    self.counts["list_records"] += 1
                    if None in row:
                        self.issue("error", "csv_extra_columns", path, line)
                    self.record(row, path, line, seen, False)
        except (OSError, UnicodeError, csv.Error):
            self.issue("error", "csv_read_error", path)
        repeated = self.list_receipts.intersection(seen)
        self.counts["list_receipts_repeated_across_files"] += len(repeated)
        self.list_receipts.update(seen)

    def detail_file(self, path: Path) -> None:
        seen: set[str] = set()
        self.counts["detail_jsonl_files"] += 1
        try:
            with path.open("rb") as handle:
                for line, raw in enumerate(handle, 1):
                    if not raw.strip():
                        continue
                    try:
                        value = json.loads(raw.decode("utf-8-sig"))
                    except (UnicodeError, ValueError):
                        # A writer can be midway through the final record.
                        severity = "warning" if not raw.endswith(b"\n") else "error"
                        self.issue(severity, "incomplete_final_jsonl_line" if severity == "warning" else "invalid_jsonl_record", path, line)
                        continue
                    self.counts["detail_records"] += 1
                    self.record(value, path, line, seen, True)
        except OSError:
            self.issue("error", "detail_read_error", path)
        repeated = self.detail_receipts.intersection(seen)
        self.counts["detail_receipts_repeated_across_files"] += len(repeated)
        self.detail_receipts.update(seen)

    def scan_secret(self, path: Path) -> None:
        if not self.secrets:
            return
        needles = [secret.encode("ascii") for secret in self.secrets]
        overlap_size = max(map(len, needles)) - 1
        overlap = b""
        opener = gzip.open if path.suffix.lower() == ".gz" else open
        try:
            with opener(path, "rb") as handle:
                self.counts["secret_scan_files"] += 1
                while data := handle.read(1024 * 1024):
                    joined = overlap + data
                    if any(needle in joined for needle in needles):
                        self.issue("error", "api_key_found_in_output", path)
                        break
                    overlap = joined[-overlap_size:]
        except (OSError, EOFError):
            self.issue("warning", "secret_scan_read_error", path)

    def zip_sample(self, paths: list[Path]) -> None:
        self.counts["zip_files"] = len(paths)
        count = min(50, len(paths))
        indices = [round(i * (len(paths) - 1) / max(1, count - 1)) for i in range(count)]
        for index in indices:
            path = paths[index]
            self.counts["zip_sampled"] += 1
            try:
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip() is not None:
                        self.issue("error", "zip_crc_failure", path)
                    else:
                        self.counts["zip_valid"] += 1
            except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError, zlib.error):
                self.issue("error", "invalid_zip", path)

    def run(self) -> dict:
        if not self.root.is_dir():
            self.issue("error", "output_directory_missing")
            return self.summary()
        files = sorted(path for path in self.root.rglob("*") if path.is_file())
        self.counts["files_seen"] = len(files)
        if not self.secrets:
            self.issue("warning", "secret_scan_skipped_credential_unavailable")
        for path in files:
            name = path.name.lower()
            if path.suffix.lower() == ".lock":
                # Live OS locks contain only a sentinel byte and cannot be read
                # on Windows while the collector holds the byte-range lock.
                self.counts["runtime_locks_skipped"] += 1
                continue
            if path.suffix.lower() == ".csv":
                self.csv_file(path)
            elif name.endswith("_detail.jsonl") or (name.startswith("detail") and name.endswith(".jsonl")):
                self.detail_file(path)
            if path.suffix.lower() != ".zip":
                self.scan_secret(path)
        self.zip_sample([path for path in files if path.suffix.lower() == ".zip"])
        if not self.counts["list_csv_files"]:
            self.issue("warning", "no_disclosure_csv_found")
        if not self.counts["detail_jsonl_files"]:
            self.issue("warning", "no_detail_jsonl_found")
        self.counts["unique_list_receipts"] = len(self.list_receipts)
        self.counts["unique_detail_receipts"] = len(self.detail_receipts)
        if self.list_receipts:
            self.counts["list_receipts_without_detail"] = len(self.list_receipts - self.detail_receipts)
            self.counts["detail_receipts_without_list"] = len(self.detail_receipts - self.list_receipts)
            if self.counts["detail_receipts_without_list"]:
                self.issue("warning", "detail_receipts_not_in_csv")
        return self.summary()

    def summary(self) -> dict:
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "output_directory": str(self.root),
            "read_only": True,
            "status": "failed" if self.counts["errors"] else "warning" if self.counts["warnings"] else "passed",
            "counts": dict(self.counts),
            "issue_counts": dict(self.issue_counts),
            "issue_examples": self.issues,
            "issue_examples_limit": MAX_ISSUE_EXAMPLES,
            "secret_scan": "exact_key_match" if self.secrets else "skipped",
            "secret_keys_checked": len(self.secrets),
            "notes": [
                "ZIP integrity checks cover at most 50 evenly distributed archives.",
                "Credential scanning checks non-ZIP output files and decompresses gzip files; no broad 40-character token pattern is used.",
                "Receipt repetition across different files is counted separately; the same filing may concern multiple companies.",
                "Missing details measure collection coverage and do not constitute a format error.",
                "An incomplete last JSONL line is treated as an in-progress append warning.",
                "Excluded types are checked through explicit type codes when present and normalized report titles otherwise.",
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--key-file", action="append", type=Path,
                        help="Credential file for exact-match leak checks; repeat for multiple keys. Defaults to all local collector key files.")
    args = parser.parse_args()
    csv.field_size_limit(10 * 1024 * 1024)
    key_files = args.key_file or list(PROJECT.glob(".env.dart*.local"))
    secrets = sorted({secret for path in key_files if (secret := load_secret(path))})
    audit = Audit(args.output.resolve(), secrets)
    report = json.dumps(audit.run(), ensure_ascii=False, indent=2)
    # Even an accidentally sensitive filename cannot reveal the credential.
    for secret in secrets:
        report = report.replace(secret, "[REDACTED]")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(report)
    return 1 if audit.counts["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
