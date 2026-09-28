#!/usr/bin/env bash
set -euo pipefail
umask 027
kind=${DOCUMENT_KIND:?set DOCUMENT_KIND=news-analyzed, dart, sec, or sec-batch}
input=${DOCUMENT_INPUT:?set a completed HDFS snapshot URI}
project=${DOCUMENT_PROJECT_DIR:-/opt/cosmos/document-loader/current/Crawling/documents}
python=${DOCUMENT_PYTHON:-$project/.venv/bin/python}
commit=${DOCUMENT_COMMIT:-0}
register=${DOCUMENT_REGISTER_SOURCES:-0}
case "$commit:$register" in 0:0|0:1|1:0|1:1) ;; *) exit 2 ;; esac
cd "$project"
arguments=(load --kind "$kind" --input "$input")
[[ "$commit" == 0 ]] || arguments+=(--commit)
[[ "$register" == 0 ]] || arguments+=(--register-sources)
[[ -z "${DOCUMENT_COMPANY_MAP:-}" ]] || arguments+=(--company-map "$DOCUMENT_COMPANY_MAP")
[[ -z "${DOCUMENT_SOURCES_FILE:-}" ]] || arguments+=(--sources-file "$DOCUMENT_SOURCES_FILE")
[[ -z "${DOCUMENT_EXPECTED_INDEX_SHA256:-}" ]] || arguments+=(--expected-index-sha256 "$DOCUMENT_EXPECTED_INDEX_SHA256")
exec "$python" -m services.document_loader.cli "${arguments[@]}"
