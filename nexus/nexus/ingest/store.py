"""Partitioned Parquet store for normalized articles."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from ..models import Article


COLUMNS = ["news_id", "title", "content", "published_date", "dateline", "provider", "byline",
           "categories", "incidents", "link", "orgs", "segments", "nouns"]


def _row(a: Article) -> dict:
    return {
        "news_id": a.news_id, "title": a.title, "content": a.content,
        "published_date": a.published_date.isoformat(), "dateline": a.dateline.isoformat(),
        "provider": a.provider, "byline": a.byline,
        "categories": list(a.categories), "incidents": list(a.incidents), "link": a.link,
        "orgs": list(a.orgs),
        "segments": json.dumps([[list(e) for e in seg] for seg in a.segments], ensure_ascii=False),
        "nouns": list(a.nouns),
    }


def _article(r: dict) -> Article:
    return Article(
        news_id=r["news_id"], title=r["title"], content=r["content"],
        published_date=datetime.fromisoformat(r["published_date"]).date(),
        dateline=datetime.fromisoformat(r["dateline"]),
        provider=r["provider"], byline=r["byline"],
        categories=tuple(r["categories"]), incidents=tuple(r["incidents"]), link=r["link"],
        orgs=tuple(r["orgs"]),
        segments=tuple(tuple((name, kind) for name, kind in seg) for seg in json.loads(r["segments"])),
        nouns=tuple(r["nouns"]),
    )


class ArticleStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _file(self, partition: str) -> Path:
        return self.root / f"partition={partition}" / "part.parquet"

    def write(self, articles: Iterable[Article], *, partition: str) -> int:
        rows = [_row(a) for a in articles]
        path = self._file(partition)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=COLUMNS).to_parquet(path, index=False)
        return len(rows)

    def partitions(self) -> list[str]:
        return sorted(p.name.split("=", 1)[1] for p in self.root.glob("partition=*") if (p / "part.parquet").is_file())

    def read(self, partitions: list[str] | None = None) -> list[Article]:
        """Later partitions (by name) win for a repeated news_id; first-seen order is kept."""
        by_id: dict[str, Article] = {}
        for partition in sorted(partitions) if partitions is not None else self.partitions():
            frame = pd.read_parquet(self._file(partition))
            for record in frame.to_dict("records"):
                by_id[record["news_id"]] = _article(record)
        return list(by_id.values())
