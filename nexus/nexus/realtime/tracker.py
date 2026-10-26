"""/changeTracker/news (뉴스 변경이력) 과 news_ids 상세 조회.

실측(docs/nexus/bigkinds-api-findings.md 4절): from 에서 interval 만큼 거슬러 올라간 시점부터 호출 시점까지의 변경을 준다.
신규 기사도 'Update' 로 오고, 취소된 기사는 'Cancelled' 다. 한 페이지는 3000건(offset 1부터).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..bigkinds.search import DOC_FIELDS, PATH

KST = timezone(timedelta(hours=9))
TRACKER_PATH = "/changeTracker/news"
PAGE = 3000
DETAIL_CHUNK = 100          # news_ids 100개는 한 번에 받는 것을 실측했다
MAX_PAGES = 50              # 3000 × 50 — 며칠 밀린 경우에도 충분하다


@dataclass(frozen=True)
class Changes:
    updated: list[str]       # 새로 들어왔거나 고쳐진 기사 (마지막 상태가 Update)
    cancelled: list[str]     # 마지막 상태가 Cancelled


def tracker_from(when: datetime) -> str:
    return when.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def changes(client, since: datetime, interval: str = "MIN_15") -> Changes:
    """since 를 기준 시점으로, interval 만큼 겹쳐서 지금까지 바뀐 기사. 같은 기사가 여러 번 오면 update_dt 가 가장 늦은 상태를 쓴다."""
    latest: dict[str, tuple[str, str]] = {}
    for offset in range(1, MAX_PAGES + 1):
        page = client.post(TRACKER_PATH, {"from": tracker_from(since), "interval": interval, "offset": str(offset)},
                           cache=False)
        rows = page.get("dataList") or []
        for row in rows:
            d = row.get("data") or {}
            news_id, status, updated = d.get("newsitem_id"), d.get("news_status"), d.get("update_dt") or ""
            if not news_id:
                continue
            if news_id not in latest or updated >= latest[news_id][1]:
                latest[news_id] = (status, updated)
        total = int(page.get("total_count") or 0)
        if len(rows) < PAGE or offset * PAGE >= total:
            break
    updated = [k for k, (s, _) in latest.items() if s != "Cancelled"]
    cancelled = [k for k, (s, _) in latest.items() if s == "Cancelled"]
    return Changes(sorted(updated), sorted(cancelled))


def fetch_documents(client, news_ids: list[str]) -> list[dict]:
    """news_ids 상세 조회 (기사 본문은 200자까지만 온다). 수집 검색과 같은 필드를 받는다."""
    docs: list[dict] = []
    for i in range(0, len(news_ids), DETAIL_CHUNK):
        chunk = news_ids[i:i + DETAIL_CHUNK]
        page = client.post(PATH, {"news_ids": chunk, "fields": DOC_FIELDS, "return_size": len(chunk)}, cache=False)
        docs.extend(page.get("documents") or [])
    return docs


def in_categories(doc: dict, categories: list[str]) -> bool:
    """수집 검색의 분류 조건과 같다: 기사 분류 중 하나라도 대상 분류(또는 그 하위)면 대상."""
    mine = doc.get("category") or []
    return any(c == want or c.startswith(want + ">") for c in mine for want in categories)
