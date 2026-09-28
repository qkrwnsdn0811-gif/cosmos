import csv
import gzip
import io
import json
from pathlib import Path
import sys
import struct
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from import_dart_zip_hdfs import prepare, members_checked
from upload_dart_hdfs_snapshot import digest, load_snapshot


class ImportDartZipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "input.zip"
        self.output = self.root / "package"
        self.scan = self.root / "scan.json"

    def tearDown(self):
        self.temporary.cleanup()

    def row(self, receipt, date):
        return {"rcept_no": receipt, "corp_code": "00000001", "corp_name": "회사",
                "rcept_dt": date, "report_nm": "보고서", "files": [{"filename": "a.xml", "text": "본문"}]}

    def create(self, rows, csv_rows=None, extra=None):
        csv_rows = csv_rows if csv_rows is not None else rows
        text = io.StringIO(newline="")
        writer = csv.DictWriter(text, fieldnames=["rcept_no", "corp_code", "corp_name", "rcept_dt", "report_nm"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(csv_rows)
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("회사.csv", text.getvalue().encode("utf-8-sig"))
            archive.writestr("회사_detail.jsonl", ("\ufeff" + "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode("utf-8"))
            archive.writestr("api_quota.json", '{"date":"20260909","count":15}')
            if extra:
                archive.writestr(extra, "extra")
        self.scan.write_text(json.dumps({"archive_sha256": digest(self.archive), "crc_check": "passed", "keys_checked": 1, "matches": 0}), encoding="utf-8")

    def test_preserves_raw_bom_bodies_and_csv_body_union_with_periods_and_overlap(self):
        old = self.row("20150101000001", "20150101")
        recent = self.row("20260908000002", "20260908")
        missing = self.row("20260907000003", "20260907")
        self.create([old, recent], [old, missing])
        reference = self.root / "reference.jsonl"
        reference.write_text(json.dumps({"rcept_no": recent["rcept_no"]}) + "\n", encoding="utf-8")
        source, manifest = prepare(self.archive, self.output, self.scan, reference)
        self.assertEqual((manifest["selected_documents"], manifest["snapshot_documents"], manifest["not_in_snapshot"]), (3, 2, 1))
        self.assertEqual(manifest["csv_documents"], 2)
        self.assertEqual(manifest["body_not_in_csv"], 1)
        self.assertEqual(manifest["body_period_counts"], {"before-2016": 1, "2016-onward": 1})
        self.assertEqual(manifest["overlap_with_reference_selection"], 1)
        self.assertEqual(self.archive.read_bytes(), (source / "raw/dart.zip").read_bytes())
        self.assertEqual(json.loads(gzip.decompress((source / "data/period=before-2016/00000001_detail.jsonl.gz").read_bytes())), old)
        self.assertEqual(json.loads(gzip.decompress((source / "data/period=2016-onward/00000001_detail.jsonl.gz").read_bytes())), recent)
        self.assertEqual(json.loads((source / "metadata/missing_body_receipts.json").read_text()), [missing["rcept_no"]])
        # Same verifier used on the host must accept the prepared inventory.
        _, verified, entries = load_snapshot(source)
        self.assertEqual(verified["snapshot_documents"], 2)
        self.assertIn("raw/dart.zip", entries)
        self.assertFalse((source / "api_quota.json").exists())

    def test_duplicate_body_rejected_without_ready_marker(self):
        row = self.row("20260908000002", "20260908")
        self.create([row, row], [row])
        with self.assertRaisesRegex(ValueError, "Duplicate body receipt"):
            prepare(self.archive, self.output, self.scan)
        self.assertFalse((self.output / "ready.json").exists())

    def test_path_escape_rejected(self):
        row = self.row("20260908000002", "20260908")
        self.create([row], extra="../outside.csv")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            prepare(self.archive, self.output, self.scan)
        self.assertFalse((self.root / "outside.csv").exists())

    def test_credential_attestation_bound_to_archive_bytes(self):
        self.create([self.row("20260908000002", "20260908")])
        self.archive.write_bytes(self.archive.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "attestation"):
            prepare(self.archive, self.output, self.scan)
        self.assertFalse(self.output.exists())

    def test_legacy_cp949_filename_decoding_uses_zipinfo(self):
        class Archive:
            def infolist(self):
                item = zipfile.ZipInfo("회사.csv".encode("cp949").decode("cp437"))
                item.flag_bits = 0
                return [item]
        checked = members_checked(Archive())
        self.assertEqual(list(checked), ["회사.csv"])
        self.assertIsInstance(checked["회사.csv"], zipfile.ZipInfo)

    def write_legacy_unicode_path_fixture(self, unicode_name):
        # Exact encoding shape of the supplied dart.zip: flag 0, CP949 header
        # bytes, and a CRC-bound Info-ZIP Unicode Path (0x7075) extra field.
        original_name = "HD한국조선해양.csv"
        raw_name = original_name.encode("cp949")

        class LegacyZipInfo(zipfile.ZipInfo):
            def _encodeFilenameFlags(self):
                return raw_name, 0

        info = LegacyZipInfo(raw_name.decode("cp437"))
        payload = struct.pack("<BL", 1, zlib.crc32(raw_name)) + unicode_name.encode("utf-8")
        info.extra = struct.pack("<HH", 0x7075, len(payload)) + payload
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr(info, b"metadata fixture")
        return original_name

    def test_actual_dart_filename_with_unicode_path_extra_reads_without_reencoding_unicode(self):
        name = self.write_legacy_unicode_path_fixture("HD한국조선해양.csv")
        with zipfile.ZipFile(self.archive) as archive:
            entry = archive.infolist()[0]
            self.assertEqual(entry.flag_bits, 0)
            self.assertEqual(entry.orig_filename.encode("cp437"), name.encode("cp949"))
            checked = members_checked(archive)
            self.assertEqual(list(checked), [name])
            self.assertIs(checked[name], entry)
            self.assertEqual(archive.read(checked[name]), b"metadata fixture")

    def test_unicode_path_extra_cannot_hide_path_escape(self):
        self.write_legacy_unicode_path_fixture("../outside.csv")
        with zipfile.ZipFile(self.archive) as archive:
            # Older Python releases do not apply 0x7075. Simulate the modern
            # ZipInfo value to exercise both header and Unicode path checks.
            archive.infolist()[0].filename = "../outside.csv"
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                members_checked(archive)


if __name__ == "__main__":
    unittest.main()
