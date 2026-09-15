"""Step 7 — entity linking: turn an NER surface form into a ticker (or an explicit "unknown").

    from linker import EntityLinker
    lk = EntityLinker.from_csv("data/aliases.csv")
    lk.link("삼성電子")     -> LinkResult(ticker="005930", method="fuzzy", score=0.83, ...)
    lk.link("포스코")       -> LinkResult(ticker="005490", method="group_default", score=0.5)  (group alias with default)
    lk.link("국토교통부")   -> LinkResult(ticker=None, reason="org_suffix")
    lk.link("티웨이항공")   -> LinkResult(ticker=None, reason="unknown")   -> logged as dictionary-growth candidate

Order of rules:
  1. normalize   : strip ㈜/(주)/주식회사, spaces, punctuation; casefold Latin; hanja → hangul for a few common company chars
  2. exact       : normalized form == normalized alias (safe / product / group aliases)
  3. blocked     : matches a blocker (sports team, unlisted subsidiary, preferred stock) -> ticker None, reason "blocked"
  4. org filter  : government / political / academic suffixes -> reason "org_suffix"  (KLUE OG labels include these)
  5. fuzzy       : difflib ratio >= threshold against aliases of the same script (Hangul vs Latin), length >= 3
  6. unknown     : candidate for the dictionary-growth loop (Step 9)
"""
from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

HANJA_TO_HANGUL = {"電子": "전자", "電": "전", "子": "자", "株": "주", "重工業": "중공업", "重": "중", "銀行": "은행",
                   "證券": "증권", "生命": "생명", "化學": "화학", "建設": "건설", "物産": "물산", "製藥": "제약",
                   "航空": "항공", "自動車": "자동차", "半導體": "반도체", "韓國": "한국", "現代": "현대", "三星": "삼성",
                   "日本": "일본", "美國": "미국", "中國": "중국", "韓": "한", "美": "미", "日": "일", "中": "중"}
STRIP_RE = re.compile(r"\(주\)|㈜|주식회사|\(株\)|株式會社|\bInc\.?\b|\bCorp\.?\b|\bCo\.,?\s*Ltd\.?\b|\bLtd\.?\b|\bplc\b|\bLLC\b|\bSA\b|\bAG\b|\bNV\b",
                      re.IGNORECASE)
PUNCT_RE = re.compile(r"[\s\.\,\-_·'’\"“”\(\)\[\]/&]+")
ORG_SUFFIXES = ("부", "청", "처", "위원회", "위", "국회", "정부", "청와대", "당", "협회", "연합", "재단", "대학교", "대학", "학교",
                "경찰서", "경찰청", "법원", "검찰", "지검", "지청", "군", "시청", "구청", "도청", "공단", "공사", "노조", "노동조합",
                "연구원", "연구소", "학회", "총연합회", "협의회", "협력체", "협력단", "본부", "사무처", "센터", "병원", "의료원",
                "방송", "신문", "사단", "부대", "사령부", "기상청", "교육청", "대사관", "영사관", "조합", "위원장")
ORG_EXACT = {"정부", "국회", "청와대", "민주당", "국민의힘", "유엔", "UN", "EU", "IMF", "WHO", "OECD", "APEC", "NATO", "FBI", "SEC",
             "연준", "Fed", "한국은행", "금융위원회", "금감원", "금융감독원", "공정위", "공정거래위원회", "기재부", "산업부", "국토부",
             "MLB", "KBO", "NBA", "FIFA", "IOC", "KOICA", "코이카", "NRC", "FDA", "CDC", "NASA", "ECB", "BOJ", "WTO", "ASEAN", "G7", "G20"}
SPORTS_HINT = ("라이온즈", "이글스", "트윈스", "베어스", "위즈", "타이거즈", "히어로즈", "랜더스", "자이언츠", "다이노스", "다저스",
               "양키스", "FC", "유나이티드", "구단")
HANGUL_RE = re.compile(r"[가-힣]")


def normalize(s: str) -> str:
    for h, k in HANJA_TO_HANGUL.items():
        s = s.replace(h, k)
    s = STRIP_RE.sub("", s)
    s = PUNCT_RE.sub("", s)
    return s.casefold().strip()


# Industry/corporate words that are a large share of many official names: difflib scores
# "Pharmaceutical" against "Hanmi Pharmaceutical" at 0.85, so a bare suffix word would link to
# whichever company happens to be closest. Never fuzzy-link a surface that is only one of these.
FUZZY_GENERIC = frozenset({
    "pharmaceutical", "pharmaceuticals", "pharma", "technologies", "technology", "tech",
    "holdings", "holding", "group", "healthcare", "health", "systems", "system", "solutions",
    "solution", "industries", "industrial", "electronics", "electronic", "electric", "motors",
    "motor", "energy", "financial", "finance", "bank", "banking", "chemical", "chemicals",
    "materials", "material", "semiconductor", "semiconductors", "corporation", "company",
    "international", "global", "partners", "capital", "networks", "network", "digital", "data",
    "software", "hardware", "communications", "telecom", "insurance", "securities", "resources",
    "제약", "전자", "화학", "그룹", "지주", "금융", "증권", "은행", "보험", "에너지", "산업",
    "중공업", "건설", "생명", "통신", "반도체", "홀딩스", "테크", "테크놀로지",
})


@dataclass
class LinkResult:
    surface: str
    ticker: str | None
    name_official: str = ""
    method: str = ""        # exact | product | group_default | fuzzy | none
    score: float = 0.0
    reason: str = ""        # for ticker None: blocked | org_suffix | sports | unknown | ambiguous_group
    matched_alias: str = ""


class EntityLinker:
    def __init__(self, alias_rows: list[dict], fuzzy_threshold: float = 0.90):
        self.exact: dict[str, dict] = {}
        self.blocked: set[str] = set()
        self.hangul_aliases: list[tuple[str, dict]] = []
        self.latin_aliases: list[tuple[str, dict]] = []
        for r in alias_rows:
            n = normalize(r["alias"])
            if not n:
                continue
            if r["alias_type"] == "blocker":
                self.blocked.add(n)
                continue
            if r["alias_type"] == "industry":
                continue
            # first writer wins, but a safe alias beats group/product for the same normalized form
            prev = self.exact.get(n)
            if prev is None or (prev["ambiguity"] != "safe" and r["ambiguity"] == "safe"):
                self.exact[n] = r
            if r["ambiguity"] == "safe" and len(n) >= 3:
                (self.hangul_aliases if HANGUL_RE.search(n) else self.latin_aliases).append((n, r))
        self.fuzzy_threshold = fuzzy_threshold

    @classmethod
    def from_csv(cls, path: str | Path, **kw) -> "EntityLinker":
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            return cls(list(csv.DictReader(f)), **kw)

    def link(self, surface: str) -> LinkResult:
        n = normalize(surface)
        if not n:
            return LinkResult(surface, None, reason="empty")
        if n in self.blocked or any(h.casefold() in n for h in SPORTS_HINT):
            return LinkResult(surface, None, reason="blocked")
        r = self.exact.get(n)
        if r is not None:
            if r["ambiguity"] == "safe" or r["ambiguity"] == "ticker_only":
                return LinkResult(surface, r["ticker"], r["name_official"], "exact", 1.0, matched_alias=r["alias"])
            if r["ambiguity"] == "product":
                return LinkResult(surface, r["ticker"], r["name_official"], "product", 0.7, matched_alias=r["alias"])
            if r["ambiguity"] == "needs_context":
                if r["ticker"]:
                    return LinkResult(surface, r["ticker"], r["name_official"], "group_default", 0.5, matched_alias=r["alias"])
                return LinkResult(surface, None, reason="ambiguous_group", matched_alias=r["alias"])
        if surface.strip() in ORG_EXACT or n in {normalize(x) for x in ORG_EXACT}:
            return LinkResult(surface, None, reason="org_suffix")
        if HANGUL_RE.search(surface) and any(surface.strip().endswith(suf) for suf in ORG_SUFFIXES):
            return LinkResult(surface, None, reason="org_suffix")
        # fuzzy against aliases of the same script
        pool = self.hangul_aliases if HANGUL_RE.search(n) else self.latin_aliases
        if n.lower() in FUZZY_GENERIC:
            return LinkResult(surface, None, reason="generic_word")
        if len(n) >= 3:
            best, best_r = 0.0, None
            for a, r in pool:
                if abs(len(a) - len(n)) > max(3, len(n) // 2):
                    continue
                s = difflib.SequenceMatcher(None, n, a).ratio()
                if s > best:
                    best, best_r = s, r
            if best_r is not None and best >= self.fuzzy_threshold:
                return LinkResult(surface, best_r["ticker"], best_r["name_official"], "fuzzy", round(best, 3),
                                  matched_alias=best_r["alias"])
        return LinkResult(surface, None, reason="unknown")
