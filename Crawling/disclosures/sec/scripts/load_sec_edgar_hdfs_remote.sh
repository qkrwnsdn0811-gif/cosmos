#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 || "$1" == "--help" ]]; then
  printf 'Usage: bash %s DATASET_DIR /datasets/sec-edgar/snapshots/NAME.inprogress\n' "$0"
  [[ "${1:-}" == "--help" ]] && exit 0 || exit 2
fi
source_dir=$(realpath -e -- "$1")
target=$2
hdfs=${HDFS_BIN:-${HADOOP_HOME:-/opt/hadoop}/bin/hdfs}
if [[ ! "$target" =~ ^/datasets/sec-edgar/snapshots/[A-Za-z0-9_-][A-Za-z0-9._-]*\.inprogress$ ]]; then
  printf 'Target must be a named .inprogress SEC snapshot directory.\n' >&2
  exit 2
fi
test -d "$source_dir/raw"
test -s "$source_dir/filings.jsonl"
for name in README.md companies.json failures.jsonl manifest.json selected_filings.jsonl; do
  test -f "$source_dir/$name"
done

# Permit only collector outputs; never upload contact files, credentials, or partials.
while IFS= read -r -d '' file; do
  relative=${file#"$source_dir/"}
  case "$relative" in
    README.md|companies.json|failures.jsonl|filings.jsonl|manifest.json|resume-report.json|selected_filings.jsonl) ;;
    raw/*/*.txt)
      if [[ ! "$relative" =~ ^raw/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.txt$ ]]; then
        printf 'Unexpected raw filing path: %s\n' "$relative" >&2
        exit 2
      fi
      ;;
    *) printf 'Unexpected source file; refusing upload: %s\n' "$relative" >&2; exit 2 ;;
  esac
done < <(find "$source_dir" -type f -print0)
if [[ -n "$(find "$source_dir" -type l -print -quit)" ]]; then
  printf 'Symlinks are not accepted in the dataset directory.\n' >&2
  exit 2
fi
if "$hdfs" dfs -test -e "$target"; then
  printf 'Refusing to overwrite existing HDFS path: %s\n' "$target" >&2
  exit 2
fi
"$hdfs" dfs -mkdir -p "${target%/*}"
"$hdfs" dfs -put "$source_dir" "$target"
"$hdfs" dfs -count -q "$target"
"$hdfs" dfs -du -s -h "$target"
printf 'Uploaded staging snapshot. Run verify_sec_edgar_hdfs_remote.sh before publication.\n'
