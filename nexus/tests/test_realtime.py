from datetime import date, datetime, timedelta, timezone

from nexus.models import Article, EventBundle
from nexus.realtime import state as st
from nexus.realtime.tracker import PAGE, changes, fetch_documents, in_categories, tracker_from

KST = timezone(timedelta(hours=9))


class FakeClient:
    def __init__(self, pages=None, docs=None):
        self.pages = pages or []
        self.docs = docs or {}
        self.calls = []

    def post(self, path, argument, *, cache):
        self.calls.append((path, argument, cache))
        if path == "/changeTracker/news":
            return self.pages[int(argument["offset"]) - 1]
        return {"documents": [self.docs[i] for i in argument["news_ids"] if i in self.docs]}


def row(news_id, status, updated):
    return {"data": {"newsitem_id": news_id, "news_status": status, "update_dt": updated}}


def article(news_id, title="t"):
    return Article(news_id=news_id, title=title, content="", published_date=date(2026, 10, 26),
                   dateline=datetime(2026, 10, 26, 9, tzinfo=KST), provider="p", byline="", categories=(),
                   incidents=(), link="", orgs=(), segments=(), nouns=())


def bundle(news_id):
    return EventBundle(article(news_id), "SEMICONDUCTOR", [], [])


def test_changes_keeps_latest_status_and_never_caches():
    client = FakeClient(pages=[{"total_count": "3", "dataList": [
        row("A", "Update", "2026-10-26 09:00:00"),
        row("B", "Update", "2026-10-26 09:01:00"),
        row("A", "Cancelled", "2026-10-26 09:05:00"),
    ]}])
    got = changes(client, datetime(2026, 10, 26, 9, 10, tzinfo=KST))
    assert got.updated == ["B"] and got.cancelled == ["A"]
    path, arg, cache = client.calls[0]
    assert arg == {"from": "2026-10-26 09:10:00", "interval": "MIN_15", "offset": "1"} and cache is False


def test_changes_pages_until_total():
    full = [row(f"N{i}", "Update", "2026-10-26 09:00:00") for i in range(PAGE)]
    client = FakeClient(pages=[{"total_count": str(PAGE + 1), "dataList": full},
                               {"total_count": str(PAGE + 1), "dataList": [row("LAST", "Update", "x")]}])
    got = changes(client, datetime(2026, 10, 26, tzinfo=KST))
    assert len(got.updated) == PAGE + 1 and len(client.calls) == 2


def test_tracker_from_uses_kst():
    assert tracker_from(datetime(2026, 10, 26, 0, 30, tzinfo=timezone.utc)) == "2026-10-26 09:30:00"


def test_fetch_documents_chunks_by_100():
    ids = [f"N{i}" for i in range(250)]
    client = FakeClient(docs={i: {"news_id": i} for i in ids})
    assert len(fetch_documents(client, ids)) == 250
    assert [len(a["news_ids"]) for _, a, _ in client.calls] == [100, 100, 50]


def test_in_categories_matches_category_or_child():
    assert in_categories({"category": ["IT_과학>모바일", "경제>반도체"]}, ["경제>반도체"])
    assert not in_categories({"category": ["경제>반도체장비주"]}, ["경제>반도체"])
    assert not in_categories({}, ["경제>반도체"])


def test_state_round_trip_and_catch_up_cap(tmp_path):
    path = tmp_path / "rt" / "semis.json"
    now = datetime(2026, 10, 26, 12, tzinfo=KST)
    assert st.tracker_since(st.load_state(path), now) == now - timedelta(minutes=15)
    st.save_state(path, st.State(since=now - timedelta(days=10), prices_date=date(2026, 10, 25)))
    loaded = st.load_state(path)
    assert loaded.prices_date == date(2026, 10, 25)
    assert st.tracker_since(loaded, now) == now - st.MAX_CATCH_UP


def test_merge_articles_new_version_wins():
    merged = st.merge_articles([article("A", "old"), article("B")], [article("A", "new"), article("C")])
    assert [(a.news_id, a.title) for a in merged] == [("A", "new"), ("B", "t"), ("C", "t")]


def test_merge_bundles_replaces_reanalyzed_and_drops_cancelled():
    existing = [bundle("A"), bundle("B"), bundle("C")]
    # A 는 다시 분석해 이벤트가 그대로, B 는 다시 분석했더니 기업이 없어짐, C 는 취소, D 는 새 기사
    merged = st.merge_bundles(existing, [bundle("A"), bundle("D")], drop_news_ids={"C"}, reanalyzed={"A", "B", "D"})
    assert sorted(b.article.news_id for b in merged) == ["A", "D"]


def test_partition_name_sorts_after_month_partition():
    rt = st.realtime_partition("semis", date(2026, 10, 26))
    assert rt == "semis-rt-2026-10-26" and sorted(["semis-2026-10", rt])[-1] == rt
