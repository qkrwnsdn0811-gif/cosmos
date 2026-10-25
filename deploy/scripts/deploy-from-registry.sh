#!/usr/bin/env bash
# Runs on the GCP demo VM as cosmos-ci. GitHub Actions has pushed both images to Artifact Registry;
# this pulls them under the names deploy.sh expects and hands the release to deploy.sh.
set -Eeuo pipefail

pull_images() {
    local registry="$1" tag="$2" app
    for app in backend frontend; do
        docker pull "$registry/cosmos-$app:$tag" || return 1
        docker tag "$registry/cosmos-$app:$tag" "cosmos-$app:$tag" || return 1
        # The short name keeps the image; deploy.sh prunes only cosmos-backend/cosmos-frontend names.
        docker image rm "$registry/cosmos-$app:$tag" > /dev/null || return 1
    done
}

main() {
    cd "$(dirname "$0")/../.."
    local sha="${1:?usage: deploy-from-registry.sh COMMIT_SHA REGISTRY}"
    local registry="${2:?usage: deploy-from-registry.sh COMMIT_SHA REGISTRY}"
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
    [[ "$registry" =~ ^[a-z0-9-]+-docker\.pkg\.dev/[a-z0-9-]+/[a-z0-9-]+$ ]] || exit 2
    local host="${registry%%/*}" tag="gh-${sha:0:12}"
    local secrets="${COSMOS_ENV_FILE:-/etc/cosmos/app.env}"
    local state="${COSMOS_STATE_DIR:-/opt/cosmos/releases}"
    local token_url="${COSMOS_METADATA_TOKEN_URL:-http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token}"
    local public_url running running_commit token pulled=0

    public_url="$(sed -n 's/^PUBLIC_URL=//p' "$secrets")"
    [[ -n "$public_url" ]] || { echo "PUBLIC_URL is missing from $secrets" >&2; exit 1; }

    # A re-run of an old workflow must not roll the demo back to a commit that has left main.
    git fetch --quiet origin main
    git merge-base --is-ancestor "$sha" FETCH_HEAD || { echo "Commit $sha is not on origin/main" >&2; exit 1; }
    # Nor may a re-run or a run whose tests finished late replace a newer release that is already live.
    running="$(cat "$state/current" 2> /dev/null || true)"
    if [[ "$running" =~ ^gh-([0-9a-f]{12})$ ]]; then
        running_commit="$(git rev-parse --verify --quiet "${BASH_REMATCH[1]}^{commit}" || true)"
        if [[ -n "$running_commit" && "$running_commit" != "$sha" ]] &&
            git merge-base --is-ancestor "$sha" "$running_commit"; then
            echo "Commit $sha is older than the running release $running; nothing to deploy" >&2
            exit 0
        fi
    fi
    git checkout --quiet --detach "$sha"

    token="$(curl --fail --silent --show-error --max-time 10 --header 'Metadata-Flavor: Google' "$token_url" |
        sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p')"
    [[ -n "$token" ]] || { echo 'Could not read the VM service account token' >&2; exit 1; }
    printf '%s' "$token" | docker login -u oauth2accesstoken --password-stdin "$host" > /dev/null
    unset token
    if pull_images "$registry" "$tag"; then
        pulled=1
    fi
    docker logout "$host" > /dev/null
    [[ "$pulled" == 1 ]] || { echo 'Image pull failed; the running release is unchanged' >&2; exit 1; }

    COSMOS_SMOKE_URL="$public_url" bash deploy/scripts/deploy.sh "$tag"
}

# git checkout above may replace this file while it runs; bash has read all of main before calling it.
main "$@"; exit $?
