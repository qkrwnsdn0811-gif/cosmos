"""Train/evaluate all three original Story 3 stages on identical temporal rows.

Stage1: frozen KR-FinBERT/FinBERT document polarity times direct prominence or
two-hop signed graph attenuation (explicit heuristic, including COMPETE=-1).
Stage2: graph-free LightGBM, using the SAME frozen text representation as GAT,
financial sentiment, ticker, known static attributes and direct mention metadata.
Stage3: 2-layer PyG GATv2, 3 seeds, joint 1/3-session direction/magnitude targets.

PCA64, neutral abs-return q33, magnitude abs-return q95 and majority baseline
are fit on train ONLY. GAT checkpoint selection uses val CE + magnitude Huber;
LightGBM early stopping uses validation only. Test never changes model choice.
Article-weighted loss/LightGBM weights avoid broad articles dominating training.
Metrics are reported on exactly the same article-company rows for all stages,
with separate markets/directness and paired SESSION-clustered uncertainty.
Weights, graph, preprocessing, metrics and predictions are saved, not just logs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import torch
from scipy.stats import spearmanr
from sklearn.decomposition import PCA
from torch.nn import functional as F

from news_impact_data import OUT, graph_arrays, mention_array, node_features
from news_impact_model import NewsGAT


def targets(y, train):
    thresholds = np.quantile(abs(y[train]), 0.33, axis=0)
    scales = np.maximum(np.quantile(abs(y[train]), 0.95, axis=0), 1e-6)
    labels = np.where(y > thresholds, 2, np.where(y < -thresholds, 0, 1)).astype("int64")
    magnitude = np.minimum(abs(y) / scales, 1).astype("float32")
    return labels, magnitude, thresholds.astype("float32"), scales.astype("float32")


def rule_predictions(docs, pairs, sentiment, threshold_norm):
    result = np.empty(len(pairs), dtype="float32")
    metadata = [{m["ticker"]: m for m in d["companies"]} for d in docs]
    for i, r in enumerate(pairs.itertuples()):
        m = metadata[r.article][r.rule_source]
        prominence = {"title": 1., "lead": .8, "body": .5}.get(m["first_pos"], .5)
        prominence *= min(1., .7 + .1 * m["n_mentions"])
        result[i] = (sentiment[r.article, 2] - sentiment[r.article, 0]) * prominence * r.rule_weight * r.rule_sign
    mag = np.repeat(abs(result)[:, None], 2, axis=1)
    cls = np.where(result[:, None] > threshold_norm, 2, np.where(result[:, None] < -threshold_norm, 0, 1))
    probs = np.eye(3, dtype="float32")[cls]
    return probs, mag


class TrainingData:
    def __init__(self, out=OUT):
        self.out = Path(out)
        from news_text_features import feature_manifest
        saved_manifest = json.loads((self.out / "feature_manifest.json").read_text())
        if saved_manifest != feature_manifest(self.out):
            raise ValueError("Text features are stale; rerun news_text_features.py for the current articles/preprocessing")
        self.docs = [json.loads(l) for l in (self.out / "articles.jsonl").open(encoding="utf8")]
        self.pairs = pd.read_parquet(self.out / "pairs.parquet")
        self.meta = pd.read_json(self.out / "companies.json", dtype={"ticker": str})
        self.tickers = self.meta.ticker.tolist()
        self.graphs = json.loads((self.out / "graphs.json").read_text(encoding="utf8"))
        self.sentiment = np.load(self.out / "sentiment.npy")
        embedding = np.load(self.out / "embeddings.npy")
        article_train = np.array([r["split"] == "train" for r in self.docs])
        pca = PCA(n_components=min(64, embedding.shape[1], int(article_train.sum())), random_state=42)
        pca.fit(embedding[article_train])
        self.text = pca.transform(embedding).astype("float32")
        np.savez(self.out / "text_projection.npz", mean=pca.mean_, components=pca.components_)
        self.mentions = np.stack([mention_array(r["companies"], self.tickers) for r in self.docs])
        self.static = {k: node_features(self.meta, g) for k, g in self.graphs.items()}
        self.graph_arrays = {k: graph_arrays(g, self.tickers) for k, g in self.graphs.items()}
        self.y = self.pairs[["y_1d", "y_3d"]].to_numpy(dtype="float32")
        self.train = (self.pairs.split == "train").to_numpy()
        self.val = (self.pairs.split == "val").to_numpy()
        self.test = (self.pairs.split == "test").to_numpy()
        self.cls, self.mag, self.thresholds, self.scales = targets(self.y, self.train)
        self.node_idx = self.pairs.ticker.map({t: i for i, t in enumerate(self.tickers)}).to_numpy()
        self.article_idx = self.pairs.article.to_numpy()
        shape = (len(self.docs), len(self.tickers), 2)
        self.dense_cls = np.zeros(shape, dtype="int64")
        self.dense_mag = np.zeros(shape, dtype="float32")
        self.mask = np.zeros(shape[:2], dtype="bool")
        self.dense_cls[self.article_idx, self.node_idx] = self.cls
        self.dense_mag[self.article_idx, self.node_idx] = self.mag
        self.mask[self.article_idx, self.node_idx] = True

    def tabular(self):
        # No graph-derived feature: no candidate weight/sign/path/neighbor info.
        a, n = self.article_idx, self.node_idx
        static = np.stack([self.static[g][t] for g, t in zip(self.pairs.graph, n)])
        return np.concatenate([self.text[a], self.sentiment[a], self.mentions[a, n],
                               static, n[:, None]], axis=1).astype("float32")


def fit_lightgbm(data):
    x = data.tabular()
    a = data.article_idx
    counts = np.bincount(a)
    weights = (1 / counts[a]).astype("float32")
    prob, mag, versions = [], [], []
    for h in range(2):
        for task, y, objective in (("direction", data.cls[:, h], "multiclass"),
                                    ("magnitude", data.mag[:, h], "regression_l1")):
            params = dict(objective=objective, learning_rate=.04, num_leaves=23, min_data_in_leaf=100,
                          max_depth=6, lambda_l2=5., feature_fraction=.8, seed=42, verbosity=-1,
                          num_threads=4, force_col_wise=True, deterministic=True)
            if task == "direction":
                params["num_class"] = 3
            train = lgb.Dataset(x[data.train], label=y[data.train], weight=weights[data.train],
                                categorical_feature=[x.shape[1] - 1])
            val = lgb.Dataset(x[data.val], label=y[data.val], weight=weights[data.val], reference=train)
            model = lgb.train(params, train, num_boost_round=250, valid_sets=[val],
                              callbacks=[lgb.early_stopping(25, verbose=False)])
            filename = f"lightgbm_{task}_{(1, 3)[h]}d.txt"
            model.save_model(str(data.out / filename))
            pred = model.predict(x)
            (prob if task == "direction" else mag).append(pred)
            versions.append({"file": filename, "best_iteration": model.best_iteration})
            print("LightGBM", task, h, model.best_iteration, flush=True)
    return np.stack(prob, axis=1).astype("float32"), np.clip(np.stack(mag, axis=1), 0, 1).astype("float32"), versions


def loss_fn(logits, pred_mag, cls, mag, mask):
    ce = F.cross_entropy(logits.reshape(-1, 3), cls.reshape(-1), reduction="none").reshape(*cls.shape)
    err = F.smooth_l1_loss(pred_mag, mag, reduction="none")
    # Equal article weighting, both horizons jointly; no masked rows affect loss.
    per_node = (ce + err).mean(-1) * mask
    return (per_node.sum(-1) / mask.sum(-1).clamp(min=1)).mean()


def batches(data, split, batch_size, rng=None):
    keys = sorted({r["graph"] for r in data.docs if r["split"] == split})
    result = []
    for key in keys:
        ids = np.array([i for i, r in enumerate(data.docs) if r["split"] == split and r["graph"] == key])
        if rng is not None:
            rng.shuffle(ids)
        result.extend((key, ids[i:i + batch_size]) for i in range(0, len(ids), batch_size))
    if rng is not None:
        rng.shuffle(result)
    return result


def fit_gat(data, seeds=3, epochs=30, patience=6, batch_size=24, device="cuda"):
    dev = torch.device(device)
    ts = lambda a, dtype=None: torch.as_tensor(a, dtype=dtype, device=dev)
    text, sentiment, mentions = ts(data.text), ts(data.sentiment), ts(data.mentions)
    static = {k: ts(v) for k, v in data.static.items()}
    edges = {k: (ts(e), ts(a)) for k, (e, a) in data.graph_arrays.items()}
    cls, mag, mask = ts(data.dense_cls), ts(data.dense_mag), ts(data.mask)
    configs, states, history, predictions = [], [], [], []
    config = dict(n_companies=len(data.tickers), static_dim=next(iter(data.static.values())).shape[-1],
                  text_dim=data.text.shape[-1], edge_dim=11)

    def run(model, split, optimizer=None, rng=None, predict=False):
        model.train(optimizer is not None)
        losses, sizes = [], []
        # Outputs are only retained for prediction; training never stores autograd graphs.
        result_p = np.empty((len(data.docs), len(data.tickers), 2, 3), dtype="float32") if predict else None
        result_m = np.empty((len(data.docs), len(data.tickers), 2), dtype="float32") if predict else None
        for key, ids in batches(data, split, batch_size, rng):
            ii = ts(ids)
            with torch.set_grad_enabled(optimizer is not None):
                logits, pred_mag = model(text[ii], sentiment[ii], mentions[ii], static[key], *edges[key])
                loss = loss_fn(logits, pred_mag, cls[ii], mag[ii], mask[ii])
                if optimizer is not None:
                    optimizer.zero_grad(set_to_none=True)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 2.)
                    optimizer.step()
            losses.append(float(loss.detach().cpu())); sizes.append(len(ids))
            if predict:
                result_p[ids] = logits.softmax(-1).detach().cpu().numpy()
                result_m[ids] = pred_mag.detach().cpu().numpy()
        return float(np.average(losses, weights=sizes)), result_p, result_m

    for seed in range(seeds):
        torch.manual_seed(seed); np.random.seed(seed)
        if dev.type == "cuda":
            torch.cuda.manual_seed_all(seed)
        model = NewsGAT(**config).to(dev)
        optimizer = torch.optim.AdamW(model.parameters(), lr=8e-4, weight_decay=1e-3)
        rng = np.random.default_rng(seed)
        best, best_epoch, state = float("inf"), -1, None
        for epoch in range(epochs):
            start = time.monotonic()
            train_loss, _, _ = run(model, "train", optimizer, rng)
            val_loss, _, _ = run(model, "val")
            history.append(dict(seed=seed, epoch=epoch, train_loss=train_loss, val_loss=val_loss,
                                seconds=time.monotonic() - start))
            print(json.dumps(history[-1]), flush=True)
            if val_loss < best - 1e-5:
                best, best_epoch = val_loss, epoch
                state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            elif epoch - best_epoch >= patience:
                break
        model.load_state_dict(state)
        states.append(state); configs.append(dict(seed=seed, best_epoch=best_epoch, val_loss=best))
        # Test first becomes visible ONLY after the checkpoint has been selected.
        result_p = np.zeros((len(data.pairs), 2, 3), dtype="float32")
        result_m = np.zeros((len(data.pairs), 2), dtype="float32")
        for split in ("train", "val", "test"):
            _, p, m = run(model, split, predict=True)
            selected = (data.pairs.split == split).to_numpy()
            a, n = data.article_idx[selected], data.node_idx[selected]
            result_p[selected], result_m[selected] = p[a, n], m[a, n]
        predictions.append((result_p, result_m))
        torch.save({"config": config, "state_dict": state, "selection": configs[-1]}, data.out / f"gat_seed{seed}.pt")
        pd.DataFrame(history).to_csv(data.out / "training_history.csv", index=False)
    return np.mean([p for p, _ in predictions], axis=0), np.mean([m for _, m in predictions], axis=0), configs


def rank_ic(x, y):
    if len(x) < 3 or np.ptp(x) < 1e-10 or np.ptp(y) < 1e-10:
        return None
    return float(spearmanr(x, y).statistic)


def common_rank_score(x, y):
    """Same label-eligible articles for every model; constant ranking = zero skill."""
    if len(y) < 3 or np.ptp(y) < 1e-10:
        return None
    ic = rank_ic(x, y)
    return ic if ic is not None else 0.


def metrics(data, outputs):
    rows, paired = [], []
    daily = {}
    for split in ("val", "test"):
        for market in ("KOSPI", "NASDAQ"):
            for scope in ("all", "direct", "indirect"):
                mask = ((data.pairs.split == split) & (data.pairs.market == market)).to_numpy()
                if scope != "all":
                    mask &= data.pairs.is_direct.to_numpy() == (scope == "direct")
                if not mask.any():
                    continue
                subset = data.pairs[mask]
                for name, (p, m) in outputs.items():
                    for h, horizon in enumerate((1, 3)):
                        pred = p[mask, h].argmax(-1)
                        signed = (p[mask, h, 2] - p[mask, h, 0]) * m[mask, h]
                        correct = pred == data.cls[mask, h]
                        temp = pd.DataFrame({"date": subset.anchor_date.to_numpy(), "article": subset.article.to_numpy(),
                                             "correct": correct.astype(float), "signed": signed, "y": data.y[mask, h]})
                        by_day = temp.groupby("date").correct.mean()
                        daily[split, market, scope, horizon, name] = by_day
                        groups = [g for _, g in temp.groupby("article") if len(g) >= 3 and np.ptp(g.y.to_numpy()) >= 1e-10]
                        ics = [ic for g in groups if (ic := rank_ic(g.signed.to_numpy(), g.y.to_numpy())) is not None]
                        # A constant prediction has undefined Spearman, but must
                        # not silently remove hard articles from model comparison.
                        # Report conventional IC separately; the common ranking
                        # score assigns zero skill to a constant prediction.
                        common_ranks = [common_rank_score(g.signed.to_numpy(), g.y.to_numpy()) for g in groups]
                        rows.append(dict(split=split, market=market, scope=scope, model=name, horizon=horizon,
                            n_pairs=len(subset), n_articles=subset.article.nunique(), n_sessions=len(by_day),
                            direction_accuracy=float(correct.mean()), session_accuracy=float(by_day.mean()),
                            signed_pooled_ic=rank_ic(signed, data.y[mask, h]),
                            article_rank_ic=float(np.mean(ics)) if ics else None, n_ic_articles=len(ics),
                            common_article_rank_score=float(np.mean(common_ranks)) if common_ranks else None,
                            n_rank_articles=len(common_ranks),
                            magnitude_mae=float(abs(m[mask, h] - data.mag[mask, h]).mean()),
                            magnitude_ic=rank_ic(m[mask, h], abs(data.y[mask, h]))))
                for horizon in (1, 3):
                    g = daily[split, market, scope, horizon, "stage3"]
                    for base in ("majority", "stage1", "stage2"):
                        diff = (g - daily[split, market, scope, horizon, base]).dropna()
                        se = float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 1 else 0.
                        paired.append(dict(split=split, market=market, scope=scope, horizon=horizon, baseline=base,
                                           metric="equal-session direction accuracy", delta=float(diff.mean()),
                                           session_t=float(diff.mean() / se) if se > 0 else None, n_sessions=len(diff)))
    pd.DataFrame(rows).to_csv(data.out / "evaluation.csv", index=False)
    pd.DataFrame(paired).to_csv(data.out / "paired_session_comparison.csv", index=False)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=24)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()
    torch.set_num_threads(4)
    started = time.monotonic()
    data = TrainingData(args.out)
    np.savez(data.out / "label_config.npz", thresholds=data.thresholds, scales=data.scales)
    majority = np.stack([np.bincount(data.cls[data.train, h], minlength=3) / data.train.sum() for h in range(2)])
    meanmag = data.mag[data.train].mean(0)
    outputs = {"majority": (np.broadcast_to(majority, (len(data.pairs), 2, 3)),
                             np.broadcast_to(meanmag, (len(data.pairs), 2)))}
    outputs["stage1"] = rule_predictions(data.docs, data.pairs, data.sentiment, data.thresholds / data.scales)
    lp, lm, lgb_info = fit_lightgbm(data)
    outputs["stage2"] = lp, lm
    gp, gm, gat_info = fit_gat(data, seeds=args.seeds, epochs=args.epochs, batch_size=args.batch_size, device=args.device)
    outputs["stage3"] = gp, gm
    for name, (p, m) in outputs.items():
        np.savez_compressed(data.out / f"predictions_{name}.npz", probabilities=p, magnitude=m)
    result = metrics(data, outputs)
    metadata = {"task": "news-conditioned direction and magnitude", "horizons": [1, 3],
        "primary_model": "stage3", "gnn": gat_info, "lightgbm": lgb_info, "n_companies": len(data.tickers),
        "neutral_threshold_log_return": data.thresholds.tolist(), "strength_scale_log_return": data.scales.tolist(),
        "train_pairs_per_ticker": {str(k): int(v) for k, v in data.pairs[data.train].ticker.value_counts().items()},
        "text_cleaning": "news_content.py: remove live stock widgets, inserted headlines, sidebars",
        "text_projection": "train PCA64 of frozen multilingual MiniLM shared by Stage2/Stage3",
        "seconds": time.monotonic() - started, "torch": torch.__version__, "lightgbm_version": lgb.__version__,
        "timestamp_precision": "date", "graph_policy": "annual past-only; serving 2026-09-14 snapshot",
        "label_convention": json.loads((data.out / "dataset_audit.json").read_text())["label"],
        "limitations": ["delayed daily-price proxy, not causal attribution or immediate news reaction",
                        "sampled 202-company survivor universe; near-duplicate stories and same-day confounding remain",
                        "historical ownership partially recovered when provided; historical sector attributes remain unavailable and masked",
                        "historically retroactive pretrained NLP/alias versions; not a live historical backtest",
                        "NASDAQ publisher timezone unknown; UTC-12 conservative date cutoff"]}
    files = sorted(data.out.glob("gat_seed*.pt")) + sorted(data.out.glob("lightgbm_*.txt")) + [
        data.out / n for n in ("graphs.json", "text_projection.npz", "label_config.npz", "pretrained.json", "companies.json")]
    files += list(data.out.glob("aliases.csv"))
    metadata["model_version"] = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()[:16]
    (data.out / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf8")
    print(json.dumps(metadata, indent=2), flush=True)
    print(pd.DataFrame(result).query("split == 'test' and scope == 'all'").to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
