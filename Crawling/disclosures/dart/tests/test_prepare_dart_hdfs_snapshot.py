import csv
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("snapshot", Path(__file__).resolve().parents[1] / "scripts/prepare_dart_hdfs_snapshot.py")
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


class SnapshotTests(unittest.TestCase):
    def test_append_cutoff_deduplication_and_zip_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            company = source / "shard-51-68/companies"
            raw = source / "shard-51-68/raw/documents"
            company.mkdir(parents=True)
            raw.mkdir(parents=True)
            rows = [{"rcept_no": f"2020010100000{i}", "corp_code": "00000001", "corp_name": "Test", "report_nm": "Report", "rcept_dt": "20200101"} for i in range(1, 4)]
            with (company / "051_test.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("text.xml", "exact original bytes")
            original = payload.getvalue()
            for row in rows:
                (raw / (row["rcept_no"] + ".zip")).write_bytes(original)
                row["files"] = [{"filename": "text.xml", "text": "text"}]
            detail = company / "051_test_detail.jsonl"
            detail.write_bytes(snapshot.encoded(rows[0]) * 2 + snapshot.encoded(rows[1])[:-2])
            captured = snapshot.capture_details(source)
            with detail.open("ab") as stream:
                stream.write(b"}\n" + snapshot.encoded(rows[2]))
            output = base / "output"
            result = snapshot.prepare(source, output, captured=captured)
            self.assertEqual(result["snapshot_documents"], 1)
            self.assertEqual(result["duplicate_detail_rows_skipped"], 1)
            self.assertGreater(result["captured_detail_boundaries"][0]["excluded_tail_bytes"], 0)
            with gzip.open(output / "data/051_test_detail.jsonl.gz", "rt", encoding="utf-8") as stream:
                parsed = [json.loads(line) for line in stream]
            self.assertEqual([row["rcept_no"] for row in parsed], [rows[0]["rcept_no"]])
            with tarfile.open(output / "raw/documents-part-00001.tar") as archive:
                self.assertEqual(len(archive.getmembers()), 1)
                self.assertEqual(archive.extractfile(archive.getmembers()[0]).read(), original)
            self.assertEqual(json.loads((output / "ready.json").read_bytes())["status"], "ready")
            self.assertEqual((output / "data/051_test_detail.jsonl.gz").read_bytes()[4:8], b"\x00" * 4)
            for entry in json.loads((output / "checksums.json").read_bytes())["files"]:
                self.assertEqual(snapshot.digest(output / entry["path"]), entry["sha256"])

    def test_secret_filter_does_not_include_secret_in_error(self):
        with self.assertRaisesRegex(ValueError, "Credential text detected") as caught:
            snapshot.check_secret(b"text private-test-key text", [b"private-test-key"])
        self.assertNotIn("private-test-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
