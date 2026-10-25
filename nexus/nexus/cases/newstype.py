"""News type (F2): keyword rules that sort a semiconductor article into one of 15 types, or OTHER.

The type is what makes past cases comparable: "after past export-control news, how did suppliers move?"
needs every article to carry a type. Rules look at the title (weight 3) and the 200-char body (weight 1);
a type needs a score of at least 2, i.e. one title hit or two body hits. Ties go to the more specific
event (TYPES order): a market wrap that mentions an export control is an export-control article.
The confidence is the winner's share of all matched scores, so a title that matches two types is less sure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Article

TITLE_WEIGHT = 3
BODY_WEIGHT = 1
MIN_SCORE = 2
OTHER = "OTHER"

_PRODUCT = r"(?:D램|디램|DRAM|낸드|NAND|메모리|HBM|MLCC|기판|웨이퍼|칩)"
# '역대 최대'·'사상 최대'는 실적 말이 붙을 때만 실적이다 — 전시회 관람객·교육생 모집·투자액의 '역대 최대'가 실적 호조로 잡혔다
_EARNINGS = r"(?:실적|매출|영업\s?이익|영업익|순이익|분기|반기)"
_PRICE = r"(?:가격|값|고정거래가|현물가|단가|판가|계약가)"


@dataclass(frozen=True)
class NewsType:
    code: str
    label: str
    patterns: tuple[str, ...]


# Most specific first: the order breaks ties between equal scores.
TYPES: tuple[NewsType, ...] = (
    NewsType("ACCIDENT", "사고·공급 차질", (
        # '폭발'은 사고일 때만 — "투심 폭발", "수요 폭발"이 사고로 잡혔다
        r"화재", r"정전", r"지진", r"폭발\s?사고", r"(?:공장|가스|설비)\s?폭발", r"가스\s?누출", r"공장\s?사고", r"산업재해",
        r"공급\s?차질", r"생산\s?차질",
        r"셧다운", r"파업", r"가동\s?중단")),
    NewsType("EXPORT_CONTROL", "수출 규제·제재", (
        r"수출\s?규제", r"수출\s?통제", r"제재", r"블랙리스트", r"엔티티\s?리스트", r"거래\s?제한", r"VEU", r"검증된\s?최종\s?사용자",
        r"화이트\s?리스트", r"반도체\s?규제")),
    NewsType("TARIFF", "관세·통상", (
        r"관세", r"무역\s?분쟁", r"무역\s?전쟁", r"통상\s?(?:압박|협상|마찰|갈등)", r"무역\s?협상", r"232조")),
    NewsType("EARNINGS_BEAT", "실적 호조", (
        r"어닝\s?서프라이즈", _EARNINGS + r"[^.。\n]{0,15}(?:사상|역대)\s?최대", r"(?:사상|역대)\s?최대\s?" + _EARNINGS,
        r"최대\s?실적", r"흑자\s?전환", r"호실적", r"실적\s?개선",
        r"깜짝\s?실적", r"영업이익\s?(?:증가|급증|껑충|두\s?배)", r"컨센서스\s?(?:상회|웃돌)")),
    NewsType("EARNINGS_MISS", "실적 부진", (
        r"어닝\s?쇼크", r"적자\s?전환", r"영업\s?손실", r"실적\s?(?:부진|악화|쇼크)", r"적자\s?(?:지속|확대|늪)",
        r"영업이익\s?(?:감소|급감|반토막|뚝)", r"컨센서스\s?(?:하회|밑돌)", r"감익")),
    NewsType("SUPPLY_DEAL", "수주·공급 계약", (
        r"수주", r"공급\s?계약", r"납품", r"계약\s?체결", r"단일\s?판매", r"공급\s?협약", r"공급\s?합의", r"장기\s?공급",
        r"수주\s?잔고", r"공급사\s?선정", r"퀄\s?테스트\s?통과", r"양산\s?승인")),
    NewsType("PRICE_UP", "제품 가격 상승", (
        _PRODUCT + r"[^.。\n]{0,12}" + _PRICE + r"[^.。\n]{0,10}(?:상승|인상|올라|오름|급등|반등|강세|뛰)",
        r"(?:판가|가격)\s?인상")),
    NewsType("PRICE_DOWN", "제품 가격 하락", (
        _PRODUCT + r"[^.。\n]{0,12}" + _PRICE + r"[^.。\n]{0,10}(?:하락|인하|내려|급락|약세|떨어|내림)",
        r"(?:판가|가격)\s?인하")),
    NewsType("PRODUCTION_CUT", "감산·재고 조정", (
        r"감산", r"생산\s?축소", r"가동률\s?(?:하락|축소|낮)", r"재고\s?조정", r"투자\s?축소", r"설비투자\s?축소")),
    NewsType("CAPEX", "증설·투자", (
        r"증설", r"신규\s?공장", r"신공장", r"팹\s?(?:건설|착공|증설)", r"착공", r"투자\s?확대", r"생산\s?라인", r"설비\s?투자",
        r"생산\s?능력\s?확대", r"양산\s?(?:시작|돌입|개시)")),
    NewsType("SHAREHOLDER", "주주환원", (
        r"자사주", r"배당", r"주식\s?소각", r"주주\s?환원")),
    NewsType("AI_DEMAND", "AI·HBM 수요", (
        r"HBM", r"AI\s?반도체", r"AI\s?수요", r"AI\s?(?:투자|인프라|서버|가속기)", r"엔비디아", r"데이터\s?센터", r"GPU",
        r"고대역폭")),
    NewsType("POLICY", "정부 정책·지원", (
        r"보조금", r"세액\s?공제", r"특별법", r"반도체법", r"칩스법", r"지원\s?(?:책|방안|대책)", r"국가전략기술", r"규제\s?완화")),
    NewsType("ANALYST", "증권가 전망·목표가", (
        r"목표\s?(?:주)?가", r"투자\s?의견", r"매수\s?(?:의견|유지|추천)", r"리포트", r"커버리지", r"증권(?:은|이)\s")),
    NewsType("MARKET_WRAP", "증시 시황", (
        r"코스피", r"코스닥", r"증시", r"순매수", r"순매도", r"장중", r"마감", r"ETF", r"시가총액", r"지수")),
)
LABELS: dict[str, str] = {t.code: t.label for t in TYPES} | {OTHER: "기타"}
_COMPILED = [(t.code, [re.compile(p) for p in t.patterns]) for t in TYPES]
_ORDER = {t.code: i for i, t in enumerate(TYPES)}


@dataclass(frozen=True)
class Classified:
    code: str
    confidence: float
    scores: dict[str, int]


def _hits(patterns: list[re.Pattern], text: str) -> int:
    return sum(1 for p in patterns if p.search(text))


def classify_text(title: str, body: str) -> Classified:
    scores = {}
    for code, patterns in _COMPILED:
        score = TITLE_WEIGHT * _hits(patterns, title) + BODY_WEIGHT * _hits(patterns, body)
        if score:
            scores[code] = score
    best = sorted(scores, key=lambda c: (-scores[c], _ORDER[c]))
    if not best or scores[best[0]] < MIN_SCORE:
        return Classified(OTHER, 0.0, scores)
    return Classified(best[0], round(scores[best[0]] / sum(scores.values()), 6), scores)


def classify(article: Article) -> Classified:
    return classify_text(article.title, article.content)
