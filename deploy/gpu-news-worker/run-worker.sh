#!/usr/bin/env bash
set -euo pipefail

root="${HOME}/cosmos-gpu-news"
mkdir -p "${root}/logs"
log="${root}/logs/worker.log"
if [[ -f "${log}" ]] && (( $(stat -c %s "${log}") > 20971520 )); then
  mv -f "${log}" "${log}.1"
fi
exec >>"${log}" 2>&1
echo "$(date --iso-8601=seconds) worker starting"
cd "${root}/current/AI/gpu_news"
# evidence.py pulls the segmentation and the attribution guard from AI/graph,
# which reads AI/ner. Without both on the path the import fails outright.
export PYTHONPATH=".:../graph:../ner${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1
exec "${root}/venv/bin/python" -u worker.py \
  --config "${HOME}/.config/cosmos/gpu-news-worker.json" \
  --bundle "${root}/models/finbert-v1" \
  --transport ssh --device cuda --batch-size 64 --max-batches 50 --loop-seconds 60

