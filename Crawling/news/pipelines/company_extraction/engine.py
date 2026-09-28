"""Dictionary detection, conservative linking, and per-company aggregation."""

from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from .automaton import AhoCorasick
from .normalization import key, normalize
from .registry import DEFAULT_REGISTRY, Registry

SECTIONS = ("title", "lead", "body")
PARTICLES = ("으로부터", "에게서는", "에서는", "으로는", "이라는", "이라고", "에서도", "에게서", "까지는", "부터는", "에서", "에게", "부터", "까지", "보다", "처럼", "으로", "라고", "이라", "와의", "과의", "에는", "에도", "은", "는", "이", "가", "을", "를", "의", "과", "와", "에", "로", "도", "만")
PARTICLES += ("로부터", "에서부터", "에게까지", "로서", "로써", "으로서", "으로써")
PARTICLES = tuple(set(PARTICLES) | {base + extra for base in PARTICLES for extra in ("는", "은", "도", "만", "의")})
ACTION = re.compile(r"주가|주식|종목|시가총액|시총|매출|영업이익|실적|공시|상장|지분|인수|합병|계약|수주|공급|생산|출시|투자|증자|감자|배당|대표이사|반도체|클라우드|발표|공개|판매|통신망|\b(?:ceo|stock|shares?|revenue|earnings?|acquisition|merger|announced?|launch(?:ed|es)?|unveil(?:ed|s)?|chip|semiconductor|dividend|invest(?:s|ed|ment)?)\b", re.I)
NEGATIVE = re.compile(r"\bapple\s+(?:pie|juice|tree|fruit)|\bamazon\s+(?:rainforest|river)|\balphabet\s+(?:soup|letters)|\barm\s+(?:muscle|injury)|사과\s*주스", re.I)
NON_COMPANY_FOLLOWING = {
    "기아": r"\s*(?:퇴치|문제|인구|구호|상태|해결|감소|종식)",
    "알파벳": r"\s*(?:문자|순서|글자|순|배열|교육)",
    "메타": r"\s*(?:인지|분석|데이터|정보|태그)",
    "apple": r"\s+(?:pie|juice|tree|fruit|cider)",
    "amazon": r"\s+(?:rainforest|river|basin)",
    "alphabet": r"\s+(?:soup|letters|order)",
    "meta": r"\s+(?:analysis|data|tags?)\b",
}


def word(char):
    return bool(char) and (char.isalnum() or char == "_")


def boundary(text, start, end):
    if start and word(text[start - 1]):
        return False
    if end == len(text) or not word(text[end]):
        return True
    # Korean particles are allowed for both Korean and English company names.
    suffix = text[end:]
    return any(suffix.startswith(p) and (len(suffix) == len(p) or not word(suffix[len(p)])) for p in PARTICLES)


def clause(text, start, end):
    left = max(text.rfind(mark, 0, start) for mark in (".", "!", "?", "\n", ";")) + 1
    right = min([pos for mark in (".", "!", "?", "\n", ";") if (pos := text.find(mark, end)) >= 0] or [len(text)])
    return text[max(left, start - 100):min(right, end + 100)]


def explicit_ticker(text, start, end):
    before = unicodedata.normalize("NFKC", text[max(0, start - 16):start])
    after = unicodedata.normalize("NFKC", text[end:end + 4])
    return bool(re.search(r"(?:\$|(?:NASDAQ|NYSE|KRX|KOSPI)\s*:\s*)$", before, re.I) or (before.endswith("(") and re.match(r"\s*\)", after)))


class CompanyExtractor:
    def __init__(self, registry_path=DEFAULT_REGISTRY, *, ner=None, fuzzy_threshold=.94, fuzzy_margin=.08):
        self.registry = registry_path if isinstance(registry_path, Registry) else Registry(registry_path or DEFAULT_REGISTRY)
        self.ner = ner
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy_margin = fuzzy_margin
        self.extractor_sha256 = hashlib.sha256(b"".join((Path(__file__).parent / filename).read_bytes() for filename in ("engine.py", "normalization.py", "automaton.py", "registry.py"))).hexdigest()
        self.exact = defaultdict(list)
        for alias in self.registry.aliases:
            if alias.get("enabled", True):
                self.exact[key(alias["alias"])].append(alias)
        self.matcher = AhoCorasick(self.exact.items())

    def _accept(self, alias, text, start, end):
        if not boundary(text, start, end):
            return False
        if alias.get("alias_type") == "ticker" or alias.get("match_policy") == "ticker":
            mention = unicodedata.normalize("NFKC", text[start:end])
            if explicit_ticker(text, start, end):
                return True
            if alias.get("risk_level") == "blocked":
                return False
            return (mention == mention.upper() and bool(ACTION.search(clause(text, start, end))))
        return True

    def _mention(self, alias, text, start, end, section, *, status="resolved", method="dict", reason="exact_alias", score=.99, candidates=None):
        cid = alias["company_id"] if alias else None
        securities = self.registry.in_scope(cid) if cid else []
        ticker = alias.get("ticker") if alias else None
        if ticker and not any(s["ticker"] == ticker for s in securities):
            status, reason = "out_of_scope", "explicit_security_out_of_scope"
        if not ticker and len(securities) == 1:
            ticker = securities[0]["ticker"]
        if status == "unresolved":
            cid, ticker = None, None
        return {
            "company_id": cid, "canonical_name": self.registry.companies[cid]["canonical_name"] if cid else None,
            "market": self.registry.companies[cid]["market"] if cid else None,
            "ticker": ticker, "tickers": [s["ticker"] for s in securities] if cid else [],
            "resolved_level": ("security" if ticker else "company") if cid else "unresolved",
            "mention": text[start:end], "start": start, "end": end, "section": section,
            "offset_unit": "unicode_codepoint", "method": method, "methods": [method],
            "matched_alias": alias["alias"] if alias else None, "alias_id": alias["alias_id"] if alias else None,
            "alias_type": alias.get("alias_type") if alias else None,
            "risk_level": alias.get("risk_level") if alias else None,
            "status": status, "confidence": score, "confidence_type": "heuristic_not_probability",
            "reason": reason, "candidate_company_ids": candidates or [],
            "universe_version": self.registry.version,
        }

    def _dictionary(self, text, section):
        normalized = normalize(text)
        groups = defaultdict(list)
        for start, end, aliases in self.matcher.find(normalized.text):
            original_start, original_end = normalized.original_span(start, end)
            # Do not recognize only one character of a compatibility expansion.
            if key(text[original_start:original_end]) != normalized.text[start:end]:
                continue
            for alias in aliases:
                if self._accept(alias, text, original_start, original_end):
                    groups[(original_start, original_end)].append(alias)
        # Longest accepted span wins, including unresolved and out-of-scope names.
        kept = []
        for span, aliases in sorted(groups.items(), key=lambda item: (-(item[0][1] - item[0][0]), item[0][0])):
            if not any(span[0] < end and span[1] > start for (start, end), _ in kept):
                kept.append((span, aliases))
        anchors = [(span, a["company_id"]) for span, aliases in kept for a in aliases if a.get("risk_level", "safe") == "safe" and a.get("alias_type") not in {"group", "ticker", "brand"}]
        results = []
        for (start, end), aliases in sorted(kept):
            cids = sorted(set(a["company_id"] for a in aliases))
            context = clause(text, start, end)
            if len(cids) > 1 or all(a.get("alias_type") == "group" for a in aliases):
                # Co-occurrence alone does not establish coreference. Only accept
                # an explicit parenthetical naming construction: 회사명(이하 약칭).
                allowed = {cid for (s, e), cid in anchors if cid in cids and e <= start
                           and re.fullmatch(r"\s*\(\s*(?:이하\s*)?[\"'“‘]?\s*", text[e:start])
                           and re.match(r"[\"'”’]?\s*\)", text[end:])}
                if len(allowed) == 1:
                    cid = next(iter(allowed))
                    alias = next(a for a in aliases if a["company_id"] == cid)
                    results.append(self._mention(alias, text, start, end, section, method="rule", reason="explicit_parenthetical_alias_definition", score=.90))
                else:
                    results.append(self._mention(None, text, start, end, section, status="unresolved", reason="ambiguous_alias", score=0.0, candidates=cids))
                continue
            # Prefer full official/company names over a same-spelling ticker.
            aliases.sort(key=lambda a: (a.get("risk_level") != "safe", a.get("alias_type") == "ticker", a["alias_id"]))
            alias = aliases[0]
            risk = alias.get("risk_level", "safe")
            ticker_alias = alias.get("alias_type") == "ticker"
            if (risk == "blocked" and not ticker_alias) or alias.get("alias_type") == "brand":
                results.append(self._mention(None, text, start, end, section, status="unresolved", reason="blocked_or_brand_alias", score=0.0, candidates=cids))
            elif risk == "contextual" and not ticker_alias:
                negative_pattern = NON_COMPANY_FOLLOWING.get(key(text[start:end]))
                non_company = negative_pattern and re.match(negative_pattern, text[end:], re.I)
                if ACTION.search(context) and not NEGATIVE.search(context) and not non_company:
                    results.append(self._mention(alias, text, start, end, section, method="rule", reason="business_context", score=.90))
                else:
                    results.append(self._mention(None, text, start, end, section, status="unresolved", reason="insufficient_context", score=0.0, candidates=cids))
            else:
                results.append(self._mention(alias, text, start, end, section, reason="ticker_context" if ticker_alias else "exact_alias", score=.97 if ticker_alias else .99))
        return results

    def _link_ner(self, text, start, end, section):
        surface = key(text[start:end])
        # Exact ambiguous names retain the same dictionary rules.
        if surface in self.exact:
            same = [m for m in self._dictionary(text, section) if m["start"] == start and m["end"] == end]
            if same:
                result = same[0]
                result.update(method="ner", methods=["ner"], reason="ner_" + result["reason"])
                return result
            return self._mention(None, text, start, end, section, method="ner", status="unresolved", reason="ner_alias_policy_rejected", score=0.)
        scored = {}
        if len(surface) >= 4 and ACTION.search(clause(text, start, end)):
            for alias_key, aliases in self.exact.items():
                if len(alias_key) < 4:
                    continue
                similarity = SequenceMatcher(None, surface, alias_key).ratio()
                if similarity < self.fuzzy_threshold:
                    continue
                for alias in aliases:
                    if alias.get("risk_level") != "safe" or alias.get("alias_type") in {"ticker", "group", "brand"} or alias.get("ticker"):
                        continue
                    cid = alias["company_id"]
                    if similarity > scored.get(cid, (0., None))[0]:
                        scored[cid] = (similarity, alias)
        ranked = sorted(scored.values(), key=lambda value: value[0], reverse=True)
        if ranked and ranked[0][0] - (ranked[1][0] if len(ranked) > 1 else 0) >= self.fuzzy_margin:
            return self._mention(ranked[0][1], text, start, end, section, method="ner", reason="fuzzy_unique_with_business_context", score=min(.90, ranked[0][0]))
        return self._mention(None, text, start, end, section, method="ner", status="unresolved", reason="ner_no_confident_link", score=0., candidates=list(scored))

    def extract(self, article):
        news_id = article.get("news_id", article.get("document_id", article.get("record_id")))
        if news_id is None or str(news_id).strip() == "":
            raise ValueError("news_id (or document_id/record_id) is required")
        mentions = []
        for section in SECTIONS:
            text = article.get(section) or ""
            if not isinstance(text, str):
                raise ValueError(f"{section} must be a string or null")
            detected = self._dictionary(text, section)
            if self.ner and text:
                for span in self.ner(text):
                    start, end = span.get("start"), span.get("end")
                    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
                        raise ValueError("NER provider returned invalid original-text span")
                    label = str(span.get("label", "ORG")).upper().removeprefix("B-").removeprefix("I-")
                    if label not in {"ORG", "COMPANY", "CORPORATION"}:
                        continue
                    same = next((m for m in detected if (m["start"], m["end"]) == (start, end)), None)
                    if same:
                        same["methods"] = list(dict.fromkeys(same["methods"] + ["ner"]))
                        same["ner_score"] = float(span.get("score", 0.))
                    elif not any(start < m["end"] and end > m["start"] for m in detected):
                        linked = self._link_ner(text, start, end, section)
                        linked["ner_score"] = float(span.get("score", 0.))
                        detected.append(linked)
            mentions.extend(sorted(detected, key=lambda m: (m["start"], m["end"])))
        grouped = defaultdict(list)
        for mention in mentions:
            mention["news_id"] = str(news_id)
            if mention["status"] == "resolved":
                grouped[mention["company_id"]].append(mention)
        companies = []
        for cid, found in grouped.items():
            securities = self.registry.in_scope(cid)
            explicit = sorted({m["ticker"] for m in found if m["ticker"]})
            generic = any(m["resolved_level"] == "company" for m in found)
            ticker = explicit[0] if len(explicit) == 1 and not generic else None
            companies.append({
                "news_id": str(news_id), "company_id": cid, "name": self.registry.companies[cid]["canonical_name"],
                "market": self.registry.companies[cid]["market"], "ticker": ticker,
                "tickers": [s["ticker"] for s in securities], "explicit_tickers": explicit,
                "resolved_level": "security" if ticker else "company", "n_mentions": len(found),
                "first_pos": found[0]["section"], "confidence": max(m["confidence"] for m in found),
                "confidence_type": "heuristic_not_probability", "method": found[0]["method"],
                "methods": sorted({method for m in found for method in m["methods"]}),
                "universe_version": self.registry.version,
            })
        return {
            "news_id": str(news_id), "mentions": mentions, "companies": companies,
            "unresolved": [m for m in mentions if m["status"] == "unresolved"],
            "out_of_scope": [m for m in mentions if m["status"] == "out_of_scope"],
            "co_mentions": [{"company_a": a, "company_b": b, "news_id": str(news_id), "relation": "CO_MENTIONED", "is_causal": False} for a, b in combinations(sorted(grouped), 2)],
            "universe_version": self.registry.version, "registry_sha256": self.registry.sha256,
            "model_version": getattr(self.ner, "model_version", "dictionary-v1"),
            "extractor_version": "dictionary-linker-v1",
            "extractor_sha256": self.extractor_sha256,
            "input_sha256": hashlib.sha256(json.dumps({s: article.get(s) or "" for s in SECTIONS}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "published_date": article.get("published_date"),
            "membership_policy": "snapshot_universe_not_historical_membership",
        }
