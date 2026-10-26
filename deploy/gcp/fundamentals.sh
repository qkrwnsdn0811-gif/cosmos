#!/usr/bin/env bash
# Fills company_fundamental (PER, PBR, dividend, net income, equity, market cap) from OpenDART without the KRX
# listing: runs `nexus fundamentals-dart` once in a throwaway Python container next to the database.
# The OpenDART key is read without echo and reaches the container only through a 0600 env file removed on exit.
set -Eeuo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
ref="${1:-main}"
[[ "$ref" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo 'usage: fundamentals.sh [git-ref]' >&2; exit 2; }
env_file=/etc/cosmos/app.env

for attempt in 1 2 3; do
    read -r -s -p 'OpenDART 인증키 40자리 (입력은 보이지 않음): ' dart_key
    echo
    dart_key="${dart_key//[[:space:]]/}"
    [[ "$dart_key" =~ ^[A-Za-z0-9]{40}$ ]] && break
    echo "인증키는 영문·숫자 40자입니다 (받은 글자 수: ${#dart_key})." >&2
    [[ "$attempt" -lt 3 ]] || exit 2
done

work="$(mktemp -d /tmp/cosmos-fundamentals.XXXXXX)"
trap 'docker rm -f cosmos-fundamentals > /dev/null 2>&1 || true; rm -rf -- "$work"' EXIT
git clone --quiet --depth 1 --branch "$ref" https://github.com/qkrwnsdn0811-gif/cosmos.git "$work/repo"
echo "nexus 코드: $ref ($(git -C "$work/repo" rev-parse --short HEAD))"

umask 077
db_password="$(sed -n 's/^DB_PASSWORD=//p' "$env_file")"
printf 'DART_API_KEY=%s\nDATABASE_URL=postgresql://cosmos:%s@postgres:5432/cosmos\n' "$dart_key" "$db_password" > "$work/run.env"
unset dart_key db_password

# The default bridge reaches OpenDART and PyPI; cosmos_data (internal) reaches PostgreSQL as "postgres".
docker rm -f cosmos-fundamentals > /dev/null 2>&1 || true
docker create --name cosmos-fundamentals --env-file "$work/run.env" -v "$work/repo:/src" \
    -v nexus-pip-cache:/root/.cache/pip -w /tmp python:3.12-slim bash -c \
    'pip install -q --root-user-action=ignore /src/nexus > /tmp/pip.log 2>&1 || { tail -20 /tmp/pip.log; exit 1; }
     python -m nexus fundamentals-dart --database-url "$DATABASE_URL"' > /dev/null
docker network connect cosmos_data cosmos-fundamentals
echo '패키지를 설치하고 OpenDART에서 61개 종목을 받습니다 (몇 분 걸립니다)...'
docker start -a cosmos-fundamentals
