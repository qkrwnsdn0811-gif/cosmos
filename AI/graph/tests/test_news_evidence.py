"""Guard outcome leakage and common-row statistical comparisons."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from verify_news_predictive_value import ablated, article_ranks, holm, priors


class EvidenceTests(unittest.TestCase):
    def test_priors_ignore_test_outcomes_and_fallback_to_market(self):
        pairs = pd.DataFrame({'split': ['train', 'train', 'test', 'test'],
            'market': ['KOSPI'] * 4, 'ticker': ['A', 'B', 'A', 'UNSEEN']})
        cls = np.array([[0, 0], [2, 2], [1, 1], [1, 1]])
        mag = np.array([[.2, .4], [.6, .8], [0., 0.], [1., 1.]])
        before = priors(pairs, cls, mag)
        cls[2:] = 2
        mag[2:] = .999
        after = priors(pairs, cls, mag)
        for key in before:
            for x, y in zip(before[key], after[key]):
                np.testing.assert_array_equal(x, y)
        np.testing.assert_allclose(before['ticker_prior'][0][3, 0], [.5, 0, .5])
        np.testing.assert_allclose(before['ticker_prior'][1][3], [.4, .6])

    def test_rank_constant_predictions_keep_same_articles(self):
        df = pd.DataFrame({'article': np.repeat([5, 19, 30], 4), 'anchor_date': ['2024-01-02'] * 12})
        y = np.array([1., 2, 2, 4, 1, 3, 4, 9, 1, 1, 1, 1])
        x = np.array([3., 2, 2, 1, 1, 1, 1, 1, 1, 2, 3, 4])
        ranked = article_ranks(df, x, y)
        constant = article_ranks(df, np.zeros(12), y)
        self.assertEqual(ranked.index.tolist(), [5, 19])
        self.assertEqual(constant.index.tolist(), [5, 19])
        self.assertAlmostEqual(ranked.loc[5, 'score'], spearmanr(x[:4], y[:4]).statistic)
        self.assertEqual(ranked.loc[19, 'score'], 0.)
        self.assertTrue(constant.score.eq(0).all())

    def test_holm_step_down_restores_original_order(self):
        np.testing.assert_allclose(holm([.04, .001, .02, .8]), [.08, .004, .06, .8])
        np.testing.assert_allclose(holm([1, 0, .01, .01]), [1, 0, .03, .03])

    def test_ablation_does_not_mutate_reference_or_candidates(self):
        data = SimpleNamespace(text=np.ones((2, 4)), sentiment=np.ones((2, 3)),
            mentions=np.ones((2, 3, 5)), mask=np.ones((2, 3), dtype=bool),
            graph_arrays={'2020': (np.array([[0, 1], [1, 2]]), np.ones((2, 11)))})
        no_content = ablated(data, 'no_content', Path('.'))
        no_edges = ablated(data, 'no_edges', Path('.'))
        self.assertTrue(data.text.all() and data.sentiment.all())
        self.assertFalse(no_content.text.any() or no_content.sentiment.any())
        self.assertEqual(no_edges.graph_arrays['2020'][0].shape, (2, 0))
        self.assertEqual(no_edges.graph_arrays['2020'][1].shape, (0, 11))
        self.assertEqual(data.graph_arrays['2020'][0].shape, (2, 2))
        self.assertIs(no_content.mask, data.mask)
        self.assertIs(no_edges.mask, data.mask)


if __name__ == '__main__':
    unittest.main()
