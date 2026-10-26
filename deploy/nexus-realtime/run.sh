#!/usr/bin/env bash
# NEXUS 파이프라인을 서버에서 돌린다. 모든 단계는 같은 결과를 다시 써도 되게 만들어져 있어, 실패하면 다음 주기에 다시 돈다.
#   run.sh realtime   15분마다 (nexus-realtime.timer). 첫 전체 실행이 아직이면 그것부터 한다(아래 '첫 실행'), 끝났으면
#                     빅카인즈 변경이력으로 바뀐 기사만 분석·적재
#   run.sh nightly    매일 03:30 (nexus-nightly.timer) — 지난달부터 다시 수집하고 관계 그래프부터 전부 다시 계산
#   run.sh bootstrap  첫 전체 실행을 지금 (realtime 이 알아서 하므로 보통은 부를 일이 없다)
#   run.sh sentiment-export | sentiment-import <scores.parquet>   GPU PC 와 기사 감성 주고받기 (gpu-sentiment.ps1)
#
# 첫 실행: 운영 DB 는 그대로 두고 새 DB(cosmos_next)에 기업 마스터 → 2025-01 부터 수집 → 분석 → 적재 → 과거 사례 → 은하를 만든 뒤,
# 검사를 통과하면 운영 DB 와 이름을 바꾼다(옛 DB 는 cosmos_old 로 남는다). 실패하면 1시간 뒤 다시 시도한다 — 빅카인즈가
# 504 로 멈춰 있을 때(2026-10-26 오후에 관찰) 계속 두드리지 않기 위해서다. 받은 달은 캐시에 남아 다음 시도가 이어서 받는다.
set -euo pipefail

mode="${1:?usage: run.sh realtime|nightly|bootstrap|sentiment-export|sentiment-import}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
db_name="${NEXUS_DB:-cosmos}"
next_db="cosmos_next"
lock="/run/lock/nexus-pipeline.lock"
retry_after_secs=3600

db_password="$(sed -n 's/^DB_PASSWORD=//p' /etc/cosmos/app.env)"
[[ -n "$db_password" ]] || { echo "DB_PASSWORD is missing in /etc/cosmos/app.env" >&2; exit 2; }
url_for() { echo "postgresql://cosmos:${db_password}@postgres:5432/$1"; }

nexus() {
    docker compose -f "$here/compose.yml" run --rm -T nexus "$@"
}

container_of() {
    docker ps -q --filter label=com.docker.compose.project=cosmos --filter "label=com.docker.compose.service=$1"
}

psql_q() {   # psql_q <db> <sql> → 한 값
    docker exec "$(container_of postgres)" psql -U cosmos -d "$1" -tAc "$2"
}

volume_dir() {
    docker volume inspect nexus_data -f '{{.Mountpoint}}'
}

full_refresh() {   # full_refresh <수집 시작일> <DB 주소>
    local start="$1" db_url="$2" tomorrow
    tomorrow="$(TZ=Asia/Seoul date -d tomorrow +%F)"
    nexus collect --chain semis --start "$start" --end "$tomorrow"
    nexus analyze --chain semis
    nexus load --chain semis --database-url "$db_url"
    nexus cases --chain semis --database-url "$db_url"
    nexus evidence --chain semis --database-url "$db_url"
    if grep -qE '^DART_API_KEY=.+' /etc/cosmos/nexus.env; then
        nexus fundamentals --chain semis --database-url "$db_url"
        nexus ownership --chain semis --database-url "$db_url"
    fi
    nexus publish-graph --database-url "$db_url"
}

ensure_next_db() {   # 빈 DB 를 앱과 같은 Flyway 마이그레이션으로 만든다
    if [[ "$(psql_q postgres "SELECT 1 FROM pg_database WHERE datname = '$next_db'")" != "1" ]]; then
        psql_q postgres "CREATE DATABASE $next_db OWNER cosmos" >/dev/null
    fi
    docker run --rm --network cosmos_data -v "$here/../../BackEnd/src/main/resources/db/migration:/flyway/sql:ro" \
        flyway/flyway:11 "-url=jdbc:postgresql://postgres:5432/$next_db" -user=cosmos "-password=$db_password" \
        -q migrate
}

promote_next_db() {   # 새 DB 가 운영 DB 보다 나으면 이름을 바꾼다. 백엔드는 그동안만 멈춘다.
    local next_events next_last cur_last backend
    next_events="$(psql_q "$next_db" "SELECT count(*) FROM news_event WHERE status = 'ACTIVE'")"
    next_last="$(psql_q "$next_db" "SELECT COALESCE(max(dateline), 'epoch') FROM news_event")"
    cur_last="$(psql_q "$db_name" "SELECT COALESCE(max(dateline), 'epoch') FROM news_event" 2>/dev/null || echo epoch)"
    echo "new DB: $next_events events, last $next_last (current last $cur_last)"
    if (( next_events < 50000 )); then
        echo "new DB looks incomplete — not promoting" >&2
        return 1
    fi
    backend="$(container_of backend)"
    docker stop "$backend" >/dev/null
    psql_q postgres "DROP DATABASE IF EXISTS cosmos_old" >/dev/null
    psql_q postgres "ALTER DATABASE $db_name RENAME TO cosmos_old" >/dev/null
    psql_q postgres "ALTER DATABASE $next_db RENAME TO $db_name" >/dev/null
    docker start "$backend" >/dev/null
    echo "promoted $next_db → $db_name (previous kept as cosmos_old)"
}

bootstrap() {
    ensure_next_db
    nexus build-master --chain semis
    full_refresh 2025-01-01 "$(url_for "$next_db")"
    promote_next_db
    mkdir -p "$(volume_dir)/realtime"
    date -Is > "$(volume_dir)/realtime/bootstrapped"
}

exec 9>"$lock"
case "$mode" in
    realtime)
        flock -n 9 || { echo "another pipeline run is active — skipping this realtime cycle"; exit 0; }
        state_dir="$(volume_dir)/realtime"
        mkdir -p "$state_dir"
        if [[ ! -f "$state_dir/bootstrapped" ]]; then
            if [[ -f "$state_dir/bootstrap-retry-after" ]] && (( $(date +%s) < $(cat "$state_dir/bootstrap-retry-after") )); then
                echo "first full run failed recently — next try after $(date -d "@$(cat "$state_dir/bootstrap-retry-after")" -Is)"
                exit 0
            fi
            echo "first full run (bootstrap) has not finished — running it now"
            # `if ! bootstrap` 로 부르면 bash 가 함수 안의 set -e 를 끈다 — 단계 하나가 실패해도 다음 단계로 넘어가 버린다.
            # 그래서 set -e 를 켠 서브셸로 돌리고 종료 코드만 본다.
            set +e
            ( set -e; bootstrap )
            rc=$?
            set -e
            if (( rc != 0 )); then
                echo $(( $(date +%s) + retry_after_secs )) > "$state_dir/bootstrap-retry-after"
                echo "bootstrap failed — will retry in $((retry_after_secs / 60)) minutes" >&2
                exit 1
            fi
            rm -f "$state_dir/bootstrap-retry-after"
        fi
        nexus realtime --chain semis --database-url "$(url_for "$db_name")"
        ;;
    nightly)
        flock 9
        [[ -f "$(volume_dir)/realtime/bootstrapped" ]] || { echo "first full run not finished yet — realtime timer handles it"; exit 0; }
        full_refresh "$(TZ=Asia/Seoul date -d "$(TZ=Asia/Seoul date +%Y-%m-01) -1 month" +%F)" "$(url_for "$db_name")"
        ;;
    bootstrap)
        flock 9
        bootstrap
        ;;
    sentiment-export)
        flock -w 900 9 || { echo "pipeline busy — try again later"; exit 75; }
        nexus sentiment-export --chain semis --out /app/nexus/data/exchange/sentiment-todo.jsonl
        install -m 0644 "$(volume_dir)/exchange/sentiment-todo.jsonl" /tmp/nexus-sentiment-todo.jsonl
        ;;
    sentiment-import)
        flock -w 900 9 || { echo "pipeline busy — try again later"; exit 75; }
        install -D -m 0644 "${2:?usage: run.sh sentiment-import <scores.parquet>}" "$(volume_dir)/exchange/sentiment-scores.parquet"
        nexus sentiment-import --chain semis --input /app/nexus/data/exchange/sentiment-scores.parquet \
            --database-url "$(url_for "$db_name")"
        ;;
    *)
        echo "unknown mode: $mode" >&2
        exit 2
        ;;
esac
