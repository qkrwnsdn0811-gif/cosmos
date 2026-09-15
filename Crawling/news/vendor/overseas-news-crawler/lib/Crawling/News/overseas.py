"""Yahoo Finance / Investing.com overseas news crawler.

The original project crawlers depend on CSS class names and on the project's
company/tag tables.  This module keeps the crawling part independent so that
news bodies can be collected and verified before they are sent to the legacy
distribution pipeline.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as http_requests

    CURL_CFFI_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback for minimal environments
    import requests as http_requests

    CURL_CFFI_AVAILABLE = False


LOGGER = logging.getLogger(__name__)

CRAWLER_USER_AGENT = (
    "Mozilla/5.0 (compatible; OverseasNewsCrawler/1.0; "
    "+https://github.com/wildyoung/overseas-news-crawler)"
)


class CrawlError(RuntimeError):
    """Raised when a page cannot be fetched or parsed as a news article."""


class RobotsDenied(CrawlError):
    """Raised when a site's robots.txt explicitly disallows a URL."""


@dataclass(frozen=True, slots=True)
class NewsCandidate:
    source: str
    title: str
    url: str
    organization: str | None = None


@dataclass(frozen=True, slots=True)
class NewsArticle:
    source: str
    organization: str
    title: str
    url: str
    content: str
    author: str | None = None
    published_at: str | None = None
    crawled_at: str = ""

    def __post_init__(self) -> None:
        if not self.crawled_at:
            object.__setattr__(
                self,
                "crawled_at",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )


def canonicalize_url(url: str) -> str:
    """Remove fragments and tracking query parameters used on news links."""

    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _iter_json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_objects(child)


def extract_news_json_ld(soup: BeautifulSoup) -> dict:
    """Return the first NewsArticle/Article object from JSON-LD."""

    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue

        for item in _iter_json_objects(payload):
            item_type = item.get("@type")
            item_types = item_type if isinstance(item_type, list) else [item_type]
            if any(kind in {"NewsArticle", "Article"} for kind in item_types):
                return item
    return {}


def _schema_name(value) -> str | None:
    if isinstance(value, str):
        return normalize_text(value) or None
    if isinstance(value, list):
        for item in value:
            name = _schema_name(item)
            if name:
                return name
    if isinstance(value, dict):
        return normalize_text(value.get("name")) or None
    return None


def _first_text(soup: BeautifulSoup, selectors: Iterable[str]) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            value = normalize_text(element.get_text(" ", strip=True))
            if value:
                return value
    return None


def _first_attribute(
    soup: BeautifulSoup, selectors: Iterable[str], attribute: str
) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if element and element.get(attribute):
            return normalize_text(element.get(attribute)) or None
    return None


def _paragraph_text(soup: BeautifulSoup, selectors: Iterable[str]) -> str:
    """Use the first matching article container and preserve paragraphs."""

    for selector in selectors:
        paragraphs: list[str] = []
        seen: set[str] = set()
        for element in soup.select(selector):
            text = normalize_text(element.get_text(" ", strip=True))
            if not text or text in seen:
                continue
            if text.lower() in {"read more", "show more"}:
                continue
            seen.add(text)
            paragraphs.append(text)
        if paragraphs:
            return "\n\n".join(paragraphs)
    return ""


class BrowserHttpClient:
    """Small polite HTTP client with throttling and transient-error retries."""

    def __init__(
        self,
        timeout: float = 25.0,
        retries: int = 3,
        request_delay: float = 0.75,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.request_delay = max(0.0, request_delay)
        self._last_request_by_host: dict[str, float] = {}
        self._robots_by_origin: dict[str, RobotFileParser | None] = {}

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc.lower()
        last_request = self._last_request_by_host.get(host)
        if last_request is not None:
            remaining = self.request_delay - (time.monotonic() - last_request)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_by_host[host] = time.monotonic()

    def get_text(self, url: str) -> str:
        headers = {
            "User-Agent": CRAWLER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                kwargs = {"headers": headers, "timeout": self.timeout}
                if CURL_CFFI_AVAILABLE:
                    kwargs["impersonate"] = "chrome"
                response = http_requests.get(url, **kwargs)

                if response.status_code == 200:
                    return response.text
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    raise CrawlError(
                        f"HTTP {response.status_code} while fetching {response.url}"
                    )
                last_error = CrawlError(
                    f"HTTP {response.status_code} while fetching {response.url}"
                )
            except CrawlError:
                raise
            except Exception as exc:  # noqa: BLE001 - backend errors vary
                last_error = exc

            if attempt < self.retries:
                time.sleep(min(8.0, 0.6 * (2**attempt)) + random.uniform(0, 0.25))

        raise CrawlError(f"Failed to fetch {url}: {last_error}") from last_error

    def assert_robots_allowed(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots_by_origin:
            robots_url = f"{origin}/robots.txt"
            parser = RobotFileParser()
            parser.set_url(robots_url)
            try:
                parser.parse(self.get_text(robots_url).splitlines())
                self._robots_by_origin[origin] = parser
            except CrawlError as exc:
                # An unavailable robots file is not an explicit prohibition.
                LOGGER.warning("robots.txt could not be read from %s: %s", origin, exc)
                self._robots_by_origin[origin] = None

        parser = self._robots_by_origin[origin]
        if parser is not None and not parser.can_fetch("OverseasNewsCrawler", url):
            raise RobotsDenied(f"robots.txt disallows {url}")


class OverseasNewsSource:
    source_name = ""
    listing_url = ""
    minimum_content_chars = 120

    def __init__(self, client: BrowserHttpClient | None = None) -> None:
        self.client = client or BrowserHttpClient()

    def parse_listing(self, html: str) -> list[NewsCandidate]:
        raise NotImplementedError

    def parse_article(self, html: str, candidate: NewsCandidate) -> NewsArticle:
        raise NotImplementedError

    def discover(self) -> list[NewsCandidate]:
        self.client.assert_robots_allowed(self.listing_url)
        return self.parse_listing(self.client.get_text(self.listing_url))

    def crawl(self, limit: int) -> tuple[list[NewsArticle], list[str]]:
        if limit < 1:
            return [], []

        articles: list[NewsArticle] = []
        errors: list[str] = []
        candidates = self.discover()
        for candidate in candidates:
            if len(articles) >= limit:
                break
            try:
                self.client.assert_robots_allowed(candidate.url)
                article = self.parse_article(
                    self.client.get_text(candidate.url), candidate
                )
                if len(article.content) < self.minimum_content_chars:
                    raise CrawlError(
                        f"article body is too short ({len(article.content)} characters)"
                    )
                articles.append(article)
            except CrawlError as exc:
                errors.append(f"{candidate.url}: {exc}")
                LOGGER.warning("Skipping %s: %s", candidate.url, exc)
        return articles, errors


class YahooFinanceNewsSource(OverseasNewsSource):
    source_name = "yahoo_finance"
    listing_url = "https://finance.yahoo.com/topic/stock-market-news/"

    def parse_listing(self, html: str) -> list[NewsCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        # Yahoo also renders unrelated navigation/personal-finance cards in the
        # same HTML document. Limit discovery to the topic page's main content.
        listing_root = soup.select_one("section.mainContent") or soup
        candidates: list[NewsCandidate] = []
        seen: set[str] = set()

        for link in listing_root.select("a[href]"):
            url = canonicalize_url(urljoin(self.listing_url, link.get("href", "")))
            parts = urlsplit(url)
            if parts.netloc != "finance.yahoo.com":
                continue
            if not re.search(r"/articles?/[^/]+\.html$", parts.path, re.IGNORECASE):
                continue

            title = normalize_text(link.get("title") or link.get_text(" ", strip=True))
            if len(title) < 15 or url in seen:
                continue

            card = link.find_parent(["section", "article", "li"]) or link.parent
            organization = None
            if card:
                publisher = card.select_one(".publishing .publisher")
                if publisher:
                    organization = normalize_text(publisher.get_text(" ", strip=True))
            if not organization and link.get("data-yga"):
                try:
                    organization = json.loads(link["data-yga"]).get(
                        "yDestinationContentPartner"
                    )
                except (TypeError, json.JSONDecodeError):
                    pass

            seen.add(url)
            candidates.append(
                NewsCandidate(self.source_name, title, url, organization or None)
            )
        return candidates

    def parse_article(self, html: str, candidate: NewsCandidate) -> NewsArticle:
        soup = BeautifulSoup(html, "html.parser")
        schema = extract_news_json_ld(soup)
        title = normalize_text(schema.get("headline")) or _first_text(soup, ["h1"])
        content = _paragraph_text(
            soup,
            [
                "article[data-testid='article-content-wrapper'] div.body-wrap p",
                "article[data-testid='article-content-wrapper'] p",
                "div.body-wrap p",
            ],
        )
        provider = _schema_name(schema.get("provider"))
        publisher = _schema_name(schema.get("publisher"))
        organization = provider or candidate.organization or publisher or "Yahoo Finance"
        author = _schema_name(schema.get("author")) or _first_text(
            soup, [".byline-attr-author", "[data-testid='author-name']"]
        )
        published_at = normalize_text(schema.get("datePublished")) or _first_attribute(
            soup, ["time[datetime]"], "datetime"
        )

        if not title:
            raise CrawlError("article title was not found")
        if not content:
            raise CrawlError("article body was not found")
        return NewsArticle(
            source=self.source_name,
            organization=organization,
            title=title,
            url=candidate.url,
            content=content,
            author=author,
            published_at=published_at,
        )


class InvestingNewsSource(OverseasNewsSource):
    source_name = "investing_com"
    listing_url = "https://www.investing.com/news/stock-market-news"

    def parse_listing(self, html: str) -> list[NewsCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: list[NewsCandidate] = []
        seen: set[str] = set()

        for link in soup.select("a[data-test='article-title-link'][href]"):
            url = canonicalize_url(urljoin(self.listing_url, link.get("href", "")))
            parts = urlsplit(url)
            if parts.netloc not in {"investing.com", "www.investing.com"}:
                continue
            if not parts.path.startswith("/news/") or url in seen:
                continue

            title = normalize_text(link.get_text(" ", strip=True))
            if len(title) < 15:
                continue
            card = link.find_parent("article") or link.parent
            provider = card.select_one("span[data-test='news-provider-name']") if card else None
            organization = (
                normalize_text(provider.get_text(" ", strip=True)) if provider else None
            )
            seen.add(url)
            candidates.append(
                NewsCandidate(self.source_name, title, url, organization or None)
            )
        return candidates

    def parse_article(self, html: str, candidate: NewsCandidate) -> NewsArticle:
        soup = BeautifulSoup(html, "html.parser")
        schema = extract_news_json_ld(soup)
        title = normalize_text(schema.get("headline")) or _first_text(soup, ["h1"])
        content = _paragraph_text(
            soup,
            [
                "div[class*='article_WYSIWYG'] p",
                "div[class*='article_articlePage'] p",
                "#article p",
            ],
        )
        provider = _schema_name(schema.get("provider"))
        publisher = _schema_name(schema.get("publisher"))
        organization = provider or candidate.organization or publisher or "Investing.com"
        author = _schema_name(schema.get("author"))
        if not author and content:
            first_paragraph = content.split("\n\n", 1)[0]
            match = re.fullmatch(r"By\s+(.{2,120})", first_paragraph, re.IGNORECASE)
            if match:
                author = normalize_text(match.group(1))
        published_at = normalize_text(schema.get("datePublished")) or None

        if not title:
            raise CrawlError("article title was not found")
        if not content:
            raise CrawlError("article body was not found")
        return NewsArticle(
            source=self.source_name,
            organization=organization,
            title=title,
            url=candidate.url,
            content=content,
            author=author,
            published_at=published_at,
        )


SOURCE_CLASSES = {
    "yahoo": YahooFinanceNewsSource,
    "investing": InvestingNewsSource,
}
