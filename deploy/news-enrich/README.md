# News enrichment — evidence sentences, sentiment, relevance

Two timers, ten minutes apart, that take a company-matched article the rest of
the way to the screen:

```
cosmos-news-enrich      10min  HDFS company-mentions -> HDFS company-sentiment
cosmos-sentiment-loader 10min  HDFS company-sentiment -> Postgres, then publish
```

## What it computes

For each matched (article, company) pair:

| Column | Where it comes from |
| --- | --- |
| `document_evidence` rows | sentences naming that company and no other company from the same article |
| `company_document.sentiment` | pinned FinBERT over those sentences, unanimous polarity or NULL |
| `company_document.relevance_score` | `0.25 + 0.55*depth + 0.20*share` |
| `company_document.impact_score` | `+1 / -1 / 0` for POSITIVE / NEGATIVE / NEUTRAL |
| `company_document.confidence` | `min(1.0, 0.40 + 0.15*n_sentences)` |
| `company_document.is_service_visible` | TRUE, once the above exist |

`depth = (clamp(n_sentences, 1, 4) - 1) / 3`, `share = 1 / companies_on_the_article`.

Two companies in one article get their own verdicts: one can be POSITIVE while
the other is NEGATIVE, because each reads only its own sentences.

## Why this is the gate

`is_service_visible` is the flag all four news queries in `NewsQueryRepository`
already filter on. The mention loader inserts new pairs with it FALSE, and the
sentiment loader flips it in the same transaction that writes the verdict and
the evidence. So a headline reaches the news page only once there is something
true to say about it — before this, matched-but-unanalysed articles were served
with a NULL sentiment that the frontend drew as "중립".

A pair whose sentences disagree, or that has no attributable sentence, stays
NULL and stays hidden. That is deliberate: a fabricated neutral reads as "we
looked and it was unremarkable", which is a different claim from "we could not
tell".

## No GPU — the bottleneck is not inference

Measured on real batches (2026-09-22, 30 batches): about 10 articles and 55
evidence sentences per batch. On a 22-core desktop FinBERT does 158
sentences/second warm, so the inference in one batch is well under a second.

What actually costs time is talking to HDFS. Measured on this server, one
`hdfs dfs` invocation is **2.4-2.9 seconds** because it starts a JVM. A batch
needs three reads and seven publish steps, so a CLI-driven worker spends ~26
seconds per batch on JVM startup alone — about ten times the real work. That is
why per-batch I/O goes through one persistent PyArrow/libhdfs client, the same
split `AI/ner/realtime_analyzer.py` already uses: CLI for glob discovery (twice
a pass), libhdfs for everything else.

A GPU does not help here, and off-box it makes things worse. The GPU lives on a
workstation outside the cluster, where libhdfs cannot reach the DataNodes, so
the `ssh` transport is stuck with the CLI and its 26 seconds a batch. Server CPU
with libhdfs beats workstation GPU over SSH by roughly an order of magnitude.

## Catching up

The worker treats any input batch without a matching output as pending, so the
first run sees the whole history — 10,951 realtime batches as of 2026-09-22.
`newest_first: true` means the visible top of the feed fills first and the
backlog drains behind it.

`max_per_pass` bounds one timer firing. At ~3 seconds a batch, 150 fits inside
the ten-minute period with room to spare; raise it to 250-300 while catching up
and put it back afterwards. Do not raise it past what fits in the period — a
pass that overruns collides with the next one and loses to the lock.

## Install

```bash
sudo -u ubuntu python3 -m venv /opt/cosmos/news-enrich/venv
/opt/cosmos/news-enrich/venv/bin/pip install --upgrade pip
# CPU-only torch: the CUDA wheels are ~2GB and nothing here uses them.
/opt/cosmos/news-enrich/venv/bin/pip install \
  torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
/opt/cosmos/news-enrich/venv/bin/pip install -r AI/gpu_news/requirements.txt

# Pinned model revisions, downloaded once, then used offline.
/opt/cosmos/news-enrich/venv/bin/python AI/gpu_news/download_models.py \
  --output /opt/cosmos/news-enrich/models/finbert-v1

sudo install -m 0640 deploy/news-enrich/enrich.example.json /etc/cosmos/news-enrich.json
```

The same two models already sit on HDFS, so a server without outbound internet
can take them from there instead. Verified 2026-09-22: the label order in both
`config.json` files matches the pinned revisions exactly, so either source
gives the same result.

```bash
BUNDLE=/data-lake/sandbox/news/junwoo/models/news_impact_v2/66c54ef0207b4783/news_impact_v2_bundle/models
mkdir -p /opt/cosmos/news-enrich/models/finbert-v1
for role in sentiment_ko sentiment_en; do
  /opt/hadoop/bin/hdfs dfs -get "$BUNDLE/$role" /opt/cosmos/news-enrich/models/finbert-v1/
done
# load_model_specs needs the receipt that download_models.py normally writes.
cat > /opt/cosmos/news-enrich/models/finbert-v1/pretrained.json <<'JSON'
{
  "sentiment_en": {"repo": "ProsusAI/finbert",
                   "revision": "4556d13015211d73dccd3fdd39d39232506f3e43",
                   "path": "sentiment_en"},
  "sentiment_ko": {"repo": "snunlp/KR-FinBert-SC",
                   "revision": "f8586286cc3161fb648e9fee09a456069fd846d0",
                   "path": "sentiment_ko"}
}
JSON
```

```bash
sudo install -m 0640 deploy/news-enrich/enrich.env.example /etc/cosmos/news-enrich.env
sudo install -m 0640 deploy/news-enrich/sentiment-loader.env.example \
  /etc/cosmos/sentiment-loader.env
# Edit COSMOS_DSN in sentiment-loader.env before enabling the loader.
sudo install -m 0644 deploy/news-enrich/cosmos-*.service \
  deploy/news-enrich/cosmos-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

## Database privileges

The loader needs rights no existing service role has. Verified on the production
database 2026-09-22:

| role | company_document | document_evidence |
| --- | --- | --- |
| `cosmos_document_loader` | DELETE, INSERT, SELECT, UPDATE | **none** |
| `cosmos_loader` | none | **none** |

Both tables are owned by `cosmos`, so the owner has to grant this before the
loader can run at all — it fails with `permission denied for table
document_evidence` on the first batch:

```sql
GRANT SELECT, INSERT, DELETE ON document_evidence TO cosmos_document_loader;
```

DELETE is needed because evidence is replaced per (document, company) rather
than merged; a re-run must not leave the previous run's sentences beside this
run's verdict.

A dedicated `cosmos_sentiment_loader` role would be cleaner than widening the
document loader's reach. The env file's DSN is the only thing that changes.

## Prove one pass before enabling the timers

```bash
cd /opt/cosmos/news-enrich/current/AI/gpu_news
PYTHONPATH=.:../graph:../ner PYTHONUTF8=1 \
  /opt/cosmos/news-enrich/venv/bin/python worker.py \
  --config /etc/cosmos/news-enrich.json \
  --bundle /opt/cosmos/news-enrich/models/finbert-v1 \
  --transport local --device cpu --max-batches 1
```

The loader defaults to a dry run that exercises every write and rolls it back,
so it is safe to point at production before committing:

```bash
cd /opt/cosmos/news-enrich/current/Crawling/documents
COSMOS_DSN=... PYTHONUTF8=1 /opt/cosmos/news-enrich/venv/bin/python \
  -m services.sentiment_loader.runner \
  --root hdfs://.../company-sentiment/model_version=evidence-sentence-finbert-v1 \
  --state-file /var/lib/cosmos-sentiment-loader/state.json --max-batches 1
```

Then:

```bash
sudo systemctl enable --now cosmos-news-enrich.timer cosmos-sentiment-loader.timer
```

## Turning the gate on

Enabling these timers does not by itself hide anything: pairs already in the
database keep whatever `is_service_visible` they have. What changes is that
*new* pairs start out hidden.

So the order matters. Run the enrichment and the loader until the backlog of
recently matched articles has a verdict, and only then let the mention loader's
FALSE default reach production — otherwise the newest articles disappear from
the feed for as long as the analysis lags. Recovering from that is just running
the loader; nothing is lost.

Rows that are already visible stay visible, including the ones with a NULL
sentiment that the frontend currently draws as "중립". The default only governs
pairs inserted from now on. To check how much of the recent feed already has a
verdict, before deciding anything:

```sql
SELECT count(*) FILTER (WHERE cd.sentiment IS NOT NULL) AS analysed,
       count(*) AS pairs
FROM company_document cd
JOIN source_document sd ON sd.document_id = cd.document_id
WHERE sd.published_at > now() - interval '24 hours';
```

Applying the gate to what is already there is a separate, deliberate step:

```sql
-- Hides every pair that has no verdict yet. Reversible: the loader sets the
-- flag back as it analyses each pair. Run the enrichment first, or the feed
-- goes quiet until it catches up.
UPDATE company_document SET is_service_visible = FALSE WHERE sentiment IS NULL;
```

## PYTHONPATH

`evidence.py` imports the segmentation from `AI/graph/relevance_sentence_evidence.py`
and the attribution guard from `AI/graph/kg_target_sentiment.py`, which in turn
reads `AI/ner`. All three trees have to be importable. `run-news-enrich.sh` sets
this; a manual run has to do the same, or the guard quietly will not be the
audited one.
