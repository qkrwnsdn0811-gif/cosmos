import subprocess
import unittest
from unittest.mock import patch

from services.document_loader.discovery import discover, discover_all, marker_glob


class DiscoveryTests(unittest.TestCase):
    def root(self, kind="news"):
        return {"kind": kind, "glob": "hdfs://cluster:9000/news/topic=news.raw/partition=*/start=*"}

    def listing(self, paths, returncode=0, stderr=b""):
        rows = ["-rw-r--r-- 2 user group 14 2026-09-16 00:00 " + path for path in paths]
        return subprocess.CompletedProcess([], returncode, ("\n".join(rows)+"\n").encode(), stderr)

    def test_committed_markers_are_discovered_with_argv_and_deduplicated(self):
        path = "/news/topic=news.raw/partition=0/start=00000000000000000001/_manifest.json"
        with patch("services.document_loader.discovery.subprocess.run", return_value=self.listing([path, path])) as run:
            items = discover(self.root(), hdfs_bin="/opt/hadoop/bin/hdfs")
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].input_uri.endswith("start=00000000000000000001"))
        self.assertEqual(run.call_args.args[0][:3], ["/opt/hadoop/bin/hdfs", "dfs", "-ls"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_explicit_marker_and_root_glob_have_same_result(self):
        root = "hdfs://cluster:9000/dart/daily-*"
        self.assertEqual(marker_glob("dart", root), marker_glob("dart", root+"/ready.json"))

    def test_analyzed_news_discovers_only_success_markers(self):
        root = {"kind": "news-analyzed", "glob":
                "hdfs://cluster:9000/analyzed/model_version=dict-v1.3/topic=*/partition=*/start=*"}
        marker = "/analyzed/model_version=dict-v1.3/topic=news.raw/partition=0/start=00000000000000000001/_SUCCESS"
        with patch("services.document_loader.discovery.subprocess.run",
                   return_value=self.listing([marker])):
            items = discover(root)
        self.assertEqual(items[0].kind, "news-analyzed")
        self.assertTrue(items[0].input_uri.endswith("start=00000000000000000001"))

    def test_historical_analyzed_batches_are_discovered(self):
        root = {"kind": "news-historical-analyzed", "glob":
                "hdfs://cluster:9000/analyzed/model_version=dict-v1.3/run_id=*/batch=*"}
        marker = "/analyzed/model_version=dict-v1.3/run_id=history-1/batch=00000/_SUCCESS"
        with patch("services.document_loader.discovery.subprocess.run",
                   return_value=self.listing([marker])):
            items = discover(root)
        self.assertEqual(items[0].kind, "news-historical-analyzed")
        self.assertTrue(items[0].input_uri.endswith("batch=00000"))

    def test_safe_empty_glob_is_not_connectivity_error(self):
        result = self.listing([], 1, b"ls: `hdfs://cluster/missing': No such file or directory\n")
        with patch("services.document_loader.discovery.subprocess.run", return_value=result):
            self.assertEqual(discover(self.root()), [])
        result.stderr = b"Connection refused\n"
        with patch("services.document_loader.discovery.subprocess.run", return_value=result), self.assertRaises(RuntimeError):
            discover(self.root())

    def test_staging_markers_are_ignored(self):
        path = "/news/topic=news.raw/partition=0/start=0001.inprogress/_manifest.json"
        with patch("services.document_loader.discovery.subprocess.run", return_value=self.listing([path])):
            self.assertEqual(discover(self.root()), [])

    def test_outside_glob_and_symlink_markers_are_rejected(self):
        with patch("services.document_loader.discovery.subprocess.run", return_value=self.listing(["/outside/_manifest.json"])), self.assertRaises(ValueError):
            discover(self.root())
        result = self.listing(["/news/topic=news.raw/partition=0/start=1/_manifest.json"])
        result.stdout = result.stdout.replace(b"-rw-r--r--", b"lrwxrwxrwx")
        with patch("services.document_loader.discovery.subprocess.run", return_value=result), self.assertRaises(ValueError):
            discover(self.root())

    def test_unsafe_globs_and_wildcard_legacy_are_rejected(self):
        for glob in ("hdfs://user:secret@host/data/*", "hdfs://host/data/../*", "hdfs://host/data/[abc]", "hdfs://host/data/%2e%2e", "hdfs://host/data/*?x=1"):
            with self.subTest(glob=glob), self.assertRaises(ValueError):
                marker_glob("news", glob)
        with self.assertRaises(ValueError):
            marker_glob("sec-batch", "hdfs://host/data/*")

    def test_discovery_continues_after_one_root_fails(self):
        from services.document_loader.discovery import Candidate
        candidate = Candidate("dart", "hdfs://cluster/dart/one")
        with patch("services.document_loader.discovery.discover", side_effect=[RuntimeError("unavailable"), [candidate]]):
            found, errors = discover_all([self.root(), {"kind":"dart", "glob":"hdfs://cluster/dart/*"}])
        self.assertEqual(found, [candidate])
        self.assertEqual(errors, [{"root_index":0, "error_type":"RuntimeError"}])


if __name__ == "__main__":
    unittest.main()
