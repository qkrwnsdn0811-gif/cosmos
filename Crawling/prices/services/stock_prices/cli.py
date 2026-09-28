"""List/select, collect, validate, and explicitly load daily stock prices."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .collector import collect, export_run, process_lock
from .loader import load_rows, validate_rows
from .store import PriceStore, dumps
from .universe import DEFAULT_UNIVERSE, MARKETS, date_range, load_universe, select_companies


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    for name in ("list", "collect"):
        child = sub.add_parser(name)
        child.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
        child.add_argument("--markets", choices=MARKETS, nargs="+", default=list(MARKETS))
        group = child.add_mutually_exclusive_group()
        group.add_argument("--tickers", nargs="+")
        group.add_argument("--selection", type=Path, help="JSON list of market/ticker objects")
        if name == "collect":
            history = child.add_mutually_exclusive_group()
            history.add_argument("--years", type=int, help="collect the last 1..100 years")
            history.add_argument("--start", help="inclusive first requested date, YYYY-MM-DD")
            history.add_argument("--max-history", action="store_true",
                                 help="request all provider-available history from 1900-01-01 (default)")
            child.add_argument("--end")
            child.add_argument("--state-dir", type=Path, default=Path("state"))
            child.add_argument("--output-dir", type=Path, default=Path("output"))
            child.add_argument("--refresh", action="store_true", help="refresh completed ranges; 60 second cache still applies")
            child.add_argument("--request-interval", type=float, default=1.0)
            child.add_argument("--attempts", type=int, choices=(1, 2, 3), default=2)
    for name in ("validate", "load"):
        child = sub.add_parser(name)
        child.add_argument("--input", type=Path, required=True, help="prices_daily.parquet")
        child.add_argument("--allow-partial", action="store_true")
        if name == "load":
            child.add_argument("--dsn-env", default="STOCK_DATABASE_URL")
            child.add_argument("--commit", action="store_true", help="commit after full validation; default rolls back")
    hdfs = sub.add_parser("hdfs-publish", help="validate and publish a complete single-market snapshot to HDFS")
    hdfs.add_argument("--source", type=Path, required=True)
    hdfs.add_argument("--market", choices=MARKETS)
    hdfs.add_argument("--hdfs-root")
    hdfs.add_argument("--hdfs-uri")
    hdfs.add_argument("--hdfs-bin")
    hdfs.add_argument("--publish", action="store_true", help="publish after validation; default is a local-only plan")
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == "hdfs-publish":
            from .hdfs import publish_snapshot
            options = {"market": args.market, "hdfs_uri": args.hdfs_uri,
                       "hdfs_bin": args.hdfs_bin, "publish": args.publish}
            if args.hdfs_root is not None:
                options["hdfs_root"] = args.hdfs_root
            print(dumps(publish_snapshot(args.source, **options)))
            return 0
        if args.command in {"list", "collect"}:
            companies = select_companies(load_universe(args.universe), args.markets, args.tickers,
                json.loads(args.selection.read_text(encoding="utf-8")) if args.selection else None)
            if args.command == "list":
                print(dumps(companies))
                return 0
            if not 1 <= args.request_interval <= 60:
                raise ValueError("request-interval must be 1..60 seconds")
            ranges = {market: date_range(market, years=args.years, start=args.start, end=args.end)
                      for market in {c["market"] for c in companies}}
            with process_lock(args.state_dir / "collector.lock"):
                store = PriceStore(args.state_dir / "prices.db")
                try:
                    outcomes = collect(companies, store, ranges, refresh=args.refresh,
                        min_interval=args.request_interval, attempts=args.attempts,
                        on_progress=lambda item: print(dumps(item), flush=True))
                    directory, manifest = export_run(store, companies, ranges, outcomes, args.output_dir)
                finally:
                    store.close()
            print(dumps({"directory": str(directory), **{k: manifest[k] for k in
                ("status", "selected_companies", "successful_companies", "rows", "parquet_bytes")}}))
            return 0 if manifest["status"] == "complete" else 2
        import pyarrow.parquet as pq
        manifest = json.loads(args.input.with_name("manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            raise ValueError("unsupported dataset schema")
        if hashlib.sha256(args.input.read_bytes()).hexdigest() != manifest["sha256"]:
            raise ValueError("dataset checksum mismatch")
        if manifest["status"] != "complete" and not args.allow_partial:
            raise ValueError("partial collection; review manifest before using --allow-partial")
        table = pq.read_table(args.input)
        # The loader derives UTC midnight from trading_date. Avoid converting
        # this redundant timezone column for every row on Windows.
        if "trading_at" in table.column_names:
            table = table.drop(["trading_at"])
        rows = table.to_pylist()
        if len(rows) != manifest["rows"]:
            raise ValueError("manifest row count mismatch")
        if args.command == "validate":
            validated = validate_rows(rows)
            result = {"status": "valid", "rows": len(validated), "companies": len({(r["market"], r["ticker"]) for r in validated})}
        else:
            import psycopg
            dsn = os.environ.get(args.dsn_env)
            if not dsn:
                raise ValueError(f"set {args.dsn_env} outside Git; never put credentials in command arguments")
            with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as connection:
                result = load_rows(connection, rows, dry_run=not args.commit)
        print(dumps(result))
        return 0
    except (ValueError, OSError, KeyError) as error:
        # CLI validation errors contain no request headers or database DSNs.
        cli.exit(2, f"{type(error).__name__}: {error}\n")
    except Exception as error:
        cli.exit(2, f"{type(error).__name__}: operation failed; previous committed data is preserved\n")


if __name__ == "__main__":
    raise SystemExit(main())
