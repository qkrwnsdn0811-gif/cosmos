"""One HDFS connection per run, instead of one JVM per batch.

Both loaders walk a tree of batch directories and read one parquet from each.
Done through ``hdfs dfs``, every read is a fresh JVM: measured on the server
2026-09-23, under the load of a backfill, a single ``-cat`` took **13-16
seconds**, essentially all of it startup. At that price the 10,405 batches
waiting behind the GPU backfill are 46 hours of loading - the backfill saves
ten days and the loader hands them straight back.

The same client held open for the whole run reads one in **0.90 seconds** after
a 15-second connect. So the connection is made once and reused.

libhdfs only works from inside the cluster; off-box it cannot reach the
DataNodes. So this falls back to the CLI when it cannot connect, which is what
a developer's machine and the tests get, and says so on stderr rather than
silently running slowly.

Paths are returned fully qualified (``hdfs://host:9000/...``), the form the CLI
prints and the loaders' state files already hold. pyarrow reports paths without
the scheme, so it is put back: a path that changes shape is a batch that looks
new, and the whole backlog would be replayed.
"""
from __future__ import annotations

import subprocess
import sys


class HdfsReadError(RuntimeError):
    pass


def split_uri(root: str) -> tuple[str, str]:
    """Split ``hdfs://host:9000/a/b`` into its authority prefix and its path."""
    if "://" not in root:
        return "", root
    scheme, rest = root.split("://", 1)
    authority, _, path = rest.partition("/")
    return f"{scheme}://{authority}", "/" + path


class CliReader:
    """Falls back to the Hadoop CLI: correct anywhere, one JVM per call."""

    kind = "cli"

    def __init__(self, hdfs_bin: str):
        self.hdfs_bin = hdfs_bin

    def _call(self, *args: str, binary: bool, timeout: int):
        result = subprocess.run([self.hdfs_bin, "dfs", *args], capture_output=True,
                                timeout=timeout)
        if result.returncode != 0:
            raise HdfsReadError(f"hdfs {' '.join(args)} failed: "
                                f"{result.stderr.decode('utf-8', 'replace').strip()[:400]}")
        return result.stdout if binary else result.stdout.decode("utf-8", "replace")

    def listing(self, root: str) -> list[str]:
        text = self._call("-ls", "-R", root, binary=False, timeout=300)
        return [line.rsplit(" ", 1)[-1].strip() for line in text.splitlines()
                if line.rsplit(" ", 1)[-1].strip()]

    def read(self, path: str) -> bytes:
        return self._call("-cat", path, binary=True, timeout=600)


class LibhdfsReader:
    """One libhdfs client, opened once and reused for every read."""

    kind = "libhdfs"

    def __init__(self, root: str):
        import pyarrow.fs as pafs

        self.prefix, _ = split_uri(root)
        self.fs = self._connect(pafs, root) if self.prefix \
            else pafs.HadoopFileSystem("default")
        self._selector = pafs.FileSelector

    @staticmethod
    def _connect(pafs, root: str):
        """from_uri returns the filesystem on some pyarrow versions and a
        (filesystem, path) pair on others. Take whichever came back."""
        resolved = pafs.HadoopFileSystem.from_uri(root)
        return resolved[0] if isinstance(resolved, tuple) else resolved

    def listing(self, root: str) -> list[str]:
        _, path = split_uri(root)
        found = self.fs.get_file_info(self._selector(path, recursive=True,
                                                     allow_not_found=True))
        return [self.prefix + info.path for info in found]

    def read(self, path: str) -> bytes:
        _, inner = split_uri(path)
        with self.fs.open_input_file(inner) as handle:
            return handle.read()


def open_reader(root: str, hdfs_bin: str, *, prefer_libhdfs: bool = True):
    """Return the fastest reader that can actually reach this cluster."""
    if prefer_libhdfs:
        try:
            reader = LibhdfsReader(root)
            reader.listing(root)          # prove it reaches the NameNode before committing
            return reader
        except Exception as error:        # noqa: BLE001 - reported, then degraded
            print(f"libhdfs unavailable ({str(error)[:200]}); "
                  f"falling back to the {hdfs_bin} CLI, which is far slower",
                  file=sys.stderr, flush=True)
    return CliReader(hdfs_bin)


def discover_batches(reader, root: str) -> list[str]:
    """Every batch directory under root that carries both a parquet and _SUCCESS.

    ``.staging-*`` is the enricher's half-written directory, renamed into place
    when it completes. Reading it loads a batch that then arrives a second time
    under its real name - harmless, because the write is an upsert, but wasted.
    """
    batches, succeeded = set(), set()
    for path in reader.listing(root):
        if "/.staging-" in path:
            continue
        if path.endswith("/data.parquet"):
            batches.add(path[: -len("/data.parquet")])
        elif path.endswith("/_SUCCESS"):
            succeeded.add(path[: -len("/_SUCCESS")])
    return sorted(batches & succeeded)
