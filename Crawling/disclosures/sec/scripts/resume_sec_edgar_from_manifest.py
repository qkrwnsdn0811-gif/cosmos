#!/usr/bin/env python3
"""Resume an SEC EDGAR dataset from its immutable filings.jsonl manifest."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from collect_sec_edgar_nasdaq100 import HttpClient, validate_user_agent, write_bytes_atomic


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--progress-every", type=int, default=100)
    return parser.parse_args()


def load_rows(root: Path) -> list[dict[str, Any]]:
    manifest = root / "filings.jsonl"
    with manifest.open("r", encoding="utf-8") as source:
        rows = [json.loads(line) for line in source if line.strip()]
    seen: set[Path] = set()
    for row in rows:
        path = filing_path(root, row)
        if path in seen:
            raise ValueError(f"Duplicate filing path: {row['localPath']}")
        seen.add(path)
    return rows


def filing_path(root: Path, row: dict[str, Any]) -> Path:
    """Keep manifest-controlled writes inside this dataset's raw directory."""
    raw_path = str(row["localPath"])
    relative = PurePosixPath(raw_path)
    if ("\\" in raw_path or relative.is_absolute() or ".." in relative.parts
            or len(relative.parts) < 3 or relative.parts[0] != "raw"):
        raise ValueError(f"Invalid filing path: {raw_path}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve() / "raw"):
        raise ValueError(f"Filing path escapes raw directory: {raw_path}")
    return path


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def resume_one(client: HttpClient, root: Path, row: dict[str, Any]) -> tuple[str, int]:
    path = filing_path(root, row)
    expected_size = int(row["bytes"])
    expected_sha256 = str(row["sha256"])

    if path.is_file() and path.stat().st_size == expected_size:
        with path.open("rb") as source:
            actual_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        if actual_sha256 == expected_sha256:
            return "reused", expected_size

    body = client.get_bytes(str(row["sourceUrl"]))
    if body is None:
        raise RuntimeError(f"empty response: {row['sourceUrl']}")
    actual_sha256 = sha256_bytes(body)
    if len(body) != expected_size or actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"content mismatch: {row['localPath']} "
            f"size={len(body)}/{expected_size} sha256={actual_sha256}/{expected_sha256}"
        )
    write_bytes_atomic(path, body)
    return "downloaded", expected_size


def main() -> int:
    args = parse_args()
    if not 1 <= args.workers <= 8:
        raise SystemExit("--workers must be between 1 and 8")
    if args.request_delay < 0.1:
        raise SystemExit("--request-delay must be at least 0.1 seconds")
    if args.progress_every < 1:
        raise SystemExit("--progress-every must be at least 1")
    user_agent = validate_user_agent(os.environ.get("SEC_EDGAR_USER_AGENT", ""))

    root = args.root.resolve()
    rows = load_rows(root)
    client = HttpClient(user_agent, request_delay=args.request_delay)
    state_counts = {"reused": 0, "downloaded": 0, "failed": 0}
    completed_bytes = 0
    failures: list[dict[str, str]] = []
    lock = threading.Lock()

    def run(row: dict[str, Any]) -> tuple[str, int]:
        return resume_one(client, root, row)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run, row): row for row in rows}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            row = futures[future]
            try:
                state, size = future.result()
                with lock:
                    state_counts[state] += 1
                    completed_bytes += size
            except Exception as error:  # keep downloading independent filings
                with lock:
                    state_counts["failed"] += 1
                    failures.append(
                        {
                            "localPath": str(row.get("localPath", "")),
                            "sourceUrl": str(row.get("sourceUrl", "")),
                            "error": f"{type(error).__name__}: {error}",
                        }
                    )
            if completed % args.progress_every == 0 or completed == len(rows):
                print(
                    f"[{completed:05d}/{len(rows):05d}] "
                    f"reused={state_counts['reused']:05d} "
                    f"downloaded={state_counts['downloaded']:05d} "
                    f"failed={state_counts['failed']:03d} "
                    f"verified={completed_bytes / 1024**3:.2f}GiB",
                    flush=True,
                )

    report = {
        "manifest": str(root / "filings.jsonl"),
        "expected": len(rows),
        **state_counts,
        "verifiedBytes": completed_bytes,
        "failures": failures,
    }
    report_path = root / "resume-report.json"
    temporary = report_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    print(json.dumps({key: value for key, value in report.items() if key != "failures"}), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
