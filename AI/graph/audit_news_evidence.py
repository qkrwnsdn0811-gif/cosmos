"""Read-only numerical cross-check of the finished predictive-value experiment.

Use a Bartlett covariance matrix (independent of the lag-sum implementation),
sequential Holm rejection thresholds, and scalar scipy Spearman comparisons.
Also check frozen source/code hashes, original reported accuracy, common counts,
and validation checkpoint selection. No fitting, selection, or service calls.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from verify_news_predictive_value import HERE, article_ranks, dump, sha


def main(out):
    out = out.resolve()
    protocol = json.loads((out / 'protocol.json').read_text(encoding='utf8'))
    source = Path(protocol['source'])
    assert all(sha(source / n) == h for n, h in protocol['source_hashes'].items())
    assert all(sha(HERE / n) == h for n, h in protocol['code_hashes'].items())
    result = pd.read_csv(out / 'evaluation.csv')
    comparisons = pd.read_csv(out / 'comparisons.csv')
    daily = pd.read_csv(out / 'session_differences.csv')
    assert len(result) == 64 and len(comparisons) == 168
    for column in ('n_pairs', 'n_articles', 'n_rank_articles'):
        assert result.groupby(['split', 'market', 'horizon'])[column].nunique().eq(1).all()
    original = pd.read_csv(source / 'evaluation.csv').query("scope == 'all'")
    merged = result.merge(original, on=['split', 'market', 'horizon', 'model'])
    assert len(merged) == 32
    accuracy_error = float(abs(merged.pair_accuracy - merged.direction_accuracy).max())
    assert accuracy_error < 1e-10
    groups = daily[daily.split.eq('test')].groupby(['market', 'horizon', 'metric', 'baseline'])
    se_error, mean_rounding_error = 0., 0.
    for row in comparisons.itertuples():
        values = groups.get_group((row.market, row.horizon, row.metric, row.baseline)).sort_values('date').delta.to_numpy()
        n = len(values)
        centered = values - values.mean()
        distances = abs(np.arange(n)[:, None] - np.arange(n)[None, :])
        covariance_weights = np.maximum(0, 1 - distances / (row.lag + 1))
        se = np.sqrt(max(0, centered @ covariance_weights @ centered) / n ** 2)
        se_error = max(se_error, abs(se - row.se))
        mean_rounding_error = max(mean_rounding_error, abs(values.mean() - row.delta))
        # The training evaluator retains float32 strength errors. Verify its
        # float32 mean exactly; the independent float64 mean is also recorded.
        expected_mean = values.astype('float32').mean() if row.metric == 'negative_magnitude_mae' else values.mean()
        assert abs(float(expected_mean) - row.delta) < 1e-12 and n == row.n_sessions
    assert se_error < 1e-12
    for lag, frame in comparisons.groupby('lag'):
        ordered = frame.sort_values('p_two_sided')
        rejected, keep_rejecting = [], True
        for i, row in enumerate(ordered.itertuples()):
            keep_rejecting = keep_rejecting and row.p_two_sided < .05 / (len(ordered) - i)
            rejected.append(keep_rejecting)
        np.testing.assert_array_equal(rejected, ordered.p_holm_84.to_numpy() < .05)
    selections = json.loads((out / 'selections.json').read_text())
    for mode, seeds in selections.items():
        history = pd.read_csv(out / mode / 'training_history.csv')
        for seed in seeds:
            h = history[history.seed.eq(seed['seed'])]
            chosen = h[h.epoch.eq(seed['best_epoch'])].iloc[0]
            assert abs(chosen.val_loss - seed['val_loss']) < 1e-12
            assert seed['val_loss'] - h.val_loss.min() <= 1e-5
    pairs = pd.read_parquet(source / 'pairs.parquet')
    checked_ranks, rank_error = 0, 0.
    for model in result.model.unique():
        if model in ('no_content', 'no_edges'):
            path = out / model / 'predictions.npz'
        else:
            path = (source if model in ('majority', 'stage1', 'stage2', 'stage3') else out) / f'predictions_{model}.npz'
        with np.load(path) as saved:
            strength = saved['magnitude']
        for market in ('KOSPI', 'NASDAQ'):
            selected = (pairs.split.eq('test') & pairs.market.eq(market)).to_numpy()
            sub = pairs[selected].reset_index(drop=True)
            for h, horizon in enumerate((1, 3)):
                x, y = strength[selected, h], abs(sub[f'y_{horizon}d'].to_numpy(dtype='float32'))
                ranked = article_ranks(sub, x, y)
                ids = np.random.default_rng(42).choice(ranked.index, min(50, len(ranked)), replace=False)
                for article in ids:
                    mask = sub.article.eq(article).to_numpy()
                    expected = 0. if np.ptp(x[mask]) < 1e-10 else spearmanr(x[mask], y[mask]).statistic
                    rank_error = max(rank_error, abs(expected - ranked.loc[article, 'score']))
                    checked_ranks += 1
    assert rank_error < 1e-12
    evidence = dict(passed=True, source_and_protocol_code_unchanged=True,
        original_accuracy_max_error=accuracy_error, hac_matrix_max_se_error=se_error,
        float32_vs_float64_mean_max_error=mean_rounding_error,
        scalar_rank_checks=checked_ranks, scalar_rank_max_error=rank_error,
        all_168_hac_and_holm_checks_passed=True, six_validation_selections_checked=True)
    dump(out / 'numerical_audit.json', evidence)
    print(json.dumps(evidence), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, default=HERE / 'artifacts/news_impact_v2_evidence')
    main(ap.parse_args().out)
