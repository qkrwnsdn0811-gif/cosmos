#!/usr/bin/env bash
# Exercises the registry hand-off to deploy.sh without git remotes, Docker, a registry, or server state.
set -Eeuo pipefail

repository="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/cosmos-registry-tests.XXXXXX")"
cleanup() {
    # Only the directory created by mktemp above may be removed.
    [[ -d "$fixture" && "${fixture##*/}" == cosmos-registry-tests.* ]] && rm -rf -- "$fixture"
}
trap cleanup EXIT
mkdir -p "$fixture/bin" "$fixture/repo/deploy/scripts"
cp "$repository/deploy/scripts/deploy-from-registry.sh" "$fixture/repo/deploy/scripts/deploy-from-registry.sh"

cat > "$fixture/repo/deploy/scripts/deploy.sh" <<'DEPLOY'
#!/usr/bin/env bash
printf 'deploy.sh %s smoke=%s\n' "$*" "${COSMOS_SMOKE_URL:-}" >> "$FAKE_CALLS"
[[ "$FAKE_SCENARIO" != deploy-failure ]] || exit 17
DEPLOY

cat > "$fixture/bin/git" <<'GIT'
#!/usr/bin/env bash
set -eu
printf 'git %s\n' "$*" >> "$FAKE_CALLS"
if [[ "$1" == merge-base && "$FAKE_SCENARIO" == not-on-main ]]; then
    exit 1
fi
GIT

cat > "$fixture/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -eu
printf 'docker %s\n' "$*" >> "$FAKE_CALLS"
if [[ "$1" == login ]]; then
    printf 'docker-login-stdin %s\n' "$(cat)" >> "$FAKE_CALLS"
fi
if [[ "$1" == pull && "$FAKE_SCENARIO" == pull-failure && "$2" == */cosmos-frontend:* ]]; then
    exit 1
fi
DOCKER

cat > "$fixture/bin/curl" <<'CURL'
#!/usr/bin/env bash
set -eu
printf 'curl %s\n' "$*" >> "$FAKE_CALLS"
printf '{"access_token":"ya29.test","expires_in":3599,"token_type":"Bearer"}'
CURL
chmod +x "$fixture/bin/git" "$fixture/bin/docker" "$fixture/bin/curl"
export PATH="$fixture/bin:$PATH"

fail() { echo "FAIL: $*" >&2; exit 1; }

sha=0123456789abcdef0123456789abcdef01234567
registry=asia-northeast3-docker.pkg.dev/cosmos-nexus-1026/cosmos
host=asia-northeast3-docker.pkg.dev
tag=gh-0123456789ab

run_case() {
    local name="$1" scenario="$2" commit="$3" target="$4"
    local case_dir="$fixture/$name"
    mkdir -p "$case_dir"
    export COSMOS_ENV_FILE="$case_dir/app.env"
    export COSMOS_METADATA_TOKEN_URL=http://metadata.test/token
    export FAKE_SCENARIO="$scenario"
    export FAKE_CALLS="$case_dir/calls"
    printf 'PUBLIC_URL=https://example.sslip.io\nDB_PASSWORD=never-printed\n' > "$COSMOS_ENV_FILE"
    : > "$FAKE_CALLS"
    if bash "$fixture/repo/deploy/scripts/deploy-from-registry.sh" "$commit" "$target" > "$case_dir/output" 2>&1; then
        status=0
    else
        status=$?
    fi
    # The environment file holds production secrets; only PUBLIC_URL may leave it.
    if grep -q never-printed "$FAKE_CALLS" "$case_dir/output"; then
        fail "$name exposed a secret from the environment file"
    fi
}

run_case success success "$sha" "$registry"
[[ "$status" == 0 ]] || fail "successful hand-off returned $status: $(cat "$fixture/success/output")"
expected="git fetch --quiet origin main
git merge-base --is-ancestor $sha FETCH_HEAD
git checkout --quiet --detach $sha
docker login -u oauth2accesstoken --password-stdin $host
docker-login-stdin ya29.test
docker pull $registry/cosmos-backend:$tag
docker tag $registry/cosmos-backend:$tag cosmos-backend:$tag
docker image rm $registry/cosmos-backend:$tag
docker pull $registry/cosmos-frontend:$tag
docker tag $registry/cosmos-frontend:$tag cosmos-frontend:$tag
docker image rm $registry/cosmos-frontend:$tag
docker logout $host
deploy.sh $tag smoke=https://example.sslip.io"
actual="$(grep -v '^curl ' "$FAKE_CALLS")"
[[ "$actual" == "$expected" ]] || fail "unexpected call sequence:
$actual"
grep '^curl ' "$FAKE_CALLS" | grep -q 'Metadata-Flavor: Google' || fail 'token request lacked the metadata header'
grep '^curl ' "$FAKE_CALLS" | grep -q 'http://metadata.test/token' || fail 'token request did not use the metadata token URL'
echo 'PASS: registry images are pulled, renamed, and handed to deploy.sh'

for bad in 0123 "$(printf '%s' "$sha" | tr a-f A-F)" "${sha}0"; do
    run_case bad-sha success "$bad" "$registry"
    [[ "$status" == 2 ]] || fail "malformed commit $bad returned $status"
    [[ ! -s "$FAKE_CALLS" ]] || fail "malformed commit $bad ran commands"
done
echo 'PASS: malformed commit is rejected before any command'

for bad in docker.io/library/cosmos "$registry/extra" "asia-northeast3-docker.pkg.dev/Cosmos/cosmos"; do
    run_case bad-registry success "$sha" "$bad"
    [[ "$status" == 2 ]] || fail "unexpected registry $bad returned $status"
    [[ ! -s "$FAKE_CALLS" ]] || fail "unexpected registry $bad ran commands"
done
echo 'PASS: registry outside Artifact Registry is rejected before any command'

run_case not-on-main not-on-main "$sha" "$registry"
[[ "$status" == 1 ]] || fail "commit outside main returned $status"
! grep -Eq '^git checkout|^docker |^deploy.sh' "$FAKE_CALLS" || fail 'commit outside main was checked out or deployed'
echo 'PASS: commit that is not on remote main is never deployed'

run_case pull-failure pull-failure "$sha" "$registry"
[[ "$status" == 1 ]] || fail "failed pull returned $status"
! grep -q '^deploy.sh' "$FAKE_CALLS" || fail 'deploy.sh ran after a failed pull'
grep -q "^docker logout $host" "$FAKE_CALLS" || fail 'registry credentials were kept after a failed pull'
echo 'PASS: failed pull leaves the running release alone and logs out'

run_case deploy-failure deploy-failure "$sha" "$registry"
[[ "$status" == 17 ]] || fail "deploy.sh failure was reported as $status"
echo 'PASS: deploy.sh exit status reaches the caller'
