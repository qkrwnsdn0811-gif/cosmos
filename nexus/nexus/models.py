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
