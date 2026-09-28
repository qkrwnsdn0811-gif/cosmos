"""Discover and analyze pending immutable realtime-news HDFS batches.

HDFS completion markers are the only durable source of truth.  The optional
local state file stores only a round-robin cursor so one busy Kafka partition
cannot starve another partition.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

from realtime_analyzer import (
    AnalysisError,
    CONTRACT_VERSION,
    DATASET,
    MINIMUM_CONFIDENCE,
    analysis_identity,
    analyze_batch,
    canonical_json,
    parse_json_object,
    sha256,
    store_for,
    strict_sha256,
)


DEFAULT_RAW_GLOB = (
    "hdfs://100.117.115.44:9000/data-lake/raw/realtime/news/"
    "topic=news.raw/partition=*/start=*/_manifest.json"
)
DEFAULT_OUTPUT_BASE = (
    "hdfs://100.117.115.44:9000/data-lake/analyzed/news/company-mentions"
)
RAW_GLOB_RE = re.compile(
    r"(?P<root>/[^*?\[\]]*)/topic=(?P<topic>[A-Za-z0-9._-]+)/"
    r"partition=\*/start=\*/_manifest\.json"
)
SAFE_COMPONENT_RE = re.compile(r"[A-Za-z0-9._-]+")


class RunnerError(RuntimeError):
    pass


def hdfs_location(authority: str, path: str) -> str:
    return urlunsplit(("hdfs", authority, path, "", ""))


def parse_raw_glob(value: str) -> tuple[str, str, str]:
    parts = urlsplit(value)
    if parts.scheme != "hdfs" or not parts.netloc:
        raise RunnerError("raw glob must be a full hdfs:// URI")
    match = RAW_GLOB_RE.fullmatch(parts.path)
    if match is None:
        raise RunnerError("raw glob must end in topic=<topic>/partition=*/start=*/_manifest.json")
    root = str(PurePosixPath(match.group("root")))
    if root != match.group("root") or ".." in PurePosixPath(root).parts:
        raise RunnerError("unsafe raw glob root")
    return parts.netloc, root, match.group("topic")


def normalize_hdfs_base(value: str) -> tuple[str, str]:
    parts = urlsplit(value.rstrip("/"))
    if parts.scheme != "hdfs" or not parts.netloc or not parts.path.startswith("/"):
        raise RunnerError("output base must be a full hdfs:// URI")
    path = str(PurePosixPath(parts.path))
    if path != parts.path or ".." in PurePosixPath(path).parts:
        raise RunnerError("unsafe output base")
    return parts.netloc, path


def normalize_listing_path(path: str, authority: str) -> str:
    if path.startswith("/"):
        return hdfs_location(authority, path)
    parts = urlsplit(path)
    if parts.scheme != "hdfs" or parts.netloc != authority:
        raise RunnerError("HDFS listing escaped the configured authority")
    return urlunsplit(("hdfs", parts.netloc, parts.path, "", ""))


def list_hdfs(glob: str, hdfs_bin: str) -> list[str]:
    result = subprocess.run(
        [hdfs_bin, "dfs", "-ls", glob], capture_output=True, text=True, check=False
    )
    if result.returncode:
        stderr = result.stderr.lower()
        if result.returncode == 1 and ("no such file" in stderr or "file does not exist" in stderr):
            return []
        detail = result.stderr.strip().splitlines()
        raise RunnerError((detail[-1] if detail else "HDFS listing failed")[:500])
    authority = urlsplit(glob).netloc
    paths = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Found "):
            continue
        fields = line.split()
        if len(fields) < 8:
            raise RunnerError("unexpected HDFS listing format")
        paths.append(normalize_listing_path(fields[-1], authority))
    return sorted(set(paths))


def raw_key(marker: str, raw_glob: str) -> tuple[str, int, int]:
    authority, root, expected_topic = parse_raw_glob(raw_glob)
    marker = normalize_listing_path(marker, authority)
    parts = urlsplit(marker)
    prefix = re.escape(root)
    match = re.fullmatch(
        prefix + r"/topic=([A-Za-z0-9._-]+)/partition=(\d+)/"
        r"start=(\d{20})/_manifest\.json",
        parts.path,
    )
    if match is None or match.group(1) != expected_topic:
        raise RunnerError("raw discovery returned a path outside the configured final batch tree")
    return match.group(1), int(match.group(2)), int(match.group(3))


def output_success_glob(output_base: str, model_version: str, topic: str) -> str:
    if SAFE_COMPONENT_RE.fullmatch(model_version or "") is None:
        raise RunnerError("unsafe model_version")
    if SAFE_COMPONENT_RE.fullmatch(topic or "") is None:
        raise RunnerError("unsafe topic")
    authority, root = normalize_hdfs_base(output_base)
    path = (
        f"{root}/model_version={model_version}/topic={topic}/"
        "partition=*/start=*/_SUCCESS"
    )
    return hdfs_location(authority, path)


def success_key(marker: str, output_base: str, model_version: str, topic: str) -> tuple[str, int, int]:
    authority, root = normalize_hdfs_base(output_base)
    marker = normalize_listing_path(marker, authority)
    parts = urlsplit(marker)
    prefix = re.escape(root)
    match = re.fullmatch(
        prefix + r"/model_version=([A-Za-z0-9._-]+)/topic=([A-Za-z0-9._-]+)/"
        r"partition=(\d+)/start=(\d{20})/_SUCCESS",
        parts.path,
    )
    if match is None or match.group(1) != model_version or match.group(2) != topic:
        raise RunnerError("analyzed discovery returned a path outside the configured output tree")
    return match.group(2), int(match.group(3)), int(match.group(4))


def read_completion_manifest(store, success_marker: str) -> dict:
    suffix = "/_SUCCESS"
    if not success_marker.endswith(suffix):
        raise RunnerError("analyzed success marker name mismatch")
    data = store.read(success_marker[:-len(suffix)] + "/_manifest.json")
    return parse_json_object(data, "completed analyzed _manifest.json")


def validate_completion_config(
    *,
    manifest: dict,
    input_uri: str,
    model_version: str,
    analysis_version: str,
    aliases_sha256: str,
    companies_sha256: str,
) -> None:
    source = manifest.get("input")
    if not isinstance(source, dict) or source.get("uri") != input_uri:
        raise RunnerError("completed analysis points to a different raw input")
    raw_digest = strict_sha256(source.get("manifest_sha256"), "completed input manifest")
    output = manifest.get("output")
    if (manifest.get("version") != 1 or manifest.get("dataset") != DATASET
            or manifest.get("schema_version") != CONTRACT_VERSION
            or not isinstance(output, dict)):
        raise RunnerError("completed analysis manifest contract is invalid")
    valid = source.get("valid_records")
    records, duplicates = output.get("records"), output.get("duplicate_records")
    if (type(valid) is not int or valid < 0 or type(records) is not int or records < 0
            or type(duplicates) is not int or duplicates < 0 or records + duplicates != valid):
        raise RunnerError("completed analysis dedup counts are invalid")
    expected_identity, expected_id = analysis_identity(
        input_uri,
        raw_digest,
        aliases_sha256,
        companies_sha256,
        model_version,
        analysis_version,
    )
    if (manifest.get("identity") != expected_identity
            or manifest.get("analysis_id") != expected_id
            or manifest.get("model_version") != model_version
            or manifest.get("analysis_version") != analysis_version
            or manifest.get("aliases_sha256") != aliases_sha256
            or manifest.get("companies_sha256") != companies_sha256
            or manifest.get("minimum_confidence") != MINIMUM_CONFIDENCE):
        raise RunnerError(
            "completed analysis was produced by a different configuration; "
            "publish the new analysis under a new model_version"
        )


def input_uri_from_marker(marker: str) -> str:
    suffix = "/_manifest.json"
    if not marker.endswith(suffix):
        raise RunnerError("raw marker name mismatch")
    return marker[:-len(suffix)]


def fair_select(
    pending: dict[int, list[tuple[object, str]]], max_batches: int, after_partition: int | None
) -> tuple[list[str], int | None]:
    if type(max_batches) is not int or max_batches <= 0:
        raise RunnerError("max_batches must be positive")
    queues = {
        partition: sorted(values, key=lambda value: value[0])
        for partition, values in pending.items() if values
    }
    partitions = sorted(queues)
    if not partitions:
        return [], after_partition
    if after_partition in partitions:
        index = (partitions.index(after_partition) + 1) % len(partitions)
    elif after_partition is None:
        index = 0
    else:
        index = next((i for i, value in enumerate(partitions) if value > after_partition), 0)
    order = partitions[index:] + partitions[:index]
    selected: list[str] = []
    last = after_partition
    while len(selected) < max_batches and queues:
        made_progress = False
        for partition in list(order):
            queue = queues.get(partition)
            if not queue:
                continue
            _start, marker = queue.pop(0)
            selected.append(input_uri_from_marker(marker))
            last = partition
            made_progress = True
            if not queue:
                queues.pop(partition, None)
            if len(selected) == max_batches:
                break
        if not made_progress:
            break
    return selected, last


def read_state(path: str | Path | None) -> dict:
    if path is None or not Path(path).exists():
        return {"after_partition": None, "attempt_sequence": 0, "failures": {}}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        cursor = value.get("after_partition")
        cursor = cursor if type(cursor) is int and cursor >= 0 else None
        sequence = value.get("attempt_sequence")
        sequence = sequence if type(sequence) is int and sequence >= 0 else 0
        failures = value.get("failures")
        if not isinstance(failures, dict):
            failures = {}
        clean_failures = {}
        for uri, item in failures.items():
            if not isinstance(uri, str) or not isinstance(item, dict):
                continue
            attempts, last = item.get("attempts"), item.get("last_attempt_sequence")
            if type(attempts) is int and attempts > 0 and type(last) is int and last >= 0:
                clean_failures[uri] = {"attempts": attempts, "last_attempt_sequence": last}
        return {"after_partition": cursor, "attempt_sequence": sequence, "failures": clean_failures}
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return {"after_partition": None, "attempt_sequence": 0, "failures": {}}


def read_cursor(path: str | Path | None) -> int | None:
    return read_state(path)["after_partition"]


def select_with_retry_quota(
    pending: dict[int, list[tuple[int, str]]], max_batches: int, state: dict
) -> tuple[list[str], int | None, int]:
    if type(max_batches) is not int or max_batches <= 0:
        raise RunnerError("max_batches must be positive")
    known_failures = state.get("failures", {})
    fresh: dict[int, list[tuple[int, str]]] = {}
    retries: dict[int, list[tuple[tuple[int, int, int], str]]] = {}
    for partition, values in pending.items():
        for start, marker in values:
            uri = input_uri_from_marker(marker)
            failure = known_failures.get(uri)
            if failure is None:
                fresh.setdefault(partition, []).append((start, marker))
            else:
                order = (failure["attempts"], failure["last_attempt_sequence"], start)
                retries.setdefault(partition, []).append((order, marker))
    fresh_count = sum(len(values) for values in fresh.values())
    retry_count = sum(len(values) for values in retries.values())
    if fresh_count:
        retry_limit = min(retry_count, max_batches // 4)
        fresh_limit = min(fresh_count, max_batches - retry_limit)
        retry_limit = min(retry_count, max_batches - fresh_limit)
    else:
        fresh_limit, retry_limit = 0, min(retry_count, max_batches)
    cursor = state.get("after_partition")
    selected, cursor = fair_select(fresh, fresh_limit, cursor) if fresh_limit else ([], cursor)
    retried, cursor = fair_select(retries, retry_limit, cursor) if retry_limit else ([], cursor)
    return selected + retried, cursor, len(retried)


def write_state(path: str | Path | None, value: dict) -> None:
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    data = canonical_json(value) + b"\n"
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


@contextmanager
def exclusive_lock(path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        try:
            import fcntl
        except ImportError as error:  # pragma: no cover - deployed target is Linux
            raise RunnerError("runner lock requires a POSIX host") from error
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RunnerError("another news analysis runner is active") from error
        yield
    finally:
        stream.close()


def discover_pending(
    *, raw_glob: str, output_base: str, model_version: str, analysis_version: str,
    aliases_sha256: str, companies_sha256: str, hdfs_bin: str,
    list_paths=list_hdfs, completion_manifest=None,
) -> tuple[dict[int, list[tuple[int, str]]], dict]:
    _authority, _root, topic = parse_raw_glob(raw_glob)
    raw_markers = list_paths(raw_glob, hdfs_bin)
    completed_markers = list_paths(output_success_glob(output_base, model_version, topic), hdfs_bin)
    completed_by_key = {
        success_key(path, output_base, model_version, topic): path for path in completed_markers
    }
    pending: dict[int, list[tuple[int, str]]] = {}
    discovered_by_key: dict[tuple[str, int, int], str] = {}
    for marker in raw_markers:
        key = raw_key(marker, raw_glob)
        if key in discovered_by_key:
            continue
        discovered_by_key[key] = marker
    relevant_completed = set(discovered_by_key) & set(completed_by_key)
    if relevant_completed:
        if completion_manifest is None:
            completion_store = store_for(output_base, hdfs_bin)
            completion_manifest = lambda marker: read_completion_manifest(completion_store, marker)
        for key in sorted(relevant_completed):
            marker = completed_by_key[key]
            validate_completion_config(
                manifest=completion_manifest(marker),
                input_uri=input_uri_from_marker(discovered_by_key[key]),
                model_version=model_version,
                analysis_version=analysis_version,
                aliases_sha256=aliases_sha256,
                companies_sha256=companies_sha256,
            )
    for key, marker in discovered_by_key.items():
        if key not in relevant_completed:
            _topic, partition, start = key
            pending.setdefault(partition, []).append((start, marker))
    for values in pending.values():
        values.sort(key=lambda value: value[0])
    stats = {
        "discovered": len(discovered_by_key),
        "completed": len(relevant_completed),
        "pending": sum(len(values) for values in pending.values()),
        "completed_markers": len(completed_by_key),
        "partitions": sorted(pending),
    }
    return pending, stats


def run_once(
    *,
    raw_glob: str,
    output_base: str,
    aliases_path: str | Path,
    companies_path: str | Path,
    model_version: str,
    analysis_version: str,
    max_batches: int,
    state_file: str | Path | None,
    hdfs_bin: str,
    list_paths=list_hdfs,
    analyzer=analyze_batch,
    completion_manifest=None,
) -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    aliases_digest = sha256(Path(aliases_path).read_bytes())
    companies_digest = sha256(Path(companies_path).read_bytes())
    pending, discovery = discover_pending(
        raw_glob=raw_glob,
        output_base=output_base,
        model_version=model_version,
        analysis_version=analysis_version,
        aliases_sha256=aliases_digest,
        companies_sha256=companies_digest,
        hdfs_bin=hdfs_bin,
        list_paths=list_paths,
        completion_manifest=completion_manifest,
    )
    state = read_state(state_file)
    all_pending = {
        input_uri_from_marker(marker)
        for values in pending.values() for _start, marker in values
    }
    state["failures"] = {
        uri: value for uri, value in state["failures"].items() if uri in all_pending
    }
    selected, cursor, retried = select_with_retry_quota(pending, max_batches, state)
    outcomes = []
    failures = []
    for input_uri in selected:
        try:
            result = analyzer(
                input_uri=input_uri,
                output_base=output_base,
                aliases_path=aliases_path,
                companies_path=companies_path,
                model_version=model_version,
                analysis_version=analysis_version,
                hdfs_bin=hdfs_bin,
            )
            outcomes.append({"input_uri": input_uri, "status": result["status"],
                             "output_uri": result["output_uri"]})
        except Exception as error:
            failures.append({
                "input_uri": input_uri,
                "error_type": type(error).__name__,
                "message": str(error)[:500],
            })
    sequence = state["attempt_sequence"]
    for outcome in outcomes:
        state["failures"].pop(outcome["input_uri"], None)
    for failure in failures:
        sequence += 1
        prior = state["failures"].get(failure["input_uri"], {"attempts": 0})
        state["failures"][failure["input_uri"]] = {
            "attempts": prior["attempts"] + 1,
            "last_attempt_sequence": sequence,
        }
    result = {
        "status": "failed" if failures else "complete",
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **discovery,
        "selected": len(selected),
        "retry_selected": retried,
        "fresh_selected": len(selected) - retried,
        "succeeded": len(outcomes),
        "failed": len(failures),
        "outcomes": outcomes,
        "failures": failures,
    }
    # Scheduling state is written only after the HDFS-backed outcomes are known.
    # Losing this file may change order, but never marks any batch complete.
    write_state(state_file, {
        "version": 1,
        "after_partition": cursor,
        "attempt_sequence": sequence,
        "failures": state["failures"],
        "updated_at": result["finished_at"],
        "last_run": {key: result[key] for key in ("selected", "succeeded", "failed")},
    })
    return result


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-glob", default=os.environ.get("NEWS_ANALYSIS_RAW_GLOB", DEFAULT_RAW_GLOB))
    parser.add_argument("--output-base", default=os.environ.get("NEWS_ANALYSIS_OUTPUT_BASE", DEFAULT_OUTPUT_BASE))
    parser.add_argument("--aliases", default=os.environ.get("NEWS_ANALYSIS_ALIASES", str(here / "data" / "aliases.csv")))
    parser.add_argument("--companies", default=os.environ.get("NEWS_ANALYSIS_COMPANIES", str(here / "data" / "companies.csv")))
    parser.add_argument("--model-version", default=os.environ.get("NEWS_ANALYSIS_MODEL_VERSION", "dict-v1.3"))
    parser.add_argument("--analysis-version", default=os.environ.get("NEWS_ANALYSIS_VERSION"), required=os.environ.get("NEWS_ANALYSIS_VERSION") is None)
    parser.add_argument("--max-batches", type=int, default=int(os.environ.get("NEWS_ANALYSIS_MAX_BATCHES", "50")))
    parser.add_argument("--state-file", default=os.environ.get("NEWS_ANALYSIS_STATE_FILE", "/var/lib/cosmos-news-analysis/runner-state.json"))
    parser.add_argument("--lock-file", default=os.environ.get("NEWS_ANALYSIS_LOCK_FILE", "/run/cosmos-news-analysis/runner.lock"))
    parser.add_argument("--hdfs-bin", default=os.environ.get("HDFS_BIN", "/opt/hadoop/bin/hdfs"))
    args = parser.parse_args(argv)
    try:
        with exclusive_lock(args.lock_file):
            result = run_once(
                raw_glob=args.raw_glob,
                output_base=args.output_base,
                aliases_path=args.aliases,
                companies_path=args.companies,
                model_version=args.model_version,
                analysis_version=args.analysis_version,
                max_batches=args.max_batches,
                state_file=args.state_file,
                hdfs_bin=args.hdfs_bin,
            )
    except (AnalysisError, RunnerError, OSError, ValueError) as error:
        print(json.dumps({"status": "fatal", "error_type": type(error).__name__,
                          "message": str(error)[:500]}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
