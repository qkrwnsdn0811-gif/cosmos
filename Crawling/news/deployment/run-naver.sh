#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 && ( "$1" == search || "$1" == deliver ) ]] || { echo 'usage: run-naver.sh search|deliver' >&2; exit 2; }
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app="${NAVER_APP_ROOT:-$(cd -- "${script_dir}/.." && pwd)}"
cd -- "$app"
export PYTHONUNBUFFERED=1
exec "${NAVER_PYTHON:-${app}/.venv/bin/python}" -m services.news_pipeline.naver_runner \
  --mode "$1" --state-dir "${NAVER_STATE_DIR:-/home/ubuntu/news-kafka/naver-state}" \
  --companies "${NAVER_COMPANIES:-${app}/config/kospi100.json}" \
  --poll-seconds "${NAVER_POLL_SECONDS:-600}" --daily-budget "${NAVER_DAILY_BUDGET:-24000}" \
  --bootstrap "${KAFKA_BOOTSTRAP_SERVERS:-127.0.0.1:9092}" --topic "${NEWS_TOPIC:-news.raw}" \
  --node "${NEWS_NODE:-node}" --article-script "${app}/services/news_pipeline/naver-fetch.bundle.mjs" \
  --limit "${NAVER_ARTICLE_LIMIT:-16}"
