"""Durable Nasdaq-100 company-news discovery from Investing.com."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from datetime import date, datetime
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .common import make_event


SOURCE = "investing_com"
COMPONENTS_URL = "https://www.investing.com/indices/nq-100-components"
KOREAN_SEARCH_URL = "https://kr.investing.com/search/"
MIN_COMPONENTS = 90
MAX_COMPONENTS = 120


@dataclass(frozen=True, slots=True)
class EquityFeed:
    slug: str
    name: str
    overview_url: str
    news_url: str


def parse_nasdaq100_feeds(html: str) -> list[EquityFeed]:
    """Extract only the Nasdaq-100 component table, excluding sidebar stocks."""

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select("table")
    if not tables:
        raise ValueError("Nasdaq-100 component table was not found")
    table = max(tables, key=lambda item: len(item.select("a[href*='/equities/']")))
    feeds: list[EquityFeed] = []
    seen: set[str] = set()
    for link in table.select("a[href*='/equities/']"):
        overview = urljoin(COMPONENTS_URL, link.get("href", "")).split("?", 1)[0]
        path = urlsplit(overview).path.rstrip("/")
        if not path.startswith("/equities/") or path in seen:
            continue
        name = " ".join(link.get_text(" ", strip=True).split())
        slug = path.rsplit("/", 1)[-1]
        if not name or not slug:
            continue
        seen.add(path)
        overview = f"https://www.investing.com{path}"
        feeds.append(EquityFeed(slug, name, overview, f"{overview}-news"))
    if not MIN_COMPONENTS <= len(feeds) <= MAX_COMPONENTS:
        raise ValueError(
            f"Unexpected Nasdaq-100 component count: {len(feeds)} "
            f"(expected {MIN_COMPONENTS}..{MAX_COMPONENTS})"
        )
    return feeds


def _page_url(news_url: str, page: int) -> str:
    return news_url if page <= 1 else f"{news_url.rstrip('/')}/{page}"


def _ticker_from_html(html: str) -> str | None:
    heading = BeautifulSoup(html, "html.parser").select_one("h1")
    if not heading:
        return None
    match = re.search(r"\(([A-Z0-9.\-]+)\)\s*$", heading.get_text(" ", strip=True))
    return match.group(1) if match else None


def parse_kospi_feed(html: str, ticker: str, name: str) -> EquityFeed:
    """Resolve one exact KOSPI security from the Korean search results."""

    soup = BeautifulSoup(html, "html.parser")
    matches: list[tuple[str, str, str]] = []
    for link in soup.select("a.js-inner-all-results-quote-item[href*='/equities/']"):
        item_ticker = " ".join(
            (link.select_one(".second").get_text(" ", strip=True)
             if link.select_one(".second") else "").split()
        )
        item_name = " ".join(
            (link.select_one(".third").get_text(" ", strip=True)
             if link.select_one(".third") else "").split()
        )
        path = urlsplit(urljoin(KOREAN_SEARCH_URL, link.get("href", ""))).path.rstrip("/")
        if path.startswith("/equities/"):
            matches.append((item_ticker, item_name, path))
    exact = [item for item in matches if item[0] == ticker]
    if not exact:
        exact = [item for item in matches if item[1] == name]
    if len(exact) != 1:
        raise ValueError(f"Could not resolve unique KOSPI equity feed for {ticker}")
    _, resolved_name, path = exact[0]
    overview = f"https://kr.investing.com{path}"
    return EquityFeed(ticker, resolved_name or name, overview, f"{overview}-news")


def _signature(candidates) -> str:
    value = "\n".join(candidate.url for candidate in candidates)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_rate_limit(error: Exception) -> bool:
    text = str(error).casefold()
    return "http 429" in text or "too many requests" in text


class InvestingNasdaqCollector:
    """Round-robin latest and historical collection with persistent cursors."""

    def __init__(
        self,
        overseas_module,
        state_dir: str,
        outbox,
        *,
        request_delay: float = 7.0,
        cooldown_seconds: int = 1800,
        max_page: int = 1000,
        article_attempts: int = 3,
        stop_requested=None,
        client=None,
        source=None,
        database_name: str = "investing-nasdaq100.db",
    ) -> None:
        if request_delay < 0 or cooldown_seconds < 1 or max_page < 1:
            raise ValueError("Invalid Investing.com collector bounds")
        self.outbox = outbox
        self.cooldown_seconds = cooldown_seconds
        self.max_page = max_page
        self.article_attempts = article_attempts
        self.stop_requested = stop_requested or (lambda: False)
        self.client = client or overseas_module.BrowserHttpClient(
            timeout=35.0, retries=2, request_delay=request_delay
        )
        self.source = source or overseas_module.SOURCE_CLASSES["investing"](self.client)
        path = Path(state_dir) / database_name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self._ensure_schema()
        with self.db:
            self.db.execute(
                "UPDATE investing_candidates SET status='pending', "
                "attempts=max(0,attempts-1) WHERE status='processing'"
            )

    def _ensure_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS investing_feeds (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                overview_url TEXT NOT NULL,
                news_url TEXT NOT NULL,
                ticker TEXT,
                latest_checked REAL NOT NULL DEFAULT 0,
                backfill_checked REAL NOT NULL DEFAULT 0,
                backfill_page INTEGER NOT NULL DEFAULT 2,
                backfill_signature TEXT NOT NULL DEFAULT '',
                backfill_complete INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                next_try REAL NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS investing_feeds_latest
                ON investing_feeds(next_try, latest_checked);
            CREATE INDEX IF NOT EXISTS investing_feeds_backfill
                ON investing_feeds(backfill_complete, next_try, backfill_checked);

            CREATE TABLE IF NOT EXISTS investing_candidates (
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
            CREATE INDEX IF NOT EXISTS investing_candidates_work
                ON investing_candidates(status, next_try, attempts, discovered_at);

            CREATE TABLE IF NOT EXISTS investing_candidate_feeds (
                url TEXT NOT NULL,
                slug TEXT NOT NULL,
                ticker TEXT,
                PRIMARY KEY(url, slug)
            );
            CREATE TABLE IF NOT EXISTS investing_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def _state(self, key: str, default: str = "") -> str:
        row = self.db.execute(
            "SELECT value FROM investing_state WHERE key=?", (key,)
        ).fetchone()
        return str(row[0]) if row else default

    def _set_state(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO investing_state(key,value) VALUES (?,?) "
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
        count = self.db.execute("SELECT count(*) FROM investing_feeds").fetchone()[0]
        refreshed = float(self._state("components_refreshed_at", "0"))
        if count >= MIN_COMPONENTS and now - refreshed < 86400:
            return 0
        self.client.assert_robots_allowed(COMPONENTS_URL)
        feeds = parse_nasdaq100_feeds(self.client.get_text(COMPONENTS_URL))
        with self.db:
            self.db.execute("UPDATE investing_feeds SET active=0")
            self.db.executemany(
                """
                INSERT INTO investing_feeds(
                    slug,name,overview_url,news_url,updated_at
                ) VALUES (?,?,?,?,?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    overview_url=excluded.overview_url,
                    news_url=excluded.news_url,
                    active=1,
                    updated_at=excluded.updated_at
                """,
                ((x.slug, x.name, x.overview_url, x.news_url, now) for x in feeds),
            )
            self._set_state("components_refreshed_at", str(now))
            self._set_state("component_count", str(len(feeds)))
        return len(feeds)

    def _choose_feed(self, mode: str):
        now = time.time()
        if mode == "latest":
            return self.db.execute(
                "SELECT * FROM investing_feeds WHERE active=1 AND next_try<=? "
                "ORDER BY latest_checked ASC, slug ASC LIMIT 1", (now,)
            ).fetchone()
        return self.db.execute(
            "SELECT * FROM investing_feeds WHERE active=1 AND backfill_complete=0 "
            "AND next_try<=? "
            "ORDER BY backfill_checked ASC, slug ASC LIMIT 1", (now,)
        ).fetchone()

    def _discover_one(self, mode: str) -> dict:
        feed = self._choose_feed(mode)
        if feed is None:
            return {"mode": mode, "found": 0, "new": 0, "feed": None}
        page = 1 if mode == "latest" else int(feed["backfill_page"])
        url = _page_url(feed["news_url"], page)
        now = time.time()
        try:
            self.client.assert_robots_allowed(url)
            html = self.client.get_text(url)
        except Exception:
            retry = now + min(3600, 60 * (2 ** min(int(feed["failures"]), 5)))
            with self.db:
                self.db.execute(
                    "UPDATE investing_feeds SET failures=failures+1,next_try=?,"
                    "latest_checked=CASE WHEN ?='latest' THEN ? ELSE latest_checked END,"
                    "backfill_checked=CASE WHEN ?='backfill' THEN ? ELSE backfill_checked END "
                    "WHERE slug=?",
                    (retry, mode, now, mode, now, feed["slug"]),
                )
            raise
        candidates = self.source.parse_listing(html)
        ticker = _ticker_from_html(html) or feed["ticker"]
        signature = _signature(candidates)
        complete = bool(
            mode == "backfill" and (
                not candidates
                or signature == feed["backfill_signature"]
                or page >= self.max_page
            )
        )
        before = self.db.execute(
            "SELECT count(*) FROM investing_candidates"
        ).fetchone()[0]
        with self.db:
            if mode == "latest":
                self.db.execute(
                    "UPDATE investing_feeds SET ticker=?,latest_checked=?,failures=0,"
                    "next_try=0 WHERE slug=?", (ticker, now, feed["slug"])
                )
            else:
                self.db.execute(
                    "UPDATE investing_feeds SET ticker=?,backfill_checked=?,"
                    "backfill_page=?,backfill_signature=?,backfill_complete=?,"
                    "failures=0,next_try=0 WHERE slug=?",
                    (ticker, now, page if complete else page + 1, signature,
                     int(complete), feed["slug"]),
                )
            for candidate in candidates:
                self.db.execute(
                    "INSERT OR IGNORE INTO investing_candidates"
                    "(url,title,organization,discovered_at) VALUES (?,?,?,?)",
                    (candidate.url, candidate.title, candidate.organization, now),
                )
                self.db.execute(
                    "INSERT INTO investing_candidate_feeds(url,slug,ticker) VALUES (?,?,?) "
                    "ON CONFLICT(url,slug) DO UPDATE SET ticker=excluded.ticker",
                    (candidate.url, feed["slug"], ticker),
                )
        after = self.db.execute(
            "SELECT count(*) FROM investing_candidates"
        ).fetchone()[0]
        return {
            "mode": mode, "feed": feed["slug"], "ticker": ticker,
            "page": page, "found": len(candidates), "new": after - before,
            "complete": complete, "url": url,
        }

    def _candidate_metadata(self, url: str) -> dict:
        rows = self.db.execute(
            "SELECT slug,ticker FROM investing_candidate_feeds WHERE url=? "
            "ORDER BY slug", (url,)
        ).fetchall()
        return {
            "discovery_method": "investing_nasdaq100_equity_news",
            "discovery_feeds": [row["slug"] for row in rows],
            "discovery_tickers": sorted({row["ticker"] for row in rows if row["ticker"]}),
        }

    def _article_rejection(self, article) -> str | None:
        return None

    def _article_metadata(self, article, url: str) -> dict:
        return self._candidate_metadata(url)

    @property
    def event_region(self) -> str:
        return "overseas"

    @property
    def event_language(self) -> str:
        return "en"

    def _drain(self, limit: int, result: dict) -> None:
        for _ in range(limit):
            if self.stop_requested():
                return
            row = self.db.execute(
                "SELECT * FROM investing_candidates WHERE status IN ('pending','failed') "
                "AND attempts<? AND next_try<=? ORDER BY discovered_at,url LIMIT 1",
                (self.article_attempts, time.time()),
            ).fetchone()
            if row is None:
                return
            url = row["url"]
            if self.outbox.seen_url(url):
                with self.db:
                    self.db.execute(
                        "UPDATE investing_candidates SET status='done',last_error=NULL "
                        "WHERE url=?", (url,)
                    )
                result["existing"] += 1
                continue
            with self.db:
                self.db.execute(
                    "UPDATE investing_candidates SET status='processing',attempts=attempts+1 "
                    "WHERE url=?", (url,)
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
                rejection = self._article_rejection(article)
                if rejection:
                    with self.db:
                        self.db.execute(
                            "UPDATE investing_candidates SET status='done',last_error=? "
                            "WHERE url=?", (rejection, url)
                        )
                    result[rejection] = result.get(rejection, 0) + 1
                    continue
                event = make_event(
                    source=article.source, region=self.event_region,
                    language=self.event_language,
                    url=article.url, title=article.title, content=article.content,
                    organization=article.organization or "",
                    published_at=article.published_at,
                    collected_at=article.crawled_at, run_id=str(uuid.uuid4()),
                    metadata=self._article_metadata(article, url),
                )
            except Exception as exc:
                if _is_rate_limit(exc):
                    until = self._set_cooldown(exc)
                    with self.db:
                        self.db.execute(
                            "UPDATE investing_candidates SET status='pending',"
                            "attempts=max(0,attempts-1),next_try=?,last_error=? WHERE url=?",
                            (until, str(exc)[-500:], url),
                        )
                    result["rate_limited_until"] = until
                    return
                with self.db:
                    self.db.execute(
                        "UPDATE investing_candidates SET status='failed',next_try=?,"
                        "last_error=? WHERE url=?",
                        (time.time() + 900, str(exc)[-500:], url),
                    )
                result["failed"] += 1
                continue
            try:
                accepted = self.outbox.enqueue(event)
            except Exception:
                # Local outbox/Kafka infrastructure failures must not consume
                # an article retry or turn a healthy source URL into a failure.
                with self.db:
                    self.db.execute(
                        "UPDATE investing_candidates SET status='pending',"
                        "attempts=max(0,attempts-1) WHERE url=?", (url,)
                    )
                raise
            with self.db:
                self.db.execute(
                    "UPDATE investing_candidates SET status='done',last_error=NULL "
                    "WHERE url=?", (url,)
                )
            result["enqueued" if accepted else "existing"] += 1

    def collect(self, limit: int = 3) -> dict:
        if limit < 1:
            raise ValueError("limit must be at least one")
        result = {
            "source": SOURCE, "fetched": 0, "enqueued": 0,
            "existing": 0, "failed": 0, "feeds_refreshed": 0,
        }
        cooldown_until = float(self._state("rate_limit_until", "0"))
        if cooldown_until > time.time():
            result["rate_limited_until"] = cooldown_until
            result["discovery"] = None
            return self._finish(result)
        try:
            result["feeds_refreshed"] = self._refresh_feeds()
            turn = int(self._state("turn", "0"))
            # One latest page per three runs keeps fresh news flowing while two
            # runs continue the resumable historical backfill.
            mode = "latest" if turn % 3 == 0 else "backfill"
            result["discovery"] = self._discover_one(mode)
            with self.db:
                self._set_state("turn", str(turn + 1))
        except Exception as exc:
            if _is_rate_limit(exc):
                result["rate_limited_until"] = self._set_cooldown(exc)
                result["discovery"] = None
                return self._finish(result)
            result["discovery_error"] = f"{type(exc).__name__}: {str(exc)[-500:]}"
            result["discovery"] = None
        self._drain(limit, result)
        return self._finish(result)

    def _finish(self, result: dict) -> dict:
        rows = self.db.execute(
            "SELECT status,count(*) AS rows FROM investing_candidates GROUP BY status"
        ).fetchall()
        result["queue"] = {row["status"]: row["rows"] for row in rows}
        result["feed_count"] = self.db.execute(
            "SELECT count(*) FROM investing_feeds"
        ).fetchone()[0]
        result["backfill_complete_feeds"] = self.db.execute(
            "SELECT count(*) FROM investing_feeds WHERE backfill_complete=1"
        ).fetchone()[0]
        return result

    def close(self) -> None:
        self.db.close()


class InvestingKospiCollector(InvestingNasdaqCollector):
    """KOSPI-100 company-news collector using exact Korean equity feeds."""

    minimum_published_date = date(2016, 1, 1)

    def __init__(
        self,
        overseas_module,
        state_dir: str,
        outbox,
        *,
        companies_config: str,
        company_extractor=None,
        **kwargs,
    ) -> None:
        from .naver import load_companies

        self.companies = load_companies(companies_config)
        self.company_extractor = company_extractor
        super().__init__(
            overseas_module, state_dir, outbox,
            database_name="investing-kospi100.db", **kwargs,
        )

    @property
    def event_region(self) -> str:
        return "domestic"

    @property
    def event_language(self) -> str:
        return "ko"

    @staticmethod
    def _search_url(name: str) -> str:
        return f"{KOREAN_SEARCH_URL}?q={quote(name)}&tab=quotes"

    def _refresh_feeds(self) -> int:
        now = time.time()
        count = self.db.execute("SELECT count(*) FROM investing_feeds").fetchone()[0]
        refreshed = float(self._state("components_refreshed_at", "0"))
        if count == len(self.companies) and now - refreshed < 86400:
            return 0
        with self.db:
            self.db.execute("UPDATE investing_feeds SET active=0")
            self.db.executemany(
                """
                INSERT INTO investing_feeds(
                    slug,name,overview_url,news_url,ticker,updated_at
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    ticker=excluded.ticker,
                    active=1,
                    updated_at=excluded.updated_at
                """,
                ((item["ticker"], item["name"], self._search_url(item["name"]), "",
                  item["ticker"], now) for item in self.companies),
            )
            self._set_state("components_refreshed_at", str(now))
            self._set_state("component_count", str(len(self.companies)))
        return len(self.companies)

    def _resolve_feed(self, feed) -> None:
        search_url = self._search_url(feed["name"])
        self.client.assert_robots_allowed(search_url)
        resolved = parse_kospi_feed(
            self.client.get_text(search_url), feed["ticker"], feed["name"]
        )
        with self.db:
            self.db.execute(
                "UPDATE investing_feeds SET overview_url=?,news_url=?,failures=0,"
                "next_try=0,updated_at=? WHERE slug=?",
                (resolved.overview_url, resolved.news_url, time.time(), feed["slug"]),
            )

    def _discover_one(self, mode: str) -> dict:
        feed = self._choose_feed(mode)
        if feed is not None and not feed["news_url"]:
            try:
                self._resolve_feed(feed)
            except Exception:
                retry = time.time() + min(
                    3600, 60 * (2 ** min(int(feed["failures"]), 5))
                )
                with self.db:
                    self.db.execute(
                        "UPDATE investing_feeds SET failures=failures+1,next_try=? "
                        "WHERE slug=?", (retry, feed["slug"]),
                    )
                raise
        return super()._discover_one(mode)

    @staticmethod
    def _article_date(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except (ValueError, TypeError, AttributeError):
            return None

    def _extracted(self, article) -> dict | None:
        if self.company_extractor is None:
            return None
        return self.company_extractor.extract({
            "news_id": article.url,
            "title": article.title,
            "body": article.content,
            "published_date": article.published_at,
        })

    def _article_rejection(self, article) -> str | None:
        published = self._article_date(article.published_at)
        if published is not None and published < self.minimum_published_date:
            return "out_of_range"
        extracted = self._extracted(article)
        if extracted is not None and not any(
            item["market"] == "KRX" for item in extracted["companies"]
        ):
            return "irrelevant"
        return None

    def _candidate_metadata(self, url: str) -> dict:
        rows = self.db.execute(
            "SELECT f.slug,f.name,f.ticker FROM investing_candidate_feeds c "
            "JOIN investing_feeds f ON f.slug=c.slug WHERE c.url=? ORDER BY f.slug",
            (url,),
        ).fetchall()
        return {
            "discovery_method": "investing_kospi100_equity_news",
            "discovery_companies": [row["name"] for row in rows],
            "discovery_tickers": [row["ticker"] for row in rows],
        }

    def _article_metadata(self, article, url: str) -> dict:
        metadata = self._candidate_metadata(url)
        extracted = self._extracted(article)
        if extracted is None:
            return metadata
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
            "company_match_method": "explicit_title_or_body_mention",
        })
        return metadata
