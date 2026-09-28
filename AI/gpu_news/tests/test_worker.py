from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from worker import LocalHdfs, _safe_parts, WorkerError


class WorkerPathTest(unittest.TestCase):
    def test_historical_identity(self):
        parts, root = _safe_parts(
            "/x/model_version=dict-v1.3/run_id=nasdaq100-10y-20260920/batch=00065/_SUCCESS",
            "historical")
        self.assertEqual(parts, ["run_id=nasdaq100-10y-20260920", "batch=00065"])
        self.assertTrue(root.endswith("batch=00065"))

    def test_realtime_identity(self):
        parts, _ = _safe_parts(
            "/x/model_version=dict-v1.3/topic=news.raw/partition=2/start=00000000000000000042/_SUCCESS",
            "realtime")
        self.assertEqual(parts, ["topic=news.raw", "partition=2", "start=00000000000000000042"])

    def test_rejects_missing_partition(self):
        with self.assertRaises(WorkerError):
            _safe_parts("/x/topic=news.raw/start=1/_SUCCESS", "realtime")


class FakeInfo:
    def __init__(self, type_):
        self.type = type_


class FakeFs:
    """Records calls so a test can prove no JVM was spawned per file."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.calls = []

    def get_file_info(self, path):
        self.calls.append(("info", path))
        return FakeInfo("FILE" if path in self.existing else "NOT_FOUND")


class LocalHdfsPathTest(unittest.TestCase):
    def test_accepts_bare_and_uri_paths(self):
        self.assertEqual(LocalHdfs._path("/data-lake/a/b"), "/data-lake/a/b")
        self.assertEqual(LocalHdfs._path("hdfs://nn:9000/data-lake/a/b"), "/data-lake/a/b")

    def test_rejects_a_relative_location(self):
        with self.assertRaises(WorkerError):
            LocalHdfs._path("data-lake/a/b")


class LocalHdfsNoSubprocessTest(unittest.TestCase):
    """Per-batch I/O must not shell out; that was 2.6 seconds of JVM per call."""

    def test_exists_uses_the_persistent_client(self):
        bridge = LocalHdfs(hdfs_bin="/nonexistent/hdfs")
        bridge._fs = FakeFs(existing={"/a/_SUCCESS"})
        bridge._not_found = "NOT_FOUND"
        self.assertTrue(bridge.exists("/a/_SUCCESS"))
        self.assertFalse(bridge.exists("/a/missing"))
        # A bogus hdfs_bin would have raised had the CLI been used.
        self.assertEqual([c[0] for c in bridge._fs.calls], ["info", "info"])


class RoundRobinTest(unittest.TestCase):
    """A busy partition must not starve the others."""

    @staticmethod
    def item(partition, offset):
        root = (f"/data-lake/analyzed/news/company-mentions/model_version=dict-v1.3"
                f"/topic=news.raw/partition={partition}/start={offset:020d}")
        return (0, "realtime", root, "/out" + root)

    def test_every_partition_advances_in_one_pass(self):
        from worker import _round_robin
        pending = ([self.item(2, n) for n in range(100, 110)]
                   + [self.item(0, n) for n in range(100, 103)]
                   + [self.item(1, n) for n in range(100, 103)])
        first_six = _round_robin(pending, newest_first=True)[:6]
        seen = {p for _i, _t, root, _o in first_six
                for p in [root.split("partition=")[1].split("/")[0]]}
        self.assertEqual(seen, {"0", "1", "2"})

    def test_newest_offset_comes_first_within_a_partition(self):
        from worker import _round_robin
        pending = [self.item(0, 100), self.item(0, 300), self.item(0, 200)]
        order = [int(root.split("start=")[1]) for _i, _t, root, _o
                 in _round_robin(pending, newest_first=True)]
        self.assertEqual(order, [300, 200, 100])

    def test_oldest_first_is_still_available(self):
        from worker import _round_robin
        pending = [self.item(0, 100), self.item(0, 300), self.item(0, 200)]
        order = [int(root.split("start=")[1]) for _i, _t, root, _o
                 in _round_robin(pending, newest_first=False)]
        self.assertEqual(order, [100, 200, 300])

    def test_historical_runs_are_grouped_by_run_id(self):
        from worker import _stream_key
        self.assertEqual(
            _stream_key("/x/model_version=dict-v1.3/run_id=abc/batch=00001"), "run_id=abc")


if __name__ == "__main__":
    unittest.main()
