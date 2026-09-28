import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
for name in ("collect_sec_edgar_nasdaq100", "run_sec_incremental"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
RUN = sys.modules["run_sec_incremental"]


class FakeHdfs:
    def __init__(self):
        self.files = {}
        self.dirs = set()
        self.put_count = 0
        self.fail_put = None
        self.fail_after_rename = False

    def exists(self, path):
        return path in self.files or path in self.dirs

    def mkdir(self, path):
        self.dirs.add(path)

    def put(self, local, target):
        self.put_count += 1
        if self.put_count == self.fail_put:
            raise RuntimeError("synthetic transport interruption")
        self.files[target] = Path(local).read_bytes()

    def read(self, path):
        return self.files[path]

    def checksum(self, path):
        value = self.files[path]
        return len(value), RUN.digest(value)

    def count(self, path):
        values = [value for key, value in self.files.items() if key.startswith(path + "/")]
        return 1, len(values), sum(map(len, values))

    def rename(self, source, target):
        if self.exists(target):
            raise RuntimeError("destination exists")
        self.dirs.remove(source)
        self.dirs.add(target)
        for path in list(self.files):
            if path.startswith(source + "/"):
                self.files[target + path[len(source):]] = self.files.pop(path)
        if self.fail_after_rename:
            self.fail_after_rename = False
            raise RuntimeError("synthetic lost rename acknowledgement")


class FakeSec:
    def __init__(self):
        self.company = {"cik": "123", "cik10": "0000000123", "name": "SYNTHETIC TEST COMPANY", "symbols": ["TEST"]}
        self.accession = "0000000123-26-000001"
        self.body = (f"<SEC-DOCUMENT>{self.accession}.txt\n<SEC-HEADER>\nACCESSION NUMBER: {self.accession}\n"
                     "</SEC-HEADER>\n<DOCUMENT>Offline synthetic filing fixture only.</DOCUMENT>\n</SEC-DOCUMENT>").encode()
        self.body_calls = 0
        self.submission = {"cik": 123, "filings": {"recent": {
            "accessionNumber": [self.accession], "filingDate": ["2026-09-10"], "form": ["10-Q"],
        }, "files": []}}

    def get_json(self, url):
        return self.submission

    def get_bytes(self, url, allow_404=False):
        self.body_calls += 1
        return self.body


class IncrementalSecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = RUN.State(self.root, {"test_contract": "synthetic"})
        self.addCleanup(self.state.close)
        self.sec = FakeSec()
        self.hdfs = FakeHdfs()
        self.publisher = RUN.Publisher(self.hdfs, RUN.HDFS_ROOT)

    def cycle(self, **kwargs):
        return RUN.run_cycle(self.state, [self.sec.company], self.sec, self.publisher,
                             start="2026-09-09", end="2026-09-15", forms={"10-Q"}, **kwargs)

    def test_repeated_discovery_publishes_one_accession_once(self):
        first = self.cycle()
        self.assertEqual("complete", first["state"])
        self.assertEqual({"published": 1}, first["counts"])
        before = dict(self.hdfs.files)
        second = self.cycle()
        self.assertEqual(0, second["discovered_new"])
        self.assertEqual(0, second["published_this_run"])
        self.assertEqual(1, self.sec.body_calls)
        self.assertEqual(before, self.hdfs.files)
        self.assertEqual([], list((self.root / "spool").rglob("raw.txt")))

    def test_partial_hdfs_upload_resumes_without_redownloading_or_duplicates(self):
        self.hdfs.fail_put = 2
        self.assertEqual("needs_attention", self.cycle()["state"])
        self.assertEqual({"pending": 1}, self.state.counts())
        result = self.cycle()
        self.assertEqual("complete", result["state"])
        self.assertEqual(1, self.sec.body_calls)
        self.assertEqual(4, len(self.hdfs.files))

    def test_lost_publish_ack_recovers_hdfs_then_marks_sqlite(self):
        self.hdfs.fail_after_rename = True
        first = self.cycle()
        self.assertEqual("needs_attention", first["state"])
        self.assertEqual({"pending": 1}, self.state.counts())
        second = self.cycle()
        self.assertEqual("complete", second["state"])
        self.assertEqual(1, second["recovered_this_run"])
        self.assertEqual(1, self.sec.body_calls)
        self.assertEqual(4, self.hdfs.put_count)

    def test_corrupt_local_payload_is_not_promoted_with_a_new_hash(self):
        self.hdfs.fail_put = 1
        self.cycle()
        raw = next((self.root / "spool").rglob("raw.txt"))
        raw.write_bytes(b"x" * raw.stat().st_size)
        result = self.cycle()
        self.assertEqual("needs_attention", result["state"])
        self.assertEqual({"pending": 1}, result["counts"])
        self.assertEqual(1, self.sec.body_calls)
        self.assertFalse(any(path.endswith("/_SUCCESS") for path in self.hdfs.files))

    def test_corrupt_hdfs_payload_blocks_recovery(self):
        self.hdfs.fail_after_rename = True
        self.cycle()
        raw = next(path for path in self.hdfs.files if path.endswith("/raw.txt"))
        self.hdfs.files[raw] = b"corrupt"
        result = self.cycle()
        self.assertEqual("needs_attention", result["state"])
        self.assertEqual({"pending": 1}, result["counts"])

    def test_html_block_page_is_never_published(self):
        self.sec.body = b"<html>Request blocked. Please try later.</html>" * 10
        self.assertEqual("needs_attention", self.cycle()["state"])
        self.assertEqual({}, self.hdfs.files)

    def test_malformed_submission_is_not_reported_as_empty_success(self):
        self.sec.submission = {"cik": 123, "filings": {}}
        result = self.cycle()
        self.assertEqual("needs_attention", result["state"])
        self.assertEqual(0, result["companies_checked"])
        self.assertEqual(0, self.sec.body_calls)

    def test_source_403_stops_cycle_without_fallback(self):
        with patch.object(self.sec, "get_json", side_effect=RUN.CollectorError("HTTP 403")):
            result = self.cycle()
        self.assertEqual("blocked_source_access", result["state"])
        self.assertEqual(0, self.sec.body_calls)
        self.assertEqual({}, self.hdfs.files)

    def test_new_filing_on_same_day_is_discovered_next_cycle(self):
        self.cycle()
        self.sec.accession = "0000000123-26-000002"
        self.sec.body = self.sec.body.replace(b"0000000123-26-000001", self.sec.accession.encode())
        self.sec.submission["filings"]["recent"]["accessionNumber"] = [self.sec.accession]
        result = self.cycle()
        self.assertEqual(1, result["discovered_new"])
        self.assertEqual({"published": 2}, result["counts"])

    def test_changed_contract_does_not_reset_existing_state(self):
        self.cycle()
        with self.assertRaisesRegex(ValueError, "State contract changed"):
            RUN.State(self.root, {"test_contract": "different"})
        self.assertEqual({"published": 1}, self.state.counts())

    def test_native_library_warning_does_not_turn_missing_path_into_error(self):
        cli = RUN.HdfsCli("hdfs")
        warning = b"WARN util.NativeCodeLoader: Unable to load native-hadoop library for your platform... using builtin-java classes where applicable\n"
        with patch.object(cli, "call", return_value=subprocess.CompletedProcess([], 1, b"", warning)):
            self.assertFalse(cli.exists("/missing"))
        with patch.object(cli, "call", return_value=subprocess.CompletedProcess([], 1, b"", b"Connection refused")):
            with self.assertRaises(RuntimeError):
                cli.exists("/missing")


if __name__ == "__main__":
    unittest.main()
