"""Import COSMOS modules in place (not copied) so pre-hackathon code stays visibly reused."""
from __future__ import annotations

import importlib
import sys

from .config import REPO_ROOT

_PATHS = (REPO_ROOT / "AI" / "ner", REPO_ROOT / "AI" / "graph")


def _ensure_paths() -> None:
    for path in _PATHS:
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def import_matcher():
    """AI/ner/matcher.py — dictionary company matcher (CompanyMatcher, Alias)."""
    _ensure_paths()
    return importlib.import_module("matcher")


def import_relations():
    """AI/graph/extract_relations.py — rule-based relation classifier (classify)."""
    _ensure_paths()
    return importlib.import_module("extract_relations")
