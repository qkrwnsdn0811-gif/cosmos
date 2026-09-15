#!/usr/bin/env bash
set -euo pipefail
export NEWS_APP_ROOT="${NEWS_APP_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
export HADOOP_HOME="${HADOOP_HOME:-/opt/hadoop}"
export HADOOP_CONF_DIR="${HADOOP_CONF_DIR:-${HADOOP_HOME}/etc/hadoop}"
export HADOOP_USER_NAME="${HADOOP_USER_NAME:-ubuntu}"
export CLASSPATH="${CLASSPATH:-$("${HADOOP_HOME}/bin/hadoop" classpath --glob)}"
export LD_LIBRARY_PATH="${HADOOP_HOME}/lib/native:${JAVA_HOME}/lib/server:${LD_LIBRARY_PATH:-}"
export PYTHONUNBUFFERED=1
cd -- "$NEWS_APP_ROOT"
exec "${NEWS_PYTHON:-${NEWS_APP_ROOT}/.venv/bin/python}" "$@"
