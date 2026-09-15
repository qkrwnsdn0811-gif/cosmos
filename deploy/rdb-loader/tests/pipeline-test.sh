#!/usr/bin/env bash
# No Hadoop, database, credentials, or network access are used in these tests.
set -euo pipefail
repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/cosmos-loader-pipeline.XXXXXX")"
cleanup() {
    [[ -d "$fixture" && "${fixture##*/}" == cosmos-loader-pipeline.* ]] && rm -rf -- "$fixture"
}
trap cleanup EXIT
export RDB_LOADER_APP_ROOT="$fixture/repo with spaces"
mkdir -p "$RDB_LOADER_APP_ROOT/deploy/rdb-loader" "$RDB_LOADER_APP_ROOT/AI/rdb_loader" "$fixture/bin"
cp "$repository/deploy/rdb-loader/run-pipeline.sh" "$RDB_LOADER_APP_ROOT/deploy/rdb-loader/"
: > "$RDB_LOADER_APP_ROOT/AI/rdb_loader/spark_aggregate.py"
export FAKE_CALLS="$fixture/calls"
export FAKE_SPARK_ARGS="$fixture/spark-args"
export FAKE_LOADER_ARGS="$fixture/loader-args"
export SPARK_BIN="$fixture/bin/spark submit"
cat > "$SPARK_BIN" <<'SPARK'
#!/usr/bin/env bash
printf 'spark\n' >> "$FAKE_CALLS"
printf '%s\n' "$@" > "$FAKE_SPARK_ARGS"
exit "${FAKE_SPARK_EXIT:-0}"
SPARK
cat > "$RDB_LOADER_APP_ROOT/deploy/rdb-loader/run-loader.sh" <<'LOADER'
#!/usr/bin/env bash
printf 'loader\n' >> "$FAKE_CALLS"
printf '%s\n' "$@" > "$FAKE_LOADER_ARGS"
exit "${FAKE_LOADER_EXIT:-0}"
LOADER
chmod +x "$SPARK_BIN"
snapshot_id='10000000-0000-0000-0000-000000000001'
arguments=(--features 'hdfs://cosmos-master:9000/features/run/data'
    --output-root 'hdfs://cosmos-master:9000/scores/' --snapshot-id "$snapshot_id"
    --as-of-at '2026-09-15T00:00:00Z' --model-version 'relation features v1')
run_case() {
    : > "$FAKE_CALLS"
    if bash "$RDB_LOADER_APP_ROOT/deploy/rdb-loader/run-pipeline.sh" "$@" > "$fixture/output" 2>&1; then
        status=0
    else
        status=$?
    fi
}
fail() { echo "FAIL: $*" >&2; exit 1; }

run_case "${arguments[@]}"
[[ "$status" == 0 ]] || fail 'successful pipeline failed'
[[ "$(cat "$FAKE_CALLS")" == $'spark\nloader' ]] || fail 'loader did not run exactly once after Spark'
grep -Fxq "$RDB_LOADER_APP_ROOT/AI/rdb_loader/spark_aggregate.py" "$FAKE_SPARK_ARGS" || fail 'producer path with spaces was split'
grep -Fxq 'relation features v1' "$FAKE_SPARK_ARGS" || fail 'model version argument was split'
grep -Fxq "hdfs://cosmos-master:9000/scores/$snapshot_id" "$FAKE_SPARK_ARGS" || fail 'unexpected producer output'
[[ "$(cat "$FAKE_LOADER_ARGS")" == $'--input\n'"hdfs://cosmos-master:9000/scores/$snapshot_id" ]] || fail 'loader input did not match producer output'
echo 'PASS: successful Spark output is passed intact to the Loader'

export FAKE_SPARK_EXIT=17
run_case "${arguments[@]}"
[[ "$status" == 17 && "$(cat "$FAKE_CALLS")" == spark ]] || fail 'Spark failure was swallowed or Loader ran'
unset FAKE_SPARK_EXIT
echo 'PASS: Spark failure prevents Loader execution and preserves exit code'

export FAKE_LOADER_EXIT=23
run_case "${arguments[@]}"
[[ "$status" == 23 ]] || fail 'Loader failure was swallowed'
unset FAKE_LOADER_EXIT
echo 'PASS: Loader failure propagates to the scheduler'

run_case "${arguments[@]}" --output-root hdfs:///scores
[[ "$status" == 2 && ! -s "$FAKE_CALLS" ]] || fail 'incomplete HDFS authority reached Spark'
run_case "${arguments[@]}" --snapshot-id ../escape
[[ "$status" == 2 && ! -s "$FAKE_CALLS" ]] || fail 'invalid snapshot ID reached Spark'
run_case "${arguments[@]}" --allow-empty
[[ "$status" == 2 && ! -s "$FAKE_CALLS" ]] || fail 'pipeline accepted implicit empty-graph publication'
echo 'PASS: invalid destination, snapshot ID, and unsupported options are rejected before execution'
