#!/usr/bin/env bash
set -euo pipefail
umask 027

project=${NEWS_ENRICH_PROJECT_DIR:-/opt/cosmos/news-enrich/current}
python=${NEWS_ENRICH_PYTHON:-/opt/cosmos/news-enrich/venv/bin/python}
config=${NEWS_ENRICH_CONFIG:-/etc/cosmos/news-enrich.json}
bundle=${NEWS_ENRICH_BUNDLE:-/opt/cosmos/news-enrich/models/finbert-v1}
device=${NEWS_ENRICH_DEVICE:-cpu}
batch_size=${NEWS_ENRICH_BATCH_SIZE:-32}
max_batches=${NEWS_ENRICH_MAX_BATCHES:-40}
hadoop_home=${HADOOP_HOME:-/opt/hadoop}

[[ -x "$python" ]] || { echo "NEWS_ENRICH_PYTHON is not executable" >&2; exit 2; }
[[ -r "$config" ]] || { echo "NEWS_ENRICH_CONFIG is not readable" >&2; exit 2; }
[[ -d "$bundle" ]] || { echo "NEWS_ENRICH_BUNDLE is not a directory" >&2; exit 2; }

# evidence.py reuses the segmentation and the attribution guard from AI/graph,
# which in turn reads AI/ner. Both trees have to be importable or the guard
# silently is not the audited one.
export PYTHONPATH="$project/AI/gpu_news:$project/AI/graph:$project/AI/ner${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1
export HADOOP_CONF_DIR=${HADOOP_CONF_DIR:-$hadoop_home/etc/hadoop}
export HDFS_BIN=${HDFS_BIN:-$hadoop_home/bin/hdfs}
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

# Batch I/O uses the PyArrow/libhdfs client rather than one `hdfs dfs` JVM per
# file (2.4-2.9s each on this server). libhdfs needs the native library and the
# Hadoop CLASSPATH, exactly as run-news-analysis.sh sets them.
export ARROW_LIBHDFS_DIR=${ARROW_LIBHDFS_DIR:-$hadoop_home/lib/native}
if [[ -z "${CLASSPATH:-}" ]]; then
  CLASSPATH=$("$hadoop_home/bin/hadoop" classpath --glob)
  export CLASSPATH
fi

# One pass per timer tick. No --loop-seconds: systemd owns the schedule, so a
# hung pass is visible as a failed unit instead of a quiet sleep.
cd "$project/AI/gpu_news"
exec "$python" -u worker.py \
  --config "$config" \
  --bundle "$bundle" \
  --transport local \
  --device "$device" \
  --batch-size "$batch_size" \
  --max-batches "$max_batches"
