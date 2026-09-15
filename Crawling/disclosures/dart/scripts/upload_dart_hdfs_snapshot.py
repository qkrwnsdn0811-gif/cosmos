"""Verify a staged DART snapshot, upload to a new HDFS path, and verify all bytes.

Run on the Hadoop host with Python 3. Only the prepared snapshot is uploaded.
Failures leave the isolated .uploading path available for inspection.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def load_snapshot(source):
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Source must be a snapshot directory")
    ready = json.loads((source / "ready.json").read_text(encoding="utf-8"))
    if ready.get("status") != "ready" or ready.get("credential_text_check") != "passed":
        raise ValueError("Snapshot is not ready or credential check did not pass")
    for filename, field in (("manifest.json", "manifest_sha256"), ("checksums.json", "checksums_sha256")):
        if digest(source / filename) != ready.get(field):
            raise ValueError("Snapshot control-file hash mismatch: " + filename)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    checksums = json.loads((source / "checksums.json").read_text(encoding="utf-8"))
    if checksums.get("algorithm") != "sha256":
        raise ValueError("Unsupported checksum algorithm")
    entries = {}
    for entry in checksums["files"]:
        relative = entry["path"]
        parts = PurePosixPath(relative)
        if (parts.is_absolute() or parts.as_posix() != relative or ".." in parts.parts
                or "\\" in relative or not parts.parts or relative in entries
                or relative in ("checksums.json", "ready.json")):
            raise ValueError("Unsafe or duplicate checksum path")
        path = source / relative
        if not path.is_file() or source not in path.resolve().parents:
            raise ValueError("Missing or external snapshot file: " + relative)
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError("Snapshot file verification failed: " + relative)
        entries[relative] = dict(entry)
    if "manifest.json" not in entries or "metadata/document_index.jsonl" not in entries:
        raise ValueError("Missing manifest or document index checksum")
    for filename in ("checksums.json", "ready.json"):
        path = source / filename
        entries[filename] = {"path": filename, "bytes": path.stat().st_size, "sha256": digest(path)}
    actual = set()
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed in a snapshot")
        if path.is_file():
            actual.add(path.relative_to(source).as_posix())
    if actual != set(entries):
        raise ValueError("Unexpected or missing files in snapshot directory")
    with (source / "metadata/document_index.jsonl").open("rb") as stream:
        receipts = set()
        for line in stream:
            row = json.loads(line)
            receipt = str(row["rcept_no"])
            if not re.fullmatch(r"\d{14}", receipt) or receipt in receipts:
                raise ValueError("Invalid or duplicate document index receipt")
            receipts.add(receipt)
    count = len(receipts)
    if (count != manifest.get("snapshot_documents") or count != ready.get("snapshot_documents")
            or manifest["selected_documents"] - count != manifest.get("not_in_snapshot")):
        raise ValueError("Snapshot document counts disagree")
    return source, manifest, entries


class Hdfs:
    def __init__(self, executable="/opt/hadoop/bin/hdfs"):
        self.executable = executable

    def run(self, *arguments):
        result = subprocess.run([self.executable, "dfs", *arguments], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("HDFS command failed (" + arguments[0] + "): " + result.stderr.strip())
        return result.stdout

    def exists(self, path):
        result = subprocess.run([self.executable, "dfs", "-test", "-e", path], capture_output=True, text=True)
        # A missing path returns 1; a native-library warning is unrelated to existence.
        errors = "\n".join(line for line in result.stderr.splitlines()
                           if "Unable to load native-hadoop library" not in line)
        if result.returncode not in (0, 1) or (result.returncode and errors.strip()):
            raise RuntimeError("HDFS existence check failed: " + result.stderr.strip())
        return result.returncode == 0

    def sizes(self, root):
        found = {}
        for line in self.run("-ls", "-R", root).splitlines():
            if not line.startswith("-"):
                continue
            fields = line.split(None, 7)
            if len(fields) != 8 or not fields[7].startswith(root + "/"):
                raise RuntimeError("Unexpected HDFS listing")
            found[fields[7][len(root) + 1:]] = int(fields[4])
        return found

    def sha256(self, path):
        sha = hashlib.sha256()
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([self.executable, "dfs", "-cat", path], stdout=subprocess.PIPE, stderr=errors)
            try:
                for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
                    sha.update(block)
                process.stdout.close()
                result = process.wait()
                if result:
                    errors.seek(0)
                    raise RuntimeError("HDFS read failed: " + errors.read().decode("utf-8", errors="replace"))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        return sha.hexdigest()


def verify_hdfs(hdfs, root, entries, workers=4):
    if hdfs.sizes(root) != {name: entry["bytes"] for name, entry in entries.items()}:
        raise RuntimeError("HDFS file inventory or sizes disagree with snapshot")

    def verify(item):
        name, entry = item
        if hdfs.sha256(root + "/" + name) != entry["sha256"]:
            raise RuntimeError("HDFS SHA256 mismatch: " + name)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(verify, entries.items()))


def resume_staging(hdfs, source, staging, entries, workers=4):
    existing = hdfs.sizes(staging)
    leftovers = {name for name in existing if name.endswith("._COPYING_")
                 and name[:-len("._COPYING_")] in entries}
    if set(existing) - set(entries) - leftovers:
        raise RuntimeError("Unexpected staging files; all files left intact")
    # A valid checksum manifest identifying another snapshot must never be overwritten.
    if "checksums.json" in existing:
        try:
            previous = json.loads(hdfs.run("-cat", staging + "/checksums.json"))
        except (ValueError, UnicodeDecodeError):
            previous = None  # An interrupted control file can be repaired below.
        if isinstance(previous, dict) and isinstance(previous.get("files"), list):
            prior = {item["path"]: (item["bytes"], item["sha256"]) for item in previous["files"]}
            expected = {name: (entry["bytes"], entry["sha256"]) for name, entry in entries.items()
                        if name not in {"checksums.json", "ready.json"}}
            if prior != expected:
                raise RuntimeError("Staging belongs to a different snapshot; all files left intact")
    for name in sorted(leftovers):
        hdfs.run("-rm", staging + "/" + name)
    existing = {name: size for name, size in existing.items() if name not in leftovers}

    def needs_copy(item):
        name, entry = item
        return (name if existing.get(name) != entry["bytes"]
                or hdfs.sha256(staging + "/" + name) != entry["sha256"] else None)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        missing = [name for name in executor.map(needs_copy, entries.items()) if name is not None]
    parents = sorted({staging + "/" + str(PurePosixPath(name).parent) for name in missing
                      if str(PurePosixPath(name).parent) != "."})
    if parents:
        hdfs.run("-mkdir", "-p", *parents)
    for name in missing:
        hdfs.run("-put", "-f", str(source / name), staging + "/" + name)


def upload(source, destination, hdfs=None, workers=4):
    if not re.fullmatch(r"/datasets/opendart/snapshots/[A-Za-z0-9][A-Za-z0-9._-]*", destination):
        raise ValueError("Destination must be one named /datasets/opendart/snapshots/<id> directory")
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
            hdfs.run("-mkdir", "-p", "/datasets/opendart/.uploading", "/datasets/opendart/snapshots")
            hdfs.run("-mkdir", staging)
            # These children belong to the exact inventory that was verified above.
            hdfs.run("-put", *(str(path) for path in sorted(source.iterdir())), staging)
        verify_hdfs(hdfs, staging, entries, workers)
        if hdfs.exists(destination):
            raise RuntimeError("Destination appeared during upload; staging preserved: " + staging)
        hdfs.run("-mv", staging, destination)
        # A directory rename retains the verified file bytes. Check the final inventory.
        if hdfs.sizes(destination) != {name: entry["bytes"] for name, entry in entries.items()}:
            raise RuntimeError("Final HDFS inventory verification failed")
        status = "uploaded_verified"
    return {"status": status, "destination": destination,
            "snapshot_documents": manifest["snapshot_documents"],
            "selected_documents": manifest["selected_documents"], "captured_at": manifest.get("captured_at"),
            "files": len(entries), "bytes": sum(entry["bytes"] for entry in entries.values()),
            "verification": "all_file_sizes_and_sha256", "verified_at": datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--hdfs", default="/opt/hadoop/bin/hdfs")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--verify-workers", type=int, default=4)
    args = parser.parse_args()
    source, report = args.source.resolve(), args.report.resolve()
    if report == source or source in report.parents:
        parser.error("Report must be outside the snapshot source directory")
    if not 1 <= args.verify_workers <= 8:
        parser.error("--verify-workers must be between 1 and 8")
    try:
        result = upload(source, args.destination, Hdfs(args.hdfs), args.verify_workers)
    except Exception as error:
        result = {"status": "failed", "destination": args.destination, "error": str(error),
                  "note": "Any HDFS staging files have been preserved for inspection."}
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        raise SystemExit(1)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
