"""Generate a real archived-news explanation without fabricating model scores.

This validates only retrieval+LLM, independently of training. Full scored API
roundtrip is checked separately after the GAT checkpoints are ready.
"""
import json
import time
import torch
from news_impact_data import OUT, expand
from news_graphrag import GraphExplainer


def main():
    torch.set_num_threads(4)
    docs = [json.loads(l) for l in (OUT / "articles.jsonl").open(encoding="utf8")]
    graphs = json.loads((OUT / "graphs.json").read_text(encoding="utf8"))
    candidates = [r for r in docs if r["region"] == "domestic" and r["published_date"].startswith("2025")
                  and any(m["ticker"] == "005930" and m["first_pos"] == "title" for m in r["companies"])]
    for article in candidates:
        routes = expand(article["companies"], graphs[article["graph"]])
        valid = [(t, route) for t, route in routes.items() if len(route["path"]) == 1
                 and route["path"][0]["type"] in ("PARTNER", "SUPPLY", "COMPETE")]
        if valid:
            break
    else:
        raise RuntimeError("no suitable actual news/path example")
    ticker, route = valid[0]
    start = time.monotonic()
    # The explanation module only consumes ticker/directness, never fake scores.
    explainer = GraphExplainer()
    result = explainer.explain(article, {"ticker": ticker, "is_direct": False}, route, docs)
    output = {"news_id": article["record_id"], "title": article["title"], "ticker": ticker,
              "seconds": time.monotonic() - start, "raw_generation": explainer.last_raw, **result}
    (OUT / "graphrag_smoke.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
