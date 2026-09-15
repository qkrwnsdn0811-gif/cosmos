"""Real local Parquet validation and a deterministic, network-free HDFS model."""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.stock_prices.collector import export_run
from services.stock_prices.hdfs import DEFAULT_ROOT, HdfsCli, publish_snapshot, validate_snapshot
from services.stock_prices.store import PriceStore, dumps


URI = "hdfs://namenode:9000"
COMPANY = {"market": "KOSPI", "ticker": "0126Z0", "name": "테스트 회사"}


class FakeHdfs:
    def __init__(self):
        self.files, self.directories, self.events = {}, set(), []
        self.fail_put = None
        self.corrupt_put = None

    def exists(self, path):
        return path in self.directories or path in self.files

    def mkdir(self, path, *, parents=False):
        self.events.append(("mkdir", path))
        if not parents and self.exists(path):
            raise RuntimeError("directory_exists")
        self.directories.add(path)

    def put(self, source, destination):
        self.events.append(("put", destination))
        if destination.endswith("/" + str(self.fail_put)):
            raise RuntimeError("injected_upload_failure")
        if self.exists(destination):
            raise RuntimeError("file_exists")
        value = Path(source).read_bytes()
        if destination.endswith("/" + str(self.corrupt_put)):
            value = b"x" * len(value)
        self.files[destination] = value

    def sizes(self, root):
        return {path[len(root) + 1:]: len(value) for path, value in self.files.items() if path.startswith(root + "/")}

    def sha256(self, path):
        return hashlib.sha256(self.files[path]).hexdigest()

    def rename_new(self, source, destination):
        self.events.append(("rename", source, destination))
        if self.exists(destination):
            raise RuntimeError("destination_exists")
        if source + "/_SUCCESS" not in self.files:
            raise AssertionError("cannot expose incomplete snapshot")
        values = {destination + path[len(source):]: value for path, value in self.files.items()
                  if path.startswith(source + "/")}
        self.files = {path: value for path, value in self.files.items() if not path.startswith(source + "/")}
        self.files.update(values)
        self.directories.remove(source)
        self.directories.add(destination)

    def rmdir(self, path):
        self.events.append(("rmdir", path))
        if self.sizes(path):
            raise AssertionError("cannot delete nonempty directory")
        self.directories.remove(path)


class HdfsPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = PriceStore(self.root / "state.db")
        self.addCleanup(self.store.close)
        start, end = date(2020, 3, 1), date(2020, 3, 4)
        base = {"market": COMPANY["market"], "ticker": COMPANY["ticker"], "interval_type": "1D",
                "open_price": 100, "high_price": 110, "low_price": 90, "close_price": 105,
                "trading_volume": 123, "currency": "KRW", "adj_close": None,
                "provider": "fixture", "price_basis": "fixture_adjusted", "quality_flags": []}
        rows = [{**base, "trading_date": "2020-03-02"}, {**base, "trading_date": "2020-03-04"},
                {**base, "trading_date": "2020-03-03", "quality_flags": ["source_missing_ohlcv"],
                 **{key: None for key in ("open_price", "high_price", "low_price", "close_price", "trading_volume", "adj_close")}}]
        self.store.begin(COMPANY)
        summary = self.store.replace(COMPANY, start, end, rows)
        outcome = {**COMPANY, **summary, "status": "collected", "requested_start": start.isoformat(),
                   "requested_end": end.isoformat(), "source_checked_at": datetime.now(timezone.utc).isoformat()}
        self.source, self.manifest = export_run(self.store, [COMPANY], {"KOSPI": (start, end)}, [outcome], self.root / "exports")
        self.hdfs = FakeHdfs()

    def save_manifest(self):
        (self.source / "manifest.json").write_text(dumps(self.manifest), encoding="utf-8")

    def publish(self, **kwargs):
        return publish_snapshot(self.source, hdfs_uri=URI, hdfs=self.hdfs, **kwargs)

    def test_default_dry_run_validates_real_parquet_without_hdfs_access(self):
        result = self.publish()
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["quarantined_rows"], 1)
        self.assertEqual(result["first_date"], "2020-03-02")
        self.assertEqual(result["as_of"], "2020-03-04")
        self.assertEqual(result["source"], str(self.source))
        self.assertEqual(result["sha"], self.manifest["sha256"])
        self.assertIn("/market=KOSPI/as_of=2020-03-04/snapshot=", result["destination"])
        self.assertEqual(self.hdfs.events, [])

    def test_atomic_publication_preserves_original_prices_manifest_selection_and_quarantine(self):
        result = self.publish(publish=True)
        destination = result["destination"]
        self.assertEqual(result["status"], "published")
        for name in ("prices_daily.parquet", "manifest.json", "selection.json", "quarantine.jsonl"):
            self.assertEqual(self.hdfs.files[destination + "/" + name], (self.source / name).read_bytes())
        publication_bytes = self.hdfs.files[destination + "/publication.json"]
        publication = json.loads(publication_bytes)
        success = json.loads(self.hdfs.files[destination + "/_SUCCESS"])
        self.assertEqual(success["publication_sha256"], hashlib.sha256(publication_bytes).hexdigest())
        self.assertEqual(publication["providers"], ["fixture"])
        self.assertNotIn("source", publication)
        self.assertEqual(len(self.hdfs.sizes(destination)), 6)
        self.assertEqual(sum(event[0] == "rename" for event in self.hdfs.events), 1)
        self.assertFalse(any("/.locks/" in directory for directory in self.hdfs.directories))

    def test_historical_range_and_zero_ohl_volume_flags_preserve_raw_data_through_export_and_hdfs(self):
        import pyarrow.parquet as pq
        start, end = date(2020, 3, 1), date(2020, 3, 4)
        original = json.loads((self.source / "quarantine.jsonl").read_text(encoding="utf-8"))
        examples = [
            {"quality_flags": ["source_inconsistent_ohlc"], "open_price": 120, "high_price": 110,
             "low_price": 90, "close_price": 105, "trading_volume": 123},
            {"quality_flags": ["source_zero_ohl_with_volume"], "open_price": 0, "high_price": 0,
             "low_price": 0, "close_price": 105, "trading_volume": 321},
        ]
        for example in examples:
            with self.subTest(flag=example["quality_flags"]):
                excluded = {**original, **example, "source_note": "원천 수치 그대로 보존"}
                good = self.store.rows(COMPANY, start, end)
                self.store.begin(COMPANY)
                summary = self.store.replace(COMPANY, start, end, [*good, excluded])
                outcome = {**self.manifest["outcomes"][0], **summary,
                           "source_checked_at": datetime.now(timezone.utc).isoformat()}
                self.source, self.manifest = export_run(self.store, [COMPANY], {"KOSPI": (start, end)},
                                                       [outcome], self.root / "exports")
                result = self.publish(publish=True)
                raw_bytes = (self.source / "quarantine.jsonl").read_bytes()
                self.assertEqual(json.loads(raw_bytes), excluded)
                self.assertEqual(self.hdfs.files[result["destination"] + "/quarantine.jsonl"], raw_bytes)
                self.assertEqual(result["rows"], 2)
                self.assertEqual(result["quarantined_rows"], 1)
                self.assertEqual(pq.read_table(self.source / "prices_daily.parquet", columns=["trading_date"])
                                 .column("trading_date").to_pylist(), ["2020-03-02", "2020-03-04"])

    def test_false_historical_quarantine_flags_are_rejected_without_changing_existing_prices(self):
        start, end = date(2020, 3, 1), date(2020, 3, 4)
        original = json.loads((self.source / "quarantine.jsonl").read_text(encoding="utf-8"))
        good = self.store.rows(COMPANY, start, end)
        examples = [
            # A valid candle is not inconsistent.
            {"quality_flags": ["source_inconsistent_ohlc"], "open_price": 100, "high_price": 110,
             "low_price": 90, "close_price": 105, "trading_volume": 123},
            # The existing one-won exception retains its own exact flag.
            {"quality_flags": ["source_inconsistent_ohlc"], "open_price": 111, "high_price": 110,
             "low_price": 90, "close_price": 105, "trading_volume": 123},
            {"quality_flags": ["source_zero_ohl_with_volume"], "open_price": 0, "high_price": 0,
             "low_price": 0, "close_price": 105, "trading_volume": 0},
            {"quality_flags": ["source_zero_ohl_with_volume"], "open_price": 1, "high_price": 0,
             "low_price": 0, "close_price": 105, "trading_volume": 123},
        ]
        for example in examples:
            with self.subTest(example=example):
                excluded = {**original, **example}
                with self.assertRaisesRegex(ValueError, "unsupported_source_quality_flag"):
                    self.store.replace(COMPANY, start, end, [*good, excluded])
                self.assertEqual(self.store.rows(COMPANY, start, end), good)
                self.assertEqual(self.store.quarantined_rows(COMPANY, start, end), [original])
                quarantine = self.source / "quarantine.jsonl"
                quarantine.write_text(dumps(excluded) + "\n", encoding="utf-8")
                self.manifest["quarantine_sha256"] = hashlib.sha256(quarantine.read_bytes()).hexdigest()
                self.save_manifest()
                with self.assertRaisesRegex(ValueError, "invalid_quarantine_record"):
                    self.publish(publish=True)
        self.assertEqual(self.hdfs.events, [])

    def test_infinite_halt_close_is_rejected_in_store_and_hdfs_even_with_matching_hash(self):
        start, end = date(2020, 3, 1), date(2020, 3, 4)
        original = json.loads((self.source / "quarantine.jsonl").read_text(encoding="utf-8"))
        excluded = {**original, "quality_flags": ["halted_ohl_zero"], "open_price": 0,
                    "high_price": 0, "low_price": 0, "close_price": float("inf"), "trading_volume": 0}
        good = self.store.rows(COMPANY, start, end)
        with self.assertRaisesRegex(ValueError, "unsupported_source_quality_flag"):
            self.store.replace(COMPANY, start, end, [*good, excluded])
        self.assertEqual(self.store.rows(COMPANY, start, end), good)
        quarantine = self.source / "quarantine.jsonl"
        # A huge JSON exponent is syntactically valid but overflows to infinity;
        # this exercises the shared quarantine helper, not only JSON constants.
        invalid_json = json.dumps(excluded).replace("Infinity", "1e999")
        quarantine.write_text(invalid_json + "\n", encoding="utf-8")
        self.manifest["quarantine_sha256"] = hashlib.sha256(quarantine.read_bytes()).hexdigest()
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "invalid_quarantine_record"):
            self.publish(publish=True)
        self.assertEqual(self.hdfs.events, [])

    def test_replay_verifies_same_snapshot_without_second_upload_or_rename(self):
        result = self.publish(publish=True)
        before = dict(self.hdfs.files)
        operations = len([event for event in self.hdfs.events if event[0] in {"put", "rename"}])
        repeated = self.publish(publish=True)
        self.assertEqual(repeated["status"], "already_verified")
        self.assertEqual(repeated["destination"], result["destination"])
        self.assertEqual(before, self.hdfs.files)
        self.assertEqual(operations, len([event for event in self.hdfs.events if event[0] in {"put", "rename"}]))

    def test_replay_from_another_local_directory_has_identical_portable_metadata(self):
        import shutil
        self.publish(publish=True)
        other = self.root / "moved-export"
        shutil.copytree(self.source, other)
        result = publish_snapshot(other, hdfs_uri=URI, hdfs=self.hdfs, publish=True)
        self.assertEqual(result["status"], "already_verified")

    def test_same_id_different_manifest_fails_without_touching_existing_snapshot(self):
        self.publish(publish=True)
        before = dict(self.hdfs.files)
        self.manifest["versions"]["fixture"] = "another_source_version"
        self.save_manifest()
        with self.assertRaisesRegex(RuntimeError, "hdfs_snapshot_"):
            self.publish(publish=True)
        self.assertEqual(self.hdfs.files, before)

    def test_upload_failure_leaves_staging_without_exposing_new_snapshot_and_preserves_old(self):
        old = self.publish(publish=True)
        before = {path: value for path, value in self.hdfs.files.items() if path.startswith(old["destination"] + "/")}
        self.manifest["run_id"] += "-next"
        self.save_manifest()
        planned = self.publish()
        self.hdfs.fail_put = "quarantine.jsonl"
        with self.assertRaisesRegex(RuntimeError, "injected_upload_failure"):
            self.publish(publish=True)
        self.assertFalse(self.hdfs.exists(planned["destination"]))
        staged = {path: value for path, value in self.hdfs.files.items() if "/.staging/" in path}
        self.assertTrue(staged)
        self.assertFalse(any(path.endswith("/_SUCCESS") for path in staged))
        self.assertEqual(before, {path: value for path, value in self.hdfs.files.items() if path in before})
        self.hdfs.fail_put = None
        self.assertEqual(self.publish(publish=True)["status"], "published")
        self.assertTrue(all(self.hdfs.files[path] == value for path, value in staged.items()))

    def test_corrupt_uploaded_parquet_is_detected_before_success_marker_or_rename(self):
        self.hdfs.corrupt_put = "prices_daily.parquet"
        with self.assertRaisesRegex(RuntimeError, "checksum_mismatch"):
            self.publish(publish=True)
        self.assertFalse(any(event[0] == "rename" for event in self.hdfs.events))
        self.assertFalse(any(path.endswith("/_SUCCESS") for path in self.hdfs.files))

    def test_failed_success_marker_upload_never_exposes_snapshot(self):
        self.hdfs.fail_put = "_SUCCESS"
        with self.assertRaisesRegex(RuntimeError, "injected_upload_failure"):
            self.publish(publish=True)
        self.assertFalse(any(event[0] == "rename" for event in self.hdfs.events))

    def test_existing_final_without_success_marker_is_rejected_and_preserved(self):
        result = self.publish(publish=True)
        self.hdfs.files.pop(result["destination"] + "/_SUCCESS")
        before = dict(self.hdfs.files)
        with self.assertRaisesRegex(RuntimeError, "inventory_mismatch"):
            self.publish(publish=True)
        self.assertEqual(before, self.hdfs.files)

    def test_same_destination_lock_prevents_overlapping_publish_without_stealing(self):
        plan = self.publish()
        lock = URI + DEFAULT_ROOT + "/.locks/KOSPI-2020-03-04-" + self.manifest["run_id"]
        self.hdfs.mkdir(lock)
        with self.assertRaisesRegex(RuntimeError, "directory_exists"):
            self.publish(publish=True)
        self.assertTrue(self.hdfs.exists(lock))
        self.assertFalse(self.hdfs.exists(plan["destination"]))
        self.assertEqual(self.hdfs.files, {})

    def test_partial_missing_control_and_tampered_local_files_fail_before_hdfs(self):
        self.manifest["status"] = "partial"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "only_complete"):
            self.publish(publish=True)
        self.manifest["status"] = "complete"
        self.save_manifest()
        (self.source / "prices_daily.parquet").write_bytes(b"interrupted")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.publish(publish=True)
        self.assertEqual(self.hdfs.events, [])

    def test_row_and_quarantine_counts_are_checked_not_only_checksum(self):
        for field in ("rows", "quarantined_rows"):
            original = self.manifest[field]
            self.manifest[field] += 1
            self.save_manifest()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "row_count_mismatch"):
                self.publish(publish=True)
            self.manifest[field] = original
        self.assertEqual(self.hdfs.events, [])

    def test_missing_or_invalid_quarantine_control_is_never_published(self):
        quarantine = self.source / "quarantine.jsonl"
        original = quarantine.read_bytes()
        quarantine.unlink()
        with self.assertRaisesRegex(ValueError, "missing_or_external"):
            self.publish(publish=True)
        row = json.loads(original)
        row["quality_flags"] = ["unknown_flag"]
        quarantine.write_text(dumps(row) + "\n", encoding="utf-8")
        self.manifest["quarantine_sha256"] = hashlib.sha256(quarantine.read_bytes()).hexdigest()
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "invalid_quarantine_record"):
            self.publish(publish=True)
        self.assertEqual(self.hdfs.events, [])

    def test_cli_uses_local_only_dry_run_by_default(self):
        from contextlib import redirect_stdout
        import io
        from services.stock_prices.cli import main
        output = io.StringIO()
        with redirect_stdout(output), patch.object(HdfsCli, "_run", side_effect=AssertionError("unexpected HDFS access")):
            self.assertEqual(main(["hdfs-publish", "--source", str(self.source), "--market", "KOSPI"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["rows"], 2)

    def test_unfinished_day_mismatched_market_and_mixed_selection_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "market_mismatch"):
            self.publish(market="NASDAQ")
        self.manifest["outcomes"][0]["requested_end"] = "2999-01-01"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "incomplete_requested_day"):
            self.publish()
        (self.source / "selection.json").write_text(dumps([
            {"market": "KOSPI", "ticker": "0126Z0"}, {"market": "NASDAQ", "ticker": "AAPL"}]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "single_market"):
            self.publish()

    def test_unsafe_destination_or_implicit_cluster_is_rejected(self):
        for options in ({"hdfs_uri": "hdfs://user:secret@host:9000"},
                        {"hdfs_uri": "file:///tmp/prices"},
                        {"hdfs_uri": URI + "/another/path"},
                        {"hdfs_uri": URI, "hdfs_root": "/data-lake/raw/realtime/news"},
                        {"hdfs_uri": URI, "hdfs_root": DEFAULT_ROOT + "/../news"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                publish_snapshot(self.source, hdfs=self.hdfs, publish=True, **options)
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "set_HDFS_URI"):
            publish_snapshot(self.source, hdfs=self.hdfs, publish=True)
        self.assertEqual(self.hdfs.events, [])

    def test_invalid_ohlc_and_non_midnight_timestamp_are_validated_from_actual_parquet(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        original = pq.read_table(self.source / "prices_daily.parquet")
        for field, value, pattern in (("close_price", 999, "OHLC"),
                                      ("trading_at", datetime(2020, 3, 2, 1, tzinfo=timezone.utc), "utc_midnight")):
            rows = original.to_pylist()
            rows[0][field] = value
            if field == "close_price":
                from decimal import Decimal
                rows[0][field] = Decimal(value)
            pq.write_table(pa.Table.from_pylist(rows, schema=original.schema), self.source / "prices_daily.parquet")
            value_bytes = (self.source / "prices_daily.parquet").read_bytes()
            self.manifest.update(sha256=hashlib.sha256(value_bytes).hexdigest(), parquet_bytes=len(value_bytes))
            self.save_manifest()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, pattern):
                self.publish()


class HdfsCliTests(unittest.TestCase):
    def test_cli_never_force_overwrites_or_passes_shell_commands(self):
        client = HdfsCli("/opt/hadoop/bin/hdfs")
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            client.put(Path("a b.parquet"), URI + DEFAULT_ROOT + "/.staging/test/prices_daily.parquet")
        self.assertEqual(run.call_args.args[0][:3], ["/opt/hadoop/bin/hdfs", "dfs", "-put"])
        self.assertNotIn("-f", run.call_args.args[0])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_not_found_is_distinct_from_hdfs_permission_or_connection_errors(self):
        client = HdfsCli()
        for result, expected in ((subprocess.CompletedProcess([], 1, "", ""), False),
                                  (subprocess.CompletedProcess([], 0, "", ""), True)):
            with patch("subprocess.run", return_value=result):
                self.assertEqual(client.exists("/somewhere"), expected)
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "secret diagnostic")):
            with self.assertRaisesRegex(RuntimeError, "^hdfs_existence_check_failed$"):
                client.exists("/somewhere")

    def test_listing_handles_qualified_and_unqualified_hdfs_paths(self):
        client = HdfsCli()
        root = URI + DEFAULT_ROOT + "/test"
        for path in (root + "/prices_daily.parquet", DEFAULT_ROOT + "/test/prices_daily.parquet"):
            output = "-rw-r--r-- 2 ubuntu supergroup 123 2026-09-15 10:00 " + path + "\n"
            with patch.object(client, "_run", return_value=output):
                self.assertEqual(client.sizes(root), {"prices_daily.parquet": 123})


if __name__ == "__main__":
    unittest.main()
