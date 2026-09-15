import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.prepare_dart_recovery_delta import prepare_delta, load_baseline


class RecoveryDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "historical"
        self.companies = self.source / "shard-51-68/companies"
        self.raw = self.source / "shard-51-68/raw/documents"
        self.companies.mkdir(parents=True)
        self.raw.mkdir(parents=True)
        self.csv = self.companies / "051_test.csv"
        self.details = self.companies / "051_test_detail.jsonl"
        self.baseline = self.root / "baseline.jsonl"
        self.output = self.root / "delta"
        self.report = self.root / "audit.json"
        self.rows = [dict(rcept_no=f"202001010000{i:02d}", corp_code="00000001", corp_name="Test",
                         stock_code="123456", universe_rank=51, report_nm="Report", rcept_dt="20200101")
                     for i in range(1, 5)]

    def populate(self, details=(0, 1, 2), baseline=(0,), copies=(0, 1, 2)):
        with self.csv.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader(); writer.writerows(self.rows)
        values = []
        for i in details:
            values.append({**self.rows[i], "files": [{"filename": "report.xml", "text": "source document"}]})
        self.details.write_bytes(b"".join(self.line(row) for row in values))
        self.baseline.write_bytes(b"".join(self.line({"rcept_no": self.rows[i]["rcept_no"]}) for i in baseline))
        for i in copies:
            with zipfile.ZipFile(self.raw / (self.rows[i]["rcept_no"] + ".zip"), "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("report.xml", "<DOCUMENT><P>source document</P></DOCUMENT>")

    @staticmethod
    def line(row):
        return (json.dumps(row, ensure_ascii=False) + "\n").encode()

    def run_delta(self, **kwargs):
        return prepare_delta(self.source, self.baseline, self.output, self.report, end="20200101", **kwargs)

    def source_hashes(self):
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [self.baseline, *self.source.rglob("*")] if path.is_file()}

    def test_exact_difference_preserves_originals_and_matches_snapshot_hashes(self):
        self.populate()
        before = self.source_hashes()
        baseline_sha = hashlib.sha256(self.baseline.read_bytes()).hexdigest()
        result = self.run_delta(expected_baseline_sha256=baseline_sha, expected_baseline_count=1)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["counts"]["valid_collected_receipts"], 3)
        self.assertEqual(result["counts"]["baseline_receipts_skipped"], 1)
        self.assertEqual(result["counts"]["included_delta_receipts"], 2)
        self.assertEqual(result["selected_without_valid_detail"], [self.rows[3]["rcept_no"]])
        self.assertEqual(result["baseline"]["sha256"], baseline_sha)
        with gzip.open(self.output / "data/051_test_detail.jsonl.gz", "rt", encoding="utf-8") as stream:
            actual = [json.loads(line)["rcept_no"] for line in stream]
        self.assertEqual(actual, [self.rows[1]["rcept_no"], self.rows[2]["rcept_no"]])
        self.assertEqual(before, self.source_hashes())
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        self.assertEqual(manifest["snapshot_documents"], 2)
        self.assertEqual(manifest["not_in_snapshot"], 0)
        self.assertTrue((self.output / "ready.json").is_file())

    def test_baseline_ids_are_skipped_before_raw_validation(self):
        self.populate()
        (self.raw / (self.rows[0]["rcept_no"] + ".zip")).write_bytes(b"old baseline bytes are not read")
        result = self.run_delta()
        self.assertEqual(result["counts"].get("raw_zip_exclusions", 0), 0)
        self.assertEqual(result["counts"]["included_delta_receipts"], 2)

    def test_same_size_crc_corruption_is_reported_and_not_published(self):
        self.populate()
        path = self.raw / (self.rows[1]["rcept_no"] + ".zip")
        content = path.read_bytes()
        damaged = content.replace(b"source document", b"broken document")
        self.assertEqual(len(content), len(damaged))
        path.write_bytes(damaged)
        self.assertTrue(zipfile.is_zipfile(path))
        before = self.source_hashes()
        result = self.run_delta()
        self.assertEqual(result["status"], "ready_with_exclusions")
        self.assertEqual(result["counts"]["raw_zip_exclusions"], 1)
        self.assertEqual(result["included_receipts"], [self.rows[2]["rcept_no"]])
        self.assertEqual(result["excluded"][0]["receipt"], self.rows[1]["rcept_no"])
        self.assertEqual(before, self.source_hashes())

    def test_missing_raw_and_missing_detail_are_separate_counts(self):
        self.populate(copies=(0, 2))
        result = self.run_delta()
        self.assertEqual(result["counts"]["raw_zip_exclusions"], 1)
        self.assertEqual(result["counts"]["selected_without_valid_detail"], 1)
        self.assertEqual(result["snapshot_documents"], 1)

    def test_broken_deflate_stream_is_excluded_without_aborting_other_receipts(self):
        self.populate()
        path = self.raw / (self.rows[1]["rcept_no"] + ".zip")
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("report.xml", "<DOCUMENT>source document</DOCUMENT>")
        damaged = bytearray(path.read_bytes())
        offset = 30 + len("report.xml")
        damaged[offset] |= 0x06  # Reserved DEFLATE block type, while central directory stays valid.
        path.write_bytes(damaged)
        self.assertTrue(zipfile.is_zipfile(path))
        result = self.run_delta()
        self.assertEqual(result["status"], "ready_with_exclusions")
        self.assertEqual(result["counts"]["raw_zip_exclusions"], 1)
        self.assertEqual(result["snapshot_documents"], 1)

    def test_all_delta_raw_missing_is_not_a_successful_empty_scan(self):
        self.populate(details=(0, 1), copies=(0,))
        result = self.run_delta()
        self.assertEqual(result["status"], "empty_with_exclusions")
        self.assertEqual(result["counts"]["candidate_delta_receipts"], 1)
        self.assertEqual(result["counts"]["raw_zip_exclusions"], 1)
        self.assertFalse(self.output.exists())

    def test_incomplete_final_line_is_excluded_without_repairing_source(self):
        self.populate(details=(0, 1))
        with self.details.open("ab") as stream:
            stream.write(self.line({**self.rows[2], "files": [{"filename": "report.xml", "text": "body"}]})[:-2])
        before = self.details.read_bytes()
        result = self.run_delta()
        self.assertEqual(result["counts"]["incomplete_tail_files"], 1)
        self.assertEqual(result["snapshot_documents"], 1)
        self.assertEqual(before, self.details.read_bytes())

    def test_duplicate_or_wrong_baseline_is_rejected_before_output(self):
        self.populate()
        self.baseline.write_bytes(self.baseline.read_bytes() * 2)
        with self.assertRaisesRegex(ValueError, "Duplicate baseline"):
            self.run_delta()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.report.exists())
        self.populate()
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.run_delta(expected_baseline_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "count"):
            self.run_delta(expected_baseline_count=2)

    def test_identical_detail_duplicate_is_skipped_but_conflict_fails(self):
        self.populate(details=(0, 1, 1))
        result = self.run_delta()
        self.assertEqual(result["snapshot_documents"], 1)
        self.assertEqual(result["counts"]["identical_duplicate_detail_rows"], 1)

    def test_conflicting_detail_duplicate_does_not_create_ready_snapshot(self):
        self.populate(details=(0, 1))
        with self.details.open("ab") as stream:
            stream.write(self.line({**self.rows[1], "files": [{"filename": "report.xml", "text": "changed body"}]}))
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            self.run_delta()
        self.assertFalse((self.output / "ready.json").exists())
        self.assertEqual(json.loads(self.report.read_bytes())["status"], "failed")

    def test_empty_delta_does_not_publish_fake_snapshot(self):
        self.populate(details=(0,), copies=(0,))
        result = self.run_delta()
        self.assertEqual(result["status"], "empty")
        self.assertFalse(self.output.exists())

    def test_outputs_inside_historical_input_are_rejected(self):
        self.populate()
        with self.assertRaisesRegex(ValueError, "historical"):
            prepare_delta(self.source, self.baseline, self.source / "delta", self.report)
        with self.assertRaisesRegex(ValueError, "separate"):
            prepare_delta(self.source, self.baseline, self.output, self.output / "report.json")

    def test_invalid_detail_body_is_not_assumed_collected(self):
        self.populate(details=(0,))
        with self.details.open("ab") as stream:
            stream.write(self.line({**self.rows[1], "files": []}))
        result = self.run_delta()
        self.assertEqual(result["status"], "empty_with_exclusions")
        self.assertEqual(result["counts"]["missing_or_empty_collected_detail"], 1)

    def test_secret_filter_never_marks_ready_or_discloses_secret(self):
        self.populate()
        with self.assertRaises(ValueError) as raised:
            self.run_delta(secrets=(b"source document",))
        self.assertNotIn("source document", str(raised.exception))
        self.assertNotIn("source document", self.report.read_text(encoding="utf-8"))
        self.assertFalse((self.output / "ready.json").exists())


if __name__ == "__main__":
    unittest.main()
