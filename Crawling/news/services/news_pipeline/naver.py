"""Bounded Naver News search and durable scheduling; no article-body requests.

All processes using one Naver application must share this state database.
The local quota cannot account for consumers outside that database. API keys
are passed to the client explicitly and are never stored in scheduler state.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import ipaddress
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

API_URL = "https://openapi.naver.com/v1/search/news.json"
HUB_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
KST = timezone(timedelta(hours=9))
MAX_DAILY_BUDGET = 24_000
ERROR_KINDS = {"request_failed", "invalid_response", "response_too_large", "request_timeout",
               "redirect_rejected", "authentication_failed", "rate_limited", "api_unavailable",
               "invalid_request", "incomplete_response", "article_fetch_failed",
               "robots_denied", "body_unavailable", "body_too_short", "delivery_failed"}


class NaverApiError(RuntimeError):
    """Contains only public error categories, never provider text or headers."""
    def __init__(self, kind, *, status=None, retry_after=None):
        self.kind = kind if kind in ERROR_KINDS else "request_failed"
        self.status = status if isinstance(status, int) and not isinstance(status, bool) else None
        self.retry_after = retry_after
        super().__init__(f"naver_{self.kind}" + (f" (HTTP {self.status})" if self.status else ""))


def _integer(value, minimum, maximum):
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _companies(values, *, exact_count=None):
    if not isinstance(values, list) or not 1 <= len(values) <= 100 or (exact_count and len(values) != exact_count):
        raise ValueError("companies must contain exactly 100 entries" if exact_count else "companies must contain 1..100 entries")
    output, tickers = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("invalid company configuration")
        ticker, name = value.get("ticker"), value.get("name")
        if not isinstance(ticker, str) or not re.fullmatch(r"[0-9A-Z]{6}", ticker) or ticker in tickers:
            raise ValueError("company tickers must be unique six-character uppercase alphanumeric strings")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(ord(c) < 32 for c in name):
            raise ValueError("company name must contain 1..120 printable characters")
        tickers.add(ticker)
        output.append({"ticker": ticker, "name": unicodedata.normalize("NFC", name.strip())})
    return output


def load_companies(path):
    path = Path(path)
    if path.stat().st_size > 128 * 1024:
        raise ValueError("company configuration is too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("company configuration must be an object")
    try:
        date.fromisoformat(payload["as_of"])
    except (ValueError, KeyError, TypeError):
        raise ValueError("company configuration needs an ISO as_of date") from None
    return _companies(payload.get("companies"), exact_count=100)


def _retry_after(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(86400.0, max(0.0, seconds)) if math.isfinite(seconds) else None


def _page(payload, start, display):
    if not isinstance(payload, dict) or not _integer(payload.get("total"), 0, 2**63 - 1):
        raise NaverApiError("invalid_response")
    items = payload.get("items")
    if payload.get("start") != start or isinstance(payload.get("start"), bool):
        raise NaverApiError("invalid_response")
    if not _integer(payload.get("display"), 0, display) or not isinstance(items, list) or len(items) > display:
        raise NaverApiError("invalid_response")
    for item in items:
        if not isinstance(item, dict):
            raise NaverApiError("invalid_response")
        for name, maximum in (("title", 10000), ("description", 20000), ("link", 4096), ("originallink", 4096), ("pubDate", 200)):
            if not isinstance(item.get(name, ""), str) or len(item.get(name, "")) > maximum:
                raise NaverApiError("invalid_response")
    return {"items": items, "total": payload["total"], "start": start, "display": payload["display"]}


class NaverSearchClient:
    def __init__(self, client_id, client_secret, *, mode="openapi", session=None, timeout=15, max_response_bytes=2 * 1024 * 1024):
        if mode not in {"openapi", "hub"}:
            raise ValueError("mode must be openapi or hub")
        for value in (client_id, client_secret):
            if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
                raise ValueError("Naver application credentials are required")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be 1..120 seconds")
        if not _integer(max_response_bytes, 1024, 8 * 1024 * 1024):
            raise ValueError("max_response_bytes must be 1024..8388608")
        if session is None:
            import requests
            session = requests.Session()
            session.trust_env = False
        self.session, self.timeout, self.max_response_bytes = session, timeout, max_response_bytes
        self.mode = mode
        self._endpoint = API_URL if mode == "openapi" else HUB_API_URL
        names = ("X-Naver-Client-Id", "X-Naver-Client-Secret") if mode == "openapi" else ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")
        self._headers = {names[0]: client_id, names[1]: client_secret, "Accept": "application/json"}
        self.application_fingerprint = hashlib.sha256(f"{mode}:{client_id}".encode("utf-8")).hexdigest()

    def search(self, query, *, start=1, display=100):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("query must contain 1..200 characters")
        if not _integer(start, 1, 1000) or not _integer(display, 1, 100):
            raise ValueError("start must be 1..1000 and display must be 1..100")
        response = None
        began = time.monotonic()
        try:
            params = {"query": query, "sort": "date", "display": display, "start": start}
            if self.mode == "hub":
                params["format"] = "json"
            response = self.session.get(self._endpoint, params=params,
                headers=self._headers.copy(), timeout=(min(5, self.timeout), self.timeout),
                allow_redirects=False, stream=True)
            status = response.status_code
            if 300 <= status < 400:
                raise NaverApiError("redirect_rejected", status=status)
            if status != 200:
                kind = ("authentication_failed" if status in (401, 403) else "rate_limited" if status == 429
                        else "api_unavailable" if status >= 500 else "invalid_request")
                raise NaverApiError(kind, status=status, retry_after=_retry_after(response.headers.get("Retry-After")))
            length = response.headers.get("Content-Length", "")
            if str(length).isdigit() and int(length) > self.max_response_bytes:
                raise NaverApiError("response_too_large")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                if time.monotonic() - began > self.timeout:
                    raise NaverApiError("request_timeout")
                body.extend(chunk)
                if len(body) > self.max_response_bytes:
                    raise NaverApiError("response_too_large")
            try:
                payload = json.loads(body.decode("utf-8-sig"))
            except (ValueError, UnicodeError, RecursionError):
                raise NaverApiError("invalid_response") from None
            return _page(payload, start, display)
        except NaverApiError:
            raise
        except Exception:
            # requests errors may embed authentication headers or request data.
            raise NaverApiError("request_failed") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def close(self):
        self.session.close()


def _url(value):
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
            raise ValueError
        if host.lower() == "localhost" or host.lower().endswith(".localhost"):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        port = parts.port
        if port and port not in (80, 443):
            raise ValueError
        query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))
    except (ValueError, TypeError, AttributeError):
        return None


def _candidate(item):
    original, naver_url = _url(item.get("originallink", "")), _url(item.get("link", ""))
    if not (original or naver_url):
        raise NaverApiError("invalid_response")
    try:
        published = parsedate_to_datetime(item.get("pubDate", ""))
        if published.tzinfo is None:
            raise ValueError
        stamp = published.timestamp()
        published_at = published.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        stamp, published_at = None, None
    clean = lambda value: html.unescape(re.sub(r"<[^>]*>", "", value)).strip()
    return {"url": original or naver_url, "naver_url": naver_url, "title": clean(item.get("title", "")),
            "description": clean(item.get("description", "")), "published_at": published_at, "published_ts": stamp}


class NaverStore:
    def __init__(self, path, companies, *, poll_seconds=600, daily_budget=MAX_DAILY_BUDGET, now=time.time):
        self.companies = _companies(companies)
        if not isinstance(poll_seconds, (int, float)) or not math.isfinite(poll_seconds) or poll_seconds < 1:
            raise ValueError("poll_seconds must be positive")
        if not _integer(daily_budget, 1, MAX_DAILY_BUDGET):
            raise ValueError("daily_budget must be 1..24000")
        self.poll_seconds, self.daily_budget, self.now = poll_seconds, daily_budget, now
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS budget (day TEXT PRIMARY KEY,used INTEGER NOT NULL,limit_value INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS runtime (id INTEGER PRIMARY KEY CHECK(id=1),not_before REAL NOT NULL DEFAULT 0,
            last_kind TEXT NOT NULL DEFAULT 'new');
          INSERT OR IGNORE INTO runtime(id) VALUES(1);
          CREATE TABLE IF NOT EXISTS searches (
            ticker TEXT PRIMARY KEY,name TEXT NOT NULL,ordinal INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,
            watermark REAL, gap_floor REAL, cycle_lower REAL, cycle_upper REAL, next_start INTEGER NOT NULL DEFAULT 1,
            next_due REAL NOT NULL,last_request REAL NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'idle',
            failures INTEGER NOT NULL DEFAULT 0,last_error TEXT,overflow_count INTEGER NOT NULL DEFAULT 0,
            lease_token TEXT,lease_until REAL NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS candidates (
            url TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL,naver_url TEXT,published_at TEXT,
            first_seen REAL NOT NULL,last_seen REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,next_try REAL NOT NULL DEFAULT 0,last_error TEXT);
          CREATE INDEX IF NOT EXISTS candidates_ready ON candidates(status,next_try,first_seen);
          CREATE TABLE IF NOT EXISTS candidate_queries (
            url TEXT NOT NULL REFERENCES candidates(url),ticker TEXT NOT NULL,name TEXT NOT NULL,
            PRIMARY KEY(url,ticker));
        """)
        with self._transaction():
            self.db.execute("UPDATE searches SET active=0")
            for index, company in enumerate(self.companies):
                self.db.execute("""INSERT INTO searches(ticker,name,ordinal,next_due) VALUES(?,?,?,?)
                    ON CONFLICT(ticker) DO UPDATE SET name=excluded.name,ordinal=excluded.ordinal,active=1""",
                    (company["ticker"], company["name"], index, self.now() + index * poll_seconds / len(self.companies)))

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _day(self, now):
        local = datetime.fromtimestamp(now, KST)
        tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), KST)
        return local.date().isoformat(), tomorrow.timestamp()

    def _reserve(self, client):
        now = self.now()
        day, reset = self._day(now)
        fingerprint = getattr(client, "application_fingerprint", None)
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("search client must identify its application_fingerprint")
        with self._transaction():
            current = self.db.execute("SELECT value FROM config WHERE key='application_fingerprint'").fetchone()
            if current and current[0] != fingerprint:
                raise ValueError("state database belongs to a different Naver application")
            self.db.execute("INSERT OR IGNORE INTO config VALUES('application_fingerprint',?)", (fingerprint,))
            self.db.execute("INSERT OR IGNORE INTO budget VALUES(?,0,?)", (day, self.daily_budget))
            self.db.execute("UPDATE budget SET limit_value=min(limit_value,?) WHERE day=?", (self.daily_budget, day))
            budget = self.db.execute("SELECT * FROM budget WHERE day=?", (day,)).fetchone()
            if budget["used"] >= budget["limit_value"]:
                return {"status": "quota_exhausted", "retry_at": reset, "requests_today": budget["used"]}
            runtime = self.db.execute("SELECT * FROM runtime WHERE id=1").fetchone()
            if now < runtime["not_before"]:
                return {"status": "throttled", "retry_at": runtime["not_before"]}
            clause = "active=1 AND next_due<=? AND lease_until<=?"
            paging = self.db.execute(f"SELECT * FROM searches WHERE {clause} AND cycle_upper IS NOT NULL ORDER BY last_request,ordinal LIMIT 1", (now, now)).fetchone()
            fresh = self.db.execute(f"SELECT * FROM searches WHERE {clause} AND cycle_upper IS NULL ORDER BY next_due,ordinal LIMIT 1", (now, now)).fetchone()
            chosen = (fresh if runtime["last_kind"] == "page" else paging) if fresh and paging else fresh or paging
            if chosen is None:
                due = self.db.execute("SELECT min(max(next_due,lease_until)) FROM searches WHERE active=1").fetchone()[0]
                return {"status": "idle", "retry_at": due or now + self.poll_seconds}
            row = dict(chosen)
            if row["cycle_upper"] is None:
                lower = row["watermark"] - 6 * 3600 if row["watermark"] is not None else now - 24 * 3600
                if row["gap_floor"] is not None:
                    lower = min(lower, row["gap_floor"])
                row.update(cycle_lower=lower, cycle_upper=now, next_start=1)
            token = uuid.uuid4().hex
            self.db.execute("UPDATE budget SET used=used+1 WHERE day=?", (day,))
            self.db.execute("UPDATE runtime SET not_before=?,last_kind=? WHERE id=1", (now + 1, "page" if paging is chosen else "new"))
            self.db.execute("""UPDATE searches SET cycle_lower=?,cycle_upper=?,next_start=?,lease_token=?,
                lease_until=?,last_request=?,status='requesting' WHERE ticker=?""",
                (row["cycle_lower"], row["cycle_upper"], row["next_start"], token, now + 150, now, row["ticker"]))
            row.update(lease_token=token, requests_today=budget["used"] + 1)
            return row

    def discover_one(self, client):
        row = self._reserve(client)
        if "lease_token" not in row:
            return row
        start, display = row["next_start"], min(100, 1001 - row["next_start"])
        try:
            page = _page(client.search(row["name"], start=start, display=display), start, display)
            candidates = [_candidate(item) for item in page["items"]]
            if not candidates and page["total"] >= start:
                raise NaverApiError("incomplete_response")
        except Exception as exc:
            error = exc if isinstance(exc, NaverApiError) else NaverApiError("request_failed")
            return self._failure(row, error)
        return self._commit_page(row, page, candidates)

    def _failure(self, row, error):
        now, failures = self.now(), min(row["failures"] + 1, 12)
        minimum = 3600 if error.kind == "authentication_failed" else 60 if error.kind == "rate_limited" else 30
        delay = min(3600, minimum * 2 ** (failures - 1))
        if error.retry_after is not None:
            delay = max(delay, error.retry_after)
        retry = now + delay
        with self._transaction():
            changed = self.db.execute("""UPDATE searches SET failures=?,last_error=?,status='error',next_due=?,
                lease_token=NULL,lease_until=0 WHERE ticker=? AND lease_token=?""",
                (failures, error.kind, retry, row["ticker"], row["lease_token"])).rowcount
            if changed and error.kind != "invalid_request":
                self.db.execute("UPDATE runtime SET not_before=max(not_before,?) WHERE id=1", (retry,))
        return {"status": "error" if changed else "stale_response", "ticker": row["ticker"],
                "error": error.kind, "retry_at": retry, "requests_today": row["requests_today"]}

    def _commit_page(self, row, page, candidates):
        now, added = self.now(), 0
        stamps = [item["published_ts"] for item in candidates]
        ordered = bool(stamps) and all(stamp is not None for stamp in stamps) and all(a >= b for a, b in zip(stamps, stamps[1:]))
        crossed_floor = ordered and stamps[-1] < row["cycle_lower"]
        next_start = row["next_start"] + len(candidates)
        complete = crossed_floor or next_start > page["total"]
        overflow = not complete and next_start > 1000
        with self._transaction():
            lease = self.db.execute("SELECT lease_token FROM searches WHERE ticker=?", (row["ticker"],)).fetchone()
            if lease[0] != row["lease_token"]:
                return {"status": "stale_response", "ticker": row["ticker"]}
            for item in candidates:
                if item["published_ts"] is not None and item["published_ts"] < row["cycle_lower"]:
                    continue
                added += self.db.execute("""INSERT OR IGNORE INTO candidates
                    (url,title,description,naver_url,published_at,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)""",
                    (item["url"], item["title"], item["description"], item["naver_url"], item["published_at"], now, now)).rowcount
                self.db.execute("UPDATE candidates SET last_seen=? WHERE url=?", (now, item["url"]))
                self.db.execute("INSERT OR IGNORE INTO candidate_queries VALUES(?,?,?)", (item["url"], row["ticker"], row["name"]))
            status = "complete" if complete else "overflow" if overflow else "paging"
            if complete or overflow:
                watermark = max(row["watermark"] or 0, row["cycle_upper"]) if complete else row["watermark"]
                self.db.execute("""UPDATE searches SET status=?,watermark=?,gap_floor=?,cycle_lower=NULL,
                    cycle_upper=NULL,next_start=1,next_due=?,failures=0,last_error=NULL,lease_token=NULL,lease_until=0,
                    overflow_count=overflow_count+? WHERE ticker=?""",
                    (status, watermark, None if complete else row["cycle_lower"], now + self.poll_seconds, int(overflow), row["ticker"]))
            else:
                self.db.execute("""UPDATE searches SET status='paging',next_start=?,next_due=?,failures=0,
                    last_error=NULL,lease_token=NULL,lease_until=0 WHERE ticker=?""", (next_start, now + 1, row["ticker"]))
        return {"status": status, "ticker": row["ticker"], "received": len(candidates), "enqueued": added,
                "next_start": None if complete or overflow else next_start, "watermark_advanced": complete,
                "requests_today": row["requests_today"], "retry_at": now + self.poll_seconds if complete or overflow else now + 1}

    def pending(self, limit=8):
        """Newest articles first; reserve a quarter of each batch for retries.

        With a one-item batch, alternate new work and due retries. This cursor
        survives restarts. Rows are not claimed, so delivery needs its own lock.
        Unknown publication dates use discovery time as their priority.
        """
        if not _integer(limit, 1, 1000):
            raise ValueError("limit must be 1..1000")
        with self._transaction():
            fresh = self.db.execute("""SELECT * FROM candidates WHERE status='pending'
                AND attempts<5 AND next_try<=? ORDER BY
                coalesce(julianday(published_at),2440587.5+first_seen/86400.0) DESC,first_seen DESC,url LIMIT ?""",
                (self.now(), limit)).fetchall()
            retries = self.db.execute("""SELECT * FROM candidates WHERE status='failed'
                AND attempts<5 AND next_try<=? ORDER BY next_try,first_seen,url LIMIT ?""",
                (self.now(), limit)).fetchall()
            if fresh and retries:
                if limit == 1:
                    saved = self.db.execute("SELECT value FROM config WHERE key='delivery_turn'").fetchone()
                    turn = int(saved[0]) if saved else 0
                    rows = fresh if turn % 2 == 0 else retries
                    self.db.execute("INSERT OR REPLACE INTO config VALUES('delivery_turn',?)", (str(turn + 1),))
                else:
                    retry_count = min(len(retries), max(1, limit // 4))
                    fresh_count = min(len(fresh), limit - retry_count)
                    rows = fresh[:fresh_count] + retries[:min(len(retries), limit - fresh_count)]
            else:
                rows = fresh or retries
        output = []
        for row in rows:
            result = dict(row)
            companies = [dict(item) for item in self.db.execute("SELECT ticker,name FROM candidate_queries WHERE url=? ORDER BY ticker", (row["url"],))]
            result["query_tickers"] = [company["ticker"] for company in companies]
            result["query_companies"] = companies
            output.append(result)
        return output

    def mark_done(self, url, status="done"):
        if not isinstance(status, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", status) or status in {"pending", "failed", "processing"}:
            raise ValueError("status must be a terminal candidate category")
        with self._transaction():
            self.db.execute("UPDATE candidates SET status=?,last_error=NULL WHERE url=?", (status, url))

    def mark_failed(self, url, error):
        code = error.kind if isinstance(error, NaverApiError) else error if isinstance(error, str) and error in ERROR_KINDS else "article_fetch_failed"
        with self._transaction():
            row = self.db.execute("SELECT attempts FROM candidates WHERE url=?", (url,)).fetchone()
            if row:
                attempts = row[0] + 1
                self.db.execute("UPDATE candidates SET status='failed',attempts=?,last_error=?,next_try=? WHERE url=?",
                    (attempts, code, self.now() + min(3600, 60 * 2 ** min(attempts - 1, 6)), url))

    def stats(self):
        day, reset = self._day(self.now())
        budget = self.db.execute("SELECT used,limit_value FROM budget WHERE day=?", (day,)).fetchone()
        return {"candidates": {row[0]: row[1] for row in self.db.execute("SELECT status,count(*) FROM candidates GROUP BY status")},
                "exhausted_candidates": self.db.execute("SELECT count(*) FROM candidates WHERE status='failed' AND attempts>=5").fetchone()[0],
                "requests_today": budget[0] if budget else 0, "daily_budget": budget[1] if budget else self.daily_budget,
                "quota_day_kst": day, "quota_reset_at": reset,
                "next_request_at": self.db.execute("SELECT not_before FROM runtime WHERE id=1").fetchone()[0],
                "companies": [dict(row) for row in self.db.execute("""SELECT ticker,name,status,next_due,next_start,
                    watermark,gap_floor,failures,last_error,overflow_count FROM searches WHERE active=1 ORDER BY ordinal""")]}

    def close(self):
        self.db.close()
