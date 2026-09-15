"""Knowledge graph step 6-3 — typed relations (SUPPLY / PARTNER / COMPETE) from candidate sentences.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe extract_relations.py --sample data/relation_sample.jsonl
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe extract_relations.py --sample ... --dump data/rel_hits.jsonl

프론트(`RELATIONSHIP_ORDER`)가 렌더링하는 4종 중 INVEST 는 DART 지분에서 오고, 나머지 3종을
여기서 만든다. 입력은 extract_relation_candidates.py 가 뽑은 "두 기업이 같은 문장에 있는" 후보.

표본 20,000문장을 실측해서 얻은 설계 근거 세 가지
------------------------------------------------
1) 연결사의 의미가 관계 유형마다 **뒤집힌다**. 키워드가 있는 국내 2기업 문장 1,944건 중
   40.6%가 "A와 B" 형태였는데, 이게 PARTNER 에는 정답 신호이고 SUPPLY 에는 오답 신호다.
       "LG전자와 메리어트의 협력은…"          -> PARTNER  (맞음)
       "플러그파워는 아마존과 월마트에 납품"    -> 둘은 나란히 **고객**이고 공급자는 제3자
   그래서 SUPPLY 는 나열 연결사를 만나면 버리고, 주어(는/가)-대상(에/에게) 형태만 받는다.
2) 키워드가 문장 어딘가 있다고 두 기업의 관계가 아니다. "SK하이닉스는 …80자… SK텔레콤"
   에서 '경쟁력'은 SK하이닉스 제품 설명이었다. 키워드는 **두 기업 스팬 근처**에 있어야 한다.
3) 주체가 유니버스 밖인 경우가 흔하다(플러그파워). 주어 자리에 우리 기업이 없으면 SUPPLY 는
   방향을 알 수 없으므로 버린다.

정밀도를 위해 재현율을 버리는 쪽으로 짰다. 관계 그래프에서 틀린 엣지는 없는 엣지보다 나쁘다.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- 키워드
# 경쟁'력'은 관계가 아니라 역량이다 — 실측에서 COMPETE 최대 오탐원이었다.
# 공급'망/과잉/부족'은 시황이지 두 기업 사이의 거래가 아니다.
KW = {
    "SUPPLY": re.compile(r"납품|공급(?!망|과잉|부족|난|처|업체를)|수주|위탁생산|파운드리|외주|조달(?!청)"),
    # '뉴스제휴평가위원회'는 기구 이름이지 두 회사의 제휴가 아니다(네이버-카카오 오탐).
    "PARTNER": re.compile(r"협력(?!사|업체|위원회|기구|단)|제휴(?!평가|심사)|파트너(?:십)?|협약|MOU|양해각서|합작|"
                          r"공동\s*(?:개발|연구|투자|설립|출자|진출)|맞손|손\s*잡"),
    "COMPETE": re.compile(r"경쟁(?!력|사와|업체와)|라이벌|맞붙|양강|점유율\s*(?:경쟁|다툼)|추격"),
}
# 부정·가정·추측. 이런 문장은 사실 진술이 아니라 버린다.
# '부정적/반대/우려'는 실측에서 샜다 — "삼성화재는 네이버 제휴에 부정적"을 제휴 관계로 잡았다.
HEDGE = re.compile(r"않|아니|없|무산|중단|철회|결렬|설(?:이|을|에)|루머|관측|전망이|검토\s*중|"
                   r"가능성|예정이|추진하기로|나설\s*것|할\s*계획|제기|부정적|반대|우려|난항|"
                   r"검토(?:하고|\s*중)")
# "A와 B, C 등 경쟁사보다" / "마이크로소프트, 시스코 등 파트너사와" — 나열된 회사들이 모두
# **제3자의** 경쟁사·협력사라는 뜻이지 A와 B가 서로 그렇다는 말이 아니다. SUPPLY 의 '같은 편'
# 문제와 동형이고 PARTNER·COMPETE 양쪽에서 났다. 나열 항목이 더 붙을 수 있으므로 뒤를 넓게 본다.
ENUM_THIRDPARTY = re.compile(r"[^가-힣](?:등|등의)\s*(?:다양한\s*)?(?:글로벌\s*)?(?:리딩\s*)?"
                             r"(?:경쟁사|경쟁업체|파트너|협력사|협력업체|업체|기업|회사)")
ENUM_LOOKAHEAD = 25    # 별칭 뒤 이 범위까지 나열 꼬리표를 찾는다
# "애플에 카메라 모듈을 공급하는 LG이노텍" — 관계절. 앞 기업이 대상, 뒤 기업이 공급자다.
# 주어 조사(는/가)가 없어 subj-target 패턴으로는 절대 안 잡힌다 — SUPPLY 재현율의 핵심.
SUPPLIER_RELCLAUSE = re.compile(r"(?:공급|납품|제공|생산|수주)(?:하는|하던|해온|해\s*온|한)\s*$")
# 나열 연결사 — SUPPLY 에서는 두 기업이 같은 편이라는 신호
ENUM_CONN = re.compile(r"^\s*(?:와|과|,|·|및|이나|나|또는|그리고)\s*$")
# 조사는 별칭 **바로 뒤**에 붙어야 하고 뒤에 한글이 이어지면 안 된다.
# "테슬라에 이어"의 '이'를 주격조사로 읽어 엉뚱한 SUPPLY 를 만든 적이 있다.
SUBJECT = re.compile(r"^(?:은|는|이|가)(?![가-힣])")   # A는 …  (주어)
TARGET = re.compile(r"^(?:에게|에|으로|로)(?![가-힣])")  # … B에   (대상)

MAX_PAIR_GAP = 60      # 두 기업 사이가 이보다 멀면 같은 절이 아닐 가능성이 크다
KW_WINDOW = 45         # 키워드는 쌍 스팬 앞뒤 이 범위 안에 있어야 한다
# 한국어 기사는 "…있다.이번 계약은"처럼 마침표 뒤 공백이 없는 경우가 흔하다(후보의 20.5%).
# 추출기 상위(Spark)의 문장 분리가 이걸 못 잘라서 두 문장이 한 덩어리로 들어온다. 두 기업이
# 이 경계를 사이에 두고 있으면 서로 다른 문장에 있는 것이므로 관계로 보지 않는다.
SENT_BREAK_INSIDE = re.compile(r"(?<=다)\.(?=[가-힣])")

# 쌍 앞에 **기관이 주어로** 서 있으면, 뒤따르는 두 회사는 그 기관의 행위 대상으로 나란히
# 놓인 것이지 서로의 관계가 아닐 때가 많다.
#   "제휴평가위는 네이버와 카카오의 제휴 심사를 …"     -> 제휴는 위원회의 업무
#   "국토교통부는 … 현대자동차와 카카오 등 14개 기업과" -> 둘은 협약 참여사 나열
# 처음엔 주어를 가리지 않고 잡았더니 동사 관형형("경쟁하는 네이버와 카카오")까지 주어로
# 읽어 정답을 더 많이 날렸다(제거 127건 중 절반 이상이 정답). 그래서 **기관처럼 생긴 것**으로
# 좁혔다: 기관 접미사 또는 전부 대문자 약어.
THIRD_PARTY_SUBJECT = re.compile(
    r"(?:^|[\s,·(\"“])("
    r"[가-힣A-Za-z0-9]{1,10}(?:위원회|평가위|금감원|공정위|연구원|진흥원|공사|협회|학회|재단|"
    r"대학교|당국|정부|부처|청|부|처)"
    r"|[A-Z]{2,6}"
    r")(?:는|은)(?=\s)")
# 다만 그 기관이 **전달자**일 뿐인 경우가 있다 — "WSJ은 'SK하이닉스는 엔비디아의 파트너'".
# 기관과 쌍 사이에 따옴표나 보도 동사가 있으면 인용이므로 관계를 살린다.
QUOTING_CONTEXT = re.compile(r"[\"“‘']|보도|전했|밝혔|따르면|제목의|기사")


def find_pair(sent: str, a: str, b: str):
    i = sent.find(a)
    if i < 0:
        return None
    j = sent.find(b, i + len(a))
    if j < 0:
        return None
    return i, j


def classify(sent: str, a: str, b: str) -> tuple[str, str, str, float, str] | None:
    """-> (src_alias, dst_alias, rel_type, confidence, pattern) 또는 None."""
    pos = find_pair(sent, a, b)
    if not pos:
        return None
    i, j = pos
    mid = sent[i + len(a):j]
    if len(mid) > MAX_PAIR_GAP:
        return None                                     # 근거 3: 너무 멀면 다른 절이다
    if SENT_BREAK_INSIDE.search(mid):
        return None                                     # 둘 사이에 문장 경계가 있다
    orgs = [m for m in THIRD_PARTY_SUBJECT.finditer(sent[:i]) if m.group(1) not in (a, b)]
    if orgs and not QUOTING_CONTEXT.search(sent[orgs[-1].end():i]):
        return None                                     # 기관이 주어 — 둘은 그 행위의 대상
    lo = max(0, i - KW_WINDOW)
    hi = min(len(sent), j + len(b) + KW_WINDOW)
    window = sent[lo:hi]
    if HEDGE.search(window):
        return None
    before_a = sent[max(0, i - 12):i]
    after_b = sent[j + len(b):j + len(b) + 12]
    enum = bool(ENUM_CONN.match(mid))

    # --- PARTNER / COMPETE : 무방향. "A와 B의 협력" 이 정석 패턴이다.
    # 나열 꼬리표는 패턴 종류와 무관하게 먼저 친다. "…마이크로소프트, 시스코 등 파트너 사와"는
    # 나열 연결사가 아니어서(near+kw) enum 분기에 안 걸렸는데도 같은 오탐이었다.
    tail = sent[j + len(b):j + len(b) + ENUM_LOOKAHEAD]
    listed_under_third_party = bool(ENUM_THIRDPARTY.search(" " + tail))

    for rel in ("PARTNER", "COMPETE"):
        if not KW[rel].search(window):
            continue
        if listed_under_third_party:
            return None                                # A·B 등 경쟁사/파트너사 -> 제3자 기준 목록
        if enum:
            return a, b, rel, 0.8, "enum+kw"           # A와 B + 협력/경쟁 — 가장 깨끗
        if TARGET.match(after_b) or SUBJECT.search(mid):
            return a, b, rel, 0.6, "clause+kw"
        return a, b, rel, 0.5, "near+kw"

    # --- SUPPLY : 방향. 나열이면 둘 다 고객일 가능성이 높아 버린다(근거 1).
    if KW["SUPPLY"].search(window):
        if enum:
            return None
        # "A에 …를 공급하는 B" : 대상 조사로 시작하고 공급 동사의 관계절로 끝나면 B가 공급자
        if TARGET.match(mid) and SUPPLIER_RELCLAUSE.search(mid):
            return b, a, "SUPPLY", 0.75, "relclause"
        a_is_subject = bool(SUBJECT.search(sent[i + len(a):i + len(a) + 3]))
        b_is_target = bool(TARGET.match(sent[j + len(b):j + len(b) + 4]))
        if a_is_subject and b_is_target:
            return a, b, "SUPPLY", 0.7, "subj-target"   # A는 … B에 납품 -> A가 B에 공급
        b_is_subject = bool(SUBJECT.search(sent[j + len(b):j + len(b) + 3]))
        a_is_target = bool(TARGET.match(sent[i + len(a):i + len(a) + 4]))
        if b_is_subject and a_is_target:
            return b, a, "SUPPLY", 0.7, "subj-target"   # A에 … B가 납품 -> B가 A에 공급
        return None                                     # 방향 못 정하면 버린다(근거 3)
    return None


def load_affiliates(path: Path) -> set[frozenset]:
    """지분 엣지에서 계열 관계 쌍을 만든다.

    현대차-현대모비스처럼 지분으로 묶인 회사끼리는 경쟁사가 아니다. 그런데 기사에는
    "엘리엇이 현대차와 현대모비스 지분을 … 경쟁사 임원을 선임하려"처럼 제3자를 가리키는
    '경쟁사'가 같은 문장에 흔히 들어온다. 우리가 이미 가진 DART 지분 엣지로 이걸 막는다.
    """
    import csv
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {frozenset((r["src_ticker"], r["dst_ticker"])) for r in csv.DictReader(f)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default=HERE / "data" / "relation_sample.jsonl", type=Path)
    ap.add_argument("--ownership", default=HERE / "data" / "edges_ownership.csv", type=Path)
    ap.add_argument("--dump", type=Path, help="추출된 관계를 JSONL로 저장 (검수용)")
    ap.add_argument("--show", type=int, default=4, help="유형별 예시 개수")
    # 검수(무작위 25건)에서 near+kw 가 오탐의 절반을 차지했다 — 키워드가 두 기업 근처에 있을 뿐
    # 그 관계의 당사자가 누구인지는 말해주지 않는 패턴이다. 0.6 이면 near+kw(0.5)가 빠진다.
    ap.add_argument("--min-conf", type=float, default=0.6,
                    help="이 신뢰도 미만 패턴은 버린다 (0.5=near+kw 포함, 0.6=제외)")
    args = ap.parse_args()
    affiliates = load_affiliates(args.ownership)

    rows = [json.loads(l) for l in args.sample.open(encoding="utf-8")]
    hits, by_type, by_pattern = [], Counter(), Counter()
    for r in rows:
        if r["n_companies"] != 2:
            continue                                    # 3개 이상은 대개 나열문
        al, tk = r.get("aliases") or [], r.get("tickers") or []
        if len(al) < 2:
            continue
        got = classify(r["sentence"], al[0], al[1])
        if not got:
            continue
        src_alias, dst_alias, rel, conf, pat = got
        if conf < args.min_conf:
            continue
        src = tk[0] if src_alias == al[0] else tk[1]
        dst = tk[1] if src_alias == al[0] else tk[0]
        if rel == "COMPETE" and frozenset((src, dst)) in affiliates:
            continue                                    # 지분으로 묶인 계열사는 경쟁사가 아니다
        by_type[rel] += 1
        by_pattern[(rel, pat)] += 1
        hits.append({"record_id": r["record_id"], "published_date": r["published_date"],
                     "src_ticker": src, "dst_ticker": dst, "rel_type": rel,
                     "confidence": conf, "pattern": pat, "sentence": r["sentence"]})

    pair_sents = sum(1 for r in rows if r["n_companies"] == 2)
    print(f"2기업 문장 {pair_sents:,} -> 관계 {len(hits):,}  (추출률 {len(hits)/max(pair_sents,1):.1%})")
    for rel, n in by_type.most_common():
        pats = ", ".join(f"{p}={c}" for (rr, p), c in by_pattern.items() if rr == rel)
        print(f"  {rel:<8} {n:>5,}   [{pats}]")
    if args.dump:
        with args.dump.open("w", encoding="utf-8") as f:
            for h in hits:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")
        print(f"  -> {args.dump}")
    for rel in ("SUPPLY", "PARTNER", "COMPETE"):
        ex = [h for h in hits if h["rel_type"] == rel][:args.show]
        if ex:
            print(f"\n--- {rel}")
            for h in ex:
                s = h["sentence"].replace("\n", " ")[:120]
                print(f"  {h['src_ticker']}->{h['dst_ticker']} [{h['pattern']}] {s}")


if __name__ == "__main__":
    main()
