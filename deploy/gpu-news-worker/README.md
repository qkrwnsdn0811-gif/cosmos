# GPU backfill worker

The same enrichment as `deploy/news-enrich`, reached over SSH from a
workstation with a GPU, for the one job where a GPU is worth it: clearing the
~180,000 pair arrears in ~25 minutes instead of ~6.8 hours.

**The live pipeline does not use this.** Ten-minute passes are about fourteen
sentences each; they run on the server under `cosmos-news-enrich.timer` with
`--transport local --device cpu`. Read `deploy/news-enrich/README.md` first —
it describes what is computed and why. This file only covers the transport.

Output, identical in shape to the local path:

```text
/data-lake/analyzed/news/company-sentiment/
  model_version=evidence-sentence-finbert-v1/source_model_version=dict-v1.3/
  run_id=<run>/batch=<batch>/{data.parquet,_manifest.json,_SUCCESS}
```

The verdict is per (article, company), read off the sentences that name that
company and no other company from the same article. It is financial polarity
from a sentence classifier, not an aspect-trained company model and not a
stock-price forecast.

## WSL2 installation

```bash
python3 -m venv ~/cosmos-gpu-news/venv
~/cosmos-gpu-news/venv/bin/pip install --upgrade pip
~/cosmos-gpu-news/venv/bin/pip install \
  torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
~/cosmos-gpu-news/venv/bin/pip install -r AI/gpu_news/requirements.txt

mkdir -p ~/cosmos-gpu-news/models ~/.config/cosmos ~/.config/systemd/user
~/cosmos-gpu-news/venv/bin/python AI/gpu_news/download_models.py \
  --output ~/cosmos-gpu-news/models/finbert-v1
cp deploy/gpu-news-worker/worker.example.json ~/.config/cosmos/gpu-news-worker.json
cp deploy/gpu-news-worker/cosmos-gpu-news-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

`evidence.py` imports from `AI/graph` and `AI/ner`, so both have to be on
`PYTHONPATH`; `run-worker.sh` sets it.

## One bounded pass first

```bash
cd ~/cosmos-gpu-news/current/AI/gpu_news
PYTHONPATH=.:../graph:../ner PYTHONUTF8=1 \
  ~/cosmos-gpu-news/venv/bin/python worker.py \
  --config ~/.config/cosmos/gpu-news-worker.json \
  --bundle ~/cosmos-gpu-news/models/finbert-v1 \
  --transport ssh --device cuda --batch-size 64 --max-batches 1
```

Then run it to completion, or enable the service for an unattended sweep.

The worker takes an exclusive lock, so a duplicate launch fails rather than
processing the same batch twice. Output directories are immutable and
published by rename, so an interrupted run resumes without re-doing finished
batches.

WSL distributions may stop when the last Windows-side WSL process exits. For an
unattended sweep, register `run-worker.sh` as a logon task so the distro stays
alive. This fragility is a reason the live path does not depend on this host.

## When the backfill is done

Nothing here needs to keep running. The live timers on the server cover new
articles; this exists for the arrears and for any future full reprocess.
