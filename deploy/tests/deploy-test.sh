#!/usr/bin/env bash
# Exercises deployment state transitions without Docker, network access, or server state.
set -Eeuo pipefail

repository="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/cosmos-deploy-tests.XXXXXX")"
cleanup() {
    # Only the directory created by mktemp above may be removed.
    [[ -d "$fixture" && "${fixture##*/}" == cosmos-deploy-tests.* ]] && rm -rf -- "$fixture"
}
trap cleanup EXIT
mkdir -p "$fixture/bin" "$fixture/repo/deploy/scripts"
cp "$repository/deploy/scripts/deploy.sh" "$fixture/repo/deploy/scripts/deploy.sh"
cp "$repository/deploy/compose.yml" "$fixture/repo/deploy/compose.yml"

cat > "$fixture/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -eu
printf 'docker %s\n' "$*" >> "$FAKE_CALLS"
if [[ "$1" == image && "$2" == inspect ]]; then
    exit 0
fi
if [[ "$1" != compose ]]; then
    echo 'Unexpected docker command in test' >&2
    exit 90
fi
case " $* " in
    *' config --quiet '*) exit 0 ;;
    *' up '*' backend frontend '*)
        if [[ "$FAKE_SCENARIO" == start-failure && "$IMAGE_TAG" == candidate ]]; then
            exit 17
        fi
        if [[ "$FAKE_SCENARIO" == rollback-failure && "$IMAGE_TAG" == previous ]]; then
            exit 23
        fi
        printf '%s\n' "$IMAGE_TAG" > "$FAKE_ACTIVE"
        if [[ "$IMAGE_TAG" == candidate ]]; then
            case "$FAKE_SCENARIO" in
                terminate) kill -TERM "$PPID" ;;
                interrupt) kill -INT "$PPID" ;;
            esac
        fi
        exit 0
        ;;
    *' up '*' postgres redis '*) exit 0 ;;
    *) echo 'Unexpected docker compose command in test' >&2; exit 90 ;;
esac
DOCKER

cat > "$fixture/bin/curl" <<'CURL'
#!/usr/bin/env bash
set -eu
printf 'curl %s %s\n' "$IMAGE_TAG" "$*" >> "$FAKE_CALLS"
if [[ "$FAKE_SCENARIO" != success && "$IMAGE_TAG" == candidate ]]; then
    exit 22
fi
printf '{"status":"UP"}\n'
CURL

# Healthcheck retries are deterministic and immediate in these tests.
cat > "$fixture/bin/sleep" <<'SLEEP'
#!/usr/bin/env bash
exit 0
SLEEP
chmod +x "$fixture/bin/docker" "$fixture/bin/curl" "$fixture/bin/sleep"
export PATH="$fixture/bin:$PATH"

fail() { echo "FAIL: $*" >&2; exit 1; }

run_case() {
    local name="$1" scenario="$2" tag="$3" with_previous="$4"
    local case_dir="$fixture/$name"
    mkdir -p "$case_dir/state"
    export COSMOS_STATE_DIR="$case_dir/state"
    export COSMOS_ENV_FILE="$case_dir/app.env"
    export COSMOS_SMOKE_URL=https://test.invalid
    export COSMOS_RDB_LOADER_COMPOSE_OVERRIDE="$case_dir/rdb-loader.override.yml"
    export FAKE_SCENARIO="$scenario"
    export FAKE_CALLS="$case_dir/calls"
    export FAKE_ACTIVE="$case_dir/active"
    : > "$COSMOS_ENV_FILE"
    : > "$FAKE_CALLS"
    if [[ "${5:-}" == loader ]]; then
        cp "$repository/deploy/rdb-loader/compose.override.yml" "$COSMOS_RDB_LOADER_COMPOSE_OVERRIDE"
    fi
    if [[ "$with_previous" == yes ]]; then
        mkdir -p "$COSMOS_STATE_DIR/previous"
        cp "$fixture/repo/deploy/compose.yml" "$COSMOS_STATE_DIR/previous/compose.yml"
        printf 'previous\n' > "$COSMOS_STATE_DIR/current"
        printf 'previous\n' > "$FAKE_ACTIVE"
    fi
    if bash "$fixture/repo/deploy/scripts/deploy.sh" "$tag" > "$case_dir/output" 2>&1; then
        status=0
    else
        status=$?
    fi
    # No deployment outcome may remove a container project or a data volume.
    if grep -Eq 'docker .* (down|volume rm|system prune)( |$)|--volumes' "$FAKE_CALLS"; then
        fail "$name attempted a destructive Docker operation"
    fi
}

run_case success success candidate yes
[[ "$status" == 0 ]] || fail 'successful release returned failure'
[[ "$(cat "$COSMOS_STATE_DIR/current")" == candidate ]] || fail 'successful release was not promoted'
[[ "$(cat "$FAKE_ACTIVE")" == candidate ]] || fail 'successful candidate is not active'
echo 'PASS: healthy candidate is promoted'

if grep -q 'rdb-loader.compose.yml' "$FAKE_CALLS"; then
    fail 'default deployment unexpectedly opted into host database access'
fi
echo 'PASS: default deployment keeps PostgreSQL on its internal network'

run_case loader-access success candidate yes loader
[[ "$status" == 0 ]] || fail 'loader-enabled release returned failure'
cmp "$COSMOS_RDB_LOADER_COMPOSE_OVERRIDE" "$COSMOS_STATE_DIR/candidate/rdb-loader.compose.yml" || fail 'loader override was not preserved with release'
grep ' up .* postgres redis' "$FAKE_CALLS" | grep -q -- "-f $COSMOS_STATE_DIR/candidate/rdb-loader.compose.yml" || fail 'database reconciliation omitted loader override'
if bash "$fixture/repo/deploy/scripts/deploy.sh" subsequent >> "$fixture/loader-access/output" 2>&1; then
    [[ "$(cat "$COSMOS_STATE_DIR/current")" == subsequent ]] || fail 'subsequent release was not promoted'
else
    fail 'subsequent release with installed loader override failed'
fi
grep ' up .* postgres redis' "$FAKE_CALLS" | grep -q -- "-f $COSMOS_STATE_DIR/subsequent/rdb-loader.compose.yml" || fail 'subsequent deployment removed loader database access'
echo 'PASS: installed loader override persists across subsequent deployments'

run_case health-failure health-failure candidate yes
[[ "$status" != 0 ]] || fail 'unhealthy release returned success'
[[ "$(cat "$COSMOS_STATE_DIR/current")" == previous ]] || fail 'unhealthy release changed current'
[[ "$(cat "$FAKE_ACTIVE")" == previous ]] || fail 'previous application was not restored'
grep -q 'Restored application release previous' "$fixture/health-failure/output" || fail 'restore outcome missing'
echo 'PASS: health failure restores and retains previous release'

run_case start-failure start-failure candidate yes
[[ "$status" == 17 ]] || fail 'candidate start error status was lost'
[[ "$(cat "$COSMOS_STATE_DIR/current")" == previous ]] || fail 'start failure changed current'
[[ "$(cat "$FAKE_ACTIVE")" == previous ]] || fail 'start failure did not restore previous'
echo 'PASS: container start failure preserves original exit code'

run_case rollback-failure rollback-failure candidate yes
[[ "$status" != 0 ]] || fail 'rollback failure returned success'
[[ "$(cat "$COSMOS_STATE_DIR/current")" == previous ]] || fail 'rollback failure changed current'
grep -q 'ROLLBACK FAILED' "$fixture/rollback-failure/output" || fail 'rollback failure was not reported'
echo 'PASS: rollback failure remains a failed deployment'

run_case terminate terminate candidate yes
[[ "$status" == 143 ]] || fail 'termination signal exit code was lost'
[[ "$(cat "$COSMOS_STATE_DIR/current")" == previous ]] || fail 'termination changed current'
[[ "$(cat "$FAKE_ACTIVE")" == previous ]] || fail 'termination did not restore previous'
echo 'PASS: SIGTERM during replacement restores previous and returns 143'

run_case interrupt interrupt candidate yes
[[ "$status" == 130 ]] || fail 'interrupt signal exit code was lost'
[[ "$(cat "$COSMOS_STATE_DIR/current")" == previous ]] || fail 'interrupt changed current'
[[ "$(cat "$FAKE_ACTIVE")" == previous ]] || fail 'interrupt did not restore previous'
echo 'PASS: SIGINT during replacement restores previous and returns 130'

run_case first-failure health-failure candidate no
[[ "$status" != 0 ]] || fail 'first failed deployment returned success'
[[ ! -f "$COSMOS_STATE_DIR/current" ]] || fail 'first failed deployment created current marker'
grep -q 'No previous application release exists' "$fixture/first-failure/output" || fail 'missing release outcome not reported'
echo 'PASS: first failed release is not promoted'

run_case invalid-tag success '../escape' yes
[[ "$status" == 2 ]] || fail 'invalid tag did not return validation error'
[[ ! -s "$FAKE_CALLS" ]] || fail 'invalid tag reached Docker or curl'
[[ "$(cat "$COSMOS_STATE_DIR/current")" == previous ]] || fail 'invalid tag changed current'
echo 'PASS: invalid tag is rejected before external commands'
