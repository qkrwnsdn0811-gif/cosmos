#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 || "$1" == "--help" ]]; then
  printf 'Usage: bash %s DATASET_DIR /datasets/sec-edgar/snapshots/NAME[.inprogress]\n' "$0"
  [[ "${1:-}" == "--help" ]] && exit 0 || exit 2
fi
stage=$(realpath -e -- "$1")
target=$2
hdfs=${HDFS_BIN:-${HADOOP_HOME:-/opt/hadoop}/bin/hdfs}
if [[ ! "$target" =~ ^/datasets/sec-edgar/snapshots/[A-Za-z0-9_-][A-Za-z0-9._-]*$ ]]; then
  printf 'Target must be a named SEC snapshot directory.\n' >&2
  exit 2
fi
test -d "$stage/raw"
test -s "$stage/filings.jsonl"

# The snapshot size is derived from the selected dataset, never a previous run.
expected_raw_files=$(find "$stage/raw" -type f -name '*.txt' | wc -l)
test "$expected_raw_files" -gt 0
expected_total_files=$(find "$stage" -type f ! -name _SUCCESS | wc -l)
expected_total_bytes=$(find "$stage" -type f ! -name _SUCCESS -printf '%s\n' | awk '{s+=$1} END {printf "%.0f",s}')
read -r directories files bytes counted_path < <("$hdfs" dfs -count "$target")
if "$hdfs" dfs -test -e "$target/_SUCCESS"; then
  expected_total_files=$((expected_total_files + 1))
fi
test "$files" -eq "$expected_total_files"
test "$bytes" -eq "$expected_total_bytes"

stage_size_hash=$(
  find "$stage/raw" -type f -name '*.txt' -printf '%P\t%s\n' |
    LC_ALL=C sort | sha256sum | cut -d ' ' -f 1
)
hdfs_size_hash=$(
  "$hdfs" dfs -ls -R "$target/raw" |
    awk -v prefix="$target/raw/" '$1 ~ /^-/ {path=$8; print substr(path,length(prefix)+1) "\t" $5}' |
    LC_ALL=C sort | sha256sum | cut -d ' ' -f 1
)
test "$stage_size_hash" = "$hdfs_size_hash"

root_files=(README.md companies.json failures.jsonl filings.jsonl manifest.json selected_filings.jsonl)
if [[ -f "$stage/resume-report.json" ]]; then root_files+=(resume-report.json); fi
for name in "${root_files[@]}"; do
  stage_hash=$(sha256sum "$stage/$name" | cut -d ' ' -f 1)
  hdfs_hash=$("$hdfs" dfs -cat "$target/$name" | sha256sum | cut -d ' ' -f 1)
  test "$stage_hash" = "$hdfs_hash"
done

# Collector paths are fixed-depth, ASCII-safe paths. GNU sort and HDFS glob order
# both enumerate those paths lexicographically. This streams every raw byte once.
stage_raw_hash=$(
  find "$stage/raw" -type f -name '*.txt' -print0 |
    LC_ALL=C sort -z | xargs -0 cat | sha256sum | cut -d ' ' -f 1
)
hdfs_raw_hash=$("$hdfs" dfs -cat "$target/raw/*/*.txt" | sha256sum | cut -d ' ' -f 1)
test "$stage_raw_hash" = "$hdfs_raw_hash"
"$hdfs" fsck "$target"
printf 'status=PASS\ndirectories=%s\nfiles=%s\nbytes=%s\nrawFiles=%s\npathSizeSha256=%s\nrawConcatenatedSha256=%s\n' \
  "$directories" "$files" "$bytes" "$expected_raw_files" "$hdfs_size_hash" "$hdfs_raw_hash"
