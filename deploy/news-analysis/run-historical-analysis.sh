#!/usr/bin/env bash
set -euo pipefail

: "${HISTORICAL_NEWS_INPUT:?HISTORICAL_NEWS_INPUT is required}"
: "${HISTORICAL_NEWS_OUTPUT_BASE:?HISTORICAL_NEWS_OUTPUT_BASE is required}"

project=${NEWS_ANALYSIS_PROJECT_DIR:-/opt/cosmos/news-analysis/current/AI/ner}
python=${NEWS_ANALYSIS_PYTHON:-/opt/cosmos/news-analysis/venv/bin/python}
export PYTHONPATH="$project${PYTHONPATH:+:$PYTHONPATH}"
export JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}
export HADOOP_HOME=${HADOOP_HOME:-/opt/hadoop}
export ARROW_LIBHDFS_DIR=${ARROW_LIBHDFS_DIR:-$HADOOP_HOME/lib/native}
export CLASSPATH=${CLASSPATH:-$($HADOOP_HOME/bin/hdfs classpath --glob)}

exec "$python" -u "$project/historical_analyzer.py" \
  --input-root "$HISTORICAL_NEWS_INPUT" \
  --output-base "$HISTORICAL_NEWS_OUTPUT_BASE" \
  --aliases "${NEWS_ANALYSIS_ALIASES:-$project/data/aliases.csv}" \
  --companies "${NEWS_ANALYSIS_COMPANIES:-$project/data/companies.csv}" \
  --model-version "${NEWS_ANALYSIS_MODEL_VERSION:-dict-v1.3}" \
  --analysis-version "${HISTORICAL_NEWS_ANALYSIS_VERSION:-news-historical-company-mentions-1.0}" \
  --batch-rows "${HISTORICAL_NEWS_BATCH_ROWS:-1000}"
