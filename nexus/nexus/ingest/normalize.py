"""BigKinds /search/news document → Article."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import Article, Entity

KST = timezone(timedelta(hours=9))


def _lines(value: str | None) -> tuple[str, ...]:
    return tuple(s.strip() for s in (value or "").split("\n") if s.strip())


def _entity(token: str) -> Entity | None:
    name, sep, kind = token.strip().rpartition(":")
    if not sep or not name.strip() or not kind.strip():
        return None
    return name.strip(), kind.strip()


def _segments(value: str | None) -> tuple[tuple[Entity, ...], ...]:
    segments = []
    for line in _lines(value):
        entities = tuple(e for e in (_entity(t) for t in line.split(",")) if e)
        if entities:
            segments.append(entities)
    return tuple(segments)


MAX_DRIFT = timedelta(days=1)   # evening release for the next day's print edition is normal


def _parse(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _timestamp(doc: dict) -> datetime:
    """Release time: dateline, else enveloped_at — each only if within a day of the published date.
    About 9% of datelines drift; some land weeks or months later, which would misplace t0."""
    published = _parse(doc["published_at"])
    for name in ("dateline", "enveloped_at"):
        if doc.get(name):
            candidate = _parse(doc[name])
            if abs(candidate.date() - published.date()) <= MAX_DRIFT:
                return candidate
    return published


def to_article(doc: dict) -> Article:
    return Article(
        news_id=doc["news_id"],
        title=(doc.get("title") or "").strip(),
        content=(doc.get("content") or "").strip(),
        published_date=datetime.fromisoformat(doc["published_at"]).date(),
        dateline=_timestamp(doc),
        provider=doc.get("provider") or "",
        byline=doc.get("byline") or "",
        categories=tuple(doc.get("category") or ()),
        incidents=tuple(doc.get("category_incident") or ()),
        link=doc.get("provider_link_page") or "",
        orgs=_lines(doc.get("tms_ne_organization")),
        segments=_segments(doc.get("tms_ne_stream")),
        nouns=_lines(doc.get("tms_raw_stream")),
    )
