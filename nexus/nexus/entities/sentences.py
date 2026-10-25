"""News-feed evidence sentences: the title and 200-char body sentences that name a company the article mentions.

The COSMOS news screen counts these as 대표 근거 문장 and averages their confidence as 분석 신뢰도. BigKinds gives only
the title and the first 200 characters, so a company found only in the full-body entities (orgs) has no sentence.
Each sentence is stored once, under the most salient company it names, so the screen never lists it twice; the
confidence still counts every sentence that names the company, by the COSMOS rule (AI/graph/
fill_company_document_scores.py): min(1, 0.40 + 0.15 × sentences).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from ..models import EventBundle

_END = re.compile(r"(?<=[.?!。…])\s+|\n+")
_TERMINAL = (".", "?", "!", "。", "…", "다", "\"", "'", "”", "’", ")")


def split_sentences(text: str) -> list[str]:
    """Sentences in order; a last piece cut off by the 200-char limit gets '…' so it does not read as complete."""
    parts = [p.strip() for p in _END.split(text or "") if p and p.strip()]
    if parts and not parts[-1].endswith(_TERMINAL):
        parts[-1] += "…"
    return parts


def confidence_for(sentences: int) -> float:
    return min(1.0, 0.40 + 0.15 * sentences)


@dataclass(frozen=True)
class NewsEvidence:
    news_id: str
    company_key: str
    order: int          # 0 = title, then body sentences in order
    sentence: str
    confidence: float


def news_evidence(bundle: EventBundle, keys_in: Callable[[str], set[str]]) -> tuple[list[NewsEvidence], dict[str, float]]:
    """(evidence rows, company key → confidence) for one article. keys_in: sentence → company keys it names."""
    salience = {m.key: m.salience for m in bundle.mentions}
    a = bundle.article
    texts = [a.title] + split_sentences(a.content) if a.title else split_sentences(a.content)
    picked: list[tuple[int, str, list[str]]] = []
    counts: dict[str, int] = {}
    for order, sentence in enumerate(texts):
        keys = sorted(k for k in keys_in(sentence) if k in salience)
        if not keys:
            continue
        picked.append((order, sentence, keys))
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
    confidence = {k: round(confidence_for(n), 6) for k, n in counts.items()}
    rows = []
    for order, sentence, keys in picked:
        owner = min(keys, key=lambda k: (-salience[k], k))
        rows.append(NewsEvidence(a.news_id, owner, order, sentence, confidence[owner]))
    return rows, confidence
