#!/usr/bin/env bash
set -euo pipefail
umask 027

project=${NEWS_ANALYSIS_PROJECT_DIR:-/opt/cosmos/news-analysis/current/AI/ner}
python=${NEWS_ANALYSIS_PYTHON:-/opt/cosmos/news-analysis/venv/bin/python}
hadoop_home=${HADOOP_HOME:-/opt/hadoop}

[[ -r "$project/realtime_runner.py" ]] || { echo "realtime_runner.py is not readable" >&2; exit 2; }
[[ -x "$python" ]] || { echo "NEWS_ANALYSIS_PYTHON is not executable" >&2; exit 2; }
: "${NEWS_ANALYSIS_VERSION:?set NEWS_ANALYSIS_VERSION to the deployed AI/ner revision}"

export PYTHONPATH="$project${PYTHONPATH:+:$PYTHONPATH}"
export HADOOP_CONF_DIR=${HADOOP_CONF_DIR:-$hadoop_home/etc/hadoop}
export ARROW_LIBHDFS_DIR=${ARROW_LIBHDFS_DIR:-$hadoop_home/lib/native}
export HDFS_BIN=${HDFS_BIN:-$hadoop_home/bin/hdfs}
if [[ -z "${CLASSPATH:-}" ]]; then
  CLASSPATH=$($hadoop_home/bin/hadoop classpath --glob)
  export CLASSPATH
fi

cd "$project"
exec "$python" -u realtime_runner.py
