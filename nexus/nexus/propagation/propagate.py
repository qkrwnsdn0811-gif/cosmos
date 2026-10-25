"""Rule-based inference: spread an event from its reported companies over the base graph.

Inferred companies never carry documents of their own; their only support is the path of
relations back to a reported company. A path made only of co-mentions is not a relation,
so such companies are always held.
"""
from __future__ import annotations

from ..models import CompanyMention, Impact, PathStep
from ..relations.graph import BaseGraph

MAX_HOPS = 2
SEED_MIN_SALIENCE = 0.8   # only companies named in the title or the 200-char body spread an event
HUB_DEGREE = 8            # spreading from a node with more relations is damped by sqrt(HUB_DEGREE / degree)
OK_THRESHOLD = 0.25
HOLD_THRESHOLD = 0.15
MAX_OK = 6                # the strongest few stay OK; the rest are held as lower priority
MAX_INFERRED = 12
HOLD_LOW = "전이 강도 낮음"
HOLD_COMENTION_ONLY = "동시언급만으로 연결"
HOLD_RANK = "우선순위 밖"

Candidate = tuple[float, tuple[PathStep, ...]]


def _better(new: Candidate, old: Candidate | None) -> bool:
    if old is None:
        return True
    return (new[0], -len(new[1])) > (old[0], -len(old[1]))


def propagate(mentions: list[CompanyMention], graph: BaseGraph, *, active_rels: set[str] | None = None) -> list[Impact]:
    """All mentions are REPORTED; only salient ones seed the spread, along `active_rels` (None = every type)."""
    reported = {m.key for m in mentions}
    seeds = {m.key: m.salience for m in mentions if m.salience >= SEED_MIN_SALIENCE}
    relational: dict[str, Candidate] = {}
    comention_only: dict[str, Candidate] = {}

    def offer(node: str, score: float, path: tuple[PathStep, ...]):
        table = relational if any(s.rel != "CO_MENTIONED" for s in path) else comention_only
        if _better((score, path), table.get(node)):
            table[node] = (score, path)

    def walk(node: str, score: float, path: tuple[PathStep, ...], visited: frozenset[str]):
        if len(path) == MAX_HOPS:
            return
        damping = min(1.0, (HUB_DEGREE / graph.degree(node)) ** 0.5) if graph.degree(node) else 1.0
        for nxt, edge in graph.neighbors(node):
            if nxt in reported or nxt in visited:
                continue
            if active_rels is not None and edge.rel not in active_rels:
                continue
            step_path = path + (PathStep(edge.src, edge.dst, edge.rel, edge.source, edge.strength),)
            step_score = score * graph.weight(edge) * damping
            offer(nxt, step_score, step_path)
            walk(nxt, step_score, step_path, visited | {nxt})

    for key, salience in seeds.items():
        walk(key, salience, (), frozenset({key}))

    inferred = []
    for node in set(relational) | set(comention_only):
        rel = relational.get(node)
        if rel and rel[0] >= HOLD_THRESHOLD:
            score, path = rel
            ok = score >= OK_THRESHOLD
            inferred.append(Impact(node, "INFERRED", len(path), path, score,
                                   "OK" if ok else "HOLD", None if ok else HOLD_LOW))
            continue
        co = comention_only.get(node)
        if co and co[0] >= HOLD_THRESHOLD:
            inferred.append(Impact(node, "INFERRED", len(co[1]), co[1], co[0], "HOLD", HOLD_COMENTION_ONLY))
    inferred.sort(key=lambda i: (-i.score, i.key))
    ranked, ok_count = [], 0
    for impact in inferred[:MAX_INFERRED]:
        if impact.status == "OK":
            ok_count += 1
            if ok_count > MAX_OK:
                impact = Impact(impact.key, impact.basis, impact.hop, impact.path, impact.score, "HOLD", HOLD_RANK)
        ranked.append(impact)

    reported_impacts = [Impact(m.key, "REPORTED", 0, (), m.salience, "OK", None)
                        for m in sorted(mentions, key=lambda m: (-m.salience, m.key))]
    return reported_impacts + ranked
