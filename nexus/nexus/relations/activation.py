"""Which relation types an article activates for propagation (rule stand-in for the stage-2 event classifier).

An article spreads only along relation types it gives a reason for: supply words open SUPPLY paths,
competition words open COMPETE paths, and so on. Keywords come from the COSMOS relation rules
(AI/graph/extract_relations.py KW); INVEST has its own. CO_MENTIONED is always open (such paths are held anyway).
"""
from __future__ import annotations

import re

from ..cosmos_reuse import import_relations
from ..models import Article

INVEST_KW = re.compile(r"지분|투자|인수|출자|합병")


def active_relations(article: Article) -> set[str]:
    text = " ".join((article.title, article.content, " ".join(article.nouns)))
    rels = {rel for rel, pattern in import_relations().KW.items() if pattern.search(text)}
    if INVEST_KW.search(text):
        rels.add("INVEST")
    return rels | {"CO_MENTIONED"}
