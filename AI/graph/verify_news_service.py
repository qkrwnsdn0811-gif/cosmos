"""Finished-model acceptance: CPU reload parity, article sensitivity, real HTTP.

Uses ORIGINAL sampled articles for the extraction/serving roundtrip, not edited
or synthesized evaluation features. Synthetic text is used only for invariance
and sensitivity assertions and is labeled as such. Starts a private localhost
Uvicorn child on a free port, tests both endpoints, then always stops that child.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import numpy as np
import pandas as pd
import torch

from news_impact_data import HERE, OUT, mention_array, node_features, graph_arrays
from news_impact_predictor import NewsImpactPredictor


def request_for(article, **extra):
    return dict(news_id=article["record_id"], title=article.get("title") or "", body=article.get("body") or "",
                published_at=article["published_date"], region=article["region"], **extra)


def main(out=OUT, raw_news=None, explanation_device="cpu", model_only=False):
    OUT = Path(out).resolve()
    torch.set_num_threads(4)
    raw = {r["record_id"]: r for r in (json.loads(l) for l in (raw_news or OUT / "raw_news.jsonl").open(encoding="utf8"))}
    docs = [json.loads(l) for l in (OUT / "articles.jsonl").open(encoding="utf8")]
    pair = pd.read_parquet(OUT / "pairs.parquet")
    smoke = json.loads((OUT / "graphrag_smoke.json").read_text(encoding="utf8"))
    samples = [smoke["news_id"]]
    samples.append(next(r["record_id"] for r in docs if r["published_date"].startswith("2025")
                        and r["region"] == "overseas" and any(m["ticker"] == "AAPL" for m in r["companies"])))
    predictor = NewsImpactPredictor(OUT, explanation_device=explanation_device)
    report = {"model_version": predictor.metadata["model_version"], "roundtrips": []}
    saved = {s: np.load(OUT / f"predictions_{s}.npz") for s in ("stage1", "stage2", "stage3")}
    for news_id in samples:
        article_idx = next(i for i, r in enumerate(docs) if r["record_id"] == news_id)
        for stage in ("stage1", "stage2", "stage3"):
            for h, horizon in enumerate((1, 3)):
                result = predictor.predict(**request_for(raw[news_id], model_ver=stage, horizon=horizon, top_k=32))
                by_ticker = {r["ticker"]: r for r in result["companies"]}
                selected = pair[pair.article == article_idx]
                max_error = 0.
                for index, row in selected.iterrows():
                    item = by_ticker[row.ticker]
                    p = np.array([item["direction_probabilities"][key] for key in ("negative", "neutral", "positive")])
                    max_error = max(max_error, float(abs(p - saved[stage]["probabilities"][index, h]).max()),
                                    abs(item["impact_score"] - saved[stage]["magnitude"][index, h]))
                assert max_error < 2e-5, (news_id, stage, horizon, max_error)
                report["roundtrips"].append(dict(news_id=news_id, stage=stage, horizon=horizon, pairs=len(selected), max_error=float(max_error)))

    # Same company, different hypothetical article contents must change output.
    common = dict(news_id="synthetic-sensitivity", published_at="2026-09-14", region="domestic", target_ticker="005930")
    positive = predictor.predict(**common, title="삼성전자, 영업이익 급증", body="삼성전자는 대규모 공급 계약을 체결하고 최대 실적을 달성했다.")
    negative = predictor.predict(**common, title="삼성전자, 대규모 영업손실", body="삼성전자는 공급 계약 취소와 대규모 적자로 위기에 처했다.")
    a, b = positive["companies"][0], negative["companies"][0]
    delta = max(abs(a["impact_score"] - b["impact_score"]), max(abs(a["direction_probabilities"][k] - b["direction_probabilities"][k]) for k in a["direction_probabilities"]))
    assert delta > 1e-5, "trained model ignores news content"
    report["synthetic_sensitivity"] = {"max_output_delta": delta, "positive_text_result": a, "negative_text_result": b,
                                       "note": "input sensitivity test, not measured forecasting accuracy"}
    widget1 = predictor.predict(**common, title="삼성전자 실적 발표", body="삼성전자(251,250원 ▼9,750 -3.74%)는 매출 100억원을 기록했다.")
    widget2 = predictor.predict(**common, title="삼성전자 실적 발표", body="삼성전자(10,000원 ▲500 +5.0%)는 매출 100억원을 기록했다.")
    assert widget1["companies"] == widget2["companies"], "crawl-time widget changed prediction"
    report["live_quote_invariance"] = True

    # Export actual per-edge attention for diagnosis, without calling it evidence.
    art, key, _ = predictor.prepare(**{k: v for k, v in request_for(raw[samples[0]]).items()})
    emb = (predictor.text.embedding([art]) - predictor.mean) @ predictor.components.T
    sent = predictor.text.sentiment([art], release_after=False)
    mentions = mention_array(art["companies"], predictor.tickers)
    static = node_features(predictor.meta, predictor.graphs[key])
    ei, ea = graph_arrays(predictor.graphs[key], predictor.tickers)
    with torch.inference_mode():
        _, _, (attention_edges, attention_weights) = predictor.models[0](
            *[torch.from_numpy(x) for x in (emb, sent, mentions[None], static, ei, ea)], attention=True)
    np.savez(OUT / "attention_example.npz", edge_index=attention_edges.numpy(), weights=attention_weights.numpy())
    report["attention"] = {"news_id": samples[0], "graph": key, "heads": attention_weights.shape[1],
                           "note": "model diagnostic; not a causal explanation"}
    if model_only:
        att = predictor.attention(**request_for(raw[samples[0]]))
        totals = {}
        for edge in att["edges"]:
            totals.setdefault(edge["dst"], np.zeros(4))[:] += np.asarray(edge["heads"])
        assert all(np.allclose(v, 1, atol=1e-5) for v in totals.values())
        report["attention_check"] = {"targets": len(totals), "incoming_heads_sum_to_one": True}
        payload = request_for(raw[samples[0]], target_ticker=smoke["ticker"])
        before = predictor.predict(**payload)["companies"][0]
        start = time.monotonic()
        after = predictor.predict(**payload, explain=True)["companies"][0]
        assert before["impact_score"] == after["impact_score"] and before["impact_dir"] == after["impact_dir"]
        assert after["grounding"]["status"] in ("GENERATED", "EXTRACTIVE_FALLBACK"), after["grounding"]
        report["grounding_check"] = {"seconds": time.monotonic() - start,
            "score_unchanged_by_explanation": True, "example": after}
        report["service_connected"] = False
        (OUT / "model_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        (OUT / "attention_example.json").write_text(json.dumps(att, ensure_ascii=False), encoding="utf8")
        print("model-only verification passed", len(report["roundtrips"]), "roundtrips; no server started", flush=True)
        return

    # Release local encoders before starting the independent server instance.
    predictor.text.release()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    log = (OUT / "http_smoke_server.log").open("w", encoding="utf8")
    process = subprocess.Popen([sys.executable, str(HERE / "serve_news_impact.py"), "--port", str(port),
                                "--artifacts", str(OUT), "--explanation-device", explanation_device],
                               env=env, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180) as client:
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError("HTTP server failed to start; inspect log")
                try:
                    response = client.get("/health")
                    if response.status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(.5)
            else:
                raise TimeoutError("server startup")
            payload = request_for(raw[samples[0]])
            timings = []
            for i in range(6):
                start = time.monotonic()
                result = client.post("/v1/impact/predict", json=payload)
                result.raise_for_status()
                timings.append(time.monotonic() - start)
            assert client.post("/v1/impact/predict", json={**payload, "impact_score": 1}).status_code == 422
            att = client.post("/v1/impact/attention", json=payload)
            att.raise_for_status()
            totals = {}
            for edge in att.json()["edges"]:
                totals.setdefault(edge["dst"], np.zeros(4))[:] += np.asarray(edge["heads"])
            assert all(np.allclose(v, 1, atol=1e-5) for v in totals.values())
            report["attention_endpoint"] = {"targets": len(totals), "incoming_heads_sum_to_one": True}
            scored = client.post("/v1/impact/predict", json={**payload, "top_k": 32}).json()
            before = next(r for r in scored["companies"] if r["ticker"] == smoke["ticker"])
            start = time.monotonic()
            explained = client.post("/v1/impact/explain", json={**payload, "target_ticker": smoke["ticker"]})
            explained.raise_for_status()
            elapsed = time.monotonic() - start
            after = explained.json()["companies"][0]
            assert after["impact_dir"] == before["impact_dir"] and after["impact_score"] == before["impact_score"]
            assert after["grounding"]["status"] == "GENERATED", after["grounding"]
            report["http"] = {"prediction_seconds": timings, "warm_median_seconds": float(np.median(timings[1:])),
                              "explanation_seconds": elapsed, "score_unchanged_by_explanation": True,
                              "example": explained.json()}
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        log.close()
    (OUT / "service_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("synthetic_sensitivity", "http")}, ensure_ascii=False, indent=2), flush=True)
    print("HTTP warm median", report["http"]["warm_median_seconds"], "explanation", report["http"]["explanation_seconds"], flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--raw-news", type=Path)
    ap.add_argument("--explanation-device", default="cpu", choices=("cpu", "cuda"))
    ap.add_argument("--model-only", action="store_true")
    args = ap.parse_args()
    main(args.out, args.raw_news, args.explanation_device, args.model_only)
