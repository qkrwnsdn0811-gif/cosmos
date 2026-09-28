"""Collect selected companies and export only successful, validated snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from .store import dumps


class FetchError(RuntimeError):
    def __init__(self, kind, halt_source=False):
        self.kind, self.halt_source = kind, halt_source
        super().__init__(kind)


def fetch_isolated(company, start, end):
    environment = {k: v for k, v in os.environ.items()
                   if k not in {"KRX_ID", "KRX_PW"} and not k.startswith("NAVER_")}
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        result = subprocess.run([sys.executable, "-m", "services.stock_prices.worker",
            company["market"], company["ticker"], start.isoformat(), end.isoformat()],
            cwd=Path(__file__).resolve().parents[2], env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=120)
    except subprocess.TimeoutExpired:
        raise FetchError("provider_timeout") from None
    if len(result.stdout) > 32 * 1024 * 1024:
        raise FetchError("provider_response_too_large")
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise FetchError("provider_process_failed") from None
    if result.returncode or not payload.get("ok"):
        kind = payload.get("error", "provider_failed")
        raise FetchError(kind if isinstance(kind, str) and len(kind) < 100 else "provider_failed",
                         bool(payload.get("halt_source")))
    return payload["rows"]


@contextmanager
def process_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


def collect(companies, store, ranges, *, fetch=fetch_isolated, refresh=False,
            min_interval=1.0, attempts=2, on_progress=lambda item: None):
    outcomes, halted, last_call = [], set(), 0.0
    for company in companies:
        market = company["market"]
        start, end = ranges[market]
        item = {"market": market, "ticker": company["ticker"], "name": company["name"],
                "requested_start": start.isoformat(), "requested_end": end.isoformat()}
        old = store.coverage(company)
        covered = (old and old["status"] == "complete" and old["start_date"] <= start.isoformat()
                   and old["end_date"] >= end.isoformat())
        if covered and (not refresh or time.time() - old["last_success"] < 60):
            cached = store.rows(company, start, end)
            cached_rows = len(cached)
            item.update(status="cached" if cached_rows else "failed", rows=cached_rows)
            if not cached_rows:
                item["error"] = "no_prices_in_selected_range"
            else:
                item.update(first_date=cached[0]["trading_date"], last_date=cached[-1]["trading_date"],
                            quarantined_rows=store.quarantine_count(company, start, end))
        elif market in halted:
            item.update(status="blocked", error="provider_stopped_after_access_or_rate_limit")
        elif old and time.time() - old["last_attempt"] < 60 and old["status"] != "complete":
            item.update(status="deferred", error="minimum_retry_interval_60_seconds")
        else:
            # Refresh all retained history together, so a split correction cannot
            # mix old and newly adjusted prices or discard an earlier backfill.
            fetch_start = min(start, date.fromisoformat(old["start_date"])) if old and old["start_date"] else start
            fetch_end = max(end, date.fromisoformat(old["end_date"])) if old and old["end_date"] else end
            store.begin(company)
            for attempt in range(attempts):
                wait = min_interval - (time.monotonic() - last_call)
                if wait > 0:
                    time.sleep(wait)
                last_call = time.monotonic()
                try:
                    source_rows = fetch(company, fetch_start, fetch_end)
                    if not any(start.isoformat() <= row["trading_date"] <= end.isoformat()
                               and not row.get("quality_flags") for row in source_rows):
                        raise FetchError("no_prices_in_selected_range")
                    summary = store.replace(company, fetch_start, fetch_end, source_rows)
                    selected_rows = store.rows(company, start, end)
                    if not selected_rows:
                        raise FetchError("no_prices_in_selected_range")
                    item.update(summary)
                    item.update(status="collected", rows=len(selected_rows),
                                first_date=selected_rows[0]["trading_date"], last_date=selected_rows[-1]["trading_date"],
                                quarantined_rows=store.quarantine_count(company, start, end))
                    item.pop("error", None)
                    break
                except Exception as error:
                    kind = getattr(error, "kind", str(error) if isinstance(error, ValueError)
                                   and str(error).isascii() and str(error).isidentifier() else type(error).__name__)
                    item.update(status="failed", error=kind)
                    store.failed(company, kind)
                    if getattr(error, "halt_source", False):
                        halted.add(market)
                        break
                    if attempt + 1 < attempts:
                        time.sleep(2 * (attempt + 1))
            item["attempts"] = attempt + 1
        if item["status"] in {"collected", "cached"}:
            item["source_checked_at"] = datetime.fromtimestamp(store.coverage(company)["last_success"], timezone.utc).isoformat()
        outcomes.append(item)
        on_progress(item)
    return outcomes


def export_run(store, companies, ranges, outcomes, output_dir):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from importlib.metadata import version
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    destination = Path(output_dir).resolve() / run_id
    destination.mkdir(parents=True)
    successful = {(r["market"], r["ticker"]) for r in outcomes if r["status"] in {"collected", "cached"}}
    schema = pa.schema([
        ("ticker", pa.string()), ("market", pa.string()), ("trading_date", pa.string()),
        ("trading_at", pa.timestamp("us", tz="UTC")), ("interval_type", pa.string()),
        *[(field, pa.decimal128(20, 4)) for field in ("open_price", "high_price", "low_price", "close_price", "adj_close")],
        ("trading_volume", pa.int64()), ("currency", pa.string()), ("provider", pa.string()), ("price_basis", pa.string()),
    ])
    parquet = destination / "prices_daily.parquet"
    quarantine = destination / "quarantine.jsonl"
    row_count, quarantined_count = 0, 0
    # Write one company at a time: decades of data for 200 companies should
    # not require materializing the whole dataset twice in memory.
    with pq.ParquetWriter(parquet, schema, compression="snappy") as writer, quarantine.open("w", encoding="utf-8") as excluded_file:
        for company in companies:
            if (company["market"], company["ticker"]) not in successful:
                continue
            selected = store.rows(company, *ranges[company["market"]])
            items = [{"tradingAt": r["trading_at"],
                      **{name: float(r[field]) for name, field in (
                          ("openPrice", "open_price"), ("highPrice", "high_price"),
                          ("lowPrice", "low_price"), ("closePrice", "close_price"))},
                      "tradingVolume": r["trading_volume"]} for r in selected]
            chart = {"ticker": company["ticker"], "market": company["market"],
                     "currency": "KRW" if company["market"] == "KOSPI" else "USD", "interval": "1D",
                     "asOfAt": items[-1]["tradingAt"] if items else None, "items": items}
            chart_path = destination / "charts" / company["market"] / (company["ticker"] + ".json")
            chart_path.parent.mkdir(parents=True, exist_ok=True)
            chart_path.write_text(dumps(chart), encoding="utf-8")
            for row in selected:
                row["trading_at"] = datetime.fromisoformat(row["trading_at"])
                for field in ("open_price", "high_price", "low_price", "close_price", "adj_close"):
                    row[field] = Decimal(str(row[field])) if row[field] is not None else None
            writer.write_table(pa.Table.from_pylist(selected, schema=schema))
            row_count += len(selected)
            for excluded in store.quarantined_rows(company, *ranges[company["market"]]):
                excluded_file.write(dumps(excluded) + "\n")
                quarantined_count += 1
    manifest = {
        "schema_version": 1, "run_id": run_id,
        "status": "complete" if len(successful) == len(companies) else "partial",
        "selected_companies": len(companies), "successful_companies": len(successful), "rows": row_count,
        "quarantined_rows": quarantined_count, "quarantine": quarantine.name,
        "quarantine_sha256": hashlib.sha256(quarantine.read_bytes()).hexdigest(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "interval_type": "1D", "trading_at_convention": "exchange trading date at UTC midnight; not actual closing time",
        "parquet": parquet.name, "sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
        "parquet_bytes": parquet.stat().st_size, "outcomes": outcomes,
        "versions": {package: version(package) for package in ("pykrx", "finance-datareader", "pandas", "pyarrow")},
    }
    (destination / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
    (destination / "selection.json").write_text(dumps([{"market": c["market"], "ticker": c["ticker"]} for c in companies]), encoding="utf-8")
    return destination, manifest
