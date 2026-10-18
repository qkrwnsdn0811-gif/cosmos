"""Labelling helpers for the relation classifier: label set, entity markers, label files, pair-level split, τ.

Labels speak of the ordered pair (A < B by company key): SUPPLY_AB means A supplies B, INVEST_AB means A owns or
acquired B. The rule for every label is the one fixed on 2026-10-18 — a relation only when the sentence states it
between the two companies; companies listed as partners of a third party are NONE.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from .evidence import standalone_index
from .fragments import Fragment

LABELS = ("SUPPLY_AB", "SUPPLY_BA", "INVEST_AB", "INVEST_BA", "PARTNER", "COMPETE", "NONE")
RELATION_LABELS = LABELS[:-1]
TAU_GRID = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))
TAU_PRECISION = 0.85
TAU_FALLBACK = 0.95
SPLIT = (0.70, 0.15)        # train, valid; the rest is test
EPS = 1e-9


def marked_text(f: Fragment, swap: bool = False) -> str:
    """Wrap the first standalone A name in '@ … @' and B name in '# … #' (swapped markers when `swap`)."""
    marks = {"a": "#" if swap else "@", "b": "@" if swap else "#"}
    spans = []
    for side, alias in (("a", f.a_alias), ("b", f.b_alias)):
        i = standalone_index(f.text, alias)
        if i != -1:
            spans.append((i, i + len(alias), marks[side]))
    text = f.text
    for start, end, mark in sorted(spans, reverse=True):      # right to left keeps earlier offsets valid
        text = f"{text[:start]}{mark} {text[start:end]} {mark}{text[end:]}"
    return text


def flip_label(label: str) -> str:
    if label.endswith("_AB"):
        return label[:-3] + "_BA"
    if label.endswith("_BA"):
        return label[:-3] + "_AB"
    return label


def write_sample(fragments: list[Fragment], path: Path, names: dict[str, str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for f in fragments:
            out.write(json.dumps({"fragment_id": f.fragment_id, "a": names.get(f.a_key, f.a_alias),
                                  "b": names.get(f.b_key, f.b_alias), "text": f.text}, ensure_ascii=False) + "\n")


def read_labels(path: Path, known_ids: set[str]) -> tuple[dict[str, str], int]:
    """Labels keyed by fragment id, and the number of lines dropped (unknown id, label outside LABELS, bad JSON)."""
    labels, dropped = {}, 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            dropped += 1
            continue
        fid, label = row.get("fragment_id"), row.get("label")
        if fid not in known_ids or label not in LABELS:
            dropped += 1
            continue
        labels[fid] = label
    return labels, dropped


def write_review(labels: dict[str, str], fragments: dict[str, Fragment], path: Path, names: dict[str, str],
                 n: int = 100, seed: int = 0) -> None:
    ids = sorted(fid for fid in labels if fid in fragments)
    chosen = sorted(random.Random(seed).sample(ids, min(n, len(ids))))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["fragment_id", "a", "b", "text", "label", "ok"])
        for fid in chosen:
            f = fragments[fid]
            writer.writerow([fid, names.get(f.a_key, f.a_alias), names.get(f.b_key, f.b_alias), f.text, labels[fid], ""])


def split_by_pair(ids_by_pair: dict[tuple[str, str], list[str]], seed: int = 0) -> tuple[list[str], list[str], list[str]]:
    """Whole company pairs go to train/valid/test (70/15/15 of the sentences) so no pair is seen in two parts."""
    pairs = sorted(ids_by_pair)
    random.Random(seed).shuffle(pairs)
    total = sum(len(v) for v in ids_by_pair.values())
    parts: tuple[list[str], list[str], list[str]] = ([], [], [])
    done = 0
    for pair in pairs:
        share = done / total if total else 1.0
        part = 0 if share < SPLIT[0] else 1 if share < SPLIT[0] + SPLIT[1] else 2
        parts[part].extend(ids_by_pair[pair])
        done += len(ids_by_pair[pair])
    return parts


def choose_threshold(predictions: list[tuple[str, float]], gold: list[str]) -> float:
    """Smallest τ whose accepted relation predictions (not NONE, probability ≥ τ) are ≥ 85% correct; else 0.95."""
    for tau in TAU_GRID:
        accepted = [(label, g) for (label, prob), g in zip(predictions, gold)
                    if label != "NONE" and prob >= tau - EPS]
        if accepted and sum(label == g for label, g in accepted) / len(accepted) >= TAU_PRECISION - EPS:
            return tau
    return TAU_FALLBACK
