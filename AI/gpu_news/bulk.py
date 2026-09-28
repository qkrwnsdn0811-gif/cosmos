"""Move a backlog of batches to a GPU outside the cluster, in bulk.

The ten-minute timer keeps the live feed current on server CPU; this is for the
arrears behind it. Measured 2026-09-23: 10,402 batches, about 520,000 sentences.
Server CPU does 11 sentences a second, an RTX 4070 does 1,248 - a hundred-fold
difference that turns ten days into minutes.

The obstacle was never inference, it was moving the data. libhdfs cannot reach
the DataNodes from outside the cluster, so an off-box worker falls back to
``hdfs dfs`` over SSH: ten invocations a batch, 2.6 seconds of JVM startup each,
which is 75 hours for this backlog - slower than the CPU it was meant to beat.

So the transport is batched instead of the work. One libhdfs client on the
server packs many batches into a single tar on stdout, the GPU host processes
them locally with the same enrich_local_batch the timer uses, and a second
stream carries the results back for one publish pass. Bandwidth measured at
3 MB/s, which puts the whole backlog at roughly half an hour.

  # on the server
  python bulk.py pack --config <cfg> --limit 500 > chunk.tar

  # on the GPU host
  python bulk.py enrich --bundle <models> --device cuda < chunk.tar > done.tar

  # on the server
  python bulk.py publish --config <cfg> < done.tar

Chunks are independent and idempotent: a batch already published is skipped, so
an interrupted run is resumed by running it again.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile

from worker import (LocalHdfs, WorkerError, _round_robin, _safe_parts, load_config,
                    open_bridge)

INDEX_NAME = "index.json"
BATCH_FILES = ("data.parquet", "_manifest.json", "_SUCCESS")


def _read_index(work: Path) -> list[dict]:
    """The archive's manifest, checked against what actually arrived.

    A stream cut on a tar block boundary reads as a clean end of archive, and
    index.json is the last member written - so a transfer that dies late looks
    like a valid tar with no index, and one that dies earlier has an index
    naming batches that are not there. Either way the cause is the transfer,
    not the data, and the error should say so instead of a bare
    FileNotFoundError from somewhere inside publish. Measured 2026-09-23: one
    such cut discarded 1,500 GPU-enriched batches, ten minutes of work.
    """
    marker = work / INDEX_NAME
    if not marker.exists():
        raise WorkerError("archive incomplete: index.json missing - "
                          "the transfer was cut before the archive ended")
    index = json.loads(marker.read_text(encoding="utf-8"))
    missing = [entry["name"] for entry in index
               if not all((work / entry["name"] / name).exists() for name in BATCH_FILES)]
    if missing:
        raise WorkerError(f"archive incomplete: {len(missing)} of {len(index)} batches lack "
                          f"files (first: {missing[0]}) - the transfer was cut")
    return index




def pack(config: dict, bridge, limit: int, model_version: str, stream) -> dict:
    """Write a tar of pending batches to ``stream``."""
    chosen = []
    for index, source in enumerate(config["sources"]):
        output_root = (source["output_base"].rstrip("/") + "/model_version=" + model_version
                       + "/source_model_version=" + source["source_model_version"])
        if source["type"] == "historical":
            output_glob = output_root + "/run_id=*/batch=*/_SUCCESS"
        else:
            output_glob = output_root + "/topic=*/partition=*/start=*/_SUCCESS"
        done = set(bridge.list(output_glob))
        pending = []
        for marker in bridge.list(source["input_success_glob"]):
            parts, input_root = _safe_parts(marker, source["type"])
            output_path = output_root + "/" + "/".join(parts)
            if output_path + "/_SUCCESS" not in done:
                pending.append((index, source["type"], input_root, output_path))
        chosen.extend(_round_robin(pending, source.get("newest_first", False)))
    chosen = chosen[:limit]

    index = []
    with tarfile.open(fileobj=stream, mode="w|") as archive:
        for position, (_source, _type, input_root, output_root) in enumerate(chosen):
            name = f"{position:06d}"
            index.append({"name": name, "input": input_root, "output": output_root})
            for filename in BATCH_FILES:
                data = bridge.read(input_root + "/" + filename)
                info = tarfile.TarInfo(f"{name}/{filename}")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        blob = json.dumps(index, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo(INDEX_NAME)
        info.size = len(blob)
        archive.addfile(info, io.BytesIO(blob))
    return {"packed": len(index)}


def enrich(bundle: Path, device: str, batch_size: int, source: io.BufferedReader,
           target: io.BufferedWriter) -> dict:
    """Read a packed tar, enrich every batch, write the results as a tar."""
    from finbert_batch import enrich_local_batch, FinbertEngine

    work = Path(tempfile.mkdtemp(prefix="cosmos-bulk-"))
    try:
        with tarfile.open(fileobj=source, mode="r|") as archive:
            archive.extractall(work, filter="data")
        index = _read_index(work)
        engine = FinbertEngine(bundle, device, batch_size)

        done, failures = [], []
        for entry in index:
            batch = work / entry["name"]
            output = work / (entry["name"] + ".out")
            try:
                manifest = enrich_local_batch(batch, output, entry["input"], engine)
                done.append({**entry, "records": manifest["output"]["records"],
                             "status_counts": manifest["output"]["status_counts"]})
            except Exception as error:                      # noqa: BLE001 - reported, not raised
                failures.append({"input": entry["input"], "error": str(error)[:300]})

        with tarfile.open(fileobj=target, mode="w|") as archive:
            for entry in done:
                for filename in BATCH_FILES:
                    path = work / (entry["name"] + ".out") / filename
                    info = tarfile.TarInfo(f"{entry['name']}/{filename}")
                    info.size = path.stat().st_size
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
            blob = json.dumps(done, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo(INDEX_NAME)
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
        return {"enriched": len(done), "failed": len(failures), "failures": failures}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def publish(bridge, source: io.BufferedReader) -> dict:
    """Read an enriched tar and publish each batch to its recorded destination."""
    work = Path(tempfile.mkdtemp(prefix="cosmos-publish-"))
    try:
        with tarfile.open(fileobj=source, mode="r|") as archive:
            archive.extractall(work, filter="data")
        index = _read_index(work)
        published = already = 0
        failures = []
        for entry in index:
            try:
                status = bridge.publish(work / entry["name"], entry["output"])
                if status == "published":
                    published += 1
                else:
                    already += 1
            except Exception as error:                      # noqa: BLE001
                failures.append({"output": entry["output"], "error": str(error)[:300]})
        return {"published": published, "already_complete": already,
                "failed": len(failures), "failures": failures}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pack", "enrich", "publish"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--model-version")
    args = parser.parse_args(argv)

    if args.action == "enrich":
        if not args.bundle:
            parser.error("--bundle is required for enrich")
        report = enrich(args.bundle, args.device, args.batch_size,
                        sys.stdin.buffer, sys.stdout.buffer)
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr, flush=True)
        return 1 if report["failed"] else 0

    if not args.config:
        parser.error("--config is required on the cluster side")
    config = load_config(args.config)
    bridge = open_bridge(config, config.get("transport", "local"))

    if args.action == "pack":
        from finbert_batch import MODEL_VERSION
        report = pack(config, bridge, args.limit, args.model_version or MODEL_VERSION,
                      sys.stdout.buffer)
    else:
        report = publish(bridge, sys.stdin.buffer)
    print(json.dumps(report, ensure_ascii=False), file=sys.stderr, flush=True)
    return 1 if report.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
