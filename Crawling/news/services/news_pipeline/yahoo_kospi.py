"""Durable KOSPI-100 company-news collection from Yahoo Finance quote pages."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import time
import uuid

from .common import make_event
from .naver import load_companies


SOURCE = "yahoo_finance"
MINIMUM_PUBLISHED_DATE = date(2016, 1, 1)


def yahoo_kospi_news_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}.KS/news/"


def _article_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (ValueError, TypeError, AttributeError):
        return None


def _is_rate_limit(error: Exception) -> bool:
    value = str(error).casefold()
    return "http 429" in value or "too many requests" in value


class YahooKospiCollector:
    """Round-robin latest collection for all configured KOSPI-100 symbols."""

    def __init__(
        self,
        overseas_module,
        state_dir: str,
        outbox,
        *,
        companies_config: str,
        company_extractor=None,
        request_delay: float = 4.5,
        cooldown_seconds: int = 1800,
        article_attempts: int = 3,
        stop_requested=None,
        client=None,
        source=None,
    ) -> None:
        if request_delay < 0 or cooldown_seconds < 1 or article_attempts < 1:
            raise ValueError("Invalid Yahoo KOSPI collector bounds")
        self.companies = load_companies(companies_config)
        self.company_extractor = company_extractor
        self.outbox = outbox
        self.cooldown_seconds = cooldown_seconds
        self.article_attempts = article_attempts
        self.stop_requested = stop_requested or (lambda: False)
        self.client = client or overseas_module.BrowserHttpClient(
            timeout=35.0, retries=2, request_delay=request_delay
        )
        self.source = source or overseas_module.SOURCE_CLASSES["yahoo"](self.client)
        path = Path(state_dir) / "yahoo-kospi100.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self._ensure_schema()
        with self.db:
            self.db.execute(
                "UPDATE yahoo_kospi_candidates SET status='pending',"
                "attempts=max(0,attempts-1) WHERE status='processing'"
            )

    def _ensure_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS yahoo_kospi_feeds (
                ticker TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                news_url TEXT NOT NULL,
                checked_at REAL NOT NULL DEFAULT 0,
                next_try REAL NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS yahoo_kospi_feeds_work
                ON yahoo_kospi_feeds(active,next_try,checked_at,ticker);

            CREATE TABLE IF NOT EXISTS yahoo_kospi_candidates (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                organization TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','processing','done','failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_try REAL NOT NULL DEFAULT 0,
                last_error TEXT,
                discovered_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS yahoo_kospi_candidates_work
                ON yahoo_kospi_candidates(status,next_try,attempts,discovered_at);

            CREATE TABLE IF NOT EXISTS yahoo_kospi_candidate_feeds (
                url TEXT NOT NULL,
                ticker TEXT NOT NULL,
                PRIMARY KEY(url,ticker)
            );
            CREATE TABLE IF NOT EXISTS yahoo_kospi_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def _state(self, key: str, default: str = "") -> str:
        row = self.db.execute(
            "SELECT value FROM yahoo_kospi_state WHERE key=?", (key,)
        ).fetchone()
        return str(row[0]) if row else default

    def _set_state(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO yahoo_kospi_state(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def _set_cooldown(self, error: Exception) -> float:
        until = time.time() + self.cooldown_seconds
        with self.db:
            self._set_state("rate_limit_until", str(until))
            self._set_state("rate_limit_error", str(error)[-500:])
        return until

    def _refresh_feeds(self) -> int:
        now = time.time()
        count = self.db.execute("SELECT count(*) FROM yahoo_kospi_feeds").fetchone()[0]
        refreshed = float(self._state("feeds_refreshed_at", "0"))
        if count == len(self.companies) and now - refreshed < 86400:
            return 0
        with self.db:
            self.db.execute("UPDATE yahoo_kospi_feeds SET active=0")
            self.db.executemany(
                """
                INSERT INTO yahoo_kospi_feeds(ticker,name,news_url,updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name,
                    news_url=excluded.news_url,
                    active=1,
                    updated_at=excluded.updated_at
                """,
                ((item["ticker"], item["name"], yahoo_kospi_news_url(item["ticker"]), now)
                 for item in self.companies),
            )
            self._set_state("feeds_refreshed_at", str(now))
            self._set_state("feed_count", str(len(self.companies)))
        return len(self.companies)

    def _discover_one(self) -> dict:
        now = time.time()
        feed = self.db.execute(
            "SELECT * FROM yahoo_kospi_feeds WHERE active=1 AND next_try<=? "
            "ORDER BY checked_at,ticker LIMIT 1", (now,),
        ).fetchone()
        if feed is None:
            return {"ticker": None, "found": 0, "new": 0}
        try:
            self.client.assert_robots_allowed(feed["news_url"])
            candidates = self.source.parse_listing(self.client.get_text(feed["news_url"]))
        except Exception:
            retry = now + min(3600, 60 * (2 ** min(int(feed["failures"]), 5)))
            with self.db:
                self.db.execute(
                    "UPDATE yahoo_kospi_feeds SET failures=failures+1,next_try=?,"
                    "checked_at=? WHERE ticker=?", (retry, now, feed["ticker"]),
                )
            raise
        before = self.db.execute(
            "SELECT count(*) FROM yahoo_kospi_candidates"
        ).fetchone()[0]
        with self.db:
            self.db.execute(
                "UPDATE yahoo_kospi_feeds SET checked_at=?,next_try=0,failures=0 "
                "WHERE ticker=?", (now, feed["ticker"]),
            )
            for candidate in candidates:
                self.db.execute(
                    "INSERT OR IGNORE INTO yahoo_kospi_candidates"
                    "(url,title,organization,discovered_at) VALUES (?,?,?,?)",
                    (candidate.url, candidate.title, candidate.organization, now),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO yahoo_kospi_candidate_feeds(url,ticker) "
                    "VALUES (?,?)", (candidate.url, feed["ticker"]),
                )
        after = self.db.execute(
            "SELECT count(*) FROM yahoo_kospi_candidates"
        ).fetchone()[0]
        return {
            "ticker": feed["ticker"], "name": feed["name"],
            "url": feed["news_url"], "found": len(candidates), "new": after - before,
        }

    def _metadata(self, article, url: str) -> dict:
        rows = self.db.execute(
            "SELECT f.ticker,f.name FROM yahoo_kospi_candidate_feeds c "
            "JOIN yahoo_kospi_feeds f ON f.ticker=c.ticker "
            "WHERE c.url=? ORDER BY f.ticker", (url,),
        ).fetchall()
        metadata = {
            "discovery_method": "yahoo_kospi100_quote_news",
            "discovery_tickers": [row["ticker"] for row in rows],
            "discovery_companies": [row["name"] for row in rows],
        }
        if self.company_extractor is None:
            return metadata
        extracted = self.company_extractor.extract({
            "news_id": article.url, "title": article.title,
            "body": article.content, "published_date": article.published_at,
        })
        companies = [item for item in extracted["companies"] if item["market"] == "KRX"]
        metadata.update({
            "kospi100_companies": [{
                "company_id": item["company_id"], "name": item["name"],
                "ticker": item["ticker"], "tickers": item["tickers"],
                "explicit_tickers": item["explicit_tickers"],
                "resolved_level": item["resolved_level"],
                "mention_count": item["n_mentions"],
            } for item in companies],
            "universe_version": extracted["universe_version"],
            "registry_sha256": extracted["registry_sha256"],
            "company_extractor_version": extracted["extractor_version"],
            "membership_policy": extracted["membership_policy"],
            "company_match_method": "quote_feed_and_explicit_title_or_body_mention",
        })
        return metadata

    def _drain(self, limit: int, result: dict) -> None:
        for _ in range(limit):
            if self.stop_requested():
                return
            row = self.db.execute(
                "SELECT * FROM yahoo_kospi_candidates "
                "WHERE status IN ('pending','failed') AND attempts<? AND next_try<=? "
                "ORDER BY discovered_at,url LIMIT 1",
                (self.article_attempts, time.time()),
            ).fetchone()
            if row is None:
                return
            url = row["url"]
            if self.outbox.seen_url(url):
                with self.db:
                    self.db.execute(
                        "UPDATE yahoo_kospi_candidates SET status='done',last_error=NULL "
                        "WHERE url=?", (url,),
                    )
                result["existing"] += 1
                continue
            with self.db:
                self.db.execute(
                    "UPDATE yahoo_kospi_candidates SET status='processing',"
                    "attempts=attempts+1 WHERE url=?", (url,),
                )
            candidate = SimpleNamespace(
                source=SOURCE, url=url, title=row["title"],
                organization=row["organization"],
            )
            try:
                self.client.assert_robots_allowed(url)
                article = self.source.parse_article(self.client.get_text(url), candidate)
                if len(article.content) < self.source.minimum_content_chars:
                    raise ValueError("article body too short")
                result["fetched"] += 1
                published = _article_date(article.published_at)
                if published is not None and published < MINIMUM_PUBLISHED_DATE:
                    with self.db:
                        self.db.execute(
                            "UPDATE yahoo_kospi_candidates SET status='done',"
                            "last_error='out_of_range' WHERE url=?", (url,),
                        )
                    result["out_of_range"] += 1
                    continue
                event = make_event(
                    source=article.source, region="overseas", language="en",
                    url=article.url, title=article.title, content=article.content,
                    organization=article.organization or "",
                    published_at=article.published_at,
                    collected_at=article.crawled_at, run_id=str(uuid.uuid4()),
                    metadata=self._metadata(article, url),
                )
            except Exception as exc:
                if _is_rate_limit(exc):
                    until = self._set_cooldown(exc)
                    with self.db:
                        self.db.execute(
                            "UPDATE yahoo_kospi_candidates SET status='pending',"
                            "attempts=max(0,attempts-1),next_try=?,last_error=? WHERE url=?",
                            (until, str(exc)[-500:], url),
                        )
                    result["rate_limited_until"] = until
                    return
                with self.db:
                    self.db.execute(
                        "UPDATE yahoo_kospi_candidates SET status='failed',next_try=?,"
                        "last_error=? WHERE url=?",
                        (time.time() + 900, str(exc)[-500:], url),
                    )
                result["failed"] += 1
                continue
            try:
                accepted = self.outbox.enqueue(event)
            except Exception:
                with self.db:
                    self.db.execute(
                        "UPDATE yahoo_kospi_candidates SET status='pending',"
                        "attempts=max(0,attempts-1) WHERE url=?", (url,),
                    )
                raise
            with self.db:
                self.db.execute(
                    "UPDATE yahoo_kospi_candidates SET status='done',last_error=NULL "
                    "WHERE url=?", (url,),
                )
            result["enqueued" if accepted else "existing"] += 1

    def collect(self, limit: int = 3) -> dict:
        if limit < 1:
            raise ValueError("limit must be at least one")
        result = {
            "source": SOURCE, "mode": "kospi100_quote_news", "fetched": 0,
            "enqueued": 0, "existing": 0, "out_of_range": 0, "failed": 0,
            "feeds_refreshed": 0,
        }
        cooldown = float(self._state("rate_limit_until", "0"))
        if cooldown > time.time():
            result["rate_limited_until"] = cooldown
            result["discovery"] = None
            return self._finish(result)
        try:
            result["feeds_refreshed"] = self._refresh_feeds()
            result["discovery"] = self._discover_one()
        except Exception as exc:
            if _is_rate_limit(exc):
                result["rate_limited_until"] = self._set_cooldown(exc)
            else:
                result["discovery_error"] = f"{type(exc).__name__}: {str(exc)[-500:]}"
            result["discovery"] = None
        self._drain(limit, result)
        return self._finish(result)

    def _finish(self, result: dict) -> dict:
        rows = self.db.execute(
            "SELECT status,count(*) AS rows FROM yahoo_kospi_candidates GROUP BY status"
        ).fetchall()
        result["queue"] = {row["status"]: row["rows"] for row in rows}
        result["feed_count"] = self.db.execute(
            "SELECT count(*) FROM yahoo_kospi_feeds WHERE active=1"
        ).fetchone()[0]
        return result

    def close(self) -> None:
        self.db.close()
