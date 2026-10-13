"""Shared value types. Company keys are "MARKET:TICKER" strings, e.g. "KOSPI:005930"."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

Entity = tuple[str, str]   # (name, type) from tms_ne_stream, e.g. ("삼성전자", "OG")


@dataclass(frozen=True)
class Article:
    news_id: str
    title: str
    content: str                               # first 200 chars only (BigKinds limit)
    published_date: date
    dateline: datetime                         # KST-aware release time
    provider: str
    byline: str
    categories: tuple[str, ...]
    incidents: tuple[str, ...]
    link: str                                  # provider_link_page or ""; never fetched
    orgs: tuple[str, ...]                      # tms_ne_organization (full body)
    segments: tuple[tuple[Entity, ...], ...]   # tms_ne_stream (per sentence-like segment)
    nouns: tuple[str, ...]                     # tms_raw_stream (full body, in order)


@dataclass(frozen=True)
class Company:
    key: str            # "MARKET:TICKER"
    market: str         # KOSPI | KOSDAQ | NASDAQ
    ticker: str
    name: str
    value_chain: str    # SEMICONDUCTOR | BATTERY | AUTO
    role: str           # 설계 | 제조 | 장비 | 소재 | 부품 | 후공정 | 고객


@dataclass(frozen=True)
class CompanyMention:
    key: str
    salience: float               # title 1.0, content 0.8, orgs only 0.6
    sources: tuple[str, ...]      # sorted subset of ("content", "orgs", "title")


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    rel: str            # SUPPLY | INVEST | PARTNER | COMPETE | CO_MENTIONED
    strength: float     # 0..1 before the type weight
    directed: bool
    source: str         # COSMOS | NEWS | COMENTION


@dataclass(frozen=True)
class EvidenceSnippet:
    news_id: str
    src: str            # company key; supplier for SUPPLY
    dst: str
    rel: str            # SUPPLY | PARTNER | COMPETE
    snippet: str        # hilight fragment, <= 300 chars
    pattern: str        # COSMOS rule pattern name
    confidence: float
    title: str
    provider: str
    published: datetime
    link: str
