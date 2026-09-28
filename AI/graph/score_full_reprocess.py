"""전수 재처리 결과로 관계 점수를 만든다 → data/rel_scores_v2.json.

  PYTHONUTF8=1 python score_full_reprocess.py

입력 (모두 gitignore — HDFS·서버에서 재생성한다)
  data/rel_hits_all_v2.jsonl   관계 근거 10,354건 (국내 9,041 + 영문 1,313)
  data/edges_ownership.csv     DART 지분 44행 → INVEST
  data/edges_correlation.csv   잔차 상관 180행 → 주가 가산점 (최대 +3점)
  ../ner/data/db/company.csv   티커 → 시장

════════════════════════════════════════════════════════════════════════════
창을 5개 낸다 — 정한 것은 30D/1Y/10Y 인데 왜 7D/90D 도 넣는가
════════════════════════════════════════════════════════════════════════════
백엔드 MetricWindow 는 아직 7D/30D/90D 만 받는다(BackEnd .../company/type/MetricWindow.java).
새 스냅샷이 서빙되기 시작하면 백엔드는 as_of_at 이 가장 늦은 PUBLISHED 스냅샷
하나만 읽으므로, 7D/90D 행이 없으면 그 두 탭이 통째로 빈다.
그래서 호환용으로 같이 낸다. 1Y/10Y 는 백엔드·프론트에 창을 추가해야 보인다.

실측 (as_of 2026-09-22)
  7D     40행    INVEST 뿐 — 최근 7일 안에 들어온 뉴스 근거가 없다
  30D   106행
  90D   123행
  1Y    301행
  10Y 1,251행    근거의 91%가 1년보다 오래됐다
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import relationship_score_components as C

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
AS_OF = C.timestamp("2026-09-22T00:00:00Z")

# 정한 창(30D/1Y/10Y) + 프론트 호환용 7D/90D. 위 머리말 참조.
C.WINDOWS = (("7D", 7), ("30D", 30), ("90D", 90), ("1Y", 365), ("10Y", 3650))
C.MAX_WINDOW_DAYS = max(d for _, d in C.WINDOWS)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    markets = {r["stock_code"]: r["market"]
               for r in read_csv(HERE.parent / "ner/data/db/company.csv")}

    # 창 종료일이 as_of 이전인 상관만 넘긴다 — 미래 가격 누설 방지(price_bonus 주석 참조).
    corr = {tuple(sorted((r["src_ticker"], r["dst_ticker"]))): float(r["corr_resid"])
            for r in read_csv(DATA / "edges_correlation.csv")
            if C.timestamp(r["window_end"]) < AS_OF}

    news = (C.normalize(r, "NEWS") for r in C.json_rows(DATA / "rel_hits_all_v2.jsonl"))
    rows, unknown = C.aggregate(news, markets, AS_OF, None, corr)

    # INVEST 는 지분율로 매기고 창을 걸지 않는다 — 지분은 사건이 아니라 상태다.
    # (relationship_score_components.invest_score 주석에 근거를 적어 뒀다.)
    excluded: dict[str, int] = defaultdict(int)
    invest = []
    for r in read_csv(DATA / "edges_ownership.csv"):
        src, dst, pct = r["src_ticker"], r["dst_ticker"], float(r["weight"])
        if src not in markets or dst not in markets:
            excluded["기업 사전에 없음"] += 1
            continue
        score = C.invest_score(pct)
        if score <= 0:
            excluded["지분 5% 미만"] += 1
            continue
        for label, _ in C.WINDOWS:
            invest.append({
                "source_market": markets[src], "source_stock_code": src,
                "target_market": markets[dst], "target_stock_code": dst,
                "relationship_type_code": "INVEST", "window_type": label,
                "news_score": None, "disclosure_score": score, "score": score,
                "impact_direction": None, "confidence": 1.0, "evidence_count": 1,
                "formula_version": C.FORMULA_VERSION,
                "as_of_at": AS_OF.isoformat().replace("+00:00", "Z")})

    rows = [r for r in rows if r["relationship_type_code"] != "INVEST"] + invest
    out = DATA / "rel_scores_v2.json"
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    pairs = {(r["source_stock_code"], r["target_stock_code"]) for r in invest}
    print(f"{out}")
    print(f"INVEST {len(pairs)}개 · 제외 {dict(excluded)} · 기업 사전에 없는 관계 {len(unknown)}")
    print()
    print(f"{'창':<6}{'행':>8}{'관계':>8}{'PARTNER':>9}{'COMPETE':>9}{'SUPPLY':>8}{'INVEST':>8}")
    print("-" * 56)
    for label, _ in C.WINDOWS:
        window = [r for r in rows if r["window_type"] == label]
        by_type: dict[str, int] = defaultdict(int)
        for r in window:
            by_type[r["relationship_type_code"]] += 1
        print(f"{label:<6}{len(window):>8,}{len(window):>8,}{by_type['PARTNER']:>9,}"
              f"{by_type['COMPETE']:>9,}{by_type['SUPPLY']:>8,}{by_type['INVEST']:>8,}")
    print("-" * 56)
    print(f"총 {len(rows):,}행")


if __name__ == "__main__":
    main()
