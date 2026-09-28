"""Fetch recent Fundus articles and emit Nasdaq-100 matches as one JSON value."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timezone
from importlib.metadata import version
from pathlib import Path


DEFAULT_PUBLISHERS = (
    "APNews",
    "CNBC",
    "TechCrunch",
    "BusinessInsider",
    "Wired",
    "TheGuardian",
    "TheIndependent",
    "EuronewsEN",
    "BBC",
    "FinancialPost",
    "TheGlobeAndMail",
    "GlobalNews",
)


def _clean(value):
    return " ".join(str(value or "").split())


def _clean_list(values):
    result = []
    seen = set()
    for value in values or []:
        cleaned = _clean(value)
        marker = cleaned.casefold()
        if cleaned and marker not in seen:
            seen.add(marker)
            result.append(cleaned)
    return result


def _catalog(collection):
    result = {}
    for country in dir(collection):
        if country.startswith("_"):
            continue
        group = getattr(collection, country)
        try:
            publishers = list(group)
        except TypeError:
            continue
        for publisher in publishers:
            result[publisher.__name__] = (publisher, country)
    return result


def article_payload(article, extractor, publisher_info, fundus_version):
    html = article.html
    url = _clean(getattr(html, "responded_url", None) or getattr(html, "requested_url", None))
    title = _clean(article.title)
    content = str(article.plaintext or "").strip()
    if not url or not title or len(content) < 120:
        return None
    extracted = extractor.extract({"news_id": url, "title": title, "body": content})
    companies = [company for company in extracted["companies"] if company["market"] == "NASDAQ"]
    if not companies:
        return None
    publisher_key, publisher_domain, country_code = publisher_info
    publishing_date = getattr(article, "publishing_date", None)
    crawl_date = getattr(html, "crawl_date", None)
    # Fundus 0.5.x emits this timestamp without an offset even though the
    # crawler records it in UTC. Make that convention explicit because the
    # shared event contract rejects timestamps with an unknown timezone.
    if crawl_date is not None and crawl_date.tzinfo is None:
        crawl_date = crawl_date.replace(tzinfo=timezone.utc)
    images = []
    for image in (article.images or [])[:20]:
        image_url = _clean(getattr(image, "url", None))
        if image_url:
            images.append({
                "url": image_url,
                "is_cover": bool(getattr(image, "is_cover", False)),
                "caption": _clean(getattr(image, "caption", None)) or None,
                "description": _clean(getattr(image, "description", None)) or None,
            })
    return {
        "publisher_key": publisher_key,
        "publisher_name": _clean(article.publisher) or publisher_key,
        "url": url,
        "title": title,
        "content": content,
        "published_at": publishing_date.isoformat() if publishing_date else None,
        "collected_at": crawl_date.isoformat() if crawl_date else None,
        "language": _clean(getattr(article, "lang", None)) or "en",
        "metadata": {
            "publisher_domain": publisher_domain,
            "publisher_country": country_code,
            "authors": _clean_list(getattr(article, "authors", [])),
            "topics": _clean_list(getattr(article, "topics", [])),
            "images": images,
            "free_access": getattr(article, "free_access", None),
            "fundus_version": fundus_version,
            "nasdaq100_companies": [{
                "company_id": company["company_id"],
                "name": company["name"],
                "ticker": company["ticker"],
                "tickers": company["tickers"],
                "mention_count": company["n_mentions"],
            } for company in companies],
            "universe_version": extracted["universe_version"],
            "registry_sha256": extracted["registry_sha256"],
        },
    }


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--scan-limit", type=int, default=300)
    parser.add_argument("--publishers", nargs="+", default=list(DEFAULT_PUBLISHERS))
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100 or args.scan_limit < args.limit:
        parser.error("invalid article limits")

    # Direct script execution otherwise lets the sibling ``fundus.py`` shadow
    # the third-party package.  Keep the application root, not this directory,
    # on the import path.
    script_dir = Path(__file__).resolve().parent
    sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != script_dir]
    app_root = script_dir.parents[1]
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))

    from fundus import Crawler, NewsMap, PublisherCollection, RSSFeed
    from pipelines.company_extraction import CompanyExtractor

    catalog = _catalog(PublisherCollection)
    missing = [key for key in args.publishers if key not in catalog]
    if missing:
        parser.error("unknown publisher(s): " + ",".join(missing))
    selected = [(key, *catalog[key]) for key in args.publishers if not catalog[key][0].deprecated]
    publisher_by_name = {
        publisher.name: (key, publisher.domain, country)
        for key, publisher, country in selected
    }
    crawler = Crawler(
        *(publisher for _, publisher, _ in selected),
        restrict_sources_to=[RSSFeed, NewsMap],
        ignore_deprecated=True,
        delay=1.0,
        threading=True,
        ignore_robots=False,
        ignore_crawl_delay=False,
        impersonate=True,
    )
    extractor = CompanyExtractor(args.registry)
    fundus_version = version("fundus")
    articles = []
    scanned = 0
    for article in crawler.crawl(
        max_articles=args.scan_limit,
        max_articles_per_publisher=max(10, args.scan_limit // max(1, len(selected))),
        error_handling="catch",
        language_filter=["en"],
        only_unique=True,
    ):
        scanned += 1
        if article.exception is not None:
            continue
        info = publisher_by_name.get(article.publisher)
        if info is None:
            continue
        payload = article_payload(article, extractor, info, fundus_version)
        if payload is not None:
            articles.append(payload)
            if len(articles) >= args.limit:
                break
    json.dump(
        {
            "scanned": scanned,
            "articles": articles,
            "publishers": [key for key, _, _ in selected],
        },
        sys.stdout,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

