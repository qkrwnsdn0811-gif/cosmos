"""Base company graph: COSMOS relations + new news evidence + same-sentence co-mention."""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from ..models import Edge, EvidenceSnippet
from .comention import PairStat

TYPE_WEIGHT = {"SUPPLY": 0.8, "INVEST": 0.6, "COMPETE": 0.5, "PARTNER": 0.5, "CO_MENTIONED": 0.4}
DIRECTED = {"SUPPLY", "INVEST"}


def load_cosmos_edges(csv_path: Path, keys: set[str]) -> list[Edge]:
    edges = []
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            src = f"{row['source_market']}:{row['source_stock_code']}"
            dst = f"{row['target_market']}:{row['target_stock_code']}"
            rel = row["relationship_type_code"]
            if src in keys and dst in keys and rel in TYPE_WEIGHT:
                edges.append(Edge(src, dst, rel, 1.0, rel in DIRECTED, "COSMOS"))
    return edges


def news_edges(snippets: list[EvidenceSnippet], *, min_strength: float = 0.5) -> list[Edge]:
    """Noisy-OR of rule confidences per (src, dst, rel); undirected pairs are merged."""
    miss = defaultdict(lambda: 1.0)
    for s in snippets:
        src, dst = (s.src, s.dst) if s.rel in DIRECTED else tuple(sorted((s.src, s.dst)))
        miss[(src, dst, s.rel)] *= 1.0 - s.confidence
    edges = []
    for (src, dst, rel), m in sorted(miss.items()):
        strength = 1.0 - m
        if strength >= min_strength - 1e-12:
            edges.append(Edge(src, dst, rel, strength, rel in DIRECTED, "NEWS"))
    return edges


def comention_edges(stats: list[PairStat]) -> list[Edge]:
    return [Edge(s.a, s.b, "CO_MENTIONED", s.npmi, False, "COMENTION") for s in stats if s.npmi > 0]


class BaseGraph:
    def __init__(self, edges: list[Edge]):
        self.edges = list(edges)
        self._adjacent: dict[str, list[tuple[str, Edge]]] = defaultdict(list)
        for e in self.edges:
            self._adjacent[e.src].append((e.dst, e))
            self._adjacent[e.dst].append((e.src, e))

    def neighbors(self, key: str) -> list[tuple[str, Edge]]:
        """Both directions: shocks travel supplier→customer and customer→supplier."""
        return list(self._adjacent.get(key, ()))

    @staticmethod
    def weight(edge: Edge) -> float:
        return TYPE_WEIGHT[edge.rel] * edge.strength

    def __len__(self) -> int:
        return len(self.edges)
