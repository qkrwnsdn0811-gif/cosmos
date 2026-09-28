"""Historical URL discovery and a durable local crawl queue.

Yahoo Finance exposes a public date-based site index. The queue in this module
separates URL discovery from article downloads so a multi-day crawl can resume
after a restart without losing or duplicating work.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Self
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from lib.Crawling.News.overseas import (
    NewsCandidate,
    canonicalize_url,
    normalize_text,
)


@dataclass(frozen=True, slots=True)
class YahooArchivePage:
    """Article candidates and the cursor for the next page of the same day."""

    candidates: list[NewsCandidate]
    next_url: str | None


def parse_yahoo_archive_page(html: str, page_url: str) -> YahooArchivePage:
    """Parse one Yahoo Finance daily archive page and its next cursor."""

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[NewsCandidate] = []
    seen: set[str] = set()
    next_url: str | None = None

    for link in soup.select("a[href]"):
        raw_url = urljoin(page_url, link.get("href", ""))
        link_text = normalize_text(link.get_text(" ", strip=True))
        parts = urlsplit(raw_url)

        if (
            link_text.casefold() == "next"
            and parts.netloc == "finance.yahoo.com"
            and "/sitemap/" in parts.path
            and "_start" in parts.path
        ):
            next_url = canonicalize_url(raw_url)
            continue

        url = canonicalize_url(raw_url)
        parts = urlsplit(url)
        if parts.netloc != "finance.yahoo.com":
            continue
        if not parts.path.endswith(".html"):
            continue
        # Yahoo also serves historical articles under category paths such as
        # /markets/stocks/articles/<slug>.html and /sectors/.../articles/<slug>.html.
        if not (
            parts.path.startswith(("/news/", "/article/", "/articles/"))
            or re.search(r"/articles?/[^/]+\.html$", parts.path)
        ):
            continue
        if len(link_text) < 10 or url in seen:
            continue

        seen.add(url)
        candidates.append(
            NewsCandidate(
                source="yahoo_finance",
                title=link_text,
                url=url,
            )
        )

    return YahooArchivePage(candidates=candidates, next_url=next_url)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class HistoryQueue:
    """SQLite-backed crawl queue and discovery checkpoint store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS history_queue (
                url_hash TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                organization TEXT,
                archive_key TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'processing', 'done', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                discovered_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_history_queue_work
                ON history_queue(source, status, attempts, discovered_at);

            CREATE TABLE IF NOT EXISTS history_state (
                source TEXT NOT NULL,
                state_key TEXT NOT NULL,
                state_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source, state_key)
            );

            CREATE TABLE IF NOT EXISTS history_discovery_errors (
                source TEXT NOT NULL,
                page_url TEXT NOT NULL,
                archive_key TEXT,
                attempts INTEGER NOT NULL DEFAULT 1,
                last_error TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source, page_url)
            );
            """
        )
        self.connection.commit()

    def reset_interrupted(self) -> int:
        cursor = self.connection.execute(
            "UPDATE history_queue SET status='pending' WHERE status='processing'"
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def enqueue(
        self,
        candidates: Iterable[NewsCandidate],
        *,
        archive_key: str,
    ) -> int:
        before = self.connection.total_changes
        now = _utc_now()
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO history_queue (
                url_hash, source, title, url, organization,
                archive_key, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    _url_hash(candidate.url),
                    candidate.source,
                    candidate.title,
                    candidate.url,
                    candidate.organization,
                    archive_key,
                    now,
                )
                for candidate in candidates
            ),
        )
        self.connection.commit()
        return self.connection.total_changes - before

    def claim_next(self, source: str, max_attempts: int) -> NewsCandidate | None:
        claimed = self.claim_batch(source, max_attempts, 1)
        return claimed[0] if claimed else None

    def claim_batch(
        self,
        source: str,
        max_attempts: int,
        limit: int,
    ) -> list[NewsCandidate]:
        """Atomically claim a bounded batch with one SQLite commit."""

        if limit < 1:
            return []
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            rows = self.connection.execute(
                """
                SELECT url_hash, source, title, url, organization
                FROM history_queue
                WHERE source=?
                  AND status IN ('pending', 'failed')
                  AND attempts < ?
                ORDER BY attempts ASC, discovered_at ASC, rowid ASC
                LIMIT ?
                """,
                (source, max_attempts, limit),
            ).fetchall()
            if not rows:
                self.connection.commit()
                return []

            hashes = [str(row["url_hash"]) for row in rows]
            placeholders = ",".join("?" for _ in hashes)
            self.connection.execute(
                f"""
                UPDATE history_queue
                SET status='processing', attempts=attempts+1
                WHERE url_hash IN ({placeholders})
                  AND status IN ('pending', 'failed')
                """,
                hashes,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        return [
            NewsCandidate(
                source=row["source"],
                title=row["title"],
                url=row["url"],
                organization=row["organization"],
            )
            for row in rows
        ]

    def mark_done(self, url: str) -> None:
        self.mark_done_many([url])

    def mark_done_many(self, urls: Iterable[str]) -> None:
        values = [(_utc_now(), _url_hash(url)) for url in urls]
        if not values:
            return
        self.connection.executemany(
            """
            UPDATE history_queue
            SET status='done', last_error=NULL, completed_at=?
            WHERE url_hash=?
            """,
            values,
        )
        self.connection.commit()

    def mark_failed(self, url: str, error: str) -> None:
        self.mark_failed_many([(url, error)])

    def mark_failed_many(self, failures: Iterable[tuple[str, str]]) -> None:
        values = [(error[:2000], _url_hash(url)) for url, error in failures]
        if not values:
            return
        self.connection.executemany(
            """
            UPDATE history_queue
            SET status='failed', last_error=?, completed_at=NULL
            WHERE url_hash=?
            """,
            values,
        )
        self.connection.commit()

    def get_state(self, source: str, key: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT state_value FROM history_state
            WHERE source=? AND state_key=?
            """,
            (source, key),
        ).fetchone()
        return str(row[0]) if row else None

    def set_states(self, source: str, **values: str) -> None:
        now = _utc_now()
        self.connection.executemany(
            """
            INSERT INTO history_state(source, state_key, state_value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source, state_key) DO UPDATE SET
                state_value=excluded.state_value,
                updated_at=excluded.updated_at
            """,
            ((source, key, value, now) for key, value in values.items()),
        )
        self.connection.commit()

    def record_discovery_error(
        self,
        source: str,
        page_url: str,
        archive_key: str,
        error: str,
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO history_discovery_errors (
                source, page_url, archive_key, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, page_url) DO UPDATE SET
                attempts=history_discovery_errors.attempts+1,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
            """,
            (source, page_url, archive_key, error[:2000], _utc_now()),
        )
        row = self.connection.execute(
            """
            SELECT attempts FROM history_discovery_errors
            WHERE source=? AND page_url=?
            """,
            (source, page_url),
        ).fetchone()
        self.connection.commit()
        return int(row[0])

    def stats(self, source: str | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) FROM history_queue"
        parameters: tuple[str, ...] = ()
        if source:
            sql += " WHERE source=?"
            parameters = (source,)
        sql += " GROUP BY status"
        rows = self.connection.execute(sql, parameters).fetchall()
        result = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
        result.update({str(row[0]): int(row[1]) for row in rows})
        return result

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
