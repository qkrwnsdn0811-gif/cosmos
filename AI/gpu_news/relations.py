"""Typed company relations, read off the sentences sentiment has to throw away.

A sentence naming two companies cannot say how either one is doing - that is why
the attribution guard in evidence.py drops it. It is exactly what a relation
needs: the predicate between the two names is what makes a pair SUPPLY rather
than COMPETE. So the two readers split the same article between them and neither
needs the other's sentences.

The classifiers are not written here. ``extract_relations`` (Korean) and
``extract_relations_en`` (English) already encode rules that were arrived at by
measurement - conjunction handling, keyword distance, refusing to guess a
direction - and their headers document the traps behind each one. This module
finds the candidate sentences, calls them, and records what they return.

Direction comes back in the classifier's answer, not from input order: "B is
supplied by A" yields src=A even though B is read first. The returned aliases
are mapped back to tickers rather than assuming the pair order held.

Relation extraction does not need a model and does not load one. It is cheap
enough to run over a day of articles in one pass.
"""
from __future__ import annotations

import collections
import re

from evidence import _name_pattern, sentence_view, usable_sentences

RELATION_TYPES = ("SUPPLY", "PARTNER", "COMPETE")
VERSION = "news-relations-1.0"

# The input contract spark/extract_relation_candidates.py states: only dictionary
# hits the matcher was certain about. A group-name guess (0.5) or a sports-club
# demotion (0.4) is a weak identity claim, and a relation asserts something about
# both parties - a wrong identity on either side invents an edge between real
# companies. Measured on live batches: 19 of 530 mentions are below 1.0.
MIN_MATCH_CONFIDENCE = 1.0

# The matcher expands "신한, KB국민, 하나, 우리은행" by carrying the suffix across
# the list. Those sentences are lists, not statements about a pair, so the flag
# travels with the row for the caller to weigh.
ELLIPSIS_METHOD = "ellipsis"

# Patterns the classifiers hand back with a hit. An enumerating conjunction is
# kept but flagged: "신한은행·카카오·배달의민족과 협약" names three companies that
# each contracted with a fourth party, not with each other. The Korean module
# already rejects enumerations for SUPPLY; for PARTNER it lets them through, so
# the caller decides. Measured on 30 live batches, enumeration was the source of
# every PARTNER false positive we found by hand.
ENUM_MARKER = "enum"

# The Korean classifier was validated on sentences naming exactly two of our
# companies - extract_relations.main() skips the rest ("3개 이상은 대개 나열문").
# The realtime path forgot that contract. On the first live pass (2026-09-23),
# every false positive found by hand was a 3+ company sentence: "삼성·SK·현대자동차·
# LG·한화·롯데·포스코·HD현대 등 조사에 응한", a survey roster read as a partnership.
MAX_PARTIES = 2

# "A·B 등은 …와 협약" - a typographic list closed by 등: the two are items in a
# roster, and whoever they contracted with is a third party outside it. This is
# not the grammatical "A와 B 등이 공동 개발", where 와 makes them parties.
LIST_CONNECTOR = re.compile(r"^\s*[·ㆍ,]\s*$")
# "A·B 등", and also "A와 B, C 등도" - the list may run on past the second name
# before 등 closes it. Up to four more items, joined by , or ·.
LIST_TAIL = re.compile(r"^(?:\s*[,·ㆍ]\s*[^\s,·ㆍ()]{1,25}){0,4}\s*등(?=[\s은는이가의과와을를도,.]|$)")
# For a 와/과 pair the list has to visibly continue ("A와 B, C 등도") - a bare
# "A와 B 등이 공동 개발" still names two parties, 등 only says there were more.
LIST_CONTINUES = re.compile(r"^(?:\s*[,·ㆍ]\s*[^\s,·ㆍ()]{1,25}){1,4}\s*등(?=[\s은는이가의과와을를도,.]|$)")
# A list mark between the two names means one of them sits inside a roster:
# "신한은행·강원신용보증재단·…와 협약을, 오후에는 카카오·우아한형제들과". Unless the
# first name is the sentence's subject ("HD현대는 … 테라파워·현대건설과 협력") - then
# the roster is its counterparty and the pair is real.
SUBJECT_AFTER = re.compile(r"^(?:은|는|이|가)(?![가-힣])")
LIST_MARK = re.compile(r"[·ㆍ]")

# The classifier's "enum+kw" covers every connector alike. They are not alike.
# "한화에어로스페이스와 KAI는 … MOU를 체결" is the canonical statement of a pair -
# the author scored it highest - while "한국투자증권, 키움증권과 … 각각 체결" puts
# two names in a list whose counterparty is someone else. Measured on 400 live
# batches (2026-09-23): 40 enum+kw rows, and the 와/과 ones were correct three
# times in four. So a grammatical conjunction passes; a typographic list mark
# is treated as a roster.
GRAMMATICAL_CONNECTOR = re.compile(r"^\s*(?:와|과|및)\s*$")

# "카카오페이와 카카오뱅크가 파이어블록스와 업무협약을 체결" - the pair is the joint
# subject and the counterparty is a third name right after it. The classifier's
# window sees 와 and 협약 and calls it a partnership between the two. 4 of 6
# grammatical-pair acceptances on the 400-batch sample were correct; both
# misses had this shape.
# The subject particle is required: "카카오뱅크는 파이어블록스와" has it, while
# "구글 딥마인드와" is the rest of Google's own name and must not read as a third.
THIRD_PARTY_AFTER = re.compile(
    r"^(?:은|는|이|가)\s+(?:[^\s,.·()]{2,}\s+){0,3}?([가-힣A-Za-z0-9㈜&]{2,20})(?:와|과)\s")
# "삼성전자와 SK하이닉스 등 국내 기업들에 악재" - 등 + a noun for "companies" makes the
# pair two names in a roster of a third party's making. The classifier has this
# rule without room for the modifier (국내/주요/글로벌) in between.
ROSTER_TAIL = re.compile(
    r"^\s*등(?:의)?\s*(?:(?:국내|해외|주요|대형|글로벌|여러|다양한|리딩|한국|미국|중국)\s*){0,3}"
    # 기업들에 / 회사가 / 업체와 … : the noun may take 들 and a particle, but "기업은행"
    # must not match - so what follows has to be a particle that itself ends the word.
    r"(?:경쟁사|경쟁업체|파트너사?|협력사|협력업체|업체|기업|회사)(?:들)?"
    r"(?=$|[\s,.]|(?:은|는|이|가|의|에|을|를|과|와|도|로|으로)(?![가-힣]))")
REJECT_THIRD_PARTY = "third_party"    # A와 B가 C와 … : C is the counterparty

# "쇼피파이는 아마존과 달리 …" was read as PARTNER: the classifier sees 과 and a
# keyword in the window and never looks at the word right after the pair.
CONTRAST = re.compile(r"^\s*(?:와|과|보다|에\s*비해)\s*(?:는\s*)?"
                      r"(?:달리|다르게|다른|반대로|대조적으로|비교하면|비교해|대비)")

# Reasons a row is not a relation. The runner drops every row with a reason;
# the reason itself is kept so a pass can report what it threw away.
REJECT_CROWDED = "crowded"          # 3+ of our companies in one sentence
REJECT_CO_LISTED = "co_listed"      # A·B 등 …
REJECT_CONTRAST = "contrast"        # A는 B와 달리 …
REJECT_ENUMERATION = "enumeration"  # classifier's own enum flag, or an ellipsis expansion


class RelationError(ValueError):
    pass


def _spans_in_order(a: tuple, b: tuple) -> tuple:
    return (a, b) if a[0] <= b[0] else (b, a)


def _co_listed(text: str, a: tuple, b: tuple) -> bool:
    """True when the pair reads as two items of a roster rather than two parties.

    Either the names are joined only by a list mark and the list is closed by
    등, or a list mark sits between them while the first is not the subject.
    """
    first, second = _spans_in_order(a, b)
    mid = text[first[1]:second[0]]
    if LIST_CONNECTOR.match(mid) and LIST_TAIL.match(text[second[1]:]):
        return True
    if GRAMMATICAL_CONNECTOR.match(mid) and LIST_CONTINUES.match(text[second[1]:]):
        return True                                     # "A와 B, C 등도 …"
    return bool(LIST_MARK.search(mid)) and not SUBJECT_AFTER.match(text[first[1]:])


def _third_party_after(text: str, a: tuple, b: tuple, aliases: tuple) -> bool:
    """True when another name with 와/과 follows the pair: the pair's counterparty."""
    _first, second = _spans_in_order(a, b)
    match = THIRD_PARTY_AFTER.match(text[second[1]:])
    return bool(match) and match.group(1) not in aliases


def _roster_tail(text: str, a: tuple, b: tuple) -> bool:
    _first, second = _spans_in_order(a, b)
    return bool(ROSTER_TAIL.match(text[second[1]:]))


def _contrast(text: str, a: tuple, b: tuple) -> bool:
    """True when either name is followed by 'unlike / as opposed to'."""
    return any(CONTRAST.match(text[span[1]:]) for span in (a, b))


def _reject_reason(text: str, hits: list, source_key, target_key, pattern: str,
                   ellipsis: set) -> str | None:
    if len(hits) > MAX_PARTIES:
        return REJECT_CROWDED
    span = {hit[0]: (hit[2], hit[3]) for hit in hits}
    aliases = tuple(hit[1] for hit in hits)
    a, b = span[source_key], span[target_key]
    if _co_listed(text, a, b) or _roster_tail(text, a, b):
        return REJECT_CO_LISTED
    if _third_party_after(text, a, b, aliases):
        return REJECT_THIRD_PARTY
    if _contrast(text, a, b):
        return REJECT_CONTRAST
    if source_key in ellipsis or target_key in ellipsis:
        return REJECT_ENUMERATION
    if ENUM_MARKER in pattern:
        first, second = _spans_in_order(a, b)
        if not GRAMMATICAL_CONNECTOR.match(text[first[1]:second[0]]):
            return REJECT_CO_LISTED            # "A, B와 …" / "A·B …" - a list, not a pair
    return None


def _positions(text: str, names: dict) -> list[tuple]:
    """(key, alias, start, end) for every company actually named, longest wins.

    Korean has no word boundary, so a short name matches inside a longer one:
    "카카오페이와 카카오뱅크는 …" contains neither the word 카카오 nor a mention of
    that company, yet a plain search finds it three times. Left alone this
    invents pairs - 카카오→카카오뱅크 from a sentence that never names 카카오 -
    which is the substring trap the sentiment guard is also built around.

    A hit whose span sits inside another company's hit is that other company's
    name, not this one's, so it is dropped.
    """
    hits = []
    for key, aliases in names.items():
        best = None
        for alias in aliases:
            match = _name_pattern(alias).search(text)
            if match and (best is None or match.start() < best[1]
                          or (match.start() == best[1] and match.end() > best[2])):
                best = (alias, match.start(), match.end())
        if best:
            hits.append((key, *best))

    kept = []
    for hit in hits:
        inside = any(other is not hit
                     and other[2] <= hit[2] and hit[3] <= other[3]
                     and (other[3] - other[2]) > (hit[3] - hit[2])
                     for other in hits)
        if not inside:
            kept.append(hit)
    return sorted(kept, key=lambda hit: hit[2])


def article_relations(title: str, content: str, companies, language: str) -> list[dict]:
    """Typed relations found in one article, each with the sentence behind it.

    One row per (relation type, source, target, sentence). The same pair stated
    twice in one article is two rows, because the loader scores by evidence
    count and each sentence is a separate mention.
    """
    certain = [company for company in (companies or [])
               if float(company.get("confidence") or 0) >= MIN_MATCH_CONFIDENCE]
    ellipsis = {(company.get("market"), company.get("stock_code"))
                for company in certain
                if ELLIPSIS_METHOD in (company.get("method") or "")}
    # A relation needs two parties. Deciding that before the sentence catalog
    # is built matters: most articles name one company or none, and splitting
    # a 50,000-character stock table into sentences cost 8 seconds on the
    # server - paid, before this check moved, for a result that was always [].
    if len(certain) < 2:
        return []
    companies, catalog, names, field, scannable = sentence_view(title, content, certain)
    if len(companies) < 2:
        return []

    korean = language != "en"
    found = []
    for entry, _end, stripped, text in usable_sentences(catalog, field, scannable):
        hits = _positions(stripped, names)
        if len(hits) < 2:
            continue
        alias_to_key = {hit[1]: hit[0] for hit in hits}
        for rel, src, dst, pattern, confidence in _classify(stripped, hits, korean):
            source_key = alias_to_key.get(src)
            target_key = alias_to_key.get(dst)
            if source_key is None or target_key is None or source_key == target_key:
                continue
            reject = _reject_reason(stripped, hits, source_key, target_key, pattern, ellipsis)
            found.append({
                "relation_type": rel,
                "source_market": source_key[0], "source_stock_code": source_key[1],
                "target_market": target_key[0], "target_stock_code": target_key[1],
                "pattern": pattern,
                # The classifier's own score for the pattern (None for English).
                # extract_relations.main() drops below 0.6 by default - near+kw.
                "confidence": confidence,
                # Kept for callers that only know the old flag: every list-shaped
                # reason counts as an enumeration. "reject" carries the reason.
                "enumeration": reject in (REJECT_ENUMERATION, REJECT_CO_LISTED, REJECT_CROWDED),
                "reject": reject,
                "sentence_order": entry["id"],
                "source": entry["source"],
                "text": text,
                "language": "ko" if korean else "en",
            })
    return _dedupe(found)


def _classify(text: str, hits: list[tuple], korean: bool):
    """Yield (relation, src_alias, dst_alias, pattern) from the owning module."""
    if korean:
        import extract_relations as ko
        for i, first in enumerate(hits):
            for j, second in enumerate(hits):
                if i == j:
                    continue
                answer = ko.classify(text, first[1], second[1])
                if answer:
                    src, dst, rel, confidence, pattern = answer
                    yield rel, src, dst, pattern, confidence
    else:
        import extract_relations_en as en
        drops: collections.Counter = collections.Counter()
        # The English module keys on ticker rather than alias and wants
        # (ticker, start, end); it finds the keyword first and reads the
        # companies on either side of it.
        by_ticker = {hit[0][1]: hit[1] for hit in hits}
        positions = [(hit[0][1], hit[2], hit[3]) for hit in hits]
        for rel, src, dst, keyword in en.classify_sentence(text, positions, drops):
            yield rel, by_ticker.get(src, src), by_ticker.get(dst, dst), "en:" + keyword, None


def _dedupe(found: list[dict]) -> list[dict]:
    """One row per (relation, pair, sentence).

    The Korean classifier is asked about both orders of every pair, so a
    symmetric statement comes back twice for the same sentence.
    """
    seen, unique = set(), []
    for row in found:
        key = (row["relation_type"], row["source_stock_code"], row["target_stock_code"],
               row["sentence_order"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique
