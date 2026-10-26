"""실시간 수집의 체크포인트와 병합 규칙 (DB·네트워크를 모르는 부분).

- 체크포인트(`nexus/data/realtime/<chain>.json`): 마지막으로 성공한 변경이력 조회 시각과 마지막 주가 갱신일.
  DB 적재까지 끝난 뒤에만 앞으로 옮긴다 — 중간에 실패하면 다음 실행이 같은 구간을 다시 본다 (적재는 같은 결과를 다시 써도 된다).
- 새 기사는 하루 단위 파티션 `<chain>-rt-YYYY-MM-DD` 에 쌓는다. 이름이 월 파티션(`<chain>-YYYY-MM`)보다 뒤라
  ArticleStore.read 에서 같은 기사가 겹치면 실시간 판(나중에 고쳐진 기사)이 이긴다. 밤마다 `analyze` 가 이 파티션도 읽는다.
"""
from __future__ import annotations

import json
import os
import pickle
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from ..models import Article, EventBundle

MAX_CATCH_UP = timedelta(days=3)     # 더 오래 멈췄으면 그 앞은 밤 `collect` 가 메운다


@dataclass(frozen=True)
class State:
    since: datetime | None = None
    prices_date: date | None = None


def load_state(path: Path) -> State:
    if not Path(path).is_file():
        return State()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return State(since=datetime.fromisoformat(raw["since"]) if raw.get("since") else None,
                 prices_date=date.fromisoformat(raw["prices_date"]) if raw.get("prices_date") else None)


def save_state(path: Path, state: State) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"since": state.since.isoformat() if state.since else None,
                               "prices_date": state.prices_date.isoformat() if state.prices_date else None}),
                   encoding="utf-8")
    os.replace(tmp, path)


def tracker_since(state: State, now: datetime, first_run: timedelta = timedelta(minutes=15)) -> datetime:
    """변경이력 조회 기준 시각. 처음이면 15분 전, 너무 오래 멈췄으면 3일 전까지만 거슬러 간다."""
    since = state.since or now - first_run
    return max(since, now - MAX_CATCH_UP)


def realtime_partition(chain: str, day: date) -> str:
    return f"{chain}-rt-{day.isoformat()}"


def merge_articles(existing: list[Article], incoming: list[Article]) -> list[Article]:
    """같은 news_id 는 새로 받은 판으로 바꾸고, 처음 본 순서는 지킨다."""
    by_id = {a.news_id: a for a in existing}
    for a in incoming:
        by_id[a.news_id] = a
    return list(by_id.values())


def merge_bundles(existing: list[EventBundle], incoming: list[EventBundle], drop_news_ids: set[str],
                  reanalyzed: set[str]) -> list[EventBundle]:
    """events.pkl 갱신: 다시 분석한 기사(reanalyzed)의 옛 이벤트를 지우고 새 이벤트(incoming)를 넣는다.
    다시 분석했는데 기업이 안 나온 기사는 incoming 에 없으므로 이벤트에서 빠진다. 취소된 기사(drop)도 뺀다."""
    gone = drop_news_ids | reanalyzed
    kept = [b for b in existing if b.article.news_id not in gone]
    fresh = {b.article.news_id: b for b in incoming if b.article.news_id not in drop_news_ids}
    return kept + list(fresh.values())


def read_bundles(path: Path) -> list[EventBundle]:
    # `analyze` 와 이 명령만 쓰는 git 밖 로컬 파일이라 pickle 이 신뢰 경계를 넘지 않는다 (pipeline.cmd_load 와 같다)
    if not Path(path).is_file():
        return []
    with Path(path).open("rb") as f:
        return pickle.load(f)


def write_bundles(path: Path, bundles: list[EventBundle]) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(bundles, f)
    os.replace(tmp, path)


def advance(state: State, since: datetime, prices_date: date | None = None) -> State:
    return replace(state, since=since, prices_date=prices_date or state.prices_date)
