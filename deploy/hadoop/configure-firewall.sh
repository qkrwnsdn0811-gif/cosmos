#!/usr/bin/env bash
# Apply only on the named host. The Python helper also supports a pure --plan.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -B "$script_dir/configure-firewall.py" "$@"
