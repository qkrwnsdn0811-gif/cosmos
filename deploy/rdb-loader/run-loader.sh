#!/usr/bin/env bash
# Run on the Hadoop Master after a complete immutable snapshot has been written.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository="${RDB_LOADER_APP_ROOT:-$(cd -- "${script_dir}/../.." && pwd)}"
python_bin="${PYTHON_BIN:-${repository}/.venv-rdb-loader/bin/python}"

if [[ ! -f "${repository}/AI/rdb_loader/__main__.py" ]]; then
    echo 'RDB_LOADER_APP_ROOT must point to a release containing AI/rdb_loader.' >&2
    exit 2
fi
if [[ ! -x "${python_bin}" ]]; then
    echo 'Set PYTHON_BIN to the Python executable with AI/rdb_loader/requirements.txt installed.' >&2
    exit 2
fi

export HDFS_BIN="${HDFS_BIN:-/opt/hadoop/bin/hdfs}"
export PYTHONDONTWRITEBYTECODE=1
cd -- "${repository}"
exec "${python_bin}" -m AI.rdb_loader "$@"
