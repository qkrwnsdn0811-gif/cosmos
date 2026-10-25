from datetime import date

import pytest

from nexus.bigkinds.client import BigKindsError
from nexus.bigkinds.search import RELATION_TERMS, fetch_details, pair_hilights, relation_hilights, search_documents

TODAY = date(2026, 10, 14)


class FakeClient:
    """Answers /search/news from a {(from, until): total_hits} table."""

    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def post(self, path, argument, *, cache):
        self.calls.append((path, argument, cache))
        if "news_ids" in argument:
            return {"documents": [{"news_id": i} for i in argument["news_ids"]]}
        window = (argument["published_at"]["from"], argument["published_at"]["until"])
        total = self.hits[window]
        start, size = argument["return_from"], argument["return_size"]
        count = max(0, min(size, total - start))
        docs = [{"news_id": f"{window[0]}-{start + i}"} for i in range(count)]
        return {"total_hits": total, "documents": docs}

    def pages(self):
        return [a for _, a, _ in self.calls if a.get("return_size") == 10000]


def collect(client, start, end):
    return list(search_documents(client, start=start, end=end, category=["경제>반도체"], today=TODAY))


def test_small_window_single_page():
    client = FakeClient({("2025-01-01", "2025-02-01"): 7})
    docs = collect(client, date(2025, 1, 1), date(2025, 2, 1))
    assert len(docs) == 7
    assert len(client.calls) == 2
    assert client.calls[0][1]["return_size"] == 1


def test_pages_until_total():
    client = FakeClient({("2025-01-01", "2025-02-01"): 25000})
    docs = collect(client, date(2025, 1, 1), date(2025, 2, 1))
    assert len(docs) == 25000
    assert [a["return_from"] for a in client.pages()] == [0, 10000, 20000]


def test_splits_large_window():
    client = FakeClient({
        ("2025-01-01", "2025-03-01"): 50000,
        ("2025-01-01", "2025-01-30"): 20000,
        ("2025-01-30", "2025-03-01"): 20000,
    })
    docs = collect(client, date(2025, 1, 1), date(2025, 3, 1))
    assert len(docs) == 40000
    windows = [(a["published_at"]["from"], a["published_at"]["until"]) for a in client.pages()]
    assert windows == [("2025-01-01", "2025-01-30")] * 2 + [("2025-01-30", "2025-03-01")] * 2


def test_single_day_overflow_raises():
    client = FakeClient({("2025-01-01", "2025-01-02"): 40000})
    with pytest.raises(BigKindsError):
        collect(client, date(2025, 1, 1), date(2025, 1, 2))


def test_cache_flag_by_today():
    client = FakeClient({("2026-10-01", "2026-10-14"): 3, ("2026-10-01", "2026-10-15"): 3})
    collect(client, date(2026, 10, 1), date(2026, 10, 14))
    assert all(cache for _, _, cache in client.calls)
    client.calls.clear()
    collect(client, date(2026, 10, 1), date(2026, 10, 15))
    assert not any(cache for _, _, cache in client.calls)


def test_fetch_details_batches():
    client = FakeClient({})
    ids = [f"id{i}" for i in range(250)]
    docs = fetch_details(client, ids)
    assert [len(a["news_ids"]) for _, a, _ in client.calls] == [100, 100, 50]
    assert len(docs) == 250


def test_pair_query_and_hilight():
    client = FakeClient({("2025-01-01", "2026-10-14"): 2})
    docs = pair_hilights(client, "삼성전자", "SK하이닉스", start=date(2025, 1, 1), end=date(2026, 10, 14), today=TODAY)
    argument = client.calls[0][1]
    assert argument["query"] == "삼성전자 AND SK하이닉스"
    assert argument["hilight"] == 200
    assert argument["return_size"] == 20
    assert len(docs) == 2


def test_relation_query_adds_relation_words():
    client = FakeClient({("2025-01-01", "2026-10-14"): 150})
    docs = relation_hilights(client, "하나머티리얼즈", "하나마이크론", start=date(2025, 1, 1), end=date(2026, 10, 14),
                             today=TODAY)
    argument = client.calls[0][1]
    assert argument["query"] == "하나머티리얼즈 AND 하나마이크론 AND (" + " OR ".join(RELATION_TERMS) + ")"
    assert {"공급", "협력", "경쟁", "지분", "인수"} <= set(RELATION_TERMS)
    assert (argument["return_size"], argument["hilight"], argument["sort"]) == (100, 200, {"date": "desc"})
    assert len(docs) == 100
