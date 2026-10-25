"""Edges, scores and evidence documents for the galaxy snapshot, per window (spec §5.1).

Two kinds of edges, both from BigKinds:
- evidence relations (SUPPLY/PARTNER/COMPETE) found by the COSMOS rules in pair hilights — score 60..100;
- co-mentions (same tms_ne_stream segment) recounted inside each window — score 0..60, top 5 per company.
Only domestic listings (KOSPI/KOSDAQ) take part, so no edge can lead the company view abroad.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from itertools import combinations
from typing import Callable, Iterable

from ..models import Article, Company, Edge, EvidenceSnippet
from ..relations.comention import sentence_comentions

KST = timezone(timedelta(hours=9))
WINDOWS: dict[str, int | None] = {"30D": 30, "1Y": 365, "10Y": None}
TOP_PER_COMPANY = 5
MIN_COMENTION = 3
MAX_DOCS = 20
DOMESTIC = ("KOSPI", "KOSDAQ")
COMENTION_MAX = 60.0
UNDIRECTED = {"PARTNER", "COMPETE", "CO_MENTIONED"}

KeyOfOrg = Callable[[str], str | None]


@dataclass(frozen=True)
class GalaxyDoc:
    news_id: str
    title: str
    provider: str
    published: datetime
    link: str
    sentence: str | None


@dataclass(frozen=True)
class GalaxyEdge:
    src: str
    dst: str
    rel: str            # SUPPLY | PARTNER | COMPETE | CO_MENTIONED
    source: str         # NEWS | COMENTION | DART (공시 지분, fundamentals/ownership.py)
    window: str
    score: float
    confidence: float | None
    docs: tuple[GalaxyDoc, ...]


def as_of_for(articles: Iterable[Article]) -> datetime:
    last = max(a.dateline.astimezone(KST).date() for a in articles)
    return datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=KST)


def in_window(when: datetime, as_of: datetime, days: int | None) -> bool:
    if days is None:
        return True
    return as_of - timedelta(days=days) <= when < as_of


def _domestic(companies: dict[str, Company], key: str) -> bool:
    c = companies.get(key)
    return c is not None and c.market in DOMESTIC


def _segment_pairs(article: Article, key_of_org: KeyOfOrg) -> set[tuple[str, str]]:
    """Same pairing rule as sentence_comentions: OG entities sharing one segment, keys sorted."""
    pairs = set()
    for segment in article.segments:
        keys = sorted({k for name, kind in segment if kind == "OG" and (k := key_of_org(name))})
        pairs.update(combinations(keys, 2))
    return pairs


def _top_pairs(stats, companies: dict[str, Company]) -> dict[tuple[str, str], int]:
    """Union of each company's TOP_PER_COMPANY strongest eligible pairs → {pair: n_ab}."""
    eligible = [s for s in stats if s.npmi > 0 and _domestic(companies, s.a) and _domestic(companies, s.b)]
    by_company: dict[str, list] = {}
    for s in eligible:
        by_company.setdefault(s.a, []).append((s, s.b))
        by_company.setdefault(s.b, []).append((s, s.a))
    chosen: dict[tuple[str, str], int] = {}
    for items in by_company.values():
        items.sort(key=lambda item: (-item[0].n_ab, -item[0].npmi, item[1]))
        for s, _ in items[:TOP_PER_COMPANY]:
            chosen[(s.a, s.b)] = s.n_ab
    return chosen


def comention_edges(articles: list[Article], key_of_org: KeyOfOrg, companies: dict[str, Company],
                    as_of: datetime) -> list[GalaxyEdge]:
    edges = []
    for window, days in WINDOWS.items():
        subset = [a for a in articles if in_window(a.dateline, as_of, days)]
        chosen = _top_pairs(sentence_comentions(subset, key_of_org, min_count=MIN_COMENTION), companies)
        if not chosen:
            continue
        docs: dict[tuple[str, str], list[Article]] = {pair: [] for pair in chosen}
        for a in subset:
            for pair in _segment_pairs(a, key_of_org) & chosen.keys():
                docs[pair].append(a)
        n_max = max(chosen.values())
        for (src, dst), n in sorted(chosen.items()):
            recent = sorted(docs[(src, dst)], key=lambda a: a.dateline, reverse=True)[:MAX_DOCS]
            edges.append(GalaxyEdge(
                src, dst, "CO_MENTIONED", "COMENTION", window,
                round(COMENTION_MAX * math.log1p(n) / math.log1p(n_max), 6), None,
                tuple(GalaxyDoc(a.news_id, a.title, a.provider, a.dateline, a.link, None) for a in recent)))
    return edges


def _matches(edge: Edge, s: EvidenceSnippet) -> bool:
    if s.rel != edge.rel:
        return False
    if edge.rel in UNDIRECTED:
        return {s.src, s.dst} == {edge.src, edge.dst}
    return (s.src, s.dst) == (edge.src, edge.dst)


def evidence_edges(edges: list[Edge], snippets: list[EvidenceSnippet], companies: dict[str, Company],
                   as_of: datetime) -> list[GalaxyEdge]:
    out = []
    for e in edges:
        if e.source != "NEWS" or not (_domestic(companies, e.src) and _domestic(companies, e.dst)):
            continue
        src, dst = (min(e.src, e.dst), max(e.src, e.dst)) if e.rel in UNDIRECTED else (e.src, e.dst)
        mine = [s for s in snippets if _matches(e, s)]
        for window, days in WINDOWS.items():
            inside = sorted((s for s in mine if in_window(s.published, as_of, days)),
                            key=lambda s: s.published, reverse=True)
            if not inside:
                continue
            docs: dict[str, GalaxyDoc] = {}
            for s in inside:              # newest first: one document per article, its latest sentence
                docs.setdefault(s.news_id, GalaxyDoc(s.news_id, s.title, s.provider, s.published, s.link, s.snippet))
            out.append(GalaxyEdge(src, dst, e.rel, "NEWS", window, float(60 + 10 * min(len(inside), 4)),
                                  max(s.confidence for s in inside), tuple(docs.values())))
    return out


def build_galaxy(chains: list[tuple[list[Article], dict[str, Company], KeyOfOrg, list[Edge], list[EvidenceSnippet]]]
                 ) -> tuple[datetime, list[GalaxyEdge]]:
    """One snapshot for every analyzed value chain; a pair seen in two chains keeps its higher score."""
    as_of = as_of_for(a for articles, *_ in chains for a in articles)
    best: dict[tuple[str, str, str, str], GalaxyEdge] = {}
    for articles, companies, key_of_org, edges, snippets in chains:
        for g in comention_edges(articles, key_of_org, companies, as_of) + evidence_edges(edges, snippets, companies, as_of):
            k = (g.src, g.dst, g.rel, g.window)
            if k not in best or g.score > best[k].score:
                best[k] = g
    return as_of, list(best.values())
