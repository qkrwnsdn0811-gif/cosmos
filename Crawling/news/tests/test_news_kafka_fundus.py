import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from services.news_pipeline.fundus import FundusCollector
from services.news_pipeline.fundus_worker import article_payload


class FakeOutbox:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.events = []

    def seen_url(self, url):
        return url in self.existing

    def enqueue(self, event):
        if event["url"] in self.existing:
            return False
        self.existing.add(event["url"])
        self.events.append(event)
        return True


def test_worker_marks_naive_fundus_crawl_time_as_utc():
    article = SimpleNamespace(
        html=SimpleNamespace(
            responded_url="https://example.com/new",
            requested_url=None,
            crawl_date=datetime(2026, 9, 18, 0, 1),
        ),
        title="Nvidia announces a new chip",
        plaintext="Nvidia announced a new semiconductor product. " * 4,
        publishing_date=None,
        images=[],
        publisher="Example",
        lang="en",
        authors=[],
        topics=[],
        free_access=True,
    )
    extractor = SimpleNamespace(extract=lambda _: {
        "companies": [{
            "market": "NASDAQ", "company_id": "SEC_1", "name": "NVIDIA CORP",
            "ticker": "NVDA", "tickers": ["NVDA"], "n_mentions": 1,
        }],
        "universe_version": "test",
        "registry_sha256": "abc",
    })
    payload = article_payload(
        article, extractor, ("Example", "https://example.com", "us"), "0.5.7",
    )
    assert payload["collected_at"] == "2026-09-18T00:01:00+00:00"


def test_fundus_worker_result_enters_shared_outbox(tmp_path, monkeypatch):
    payload = {
        "scanned": 17,
        "publishers": ["CNBC"],
        "articles": [{
            "publisher_key": "CNBC",
            "publisher_name": "CNBC",
            "url": "https://www.cnbc.com/example",
            "title": "Nvidia announces a new chip",
            "content": "Nvidia announced a new semiconductor product. " * 4,
            "published_at": "2026-09-18T00:00:00+00:00",
            "collected_at": "2026-09-18T00:01:00+00:00",
            "language": "en",
            "metadata": {"nasdaq100_companies": [{"ticker": "NVDA"}]},
        }],
    }
    monkeypatch.setattr(
        "services.news_pipeline.fundus.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )
    outbox = FakeOutbox()
    collector = FundusCollector(
        tmp_path, outbox, python="python", worker="worker.py", registry="registry.json",
        interval_seconds=600,
    )
    result = collector.collect_latest(limit=8)
    assert result["enqueued"] == 1
    assert outbox.events[0]["source"] == "fundus_cnbc"
    assert outbox.events[0]["metadata"]["nasdaq100_companies"][0]["ticker"] == "NVDA"


def test_fundus_existing_snapshot_url_is_not_reenqueued(tmp_path, monkeypatch):
    url = "https://example.com/already-preserved"
    payload = {"scanned": 1, "publishers": ["CNBC"], "articles": [{
        "publisher_key": "CNBC", "publisher_name": "CNBC", "url": url,
        "title": "Existing title", "content": "Existing article body. " * 6,
        "language": "en", "metadata": {},
    }]}
    monkeypatch.setattr(
        "services.news_pipeline.fundus.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )
    outbox = FakeOutbox(existing=[url])
    result = FundusCollector(
        tmp_path, outbox, python="python", worker="worker.py", registry="registry.json",
        interval_seconds=600,
    ).collect_latest(limit=8)
    assert result["existing"] == 1
    assert outbox.events == []

