"""Full-body delivery of Naver-discovered URLs into the existing news contract.

Search descriptions stay metadata. Failed full-body downloads remain retryable;
they must never be silently relabelled as full news articles.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
import os
import re
import subprocess
import unicodedata
from urllib.parse import urlsplit

from .common import make_event


def normalized_text(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).casefold().strip()


def publication_time(article, candidate):
    for source, raw in (("publisher", article.get("published_at")), ("naver", candidate.get("published_at"))):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if value.tzinfo is None:
                continue
            return value.astimezone(timezone.utc).isoformat(), source
        except (ValueError, AttributeError, TypeError):
            continue
    return None, "unknown"


def company_mentions(companies, **fields):
    """Literal company names, with Korean particles but not subsidiary suffixes."""
    texts = {field: normalized_text(text) for field, text in fields.items()}
    alphabet = "0-9a-z가-힣"
    particles = "으로|에서|에게|부터|까지|보다|처럼|은|는|이|가|을|를|의|와|과|도|에|로|만"
    matches = []
    for company in companies:
        name = normalized_text(company["name"])
        pattern = re.compile(rf"(?<![{alphabet}]){re.escape(name)}(?=$|[^{alphabet}]|(?:{particles})(?=$|[^{alphabet}]))")
        evidence = [field for field, text in texts.items() if pattern.search(text)]
        if evidence:
            matches.append({"ticker": company["ticker"], "name": company["name"], "fields": evidence})
    return matches


class ArticleFetcher:
    def __init__(self, node, script):
        self.node, self.script = str(node), str(script)

    def __call__(self, url):
        # Naver credentials go only to its fixed HTTPS API endpoint, never to
        # the publisher-fetching child process.
        environment = {key: value for key, value in os.environ.items()
                       if not (key.upper().startswith("NAVER_") and
                               any(token in key.upper() for token in ("SECRET", "CLIENT", "KEY", "TOKEN")))}
        result = subprocess.run([self.node, self.script, "--url", url],
                                capture_output=True, text=True, encoding="utf-8",
                                timeout=60, env=environment)
        if result.returncode:
            try:
                error = json.loads(result.stdout).get("error", "article_fetch_failed")
            except (ValueError, AttributeError):
                error = "article_fetch_failed"
            if not isinstance(error, str) or not re.fullmatch(r"[a-z][a-z0-9_:-]{0,119}", error, re.I):
                error = "article_fetch_failed"
            raise RuntimeError(error)
        article = json.loads(result.stdout)
        if not isinstance(article, dict) or len(article.get("content", "").strip()) < 80:
            raise ValueError("article_content_too_short")
        return article


class NaverDelivery:
    def __init__(self, store, companies, outbox, fetch_article, stop_requested=lambda: False,
                 after_enqueue=lambda: None):
        self.store, self.companies, self.outbox = store, companies, outbox
        self.fetch_article, self.stop_requested = fetch_article, stop_requested
        self.after_enqueue = after_enqueue

    def fetch_candidate(self, candidate):
        try:
            return self.fetch_article(candidate["url"])
        except Exception:
            naver_url = candidate.get("naver_url") or ""
            parts = urlsplit(naver_url)
            if (naver_url != candidate["url"] and parts.scheme == "https" and
                    parts.hostname in {"n.news.naver.com", "news.naver.com"} and
                    not parts.username and not parts.password and parts.port in (None, 443)):
                return self.fetch_article(naver_url)
            raise

    def collect(self, limit=16):
        counts = dict(enqueued=0, existing=0, filtered=0, failed=0)
        for candidate in self.store.pending(limit):
            if self.stop_requested():
                break
            url = candidate["url"]
            if self.outbox.seen_url(url):
                self.store.mark_done(url)
                counts["existing"] += 1
                continue
            try:
                article = self.fetch_candidate(candidate)
                if not isinstance(article.get("content"), str) or len(article["content"].strip()) < 80:
                    raise ValueError("article_content_too_short")
                mentions = company_mentions(self.companies, title=article.get("title"), content=article.get("content"))
                if not mentions:
                    self.store.mark_done(url, status="filtered")
                    counts["filtered"] += 1
                    continue
                published_at, time_source = publication_time(article, candidate)
                metadata = {
                    "collection_method": "naver_search_api", "content_kind": "full_article",
                    "matched_companies": mentions, "query_tickers": candidate.get("query_tickers", []),
                    "search_title": candidate.get("title"), "search_description": candidate.get("description"),
                    "naver_url": candidate.get("naver_url"), "naver_provided_at": candidate.get("published_at"),
                    "publisher_published_at_original": article.get("published_at"), "publication_time_source": time_source,
                    **{key: article.get(key) for key in ("final_url", "extraction_method", "truncated", "robots")},
                }
                event = make_event(source="naver_news_search", region="domestic", language="ko",
                    url=url, title=article.get("title") or candidate["title"], content=article["content"],
                    organization=article.get("organization") or urlsplit(url).hostname or "",
                    published_at=published_at, metadata=metadata)
            except Exception as error:
                # Do not persist arbitrary response bodies, request headers, or
                # subprocess diagnostics. The durable URL is already in the queue.
                self.store.mark_failed(url, type(error).__name__)
                counts["failed"] += 1
                continue
            # An outbox/database failure does not consume the download retry
            # budget. If the process dies after enqueue, its unique URL makes
            # the following retry idempotent.
            added = self.outbox.enqueue(event)
            self.store.mark_done(url)
            counts["enqueued" if added else "existing"] += 1
            if added:
                self.after_enqueue()
        return counts
