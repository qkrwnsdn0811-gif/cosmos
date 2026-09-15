"""Run on the Hadoop Master after the aggregate job has succeeded."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .contract import ContractError
from .source import local_snapshot, prepare_snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Completed local/HDFS snapshot directory")
    parser.add_argument("--validate-only", action="store_true", help="Validate all files and rows without connecting to PostgreSQL")
    parser.add_argument("--allow-empty", action="store_true", help="Explicitly allow replacing current with an empty graph")
    args = parser.parse_args(argv)
    try:
        with local_snapshot(args.input) as root:
            with prepare_snapshot(root, input_uri=args.input, allow_empty=args.allow_empty) as prepared:
                result = {"snapshot_id": str(prepared.manifest.snapshot_id), "record_count": prepared.manifest.record_count, "stored_count": prepared.stored_count}
                if args.validate_only:
                    result["status"] = "VALIDATED"
                else:
                    import psycopg
                    from .postgres import publish_snapshot

                    # libpq also reads PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSFILE.
                    with psycopg.connect(os.environ.get("DATABASE_URL", ""), connect_timeout=15, application_name="cosmos-rdb-loader") as connection:
                        published = publish_snapshot(connection, prepared.manifest, prepared.rows())
                    result["status"] = "ALREADY_PUBLISHED" if published.already_published else "PUBLISHED"
                print(json.dumps(result))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        # File/contract errors contain no connection strings or environment values.
        print(f"rdb-loader: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # DB/Hadoop errors can contain credentials or row contents; keep CLI logs redacted.
        print(f"rdb-loader: {type(exc).__name__}; snapshot was not published", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
