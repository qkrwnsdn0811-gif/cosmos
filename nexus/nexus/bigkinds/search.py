"""News search on top of BigKindsClient: window splitting, detail lookup, pair hilights."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator

from .client import BigKindsError

PATH = "/search/news"
PAGE_SIZE = 10000
MAX_WINDOW_HITS = 30000   # return_from caps at 20000, return_size at 10000
MIN_PAGE_SIZE = 1000      # 10,000건 페이지가 게이트웨이 시간 초과(504)로 끝나면 반씩 나눠 이 크기까지 내려간다 (2026-10-26 서버에서 관찰)

DOC_FIELDS = [
    "title", "content", "published_at", "enveloped_at", "dateline", "provider",
    "category", "category_incident", "byline", "provider_link_page", "printing_page",
    "tms_raw_stream", "provider_news_id", "publisher_code", "images", "images_caption",
    "provider_subject", "subject_info",
    "tms_ne_organization", "tms_ne_person", "tms_ne_location", "tms_ne_stream",
]
PAIR_FIELDS = ["title", "hilight", "dateline", "provider", "provider_link_page"]
# Relation words for pair searches: BigKinds then cuts the hilight around them, so the fragments that come back
# are the sentences that state a supply/partner/compete/ownership link (2026-10-18; OR + parentheses verified).
RELATION_TERMS = ("공급", "납품", "수주", "위탁생산", "협력", "제휴", "협약", "MOU", "합작", "공동개발",
                  "경쟁", "추격", "지분", "자회사", "종속회사", "인수")
RELATION_HITS = 100


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
            docs = _page(client, start, end, query=query, category=category, return_from=return_from,
                         size=PAGE_SIZE, cache=cache)
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


def _is_gateway_timeout(exc: BigKindsError) -> bool:
    return "HTTP 504" in str(exc.reason)


def _page(client, start: date, end: date, *, query: str, category: list[str] | None, return_from: int, size: int,
          cache: bool) -> list[dict]:
    """[return_from, return_from + size) 구간의 문서. 504 면 두 구간으로 나눠 받는다 (같은 문서를 같은 순서로 준다)."""
    try:
        page = client.post(PATH, _argument(start, end, query=query, category=category, return_from=return_from,
                                           return_size=size, fields=DOC_FIELDS), cache=cache)
        return page.get("documents", [])
    except BigKindsError as exc:
        if not _is_gateway_timeout(exc) or size // 2 < MIN_PAGE_SIZE:
            raise
    half = size // 2
    first = _page(client, start, end, query=query, category=category, return_from=return_from, size=half, cache=cache)
    if len(first) < half:
        return first
    return first + _page(client, start, end, query=query, category=category, return_from=return_from + half,
                         size=size - half, cache=cache)


def fetch_details(client, news_ids: list[str], *, batch: int = 100) -> list[dict]:
    docs = []
    for i in range(0, len(news_ids), batch):
        page = client.post(PATH, {"news_ids": news_ids[i:i + batch], "fields": DOC_FIELDS}, cache=False)
        docs.extend(page.get("documents", []))
    return docs


def _hilight_search(client, query: str, *, start: date, end: date, size: int, today: date) -> list[dict]:
    argument = _argument(start, end, query=query, category=None, return_from=0, return_size=size, fields=PAIR_FIELDS)
    argument["sort"] = {"date": "desc"}
    argument["hilight"] = 200
    return client.post(PATH, argument, cache=end <= today).get("documents", [])


def pair_hilights(client, a_name: str, b_name: str, *, start: date, end: date, size: int = 20,
                  today: date) -> list[dict]:
    return _hilight_search(client, f"{a_name} AND {b_name}", start=start, end=end, size=size, today=today)


def relation_hilights(client, a_name: str, b_name: str, *, start: date, end: date, size: int = RELATION_HITS,
                      today: date) -> list[dict]:
    """Newest pair articles that also contain a relation word, with hilights cut around those words."""
    query = f"{a_name} AND {b_name} AND (" + " OR ".join(RELATION_TERMS) + ")"
    return _hilight_search(client, query, start=start, end=end, size=size, today=today)
