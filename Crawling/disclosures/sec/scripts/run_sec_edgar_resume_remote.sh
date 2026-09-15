#!/usr/bin/env bash
set -euo pipefail

# This wrapper can run from any working directory. It does not schedule a daemon.
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $# -lt 1 || "$1" == "--help" ]]; then
  printf 'Usage: bash %s DATASET_DIR [resume options...]\n' "$0"
  printf 'Set SEC_EDGAR_USER_AGENT or SEC_EDGAR_USER_AGENT_FILE before running.\n'
  [[ $# -gt 0 ]] && exit 0 || exit 2
fi
dataset_dir=$1
shift

if [[ -z "${SEC_EDGAR_USER_AGENT:-}" ]]; then
  if [[ -z "${SEC_EDGAR_USER_AGENT_FILE:-}" || ! -s "$SEC_EDGAR_USER_AGENT_FILE" ]]; then
    printf 'SEC_EDGAR_USER_AGENT or a nonempty SEC_EDGAR_USER_AGENT_FILE is required.\n' >&2
    exit 2
  fi
  SEC_EDGAR_USER_AGENT=$(<"$SEC_EDGAR_USER_AGENT_FILE")
fi
export SEC_EDGAR_USER_AGENT
# The operator owns the optional contact file; do not delete it automatically.
exec "${PYTHON_BIN:-python3}" "$script_dir/resume_sec_edgar_from_manifest.py" \
  --root "$dataset_dir" "$@"
