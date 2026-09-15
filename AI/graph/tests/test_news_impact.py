"""Temporal leakage, label arithmetic and graph/text isolation regressions."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from news_impact_data import Labels, availability, close_times, expand, mention_array, node_features
from news_impact_model import NewsGAT
from train_news_impact import targets, loss_fn, common_rank_score
from news_content import remove_live_quotes, sanitize_article


class TemporalTests(unittest.TestCase):
    def test_live_quote_changes_cannot_change_model_text(self):
        a = "삼성전자(005930)는 매출 100억원을 기록했다. 삼성전자(251,250원 ▼9,750 -3.74%)"
        b = a.replace("251,250원 ▼9,750 -3.74%", "10,000원 ▲500 +5.0%")
        self.assertEqual(remove_live_quotes(a), remove_live_quotes(b))
        self.assertIn("매출 100억원", remove_live_quotes(a))
        self.assertIn("(005930)", remove_live_quotes(a))
        self.assertEqual(remove_live_quotes("Apple (AAPL -2.3%)"), "Apple ")
        article = dict(title="기사", body=a)
        self.assertEqual(sanitize_article(sanitize_article(article)), sanitize_article(article))

    def test_date_precision_and_dst(self):
        self.assertEqual(str(availability("2024-01-02", "domestic")), "2024-01-02 15:00:00+00:00")
        self.assertEqual(str(availability("2024-01-02", "overseas")), "2024-01-03 12:00:00+00:00")
        self.assertEqual(close_times(["2024-01-03", "2024-07-02", "2024-07-03"], "NASDAQ").hour.tolist(), [21, 20, 17])
        with self.assertRaises(ValueError):
            availability("2024-01-02T12:00:00", "domestic")

    def test_labels_use_only_post_anchor_adj_close_and_purge(self):
        # Two-company market: excess is exactly A log-return minus B log-return.
        dates = pd.bdate_range("2023-12-26", "2024-01-12").difference(pd.DatetimeIndex(["2023-12-29", "2024-01-01"]))
        meta = pd.DataFrame({"ticker": ["A", "B"], "market": ["KOSPI", "KOSPI"]})
        p = pd.DataFrame({"A": np.exp(np.arange(len(dates)) * .02),
                          "B": np.exp(np.arange(len(dates)) * .01)}, index=dates)
        labels = Labels(p, meta)
        t = availability("2024-01-02", "domestic")
        row = labels.label("A", t, "test")
        self.assertEqual(row["anchor_date"], "2024-01-03")
        self.assertAlmostEqual(row["y_1d"], .01)
        self.assertAlmostEqual(row["y_3d"], .03)
        self.assertIsNone(labels.label("A", availability("2023-12-28", "domestic"), "val"))
        # Missing target close is not forward-filled into a fictitious flat return.
        p.loc["2024-01-04", "A"] = np.nan
        self.assertIsNone(Labels(p, meta).label("A", t, "test"))

    def test_train_only_normalization(self):
        y = np.array([[.01, .02], [-.02, -.03], [.03, .01], [100., 100.]])
        mask = np.array([True, True, True, False])
        _, _, threshold, scale = targets(y, mask)
        y[-1] = -99999
        _, _, threshold2, scale2 = targets(y, mask)
        np.testing.assert_array_equal(threshold, threshold2)
        np.testing.assert_array_equal(scale, scale2)


class GraphTests(unittest.TestCase):
    def test_constant_model_does_not_drop_hard_ranking_articles(self):
        actual = np.array([-.02, 0, .01])
        self.assertEqual(common_rank_score(np.zeros(3), actual), 0.)
        self.assertEqual(common_rank_score(actual, actual), 1.)
        self.assertIsNone(common_rank_score(actual, np.zeros(3)))

    def test_two_hops_and_direct_priority(self):
        edges = [dict(src=a, dst=b, type="SUPPLY", weight=.7, sign=1., reverse=False, evidence=[])
                 for a, b in [("A", "B"), ("B", "C"), ("C", "D")]]
        graph = {"edges": edges}
        mentions = [{"ticker": "A", "n_mentions": 2, "first_pos": "title"}]
        result = expand(mentions, graph)
        self.assertEqual(set(result), {"A", "B", "C"})
        self.assertEqual(len(result["C"]["path"]), 2)
        self.assertEqual(list(expand(mentions, graph, cap=1)), ["A"])

    def test_unknown_historical_industry_is_masked(self):
        meta = pd.DataFrame({"ticker": ["A"], "market": ["KOSPI"], "industry_id": ["SEMI"]})
        x = node_features(meta, {"industry_available": False})
        np.testing.assert_array_equal(x, [[1, 0, 0, 0]])

    def test_news_only_reaches_two_hops(self):
        torch.manual_seed(4)
        model = NewsGAT(4, 3, text_dim=6, edge_dim=11, hidden=16, dropout=0).eval()
        edges = torch.tensor([[0, 1, 2], [1, 2, 3]])
        attrs = torch.zeros(3, 11)
        mentions = torch.zeros(1, 4, 5); mentions[0, 0, 0] = 1
        static = torch.zeros(4, 3)
        with torch.no_grad():
            a, am = model(torch.zeros(1, 6), torch.zeros(1, 3), mentions, static, edges, attrs)
            b, bm = model(torch.ones(1, 6), torch.zeros(1, 3), mentions, static, edges, attrs)
        self.assertFalse(torch.allclose(a[:, :3], b[:, :3]))
        torch.testing.assert_close(a[:, 3], b[:, 3])
        torch.testing.assert_close(am[:, 3], bm[:, 3])
        self.assertTrue(((am >= 0) & (am <= 1)).all())

    def test_masked_targets_do_not_change_loss(self):
        logits = torch.randn(2, 3, 2, 3)
        mag = torch.rand(2, 3, 2)
        y = torch.zeros(2, 3, 2, dtype=torch.long)
        ym = torch.zeros(2, 3, 2)
        mask = torch.tensor([[True, False, False], [True, True, False]])
        a = loss_fn(logits, mag, y, ym, mask)
        y[~mask] = 2; ym[~mask] = 1
        torch.testing.assert_close(a, loss_fn(logits, mag, y, ym, mask))


if __name__ == "__main__":
    unittest.main()
