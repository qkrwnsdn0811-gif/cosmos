#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export NEWS_APP_ROOT="${NEWS_APP_ROOT:-$(cd -- "${script_dir}/.." && pwd)}"
exec "${script_dir}/run-news-python.sh" -m services.news_pipeline.collector \
  --state-dir "${NEWS_STATE_DIR:-${NEWS_APP_ROOT}/state}" \
  --crawler-root "${NEWS_APP_ROOT}/vendor/overseas-news-crawler" \
  --node "${NEWS_NODE:-node}" \
  --domestic-script "${NEWS_APP_ROOT}/services/news_pipeline/domestic.bundle.mjs" \
  --bootstrap "${KAFKA_BOOTSTRAP_SERVERS:-127.0.0.1:9092}" \
  --topic "${NEWS_TOPIC:-news.raw}" \
  --jobs "${NEWS_JOBS:-latest,domestic,backfill}" --limit "${NEWS_LIMIT:-8}" "$@"
