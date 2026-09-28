#!/usr/bin/env bash
# Local Master orchestration only; Worker startup remains an explicit separate step.
set -Eeuo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/migrate-cluster.py" "$@"
