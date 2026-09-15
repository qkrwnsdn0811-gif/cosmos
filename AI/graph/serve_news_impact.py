"""Internal news-conditioned AI API. Start: python serve_news_impact.py --port 8092.

POST /v1/impact/predict: text + date/time -> direction/magnitude/candidate paths.
POST /v1/impact/explain: same input + target_ticker -> identical score, GraphRAG.
Explicit Stage1/2 selection enables fallback without mislabeling it as GNN.
The earlier source-only /v1/impact/rank remains in serve_impact.py on 8091.
"""
from __future__ import annotations
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import torch
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from news_impact_data import OUT, availability
from news_impact_predictor import NewsImpactPredictor


class NewsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    news_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=2000)
    body: str = Field(default="", max_length=50000)
    published_at: str = Field(min_length=10, max_length=40)
    region: Literal["domestic", "overseas"]
    horizon: Literal[1, 3] = 1
    model_ver: Literal["stage1", "stage2", "stage3"] = "stage3"
    top_k: int = Field(default=20, ge=1, le=32)

    @field_validator("horizon", mode="before")
    @classmethod
    def integer_horizon(cls, value):
        if type(value) is not int:
            raise ValueError("horizon must be an integer 1 or 3")
        return value

    @model_validator(mode="after")
    def valid_article(self):
        if not (self.title.strip() or self.body.strip()):
            raise ValueError("기사 제목 또는 본문이 필요합니다.")
        availability(self.published_at, self.region)
        return self


class ExplainRequest(NewsRequest):
    target_ticker: str = Field(min_length=1, max_length=20)


def create_app(out: Path = OUT, explanation_device="cpu"):
    @asynccontextmanager
    async def lifespan(app):
        torch.set_num_threads(4)
        app.state.predictor = NewsImpactPredictor(out, explanation_device=explanation_device)
        yield

    app = FastAPI(title="cosmos news impact GAT", version="2.0.0", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request):
        p = request.app.state.predictor
        return {"status": "ok", "model_version": p.metadata["model_version"],
                "primary_model": "stage3", "company_count": len(p.tickers)}

    @app.post("/v1/impact/predict")
    def predict(body: NewsRequest, request: Request):
        try:
            return request.app.state.predictor.predict(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.post("/v1/impact/explain")
    def explain(body: ExplainRequest, request: Request):
        try:
            return request.app.state.predictor.predict(**body.model_dump(), explain=True)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.post("/v1/impact/attention")
    def attention(body: NewsRequest, request: Request):
        if body.model_ver != "stage3":
            raise HTTPException(422, detail="Attention is available only for stage3")
        try:
            return request.app.state.predictor.attention(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", type=Path, default=OUT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--explanation-device", choices=("cpu", "cuda"), default="cpu")
    args = ap.parse_args()
    uvicorn.run(create_app(args.artifacts, args.explanation_device), host=args.host, port=args.port)
