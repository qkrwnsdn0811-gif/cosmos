"""Same-segment company co-mention from tms_ne_stream, scored with normalized PMI."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Callable

from ..models import Article


@dataclass(frozen=True)
class PairStat:
    a: str          # a < b
    b: str
    n_ab: int       # articles with a and b in one segment
    n_a: int        # articles mentioning a anywhere in the segments
    n_b: int
    n_docs: int
    npmi: float


def _npmi(n_ab: int, n_a: int, n_b: int, n_docs: int) -> float:
    if n_ab == n_docs:
        return 1.0
    return math.log(n_ab * n_docs / (n_a * n_b)) / -math.log(n_ab / n_docs)


def sentence_comentions(articles: list[Article], key_of_org: Callable[[str], str | None], *,
                        min_count: int = 3) -> list[PairStat]:
    single, pair = Counter(), Counter()
    for article in articles:
        keys_in_article, pairs_in_article = set(), set()
        for segment in article.segments:
            keys = sorted({k for name, kind in segment if kind == "OG" and (k := key_of_org(name))})
            keys_in_article.update(keys)
            pairs_in_article.update(combinations(keys, 2))
        single.update(keys_in_article)
        pair.update(pairs_in_article)
    n_docs = len(articles)
    stats = [PairStat(a, b, n, single[a], single[b], n_docs, _npmi(n, single[a], single[b], n_docs))
             for (a, b), n in pair.items() if n >= min_count]
    return sorted(stats, key=lambda s: (-s.n_ab, s.a, s.b))
