"""Read-only, bounded local/HDFS access for published document metadata."""
from __future__ import annotations

from functools import lru_cache
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import urlsplit, urlunsplit, unquote


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Empty snapshot path")
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or any(p in (".", "..") for p in path.parts)
            or any(p.startswith(".") for p in path.parts)
            or any(c in value for c in "\\:*?[]#%\x00\r\n")
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Unsafe snapshot path")
    return value


def canonical_hdfs_uri(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != "hdfs" or parts.query or parts.fragment or parts.username or parts.password
            or (parts.netloc and not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", parts.netloc))
            or not parts.path.startswith("/") or parts.path == "/"):
        raise ValueError("Expected an absolute HDFS snapshot URI")
    safe_relative(parts.path[1:])
    return urlunsplit(("hdfs", parts.netloc, parts.path, "", ""))


def reject_staging(value: str) -> None:
    if any(p.startswith(".") or p.endswith((".inprogress", ".uploading", ".partial", ".tmp"))
           for p in value.replace("\\", "/").split("/") if p):
        raise ValueError("Staging or incomplete snapshot paths are not accepted")


@lru_cache(maxsize=16)
def _arrow_filesystem(authority, user, timeout):
    """Reuse a native HDFS client and its JVM across immutable input batches.

    Native RPC/socket timeouts bound individual network operations. They are
    not the CLI backend's hard wall-clock deadline for an entire subprocess.
    Hadoop/JVM environment variables must be set before this first call.
    """
    try:
        from pyarrow.fs import HadoopFileSystem
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid native HDFS timeout")
        milliseconds = str(max(1, math.ceil(timeout * 1000)))
        endpoint = urlsplit("hdfs://" + authority)
        return HadoopFileSystem(endpoint.hostname or "default", endpoint.port or 0, user=user,
                                extra_conf={"ipc.client.connect.timeout": milliseconds,
                                            "ipc.client.rpc-timeout.ms": milliseconds,
                                            "dfs.client.socket-timeout": milliseconds})
    except Exception:
        # Native errors can include environment/authentication details. Report
        # a stable error; never silently switch transports after a failure.
        raise ValueError("HDFS Arrow filesystem initialization failed") from None


class SnapshotReader:
    """No filesystem mutation or shell invocation; raw bodies are only stat'ed."""

    def __init__(self, root, *, hdfs_bin=None, source_uri=None, timeout=120, max_bytes=256 * 1024**2):
        root = str(root).rstrip("/")
        reject_staging(root)
        self.timeout, self.max_bytes = timeout, max_bytes
        self.hdfs = root.startswith("hdfs:")
        self.executable = hdfs_bin or "/opt/hadoop/bin/hdfs"
        if self.hdfs:
            self.root = canonical_hdfs_uri(root)
            self.local = None
        else:
            if root.startswith("file:"):
                parts = urlsplit(root)
                if parts.netloc or parts.query or parts.fragment:
                    raise ValueError("Only local file URIs are accepted")
                root = unquote(parts.path)
                if re.match(r"^/[A-Za-z]:/", root):
                    root = root[1:]
            local = Path(root).absolute()
            if any(p.is_symlink() for p in [local, *local.parents]):
                raise ValueError("Snapshot symlinks are not accepted")
            self.local = local.resolve(strict=True)
            if not self.local.is_dir():
                raise ValueError("Snapshot root must be a directory")
            self.root = self.local.as_uri()
        self.source_uri = str(source_uri or self.root).rstrip("/")
        if self.source_uri.startswith("hdfs:"):
            self.source_uri = canonical_hdfs_uri(self.source_uri)
        elif not self.source_uri.startswith("file:"):
            raise ValueError("source_uri must be an absolute HDFS or file URI")
        if self.hdfs and self.source_uri != self.root:
            raise ValueError("HDFS input provenance cannot be overridden; source_uri is for local mirrors")
        reject_staging(self.source_uri)
        self.sizes = {}
        self.backend = os.environ.get("DOCUMENT_HDFS_BACKEND", "cli") if self.hdfs else "local"
        if self.backend not in ("cli", "arrow", "local"):
            raise ValueError("DOCUMENT_HDFS_BACKEND must be cli or arrow")
        if self.hdfs and self.backend == "local":
            raise ValueError("DOCUMENT_HDFS_BACKEND must be cli or arrow")
        self.filesystem = None
        if self.backend == "arrow":
            self.filesystem = _arrow_filesystem(urlsplit(self.root).netloc,
                                               os.environ.get("HADOOP_USER_NAME", "ubuntu"), timeout)

    def uri(self, relative):
        return self.source_uri + "/" + safe_relative(relative)

    def _local_path(self, relative):
        path = self.local.joinpath(*PurePosixPath(safe_relative(relative)).parts)
        if any(p.is_symlink() for p in [path, *path.parents] if p != self.local.parent):
            raise ValueError("Snapshot symlinks are not accepted")
        if not path.resolve().is_relative_to(self.local):
            raise ValueError("Snapshot path escapes the root")
        return path

    def _call(self, *args):
        result = subprocess.run([self.executable, "dfs", *args], capture_output=True, timeout=self.timeout)
        if result.returncode:
            raise ValueError("HDFS read failed: " + args[0])
        return result.stdout

    def _arrow_path(self, relative):
        return urlsplit(self.root).path + "/" + safe_relative(relative)

    def _inventory_relative(self, path, prefix):
        if path.startswith("hdfs:"):
            parsed = urlsplit(path)
            if parsed.netloc != urlsplit(self.root).netloc or parsed.query or parsed.fragment:
                raise ValueError("HDFS inventory changed authority")
            path = parsed.path
        base = urlsplit(self.root).path
        if not path.startswith(base + "/"):
            raise ValueError("HDFS inventory escapes snapshot")
        relative = safe_relative(path[len(base) + 1:])
        if not (relative == prefix or relative.startswith(prefix + "/")):
            raise ValueError("HDFS inventory escapes raw prefix")
        return relative

    def size(self, relative):
        safe_relative(relative)
        if relative not in self.sizes:
            if self.backend == "arrow":
                from pyarrow.fs import FileType
                path = self._arrow_path(relative)
                try:
                    info = self.filesystem.get_file_info(path)
                except Exception:
                    raise ValueError("HDFS Arrow file metadata read failed") from None
                if info.type != FileType.File or info.path != path:
                    raise ValueError("Missing or non-regular HDFS file: " + relative)
                if not isinstance(info.size, int) or isinstance(info.size, bool) or info.size < 0:
                    raise ValueError("Invalid HDFS file size")
                self.sizes[relative] = info.size
            elif self.hdfs:
                value = self._call("-stat", "%b", self.root + "/" + relative).decode().strip()
                if not value.isdecimal():
                    raise ValueError("Invalid HDFS file size")
                self.sizes[relative] = int(value)
            else:
                path = self._local_path(relative)
                if not path.is_file():
                    raise ValueError("Missing snapshot file: " + relative)
                self.sizes[relative] = path.stat().st_size
        return self.sizes[relative]

    def prime_sizes(self, prefix):
        """List legacy raw-file sizes once instead of launching 16k JVMs."""
        safe_relative(prefix)
        if not self.hdfs:
            return
        if self.backend == "arrow":
            from pyarrow.fs import FileSelector, FileType
            try:
                entries = self.filesystem.get_file_info(FileSelector(self._arrow_path(prefix), recursive=True))
            except Exception:
                raise ValueError("HDFS Arrow inventory read failed") from None
            sizes = {}
            for info in entries:
                relative = self._inventory_relative(info.path, prefix)
                if info.type == FileType.Directory:
                    continue
                if (info.type != FileType.File or not isinstance(info.size, int) or isinstance(info.size, bool)
                        or info.size < 0 or relative in self.sizes or relative in sizes):
                    raise ValueError("Invalid or duplicate HDFS raw inventory entry")
                sizes[relative] = info.size
            self.sizes.update(sizes)
            return
        for line in self._call("-ls", "-R", self.root + "/" + prefix).decode().splitlines():
            if not line or line.startswith("Found "):
                continue
            fields = line.split(None, 7)
            if len(fields) != 8 or fields[0][0] not in ("-", "d"):
                raise ValueError("Unexpected HDFS raw inventory entry")
            relative = self._inventory_relative(fields[7], prefix)
            if fields[0][0] == "-":
                if not fields[4].isdecimal() or relative in self.sizes:
                    raise ValueError("Invalid or duplicate HDFS raw inventory entry")
                self.sizes[relative] = int(fields[4])

    def read(self, relative, *, limit=None):
        maximum = self.max_bytes if limit is None else min(limit, self.max_bytes)
        size = self.size(relative)
        if size > maximum:
            raise ValueError("Metadata file exceeds read limit: " + relative)
        # head bounds subprocess output even if an ostensibly immutable object is
        # replaced after stat; Hadoop's -cat itself has no byte limit.
        if self.backend == "arrow":
            try:
                # open_input_file reads the stored bytes without detecting or
                # decompressing a format based on its filename.
                with self.filesystem.open_input_file(self._arrow_path(relative)) as stream:
                    data = stream.read(maximum + 1)
            except Exception:
                raise ValueError("HDFS Arrow metadata read failed") from None
            if len(data) > maximum:
                raise ValueError("Metadata file exceeds read limit: " + relative)
        elif self.hdfs:
            with subprocess.Popen([self.executable, "dfs", "-cat", self.root + "/" + relative],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                import threading
                expired = threading.Event()
                def stop():
                    expired.set()
                    process.kill()
                timer = threading.Timer(self.timeout, stop)
                timer.start()
                try:
                    data = process.stdout.read(maximum + 1)
                    if len(data) > maximum:
                        process.kill()
                        raise ValueError("Metadata file exceeds read limit: " + relative)
                    code = process.wait(timeout=self.timeout)
                    if expired.is_set() or code:
                        raise ValueError("HDFS metadata read failed or timed out")
                finally:
                    timer.cancel()
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        else:
            with self._local_path(relative).open("rb") as stream:
                data = stream.read(maximum + 1)
        if len(data) != size:
            raise ValueError("Snapshot file changed during read: " + relative)
        return data
