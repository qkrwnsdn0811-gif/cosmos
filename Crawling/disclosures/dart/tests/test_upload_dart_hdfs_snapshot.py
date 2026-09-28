import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("uploader", Path(__file__).resolve().parents[1] / "scripts/upload_dart_hdfs_snapshot.py")
uploader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(uploader)


class FakeHdfs:
    def __init__(self, corrupt=False):
        self.files, self.directories, self.commands = {}, set(), []
        self.corrupt = corrupt

    def exists(self, path):
        return path in self.directories

    def sizes(self, root):
        return {name[len(root) + 1:]: len(data) for name, data in self.files.items() if name.startswith(root + "/")}

    def sha256(self, path):
        data = self.files[path]
        return hashlib.sha256(data + (b"damage" if self.corrupt else b"")).hexdigest()

    def run(self, *args):
        self.commands.append(args)
        if args[0] == "-mkdir":
            for path in args[1:]:
                if path != "-p":
                    self.directories.add(path)
        elif args[0] == "-put":
            if args[1] == "-f":
                self.files[args[3]] = Path(args[2]).read_bytes()
                return
            root = args[-1]
            for source in map(Path, args[1:-1]):
                for path in ([source] if source.is_file() else source.rglob("*")):
                    if path.is_file():
                        self.files[root + "/" + path.relative_to(source.parent).as_posix()] = path.read_bytes()
        elif args[0] == "-mv":
            old, new = args[1:]
            for name in list(self.files):
                if name.startswith(old + "/"):
                    self.files[new + name[len(old):]] = self.files.pop(name)
            self.directories.remove(old)
            self.directories.add(new)
        elif args[0] == "-cat":
            return self.files[args[1]].decode("utf-8")
        elif args[0] == "-rm":
            del self.files[args[1]]
        else:
            raise AssertionError(args)


def fixture(root):
    (root / "metadata").mkdir()
    (root / "metadata/document_index.jsonl").write_text('{"rcept_no":"20260101000001"}\n', encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"snapshot_documents": 1, "selected_documents": 2, "not_in_snapshot": 1}), encoding="utf-8")
    entries = [{"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": uploader.digest(path)}
               for path in root.rglob("*") if path.is_file()]
    (root / "checksums.json").write_text(json.dumps({"algorithm": "sha256", "files": entries}), encoding="utf-8")
    (root / "ready.json").write_text(json.dumps({"status": "ready", "credential_text_check": "passed", "snapshot_documents": 1,
        "manifest_sha256": uploader.digest(root / "manifest.json"), "checksums_sha256": uploader.digest(root / "checksums.json")}), encoding="utf-8")


class UploadTests(unittest.TestCase):
    destination = "/datasets/opendart/snapshots/fixture"

    def test_verified_upload_and_idempotent_existing_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            hdfs = FakeHdfs()
            result = uploader.upload(root, self.destination, hdfs)
            self.assertEqual(result["status"], "uploaded_verified")
            self.assertEqual(result["files"], 4)
            first_commands = len(hdfs.commands)
            result = uploader.upload(root, self.destination, hdfs)
            self.assertEqual(result["status"], "already_verified")
            self.assertEqual(len(hdfs.commands), first_commands)

    def test_checksum_failure_preserves_staging_and_never_moves(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            hdfs = FakeHdfs(corrupt=True)
            with self.assertRaisesRegex(RuntimeError, "SHA256"):
                uploader.upload(root, self.destination, hdfs)
            self.assertTrue(hdfs.exists("/datasets/opendart/.uploading/fixture"))
            self.assertFalse(hdfs.exists(self.destination))
            self.assertFalse(any(cmd[0] == "-mv" for cmd in hdfs.commands))

    def test_unlisted_files_rejected_before_remote_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            (root / "unlisted.txt").write_text("extra", encoding="utf-8")
            hdfs = FakeHdfs()
            with self.assertRaisesRegex(ValueError, "Unexpected or missing"):
                uploader.upload(root, self.destination, hdfs)
            self.assertEqual(hdfs.commands, [])

    def test_existing_destination_mismatch_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            hdfs = FakeHdfs()
            hdfs.directories.add(self.destination)
            hdfs.files[self.destination + "/old"] = b"keep"
            with self.assertRaisesRegex(RuntimeError, "inventory"):
                uploader.upload(root, self.destination, hdfs)
            self.assertEqual(hdfs.files[self.destination + "/old"], b"keep")
            self.assertEqual(hdfs.commands, [])

    def test_partial_staging_reuses_valid_files_and_repairs_only_missing_or_bad(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            hdfs = FakeHdfs()
            staging = "/datasets/opendart/.uploading/fixture"
            hdfs.directories.add(staging)
            for name in ("checksums.json", "manifest.json"):
                hdfs.files[staging + "/" + name] = (root / name).read_bytes()
            hdfs.files[staging + "/ready.json"] = b"partial"
            hdfs.files[staging + "/metadata/document_index.jsonl._COPYING_"] = b"partial"
            result = uploader.upload(root, self.destination, hdfs)
            self.assertEqual(result["status"], "uploaded_verified")
            copies = [Path(command[2]).name for command in hdfs.commands if command[:2] == ("-put", "-f")]
            self.assertCountEqual(copies, ["ready.json", "document_index.jsonl"])
            self.assertTrue(any(command[0] == "-rm" for command in hdfs.commands))

    def test_unexpected_staging_file_is_preserved_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            hdfs = FakeHdfs()
            staging = "/datasets/opendart/.uploading/fixture"
            hdfs.directories.add(staging)
            hdfs.files[staging + "/unrelated._COPYING_"] = b"keep"
            with self.assertRaisesRegex(RuntimeError, "Unexpected staging"):
                uploader.upload(root, self.destination, hdfs)
            self.assertEqual(hdfs.commands, [])
            self.assertEqual(hdfs.files[staging + "/unrelated._COPYING_"], b"keep")


if __name__ == "__main__":
    unittest.main()
