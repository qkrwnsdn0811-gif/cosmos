"""Discover committed metadata units with read-only HDFS marker globs."""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
from pathlib import PurePosixPath
import re
import subprocess
from urllib.parse import urlsplit, urlunsplit

from .hdfs import canonical_hdfs_uri, reject_staging


MARKERS = {"news": "_manifest.json", "news-analyzed": "_SUCCESS",
           "news-historical-analyzed": "_SUCCESS", "dart": "ready.json", "sec": "_SUCCESS",
           "sec-incremental": "_SUCCESS", "sec-batch": "_SUCCESS"}


@dataclass(frozen=True)
class Candidate:
    kind: str
    input_uri: str

    @property
    def batch_id(self):
        return hashlib.sha256(self.input_uri.encode()).hexdigest()


def marker_glob(kind, value):
    """Accept a root glob or its explicit marker; only '*' globbing is needed."""
    if kind not in MARKERS or not isinstance(value, str):
        raise ValueError("Discovery root requires a supported kind and HDFS glob")
    parts = urlsplit(value.rstrip("/"))
    if (parts.scheme != "hdfs" or not parts.path.startswith("/") or parts.path == "/"
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment
            or (parts.netloc and not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", parts.netloc))):
        raise ValueError("Discovery glob must be an absolute HDFS URI without credentials")
    path = PurePosixPath(parts.path)
    if (str(path) != parts.path or any(p in (".", "..") or p.startswith(".") for p in path.parts[1:])
            or any(c in parts.path for c in "\\:?[]{}%#") or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("Unsafe HDFS discovery glob")
    if kind == "sec-batch" and "*" in parts.path:
        raise ValueError("Legacy SEC batches must use explicitly selected roots")
    if path.name != MARKERS[kind]:
        path /= MARKERS[kind]
    return urlunsplit(("hdfs", parts.netloc, str(path), "", ""))


def _matches(pattern, path):
    left, right = PurePosixPath(pattern).parts, PurePosixPath(path).parts
    return len(left) == len(right) and all(fnmatchcase(b, a) for a, b in zip(left, right))


def _no_matches(stderr):
    # Missing globs are normal. Connectivity, authentication and permission
    # errors must remain visible instead of becoming a misleading empty queue.
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()
             and not ("NativeCodeLoader" in line and "Unable to load native-hadoop library" in line)]
    return len(lines) == 1 and lines[0].startswith("ls:") and lines[0].endswith("No such file or directory")


def discover(root, *, hdfs_bin="/opt/hadoop/bin/hdfs", timeout=120):
    if not isinstance(root, dict):
        raise ValueError("Discovery roots must be JSON objects")
    kind = root.get("kind")
    pattern = marker_glob(kind, root.get("glob"))
    result = subprocess.run([hdfs_bin, "dfs", "-ls", pattern], capture_output=True, timeout=timeout)
    if result.returncode:
        if result.returncode == 1 and _no_matches(result.stderr):
            return []
        raise RuntimeError("HDFS marker discovery failed")
    configured = urlsplit(pattern)
    found = set()
    for line in result.stdout.decode("utf-8").splitlines():
        if not line or line.startswith("Found "):
            continue
        fields = line.split(None, 7)
        if len(fields) != 8 or not fields[0].startswith("-") or not fields[4].isdecimal():
            raise ValueError("HDFS discovery returned a non-regular marker")
        listed = fields[7]
        if listed.startswith("hdfs:"):
            remote = urlsplit(listed)
            if remote.netloc != configured.netloc or remote.query or remote.fragment:
                raise ValueError("HDFS discovery changed marker authority")
            listed = remote.path
        if not _matches(configured.path, listed):
            raise ValueError("HDFS discovery returned a path outside the configured glob")
        uri = urlunsplit(("hdfs", configured.netloc, str(PurePosixPath(listed).parent), "", ""))
        try:
            reject_staging(uri)
        except ValueError:
            continue
        uri = canonical_hdfs_uri(uri)
        found.add(Candidate(kind, uri))
    return sorted(found, key=lambda item: item.input_uri)


def discover_all(roots, *, hdfs_bin="/opt/hadoop/bin/hdfs"):
    candidates, errors = {}, []
    for index, root in enumerate(roots):
        try:
            for item in discover(root, hdfs_bin=hdfs_bin):
                previous = candidates.get(item.input_uri)
                if previous and previous.kind != item.kind and {previous.kind, item.kind} != {"sec", "sec-incremental"}:
                    raise ValueError("The same HDFS input was configured with conflicting kinds")
                candidates[item.input_uri] = item
        except Exception as error:
            errors.append({"root_index": index, "error_type": type(error).__name__})
    return sorted(candidates.values(), key=lambda item: (item.kind, item.input_uri)), errors
