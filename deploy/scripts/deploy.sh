#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/../.."
tag="${1:?usage: deploy.sh IMAGE_TAG}"
[[ "$tag" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$ ]] || exit 2
state="${COSMOS_STATE_DIR:-/opt/cosmos/releases}"
env_override="${COSMOS_ENV_FILE:-}"
secrets="${env_override:-/etc/cosmos/app.env}"
mkdir -p "$state"
exec 9>"$state/deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
recover_environment_from_backend() {
    local recovery_file="$state/app.env" backend_id container_env
    local db_password redis_password jwt_secret public_url temporary_file
    if [[ -r "$recovery_file" ]]; then
        secrets="$recovery_file"
        echo "Using recovered deployment environment: $recovery_file" >&2
        return 0
    fi
    backend_id="$(docker ps \
        --filter 'label=com.docker.compose.project=cosmos' \
        --filter 'label=com.docker.compose.service=backend' \
        --format '{{.ID}}' | head -n 1)"
    [[ -n "$backend_id" ]] || return 1
    container_env="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$backend_id")"
    env_value() {
        local key="$1" line
        while IFS= read -r line; do
            if [[ "$line" == "$key="* ]]; then
                printf '%s' "${line#*=}"
                return 0
            fi
        done <<< "$container_env"
        return 1
    }
    db_password="$(env_value DB_PASSWORD)" || return 1
    redis_password="$(env_value REDIS_PASSWORD)" || return 1
    jwt_secret="$(env_value JWT_SECRET)" || return 1
    public_url="$(env_value CORS_ALLOWED_ORIGINS)" || return 1
    [[ -n "$db_password" && -n "$redis_password" && -n "$jwt_secret" && -n "$public_url" ]] || return 1
    temporary_file="$(mktemp "$state/.app.env.XXXXXX")"
    chmod 0600 "$temporary_file"
    if ! printf 'PUBLIC_URL=%s\nDB_PASSWORD=%s\nREDIS_PASSWORD=%s\nJWT_SECRET=%s\n' \
        "$public_url" "$db_password" "$redis_password" "$jwt_secret" > "$temporary_file"; then
        rm -f -- "$temporary_file"
        return 1
    fi
    mv "$temporary_file" "$recovery_file"
    secrets="$recovery_file"
    unset container_env db_password redis_password jwt_secret public_url
    echo "Recovered deployment environment from the running backend: $recovery_file" >&2
}
if [[ ! -r "$secrets" ]]; then
    if [[ -n "$env_override" ]] || ! recover_environment_from_backend; then
        echo "Missing environment file: $secrets" >&2
        exit 1
    fi
fi
docker image inspect "cosmos-backend:$tag" "cosmos-frontend:$tag" >/dev/null
export IMAGE_TAG="$tag"
release="$state/$tag"
mkdir -p "$release"
cp deploy/compose.yml "$release/compose.yml"
compose=(docker compose --env-file "$secrets" -p cosmos -f "$release/compose.yml")
# An operator-installed override keeps the Master's loopback database endpoint
# present across later releases. Absence preserves the original internal network.
loader_override="${COSMOS_RDB_LOADER_COMPOSE_OVERRIDE:-/etc/cosmos/rdb-loader.compose.yml}"
if [[ -e "$loader_override" ]]; then
    [[ -f "$loader_override" && -r "$loader_override" ]] || { echo 'RDB Loader Compose override must be a readable file' >&2; exit 1; }
    cp "$loader_override" "$release/rdb-loader.compose.yml"
    compose+=(-f "$release/rdb-loader.compose.yml")
fi
"${compose[@]}" config --quiet
previous=''
[[ ! -f "$state/current" ]] || previous="$(cat "$state/current")"
previous_previous=''
[[ ! -f "$state/.previous" ]] || previous_previous="$(cat "$state/.previous")"
prune_deployment_images() {
    local keep_current="$1" keep_previous="${2:-}" repository image_tag ref
    while read -r repository image_tag; do
        case "$repository" in
            cosmos-backend|cosmos-frontend)
                [[ "$image_tag" == "$keep_current" || (-n "$keep_previous" && "$image_tag" == "$keep_previous") ]] && continue
                ;;
            cosmos-backend-build|cosmos-frontend-build)
                ;;
            *)
                continue
                ;;
        esac
        [[ "$image_tag" != '<none>' ]] || continue
        ref="$repository:$image_tag"
        if docker image rm "$ref" >/dev/null; then
            printf 'Removed superseded deployment image: %s\n' "$ref"
        else
            printf 'Warning: could not remove superseded deployment image: %s\n' "$ref" >&2
        fi
    done < <(docker image ls --format '{{.Repository}} {{.Tag}}')
}
check_health() {
    local attempt
    for ((attempt=0; attempt<60; attempt++)); do
        if curl --fail --silent --max-time 3 http://127.0.0.1:18081/actuator/health | grep -q '"status":"UP"' &&
           curl --fail --silent --max-time 3 http://127.0.0.1:18080/healthz >/dev/null; then
            return 0
        fi
        sleep 3
    done
    return 1
}
check_web_delivery() {
    local base_url="$1" index asset_path response_headers http_version
    curl --fail --silent --show-error --max-time 15 "$base_url/healthz" | grep -q '"status":"UP"'
    index="$(curl --fail --silent --show-error --max-time 15 "$base_url/")"
    asset_path="$(printf '%s' "$index" | grep -oE '/assets/[^"[:space:]]+\.(js|css)' | sed -n '1p' || true)"
    [[ -n "$asset_path" ]] || { echo 'Smoke test could not find a JS/CSS asset in index.html' >&2; return 1; }
    response_headers="$(curl --fail --silent --show-error --max-time 30 \
        --header 'Accept-Encoding: gzip' --dump-header - --output /dev/null "$base_url$asset_path")"
    if ! printf '%s\n' "$response_headers" | grep -Eqi '^content-encoding:[[:space:]]*gzip([[:space:]]|$)'; then
        echo "Smoke test expected gzip for $asset_path" >&2
        return 1
    fi
    http_version="$(curl --fail --silent --show-error --max-time 15 \
        --http2 --output /dev/null --write-out '%{http_version}' "$base_url/")"
    [[ "$http_version" == 2 ]] || { echo "Smoke test expected HTTP/2, got HTTP/$http_version" >&2; return 1; }
}
rollback() {
    result=${1:-$?}
    trap - ERR INT TERM
    echo 'Deployment failed; application rollback requested' >&2
    if [[ -n "$previous" && -f "$state/$previous/compose.yml" ]]; then
        export IMAGE_TAG="$previous"
        if docker compose --env-file "$secrets" -p cosmos -f "$state/$previous/compose.yml" up -d --no-deps backend frontend && check_health; then
            printf '%s\n' "$previous" > "$state/current.next"
            mv "$state/current.next" "$state/current"
            echo "Restored application release $previous" >&2
            prune_deployment_images "$previous" "$previous_previous"
        else
            echo 'ROLLBACK FAILED: manual intervention required' >&2
        fi
    else
        echo 'No previous application release exists; inspect the failed containers' >&2
    fi
    # Flyway migrations are not reversed. Only backward-compatible migrations may deploy automatically.
    exit "$result"
}
trap rollback ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM
# Database services and their named volumes are retained during application replacement.
"${compose[@]}" up -d --wait --wait-timeout 90 postgres redis
"${compose[@]}" up -d --no-deps backend frontend
check_health
if [[ -n "${COSMOS_SMOKE_URL:-}" ]]; then
    check_web_delivery "$COSMOS_SMOKE_URL"
fi
printf '%s\n' "$tag" > "$state/current.next"
mv "$state/current.next" "$state/current"
if [[ -n "$previous" && "$previous" != "$tag" ]]; then
    printf '%s\n' "$previous" > "$state/.previous.next"
    mv "$state/.previous.next" "$state/.previous"
elif [[ -z "$previous" ]]; then
    rm -f -- "$state/.previous" "$state/.previous.next"
fi
retained_previous=''
[[ ! -f "$state/.previous" ]] || retained_previous="$(cat "$state/.previous")"
prune_deployment_images "$tag" "$retained_previous"
printf 'Deployment healthy: %s\n' "$tag"
