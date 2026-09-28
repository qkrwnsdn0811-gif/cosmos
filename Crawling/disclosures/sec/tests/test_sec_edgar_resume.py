import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
for name in ("collect_sec_edgar_nasdaq100", "resume_sec_edgar_from_manifest"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
RESUME = sys.modules["resume_sec_edgar_from_manifest"]


class ManifestResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = b"Synthetic filing body for an offline fixture.\n" * 5
        self.row = {
            "localPath": "raw/001_TEST/2026-01-02_10-K_0000000000-26-000001.txt",
            "sourceUrl": "https://www.sec.gov/Archives/edgar/data/0/fixture.txt",
            "bytes": len(self.body),
            "sha256": hashlib.sha256(self.body).hexdigest(),
        }

    def client(self, body):
        class StubClient:
            def __init__(self):
                self.calls = 0

            def get_bytes(self, url):
                self.calls += 1
                return body

        return StubClient()

    def test_reuses_verified_file_without_http(self):
        path = self.root / self.row["localPath"]
        path.parent.mkdir(parents=True)
        path.write_bytes(self.body)
        client = self.client(None)
        self.assertEqual(("reused", len(self.body)), RESUME.resume_one(client, self.root, self.row))
        self.assertEqual(0, client.calls)

    def test_downloads_missing_file_and_checks_manifest(self):
        client = self.client(self.body)
        self.assertEqual(("downloaded", len(self.body)), RESUME.resume_one(client, self.root, self.row))
        self.assertEqual(self.body, (self.root / self.row["localPath"]).read_bytes())
        self.assertEqual(1, client.calls)

    def test_replaces_same_size_corruption_only_after_hash_validation(self):
        path = self.root / self.row["localPath"]
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x" * len(self.body))
        RESUME.resume_one(self.client(self.body), self.root, self.row)
        self.assertEqual(self.body, path.read_bytes())

    def test_bad_download_does_not_write_file(self):
        with self.assertRaisesRegex(RuntimeError, "content mismatch"):
            RESUME.resume_one(self.client(b"invalid response"), self.root, self.row)
        self.assertFalse((self.root / self.row["localPath"]).exists())

    def test_manifest_path_escape_is_rejected_before_http(self):
        client = self.client(self.body)
        for path in ("../outside.txt", "/tmp/outside.txt", "raw/../../outside.txt", "raw\\outside.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                RESUME.resume_one(client, self.root, {**self.row, "localPath": path})
        self.assertEqual(0, client.calls)

    def test_duplicate_manifest_paths_are_rejected(self):
        (self.root / "filings.jsonl").write_text(
            json.dumps(self.row) + "\n" + json.dumps(self.row) + "\n", encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate filing path"):
            RESUME.load_rows(self.root)


if __name__ == "__main__":
    unittest.main()
