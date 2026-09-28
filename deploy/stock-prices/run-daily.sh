#!/usr/bin/env bash
# One completed-day refresh per market. Registration is an operator action.
set -euo pipefail
umask 027

market=${1:?usage: run-daily.sh KOSPI|NASDAQ}
case "$market" in KOSPI|NASDAQ) ;; *) printf '%s\n' 'invalid market' >&2; exit 2 ;; esac
project=${STOCK_PRICE_PROJECT_DIR:-/opt/cosmos/stock-prices/current/Crawling/prices}
python=${STOCK_PRICE_PYTHON:-$project/.venv/bin/python}
state=${STOCK_PRICE_STATE_DIR:-/var/lib/cosmos/stock-prices/state}
output=${STOCK_PRICE_OUTPUT_DIR:-/var/lib/cosmos/stock-prices/runs}
publish=${STOCK_PRICE_PUBLISH:-0}
load_pg=${STOCK_PRICE_PG_LOAD:-0}
case "$publish:$load_pg" in 0:0|1:0|1:1) ;; *) printf '%s\n' 'publish/load must be 0 or 1; PG load requires HDFS publication' >&2; exit 2 ;; esac

mkdir -p "$state/$market" "$output/$market"
# Hold across collection, validation, HDFS publication and optional PG load.
# Different markets have independent SQLite stores and can run independently.
exec 9>"$state/$market/daily.lock"
flock --nonblock --conflict-exit-code 75 9
run_area=$(mktemp -d "$output/$market/run-XXXXXXXX")
cd "$project"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
"$python" -m services.stock_prices.cli collect --markets "$market" --max-history --refresh \
  --state-dir "$state/$market" --output-dir "$run_area" \
  --request-interval "${STOCK_PRICE_REQUEST_INTERVAL:-1}" --attempts "${STOCK_PRICE_ATTEMPTS:-2}" \
  | tee "$run_area/collect.jsonl"

# Read the final structured result, never evaluate output as a shell command.
source_dir=$("$python" - "$run_area" <<'PY'
import json
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
with (root / "collect.jsonl").open(encoding="utf-8") as stream:
    last = None
    for line in stream:
        if line.strip():
            last = json.loads(line)
if not last or last.get("status") != "complete":
    raise SystemExit("collection_not_complete")
source = Path(last["directory"]).resolve(strict=True)
if source.parent != root or not source.is_dir():
    raise SystemExit("invalid_collection_directory")
print(source)
PY
)

arguments=(hdfs-publish --source "$source_dir" --market "$market"
  --hdfs-root "${STOCK_PRICE_HDFS_ROOT:-/data-lake/raw/stock-prices/daily}")
if [[ "$publish" == 1 ]]; then
  : "${HDFS_URI:?HDFS_URI must identify the intended Hadoop cluster}"
  arguments+=(--publish)
fi
# hdfs-publish always validates local metadata, all Parquet rows and quarantine
# before the first remote write. Without --publish it only reports the plan.
"$python" -m services.stock_prices.cli "${arguments[@]}" | tee "$run_area/hdfs-publication.json"

if [[ "$load_pg" == 1 ]]; then
  : "${STOCK_DATABASE_URL:?set STOCK_DATABASE_URL outside the repository}"
  "$python" -m services.stock_prices.cli load --input "$source_dir/prices_daily.parquet" --commit \
    | tee "$run_area/postgres-load.json"
fi
