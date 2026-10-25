"""OpenDART 타법인 출자 현황(otrCprInvstmntSttus) → listed-peer ownership (who holds how much of whom).

Each company's latest periodic report lists the corporations it invested in by name (`inv_prm`, e.g. "삼성전기㈜") with
the closing stake (`trmend_blce_qota_rt`, percent). Only investees that are themselves in the value chain's company
master are kept, so the result is a graph among the listed peers. Parsing and matching are pure; the network call is
`DartClient.investments`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# 법인 표시: ㈜ (주) （주） 주식회사 (유) 와 영문 법인 접미사
_MARKS = re.compile(r"㈜|\(주\)|（주）|\(유\)|주식회사|\b(?:co|ltd|inc|corp|corporation|limited|company)\b\.?", re.IGNORECASE)
_PUNCT = re.compile(r"[\s.,·()（）]+")
# 1% 미만은 지분 관계로 보지 않는다 (예: 단순투자로 들고 있는 0.0002%)
MIN_STAKE = 1.0


@dataclass(frozen=True)
class Investment:
    name: str
    stake: float | None      # closing stake, percent (23.7 = 23.7%)
    purpose: str
    settled: str             # stlm_dt, the report's settlement date
    receipt: str             # rcept_no


@dataclass(frozen=True)
class Ownership:
    investor: str            # company key (KOSPI:005930)
    investee: str
    stake: float
    purpose: str
    settled: str
    receipt: str


def normalize_name(name: str) -> str:
    return _PUNCT.sub("", _MARKS.sub("", name)).lower()


def _stake(value) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_investments(payload: dict) -> list[Investment]:
    if payload.get("status") != "000":
        return []
    return [Investment(str(r.get("inv_prm") or "").strip(), _stake(r.get("trmend_blce_qota_rt")),
                       "".join(str(r.get("invstmnt_purps") or "").split()), str(r.get("stlm_dt") or ""), str(r.get("rcept_no") or ""))
            for r in payload.get("list") or [] if r.get("inv_prm")]


def name_index(names: dict[str, str], aliases: Iterable[dict]) -> dict[str, str]:
    """normalized name → company key. Official names win; aliases only when marked safe (not ambiguous)."""
    index = {normalize_name(a["alias"]): a["ticker"] for a in aliases if a.get("ambiguity") == "safe" and a.get("alias")}
    index.update({normalize_name(name): key for key, name in names.items()})
    return index


def match_ownerships(investor: str, investments: Iterable[Investment], index: dict[str, str]) -> list[Ownership]:
    """Investees among the peers, one row per investee (largest stake). Skips self, unknown names and stakes outside
    [MIN_STAKE, 100]."""
    best: dict[str, Ownership] = {}
    for inv in investments:
        investee = index.get(normalize_name(inv.name))
        if investee is None or investee == investor or inv.stake is None or not MIN_STAKE <= inv.stake <= 100:
            continue
        if investee not in best or inv.stake > best[investee].stake:
            best[investee] = Ownership(investor, investee, inv.stake, inv.purpose, inv.settled, inv.receipt)
    return sorted(best.values(), key=lambda o: (-o.stake, o.investee))


def ownership_edges(owned: Iterable[Ownership], windows: Iterable[str]):
    """Galaxy INVEST edges from disclosures, one per window (a stake is not a news count, so every window shows it).
    The score is the stake itself (23.7% → 23.7 on the 0–100 scale); there are no evidence articles."""
    from ..galaxy.build import GalaxyEdge
    windows = tuple(windows)
    return [GalaxyEdge(o.investor, o.investee, "INVEST", "DART", w, round(o.stake, 4), None, ()) for o in owned for w in windows]


def merge_ownership(edges, dart_edges):
    """News edges plus disclosure edges; a news INVEST edge in the same direction as a disclosed stake gives way to it."""
    disclosed = {(e.src, e.dst) for e in dart_edges}
    kept = [e for e in edges if not (e.rel == "INVEST" and (e.src, e.dst) in disclosed)]
    return kept + list(dart_edges)
