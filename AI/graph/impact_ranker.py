"""U3 GNN 추론: 뉴스의 소스 기업 -> 같은 대상 시장의 관계 이웃 영향 크기 순위.

가격 이벤트는 학습 라벨을 만드는 데만 쓴다. 입력은 소스 one-hot(값 1) 하나이며 학습과
서빙이 같다. 뉴스 문장 의미·호재/악재·미래 수익률을 추정하는 모델은 아니다.
GNN 출력은 순위용 logit 이다. rankScore 는 후보 안 softmax 비중(0~100)으로, 확률이나
수익률이 아니고 다른 소스/시장 요청끼리 비교하지 않는다. 관계는 학습 시점 스냅샷이다.

체크포인트에는 가중치뿐 아니라 특징 순서·엣지·종목·관계 마스크를 저장한다. 서버 시작 시
동일한 GNN 으로 202개 소스의 점수를 미리 계산한다. 요청마다 가격이나 원본 CSV 는 불필요하다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from train_impact_gnn import ImpactGNN

DEFAULT_BUNDLE = Path(__file__).resolve().parent / "artifacts" / "impact_u3" / "model.pt"
MARKETS = ("KOSPI", "NASDAQ")


def infer_sources(model, src, dst, features, norm, n_nodes, batch=32):
    """소스마다 한 기업의 단위 입력으로 추론해 W[source, target] 을 반환한다."""
    model.eval()
    outputs = []
    with torch.inference_mode():
        for first in range(0, n_nodes, batch):
            ids = torch.arange(first, min(first + batch, n_nodes), device=features.device)
            h = torch.zeros((len(ids), n_nodes), device=features.device)
            h[torch.arange(len(ids), device=features.device), ids] = 1.0
            outputs.append(model(h, src, dst, features, norm).cpu())
    return torch.cat(outputs).numpy()


class ImpactRanker:
    def __init__(self, bundle_path: Path = DEFAULT_BUNDLE):
        self.model_id = hashlib.sha256(bundle_path.read_bytes()).hexdigest()[:16]
        bundle = torch.load(bundle_path, map_location="cpu", weights_only=True)
        if bundle.get("schema_version") != 1 or bundle.get("objective") != "U3_UNIT_SOURCE":
            raise ValueError("지원하지 않는 영향도 모델 형식입니다")
        self.metadata = bundle["metadata"]
        self.tickers = bundle["tickers"]
        self.markets = bundle["markets"]
        self.names = bundle["names"]
        self.train_days = bundle["train_days"]
        self.pos = {ticker: i for i, ticker in enumerate(self.tickers)}
        self.neighbors = bundle["neighbors"].numpy().astype(bool)
        self.relations = bundle["relations"]
        scores = []
        for state in bundle["states"]:
            model = ImpactGNN(**bundle["model_config"])
            model.load_state_dict(state, strict=True)
            scores.append(infer_sources(model, bundle["src"], bundle["dst"],
                                        bundle["features"], bundle["norm"], len(self.tickers)))
        self.scores = np.mean(scores, axis=0)
        if self.scores.shape != self.neighbors.shape or not np.isfinite(self.scores).all():
            raise ValueError("모델 출력이 유효하지 않습니다")

    def rank(self, source_ticker: str, target_market: str, top_k: int = 10) -> dict:
        if source_ticker not in self.pos:
            raise ValueError(f"지원하지 않는 종목 코드: {source_ticker}")
        if target_market not in MARKETS:
            raise ValueError("targetMarket 은 KOSPI 또는 NASDAQ 이어야 합니다")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("topK 는 1~100 정수여야 합니다")
        source = self.pos[source_ticker]
        candidates = np.flatnonzero(self.neighbors[source] & (np.array(self.markets) == target_market))
        candidates = candidates[candidates != source]
        result = {"modelVersion": self.model_id, "objective": "RELATED_COMPANY_IMPACT_RANKING",
                  "sourceTicker": source_ticker, "targetMarket": target_market,
                  "graphAsOf": self.metadata["graph_as_of"], "candidateCount": len(candidates),
                  "scoreMeaning": "relative_rank_weight_not_probability", "companies": []}
        if len(candidates) == 0:
            return {**result, "status": "NO_RELATION_CANDIDATES"}
        raw = self.scores[source, candidates].astype(float)
        if len(raw) > 1 and np.ptp(raw) < 1e-8:
            return {**result, "status": "NO_RANKING_SIGNAL"}
        weights = np.exp(raw - raw.max())
        weights = weights / weights.sum() * 100
        order = sorted(range(len(candidates)), key=lambda i: (-raw[i], self.tickers[candidates[i]]))
        for rank, i in enumerate(order[:top_k], 1):
            j = candidates[i]
            result["companies"].append({"rank": rank, "ticker": self.tickers[j],
                "market": self.markets[j], "name": self.names[j],
                "rankScore": round(float(weights[i]), 6), "rawScore": float(raw[i]),
                "limitedPriceHistory": self.train_days[j] < 120 or self.train_days[source] < 120,
                "relationshipTypes": self.relations.get(f"{source}:{j}", [])})
        return {**result, "status": "OK"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    ap.add_argument("--source", required=True, help="예: 005930 또는 NVDA")
    ap.add_argument("--market", choices=MARKETS, required=True)
    ap.add_argument("--top-k", type=int, default=10)
    args = ap.parse_args()
    torch.set_num_threads(2)
    result = ImpactRanker(args.bundle).rank(args.source, args.market, args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
