"""Article → company mentions, using title/content text and BigKinds full-body org entities."""
from __future__ import annotations

from ..cosmos_reuse import import_matcher
from ..models import Article, Company, CompanyMention

TITLE, CONTENT, ORGS = 1.0, 0.8, 0.6
MIN_CONFIDENCE = 0.5


class Recognizer:
    def __init__(self, companies: list[Company], aliases: list[dict]):
        matcher = import_matcher()
        known = {c.key for c in companies}
        rows = [r for r in aliases if r["ticker"] in known and r.get("ambiguity", "safe") == "safe"]
        self._key_by_alias = {r["alias"]: r["ticker"] for r in rows}
        self._aliases: dict[str, list[str]] = {}
        for r in rows:
            self._aliases.setdefault(r["ticker"], []).append(r["alias"])
        self._matcher = matcher.CompanyMatcher(
            [matcher.Alias(r["alias"], r["ticker"], r["name_official"], r.get("alias_type") or "official", "safe", "")
             for r in rows])

    def key_of_org(self, name: str) -> str | None:
        return self._key_by_alias.get(name.strip())

    def aliases_of(self, key: str) -> list[str]:
        return sorted(self._aliases.get(key, []), key=lambda a: (-len(a), a))

    def _text_hits(self, title: str | None, body: str | None) -> set[str]:
        result = self._matcher.match(title, body)
        return {m.ticker for m in result.mentions if m.ticker and m.confidence >= MIN_CONFIDENCE}

    def recognize(self, article: Article) -> list[CompanyMention]:
        found: dict[str, tuple[float, set[str]]] = {}

        def add(key: str, salience: float, source: str):
            best, sources = found.get(key, (0.0, set()))
            found[key] = (max(best, salience), sources | {source})

        for key in self._text_hits(article.title, None):
            add(key, TITLE, "title")
        for key in self._text_hits(None, article.content):
            add(key, CONTENT, "content")
        for org in article.orgs:
            key = self.key_of_org(org)
            if key:
                add(key, ORGS, "orgs")
        mentions = [CompanyMention(k, s, tuple(sorted(src))) for k, (s, src) in found.items()]
        return sorted(mentions, key=lambda m: (-m.salience, m.key))
