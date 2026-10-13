"""News search on top of BigKindsClient: window splitting, detail lookup, pair hilights."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator

from .client import BigKindsError

PATH = "/search/news"
PAGE_SIZE = 10000
MAX_WINDOW_HITS = 30000   # return_from caps at 20000, return_size at 10000

DOC_FIELDS = [
    "title", "content", "published_at", "enveloped_at", "dateline", "provider",
    "category", "category_incident", "byline", "provider_link_page", "printing_page",
    "tms_raw_stream", "provider_news_id", "publisher_code", "images", "images_caption",
    "provider_subject", "subject_info",
    "tms_ne_organization", "tms_ne_person", "tms_ne_location", "tms_ne_stream",
]
PAIR_FIELDS = ["title", "hilight", "dateline", "provider", "provider_link_page"]


def _argument(start: date, end: date, *, query: str, category: list[str] | None,
              return_from: int, return_size: int, fields: list[str]) -> dict:
    argument = {
        "query": query,
        "published_at": {"from": start.isoformat(), "until": end.isoformat()},
        "sort": {"date": "asc"},
        "return_from": return_from,
        "return_size": return_size,
        "fields": fields,
    }
    if category is not None:
        argument["category"] = category
    return argument


def search_documents(client, *, start: date, end: date, category: list[str] | None = None,
                     query: str = "", today: date) -> Iterator[dict]:
    """Yield every document published in [start, end), splitting windows that exceed the paging cap."""
    cache = end <= today
    probe = client.post(PATH, _argument(start, end, query=query, category=category,
                                        return_from=0, return_size=1, fields=[]), cache=cache)
    total = probe.get("total_hits", 0)
    if total <= MAX_WINDOW_HITS:
        received = 0
        for return_from in range(0, MAX_WINDOW_HITS, PAGE_SIZE):
            if received >= total:
                break
            page = client.post(PATH, _argument(start, end, query=query, category=category,
                                               return_from=return_from, return_size=PAGE_SIZE,
                                               fields=DOC_FIELDS), cache=cache)
            docs = page.get("documents", [])
            if not docs:
                break
            received += len(docs)
            yield from docs
        return
    days = (end - start).days
    if days <= 1:
        raise BigKindsError(-1, f"single day exceeds {MAX_WINDOW_HITS}: {start} has {total} hits")
    mid = start + timedelta(days=days // 2)
    yield from search_documents(client, start=start, end=mid, category=category, query=query, today=today)
    yield from search_documents(client, start=mid, end=end, category=category, query=query, today=today)


def fetch_details(client, news_ids: list[str], *, batch: int = 100) -> list[dict]:
    docs = []
    for i in range(0, len(news_ids), batch):
        page = client.post(PATH, {"news_ids": news_ids[i:i + batch], "fields": DOC_FIELDS}, cache=False)
        docs.extend(page.get("documents", []))
    return docs


def pair_hilights(client, a_name: str, b_name: str, *, start: date, end: date, size: int = 20,
                  today: date) -> list[dict]:
    argument = _argument(start, end, query=f"{a_name} AND {b_name}", category=None,
                         return_from=0, return_size=size, fields=PAIR_FIELDS)
    argument["sort"] = {"date": "desc"}
    argument["hilight"] = 200
    return client.post(PATH, argument, cache=end <= today).get("documents", [])
