import subprocess
import unittest
from unittest.mock import MagicMock, patch

from services.hdfs_reader import (CliReader, HdfsReadError, LibhdfsReader,
                                  discover_batches, open_reader, split_uri)

CLUSTER = "hdfs://100.117.115.44:9000"
ROOT = CLUSTER + "/data-lake/analyzed/news/company-sentiment/model_version=v1"


class SplitUriTests(unittest.TestCase):
    def test_authority_is_separated_from_path(self):
        self.assertEqual(split_uri(ROOT), (CLUSTER, ROOT[len(CLUSTER):]))

    def test_a_bare_path_has_no_authority(self):
        self.assertEqual(split_uri("/data-lake/news"), ("", "/data-lake/news"))


class PathShapeTests(unittest.TestCase):
    """The loaders key their state files on these strings.

    A batch whose path comes back in a different shape is a batch that looks
    new, and the entire loaded backlog is replayed. libhdfs reports paths
    without the scheme, so the two readers are held to the same output.
    """

    paths = [ROOT + "/topic=news.raw/partition=0/start=0001/data.parquet",
             ROOT + "/topic=news.raw/partition=0/start=0001/_SUCCESS"]

    def cli(self):
        rows = ["-rw-r--r-- 2 u g 14 2026-09-16 00:00 " + p for p in self.paths]
        done = subprocess.CompletedProcess([], 0, ("\n".join(rows) + "\n").encode(), b"")
        with patch("services.hdfs_reader.subprocess.run", return_value=done):
            return discover_batches(CliReader("/opt/hadoop/bin/hdfs"), ROOT)

    def libhdfs(self):
        reader = LibhdfsReader.__new__(LibhdfsReader)
        reader.prefix = CLUSTER
        reader.fs = MagicMock()
        reader._selector = MagicMock()
        # pyarrow strips the scheme; the reader has to put it back.
        reader.fs.get_file_info.return_value = [
            MagicMock(path=p[len(CLUSTER):]) for p in self.paths]
        return discover_batches(reader, ROOT)

    def test_both_readers_return_the_same_fully_qualified_batch(self):
        expected = [ROOT + "/topic=news.raw/partition=0/start=0001"]
        self.assertEqual(self.cli(), expected)
        self.assertEqual(self.libhdfs(), expected, "libhdfs dropped the scheme")


class DiscoverBatchesTests(unittest.TestCase):
    def reader(self, paths):
        listing = MagicMock()
        listing.listing.return_value = paths
        return listing

    def test_a_batch_needs_both_the_parquet_and_the_marker(self):
        found = discover_batches(self.reader([
            ROOT + "/a/data.parquet", ROOT + "/a/_SUCCESS",
            ROOT + "/b/data.parquet",                       # still being written
            ROOT + "/c/_SUCCESS",                           # marker without data
        ]), ROOT)
        self.assertEqual(found, [ROOT + "/a"])

    def test_staging_directories_are_not_batches(self):
        """The enricher renames .staging-* into place when it finishes.

        Read before the rename, the batch arrives twice under two names. The
        write is an upsert so nothing breaks, but the work is done twice.
        """
        found = discover_batches(self.reader([
            ROOT + "/p/.staging-5166b78d/data.parquet", ROOT + "/p/.staging-5166b78d/_SUCCESS",
            ROOT + "/p/start=0001/data.parquet", ROOT + "/p/start=0001/_SUCCESS",
        ]), ROOT)
        self.assertEqual(found, [ROOT + "/p/start=0001"])


class FallbackTests(unittest.TestCase):
    def test_a_cluster_it_cannot_reach_degrades_to_the_cli_and_says_so(self):
        with patch("services.hdfs_reader.LibhdfsReader", side_effect=OSError("no DataNode route")), \
             patch("sys.stderr") as err:
            reader = open_reader(ROOT, "/opt/hadoop/bin/hdfs")
        self.assertEqual(reader.kind, "cli")
        self.assertIn("libhdfs unavailable", "".join(
            call.args[0] for call in err.write.call_args_list if call.args))

    def test_a_reachable_cluster_keeps_the_fast_reader(self):
        fake = MagicMock(kind="libhdfs")
        with patch("services.hdfs_reader.LibhdfsReader", return_value=fake):
            self.assertIs(open_reader(ROOT, "/opt/hadoop/bin/hdfs"), fake)
        fake.listing.assert_called_once_with(ROOT)

    def test_a_cluster_that_connects_but_cannot_list_is_not_used(self):
        """Connecting proves nothing: libhdfs reaches the NameNode from outside
        the cluster and only fails later, on the DataNodes."""
        fake = MagicMock(kind="libhdfs")
        fake.listing.side_effect = OSError("connection refused")
        with patch("services.hdfs_reader.LibhdfsReader", return_value=fake), patch("sys.stderr"):
            self.assertEqual(open_reader(ROOT, "/opt/hadoop/bin/hdfs").kind, "cli")


class CliReaderTests(unittest.TestCase):
    def test_a_failed_call_names_the_command_and_the_error(self):
        failed = subprocess.CompletedProcess([], 1, b"", b"ls: `/nope': No such file")
        with patch("services.hdfs_reader.subprocess.run", return_value=failed):
            with self.assertRaises(HdfsReadError) as caught:
                CliReader("/opt/hadoop/bin/hdfs").listing("/nope")
        self.assertIn("-ls", str(caught.exception))
        self.assertIn("No such file", str(caught.exception))

    def test_reading_returns_bytes_untouched(self):
        blob = b"PAR1\x00\xff\xfeparquet"
        done = subprocess.CompletedProcess([], 0, blob, b"")
        with patch("services.hdfs_reader.subprocess.run", return_value=done) as run:
            self.assertEqual(CliReader("/opt/hadoop/bin/hdfs").read(ROOT + "/a/data.parquet"), blob)
        self.assertEqual(run.call_args.args[0][:3], ["/opt/hadoop/bin/hdfs", "dfs", "-cat"])


if __name__ == "__main__":
    unittest.main()


class ConnectShapeTests(unittest.TestCase):
    """from_uri returns a filesystem on some pyarrow versions and a
    (filesystem, path) pair on others. Getting it wrong is not an error the
    caller sees - open_reader catches it and quietly degrades to the CLI, which
    is how this shipped to the server and ran twenty times slower."""

    def connect(self, returned):
        pafs = MagicMock()
        pafs.HadoopFileSystem.from_uri.return_value = returned
        return LibhdfsReader._connect(pafs, ROOT)

    def test_a_bare_filesystem_is_used_as_is(self):
        fs = MagicMock()
        self.assertIs(self.connect(fs), fs)

    def test_a_filesystem_and_path_pair_yields_the_filesystem(self):
        fs = MagicMock()
        self.assertIs(self.connect((fs, "/data-lake/news")), fs)
