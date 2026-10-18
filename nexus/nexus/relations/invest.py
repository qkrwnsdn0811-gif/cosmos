"""Ownership (INVEST) evidence from one hilight fragment — the COSMOS rule classifier has no INVEST rule.

Conservative surface patterns only; the investor comes first in the returned tuple:
  "X가 보유한 Y 지분/주식"            X → Y   invest-hold
  "X의 자회사(인) Y", "X, 종속회사 Y"   X → Y   invest-sub
  "Y의 최대주주(인) X"                X → Y   invest-parent
  "X가 Y를 인수했다"                  X → Y   invest-acq
Hedged statements (rumour, plan, review, denial) are dropped with the same HEDGE pattern COSMOS uses.
"""
from __future__ import annotations

import re

from ..cosmos_reuse import import_relations

CONFIDENCE = 0.7
_SUBJ = r"(?:이|가|은|는)?"
_TEMPLATES = (
    ("invest-hold", r"{x}" + _SUBJ + r"\s*(?:보유(?:한|\s*중인|하고\s*있는)|가진)\s*{y}\s*(?:보통주|우선주|주식|지분)"),
    ("invest-sub", r"{x}(?:의|,)?\s*(?:자회사|종속회사|손자회사|계열사)(?:인|로|이자)?\s*{y}"),
    ("invest-parent", r"{y}(?:의)?\s*(?:최대주주|모회사|지주회사|지주사)(?:인|로|이자)?\s*{x}"),
    ("invest-acq", r"{x}(?:이|가|은|는)\s*{y}(?:을|를)?\s*(?:인수|합병)(?:했|하였|를\s*완료|를\s*마무리|한다)"),
)


def classify_invest(fragment: str, x: str, y: str) -> tuple[str, str, str, float, str] | None:
    """(investor alias, investee alias, "INVEST", confidence, pattern) when the fragment says x owns y."""
    if import_relations().HEDGE.search(fragment):
        return None
    for name, template in _TEMPLATES:
        if re.search(template.format(x=re.escape(x), y=re.escape(y)), fragment):
            return x, y, "INVEST", CONFIDENCE, name
    return None
