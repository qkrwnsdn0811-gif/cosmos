"""Dictionary-based company mention matcher (Aho-Corasick + boundary rules + disambiguation).

    from matcher import CompanyMatcher
    m = CompanyMatcher.from_csv("data/aliases.csv")
    result = m.match(title, body)
    result.mentions      # list[Mention] with span, ticker, alias, method, confidence
    result.by_ticker()   # aggregated rows: ticker, n_mentions, first_pos, confidence, method
    result.unresolved    # group-name hits that could not be attributed

Rules (in order):
  1. Aho-Corasick finds every alias occurrence (case-sensitive).
  2. Boundary filter
     - left : previous char must not be Hangul or ASCII alnum        ("SKT" must not yield KT)
     - right: next char must not be ASCII alnum                      ("KTX" must not yield KT)
              next Hangul is allowed only when it starts a particle   ("삼성전자는" ok, "하이브리드" no)
     - ticker_only aliases need "(", "$", ":" before or ")" after     ("삼성전자(005930)")
  3. Leftmost-longest overlap resolution. Blockers win their span and emit nothing
     ("삼성전자우", "한화 이글스", "SK온").
  4. Group aliases (needs_context): dropped when a member company is present in the same
     document, otherwise emitted with the default ticker at low confidence, or reported as unresolved.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # fast C backend on dev machines; the Spark cluster has no pip, so fall back to a regex scanner
    import ahocorasick
except ImportError:  # pragma: no cover
    ahocorasick = None


class _RegexScanner:
    """Dependency-free stand-in for ahocorasick.Automaton.iter().

    Finds every alias occurrence, including overlapping ones, by running a longest-first
    alternation at every position where a match can start (re.finditer only reports
    non-overlapping matches, so we re-scan from start+1 after each hit).
    """

    def __init__(self, words: list[str]):
        pattern = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
        self._re = re.compile(pattern)
        # any alias that is a prefix of a longer alias must also be reported at the same start
        self._by_prefix: dict[str, list[str]] = {}
        wordset = set(words)
        for w in words:
            shorter = [w[:i] for i in range(2, len(w)) if w[:i] in wordset]
            if shorter:
                self._by_prefix[w] = shorter

    def iter(self, text: str):
        pos = 0
        n = len(text)
        while pos < n:
            m = self._re.search(text, pos)
            if not m:
                return
            s, e, w = m.start(), m.end(), m.group(0)
            yield e - 1, w
            for shorter in self._by_prefix.get(w, ()):
                yield s + len(shorter) - 1, shorter
            pos = s + 1

HANGUL_RE = re.compile(r"[가-힣]")
ALNUM_RE = re.compile(r"[A-Za-z0-9]")
# Korean particles / suffixes that may directly follow a company name
PARTICLES = (
    "은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "에", "로", "만", "측", "등", "께",
    "서", "엔", "선", "랑", "나", "부터", "까지", "처럼", "보다", "조차", "마저", "밖에", "한테", "에게",
    "으로", "및", "대", "발", "산", "표", "판", "행", "채", "주", "株", "側", "내", "간", "식", "형",
    "다", "라", "였", "임", "이라", "이란", "이면", "이며", "이자", "라고", "라는", "라며",
)
# Scraped article bodies often end with sidebars / recommendation feeds / newsletter boilerplate that name other
# companies ("Stocks that made our list ... Nvidia", "[뉴스핌 베스트 기사]", blog hashtags followed by related posts).
# When one of these markers appears in the second half of the text, matching stops there.
# 짧고 일반적인 한글 표현은 줄머리에 있을 때만 사이드바로 인정한다. 본문 안에서도 쓰이기
# 때문이다 — "강수연 관련 기사", "▶관련기사"는 기사 본문이지 피드 머리글이 아니다.
BOILERPLATE_MARKERS_LINESTART = (
    "많이 본 뉴스", "주요 뉴스", "오늘의 상승종목", "마켓 뉴스", "실시간 암호화폐 시세", "[주요포토]",
    "종목 추적기", "관련기사", "관련 기사", "함께 보면 좋은", "추천 기사", "인기 기사",
)
# 길고 고유해서 위치를 가릴 필요가 없는 정형구. Motley Fool 고지문은 문단 중간에서 시작한다.
BOILERPLATE_MARKERS_ANY = (
    "[뉴스핌 베스트 기사]", "blog.naver.com",
    "Stocks that made our list", "Stock Advisor", "if you invested $", "made this list on", "The Motley Fool",
    "StockStory", "Our Top 5", "Read more here", "Sign up for", "Subscribe to", "Disclosure:", "Disclaimer:",
    "This article was originally published", "Benzinga does not provide",
)
BOILERPLATE_MARKERS = BOILERPLATE_MARKERS_LINESTART + BOILERPLATE_MARKERS_ANY   # 진단·테스트용
HASHTAG_BLOCK_RE = re.compile(r"(?:#[^\s#]{2,}\s*){3,}")


def trim_boilerplate(text: str) -> int:
    """Return the cut position (len(text) when nothing is trimmed).

    v1.2 는 본문 앞 40%(BOILERPLATE_MIN_FRACTION=0.4) 안에서는 절대 자르지 않았다. 그런데
    스크랩된 본문 중에는 기사 자체가 한두 줄이고 나머지가 전부 사이드바인 것들이 있다 —
    "[인사] 새만금개발청" 본문 58자 뒤에 "주요 뉴스" 피드가 붙어 삼성전자·SK하이닉스가
    잡히는 문서가 그렇다. 앞 40% 보호가 바로 이런 문서를 통째로 살려두고 있었다.
    전문 200건 회귀셋 기준 floor 를 없애면 오탐 멘션 28개가 사라지고 새로 생기는 멘션은
    0개다(문서 4건: 인사공고·상한가봇·촛불집회·StockStory). 위치 기준 보호를 없앤 대신
    짧은 한글 마커에 줄머리 조건을 걸어 본문 속 "관련 기사" 표현을 지킨다.

    폰트위젯("가장작게/작게/기본/크게/가장크게")은 마커로 넣지 않는다. 그건 본문 앞에
    붙는 껍데기라 거기서 자르면 진짜 기사가 통째로 날아간다(5f9455d7 상한가 기사).
    """
    cut = len(text)
    for mk in BOILERPLATE_MARKERS_ANY:
        i = text.find(mk)
        if 0 <= i < cut:
            cut = i
    for mk in BOILERPLATE_MARKERS_LINESTART:
        i = text.find(mk)
        while 0 <= i < cut:
            if i == 0 or text[i - 1] == "\n":
                cut = i
                break
            i = text.find(mk, i + 1)
    m = HASHTAG_BLOCK_RE.search(text)
    if m and m.start() < cut:
        cut = m.start()
    return cut

GROUP_MEMBERS = {
    "SAMSUNG": ("삼성",), "HYUNDAI": ("현대", "HD현대"), "HYUNDAI_MOTOR": ("현대차", "기아", "현대모비스", "현대글로비스", "현대오토에버", "현대로템", "현대건설"),
    "HD_HYUNDAI": ("HD현대", "HD한국조선해양"), "LG": ("LG",), "SK": ("SK",), "GS": ("GS",), "LS": ("LS",),
    "HANWHA": ("한화",), "DOOSAN": ("두산",), "POSCO": ("POSCO", "포스코"), "MIRAE": ("미래에셋",),
    "KAKAO": ("카카오",), "KB": ("KB",), "SHINHAN": ("신한",), "CELLTRION": ("셀트리온",),
    "HANJIN": ("한진칼", "대한항공"), "ALPHABET": ("Alphabet",),
}
# "70억2000만달러(한화 약 10조849억원)" is 韓貨 - a won conversion of a foreign amount, not the Hanwha group.
# Both halves are required: a won amount right after the alias AND a foreign currency just before it.
KRW_CONVERSION_ALIASES = frozenset({"한화"})
KRW_AMOUNT_RE = re.compile(r"\s*(?:약|기준)?\s*\d[\d,.\s조억만천백]*\s*원")
FOREIGN_CURRENCY_RE = re.compile(
    r"달러|유로|파운드|위안|엔화|바트|페소|루피|링깃|프랑|루블|헤알|리라|디르함|크로나|\d\s*엔"
)
KRW_CONVERSION_LOOKBEHIND = 40


def is_krw_conversion(text: str, start: int, end: int) -> bool:
    if not KRW_AMOUNT_RE.match(text, end):
        return False
    return bool(FOREIGN_CURRENCY_RE.search(text[max(0, start - KRW_CONVERSION_LOOKBEHIND):start]))


# "(주관사 KB)", "삼성·신한·교보·다올 등 4개 증권사"에서 그룹 약칭은 지주사가 아니라 202종목에
# 없는 증권 계열사(KB증권·신한투자증권)를 가리킨다. "신한투자증권"처럼 접미사가 붙은 표기는
# _right_ok 가 이미 막으므로, 남은 누수는 접미사 없이 단독으로 쓰이는 두 자리뿐이다.
BROKER_ROLE_PREFIX_RE = re.compile(r"(?:대표주관|공동주관|주관사|주간사|인수단|모집주선|주선인)\s*[:=(\[]?\s*$")
BROKER_ROLE_LOOKBEHIND = 12
BROKER_ENUM_DELIM = frozenset("·ㆍ∙・,")   # frozenset: 문서 끝/시작의 빈 문자열이 통과하지 않는다
BROKER_ENUM_FORWARD = 60
_SENT_END_RE = re.compile(r"[.!?\n]")


def is_broker_role(text: str, start: int, end: int, name_official: str) -> str | None:
    """그룹 약칭이 '증권사 자리'에 놓였으면 사유, 아니면 None."""
    if "증권" in (name_official or ""):
        return None  # 미래에셋 -> 미래에셋증권(006800). 증권사 문맥이 오히려 근거다.
    if BROKER_ROLE_PREFIX_RE.search(text[max(0, start - BROKER_ROLE_LOOKBEHIND):start]):
        return "role_prefix"
    left = text[start - 1] if start else ""
    right = text[end] if end < len(text) else ""
    if left in BROKER_ENUM_DELIM or right in BROKER_ENUM_DELIM:
        fwd = text[end:end + BROKER_ENUM_FORWARD]
        m = _SENT_END_RE.search(fwd)
        if m:
            fwd = fwd[:m.start()]  # 문장 경계를 넘어가지 않는다
        if "증권사" in fwd:
            return "enum_broker"
    return None


# safe 등급인데 일반명사·지명과 동형인 별칭들. is_krw_conversion 과 같은 **앵커 방식**이다 —
# 멘션 바로 뒤(또는 바로 앞)만 본다. 창 검색으로 만들면 "아마존은 브라질 아마존 열대우림
# 인근에 데이터센터를 짓는다" 같은 혼합 문장에서 앞쪽 회사 멘션까지 같이 죽는다.
AMAZON_NATURE_RIGHT_RE = re.compile(
    r"\s*(?:강|강변)?\s*(?:의)?\s*"
    r"(?:열대\s*우림|열대림|우림|밀림|정글|원주민|부족민|삼각주|아나콘다|피라니아)")
AMAZON_NATURE_EN_RE = re.compile(
    r"\s*(?:[Rr]ain\s?forest|[Jj]ungle|[Bb]asin|[Rr]iver\b|[Dd]eforestation|[Tt]ribe|[Aa]naconda|[Pp]iranha)")
# 조사는 '의'와 무조사만 받는다. '은|는|과|와|에서|에'까지 받으면 "아마존과 환경단체가 협약",
# "아마존에서 다큐를 스트리밍" 같은 정상 회사 문맥이 죽는다. '숲|산림|보호구역|생물다양성'도
# 뺐다 - ESG 기사의 범용어라 회사 기사와 충돌한다.

APR_RATE_LEFT_RE = re.compile(
    r"(?:연\s*이율|연이율|연리|이자율|금리|APY|리볼빙|할부|무이자|대출|annual percentage rate)"
    r"\s*[(（]?\s*$|\d{1,3}(?:\.\d+)?\s*%\s*[(（]?\s*$")
# 우측 "APR 19.9%" 분기는 쓰지 않는다. 한국 증시기사의 표준 표기 "<종목명> 3.2%" 와 정면충돌한다.

SAMBA_RIGHT_RE = re.compile(r"\s*(?:축구|군단|리듬|댄스|춤|카니발|축제|스텝|퍼레이드|무희|드럼)")
SAMBA_LEFT_RE = re.compile(r"(?:브라질|리우|카니발|월드컵|남미)\s*$")

ORION_RIGHT_RE = re.compile(r"\s*(?:capsule|spacecraft|crew|mission|rocket|program|캡슐|우주선|성운|미션)")
ORION_LEFT_RE = re.compile(r"(?:NASA|ESA|Artemis|아르테미스|나사|Lockheed)[’'s\s]*$")

STRATEGY_LEFT_RE = re.compile(
    r"(?:콘텐츠|컨텐츠|브랜드|마케팅|캠페인|크리에이티브|세일즈|채용|HR)\s*$")
# MSTR 은 비즈니스 인텔리전스 기업이라 '비즈니스|디지털|데이터|미디어' 는 넣으면 안 된다.


def _amazon_nature(text: str, start: int, end: int) -> bool:
    return bool(AMAZON_NATURE_RIGHT_RE.match(text, end))


def _amazon_nature_en(text: str, start: int, end: int) -> bool:
    return bool(AMAZON_NATURE_EN_RE.match(text, end))


def _apr_rate(text: str, start: int, end: int) -> bool:
    return bool(APR_RATE_LEFT_RE.search(text[max(0, start - 14):start]))


def _samba_dance(text: str, start: int, end: int) -> bool:
    return bool(SAMBA_RIGHT_RE.match(text, end) or SAMBA_LEFT_RE.search(text[max(0, start - 8):start]))


def _orion_space(text: str, start: int, end: int) -> bool:
    return bool(ORION_RIGHT_RE.match(text, end) or ORION_LEFT_RE.search(text[max(0, start - 12):start]))


def _strategy_compound(text: str, start: int, end: int) -> bool:
    return bool(STRATEGY_LEFT_RE.search(text[max(0, start - 12):start]))


# 별칭 표면형 -> 부정 문맥 판정. 같은 종목의 표기가 여럿이면 전부 등록해야 구멍이 안 생긴다.
NEGATIVE_CONTEXT = {
    "아마존": _amazon_nature, "Amazon": _amazon_nature_en,
    "APR": _apr_rate,
    "삼바": _samba_dance,
    "Orion": _orion_space, "ORION": _orion_space, "Orion Corp": _orion_space,
    "스트래티지": _strategy_compound,
}


GROUP_CONFIDENCE = 0.5
PRODUCT_CONFIDENCE = 0.7  # product name (페이스북, 아이폰) counted only when the company is also named

# Sports articles refer to teams by the sponsor's group name ("한화의 선발투수", "삼성과 신한 SOL 뱅크 KBO").
# When a document looks like sports coverage, group names are not attributed to the holding company.
# 국토교통부 실거래가 오픈API 로 자동 생성되는 시세 기사에서 따옴표 안의 이름('두산')은
# 기업이 아니라 아파트 단지명이다. 바이라인 한 줄만으로 발동하면 사람이 쓴 부동산 기사까지
# 걸리므로, 정형 템플릿 문구 2개 또는 템플릿 1개+봇 바이라인을 요구한다.
REALTY_BOT_BYLINE = ("부동산뉴스봇", "부동산 시세분석 전문기자")
REALTY_BOT_TEMPLATE = ("실거래가 공개시스템", "중위거래가격", "실거래가 추이", "거래량 월별 추이")


def is_realty_bot(scan_text: str) -> bool:
    tmpl = sum(1 for k in REALTY_BOT_TEMPLATE if k in scan_text)
    return tmpl >= 2 or (tmpl >= 1 and any(k in scan_text for k in REALTY_BOT_BYLINE))


SPORTS_KEYWORDS = (
    "KBO", "프로야구", "플레이오프", "포스트시즌", "한국시리즈", "선발투수", "선발 투수", "이닝", "홈런", "타율",
    "타점", "등판", "불펜", "구원", "K리그", "프로축구", "프로농구", "프로배구", "V리그", "V-리그", "KBL", "WKBL",
    "구단", "감독은", "감독이", "리그 ", "경기에서", "연승", "연패", "승리를", "패배", "결승", "준결승", "우승",
    "FC ", "FC와", "FC의", "라운드", "세트 ", "골을", "득점", "MVP", "배구", "농구", "세터", "리시브", "블로킹",
    "공격 성공률", "경기 기록", "선수들", "홈경기", "원정경기", "시즌 개막", "창단",
)
SPORTS_MIN_KEYWORDS = 2
# Companies that are also the name of a pro team or its sponsor. In a sports article their dictionary hits are
# downgraded to confidence 0.4 / method "rule" (kept for audit, excluded by the default 0.5 threshold).
SPORTS_SPONSOR_TICKERS = frozenset({
    "030200",  # KT (위즈, 소닉붐)          "024110",  # 기업은행 (알토스)
    "024110", "015760",  # 한국전력 (빅스톰)   "003490",  # 대한항공 (점보스)
    "003490", "000810",  # 삼성화재 (블루팡스) "000720",  # 현대건설 (힐스테이트)
    "000720", "033780",  # KT&G (정관장)      "105560",  # KB금융 (KB손보 스타즈, KB스타즈)
    "105560", "055550",  # 신한지주 (에스버드) "086790",  # 하나금융지주 (하나원큐)
    "086790", "316140",  # 우리금융지주 (우리WON) "032830",  # 삼성생명 (블루밍스)
    "032830", "005830",  # DB손해보험 (프로미) "000270",  # 기아 (타이거즈)
    "000270", "034730",  # SK (나이츠)         "003550",  # LG (트윈스, 세이커스)
    "003550", "000150",  # 두산 (베어스)        "000880",  # 한화 (이글스)
    "000880", "039490",  # 키움증권 (히어로즈)  "012330",  # 현대모비스 (피버스)
    "012330", "011200",  # HMM? no team — placeholder removed below
})
SPORTS_SPONSOR_TICKERS = SPORTS_SPONSOR_TICKERS - {"011200"}
SPORTS_SPONSOR_CONFIDENCE = 0.4
# When a team name is the ONLY sports signal, the article may just be naming a stadium or a
# sponsorship; downgrade the sponsor's mentions near that team name instead of the whole document.
SPORTS_DOWNGRADE_WINDOW = 200

# Teaser headlines of other articles inserted between paragraphs (StockStory, Barchart, Benzinga...):
# "→ Intel's Black Friday Breakout: ...", "Is Meta Stock a Buy?". Masked (replaced by spaces) before matching so
# offsets stay valid.
INSERTED_HEADLINE_RE = re.compile(
    r"^[ \t]*(?:→|▶|►|»|Related:|RELATED:|Read also:?|READ MORE:?|Read more:?|ALSO READ:?|See also:?|More:|Recommended:?|"
    r"Trending:?|Don't miss:?|Missed .{1,60}\?).*$"
    r"|^[ \t]*(?:Is|Should|Here's|Why|How|Can|Will|What|Where|Which|Buy|Sell|Forget|Time to|This|These|The)\b"
    r"(?![^\n]*\. )[^\n]{2,130}(?:Stock|Stocks|Buy\?|Sell\?|Rally|Breakout|Split|Earnings|Analyst|Bet|Dividend|Upside|Downside)[^\n]{0,60}$",
    re.MULTILINE,
)


def mask_inserted_headlines(text: str) -> str:
    def blank(m):
        return " " * (m.end() - m.start())
    return INSERTED_HEADLINE_RE.sub(blank, text)
SPORTS_TEAM_HINT = frozenset({
    "삼성 라이온즈", "삼성라이온즈", "한화 이글스", "한화이글스", "LG 트윈스", "LG트윈스", "두산 베어스", "두산베어스",
    "KT 위즈", "KT위즈", "kt wiz", "KT wiz", "기아 타이거즈", "KIA 타이거즈", "KIA타이거즈", "키움 히어로즈", "키움히어로즈",
    "SK 와이번스", "SSG 랜더스", "롯데 자이언츠", "NC 다이노스", "삼성 블루팡스", "삼성 썬더스", "현대캐피탈 스카이워커스",
    "LG 세이커스", "KT 소닉붐", "SK 나이츠", "SK 핸드볼", "한화 핸드볼", "현대모비스 피버스",
})


# SPORTS_TEAM_HINT 는 리터럴이라 "기아(KIA) 타이거즈" 처럼 토큰 사이에 뭐가 끼면 못 막는다.
# 스폰서와 마스코트만 고정하고 사이는 유연하게 본다. 구분자에 하이픈·중점은 넣지 않는다 —
# "기아-교육부", "삼성·신한·교보" 처럼 조직 병기용이라 구단명 내부 구분자가 아니다.
SPORTS_TEAM_SPONSORS = (
    "삼성화재", "삼성", "한화", "LG", "두산", "KT", "kt", "KIA", "기아", "키움",
    "SK", "SSG", "롯데", "NC", "현대캐피탈", "현대모비스", "IBK기업은행", "기업은행", "IBK",
)
# '핸드볼'(일반 종목명)과 '알토스'(알토스벤처스와 충돌)는 넣지 않는다. blockers.txt 의
# 기존 리터럴('SK 핸드볼', '기업은행 알토스' 등)이 이미 커버한다.
SPORTS_TEAM_MASCOTS = (
    "라이온즈", "이글스", "트윈스", "베어스", "위즈", "wiz", "WIZ", "Wiz", "타이거즈", "히어로즈",
    "와이번스", "랜더스", "자이언츠", "다이노스", "블루팡스", "썬더스", "스카이워커스", "세이커스",
    "소닉붐", "나이츠", "피버스",
)
_SPT = "|".join(sorted((re.escape(x) for x in SPORTS_TEAM_SPONSORS), key=len, reverse=True))
_MST = "|".join(sorted((re.escape(x) for x in SPORTS_TEAM_MASCOTS), key=len, reverse=True))
_TSEP = r"(?:\([A-Za-z0-9.&\- ]{1,12}\)|[ \t\u00a0])"
SPORTS_TEAM_RE = re.compile(
    r"(?<![A-Za-z0-9가-힣])(?:%s)%s{0,4}(?:%s)(?![A-Za-z0-9])" % (_SPT, _TSEP, _MST))


# 한국어는 나열의 마지막 항목에만 공통 접미사를 붙인다: "신한, KB국민, 하나, 우리은행".
# 앞 세 개는 사전에 없는 표기라 통째로 미탐이었다. 마지막 항목의 접미사를 앞으로 되돌려
# 사전을 다시 조회한다. 전개 결과는 전부 이미 사전에 있는 safe 별칭이라 시드 수정은 없다.
ELLIPSIS_SUFFIXES = tuple(sorted((
    "에어로스페이스", "엔지니어링", "생활건강", "디스플레이", "중공업", "캐피탈", "자동차", "홀딩스",
    "텔레콤", "케미칼", "백화점", "에너지", "은행", "증권", "화재", "생명", "전자", "카드", "금융",
    "건설", "제철", "화학", "바이오", "해운", "항공", "지주", "보험", "물산", "공업", "기계", "조선",
    "제약", "통신", "손보", "산업", "오션", "쇼핑", "차",
), key=len, reverse=True))
LIST_SEPS = frozenset(",·ㆍ‧∙•/、")
LIST_ITEM_RE = re.compile(r"[가-힣A-Za-z0-9&]+$")
LIST_ITEM_HAS_LETTER_RE = re.compile(r"[가-힣A-Za-z]")   # 숫자만인 토큰은 나열 항목이 아니다
MAX_LIST_ITEMS = 6
MAX_LIST_ITEM_LEN = 10
# 범주명사로 접미사를 역추론한다: "… 등 4개 증권사" -> 증권
CATEGORY_NOUNS = {"증권사": "증권", "시중은행": "은행", "카드사": "카드", "보험사": "보험",
                  "생명보험사": "생명", "손해보험사": "화재", "건설사": "건설", "완성차": "차"}
CATEGORY_WINDOW = 60
# 일반명사·지명과 동형이라 단독으로는 전개하지 않는 어두. 같은 나열에서 2개 이상이 해결될 때만 인정한다.
AMBIGUOUS_HEAD = frozenset({
    "우리", "국민", "기업", "한국", "대한", "미국", "중국", "정부", "서울", "대구", "부산", "광주",
    "전북", "경남", "제주", "대덕", "전자", "산업", "하나", "암", "기아", "대우",
})
ELLIPSIS_CONFIDENCE = 0.9        # dict(1.0) 보다 한 단계 낮춰 감사 가능하게
ELLIPSIS_GROUP_CONFIDENCE = 0.8  # "삼성·LG전자" 의 '삼성'은 그룹 전체를 뜻할 수도 있다


def _ellipsis_suffix_of(alias: str) -> str | None:
    for suf in ELLIPSIS_SUFFIXES:
        if alias.endswith(suf) and len(alias) > len(suf):
            return suf
    return None


def _walk_list_left(text: str, pos: int) -> list[tuple[int, int, str]]:
    """pos 왼쪽의 '구분자-항목' 연쇄를 훑는다. [(start, end, item)]"""
    items = []
    for _ in range(MAX_LIST_ITEMS):
        i = pos
        while i > 0 and text[i - 1] == " ":
            i -= 1
        if i == 0 or text[i - 1] not in LIST_SEPS:
            break
        i -= 1
        while i > 0 and text[i - 1] == " ":
            i -= 1
        m = LIST_ITEM_RE.search(text[:i])
        if not m or m.end() != i:
            break
        st, en = m.start(), m.end()
        tok = text[st:en]
        if en - st > MAX_LIST_ITEM_LEN or not LIST_ITEM_HAS_LETTER_RE.search(tok) or tok[0].isdigit():
            break
        if st > 0 and (HANGUL_RE.match(text[st - 1]) or ALNUM_RE.match(text[st - 1])):
            break  # _left_ok 와 같은 기준
        items.append((st, en, tok))
        pos = st
    return items


def _walk_list_right(text: str, pos: int, stop: int) -> list[tuple[int, int, str]]:
    """'(KB국민·신한·하나)' 처럼 괄호 안을 왼쪽에서 오른쪽으로 훑는다."""
    items, i = [], pos
    for _ in range(MAX_LIST_ITEMS):
        while i < stop and text[i] == " ":
            i += 1
        m = re.compile(r"[가-힣A-Za-z0-9&]+").match(text, i)
        if not m or m.end() > stop:
            break
        tok = m.group(0)
        if len(tok) > MAX_LIST_ITEM_LEN or not LIST_ITEM_HAS_LETTER_RE.search(tok) or tok[0].isdigit():
            break
        items.append((m.start(), m.end(), tok))
        i = m.end()
        while i < stop and text[i] == " ":
            i += 1
        if i >= stop or text[i] not in LIST_SEPS:
            break
        i += 1
    return items


@dataclass
class Alias:
    alias: str
    ticker: str
    name_official: str
    alias_type: str
    ambiguity: str
    group_id: str


@dataclass
class Mention:
    ticker: str
    alias: str
    start: int
    end: int
    method: str          # dict | rule
    confidence: float
    name_official: str = ""
    group_id: str = ""


@dataclass
class MatchResult:
    text: str
    title_len: int
    mentions: list[Mention] = field(default_factory=list)
    unresolved: list[Mention] = field(default_factory=list)
    blocked: list[tuple[str, int, int]] = field(default_factory=list)
    industries: list[Mention] = field(default_factory=list)   # sector expressions; ticker="" and group_id=industry_id
    is_sports: bool = False
    is_realty_bot: bool = False   # 국토부 실거래가 봇기사: 따옴표 안 이름이 단지명이라 그룹 약칭을 귀속하지 않는다
    trimmed_at: int | None = None   # char offset where sidebar/boilerplate was cut off (None = nothing cut)

    def by_industry(self) -> list[dict]:
        agg: dict[str, dict] = {}
        for m in sorted(self.industries, key=lambda x: x.start):
            a = agg.setdefault(m.group_id, {"industry_id": m.group_id, "industry_ko": m.name_official, "n_mentions": 0,
                                            "first_pos": self.position_of(m.start), "aliases": set()})
            a["n_mentions"] += 1
            a["aliases"].add(m.alias)
        for a in agg.values():
            a["aliases"] = sorted(a["aliases"])
        return list(agg.values())

    def position_of(self, start: int, lead_chars: int = 200) -> str:
        if start < self.title_len:
            return "title"
        if start < self.title_len + 1 + lead_chars:
            return "lead"
        return "body"

    def by_ticker(self) -> list[dict]:
        agg: dict[str, dict] = {}
        for m in sorted(self.mentions, key=lambda x: x.start):
            a = agg.setdefault(m.ticker, {
                "ticker": m.ticker, "name_official": m.name_official, "n_mentions": 0,
                "first_pos": self.position_of(m.start), "confidence": 0.0, "method": set(), "aliases": set(),
            })
            a["n_mentions"] += 1
            a["confidence"] = max(a["confidence"], m.confidence)
            a["method"].add(m.method)
            a["aliases"].add(m.alias)
        for a in agg.values():
            a["method"] = "+".join(sorted(a["method"]))
            a["aliases"] = sorted(a["aliases"])
        return list(agg.values())


class CompanyMatcher:
    def __init__(self, aliases: list[Alias]):
        self.aliases: dict[str, Alias] = {}
        for a in aliases:
            # groups win over same-string company aliases (build_aliases already avoids most cases)
            if a.alias in self.aliases and self.aliases[a.alias].alias_type == "group":
                continue
            self.aliases[a.alias] = a
        if ahocorasick is not None:
            self.automaton = ahocorasick.Automaton()
            for s in self.aliases:
                self.automaton.add_word(s, s)
            self.automaton.make_automaton()
            self.backend = "pyahocorasick"
        else:
            self.automaton = _RegexScanner(list(self.aliases))
            self.backend = "regex"

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "CompanyMatcher":
        return cls([Alias(r["alias"], r["ticker"], r["name_official"], r["alias_type"], r["ambiguity"], r["group_id"])
                    for r in rows])

    @classmethod
    def from_csv(cls, path: str | Path) -> "CompanyMatcher":
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        return cls([Alias(r["alias"], r["ticker"], r["name_official"], r["alias_type"], r["ambiguity"], r["group_id"])
                    for r in rows])

    # ---------------------------------------------------------------- boundaries
    @staticmethod
    def _left_ok(text: str, start: int) -> bool:
        if start == 0:
            return True
        p = text[start - 1]
        return not (HANGUL_RE.match(p) or ALNUM_RE.match(p))

    @staticmethod
    def _right_ok(text: str, end: int) -> bool:
        if end >= len(text):
            return True
        n = text[end]
        if ALNUM_RE.match(n):
            return False
        if HANGUL_RE.match(n):
            tail = text[end:end + 3]
            return any(tail.startswith(p) for p in PARTICLES)
        return True

    @staticmethod
    def _ticker_context_ok(text: str, start: int, end: int, ticker: str) -> bool:
        """Numeric tickers: '(005930)'. Latin tickers: '$NVDA', 'NASDAQ: NVDA', '나스닥:NVDA'.

        A bare '(NVDA)' is NOT enough for Latin tickers because Korean news puts acronyms in
        parentheses ('황반변성(AMD)', '스트리밍(FAST)'). Such cases are accepted later only when
        the token right before '(' is a name alias of the same ticker (see match()).
        """
        prev = text[start - 1] if start > 0 else ""
        prev2 = text[max(0, start - 2):start]
        nxt = text[end] if end < len(text) else ""
        if ALNUM_RE.match(nxt or " "):
            return False
        if prev == "$" or prev2 in (": ", ":", "：") or prev == ":":
            return True
        if ticker.isdigit():
            return prev == "(" or (nxt == ")" and not (ALNUM_RE.match(prev or " ") or HANGUL_RE.match(prev or " ")))
        return prev == "(" and nxt == ")"  # provisional; confirmed against an adjacent name in match()

    def _expand_ellipsis(self, text: str, res: "MatchResult") -> list[Mention]:
        """나열문에서 생략된 접미사를 되돌려 추가 멘션을 만든다. 스팬은 원문 토큰 위치 그대로."""
        taken = [(m.start, m.end) for m in res.mentions]
        out: list[Mention] = []

        def resolve(items, suffix):
            got = []
            for st, en, tok in items:
                a = self.aliases.get(tok + suffix)
                got.append((st, en, tok, a if (a and a.ambiguity == "safe") else None))
            return got

        def emit(resolved):
            n_res = sum(1 for *_x, a in resolved if a)
            for st, en, tok, a in resolved:
                if a is None:
                    continue
                if tok in AMBIGUOUS_HEAD and n_res < 2:
                    continue  # "정부와 우리, 미국 등 주요 은행" 류를 막는 유일한 장치
                if any(st < te and ts < en for ts, te in taken + [(m.start, m.end) for m in out]):
                    continue
                conf = ELLIPSIS_GROUP_CONFIDENCE if a.group_id in GROUP_MEMBERS else ELLIPSIS_CONFIDENCE
                out.append(Mention(a.ticker, a.alias, st, en, "ellipsis", conf, a.name_official, a.group_id))

        # A. 후행 앵커형: "신한, KB국민, 하나, 우리은행"
        for m in list(res.mentions):
            suffix = _ellipsis_suffix_of(m.alias)
            if suffix:
                emit(resolve(_walk_list_left(text, m.start), suffix))

        # B. 범주명사형: "삼성·신한·교보·다올 등 4개 증권사" / "5대 시중은행(KB국민·신한·하나)"
        for noun, suffix in CATEGORY_NOUNS.items():
            for mo in re.finditer(re.escape(noun), text):
                # B1: 앞쪽 " 등 " 나열. 둘 사이에 구두점이나 '과/와'가 끼면 다른 명사구다.
                seg = text[max(0, mo.start() - CATEGORY_WINDOW):mo.start()]
                k = seg.rfind(" 등 ")
                if k >= 0 and not re.search(r"[.,;]|과 |와 ", seg[k + 3:]):
                    anchor_pos = max(0, mo.start() - CATEGORY_WINDOW) + k
                    head = LIST_ITEM_RE.search(text[:anchor_pos])
                    items = _walk_list_left(text, anchor_pos)
                    if head and head.end() == anchor_pos and len(head.group(0)) <= MAX_LIST_ITEM_LEN:
                        items = [(head.start(), head.end(), head.group(0))] + items
                    emit(resolve(items, suffix))
                # B2: 괄호 나열 "시중은행(KB국민·신한·하나)"
                if mo.end() < len(text) and text[mo.end()] == "(":
                    close = text.find(")", mo.end())
                    if 0 < close <= mo.end() + 80:
                        emit(resolve(_walk_list_right(text, mo.end() + 1, close), suffix))
        return out

    # ---------------------------------------------------------------- matching
    def _candidates(self, text: str) -> list[tuple[int, int, Alias]]:
        out = []
        for end_idx, s in self.automaton.iter(text):
            end = end_idx + 1
            start = end - len(s)
            a = self.aliases[s]
            if a.ambiguity == "ticker_only":
                if not self._ticker_context_ok(text, start, end, a.ticker):
                    continue
            elif a.ambiguity == "blocked":
                # exclusion patterns only need a clean left edge and no ASCII continuation:
                # "IBK 기업은행 알토스전" must still consume "기업은행"
                nxt = text[end] if end < len(text) else ""
                if not self._left_ok(text, start) or ALNUM_RE.match(nxt or " "):
                    continue
            else:
                if not (self._left_ok(text, start) and self._right_ok(text, end)):
                    continue
                if a.alias in KRW_CONVERSION_ALIASES and is_krw_conversion(text, start, end):
                    continue
                neg = NEGATIVE_CONTEXT.get(a.alias)
                if neg is not None and neg(text, start, end):
                    continue
            out.append((start, end, a))
        # leftmost-longest
        out.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        chosen, last_end = [], -1
        for start, end, a in out:
            if start < last_end:
                continue
            chosen.append((start, end, a))
            last_end = end
        return chosen

    def match(self, title: str | None, body: str | None) -> MatchResult:
        title = title or ""
        body = body or ""
        text = title + "\n" + body
        res = MatchResult(text=text, title_len=len(title))
        cut = trim_boilerplate(text)
        if cut < len(text):
            res.trimmed_at = cut
        scan_text = mask_inserted_headlines(text[:cut])
        group_hits: list[Mention] = []
        product_hits: list[Mention] = []
        pending_tickers: list[Mention] = []
        team_flex = [(mo.start(), mo.end(), mo.group(0)) for mo in SPORTS_TEAM_RE.finditer(scan_text)]
        candidates = self._candidates(scan_text)
        for start, end, a in candidates:
            if any(start < te and ts < end for ts, te, _lit in team_flex):
                continue  # 구단명 스팬 안이다 (blocked 와 같은 의미론)
            if a.ambiguity == "blocked":
                res.blocked.append((a.alias, start, end))
                continue
            if a.ambiguity == "needs_context":
                group_hits.append(Mention(a.ticker, a.alias, start, end, "rule", GROUP_CONFIDENCE, a.name_official, a.group_id))
                continue
            if a.ambiguity == "product":
                product_hits.append(Mention(a.ticker, a.alias, start, end, "product", PRODUCT_CONFIDENCE, a.name_official, "PRODUCT"))
                continue
            if a.ambiguity == "industry":
                res.industries.append(Mention("", a.alias, start, end, "industry", 1.0, a.name_official, a.group_id))
                continue
            if a.ambiguity == "ticker_only":
                m = Mention(a.ticker, a.alias, start, end, "dict", 0.9, a.name_official, a.group_id)
                strong = a.ticker.isdigit() or text[start - 1:start] == "$" or text[start - 1:start] == ":" \
                    or text[max(0, start - 2):start] in (": ", "：")
                (res.mentions if strong else pending_tickers).append(m)
                continue
            res.mentions.append(Mention(a.ticker, a.alias, start, end, "dict", 1.0, a.name_official, a.group_id))

        # '(NVDA)' style Latin tickers: keep only when a name alias of the same ticker sits right before '('
        if pending_tickers:
            name_ends = {(m.ticker, m.end) for m in res.mentions + group_hits}
            for m in pending_tickers:
                before = m.start - 1  # index of '('
                while before > 0 and text[before - 1] == " ":
                    before -= 1
                if (m.ticker, before) in name_ends:
                    res.mentions.append(m)

        for _s, _e, _lit in team_flex:
            if not any(bs == _s for _b, bs, _be in res.blocked):
                res.blocked.append((_lit, _s, _e))
        res.blocked.sort(key=lambda b: b[1])
        sports_hits = sum(1 for k in SPORTS_KEYWORDS if k in text)
        # 리터럴 일치일 때만 문서를 스포츠로 판정한다(변형은 스팬 소거까지만)
        team_spans = [(st, en) for _a, st, en in res.blocked if _a in SPORTS_TEAM_HINT]
        res.is_sports = bool(team_spans) or sports_hits >= SPORTS_MIN_KEYWORDS
        # 사이드바가 본문을 게이트하지 못하도록 원문이 아니라 scan_text 를 본다
        res.is_realty_bot = is_realty_bot(scan_text)

        if group_hits:
            has_hangul = bool(HANGUL_RE.search(text))
            present_names = {m.name_official for m in res.mentions}
            present_tickers = {m.ticker for m in res.mentions}
            for g in group_hits:
                # "GS", "MS", "KB", "SK", "LG" mean other things in English text (Goldman Sachs, Morgan Stanley, kilobyte)
                if g.alias.isascii() and len(g.alias) <= 2 and not has_hangul:
                    continue
                prefixes = GROUP_MEMBERS.get(g.group_id, ())
                member_present = (g.group_id in present_tickers) or any(
                    n.startswith(p) for n in present_names for p in prefixes)
                if member_present:
                    continue  # attributed to the explicitly named member(s)
                if is_broker_role(text, g.start, g.end, g.name_official):
                    res.unresolved.append(g)  # 버리지 않고 미확정으로: 감사 추적이 남는다
                    continue
                if res.is_realty_bot:
                    res.unresolved.append(g)
                    continue
                if g.ticker and not res.is_sports:
                    res.mentions.append(g)
                else:
                    res.unresolved.append(g)

        if product_hits:
            # "페이스북에 글을 올렸다" is a platform mention, not a company mention. Keep product names only when
            # the company itself (메타/Meta, 애플...) is named somewhere in the same document.
            company_tickers = {m.ticker for m in res.mentions if m.method in ("dict", "rule")}
            if res.is_realty_bot:
                company_tickers |= {u.ticker for u in res.unresolved if u.ticker}
            for p in product_hits:
                (res.mentions if p.ticker in company_tickers else res.unresolved).append(p)

        expanded = self._expand_ellipsis(scan_text, res)
        if expanded:
            res.mentions.extend(expanded)
            spans = [(m.start, m.end) for m in expanded]
            # 전개로 확정된 자리는 미확정 목록에서 뺀다 (같은 스팬이 양쪽에 남지 않게)
            res.unresolved = [u for u in res.unresolved
                              if not any(u.start < e and s2 < u.end for s2, e in spans)]

        # 스포츠 강등은 전개 뒤에 둔다 - 전개된 스폰서 티커도 같은 규칙을 받아야 한다
        if res.is_sports:
            # "기업은행보다 잘했다" in a volleyball recap is the team, not the bank. A match recap is
            # sports end to end, but "수원 KT 위즈파크점" inside a phone-launch story is one stadium
            # name - there, only the mentions sitting next to the team name are the team.
            whole_doc = sports_hits >= SPORTS_MIN_KEYWORDS
            for m in res.mentions:
                if m.ticker not in SPORTS_SPONSOR_TICKERS or m.method not in ("dict", "ellipsis"):
                    continue
                if whole_doc or any(st - SPORTS_DOWNGRADE_WINDOW <= m.start <= en + SPORTS_DOWNGRADE_WINDOW
                                    for st, en in team_spans):
                    m.confidence, m.method = SPORTS_SPONSOR_CONFIDENCE, "rule"

        res.mentions.sort(key=lambda m: m.start)
        return res
