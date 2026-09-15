"""Knowledge graph step 6-5 — score relation edges against the co-mention baseline, drop the flukes.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe score_relation_edges.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe score_relation_edges.py --min-lift 3 --min-docs 5

build_relation_edges.py 는 "같은 관계가 몇 개 기사에서 나왔나"(n_docs)로만 걸렀는데, 그러면
자주 등장하는 기업이 우연히 3~4건 걸려 살아남는다. 실제로 기아가 삼성전자·KT·네이버·카카오·
구글과 전부 PARTNER 로 잡혔다. 공동언급 엣지가 npmi 로 푼 빈도 편향과 같은 문제다.

분모가 필요하다: **두 기업이 같이 언급된 기사 수**(edges_co_mention.csv 의 n_docs).
    관계율 = 관계로 읽힌 기사 / 같이 언급된 기사
관계율만으로도 노이즈는 잘 드러나지만(삼성전자-SK하이닉스 SUPPLY 는 3/33,585 = 0.0%),
비율 임계값을 그대로 쓰면 1위 엣지인 현대차-기아(446/28,100 = 1.6%)가 잘린다. 그래서 비율이
아니라 **기대치 대비 배수(lift)** 로 본다.

    p0   = 전체 관계 기사 / 전체 공동언급 기사      (배경 관계율)
    기대 = 그 쌍의 공동언급 기사 수 x p0
    lift = 실제 관계 기사 수 / 기대

lift 는 "이 쌍이 배경보다 몇 배나 자주 관계로 읽혔나"이고, 많이 언급되는 쌍일수록 기대치가
커져 자동으로 보정된다. 실측:
    현대차-기아 PARTNER          446 / 기대 84   = 5.3배   -> 남김
    LG전자-메리어트 PARTNER        30 / 기대 0.2  = 178배  -> 남김
    삼성전자-SK하이닉스 SUPPLY       3 / 기대 100  = 0.03배 -> 버림
    기아-삼성전자 PARTNER            4 / 기대 30   = 0.13배 -> 버림

lift 만으로 자르면 안 되는 이유 (COMPETE 과교정)
-----------------------------------------------
경쟁사는 **경쟁하기 때문에** 늘 같이 언급된다. 분모가 관계와 독립이 아니라서 lift 가
구조적으로 낮게 나온다. lift>=2 만 적용했더니 COMPETE 가 120 -> 18 로 무너졌는데,
잘린 것들이 삼성전자-SK하이닉스(247건), 애플-구글(155건), 신한-KB(108건) 같은
교과서적 경쟁 관계였다. 기사 수백 건이 같은 말을 하면 그건 우연이 아니다.
그래서 **절대 근거와 상대 근거 중 하나만 넘으면 남긴다**:
    n_docs >= --min-docs-strong (많은 기사가 같은 관계를 말함)   또는
    lift   >= --min-lift        (배경보다 유난히 자주 관계로 읽힘)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIFT_FULL = 50.0        # 이 배수면 weight 1.0 (상대 근거 기준점)
DOCS_FULL = 200.0       # 이 기사 수면 weight 1.0 (절대 근거 기준점)
RECENCY_HALF_LIFE = 2.0  # 마지막 근거가 이만큼(년) 지나면 가중치 절반


def load_comention(path: Path) -> dict[tuple, int]:
    if not path.exists():
        raise SystemExit(f"{path} 없음 — 공동언급 엣지가 있어야 기준선을 만들 수 있습니다.")
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {tuple(sorted((r["src_ticker"], r["dst_ticker"]))): int(r["n_docs"])
                for r in csv.DictReader(f)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relations", default=HERE / "data" / "edges_relation.csv", type=Path)
    ap.add_argument("--co-mention", default=HERE / "data" / "edges_co_mention.csv", type=Path)
    ap.add_argument("--out", default=HERE / "data" / "edges_relation_scored.csv", type=Path)
    ap.add_argument("--min-lift", type=float, default=2.0)
    ap.add_argument("--min-docs", type=int, default=3)
    ap.add_argument("--min-docs-strong", type=int, default=20,
                    help="이만큼 많은 기사가 같은 관계를 말하면 lift 미달이어도 남긴다")
    ap.add_argument("--lift-floor", type=float, default=1.0,
                    help="절대 근거로 통과해도 이 lift 미만이면 버린다(배경보다 드물면 반증)")
    ap.add_argument("--half-life", type=float, default=RECENCY_HALF_LIFE,
                    help="마지막 근거가 이만큼(년) 지나면 가중치 절반")
    ap.add_argument("--max-age-years", type=float, default=5.0,
                    help="마지막 근거가 이보다 오래되면 끝난 관계로 보고 버린다")
    ap.add_argument("--names", default=HERE.parent / "ner" / "data" / "companies.csv", type=Path)
    args = ap.parse_args()

    co = load_comention(args.co_mention)
    with args.relations.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    with args.names.open(encoding="utf-8-sig", newline="") as f:
        names = {r["ticker"]: r["name_official"] for r in csv.DictReader(f)}

    # 배경 관계율: 전체 관계 기사 / 그 쌍들이 같이 언급된 전체 기사
    tot_rel = sum(int(r["n_docs"]) for r in rows)
    tot_co = sum(co.get(tuple(sorted((r["src_ticker"], r["dst_ticker"]))), 0) for r in rows)
    p0 = tot_rel / tot_co if tot_co else 0.0

    # 최근성 기준일은 '오늘'이 아니라 데이터가 끝나는 날. 배치 시점과 무관하게 재현된다.
    ref_date = max(dt.date.fromisoformat(r["last_date"]) for r in rows)

    out, dropped = [], []
    for r in rows:
        pair = tuple(sorted((r["src_ticker"], r["dst_ticker"])))
        n_rel, n_co = int(r["n_docs"]), co.get(pair, 0)
        if n_co <= 0:
            dropped.append((r, 0.0, "공동언급 없음"))
            continue
        expected = n_co * p0
        lift = n_rel / expected if expected > 0 else 0.0
        if n_rel < args.min_docs:
            dropped.append((r, lift, "기사 수 미달"))
            continue
        if lift < args.min_lift and n_rel < args.min_docs_strong:
            dropped.append((r, lift, "lift·기사수 둘 다 미달"))
            continue
        # 절대 근거로 통과하더라도 lift 가 1 미만이면 배경보다 **덜** 자주 관계로 읽혔다는 뜻이라
        # 근거가 아니라 반증이다. 검수에서 남은 오탐(Amazon-PARTNER-구글 lift 0.2,
        # SK하이닉스-PARTNER-삼성전자 0.7)이 전부 이 구간이었다.
        if lift < args.lift_floor:
            dropped.append((r, lift, "lift 1 미만 (배경보다 드묾)"))
            continue
        # 상대 근거(lift)와 절대 근거(기사 수)의 기하평균. 처음엔 max 를 썼는데 그러면
        # 근거 3건짜리(lift 높음)가 305건짜리와 같은 점수가 돼 화면 정렬이 뒤집혔다
        # (LG씨엔에스-Adobe 3건 82.3점 > 삼성전자-LG전자 305건 81.2점). 둘 다 높아야 높게 준다.
        w_lift = min(1.0, math.log10(1 + lift) / math.log10(1 + LIFT_FULL))
        w_docs = min(1.0, math.log10(1 + n_rel) / math.log10(1 + DOCS_FULL))
        # 최근성. 집계는 2012년치까지 훑으므로 그대로 두면 끝난 관계가 현재 관계처럼 뜬다
        # (마지막 근거가 2023년 이전인 엣지가 32%였다: LG전자-Kraft Heinz 2021-01,
        #  SK텔레콤-GE HealthCare 2021-09 등). 마지막 근거로부터의 경과로 반감시킨다.
        age = (ref_date - dt.date.fromisoformat(r["last_date"])).days / 365.25
        recency = 0.5 ** (max(0.0, age) / args.half_life)
        if age > args.max_age_years:
            dropped.append((r, lift, f"마지막 근거 {age:.1f}년 전"))
            continue
        weight = round(math.sqrt(w_lift * w_docs) * recency, 4)
        out.append({**r, "weight": weight, "co_docs": n_co, "rate": round(n_rel / n_co, 5),
                    "lift": round(lift, 2),
                    "evidence": r["evidence"] + f";lift={lift:.1f};co_docs={n_co}"})
    out.sort(key=lambda x: -x["lift"])

    fields = list(out[0].keys()) if out else list(rows[0].keys())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)

    from collections import Counter
    by = Counter(x["rel_type"] for x in out)
    print(f"배경 관계율 p0 = {p0:.4f}  (관계 {tot_rel:,}건 / 공동언급 {tot_co:,}건)")
    print(f"엣지 {len(rows)} -> {len(out)}  (버림 {len(dropped)})   min_lift={args.min_lift} min_docs={args.min_docs}")
    print("  유형별:", dict(by))
    print(f"  -> {args.out}")
    f_ = lambda t: names.get(t, t)
    print("\n=== lift 상위 12 ===")
    for x in out[:12]:
        print(f"  {f_(x['src_ticker']):<13}-{x['rel_type']:<8}->{f_(x['dst_ticker']):<13} "
              f"n={x['n_docs']:>4} co={x['co_docs']:>6} lift={x['lift']:>7.1f} w={x['weight']:.2f}")
    print("\n=== 버려진 것 상위 8 (기사 수는 많지만 우연) ===")
    for r, lift, why in sorted(dropped, key=lambda d: -int(d[0]["n_docs"]))[:8]:
        print(f"  {f_(r['src_ticker']):<13}-{r['rel_type']:<8}->{f_(r['dst_ticker']):<13} "
              f"n={r['n_docs']:>4} lift={lift:>5.2f}  {why}")


if __name__ == "__main__":
    main()
