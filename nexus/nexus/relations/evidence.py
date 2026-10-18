"""Pair hilight fragments → typed relation evidence via the COSMOS rule classifier."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from ..cosmos_reuse import import_relations
from ..models import Company, EvidenceSnippet
from .invest import classify_invest

TAG_RE = re.compile(r"</?b>")
GAP_RE = re.compile(r"\.{2,}")          # BigKinds joins hilight pieces with ".."
SENTENCE_RE = re.compile(r"(?<=다\.)\s*")
MAX_SNIPPET = 300


def hilight_fragments(hilight: str) -> list[str]:
    fragments = []
    for piece in GAP_RE.split(TAG_RE.sub("", hilight or "")):
        fragments.extend(s.strip() for s in SENTENCE_RE.split(piece))
    return [f for f in fragments if f]


def _first_alias(fragment: str, aliases: list[str]) -> str | None:
    return next((a for a in aliases if a in fragment), None)


def extract_evidence(docs: list[dict], a: Company, b: Company,
                     aliases_of: Callable[[str], list[str]]) -> list[EvidenceSnippet]:
    classify = import_relations().classify
    a_aliases, b_aliases = aliases_of(a.key), aliases_of(b.key)
    key_of = {alias: a.key for alias in a_aliases} | {alias: b.key for alias in b_aliases}
    snippets = []
    for doc in docs:
        for fragment in hilight_fragments(doc.get("hilight", "")):
            x, y = _first_alias(fragment, a_aliases), _first_alias(fragment, b_aliases)
            if not x or not y:
                continue
            hit = (classify(fragment, x, y) or classify(fragment, y, x)
                   or classify_invest(fragment, x, y) or classify_invest(fragment, y, x))
            if not hit:
                continue
            src_alias, dst_alias, rel, confidence, pattern = hit
            snippets.append(EvidenceSnippet(
                news_id=doc["news_id"], src=key_of[src_alias], dst=key_of[dst_alias], rel=rel,
                snippet=fragment[:MAX_SNIPPET], pattern=pattern, confidence=confidence,
                title=doc.get("title", ""), provider=doc.get("provider", ""),
                published=datetime.fromisoformat(doc["dateline"]), link=doc.get("provider_link_page") or ""))
    return snippets
