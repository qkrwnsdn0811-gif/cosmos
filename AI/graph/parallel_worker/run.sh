#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$TASK_ROOT"

is_python312() {
  "$@" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1
}

if [[ -n "${COSMOS_WORKER_PYTHON:-}" ]]; then
  TASK_PY=("$COSMOS_WORKER_PYTHON")
  if ! is_python312 "${TASK_PY[@]}"; then
    echo 'COSMOS_WORKER_PYTHON must name a working Python 3.12 executable.' >&2
    exit 2
  fi
elif [[ -x "$TASK_ROOT/.venv/bin/python" ]] && is_python312 "$TASK_ROOT/.venv/bin/python"; then
  TASK_PY=("$TASK_ROOT/.venv/bin/python")
elif [[ -f "$TASK_ROOT/.venv/Scripts/python.exe" ]] && is_python312 "$TASK_ROOT/.venv/Scripts/python.exe"; then
  TASK_PY=("$TASK_ROOT/.venv/Scripts/python.exe")
elif [[ -f "$TASK_ROOT/python/python.exe" ]] && is_python312 "$TASK_ROOT/python/python.exe"; then
  TASK_PY=("$TASK_ROOT/python/python.exe")
elif command -v python3.12 >/dev/null 2>&1 && is_python312 python3.12; then
  TASK_PY=(python3.12)
elif command -v python3 >/dev/null 2>&1 && is_python312 python3; then
  TASK_PY=(python3)
elif command -v py >/dev/null 2>&1 && is_python312 py -3.12; then
  TASK_PY=(py -3.12)
elif command -v python >/dev/null 2>&1 && is_python312 python; then
  TASK_PY=(python)
else
  echo 'Python 3.12 is required. Install full Python 3.12 with venv/pip, or set COSMOS_WORKER_PYTHON.' >&2
  exit 2
fi
exec "${TASK_PY[@]}" -u "$TASK_ROOT/bootstrap.py" "$@"
