"""U3 학습/서빙의 누수 방지와 체크포인트·후보 계약 회귀 검증."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from impact_ranker import ImpactRanker, infer_sources
from train_impact_gnn import ImpactGNN
from train_impact_u3 import make_events, preference_counts, preference_loss
from impact_dataset import Panel, build_panel


class DataTests(unittest.TestCase):
    def test_missing_train_sigma_is_not_a_small_target(self):
        names = ["A", "B", "C", "D", "NEW"]
        frame = pd.DataFrame([[-1.] * 4 + [np.nan], [0.] * 4 + [np.nan],
                              [1.] * 4 + [np.nan], [10., 5., 1., 2., .001]], columns=names)
        panel = Panel(frame, pd.Series("KOSPI", index=names), pd.Series("I", index=names),
                      {}, pd.Series(["train"] * 3 + ["test"]))
        aligned = {"KOSPI": {"X": np.array([[10., 0., 0., 0., .001]]),
            "Y": np.array([[10., 5., 1., 2., .001]]), "cols": np.arange(5), "rows": np.array([3])}}
        nb = np.zeros((5, 5), bool)
        nb[0, 1:] = True
        with patch("train_impact_u3.aligned_tensors", return_value=aligned):
            events = make_events(panel, nb, 2)["KOSPI"]
        self.assertEqual(len(events["source"]), 1)
        np.testing.assert_array_equal(events["valid"][0], [False, True, True, True, False])
        self.assertTrue(np.isnan(events["truth"][0, 4]))

    def test_future_price_cannot_change_train_clipping(self):
        rng = np.random.default_rng(5)
        dates = pd.DatetimeIndex(np.concatenate([pd.bdate_range(f"{y}-01-01", periods=60).values
                                                for y in [2016, 2017, 2023, 2024]]))
        records = []
        for i in range(10):
            price = 100 * np.exp(np.cumsum(rng.normal(0, .02, len(dates))))
            records.extend({"ticker": str(i), "market": "KOSPI" if i < 5 else "NASDAQ",
                            "trading_at": d, "adj_close": x} for d, x in zip(dates, price))
        original = pd.DataFrame(records)
        changed = original.copy()
        changed.loc[(changed.ticker == "0") & (changed.trading_at.dt.year == 2024), "adj_close"] *= 100
        industry = pd.DataFrame({"ticker": [str(i) for i in range(10)], "industry_id": ["I"] * 10})
        panels = []
        for prices in [original, changed]:
            with patch("impact_dataset.pd.read_parquet", return_value=prices), \
                 patch("impact_dataset.pd.read_csv", return_value=industry):
                panels.append(build_panel(factors="market", winsor_fit_end="2022-12-31"))
        pd.testing.assert_frame_equal(panels[0].resid.loc["2017"], panels[1].resid.loc["2017"])


class PreferenceTests(unittest.TestCase):
    def test_aggregated_loss_matches_event_average_and_gradients(self):
        source = np.array([0, 0, 1])
        truth = np.array([[3., 1., 2., 99.], [1., 1., 3., 99.], [2., 4., 1., 99.]])
        valid = np.tile([True, True, True, False], (3, 1))
        pref = torch.tensor(preference_counts(source, truth, valid, 2))
        score = torch.tensor([[0.5, -0.1, 0.3, 1e5], [-0.2, 0.4, 0.1, 1e5]], requires_grad=True)
        got = preference_loss(score, pref)
        losses = []
        for node, target, mask in zip(source, truth, valid):
            terms = [F.softplus(-(score[node, i] - score[node, j]))
                     for i in range(4) for j in range(4)
                     if mask[i] and mask[j] and target[i] > target[j]]
            losses.append(torch.stack(terms).mean())
        expected = torch.stack(losses).mean()
        torch.testing.assert_close(got, expected)
        got.backward()
        self.assertTrue(torch.isfinite(score.grad).all())
        self.assertTrue((score.grad[:, 3] == 0).all())

    def test_less_than_three_candidates_is_not_training_data(self):
        with self.assertRaises(ValueError):
            preference_counts(np.array([0]), np.array([[1., 2., 3.]]),
                              np.array([[True, True, False]]), 1)

    def test_equal_scores_still_have_finite_learning_gradient(self):
        pref = torch.tensor(preference_counts(np.array([0]), np.array([[3., 2., 1.]]),
                            np.ones((1, 3), bool), 1))
        score = torch.zeros((1, 3), requires_grad=True)
        preference_loss(score, pref).backward()
        self.assertTrue(torch.isfinite(score.grad).all())
        self.assertGreater(float(score.grad.abs().sum()), 0)


class BundleTests(unittest.TestCase):
    def test_round_trip_filters_market_self_and_non_neighbors(self):
        torch.manual_seed(2)
        n = 6
        model = ImpactGNN(n_feat=2, hops=2)
        src = torch.tensor([0, 0, 0, 1, 2, 3, 4, 5])
        dst = torch.tensor([1, 2, 4, 2, 3, 4, 5, 0])
        features = torch.randn(len(src), 2)
        norm = torch.ones(n)
        expected = infer_sources(model, src, dst, features, norm, n)
        neighbors = torch.zeros((n, n), dtype=torch.bool)
        neighbors[0, [0, 1, 2, 4]] = True
        bundle = {"schema_version": 1, "objective": "U3_UNIT_SOURCE",
            "metadata": {"graph_as_of": "2024-01-01"}, "tickers": [f"T{i}" for i in range(n)],
            "markets": ["KOSPI"] * 4 + ["NASDAQ"] * 2, "names": [f"N{i}" for i in range(n)],
            "train_days": [150] * n, "neighbors": neighbors, "relations": {"0:1": ["PARTNER"]},
            "states": [model.state_dict()], "model_config": {"n_feat": 2, "hops": 2},
            "src": src, "dst": dst, "features": features, "norm": norm}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.pt"
            torch.save(bundle, path)
            ranker = ImpactRanker(path)
        np.testing.assert_allclose(ranker.scores, expected, atol=1e-6)
        result = ranker.rank("T0", "KOSPI")
        self.assertEqual({r["ticker"] for r in result["companies"]}, {"T1", "T2"})
        self.assertAlmostEqual(sum(r["rankScore"] for r in result["companies"]), 100., places=5)
        self.assertEqual(ranker.rank("T0", "NASDAQ")["companies"][0]["ticker"], "T4")
        self.assertEqual(ranker.rank("T5", "KOSPI")["status"], "NO_RELATION_CANDIDATES")
        for params in [("BAD", "KOSPI", 1), ("T0", "OTHER", 1), ("T0", "KOSPI", 0),
                       ("T0", "KOSPI", True)]:
            with self.assertRaises(ValueError):
                ranker.rank(*params)


if __name__ == "__main__":
    torch.set_num_threads(2)
    unittest.main()
