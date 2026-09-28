"""영문 기사에서 관계(SUPPLY/PARTNER/COMPETE)를 뽑는다. extract_relations.py 의 영문판이다.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe extract_relations_en.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe extract_relations_en.py --dump data/en_rel_hits.jsonl

왜 한국어 추출기를 못 쓰는가
────────────────────────────────────────────────────────────────────────────
그쪽은 조사(은/는/이/가, 에/에게)로 주어와 대상을 가른다. 영어에는 그 장치가 없다.
대신 어순과 동사가 방향을 준다: "A supplies B" / "A's supplier B" / "B supplier A".

후보 1,483문장을 실제로 읽고 얻은 함정 네 가지 (전부 실측)
────────────────────────────────────────────────────────────────────────────
1) 키워드 옆의 회사가 **제3자**인 경우가 가장 흔하다.
     "Hyundai Motor with auto supplier Aptiv"   -> 공급자는 Aptiv, 우리 쌍이 아니다
     "AirTag, a Tile competitor"                -> 경쟁사는 Tile
   그래서 키워드 바로 앞뒤의 고유명사가 A/B 가 아니면 버린다.
2) "versus / vs." 는 **숫자 비교**에 압도적으로 많이 쓰인다.
     "Q3 EPS of $1.95 versus $1.54 expected"
   관계 신호로 쓸 수 없어 아예 제외한다.
3) "supply chain" 은 관계가 아니라 배경 서술이다. supply 계열에서 따로 막는다.
4) 부정·무산 표현이 붙으면 관계가 성립하지 않는다.
     "Netflix didn't ultimately sell to Amazon"

한국어판과 같은 원칙을 지킨다 — 정밀도를 위해 재현율을 버린다.
그래프에서 틀린 엣지는 없는 엣지보다 나쁘다.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "en_sentences.csv"

# 키워드 바로 옆의 고유명사를 잡아 제3자인지 본다.
PROPER = re.compile(r"\b[A-Z][A-Za-z0-9&.\-]+(?:\s+[A-Z][A-Za-z0-9&.\-]+)?")
HEDGE = re.compile(r"\b(?:didn't|did not|doesn't|does not|no longer|not\s+a\b|never|"
                   r"denied|declined|rumor|reportedly|could|might|may|would|plans to|"
                   r"scrapped|terminated|ended|former)\b", re.I)
# 'supply chain' 은 관계가 아니라 배경 서술이다 (실측: SUPPLY 오탐의 주요 원인)
SUPPLY_NOISE = re.compile(r"supply\s+chain|supply\s+shortage|in\s+short\s+supply", re.I)

SUPPLY_KW = re.compile(r"\b(?:supplier|suppliers|supplies|supplying|supplied to|"
                       r"provider of|vendor for|manufactures for|makes .{0,20}for)\b", re.I)
PARTNER_KW = re.compile(r"\b(?:partner|partners|partnership|partnered|alliance|allied|"
                        r"collaborat\w+|joint venture|teamed up|team up|co-develop\w*)\b", re.I)
COMPETE_KW = re.compile(r"\b(?:competitor|competitors|rival|rivals|competes|competing|"
                        r"competition with|challenger to)\b", re.I)
# versus/vs. 는 제외한다 — 숫자 비교가 압도적이다

NEAR = 60          # 키워드는 두 기업 스팬 앞뒤 이 범위 안에 있어야 한다
MAX_GAP = 220      # 두 기업이 이보다 멀면 같은 절로 보지 않는다
THIRD_PARTY = 28   # 키워드 앞뒤 이 범위에서 제3자 고유명사를 찾는다
KW_DIST = 70       # 두 기업 각각이 키워드에서 이보다 멀면 다른 절의 이야기로 본다

# "including A, B, C" 처럼 제3자 기준으로 **나열**된 것은 두 기업의 관계가 아니다.
#   "observability vendors, including Datadog, especially ..." -> Datadog 과 뒤 기업은 무관
# 한국어판의 ENUM_THIRDPARTY 와 같은 취지다.
ENUM_LIST = re.compile(r"(?:including|such as|like|among them|namely|others? such as)", re.I)


TICKER_LIKE = re.compile(r"^[A-Z]{2,5}$")


def find(sent: str, names: list[str]) -> tuple[int, int] | None:
    """별칭 중 하나라도 나오는 첫 위치. 영문 경계를 지켜 Appleton 이 Apple 로 안 걸리게 한다.

    티커형 약어(COST, AMD…)는 **대소문자를 구분**해 찾는다. 소문자까지 허용하면
    영어 일반 단어와 충돌한다 — 실측: 'at lower cost.' 가 Costco(COST) 로 잡혀
    문장에 있지도 않은 기업의 관계가 만들어졌다.
    """
    best = None
    for n in names:
        if len(n) < 2:
            continue
        flags = 0 if TICKER_LIKE.match(n) else re.I
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", sent, flags)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.end())
    return best


def third_party_near(sent: str, kw_span: tuple[int, int], own: set[str]) -> str | None:
    """키워드 바로 옆의 고유명사가 우리 두 기업이 아니면 그 이름을 돌려준다.

    'auto supplier Aptiv', 'a Tile competitor' 를 막는 장치다. 이게 없으면
    제3자 관계를 우리 쌍의 관계로 잘못 기록한다 (실측 오탐 1위).
    """
    lo = max(0, kw_span[0] - THIRD_PARTY)
    hi = min(len(sent), kw_span[1] + THIRD_PARTY)
    for m in PROPER.finditer(sent[lo:hi]):
        cand = m.group(0).strip(". ")
        if len(cand) < 3 or cand.lower() in {"the", "and", "inc", "corp"}:
            continue
        if any(o.lower() in cand.lower() or cand.lower() in o.lower() for o in own):
            continue
        return cand
    return None


def classify_sentence(sent: str, found: list[tuple[str, int, int]], drops):
    """키워드를 먼저 찾고 그 **양옆 기업**을 본다.

    쌍을 먼저 만들고 키워드를 찾으면, 문장에 기업이 셋 이상일 때 엉뚱한 쌍이 맺힌다.
    실측 오탐: "Meta struck a deal with Nvidia's GPUs" 문장에서 AMD->NVDA 가 나왔다.
    키워드 기준으로 뒤집으면 그런 조합 자체가 생기지 않는다.

    found: [(ticker, start, end)] — 문장에 등장한 기업들, 위치순
    """
    out = []
    for rel, kw in (("SUPPLY", SUPPLY_KW), ("PARTNER", PARTNER_KW), ("COMPETE", COMPETE_KW)):
        for m in kw.finditer(sent):
            ks, ke = m.span()
            if rel == "SUPPLY" and SUPPLY_NOISE.search(sent[max(0, ks - 20):ke + 20]):
                drops["supply chain 배경 서술"] += 1
                continue
            # 키워드가 두 기업 **사이**에 있어야 한다. 밖에 있으면 다른 절의 이야기다.
            left = [f for f in found if f[2] <= ks]
            right = [f for f in found if f[1] >= ke]
            if not left or not right:
                drops["키워드가 두 기업 사이가 아님"] += 1
                continue
            a = left[-1]                      # 키워드 바로 왼쪽 기업
            b = right[0]                      # 키워드 바로 오른쪽 기업
            if a[0] == b[0]:
                drops["같은 기업"] += 1
                continue
            if b[2] - a[1] > MAX_GAP:
                drops["두 기업이 너무 멂"] += 1
                continue
            # 키워드에서 멀리 떨어진 기업은 그 키워드의 주체가 아니다
            if ks - a[2] > KW_DIST or b[1] - ke > KW_DIST:
                drops["기업이 키워드에서 멂"] += 1
                continue
            if HEDGE.search(sent[max(0, a[1] - 40):b[2] + 40]):
                drops["부정·추측 표현"] += 1
                continue
            # 두 기업 사이가 나열이면 버린다 ("including Datadog, ... Costco")
            between = sent[a[2]:b[1]]
            if ENUM_LIST.search(between) or ENUM_LIST.search(sent[max(0, a[1] - 35):a[1]]):
                drops["제3자 기준 나열"] += 1
                continue
            # own 에는 **실제 매칭된 기업명만** 넣는다. 예전에 40자 문장 조각을 넣었더니
            # 그 안에 든 제3자 이름(IBM 등)이 '우리 기업'으로 통과해 가드가 무력화됐다.
            own = {sent[a[1]:a[2]], sent[b[1]:b[2]]}
            tp = third_party_near(sent, (ks, ke), own)
            if tp:
                drops[f"제3자({tp})"] += 1
                continue
            # 방향: "Apple supplier LG Innotek" 은 뒤쪽이 공급자,
            #       "Micron supplies memory to Apple" 은 앞쪽이 공급자다.
            if rel == "SUPPLY":
                noun_form = re.search(r"supplier[s]?$", m.group(0), re.I)
                src, dst = (b, a) if noun_form else (a, b)
            else:
                # PARTNER/COMPETE 는 무방향이다. 같은 관계가 방향만 바뀌어 두 번
                # 들어가지 않도록 티커 순으로 정규화한다.
                src, dst = (a, b) if a[0] <= b[0] else (b, a)
            out.append((rel, src[0], dst[0], m.group(0)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC, type=Path)
    ap.add_argument("--dump", type=Path)
    ap.add_argument("--aliases", default=HERE.parent / "ner" / "data" / "aliases.csv", type=Path)
    args = ap.parse_args()

    # 티커를 직접 키로 쓴다. name_official 로 묶었더니 'AMD' 처럼 별칭만 있고
    # 정식명이 다른 기업(Advanced Micro Devices)에서 티커가 nan 이 됐다.
    by_ticker = collections.defaultdict(set)
    for r in pd.read_csv(args.aliases, encoding="utf-8-sig").itertuples(index=False):
        t = str(getattr(r, "ticker", "") or "").strip()
        a = str(getattr(r, "alias", "") or "").strip()
        if t and t.lower() != "nan" and len(a) >= 3 and not re.search(r"[가-힣]", a):
            by_ticker[t].add(a)
    alias_list = [(t, sorted(v, key=len, reverse=True)) for t, v in by_ticker.items()]
    print(f"영문 별칭을 가진 기업 {len(alias_list)}개")

    # 별칭 648개를 문장마다 따로 정규식 돌리면 7만 문장에 157분이다. 하나로 합치면 2.3분.
    # 티커형 약어는 대소문자를 구분해야 하므로 두 벌로 나눠 만든다.
    def build(pairs):
        pairs = sorted(pairs, key=lambda x: -len(x[0]))
        body = "|".join(f"(?P<g{i}>{re.escape(n)})" for i, (n, _) in enumerate(pairs))
        return r"(?<![A-Za-z0-9])(?:" + body + r")(?![A-Za-z0-9])", [t for _, t in pairs]

    ci, cs = [], []
    for t, names in by_ticker.items():
        for n in names:
            (cs if TICKER_LIKE.match(n) else ci).append((n, t))
    pat_ci, tick_ci = build(ci)
    pat_cs, tick_cs = build(cs)
    scanners = [(re.compile(pat_ci, re.I), tick_ci), (re.compile(pat_cs), tick_cs)]

    df = pd.read_csv(args.src)
    hits, drops = [], collections.Counter()
    for row in df.itertuples(index=False):
        sent = str(row.sentence_text or "")
        if len(sent) < 25:
            continue
        first = {}
        for rx, ticks in scanners:
            for m in rx.finditer(sent):
                t = ticks[int(m.lastgroup[1:])]
                if t not in first:
                    first[t] = (m.start(), m.end())
        if len(first) < 2:
            drops["기업 1개 이하"] += 1
            continue
        found = [(t, a, b) for t, (a, b) in first.items()]
        found.sort(key=lambda x: x[1])
        for rel, src, dst, kw in classify_sentence(sent, found, drops):
            hits.append({"document_id": row.document_id, "rel_type": rel,
                         "src_ticker": src, "dst_ticker": dst,
                         "keyword": kw, "sentence": sent})

    print(f"입력 문장 {len(df):,}")
    print(f"추출 {len(hits):,}건  {dict(collections.Counter(h['rel_type'] for h in hits))}")
    print("버린 이유:")
    for k, v in drops.most_common(7):
        print(f"  {k:<28} {v:,}")
    pairs = {(h["rel_type"], h["src_ticker"], h["dst_ticker"]) for h in hits}
    print(f"고유 관계 {len(pairs)}개")

    if args.dump:
        with args.dump.open("w", encoding="utf-8") as f:
            for h in hits:
                f.write(json.dumps(h, ensure_ascii=False) + chr(10))
        print(f"-> {args.dump}")


if __name__ == "__main__":
    main()
