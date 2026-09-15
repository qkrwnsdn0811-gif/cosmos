from datetime import datetime, timezone
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("dart_service", SCRIPTS / "run_dart_hdfs_service.py")
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


def fixture(active, receipt, state="collected"):
    shard = active / "shard-51-51"
    (shard / "companies").mkdir(parents=True)
    (shard / "raw/documents").mkdir(parents=True)
    (shard / "state/documents").mkdir(parents=True)
    (shard / "progress.json").write_text(json.dumps({"lists_complete": True}), encoding="utf-8")
    row = {"rcept_no": receipt, "rcept_dt": "20200101"}
    with (shard / "companies/051_company.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    if state == "collected":
        (shard / "companies/051_company_detail.jsonl").write_text(json.dumps({**row, "files": [{"text": "body"}]}) + "\n", encoding="utf-8")
        with zipfile.ZipFile(shard / "raw/documents" / (receipt + ".zip"), "w") as archive:
            archive.writestr("body.xml", "body")
    else:
        (shard / "state/documents" / (receipt + ".json")).write_text(json.dumps({"status": state}), encoding="utf-8")
    return shard


class ServiceTests(unittest.TestCase):
    def test_quota_retry_uses_next_korean_day_at_0005(self):
        now = datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc)
        retry = service.next_quota_retry(now)
        self.assertEqual(retry.isoformat(), "2026-09-11T00:05:00+09:00")

    def test_only_named_source_damage_is_terminal_exception(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(service, "SHARDS", ((51, 51),)):
            active = Path(folder)
            fixture(active, "20200305000488", "parse_error")
            status = service.assess_collection(active)
            self.assertEqual(status["source_exception_receipts"], ["20200305000488"])
            self.assertEqual(status["actionable_pending"], 0)
            # A subset fixture must never satisfy the required 50-company completeness.
            self.assertFalse(status["terminal"])

    def test_detail_without_original_is_pending(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(service, "SHARDS", ((51, 51),)):
            active = Path(folder)
            shard = fixture(active, "20260101000001")
            original = shard / "raw/documents/20260101000001.zip"
            original.unlink()
            status = service.assess_collection(active)
            self.assertEqual(status["collected"], 0)
            self.assertEqual(status["actionable_pending"], 1)

    def test_unnamed_errors_remain_actionable(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(service, "SHARDS", ((51, 51),)):
            active = Path(folder)
            fixture(active, "20260101000001", "parse_error")
            status = service.assess_collection(active)
            self.assertEqual(status["actionable_pending"], 1)
            self.assertEqual(status["source_exception_receipts"], [])

    def test_verified_source_error_requires_exact_preserved_bytes(self):
        receipt = "20200305000085"
        content = b"synthetic damaged ZIP fixture"
        expected = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as folder, patch.object(service, "SHARDS", ((51, 51),)), \
                patch.object(service, "VERIFIED_SOURCE_ZIP_ERRORS", {receipt: expected}):
            active = Path(folder)
            shard = fixture(active, receipt, "error")
            raw = shard / "raw/documents" / (receipt + ".zip")
            self.assertEqual(service.assess_collection(active)["actionable_pending"], 1)
            raw.write_bytes(content)
            status = service.assess_collection(active)
            self.assertEqual(status["actionable_pending"], 0)
            self.assertEqual(status["collected"], 0)
            self.assertEqual(status["source_exception_receipts"], [receipt])
            raw.write_bytes(b"different bytes must remain actionable")
            self.assertEqual(service.assess_collection(active)["actionable_pending"], 1)

    def test_child_environment_ignores_inherited_api_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"DART_API_KEY": "placeholder"}):
            runner = service.Service(folder, ".env.dart.final.local", "20260909")
            self.assertNotIn("DART_API_KEY", runner.env)


if __name__ == "__main__":
    unittest.main()
