"""Audit the finished dataset and artifacts without fitting or selecting a model.

Checks all paired rows, exact duplicate separation, split purging, conservative
graph availability and evidence dates. Records input/output hashes and adds
HAC-adjusted uncertainty on the already-saved predictions (overlapping 3-day
labels make adjacent sessions dependent). Never changes predictions or weights.
"""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

from news_impact_data import HERE, OUT, SPLITS, availability
from train_news_impact import targets


def hac_mean_se(values, lag=5):
    x = np.asarray(values, dtype=float)
    x = x - x.mean()
    n = len(x)
    if n < 2:
        return None
    variance = float(x @ x / n)
    for k in range(1, min(lag, n - 1) + 1):
        variance += 2 * (1 - k / (lag + 1)) * float(x[k:] @ x[:-k] / n)
    return float(np.sqrt(max(variance, 0) / n))


def main(out=OUT):
    OUT = Path(out).resolve()
    docs = [json.loads(l) for l in (OUT / "articles.jsonl").open(encoding="utf8")]
    pairs = pd.read_parquet(OUT / "pairs.parquet")
    graphs = json.loads((OUT / "graphs.json").read_text(encoding="utf8"))
    assert not pairs.duplicated(["news_id", "ticker"]).any()
    assert len({r["text_hash"] for r in docs}) == len(docs)
    for r in docs:
        knowledge = pd.Timestamp(graphs[r["graph"]]["as_of"] + "T12:00:00+00:00")
        assert knowledge <= availability(r["published_date"], r["region"])
    for g in graphs.values():
        for e in g["edges"]:
            assert all(v["date"][:10] < g["as_of"] for v in e["evidence"])
    for split, (_, end) in SPLITS.items():
        p = pairs[pairs.split == split]
        assert (p.label_end <= end).all()
        assert (p.anchor_date < p.label_end).all()
    y = pairs[["y_1d", "y_3d"]].to_numpy(dtype="float32")
    cls, mag, _, _ = targets(y, (pairs.split == "train").to_numpy())
    predictions = {name: np.load(OUT / f"predictions_{name}.npz") for name in ("majority", "stage1", "stage2", "stage3")}
    for out in predictions.values():
        assert out["probabilities"].shape == (len(pairs), 2, 3)
        assert out["magnitude"].shape == (len(pairs), 2)
        assert np.isfinite(out["probabilities"]).all() and np.isfinite(out["magnitude"]).all()
        assert np.allclose(out["probabilities"].sum(-1), 1, atol=2e-6)
        assert ((out["magnitude"] >= 0) & (out["magnitude"] <= 1)).all()
    # Training already computed the expensive per-article ranks. Audit those
    # saved report counts and independently recompute every accuracy directly
    # from saved predictions, rather than rewrite the same metrics a second time.
    evaluation = pd.read_csv(OUT / "evaluation.csv")
    for row in evaluation.itertuples():
        selected = ((pairs.split == row.split) & (pairs.market == row.market)).to_numpy()
        if row.scope != "all":
            selected &= pairs.is_direct.to_numpy() == (row.scope == "direct")
        h = (1, 3).index(row.horizon)
        actual = (predictions[row.model]["probabilities"][selected, h].argmax(-1) == cls[selected, h]).mean()
        assert abs(actual - row.direction_accuracy) < 1e-10
        assert int(selected.sum()) == row.n_pairs
    counts = evaluation.groupby(["split", "market", "scope", "horizon"])
    assert (counts.n_pairs.nunique() == 1).all()
    assert (counts.n_rank_articles.nunique() == 1).all()
    comparisons = []
    for split in ("val", "test"):
        for market in ("KOSPI", "NASDAQ"):
            for scope in ("all", "direct", "indirect"):
                selected = ((pairs.split == split) & (pairs.market == market)).to_numpy()
                if scope != "all":
                    selected &= pairs.is_direct.to_numpy() == (scope == "direct")
                for h, horizon in enumerate((1, 3)):
                    actual = cls[selected, h]
                    g = predictions["stage3"]["probabilities"][selected, h].argmax(-1) == actual
                    for base in ("majority", "stage1", "stage2"):
                        b = predictions[base]["probabilities"][selected, h].argmax(-1) == actual
                        daily = pd.Series(g.astype(float) - b.astype(float), index=pairs.anchor_date[selected]).groupby(level=0).mean()
                        se = hac_mean_se(daily, 5)
                        delta = float(daily.mean())
                        comparisons.append(dict(split=split, market=market, scope=scope, horizon=horizon,
                            baseline=base, delta=delta, hac_lag=5, n_sessions=len(daily),
                            hac_t=delta / se if se and se > 0 else None,
                            ci95_low=delta - 1.96 * se if se else None, ci95_high=delta + 1.96 * se if se else None))
    pd.DataFrame(comparisons).to_csv(OUT / "paired_hac_comparison.csv", index=False)
    raw_inputs = [HERE / "data/prices_daily.parquet", HERE / "data/rel_hits.jsonl",
                  HERE / "data/edges_ownership.csv", HERE / "data/edges_sector.csv",
                  HERE.parent / "ner/data/aliases.csv", HERE.parent / "ner/data/company_industry.csv"]
    files = raw_inputs + list(OUT.glob("index_*.csv")) + list(OUT.glob("historical_ownership.jsonl")) + list(HERE.glob("*news*.py")) + [HERE / "news_content.py"] + [
        OUT / n for n in ("dataset_audit.json", "graphs.json", "pairs.parquet", "articles.jsonl", "pretrained.json", "metadata.json")]
    manifest = {str(p.relative_to(HERE.parent.parent)).replace("\\", "/"): {
        "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}
    (OUT / "audit_manifest.json").write_text(json.dumps({"checks_passed": True, "articles": len(docs),
        "pairs": len(pairs), "files": manifest}, indent=2), encoding="utf8")
    print("dataset/model audit passed", len(docs), len(pairs), flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    main(ap.parse_args().out)
