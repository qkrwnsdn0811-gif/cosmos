#!/usr/bin/env bash
set -euo pipefail
umask 027

project=${SENTIMENT_LOADER_PROJECT_DIR:-/opt/cosmos/news-enrich/current}
python=${SENTIMENT_LOADER_PYTHON:-/opt/cosmos/news-enrich/venv/bin/python}
root=${SENTIMENT_LOADER_ROOT:?set SENTIMENT_LOADER_ROOT to the model_version partition}
state=${SENTIMENT_LOADER_STATE:-/var/lib/cosmos-sentiment-loader/state.json}
hadoop_home=${HADOOP_HOME:-/opt/hadoop}

[[ -x "$python" ]] || { echo "SENTIMENT_LOADER_PYTHON is not executable" >&2; exit 2; }
: "${COSMOS_DSN:?set COSMOS_DSN to the Postgres connection string}"

export PYTHONPATH="$project/Crawling/documents${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1
export HADOOP_CONF_DIR=${HADOOP_CONF_DIR:-$hadoop_home/etc/hadoop}

# The loader holds one libhdfs client open for the whole pass instead of
# spawning a JVM per batch. Measured on this server 2026-09-23, with a backfill
# running: `hdfs dfs -cat` 13-16s a batch against 0.90s once connected. Without
# these two the client cannot start, the runner falls back to the CLI, and the
# pass is twenty times slower - it says so on stderr, and reports its transport.
export ARROW_LIBHDFS_DIR=${ARROW_LIBHDFS_DIR:-$hadoop_home/lib/native}
if [[ -z "${CLASSPATH:-}" ]]; then
  CLASSPATH=$("$hadoop_home/bin/hadoop" classpath --glob)
  export CLASSPATH
fi

cd "$project/Crawling/documents"
exec "$python" -u -m services.sentiment_loader.runner \
  --root "$root" \
  --state-file "$state" \
  --hdfs-bin "${HDFS_BIN:-$hadoop_home/bin/hdfs}" \
  --commit
