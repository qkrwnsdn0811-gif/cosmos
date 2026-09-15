"""Knowledge graph step 6-2 — what predicates actually connect two companies in one sentence.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe analyze_relation_predicates.py \
      --sample data/relation_sample.jsonl --top 40

extract_relation_candidates.py 가 뽑은 후보 문장 표본을 읽어서, 두 기업 사이(그리고 주변)에
실제로 어떤 말이 오는지 빈도로 센다. 술어 사전을 머리로 지어내면 실제 기사에 없는 표현을
넣고 정작 흔한 표현을 빠뜨린다. v1.2 NER 에서 별칭을 추측으로 넣었다가 오탐을 만든 것과
같은 실수를 반복하지 않으려고, 사전을 만들기 전에 분포부터 본다.

출력
----
1) 두 기업 사이 구간(between)에 나오는 어절 빈도 — 여기가 술어가 가장 자주 오는 자리다
2) 나열문 비율 — "A, B, C 등"은 관계가 아니라 목록이다. method=ellipsis 와 쉼표 패턴으로 센다
3) 문장당 기업 수 분포 — 3개 이상이면 대개 나열문이다
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 조사·접속사·수치 같은 기능어는 술어 후보가 아니다
STOP = set("""
은 는 이 가 을 를 의 와 과 도 에 로 만 등 및 대 그 이런 저런 것 수 더 못 안 잘 또 또는 하지만
에서 에게 으로 부터 까지 처럼 보다 통해 위해 따라 대해 관련 지난 오는 올해 내년 지난해 현재 최근
있다 없다 했다 한다 된다 됐다 이다 라고 라며 밝혔다 말했다 전했다 설명했다 나타났다 보인다
%  원 억원 조원 달러 개 명 년 월 일 분기 상반기 하반기
""".split())

TOKEN = re.compile(r"[가-힣A-Za-z]{2,}")
ENUM_HINT = re.compile(r"[,·]\s*$|[,·]\s*\S+\s*[,·]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default=HERE / "data" / "relation_sample.jsonl", type=Path)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--pairs-only", action="store_true", help="기업 2개인 문장만 (나열문 제외)")
    args = ap.parse_args()

    if not args.sample.exists():
        raise SystemExit(f"{args.sample} 없음 — 서버 extract_relation_candidates.py 산출물을 받아오세요.")
    rows = [json.loads(l) for l in args.sample.open(encoding="utf-8")]
    print(f"후보 문장 {len(rows):,}")

    by_n = Counter(r["n_companies"] for r in rows)
    print("문장당 기업 수:", ", ".join(f"{k}개={v:,}" for k, v in sorted(by_n.items())[:8]))
    ell = sum(1 for r in rows if "ellipsis" in (r.get("methods") or []))
    print(f"ellipsis 멘션 포함 문장: {ell:,} ({ell/len(rows):.1%}) — 나열문 신호")

    pairs = [r for r in rows if r["n_companies"] == 2] if args.pairs_only else rows
    between, enum_like = Counter(), 0
    for r in pairs:
        sent, aliases = r["sentence"], r.get("aliases") or []
        if len(aliases) < 2:
            continue
        i = sent.find(aliases[0])
        j = sent.find(aliases[1], i + len(aliases[0]) if i >= 0 else 0)
        if i < 0 or j < 0:
            continue
        mid = sent[i + len(aliases[0]):j]
        if ENUM_HINT.search(mid) or mid.strip() in (",", "·", "와", "과", ",", "、"):
            enum_like += 1
        for t in TOKEN.findall(mid):
            if t not in STOP:
                between[t] += 1

    print(f"\n두 기업 사이가 나열 패턴인 문장: {enum_like:,} / {len(pairs):,} ({enum_like/max(len(pairs),1):.1%})")
    print(f"\n두 기업 사이 어절 빈도 상위 {args.top}:")
    for w, c in between.most_common(args.top):
        print(f"  {w:<14} {c:>6,}")


if __name__ == "__main__":
    main()
