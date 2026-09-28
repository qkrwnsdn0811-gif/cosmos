# Vendor source provenance

- Upstream repository: https://github.com/wildyoung/overseas-news-crawler.git
- Source revision: `0f43352`
- License: GNU GPL version 3; the upstream `LICENSE` is preserved alongside this notice.
- Copied modules: `lib/__init__.py`, `lib/Crawling/__init__.py`, `lib/Crawling/News/__init__.py`, `lib/Crawling/News/overseas.py`, `lib/Crawling/News/history.py`.

These five modules were copied without source changes from the local overseas crawler used by the deployed COSMOS news pipeline on 2026-09-15. Packaging may normalize CRLF to LF. Original notices are retained. The COSMOS adapter is separate at `services/news_pipeline/overseas.py`.

The legacy MySQL/RDS entrypoint, credentials, database schema, downloaded articles and crawl state are not included. This subset supplies the HTTP client, robots policy, Yahoo parser and persistent history queue needed by the COSMOS adapter.
