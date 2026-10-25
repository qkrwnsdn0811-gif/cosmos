"""Shared value types. Company keys are "MARKET:TICKER" strings, e.g. "KOSPI:005930"."""
from __future__ import annotations

from dataclasses import dataclass, field
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
class PathStep:
    src: str            # actual edge direction, not traversal direction
    dst: str
    rel: str
    source: str = field(default="", compare=False)      # COSMOS | NEWS | COMENTION — where the edge came from
    strength: float = field(default=0.0, compare=False)


@dataclass(frozen=True)
class Impact:
    key: str
    basis: str                      # REPORTED | INFERRED
    hop: int                        # 0 for REPORTED
    path: tuple[PathStep, ...]      # seed outward
    score: float
    status: str                     # OK | HOLD
    hold_reason: str | None


@dataclass(frozen=True)
class AbnormalReturn:
    key: str
    window_days: int        # trading days from t0 inclusive (1 = event day only)
    benchmark: str          # KOSPI | KOSDAQ
    stock_return: float
    benchmark_return: float
    abnormal_return: float


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


@dataclass
class EventBundle:
    """One BigKinds article treated as an event, with everything the loader writes for it."""
    article: Article
    value_chain: str
    mentions: list[CompanyMention]
    impacts: list[Impact]
    returns: list[AbnormalReturn] = field(default_factory=list)
    trading_date: date | None = None
