#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export NEWS_APP_ROOT="${NEWS_APP_ROOT:-$(cd -- "${script_dir}/.." && pwd)}"
exec "${script_dir}/run-news-python.sh" -m services.news_pipeline.writer \
  --bootstrap "${KAFKA_BOOTSTRAP_SERVERS:-127.0.0.1:9092}" \
  --topic "${NEWS_TOPIC:-news.raw}" --group "${NEWS_GROUP:-cosmos-news-hdfs-v1}" \
  --hdfs-root "${NEWS_HDFS_ROOT:-/data-lake/raw/realtime/news}" \
  --state-dir "${NEWS_WRITER_STATE_DIR:-${NEWS_APP_ROOT}/writer-state}" \
  --batch-records 2000 --max-bytes 8388608 --flush-seconds 120 "$@"
