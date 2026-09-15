#!/usr/bin/env bash
set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
: "${SEC_STATE_DIR:?Set SEC_STATE_DIR}"
: "${SEC_UNIVERSE:?Set SEC_UNIVERSE to the preserved companies.json}"
: "${SEC_EDGAR_USER_AGENT_FILE:?Set the private SEC User-Agent file}"
exec "${PYTHON_BIN:-python3}" "$script_dir/../scripts/run_sec_incremental.py" \
  --state-dir "$SEC_STATE_DIR" --universe "$SEC_UNIVERSE" \
  --user-agent-file "$SEC_EDGAR_USER_AGENT_FILE" \
  --start-date "${SEC_START_DATE:-2026-09-09}" \
  --max-filings "${SEC_MAX_FILINGS:-200}" \
  --request-delay "${SEC_REQUEST_DELAY:-0.2}" \
  --hdfs-root "${SEC_HDFS_ROOT:-/data-lake/raw/realtime/sec-edgar}" \
  --hdfs-bin "${HDFS_BIN:-/opt/hadoop/bin/hdfs}" "$@"
