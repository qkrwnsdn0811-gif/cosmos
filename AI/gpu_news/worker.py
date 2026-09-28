"""Poll immutable HDFS company-mention batches, enrich them, publish the result.

Two transports, one job.  ``local`` runs on a cluster host and calls ``hdfs``
directly; it is what the ten-minute timer uses, because the live volume is
about fourteen sentences per pass and a CPU clears that in a second or two.
``ssh`` reaches the cluster from a workstation and exists for the one-off
backfill, where a GPU turns roughly seven hours into twenty-five minutes.

The transport is a deployment detail, not a difference in what gets computed:
both call the same engine with the same pinned models, and ``--device`` is
independent of it.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from finbert_batch import FinbertEngine, enrich_local_batch


SAFE = re.compile(r"[A-Za-z0-9._=-]+")


class WorkerError(RuntimeError):
    pass


def _parse_listing(stdout: bytes) -> list[str]:
    paths = []
    for raw in stdout.decode("utf-8", "strict").splitlines():
        fields = raw.split()
        if len(fields) >= 8 and fields[0][0] in "-d":
            paths.append(fields[-1])
    return sorted(set(paths))


def _missing(stderr: bytes) -> bool:
    error = stderr.decode("utf-8", "replace").lower()
    return "no such file" in error or "file does not exist" in error


class LocalHdfs:
    """The same interface as SshHdfs, for a worker running on a cluster host.

    Discovery and per-batch I/O deliberately use different mechanisms, which is
    the split AI/ner/realtime_analyzer.py already settled on for this cluster:

    ``hdfs dfs`` starts a JVM on every invocation. Measured on this server that
    is 2.4-2.9 seconds a call. A batch needs three reads and seven publish
    steps, so driving it from the CLI costs about 26 seconds of JVM startup per
    batch - roughly ten times the actual work, and enough to push a forty-batch
    pass past the ten-minute timer into overlapping with the next one. So batch
    I/O goes through one persistent PyArrow/libhdfs client for the life of the
    process.

    Discovery stays on the CLI because it needs shell globs over partition
    directories, which libhdfs does not offer, and because it runs twice per
    pass rather than ten times per batch.
    """

    def __init__(self, hdfs_bin: str = "/opt/hadoop/bin/hdfs", uri: str | None = None):
        self.hdfs_bin = hdfs_bin
        self._uri = uri
        self._fs = None
        self._not_found = None

    @property
    def fs(self):
        if self._fs is None:
            from pyarrow import fs as pafs
            try:
                if self._uri:
                    self._fs, _root = pafs.FileSystem.from_uri(self._uri)
                else:
                    # The cluster default from HADOOP_CONF_DIR/core-site.xml.
                    self._fs = pafs.HadoopFileSystem("default")
            except Exception as error:
                raise WorkerError("could not initialize the PyArrow HDFS client") from error
            self._not_found = pafs.FileType.NotFound
        return self._fs

    @staticmethod
    def _path(location: str) -> str:
        if location.startswith("hdfs://"):
            path = urlsplit(location).path
        else:
            path = location
        if not path.startswith("/"):
            raise WorkerError(f"HDFS location is not absolute: {location}")
        return path

    def _run(self, *args: str, check: bool = True,
             timeout: int = 600) -> subprocess.CompletedProcess:
        """CLI, for discovery only."""
        result = subprocess.run([self.hdfs_bin, "dfs", *args],
                                capture_output=True, check=False, timeout=timeout)
        if check and result.returncode:
            message = result.stderr.decode("utf-8", "replace").strip().splitlines()
            raise WorkerError((message[-1] if message else "HDFS command failed")[:1000])
        return result

    def list(self, pattern: str) -> list[str]:
        result = self._run("-ls", pattern, check=False)
        if result.returncode:
            if _missing(result.stderr):
                return []
            raise WorkerError(result.stderr.decode("utf-8", "replace")[-1000:])
        return _parse_listing(result.stdout)

    def exists(self, path: str) -> bool:
        try:
            return self.fs.get_file_info(self._path(path)).type != self._not_found
        except WorkerError:
            raise
        except Exception as error:
            raise WorkerError("could not inspect HDFS location") from error

    def read(self, path: str) -> bytes:
        try:
            with self.fs.open_input_stream(self._path(path)) as stream:
                return stream.read()
        except WorkerError:
            raise
        except Exception as error:
            raise WorkerError(f"could not read HDFS input: {path}") from error

    def publish(self, local_dir: Path, final_path: str) -> str:
        if self.exists(final_path + "/_SUCCESS"):
            return "already_complete"
        parent = str(PurePosixPath(final_path).parent)
        staging = parent + "/.staging-enrich-" + uuid.uuid4().hex
        try:
            self.fs.create_dir(self._path(staging), recursive=True)
        except Exception as error:
            raise WorkerError("could not create HDFS staging directory") from error
        try:
            for name in ("data.parquet", "_manifest.json", "_SUCCESS"):
                data = (local_dir / name).read_bytes()
                try:
                    with self.fs.open_output_stream(self._path(staging + "/" + name)) as stream:
                        stream.write(data)
                except Exception as error:
                    raise WorkerError(f"could not write HDFS staging file: {name}") from error
            if self.exists(final_path):
                if self.exists(final_path + "/_SUCCESS"):
                    return "already_complete"
                raise WorkerError("incomplete immutable destination already exists")
            try:
                self.fs.move(self._path(staging), self._path(final_path))
            except Exception as error:
                raise WorkerError("could not publish the HDFS output directory") from error
            staging = ""
            return "published"
        finally:
            if staging:
                try:
                    self.fs.delete_dir(self._path(staging))
                except Exception:
                    pass


class SshHdfs:
    def __init__(self, host: str, identity_file: Path, hdfs_bin: str = "/opt/hadoop/bin/hdfs"):
        if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+", host):
            raise ValueError("host must be user@hostname")
        self.host = host
        self.identity_file = identity_file.expanduser().resolve()
        self.hdfs_bin = hdfs_bin
        if not self.identity_file.is_file():
            raise FileNotFoundError(self.identity_file)
        control_path = str(self.identity_file.parent / "cosmos-gpu-%C")
        self.ssh = ["ssh", "-i", str(self.identity_file), "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15", host]
        self.ssh[1:1] = ["-o", "ControlMaster=auto", "-o", "ControlPersist=300",
                         "-o", f"ControlPath={control_path}"]

    def _run(self, command: str, *, input_bytes: bytes | None = None,
             check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run([*self.ssh, command], input=input_bytes,
                                capture_output=True, check=False)
        if check and result.returncode:
            message = result.stderr.decode("utf-8", "replace").strip().splitlines()
            raise WorkerError((message[-1] if message else "remote HDFS command failed")[:1000])
        return result

    def list(self, pattern: str) -> list[str]:
        result = self._run(f"{shlex.quote(self.hdfs_bin)} dfs -ls {shlex.quote(pattern)}", check=False)
        if result.returncode:
            error = result.stderr.decode("utf-8", "replace").lower()
            if "no such file" in error or "file does not exist" in error:
                return []
            raise WorkerError(error[-1000:])
        paths = []
        for raw in result.stdout.decode("utf-8", "strict").splitlines():
            fields = raw.split()
            if len(fields) >= 8 and fields[0][0] in "-d":
                paths.append(fields[-1])
        return sorted(set(paths))

    def exists(self, path: str) -> bool:
        command = f"{shlex.quote(self.hdfs_bin)} dfs -test -e {shlex.quote(path)}"
        return self._run(command, check=False).returncode == 0

    def read(self, path: str) -> bytes:
        return self._run(f"{shlex.quote(self.hdfs_bin)} dfs -cat {shlex.quote(path)}").stdout

    def publish(self, local_dir: Path, final_path: str) -> str:
        if self.exists(final_path + "/_SUCCESS"):
            return "already_complete"
        parent = str(PurePosixPath(final_path).parent)
        staging = parent + "/.staging-gpu-" + uuid.uuid4().hex
        self._run(f"{shlex.quote(self.hdfs_bin)} dfs -mkdir -p {shlex.quote(staging)}")
        try:
            for name in ("data.parquet", "_manifest.json", "_SUCCESS"):
                data = (local_dir / name).read_bytes()
                destination = staging + "/" + name
                command = f"{shlex.quote(self.hdfs_bin)} dfs -put - {shlex.quote(destination)}"
                self._run(command, input_bytes=data)
            if self.exists(final_path):
                if self.exists(final_path + "/_SUCCESS"):
                    return "already_complete"
                raise WorkerError("incomplete immutable destination already exists")
            self._run(f"{shlex.quote(self.hdfs_bin)} dfs -mv {shlex.quote(staging)} {shlex.quote(final_path)}")
            staging = ""
            return "published"
        finally:
            if staging:
                self._run(f"{shlex.quote(self.hdfs_bin)} dfs -rm -r -skipTrash {shlex.quote(staging)}",
                          check=False)


def _safe_parts(path: str, input_type: str) -> tuple[list[str], str]:
    parts = PurePosixPath(path).parts
    if input_type == "historical":
        names = [part for part in parts if part.startswith(("run_id=", "batch="))]
        expected = ("run_id=", "batch=")
    elif input_type == "realtime":
        names = [part for part in parts if part.startswith(("topic=", "partition=", "start="))]
        expected = ("topic=", "partition=", "start=")
    else:
        raise ValueError("input_type must be historical or realtime")
    if len(names) != len(expected) or any(not name.startswith(prefix) for name, prefix in zip(names, expected)):
        raise WorkerError(f"unexpected {input_type} input path: {path}")
    if any(SAFE.fullmatch(name) is None for name in names):
        raise WorkerError("unsafe HDFS partition component")
    batch_root = str(PurePosixPath(path).parent)
    return names, batch_root


def _stream_key(input_root: str) -> str:
    """The Kafka partition (or historical run) a batch belongs to."""
    return "/".join(part for part in PurePosixPath(input_root).parts
                    if part.startswith(("topic=", "partition=", "run_id=")))


def _round_robin(pending: list, newest_first: bool) -> list:
    """Order pending batches so no one partition can starve the others.

    Sorting the whole list by path looks like "newest first" and is not: the
    path sorts by partition before it sorts by offset, so partition=2 has to
    drain entirely before partition=1 gets a single batch. With a backlog of
    11,828 batches that starved two of three partitions for a day - two thirds
    of the live feed stayed unanalysed while the worker reported success.

    Sort within a partition, then take one from each in turn. This is the same
    guarantee AI/ner/realtime_runner.py keeps with its round-robin cursor.
    """
    streams: dict[str, list] = {}
    for item in pending:
        streams.setdefault(_stream_key(item[2]), []).append(item)
    for batches in streams.values():
        batches.sort(key=lambda item: item[2], reverse=newest_first)
    ordered = []
    while any(streams.values()):
        for key in sorted(streams):
            if streams[key]:
                ordered.append(streams[key].pop(0))
    return ordered


def open_bridge(config: dict, transport: str):
    hdfs_bin = config.get("hdfs_bin", "/opt/hadoop/bin/hdfs")
    if transport == "local":
        return LocalHdfs(hdfs_bin, config.get("hdfs_uri"))
    if transport == "ssh":
        if not config.get("host") or not config.get("identity_file"):
            raise WorkerError("ssh transport requires host and identity_file")
        return SshHdfs(config["host"], Path(config["identity_file"]), hdfs_bin)
    raise WorkerError("transport must be local or ssh")


def run_pass(config: dict, engine: FinbertEngine, maximum: int | None = None,
             bridge=None) -> dict:
    if bridge is None:
        bridge = open_bridge(config, config.get("transport", "ssh"))
    selected = []
    for source_index, source in enumerate(config["sources"]):
        output_root = source["output_base"].rstrip("/") + "/model_version=" + engine.provenance["model_version"]
        output_root += "/source_model_version=" + source["source_model_version"]
        if source["type"] == "historical":
            output_glob = output_root + "/run_id=*/batch=*/_SUCCESS"
        else:
            output_glob = output_root + "/topic=*/partition=*/start=*/_SUCCESS"
        completed = set(bridge.list(output_glob))
        source_pending = []
        for marker in bridge.list(source["input_success_glob"]):
            parts, input_root = _safe_parts(marker, source["type"])
            output_path = output_root + "/" + "/".join(parts)
            if output_path + "/_SUCCESS" not in completed:
                source_pending.append((source_index, source["type"], input_root, output_path))
        source_pending = _round_robin(source_pending, source.get("newest_first", False))
        source_limit = source.get("max_per_pass")
        if source_limit is not None:
            source_pending = source_pending[:source_limit]
        selected.extend(source_pending)
    # Python's sort is stable, preserving each source's oldest/newest policy.
    selected.sort(key=lambda item: item[0])
    if maximum is not None:
        selected = selected[:maximum]
    outcomes, failures = [], []
    for _source_index, input_type, input_root, output_root in selected:
        work = Path(tempfile.mkdtemp(prefix="cosmos-gpu-news-"))
        try:
            source_dir, output_dir = work / "source", work / "output"
            source_dir.mkdir()
            for name in ("data.parquet", "_manifest.json", "_SUCCESS"):
                (source_dir / name).write_bytes(bridge.read(input_root + "/" + name))
            manifest = enrich_local_batch(source_dir, output_dir, input_root, engine)
            status = bridge.publish(output_dir, output_root)
            outcomes.append({"input": input_root, "output": output_root, "status": status,
                             "records": manifest["output"]["records"],
                             "status_counts": manifest["output"]["status_counts"]})
        except Exception as error:
            failures.append({"input": input_root, "error_type": type(error).__name__,
                             "message": str(error)[:1000]})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return {"status": "failed" if failures else "complete", "selected": len(selected),
            "succeeded": len(outcomes), "failed": len(failures),
            "outcomes": outcomes, "failures": failures}


def load_config(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or not isinstance(value.get("sources"), list)
            or not value["sources"]):
        raise ValueError("worker config requires sources")
    if value.get("transport", "ssh") not in {"local", "ssh"}:
        raise ValueError("transport must be local or ssh")
    for source in value["sources"]:
        if (not isinstance(source, dict) or source.get("type") not in {"historical", "realtime"}
                or not all(isinstance(source.get(key), str) and source[key]
                           for key in ("input_success_glob", "output_base", "source_model_version"))):
            raise ValueError("invalid worker source configuration")
        limit = source.get("max_per_pass")
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("max_per_pass must be a positive integer")
        if not isinstance(source.get("newest_first", False), bool):
            raise ValueError("newest_first must be boolean")
    return value


@contextmanager
def exclusive_lock(path: Path):
    import fcntl
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkerError("another GPU news worker is already running") from error
        yield


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--transport", choices=("local", "ssh"),
                        help="default: the config's transport, else ssh")
    parser.add_argument("--device", default="cpu",
                        help="cpu is enough for the live batch; cuda is for the backfill")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--loop-seconds", type=int)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    transport = args.transport or config.get("transport", "ssh")
    with exclusive_lock(Path(config.get("lock_file", "~/.cache/cosmos/news-enrich-worker.lock"))):
        engine = FinbertEngine(args.bundle, args.device, args.batch_size)
        bridge = open_bridge(config, transport)
        while True:
            result = run_pass(config, engine, args.max_batches, bridge)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            if args.loop_seconds is None:
                return 1 if result["failed"] else 0
            if args.loop_seconds < 10:
                raise ValueError("loop-seconds must be at least 10")
            time.sleep(args.loop_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
