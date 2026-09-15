#!/usr/bin/env bash
# Run the producer to completion, then publish precisely that immutable output.
set -euo pipefail

usage() {
    echo 'usage: run-pipeline.sh --features HDFS_URI --output-root HDFS_URI --snapshot-id UUID --as-of-at UTC_TIMESTAMP --model-version VERSION' >&2
}

features=''
output_root=''
snapshot_id=''
as_of_at=''
model_version=''
while (( $# )); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --features|--output-root|--snapshot-id|--as-of-at|--model-version)
            (( $# >= 2 )) && [[ -n "$2" && "$2" != --* ]] || { usage; exit 2; }
            case "$1" in
                --features) features="$2" ;;
                --output-root) output_root="$2" ;;
                --snapshot-id) snapshot_id="$2" ;;
                --as-of-at) as_of_at="$2" ;;
                --model-version) model_version="$2" ;;
            esac
            shift 2
            ;;
        *) usage; exit 2 ;;
    esac
done
[[ -n "$features" && -n "$output_root" && -n "$snapshot_id" && -n "$as_of_at" && -n "$model_version" ]] || { usage; exit 2; }
[[ "$features" =~ ^hdfs://[^/@[:space:]]+/.+ && "$output_root" =~ ^hdfs://[^/@[:space:]]+/.+ ]] || {
    echo 'Use complete HDFS URIs including the NameNode host for features and output-root.' >&2
    exit 2
}
[[ "$snapshot_id" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]] || {
    echo 'snapshot-id must be a UUID.' >&2
    exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository="${RDB_LOADER_APP_ROOT:-$(cd -- "${script_dir}/../.." && pwd)}"
spark_bin="${SPARK_BIN:-/opt/spark/bin/spark-submit}"
snapshot_uri="${output_root%/}/${snapshot_id}"
[[ -f "${repository}/AI/rdb_loader/spark_aggregate.py" ]] || {
    echo 'RDB_LOADER_APP_ROOT must point to a release containing the Spark producer.' >&2
    exit 2
}
cd -- "$repository"
"$spark_bin" --master yarn --deploy-mode client \
    "${repository}/AI/rdb_loader/spark_aggregate.py" \
    --input "$features" --output "$snapshot_uri" \
    --snapshot-id "$snapshot_id" --as-of-at "$as_of_at" --model-version "$model_version"

# set -e preserves Spark's error code and prevents publication after failure.
# PostgreSQL environment variables are inherited from the scheduler/service.
exec /usr/bin/bash "${script_dir}/run-loader.sh" --input "$snapshot_uri"
