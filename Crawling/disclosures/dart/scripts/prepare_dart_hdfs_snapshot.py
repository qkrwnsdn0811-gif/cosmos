"""Create an immutable local DART handoff from bounded, append-only JSONL files.

No network calls. Raw ZIP payloads are copied byte-for-byte into tar bundles.
The output directory must not already exist; ready.json is written last.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def check_secret(data, secrets):
    if any(secret and secret in data for secret in secrets):
        raise ValueError("Credential text detected; snapshot is not ready")


def write_json(path, value, secrets):
    data = encoded(value)
    check_secret(data, secrets)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def capture_details(source):
    return [{"path": path, "bytes": path.stat().st_size}
            for path in sorted(source.glob("shard-*/companies/*_detail.jsonl"))]


def bounded_lines(path, limit):
    """Never read beyond the captured byte boundary, including later appends."""
    with path.open("rb") as stream:
        remaining = limit
        while remaining:
            line = stream.readline(remaining)
            if not line:
                raise ValueError("Source detail file shrank after capture")
            remaining -= len(line)
            if not line.endswith(b"\n"):
                return
            yield line


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def prepare(source, output, begin="20160101", end="20260909", bundle_bytes=256 * 1024**2,
            secrets=(), captured=None, universe=None, *, rank_from=51, rank_to=100):
    if not 1 <= rank_from <= rank_to:
        raise ValueError("Invalid snapshot rank range")
    source, output = Path(source).resolve(), Path(output).resolve()
    captured_at = utcnow()
    captured = capture_details(source) if captured is None else captured
    if not captured:
        raise ValueError("No detail JSONL source files")
    if output.exists():
        raise ValueError("Output must be a new directory")
    output.mkdir(parents=True)
    (output / "metadata/company_lists").mkdir(parents=True)
    (output / "data").mkdir()
    (output / "raw").mkdir()
    selected, csv_sources = {}, []
    for path in sorted(source.glob("shard-*/companies/*.csv")):
        raw = path.read_bytes()
        check_secret(raw, secrets)
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        rows = []
        for row in reader:
            receipt, date = str(row["rcept_no"]), str(row["rcept_dt"])
            if not re.fullmatch(r"\d{14}", receipt):
                raise ValueError("Invalid receipt in selected CSV")
            if not begin <= date <= end:
                continue
            if receipt in selected and selected[receipt] != row:
                raise ValueError("Conflicting selected receipt metadata")
            selected[receipt] = row
            rows.append(row)
        relative = Path("metadata/company_lists") / path.name
        if (output / relative).exists():
            raise ValueError("Duplicate company CSV filename")
        with (output / relative).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        csv_sources.append({"source": path.relative_to(source).as_posix(), "snapshot": relative.as_posix(), "rows": len(rows)})
    if not selected:
        raise ValueError("No selected receipts in range")
    if universe:
        kept = []
        for line in Path(universe).read_bytes().splitlines():
            check_secret(line, secrets)
            row = json.loads(line)
            if rank_from <= int(row.get("universe_rank", 0)) <= rank_to:
                kept.append(row)
        write_json(output / "metadata/universe.json", kept, secrets)

    included, documents, boundaries, rejected = set(), [], [], []
    duplicate_count = 0
    index_path = output / "metadata/document_index.jsonl"
    bundle_number, bundle_used = 1, 0
    for capture in captured:
        path, limit = Path(capture["path"]), capture["bytes"]
        source_relative = path.relative_to(source).as_posix()
        destination = Path("data") / (path.stem + ".jsonl.gz")
        if (output / destination).exists():
            raise ValueError("Duplicate company detail filename")
        read_bytes, complete_lines, exported = 0, 0, 0
        with (output / destination).open("wb") as binary:
            with gzip.GzipFile(fileobj=binary, mode="wb", filename="", mtime=0, compresslevel=6) as zipped:
                for line in bounded_lines(path, limit):
                    read_bytes += len(line)
                    complete_lines += 1
                    check_secret(line, secrets)
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        rejected.append({"source": source_relative, "line": complete_lines, "reason": "invalid_json"})
                        continue
                    receipt = str(row.get("rcept_no", ""))
                    if receipt in included:
                        duplicate_count += 1
                        continue
                    expected = selected.get(receipt)
                    if not expected or not begin <= str(row.get("rcept_dt", "")) <= end:
                        rejected.append({"source": source_relative, "line": complete_lines, "receipt": receipt, "reason": "outside_selection"})
                        continue
                    if any(str(row.get(field, "")) != str(expected.get(field, "")) for field in ("corp_code", "rcept_dt", "report_nm")):
                        rejected.append({"source": source_relative, "line": complete_lines, "receipt": receipt, "reason": "metadata_mismatch"})
                        continue
                    if not isinstance(row.get("files"), list) or not row["files"]:
                        rejected.append({"receipt": receipt, "reason": "missing_parsed_files"})
                        continue
                    raw_path = path.parent.parent / "raw/documents" / (receipt + ".zip")
                    if not raw_path.is_file() or raw_path.stat().st_size == 0:
                        rejected.append({"receipt": receipt, "reason": "raw_zip_missing"})
                        continue
                    raw_size = raw_path.stat().st_size
                    tar_size = 512 + ((raw_size + 511) // 512) * 512
                    if bundle_used and bundle_used + tar_size > bundle_bytes:
                        bundle_number += 1
                        bundle_used = 0
                    bundle = f"raw/documents-part-{bundle_number:05d}.tar"
                    bundle_used += tar_size
                    zipped.write(line)
                    documents.append({"receipt": receipt, "raw_path": raw_path, "raw_bytes": raw_size,
                                      "bundle": bundle, "member": f"documents/{receipt}.zip",
                                      "index": {"receipt": receipt, "rcept_no": receipt,
                                                "corp_code": row["corp_code"], "corp_name": row.get("corp_name"),
                                                "stock_code": row.get("stock_code"), "universe_rank": row.get("universe_rank"),
                                                "rcept_dt": row["rcept_dt"], "report_nm": row["report_nm"],
                                                "source_detail": source_relative, "source_line": complete_lines,
                                                "snapshot_detail": destination.as_posix(), "raw_bundle": bundle,
                                                "raw_member": f"documents/{receipt}.zip", "raw_bytes": raw_size}})
                    included.add(receipt)
                    exported += 1
        boundaries.append({"source": source_relative, "captured_bytes": limit, "complete_line_bytes": read_bytes,
                           "excluded_tail_bytes": limit - read_bytes, "complete_lines": complete_lines,
                           "exported_documents": exported, "snapshot": destination.as_posix()})

    current_bundle, archive = None, None
    try:
        with index_path.open("wb") as index:
            for document in documents:
                if document["bundle"] != current_bundle:
                    if archive:
                        archive.close()
                    current_bundle = document["bundle"]
                    archive = tarfile.open(output / current_bundle, "w", format=tarfile.USTAR_FORMAT)
                info = tarfile.TarInfo(document["member"])
                info.size, info.mtime, info.mode = document["raw_bytes"], 0, 0o644
                if document["raw_path"].stat().st_size != info.size:
                    raise ValueError("Raw ZIP size changed during snapshot")
                with document["raw_path"].open("rb") as raw:
                    payload = raw.read(info.size + 1)
                if len(payload) != info.size:
                    raise ValueError("Raw ZIP size changed during snapshot")
                archive.addfile(info, io.BytesIO(payload))
                record = document["index"]
                record["raw_sha256"] = hashlib.sha256(payload).hexdigest()
                data = encoded(record)
                check_secret(data, secrets)
                index.write(data)
    finally:
        if archive:
            archive.close()

    unavailable, source_errors = [], []
    state_observed_at = utcnow()
    state_roots = sorted(source.glob("shard-*/state/documents"))
    for receipt in sorted(set(selected) - included):
        for state_root in state_roots:
            state_path = state_root / (receipt + ".json")
            if not state_path.is_file():
                continue
            try:
                state = json.loads(state_path.read_bytes())
            except (ValueError, OSError):
                source_errors.append({"receipt": receipt, "status": "state_unreadable"})
                break
            safe = {"receipt": receipt, "status": state.get("status"), "api_status": state.get("api_status"), "at": state.get("at")}
            if state.get("status") == "unavailable":
                unavailable.append(safe)
            elif state.get("status") not in ("collected", "pending"):
                source_errors.append(safe)
            break
    write_json(output / "metadata/source_exceptions.json", {"observed_at": state_observed_at, "errors": source_errors,
               "source_unavailable": unavailable, "snapshot_rejections": rejected}, secrets)
    write_json(output / "metadata/pending_receipts.json", sorted(set(selected) - included), secrets)
    manifest = {"format_version": 1, "snapshot_kind": "bounded_partial_collection", "captured_at": captured_at,
                "completed_at": utcnow(), "begin": begin, "end": end, "rank_from": rank_from, "rank_to": rank_to,
                "source_directory": source.as_posix(), "selected_documents": len(selected),
                "snapshot_documents": len(included), "not_in_snapshot": len(selected) - len(included),
                "source_unavailable_observed": len(unavailable), "source_errors_observed": len(source_errors),
                "duplicate_detail_rows_skipped": duplicate_count, "rejected_rows": len(rejected),
                "raw_bundles": bundle_number if documents else 0, "company_csv_files": csv_sources,
                "captured_detail_boundaries": boundaries,
                "notes": ["Completed JSONL lines at per-file captured byte boundaries; ongoing appends are excluded.",
                          "Raw ZIP bytes are preserved without extraction or repair.",
                          "State observations occur after capture and may reflect continued collection.",
                          "Raw tar bundles are preservation copies; data/*.jsonl.gz is the parsed analysis input."]}
    write_json(output / "manifest.json", manifest, secrets)
    checksums = [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)}
                 for path in sorted(output.rglob("*")) if path.is_file()]
    write_json(output / "checksums.json", {"algorithm": "sha256", "files": checksums,
               "excluded": ["checksums.json", "ready.json"]}, secrets)
    write_json(output / "ready.json", {"status": "ready", "created_at": utcnow(),
               "snapshot_documents": len(included), "manifest_sha256": digest(output / "manifest.json"),
               "checksums_sha256": digest(output / "checksums.json"), "credential_text_check": "passed",
               "credential_patterns_checked": len(secrets)}, secrets)
    return manifest


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--begin", default="20160101")
    parser.add_argument("--end", required=True)
    parser.add_argument("--bundle-mib", type=int, default=256)
    parser.add_argument("--key-file", type=Path, action="append", default=[])
    parser.add_argument("--universe", type=Path, default=project / "config/universe-20260907.jsonl")
    args = parser.parse_args()
    secrets = set()
    for path in set(project.glob(".env.dart*.local")) | set(args.key_file):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if re.match(r"\s*DART_API_KEY\s*=", line):
                value = line.split("=", 1)[1].strip().strip("\"'")
                if value:
                    secrets.add(value.encode("utf-8"))
    if args.bundle_mib < 1:
        parser.error("--bundle-mib must be positive")
    result = prepare(args.source, args.output, args.begin, args.end, args.bundle_mib * 1024**2,
                     tuple(secrets), universe=args.universe if args.universe.exists() else None)
    print(json.dumps({key: result[key] for key in ("selected_documents", "snapshot_documents", "not_in_snapshot", "raw_bundles")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
