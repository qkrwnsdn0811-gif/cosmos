"""Portable CPU inference for original Story 3 (news -> direction + magnitude).

Requires trained artifacts, frozen encoders, and versioned graph snapshots, but
no price files, DB, GPU or remote API. Annual snapshots are selected at/before
the article date; the newer unvalidated serving graph is not silently enabled.
Stage3 is primary; Stage1/2 are explicit selectable fallbacks/comparators.
Score is normalized magnitude, NOT a probability or predicted percent return.
"""
from __future__ import annotations
import json
import sys
import threading
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import torch

from news_impact_data import OUT, availability, expand, graph_arrays, mention_array, node_features
from news_impact_model import NewsGAT
from news_text_features import FrozenText
from news_content import sanitize_article


class NewsImpactPredictor:
    def __init__(self, out=OUT, explanation_device="cpu"):
        self.out = Path(out)
        self.metadata = json.loads((self.out / "metadata.json").read_text())
        self.meta = pd.read_json(self.out / "companies.json", dtype={"ticker": str})
        self.tickers = self.meta.ticker.tolist()
        self.names = dict(zip(self.meta.ticker, self.meta.name_official))
        self.graphs = json.loads((self.out / "graphs.json").read_text(encoding="utf8"))
        projection = np.load(self.out / "text_projection.npz")
        self.mean, self.components = projection["mean"], projection["components"]
        labels = np.load(self.out / "label_config.npz")
        self.thresholds, self.scales = labels["thresholds"], labels["scales"]
        self.models = []
        for info in self.metadata["gnn"]:
            bundle = torch.load(self.out / f"gat_seed{info['seed']}.pt", map_location="cpu", weights_only=True)
            model = NewsGAT(**bundle["config"]).eval()
            model.load_state_dict(bundle["state_dict"])
            self.models.append(model)
        self.lightgbm = {(task, h): lgb.Booster(model_file=str(self.out / f"lightgbm_{task}_{h}d.txt"))
                         for task in ("direction", "magnitude") for h in (1, 3)}
        self.text = FrozenText(self.out)
        # Use the same high-confidence dictionary extraction as the HDFS training
        # batch; callers do not submit editable model features or scores.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ner"))
        from matcher import CompanyMatcher
        alias_path = self.out / "aliases.csv"
        if not alias_path.exists():
            alias_path = Path(__file__).resolve().parent.parent / "ner/data/aliases.csv"
        self.matcher = CompanyMatcher.from_csv(alias_path)
        self.archive = None
        self.explainer = None
        self.lock = threading.Lock()
        self.explanation_lock = threading.Lock()
        self.explanation_device = explanation_device

    def predict(self, news_id, title, body, published_at, region, horizon=1, model_ver="stage3", top_k=20,
                target_ticker=None, explain=False):
        if explain and not target_ticker:
            raise ValueError("설명 요청에는 target_ticker가 필요합니다.")
        result = self._predict_scores(news_id, title, body, published_at, region, horizon, model_ver, top_k, target_ticker)
        if explain and result["companies"]:
            # A slow language-model request must not hold the score-model lock.
            article, _, routes = self.prepare(news_id, title, body, published_at, region)
            with self.explanation_lock:
                if self.explainer is None:
                    from news_graphrag import GraphExplainer
                    self.explainer = GraphExplainer(self.out, device=self.explanation_device)
                    self.archive = [json.loads(l) for l in (self.out / "articles.jsonl").open(encoding="utf8")]
                item = result["companies"][0]
                explanation = self.explainer.explain(article, item, routes[target_ticker], self.archive)
                item["explanation"] = explanation["explanation"]
                item["grounding"] = explanation
        return result

    @torch.inference_mode()
    def attention(self, news_id, title, body, published_at, region, **unused):
        """Actual last-layer attention, averaged over trained seeds, per head.

        Keep ALL incoming edges of the returned candidates, including self loops,
        so incoming softmax sums remain auditable. Path retrieval remains a
        separate graph search and is never mislabeled as attention attribution.
        """
        with self.lock:
            article, key, routes = self.prepare(news_id, title, body, published_at, region)
            if not routes:
                return {"news_id": news_id, "status": "NO_LINKED_COMPANIES", "nodes": [], "edges": []}
            graph = self.graphs[key]
            sentiment = self.text.sentiment([article], release_after=False)
            text = (self.text.embedding([article]) - self.mean) @ self.components.T
            mentions = mention_array(article["companies"], self.tickers)
            ei, ea = graph_arrays(graph, self.tickers)
            inputs = [torch.from_numpy(a) for a in (text, sentiment, mentions[None], node_features(self.meta, graph), ei, ea)]
            outputs = [model(*inputs, attention=True)[2] for model in self.models]
            edges = outputs[0][0]
            if any(not torch.equal(edges, e) for e, _ in outputs):
                raise ValueError("ensemble attention edge mismatch")
            weights = torch.stack([w for _, w in outputs]).mean(0).numpy()
            rows, nodes = [], set(routes)
            original = graph["edges"]
            for i, (a, b) in enumerate(edges.T.tolist()):
                src, dst = self.tickers[a], self.tickers[b]
                if dst not in routes:
                    continue
                edge = original[i] if i < len(original) else {"type": "self", "reverse": False}
                rows.append({"src": src, "dst": dst, "type": edge["type"], "reverse": edge["reverse"],
                             "heads": weights[i].tolist(), "attention": float(weights[i].mean())})
                nodes.add(src)
            return {"news_id": news_id, "status": "OK", "model_version": self.metadata["model_version"],
                    "graph_as_of": graph["as_of"], "layer": 2, "seed_count": len(self.models),
                    "nodes": [{"ticker": t, "name": self.names[t], "is_direct": t in routes and not routes[t]["path"],
                               "is_candidate": t in routes} for t in sorted(nodes)], "edges": rows,
                    "note": "Incoming attention sums to 1 per target/head; this is a model diagnostic, not causal evidence or signed impact."}

    def prepare(self, news_id, title, body, published_at, region):
        when = availability(published_at, region)
        result = self.matcher.match(title, body)
        mentions = [m for m in result.by_ticker() if m["confidence"] >= .9 and m["ticker"] in self.tickers]
        if result.is_sports:
            mentions = []
        if len(mentions) > 20:
            raise ValueError("기업 20개 초과 나열 기사는 학습 범위 밖입니다.")
        article = {"record_id": news_id, "title": title, "body": body, "published_date": published_at,
                   "available_at": when.isoformat(), "region": region, "companies": mentions}
        keys = [k for k in self.graphs if k.isdigit() and self.graphs[k]["as_of"] <= published_at[:10]
                and pd.Timestamp(self.graphs[k]["as_of"] + "T12:00:00+00:00") <= when]
        if not keys:
            raise ValueError("2017년 이전 기사에 사용할 과거 그래프가 없습니다.")
        key = max(keys)
        return sanitize_article(article), key, expand(mentions, self.graphs[key])

    @torch.inference_mode()
    def _predict_scores(self, news_id, title, body, published_at, region, horizon=1, model_ver="stage3", top_k=20,
                        target_ticker=None):
        if horizon not in (1, 3) or model_ver not in ("stage1", "stage2", "stage3"):
            raise ValueError("unsupported model or horizon")
        with self.lock:
            article, key, routes = self.prepare(news_id, title, body, published_at, region)
            response = {"news_id": news_id, "status": "OK" if routes else "NO_LINKED_COMPANIES",
                "model_ver": model_ver, "model_version": self.metadata["model_version"], "horizon_sessions": horizon,
                "graph_as_of": self.graphs[key]["as_of"], "available_at": article["available_at"],
                "graph_available_at": self.graphs[key]["as_of"] + "T12:00:00+00:00",
                "label_convention": self.metadata.get("label_convention", "first close after availability to +1/+3 closes; market-proxy adjusted"),
                "companies": []}
            if not routes:
                return response
            if target_ticker is not None and target_ticker not in routes:
                raise ValueError("요청 기업이 이 기사의 2홉 후보에 없습니다.")
            sentiment = self.text.sentiment([article], release_after=False)
            mentions = mention_array(article["companies"], self.tickers)
            static = node_features(self.meta, self.graphs[key])
            horizon_idx = (1, 3).index(horizon)
            if model_ver == "stage1":
                # Shared implementation with evaluation guarantees rule parity.
                from train_news_impact import rule_predictions
                rows = pd.DataFrame([{"article": 0, "rule_source": r["source"], "rule_weight": r["weight"],
                                      "rule_sign": r["sign"]} for r in routes.values()])
                probs, mag = rule_predictions([article], rows, sentiment, self.thresholds / self.scales)
                values = {t: (probs[i, horizon_idx], mag[i, horizon_idx]) for i, t in enumerate(routes)}
            else:
                text = (self.text.embedding([article]) - self.mean) @ self.components.T
                if model_ver == "stage2":
                    nodes = np.array([self.tickers.index(t) for t in routes])
                    x = np.concatenate([np.repeat(text, len(nodes), axis=0), np.repeat(sentiment, len(nodes), axis=0),
                                        mentions[nodes], static[nodes], nodes[:, None]], axis=1).astype("float32")
                    p = self.lightgbm["direction", horizon].predict(x)
                    m = np.clip(self.lightgbm["magnitude", horizon].predict(x), 0, 1)
                    values = {t: (p[i], m[i]) for i, t in enumerate(routes)}
                else:
                    edges, attrs = graph_arrays(self.graphs[key], self.tickers)
                    inputs = [torch.from_numpy(a) for a in (text, sentiment, mentions[None], static, edges, attrs)]
                    results = [model(*inputs) for model in self.models]
                    p = torch.stack([r[0].softmax(-1) for r in results]).mean(0)[0, :, horizon_idx].numpy()
                    m = torch.stack([r[1] for r in results]).mean(0)[0, :, horizon_idx].numpy()
                    values = {t: (p[self.tickers.index(t)], m[self.tickers.index(t)]) for t in routes}
            for ticker, (p, magnitude) in values.items():
                route = routes[ticker]
                response["companies"].append({"news_id": news_id, "ticker": ticker, "name": self.names[ticker],
                    "impact_dir": int(np.argmax(p)) - 1, "impact_score": round(float(magnitude), 6),
                    "direction_probabilities": {k: round(float(p[i]), 6) for i, k in enumerate(("negative", "neutral", "positive"))},
                    "is_direct": not route["path"], "path": [{k: e[k] for k in ("src", "dst", "type", "weight", "reverse")} for e in route["path"]],
                    "explanation": None, "model_ver": model_ver})
                response["companies"][-1]["limited_training_history"] = (
                    self.metadata.get("train_pairs_per_ticker", {}).get(ticker, 0) < 100)
            response["companies"].sort(key=lambda r: (-r["impact_score"], r["ticker"]))
            if target_ticker:
                response["companies"] = [r for r in response["companies"] if r["ticker"] == target_ticker]
            else:
                response["companies"] = response["companies"][:top_k]
            return response
