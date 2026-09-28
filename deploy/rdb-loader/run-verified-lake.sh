#!/usr/bin/env bash
# Publish one producer-verified analytics run after Spark has written _VERIFIED.
set -euo pipefail

if (( $# != 1 )) || [[ -z "$1" || "$1" == -* ]]; then
    echo 'usage: run-verified-lake.sh RUN_ID|latest' >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export RDB_LOADER_APP_ROOT="$(cd -- "${script_dir}/../.." && pwd)"
exec /usr/bin/bash "${script_dir}/run-loader.sh" \
    --run-id "$1" --lake-root "${RDB_LOADER_LAKE_ROOT:-hdfs://cosmos-master:9000/data-lake}"
