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
  --jobs "${NEWS_JOBS:-latest,investing,investing-kospi,yahoo-kospi,domestic,backfill}" \
  --limit "${NEWS_LIMIT:-8}" \
  --backfill-limit "${NEWS_BACKFILL_LIMIT:-100}" \
  --company-registry "${NEWS_COMPANY_REGISTRY:-${NEWS_APP_ROOT}/data/company_extraction/registry.json}" \
  --kospi-config "${INVESTING_KOSPI_CONFIG:-${NEWS_APP_ROOT}/config/kospi100.json}" \
  --investing-limit "${INVESTING_LIMIT:-3}" \
  --investing-delay "${INVESTING_DELAY:-7}" \
  --investing-cooldown-seconds "${INVESTING_COOLDOWN_SECONDS:-1800}" \
  --investing-max-page "${INVESTING_MAX_PAGE:-1000}" \
  --yahoo-kospi-limit "${YAHOO_KOSPI_LIMIT:-3}" \
  --yahoo-kospi-delay "${YAHOO_KOSPI_DELAY:-4.5}" \
  --yahoo-kospi-cooldown-seconds "${YAHOO_KOSPI_COOLDOWN_SECONDS:-1800}" "$@"
