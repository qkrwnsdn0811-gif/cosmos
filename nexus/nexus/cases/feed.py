"""Feed order for the first screen: newest day first, and within a day rotate through the companies.

SK hynix or Samsung Electronics is the main company of about nine in ten semiconductor articles, so a plain newest-first
feed shows almost nothing else (2026-10-25: 185 of the latest 200). Within each KST day the feed now shows every main
company's latest article first, then each company's second article, and so on; days stay newest first.

The order is stored as one timestamp per event (news_event.feed_at) so the API keeps its (time, id) keyset cursor:
day start + (1440 − round) minutes + the article's time of day / 1440. Rounds stay below 1440, so a day's feed times
never leave that day, a lower round always sorts first, and within a round the later article does.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ..models import EventBundle

KST = timezone(timedelta(hours=9))
MAX_ROUND = 1439


def main_company(bundle: EventBundle, counts: dict[str, int]) -> str | None:
    """The article's most salient company; a tie goes to the company with fewer articles overall, then the key."""
    if not bundle.mentions:
        return None
    return min(bundle.mentions, key=lambda m: (-m.salience, counts.get(m.key, 0), m.key)).key


def main_companies(bundles: Iterable[EventBundle]) -> dict[str, str | None]:
    """news_id → main company; ties go to the company that is the main one less often (counted with key ties)."""
    bundles = list(bundles)
    counts = Counter(main for b in bundles if (main := main_company(b, {})) is not None)
    return {b.article.news_id: main_company(b, counts) for b in bundles}


def feed_times(bundles: Iterable[EventBundle], mains: dict[str, str | None] | None = None) -> dict[str, datetime]:
    """news_id → feed time (sort descending for the feed). mains: main_companies(bundles), computed when omitted."""
    bundles = list(bundles)
    mains = main_companies(bundles) if mains is None else mains
    groups: dict[tuple, list[EventBundle]] = defaultdict(list)
    for b in bundles:
        local = b.article.dateline.astimezone(KST)
        groups[(local.date(), mains.get(b.article.news_id) or b.article.news_id)].append(b)
    times = {}
    for (day, _), members in groups.items():
        start = datetime(day.year, day.month, day.day, tzinfo=KST)
        for rnd, b in enumerate(sorted(members, key=lambda x: x.article.dateline, reverse=True), start=1):
            into_day = b.article.dateline.astimezone(KST) - start
            times[b.article.news_id] = start + timedelta(minutes=1440 - min(rnd, MAX_ROUND)) + into_day / 1440
    return times
