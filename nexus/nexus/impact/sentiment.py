"""Article sentiment with KR-FinBERT (the same pinned revision COSMOS uses), scored on CPU.

The model object is injected as a `Scorer`, so tests run with a fake and never import torch. Scores are
cached per (news_id, model revision) in one Parquet file that is rewritten every few batches, so an
interrupted run resumes where it stopped.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pandas as pd

from ..models import Article

MODEL_ID = "snunlp/KR-FinBert-SC"
MODEL_REVISION = "f8586286cc3161fb648e9fee09a456069fd846d0"
COLUMNS = ["news_id", "p_neg", "p_neu", "p_pos", "model_revision"]
PROB_COLUMNS = ["p_neg", "p_neu", "p_pos"]
DOCUMENTED_ORDER = ("negative", "neutral", "positive")   # KR-FinBert-SC: 0=부정, 1=중립, 2=긍정
MAX_LENGTH = 256

Scorer = Callable[[list[str]], list[tuple[float, float, float]]]


def article_text(article: Article) -> str:
    return f"{article.title}\n{article.content}".strip()


def to_neg_neu_pos(probs: Sequence[float], id2label: dict[int, str]) -> tuple[float, float, float]:
    """Reorder by label name when the config names them; otherwise trust the model card's order."""
    names = {i: str(name).lower() for i, name in id2label.items()}
    if set(names.values()) == set(DOCUMENTED_ORDER):
        by_name = {names[i]: float(p) for i, p in enumerate(probs)}
        return tuple(by_name[n] for n in DOCUMENTED_ORDER)
    return float(probs[0]), float(probs[1]), float(probs[2])


def load_finbert(threads: int = 8, device: str | None = None) -> Scorer:
    """Download (first run) and load the pinned model on the GPU when torch sees one, else CPU. Needs torch."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from ..device import padding_for, pick_device
    device = device or pick_device(torch)
    pad = padding_for(device, MAX_LENGTH)

    torch.set_num_threads(threads)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID, revision=MODEL_REVISION,
                                                               trust_remote_code=False).to(device).eval()
    id2label = dict(model.config.id2label)

    def score(texts: list[str]) -> list[tuple[float, float, float]]:
        with torch.no_grad():
            enc = tokenizer(texts, return_tensors="pt", **pad).to(device)
            probs = torch.softmax(model(**enc).logits, dim=-1).tolist()
        return [to_neg_neu_pos(row, id2label) for row in probs]

    return score


def _read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if Path(path).is_file() else pd.DataFrame(columns=COLUMNS)


def _write(frame: pd.DataFrame, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)          # never leave a half-written cache behind


def score_articles(articles: Iterable[Article], scorer: Scorer, out_path: Path, *, batch_size: int = 32,
                   save_every: int = 50, revision: str = MODEL_REVISION,
                   progress: Callable[[int, int], None] | None = None) -> pd.DataFrame:
    existing = _read(out_path)
    kept = existing[existing["model_revision"] == revision]
    done = set(kept["news_id"])
    todo: dict[str, str] = {}
    for a in articles:
        if a.news_id not in done and a.news_id not in todo:
            todo[a.news_id] = article_text(a)
    ids = list(todo)
    rows: list[dict] = []

    def flush() -> pd.DataFrame:
        new = pd.DataFrame(rows, columns=COLUMNS)
        frame = new if kept.empty else (kept if new.empty else pd.concat([kept, new], ignore_index=True))
        _write(frame, out_path)
        return frame

    for b, start in enumerate(range(0, len(ids), batch_size), 1):
        batch = ids[start:start + batch_size]
        for news_id, (neg, neu, pos) in zip(batch, scorer([todo[i] for i in batch])):
            rows.append({"news_id": news_id, "p_neg": neg, "p_neu": neu, "p_pos": pos, "model_revision": revision})
        if b % save_every == 0:
            flush()
            if progress:
                progress(len(rows), len(ids))
    return flush()


def read_sentiment(path: Path, revision: str = MODEL_REVISION) -> pd.DataFrame:
    frame = _read(path)
    frame = frame[frame["model_revision"] == revision]
    return frame.set_index("news_id")[PROB_COLUMNS]
