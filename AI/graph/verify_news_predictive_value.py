"""Retrospective, falsifiable comparison of the frozen v2 news GAT.

This is NOT a fresh holdout: 2024--2026 outcomes have already been inspected.
Freeze a protocol and hashes before fitting two matched three-seed ablations:
no_content removes embedding AND sentiment (mentions remain); no_edges removes
inter-company messages (GAT self loops and the original candidates remain).
Retrain both with the original train/val split, optimizer and stopping rule.
Thus no_edges measures message-passing benefit conditional on graph candidates,
not the value of graph-based candidate retrieval. Neither is a causal test.

Add train-only market/ticker priors: empirical class proportions, median strength,
unseen ticker -> market -> global. No tuning or test-based model selection.
Primary: equal-session direction accuracy. Secondary: negative magnitude MAE
and common-article magnitude rank (constant prediction=0). Compare all seven
baselines, both markets/horizons: 84 two-sided HAC tests, one Holm family.
Lag 5 is primary, lag 20 is a separately corrected sensitivity analysis.
Unadjusted 95% CIs are labeled; a win needs positive delta and Holm p<.05
at BOTH lags. These are approximate retrospective diagnostics, not proof of
future performance. Original model files are read-only and hash checked.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
import pandas as pd
from scipy.stats import norm
import torch

from audit_news_impact import hac_mean_se
from train_news_impact import TrainingData, fit_gat, targets

HERE = Path(__file__).resolve().parent
BASELINES = ('majority', 'stage1', 'stage2', 'market_prior', 'ticker_prior', 'no_content', 'no_edges')
METRICS = ('accuracy', 'negative_magnitude_mae', 'magnitude_article_rank')
COPIES = ('articles.jsonl', 'pairs.parquet', 'companies.json', 'graphs.json',
          'sentiment.npy', 'embeddings.npy', 'feature_manifest.json', 'pretrained.json')


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def holm(p):
    p = np.asarray(p, dtype=float)
    assert np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all()
    order = np.argsort(p)
    result = np.empty_like(p)
    result[order] = np.minimum(1, np.maximum.accumulate(p[order] * np.arange(len(p), 0, -1)))
    return result


def priors(pairs, cls, mag):
    """All statistics use train rows only; test rows merely select a group."""
    train = pairs.split.eq('train').to_numpy()

    def stats(mask):
        return (np.stack([np.bincount(cls[mask, h], minlength=3) / mask.sum() for h in range(2)]),
                np.median(mag[mask], axis=0))

    global_prior = stats(train)
    market = {k: stats(train & pairs.market.eq(k).to_numpy()) for k in pairs.market[train].unique()}
    ticker = {k: stats(train & pairs.ticker.eq(k).to_numpy()) for k in pairs.ticker[train].unique()}
    result = {}
    for name, lookup in [('market_prior', market), ('ticker_prior', ticker)]:
        key = pairs.market if name == 'market_prior' else pairs.ticker
        pm = [lookup.get(k, market.get(m, global_prior)) for k, m in zip(key, pairs.market)]
        result[name] = (np.array([p for p, _ in pm], dtype='float32'),
                        np.array([m for _, m in pm], dtype='float32'))
    return result


def ablated(data, mode, out):
    result = copy.copy(data)
    result.out = out
    if mode == 'no_content':
        result.text = np.zeros_like(data.text)
        result.sentiment = np.zeros_like(data.sentiment)
    elif mode == 'no_edges':
        result.graph_arrays = {k: (e[:, :0].copy(), a[:0].copy())
                               for k, (e, a) in data.graph_arrays.items()}
    else:
        raise ValueError(mode)
    return result


def article_ranks(frame, prediction, actual):
    """Vectorized Spearman, identical label-eligible articles for every model."""
    df = pd.DataFrame({'article': frame.article.to_numpy(), 'date': frame.anchor_date.to_numpy(),
                       'x': prediction, 'y': actual})
    grouped = df.groupby('article')
    if not grouped.date.nunique().eq(1).all():
        raise ValueError('A market/article must have one anchor session')
    counts = grouped.size()
    eligible = (counts >= 3) & ((grouped.y.max() - grouped.y.min()) >= 1e-10)
    ranks = grouped[['x', 'y']].rank(method='average')
    centered = ranks - ranks.groupby(df.article).transform('mean')
    sums = pd.DataFrame({'article': df.article, 'xx': centered.x ** 2,
                         'yy': centered.y ** 2, 'xy': centered.x * centered.y}).groupby('article').sum()
    denom = np.sqrt(sums.xx * sums.yy)
    score = (sums.xy / denom.replace(0, np.nan)).fillna(0).clip(-1, 1)
    score[(grouped.x.max() - grouped.x.min()) < 1e-10] = 0
    return pd.DataFrame({'score': score[eligible], 'date': grouped.date.first()[eligible]})


def evaluate(source, out):
    pairs = pd.read_parquet(source / 'pairs.parquet')
    y = pairs[['y_1d', 'y_3d']].to_numpy(dtype='float32')
    cls, mag, _, _ = targets(y, pairs.split.eq('train').to_numpy())
    outputs = {}
    for name in ('majority', 'stage1', 'stage2', 'stage3'):
        with np.load(source / f'predictions_{name}.npz') as saved:
            outputs[name] = saved['probabilities'], saved['magnitude']
    outputs.update(priors(pairs, cls, mag))
    for name in ('no_content', 'no_edges'):
        with np.load(out / name / 'predictions.npz') as saved:
            outputs[name] = saved['probabilities'], saved['magnitude']
    for name in ('market_prior', 'ticker_prior'):
        p, m = outputs[name]
        np.savez_compressed(out / f'predictions_{name}.npz', probabilities=p, magnitude=m)
    rows, comparisons, session_rows = [], [], []
    for split in ('val', 'test'):
        for market in ('KOSPI', 'NASDAQ'):
            mask = (pairs.split.eq(split) & pairs.market.eq(market)).to_numpy()
            sub = pairs[mask]
            for h, horizon in enumerate((1, 3)):
                daily = {}
                for name, (p, m) in outputs.items():
                    assert p.shape == (len(pairs), 2, 3) and m.shape == (len(pairs), 2)
                    assert np.isfinite(p).all() and np.isfinite(m).all()
                    assert np.allclose(p.sum(-1), 1, atol=2e-6)
                    assert ((m >= 0) & (m <= 1)).all()
                    correct = (p[mask, h].argmax(-1) == cls[mask, h]).astype(float)
                    error = abs(m[mask, h] - mag[mask, h])
                    ranks = article_ranks(sub, m[mask, h], abs(y[mask, h]))
                    daily[name] = {
                        'accuracy': pd.Series(correct, index=sub.anchor_date).groupby(level=0).mean(),
                        'negative_magnitude_mae': -pd.Series(error, index=sub.anchor_date).groupby(level=0).mean(),
                        'magnitude_article_rank': ranks.groupby('date').score.mean(),
                    }
                    rows.append(dict(split=split, market=market, horizon=horizon, model=name,
                        n_pairs=int(mask.sum()), n_articles=int(sub.article.nunique()),
                        n_rank_articles=len(ranks), pair_accuracy=float(correct.mean()),
                        pair_magnitude_mae=float(error.mean()), article_magnitude_rank=float(ranks.score.mean()),
                        **{k: float(v.mean()) for k, v in daily[name].items()}))
                for metric in METRICS:
                    reference = daily['stage3'][metric]
                    for name in BASELINES:
                        assert reference.index.equals(daily[name][metric].index)
                        diff = reference - daily[name][metric]
                        assert np.isfinite(diff).all() and len(diff) > 20
                        for date, value in diff.items():
                            session_rows.append(dict(split=split, market=market, horizon=horizon,
                                metric=metric, baseline=name, date=date, delta=float(value)))
                        if split != 'test':
                            continue
                        delta = float(diff.mean())
                        for lag in (5, 20):
                            se = hac_mean_se(diff.to_numpy(), lag)
                            z = delta / se if se > 0 else 0.
                            # Zero variance with a nonzero mean is a degenerate deterministic difference.
                            pvalue = float(2 * norm.sf(abs(z))) if se > 0 else (0. if delta != 0 else 1.)
                            comparisons.append(dict(market=market, horizon=horizon, metric=metric,
                                baseline=name, n_sessions=len(diff), lag=lag, delta=delta, se=se,
                                ci95_unadjusted_low=delta - 1.96 * se,
                                ci95_unadjusted_high=delta + 1.96 * se, p_two_sided=pvalue))
    report = pd.DataFrame(rows)
    assert report.groupby(['split', 'market', 'horizon']).n_rank_articles.nunique().eq(1).all()
    comp = pd.DataFrame(comparisons)
    for lag in (5, 20):
        selected = comp.lag.eq(lag)
        assert selected.sum() == 84
        comp.loc[selected, 'p_holm_84'] = holm(comp.loc[selected, 'p_two_sided'])
    comp['positive_significant'] = (comp.delta > 0) & (comp.p_holm_84 < .05)
    stable = comp.groupby(['market', 'horizon', 'metric', 'baseline']).positive_significant.all()
    comp['positive_at_both_lags'] = [bool(stable.loc[r.market, r.horizon, r.metric, r.baseline])
                                    for r in comp.itertuples()]
    report.to_csv(out / 'evaluation.csv', index=False)
    comp.to_csv(out / 'comparisons.csv', index=False)
    pd.DataFrame(session_rows).to_csv(out / 'session_differences.csv', index=False)
    primary = comp[comp.metric.eq('accuracy') & comp.lag.eq(5)]
    verdict = {'scope': 'retrospective previously inspected test, not independent confirmation',
        'primary_direction_superiority_all_cells_and_baselines': bool(primary.positive_at_both_lags.all()),
        'direction_wins_of_28': int(primary.positive_at_both_lags.sum()),
        'comparisons_per_hac_lag': 84, 'source_unchanged': True}
    dump(out / 'verdict.json', verdict)
    print(json.dumps(verdict), flush=True)
    print(report[report.split.eq('test')][['market', 'horizon', 'model', 'pair_accuracy',
                                         'accuracy', 'article_magnitude_rank']].to_string(index=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=HERE / 'artifacts/news_impact_v2')
    ap.add_argument('--out', type=Path, default=HERE / 'artifacts/news_impact_v2_evidence')
    ap.add_argument('--evaluate-only', action='store_true')
    args = ap.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if source == out or source in out.parents:
        raise ValueError('Use a separate sibling experiment directory')
    start = time.monotonic()
    if not args.evaluate_only:
        out.mkdir(parents=True, exist_ok=False)
        files = set(COPIES) | {'text_projection.npz', 'label_config.npz', 'metadata.json'}
        files |= {p.name for p in source.glob('*.pt')} | {p.name for p in source.glob('predictions_*.npz')}
        hashes = {name: sha(source / name) for name in sorted(files)}
        protocol = dict(created_utc=datetime.now(timezone.utc).isoformat(), source=str(source), source_hashes=hashes,
            code_hashes={p.name: sha(p) for p in [Path(__file__), HERE / 'train_news_impact.py',
                                                HERE / 'news_impact_model.py', HERE / 'audit_news_impact.py']},
            seeds=[0, 1, 2], epochs=30, patience=6, batch_size=24,
            primary='equal-session direction accuracy', secondary=list(METRICS[1:]),
            baselines=list(BASELINES), split='2017-2022 train, 2023 val, 2024-2026 test ALREADY SEEN',
            inference='two-sided normal HAC Bartlett lag 5; sensitivity lag 20; Holm all 84 per lag',
            success='positive delta and Holm p<.05 at both lags; broad direction claim needs all 28 cells',
            stopping='one fixed run per ablation; no outcome-based tuning',
            limits=['retrospective, not preregistered before seeing original test results',
                    'current survivor universe and modern frozen language models',
                    'no_content retains article mention metadata',
                    'no_edges retains graph-derived candidate selection',
                    'HAC lag is measured in observed news-bearing sessions',
                    'uncertainty is conditional on the fitted ensembles, not repeated dataset training'])
        dump(out / 'protocol.json', protocol)
        snapshot = out / 'training_inputs'
        snapshot.mkdir()
        for name in COPIES:
            shutil.copy2(source / name, snapshot / name)
        torch.set_num_threads(4)
        data = TrainingData(snapshot)
        with np.load(source / 'text_projection.npz') as old, np.load(snapshot / 'text_projection.npz') as new:
            for name in old.files:
                np.testing.assert_allclose(old[name], new[name], atol=1e-6, rtol=1e-6)
        selections = {}
        for mode in ('no_content', 'no_edges'):
            target = out / mode
            target.mkdir()
            dump(target / 'MODEL_POLICY.json', {'mode': mode, 'research_only': True,
                'warning': 'Requires this ablation input policy at inference; do not load into service'})
            print('TRAIN', mode, flush=True)
            gp, gm, selected = fit_gat(ablated(data, mode, target), seeds=3, epochs=30, patience=6,
                                       batch_size=24, device='cuda')
            np.savez_compressed(target / 'predictions.npz', probabilities=gp, magnitude=gm)
            selections[mode] = selected
            dump(out / 'selections.json', selections)
        print('TRAINING COMPLETE', time.monotonic() - start, flush=True)
    protocol = json.loads((out / 'protocol.json').read_text(encoding='utf8'))
    assert protocol['source'] == str(source)
    assert all(sha(source / name) == value for name, value in protocol['source_hashes'].items())
    evaluate(source, out)
    assert all(sha(source / name) == value for name, value in protocol['source_hashes'].items())
    dump(out / 'completion.json', {'seconds_this_run': time.monotonic() - start, 'source_unchanged': True,
        'finished_utc': datetime.now(timezone.utc).isoformat(),
        'output_hashes': {p.name: sha(p) for p in out.glob('*.csv')}})


if __name__ == '__main__':
    main()
