"""Preserve a checked public DART ZIP, prepare JSONL analysis files, and import to HDFS.

Archive contents are data, never commands or configuration. The original archive
is retained byte-for-byte; every company CSV is retained and every body record is
validated without applying date or report-type filters.
"""
import argparse
import csv
from contextlib import ExitStack
from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile

from upload_dart_hdfs_snapshot import Hdfs, digest, load_snapshot, resume_staging, verify_hdfs


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def valid_digits(value, width, label):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{%d}" % width, value) is None:
        raise ValueError("Invalid " + label)
    return value


def valid_date(value):
    valid_digits(value, 8, "receipt date")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError("Invalid receipt date") from None
    return value


def members_checked(archive):
    found = {}
    for entry in archive.infolist():
        # Legacy Windows ZIP writers store CP949 filenames without the UTF-8 flag.
        # A Unicode Path extra field may already have replaced ZipInfo.filename.
        # orig_filename retains the CP437 interpretation needed to recover the
        # original bytes. Use the unmodified ZipInfo object for subsequent reads.
        try:
            name = entry.orig_filename if entry.flag_bits & 0x800 else entry.orig_filename.encode("cp437").decode("cp949")
        except UnicodeError:
            raise ValueError("Archive filename is not valid UTF-8 or CP949") from None
        path = PurePosixPath(name)
        mode = entry.external_attr >> 16
        for candidate in (entry.orig_filename, entry.filename, name):
            candidate_path = PurePosixPath(candidate)
            if (not candidate or "\\" in candidate or ":" in candidate or "\x00" in candidate
                    or candidate_path.is_absolute() or ".." in candidate_path.parts
                    or candidate_path.as_posix() != candidate.rstrip("/")):
                raise ValueError("Unsafe, encrypted, or duplicate archive member")
        if stat.S_ISLNK(mode) or entry.flag_bits & 1 or name in found:
            raise ValueError("Unsafe, encrypted, or duplicate archive member")
        if entry.filename not in (entry.orig_filename, name):
            raise ValueError("Unicode path field disagrees with legacy archive filename")
        if entry.is_dir():
            continue
        # The supplied format contains data files at archive root only.
        if len(path.parts) != 1 or not (name.endswith(".csv") or name.endswith("_detail.jsonl")
                or name in {"api_quota.json", "corp_code_cache.json"}):
            raise ValueError("Unexpected archive member type")
        found[name] = entry
    return found


def read_reference(path):
    receipts = set()
    if path:
        with Path(path).open(encoding="utf-8-sig") as stream:
            for line in stream:
                if line.strip():
                    row = json.loads(line)
                    receipts.add(valid_digits(row.get("rcept_no"), 14, "reference receipt"))
    return receipts


def prepare(archive_path, output, credential_report, reference_selection=None, compression_level=1):
    archive_path = Path(archive_path).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        # A completed package can be retried; an incomplete one must be inspected.
        if not (output / "ready.json").is_file():
            raise ValueError("Output already exists without a completed ready marker")
        source, manifest, _ = load_snapshot(output)
        if digest(archive_path) != manifest.get("source_archive_sha256"):
            raise ValueError("Existing output belongs to a different archive")
        return source, manifest
    scan = json.loads(Path(credential_report).read_text(encoding="utf-8-sig"))
    archive_hash = digest(archive_path)
    if (scan.get("archive_sha256") != archive_hash or scan.get("crc_check") != "passed"
            or scan.get("keys_checked", 0) < 1 or scan.get("matches") != 0):
        raise ValueError("Archive hash, CRC, or credential scan attestation is missing or invalid")
    reference = read_reference(reference_selection)
    captured = datetime.now(timezone.utc).isoformat()
    seen, selected, csv_selected, company_codes, years = set(), set(), set(), set(), {}
    dates, companies, overlaps = [], [], 0
    output.mkdir(parents=True)
    for directory in ("raw", "data/period=2016-onward", "data/period=before-2016", "metadata/company_lists"):
        (output / directory).mkdir(parents=True)
    print("Preparing archive metadata and company bodies", flush=True)
    with zipfile.ZipFile(archive_path) as archive, (output / "metadata/document_index.jsonl").open("w", encoding="utf-8", newline="\n") as index:
        members = members_checked(archive)
        csv_members = sorted(name for name in members if name.endswith(".csv"))
        expected_details = {name[:-4] + "_detail.jsonl" for name in csv_members}
        if expected_details != {name for name in members if name.endswith("_detail.jsonl")}:
            raise ValueError("Company CSV and detail JSONL members do not pair")
        for csv_name in csv_members:
            detail_name = csv_name[:-4] + "_detail.jsonl"
            code, company_name, company_selected = None, None, set()
            with archive.open(members[csv_name]) as binary, io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text:
                rows = csv.DictReader(text)
                if not {"rcept_no", "corp_code", "rcept_dt"}.issubset(rows.fieldnames or []):
                    raise ValueError("Company CSV lacks required columns")
                for row in rows:
                    row_code = valid_digits(row.get("corp_code"), 8, "company code")
                    receipt = valid_digits(row.get("rcept_no"), 14, "CSV receipt")
                    valid_date(row.get("rcept_dt"))
                    if code is not None and code != row_code:
                        raise ValueError("Company CSV contains multiple company codes")
                    if receipt in csv_selected or receipt in company_selected:
                        raise ValueError("Duplicate CSV receipt: " + receipt)
                    code, company_name = row_code, row.get("corp_name", "")
                    company_selected.add(receipt)
            if code is None:
                raise ValueError("Cannot identify company from empty CSV")
            if code in company_codes:
                raise ValueError("Duplicate company code: " + code)
            company_codes.add(code)
            csv_selected.update(company_selected)
            selected.update(company_selected)
            csv_relative = "metadata/company_lists/" + code + ".csv"
            with archive.open(members[csv_name]) as source, (output / csv_relative).open("wb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            detail_relatives = {period: "data/period=" + period + "/" + code + "_detail.jsonl.gz"
                                for period in ("before-2016", "2016-onward")}
            count = 0
            company_seen = set()
            period_counts = {period: 0 for period in detail_relatives}
            with archive.open(members[detail_name]) as binary, io.TextIOWrapper(binary, encoding="utf-8-sig") as text:
                with ExitStack() as stack:
                    targets = {}
                    for period, relative in detail_relatives.items():
                        compressed = stack.enter_context((output / relative).open("wb"))
                        targets[period] = stack.enter_context(gzip.GzipFile(filename="", fileobj=compressed, mode="wb", compresslevel=compression_level, mtime=0))
                    for line_number, line in enumerate(text, 1):
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except (ValueError, UnicodeError):
                            raise ValueError("Malformed company JSONL for " + code + " at line " + str(line_number)) from None
                        if not isinstance(row, dict):
                            raise ValueError("Company body record is not an object")
                        receipt = valid_digits(row.get("rcept_no"), 14, "body receipt")
                        if valid_digits(row.get("corp_code"), 8, "body company code") != code:
                            raise ValueError("Body company disagrees with CSV")
                        date = valid_date(row.get("rcept_dt"))
                        if receipt in seen:
                            raise ValueError("Duplicate body receipt: " + receipt)
                        files = row.get("files")
                        if (not isinstance(files, list) or not files or any(
                                not isinstance(item, dict) or not isinstance(item.get("filename"), str)
                                or not isinstance(item.get("text"), str) for item in files)):
                            raise ValueError("Invalid body files schema for receipt " + receipt)
                        for field in ("corp_name", "report_nm"):
                            if not isinstance(row.get(field), str):
                                raise ValueError("Invalid body metadata field " + field)
                        seen.add(receipt)
                        selected.add(receipt)
                        company_seen.add(receipt)
                        count += 1
                        dates.append(date)
                        years[date[:4]] = years.get(date[:4], 0) + 1
                        overlaps += int(receipt in reference)
                        period = "2016-onward" if date >= "20160101" else "before-2016"
                        detail_relative = detail_relatives[period]
                        period_counts[period] += 1
                        # Preserve each JSONL record's text, including whitespace and key order.
                        targets[period].write((line.rstrip("\r\n") + "\n").encode("utf-8"))
                        item = {key: row[key] for key in ("rcept_no", "corp_code", "corp_name", "rcept_dt", "report_nm")}
                        item.update(snapshot_detail=detail_relative, source_zip_member=detail_name,
                                    period=period, overlap_with_reference_selection=receipt in reference)
                        index.write(json.dumps(item, ensure_ascii=False) + "\n")
            companies.append({"corp_code": code, "corp_name": company_name, "selected_documents": len(company_selected | company_seen),
                              "csv_documents": len(company_selected), "body_not_in_csv": len(company_seen - company_selected),
                              "snapshot_documents": count, "not_in_snapshot": len(company_selected - company_seen),
                              "csv": csv_relative, "detail_by_period": detail_relatives, "body_period_counts": period_counts,
                              "source_csv_member": csv_name, "source_detail_member": detail_name})
            print("Prepared company " + code + ": " + str(count) + " bodies", flush=True)
        archive_inventory = [{"name": name, "bytes": entry.file_size, "compressed_bytes": entry.compress_size,
                              "crc32": format(entry.CRC, "08x")} for name, entry in members.items()]
    print("Preserving original archive and hashing package", flush=True)
    shutil.copyfile(archive_path, output / "raw/dart.zip")
    if digest(output / "raw/dart.zip") != archive_hash:
        raise ValueError("Preserved raw archive hash mismatch")
    write_json(output / "metadata/missing_body_receipts.json", sorted(selected - seen))
    manifest = {"format_version": 1, "kind": "user_dart_zip_import", "captured_at": captured,
                "source_archive": "raw/dart.zip", "source_archive_sha256": archive_hash,
                "source_archive_bytes": archive_path.stat().st_size, "source_members": archive_inventory,
                "snapshot_documents": len(seen), "selected_documents": len(selected),
                "csv_documents": len(csv_selected), "body_not_in_csv": len(seen - csv_selected),
                "csv_without_body": len(csv_selected - seen),
                "not_in_snapshot": len(selected - seen), "companies": companies,
                "body_date_min": min(dates) if dates else None, "body_date_max": max(dates) if dates else None,
                "body_year_counts": dict(sorted(years.items())), "filters_applied": [],
                "body_period_counts": {"before-2016": sum(n for year, n in years.items() if year < "2016"),
                                       "2016-onward": sum(n for year, n in years.items() if year >= "2016")},
                "reference_selection_documents": len(reference), "overlap_with_reference_selection": overlaps,
                "credential_text_check": "passed", "crc_check": "passed",
                "original_archive_preserved": True,
                "note": "Archive members are data only. api_quota.json and corp_code_cache.json remain inside the original archive."}
    write_json(output / "manifest.json", manifest)
    entries = [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)}
               for path in sorted(output.rglob("*")) if path.is_file()]
    write_json(output / "checksums.json", {"algorithm": "sha256", "files": entries})
    write_json(output / "ready.json", {"status": "ready", "credential_text_check": "passed",
                                      "snapshot_documents": len(seen), "manifest_sha256": digest(output / "manifest.json"),
                                      "checksums_sha256": digest(output / "checksums.json")})
    return output, manifest


def upload_import(source, destination, hdfs=None, workers=2):
    if not re.fullmatch(r"/datasets/opendart/imports/[A-Za-z0-9][A-Za-z0-9._-]*", destination):
        raise ValueError("Destination must be one named /datasets/opendart/imports/<id> directory")
    source, manifest, entries = load_snapshot(source)
    hdfs = hdfs or Hdfs()
    staging = "/datasets/opendart/.uploading/" + destination.rsplit("/", 1)[1]
    if hdfs.exists(destination):
        verify_hdfs(hdfs, destination, entries, workers)
        status = "already_verified"
    else:
        if hdfs.exists(staging):
            resume_staging(hdfs, source, staging, entries, workers)
        else:
            hdfs.run("-mkdir", "-p", "/datasets/opendart/.uploading", "/datasets/opendart/imports")
            hdfs.run("-mkdir", staging)
            hdfs.run("-put", *(str(path) for path in sorted(source.iterdir())), staging)
        verify_hdfs(hdfs, staging, entries, workers)
        if hdfs.exists(destination):
            raise RuntimeError("Destination appeared during upload; staging preserved")
        hdfs.run("-mv", staging, destination)
        if hdfs.sizes(destination) != {name: entry["bytes"] for name, entry in entries.items()}:
            raise RuntimeError("Final HDFS inventory verification failed")
        status = "uploaded_verified"
    return {"status": status, "destination": destination, "snapshot_documents": manifest["snapshot_documents"],
            "selected_documents": manifest["selected_documents"], "not_in_snapshot": manifest["not_in_snapshot"],
            "companies": len(manifest["companies"]), "csv_documents": manifest["csv_documents"],
            "body_not_in_csv": manifest["body_not_in_csv"], "csv_without_body": manifest["csv_without_body"],
            "body_period_counts": manifest["body_period_counts"],
            "overlap_with_reference_selection": manifest["overlap_with_reference_selection"],
            "body_date_min": manifest["body_date_min"], "body_date_max": manifest["body_date_max"],
            "files": len(entries), "bytes": sum(entry["bytes"] for entry in entries.values()),
            "source_archive_sha256": manifest["source_archive_sha256"],
            "verification": "all_file_sizes_and_sha256", "verified_at": datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--credential-scan-report", type=Path, required=True)
    parser.add_argument("--reference-selection", type=Path)
    parser.add_argument("--hdfs", default="/opt/hadoop/bin/hdfs")
    parser.add_argument("--verify-workers", type=int, default=2)
    parser.add_argument("--compression-level", type=int, choices=range(1, 10), default=1)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.output.resolve() == args.report.resolve() or args.output.resolve() in args.report.resolve().parents:
        parser.error("Report must be outside prepared package directory")
    if not 1 <= args.verify_workers <= 8:
        parser.error("--verify-workers must be between 1 and 8")
    if not re.fullmatch(r"/datasets/opendart/imports/[A-Za-z0-9][A-Za-z0-9._-]*", args.destination):
        parser.error("Destination must be one named /datasets/opendart/imports/<id> directory")
    try:
        source, manifest = prepare(args.archive, args.output, args.credential_scan_report,
                                   args.reference_selection, args.compression_level)
        if args.prepare_only:
            result = {"status": "prepared", "output": str(source), "snapshot_documents": manifest["snapshot_documents"],
                      "selected_documents": manifest["selected_documents"], "not_in_snapshot": manifest["not_in_snapshot"]}
        else:
            print("Uploading and verifying HDFS package", flush=True)
            result = upload_import(source, args.destination, Hdfs(args.hdfs), args.verify_workers)
    except Exception as error:
        # Avoid echoing source bodies or arbitrary exception text from malformed data.
        result = {"status": "failed", "error_class": type(error).__name__,
                  "error": str(error) if type(error) is ValueError else "Import operation failed; prepared and staging files preserved"}
        write_json(args.report, result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        raise SystemExit(1)
    write_json(args.report, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
