#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/../.."
tag="${1:?usage: ci.sh IMAGE_TAG}"
[[ "$tag" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$ ]] || exit 2
export IMAGE_TAG="$tag"
project="cosmos-ci-$(printf '%s' "$tag" | tr '[:upper:]_.' '[:lower:]--')"
compose=(docker compose -p "$project" -f deploy/compose.ci.yml)
mkdir -p deploy/reports/backend
cleanup() {
    result=$?
    trap - EXIT
    container="$("${compose[@]}" ps -aq test 2>/dev/null || true)"
    if [[ -n "$container" ]]; then
        docker cp "$container:/workspace/build/test-results/test/." deploy/reports/backend/ 2>/dev/null || true
    fi
    # Only this build's disposable test project is removed. Production volumes are separate.
    "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    exit "$result"
}
trap cleanup EXIT
docker build --target build -t "cosmos-frontend-build:$tag" FrontEnd
docker run --rm "cosmos-frontend-build:$tag" npm run lint
docker build --target build -t "cosmos-backend-build:$tag" BackEnd
"${compose[@]}" up --abort-on-container-exit --exit-code-from test test
