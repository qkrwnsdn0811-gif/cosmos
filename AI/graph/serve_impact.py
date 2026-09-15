"""내부 서비스용 GNN 순위 API. 실행: python serve_impact.py --port 8091.

시작 시 체크포인트를 로드하고 CPU 에서 전체 소스 점수를 계산한다. 모델이 없거나 불량이면
시작이 실패한다. 요청 처리에는 가격 파일·GPU·DB 접근이 없다. 서버 간 호출용이며 외부
공개·인증·TLS 는 기존 백엔드/인프라 경계에서 처리한다. 기본 바인딩은 localhost 다.
"""
from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from impact_ranker import DEFAULT_BUNDLE, ImpactRanker


class RankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sourceTicker: str = Field(min_length=1, max_length=20)
    targetMarket: str = Field(pattern="^(KOSPI|NASDAQ)$")
    topK: int = Field(default=10, ge=1, le=100)


def create_app(bundle_path: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        torch.set_num_threads(2)
        path = bundle_path or Path(os.environ.get("IMPACT_MODEL_PATH", str(DEFAULT_BUNDLE)))
        app.state.ranker = ImpactRanker(path)
        yield

    app = FastAPI(title="cosmos GNN impact ranking", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request):
        ranker = request.app.state.ranker
        return {"status": "ok", "modelVersion": ranker.model_id,
                "companyCount": len(ranker.tickers), "graphAsOf": ranker.metadata["graph_as_of"]}

    @app.post("/v1/impact/rank")
    def rank(body: RankRequest, request: Request):
        try:
            return request.app.state.ranker.rank(body.sourceTicker, body.targetMarket, body.topK)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()


def main():
    import uvicorn
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8091)
    args = ap.parse_args()
    uvicorn.run(create_app(args.bundle), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
