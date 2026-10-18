"""Pair fragments for the KPF-BERT relation classifier: sentences where both companies appear by name.

A fragment is one hilight sentence for one company pair, with the pair ordered by company key (A < B) so labels
can speak of direction as A→B or B→A. The rule judgement is kept alongside (loose patterns included) to seed the
labelling sample and to compare the model with the rules.
"""
from __future__ import annotations

import hashlib
import random
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from ..models import Company
from .evidence import _first_alias, hilight_fragments, rule_hit

DIRECTED = {"SUPPLY", "INVEST"}


@dataclass(frozen=True)
class Fragment:
    fragment_id: str
    a_key: str
    b_key: str
    a_alias: str
    b_alias: str
    text: str
    news_id: str
    title: str
    provider: str
    published: datetime | None
    link: str
    rule_rel: str | None        # SUPPLY_AB / SUPPLY_BA / INVEST_AB / INVEST_BA / PARTNER / COMPETE, or None
    rule_pattern: str | None


def fragment_id(text: str, a_key: str, b_key: str) -> str:
    return hashlib.sha1(f"{a_key}|{b_key}|{text}".encode("utf-8")).hexdigest()[:16]


def _ab_label(rel: str, src_key: str, a_key: str) -> str:
    if rel in DIRECTED:
        return f"{rel}_AB" if src_key == a_key else f"{rel}_BA"
    return rel


def pair_fragments(docs: list[dict], a: Company, b: Company, aliases_of: Callable[[str], list[str]]) -> list[Fragment]:
    if b.key < a.key:
        a, b = b, a
    a_aliases, b_aliases = aliases_of(a.key), aliases_of(b.key)
    key_of = {alias: a.key for alias in a_aliases} | {alias: b.key for alias in b_aliases}
    out = []
    for doc in docs:
        for text in hilight_fragments(doc.get("hilight", "")):
            x, y = _first_alias(text, a_aliases), _first_alias(text, b_aliases)
            if not x or not y:
                continue
            hit = rule_hit(text, x, y)
            rel = pattern = None
            if hit:
                src_alias, _, raw_rel, _, pattern = hit
                rel = _ab_label(raw_rel, key_of[src_alias], a.key)
            out.append(Fragment(fragment_id(text, a.key, b.key), a.key, b.key, x, y, text, doc["news_id"],
                                doc.get("title", ""), doc.get("provider", ""),
                                datetime.fromisoformat(doc["dateline"]) if doc.get("dateline") else None,
                                doc.get("provider_link_page") or "", rel, pattern))
    return out


def collect_fragments(articles, recognizer, companies: dict[str, Company], client, *, start: date, end: date,
                      today: date, top_pairs: int = 300) -> list[Fragment]:
    """The same searches as build_graph (plain search for the top sentence pairs, relation-word search for every
    pair sharing an article), one fragment per (sentence, pair)."""
    from ..bigkinds.search import pair_hilights, relation_hilights
    from ..pipeline import candidate_pairs, search_terms
    from .comention import sentence_comentions

    terms = search_terms(articles, recognizer, companies)
    seen: dict[str, Fragment] = {}

    def add(docs, key_a, key_b):
        for f in pair_fragments(docs, companies[key_a], companies[key_b], recognizer.aliases_of):
            seen.setdefault(f.fragment_id, f)

    for stat in sentence_comentions(articles, recognizer.key_of_org, min_count=3)[:top_pairs]:
        add(pair_hilights(client, terms[stat.a], terms[stat.b], start=start, end=end, today=today), stat.a, stat.b)
    for key_a, key_b in candidate_pairs(articles, recognizer, companies):
        add(relation_hilights(client, terms[key_a], terms[key_b], start=start, end=end, today=today), key_a, key_b)
    return list(seen.values())


def write_fragments(fragments: list[Fragment], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([asdict(f) for f in fragments], columns=list(Fragment.__dataclass_fields__)).to_parquet(path, index=False)


def _clean(value):
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value


def read_fragments(path: Path) -> list[Fragment]:
    frame = pd.read_parquet(path)
    return [Fragment(**{k: _clean(v) for k, v in row.items()}) for row in frame.to_dict("records")]


def sample_for_labeling(fragments: list[Fragment], size: int = 2000, per_pair: int = 20, seed: int = 0) -> list[Fragment]:
    """Every rule-judged fragment, then a seeded shuffle of the rest capped at `per_pair` per company pair."""
    sample = [f for f in fragments if f.rule_rel]
    per = Counter((f.a_key, f.b_key) for f in sample)
    rest = [f for f in fragments if not f.rule_rel]
    random.Random(seed).shuffle(rest)
    for f in rest:
        if len(sample) >= size:
            break
        pair = (f.a_key, f.b_key)
        if per[pair] >= per_pair:
            continue
        sample.append(f)
        per[pair] += 1
    return sample
