"""Rule-based inference: spread an event from its reported companies over the base graph.

Inferred companies never carry documents of their own; their only support is the path of
relations back to a reported company. A path made only of co-mentions is not a relation,
so such companies are always held.
"""
from __future__ import annotations

from ..models import CompanyMention, Impact, PathStep
from ..relations.graph import BaseGraph

MAX_HOPS = 2
OK_THRESHOLD = 0.30
HOLD_THRESHOLD = 0.15
MAX_INFERRED = 20
HOLD_LOW = "전이 강도 낮음"
HOLD_COMENTION_ONLY = "동시언급만으로 연결"

Candidate = tuple[float, tuple[PathStep, ...]]


def _better(new: Candidate, old: Candidate | None) -> bool:
    if old is None:
        return True
    return (new[0], -len(new[1])) > (old[0], -len(old[1]))


def propagate(mentions: list[CompanyMention], graph: BaseGraph) -> list[Impact]:
    seeds = {m.key: m.salience for m in mentions}
    relational: dict[str, Candidate] = {}
    comention_only: dict[str, Candidate] = {}

    def offer(node: str, score: float, path: tuple[PathStep, ...]):
        table = relational if any(s.rel != "CO_MENTIONED" for s in path) else comention_only
        if _better((score, path), table.get(node)):
            table[node] = (score, path)

    def walk(node: str, score: float, path: tuple[PathStep, ...], visited: frozenset[str]):
        if len(path) == MAX_HOPS:
            return
        for nxt, edge in graph.neighbors(node):
            if nxt in seeds or nxt in visited:
                continue
            step_path = path + (PathStep(edge.src, edge.dst, edge.rel),)
            step_score = score * graph.weight(edge)
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

    reported = [Impact(m.key, "REPORTED", 0, (), m.salience, "OK", None)
                for m in sorted(mentions, key=lambda m: (-m.salience, m.key))]
    return reported + inferred[:MAX_INFERRED]
