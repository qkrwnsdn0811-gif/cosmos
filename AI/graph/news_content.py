"""Remove crawl-time stock widgets and inserted feeds BEFORE NLP inference.

A real 2025 article contained '삼성전자(251,250원 ▼9,750 -3.74%)' when
crawled in 2026. Such live quotes are not publication-time information. Keep
ordinary earnings figures and ticker codes, but remove parenthesized quote
widgets (currency plus movement, or ticker plus signed percentage). Reuse the
audited Story1 sidebar/headline filters. Original HDFS/local raw text is retained.
This reduces known contamination; it cannot certify that publishers never edited
the historical article. That remaining timestamp limitation is reported.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ner"))
from matcher import mask_inserted_headlines, trim_boilerplate

GROUP = re.compile(r"\([^()\n]{1,140}\)|\[[^\[\]\n]{1,140}\]")


def remove_live_quotes(text):
    def clean(m):
        part = m.group()
        currency = bool(re.search(r"\d[\d,.]*\s*(?:원|달러)|[$€£]\s*\d", part))
        movement = bool(re.search(r"[▲▼△▽]|[+-]\s*\d[\d,.]*\s*%", part))
        ticker_change = bool(re.search(r"\b[A-Z]{1,6}\b\s*[, :]?\s*[+-]\d[\d,.]*%", part))
        return "" if (currency and movement) or ticker_change else part
    return GROUP.sub(clean, text)


def sanitize_article(article):
    out = dict(article)
    title, body = article.get("title") or "", article.get("body") or ""
    joined = title + "\n" + body
    trimmed = mask_inserted_headlines(joined[:trim_boilerplate(joined)])
    # Keep the title even when a marker appears there; body may be fully absent.
    out["title"] = remove_live_quotes(title)
    out["body"] = remove_live_quotes(trimmed[len(title) + 1:])
    return out
