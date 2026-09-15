"""Story 3 — impact_direction 을 관계 유형이 아니라 쌍별 가격 반응에서 추정한다.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe estimate_edge_direction.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe estimate_edge_direction.py --factors market+industry

왜 고치는가
  build_relationship_seed.py 는 유형만 보고 부호를 준다
  (IMPACT = SUPPLY/INVEST/PARTNER POSITIVE, COMPETE NEGATIVE).
  COMPETE 의 NEGATIVE 가 가격과 정면으로 어긋난다 — test 구간에서 유형 기본값 적중률이
  **5%** 다. 국내 "경쟁사"는 같은 산업 충격을 같이 맞는 쪽이 압도적이라 같이 움직인다
  (SK하이닉스-삼성전자, NAVER-카카오, 신한지주-KB금융 ...).

════════════════════════════════════════════════════════════════════════════
기준선을 제대로 잡을 것 — 이 스크립트가 한 번 틀렸던 지점
════════════════════════════════════════════════════════════════════════════
처음에는 "유형 기본값 55% -> 쌍별 추정 66%" 로 보고했다. 두 가지가 잘못이었다.

1) **허수아비를 상대했다.** 유형 기본값(COMPETE 만 NEGATIVE)은 약한 기준선이다. 파라미터가
   0개인 "**전부 POSITIVE**" 가 같은 표본에서 더 높다. 이걸 안 놓고 비교하면 "경쟁사도 같이
   움직인다"를 발견한 공을 쌍별 추정이 가져가 버린다. 실제로 COMPETE 는 전부 POSITIVE 가
   test 95% 로 쌍별 추정(90%)보다 낫다 — 부호를 더 갈라봐야 손해다.
2) **쓰이는 공간이 아닌 데서 쟀다.** 부호를 market+industry 잔차로 추정했는데 영향도 모델
   (build_impact_scores.py / train_impact_gnn.py)은 market-only 잔차를 쓴다. 산업 LOO 팩터를
   빼면 같은 업종 안에서 잔차 합이 0 에 묶여 **기계적인 음의 상관**이 생기고(그룹 5종목이면
   -0.09~-0.16), 그걸 관계의 부호로 착각하게 된다. 공간을 맞추면 숫자가 이렇게 바뀐다.

     잔차 공간              유형기본  전부POS  (옛)출하본
     market+industry         54.8%   59.7%   66.8%   <- 옛 보고가 선 곳
     market only (모델이 씀)  57.4%   63.5%   63.5%   <- 이득이 사라진다
     raw (화면 수익률)         91.8%   99.1%   75.0%   <- 오히려 크게 나쁘다

   그래서 --factors 기본값을 **market** 으로 바꿨다. 소비하는 쪽과 같은 정의여야 한다.

어떻게 정하는가 (3단 분할)
  train 2017~2022  쌍별 동시 잔차 상관 r 을 추정
  val   2023       **완성된 규칙들끼리** 통째로 비교해 하나를 고른다
  test  2024~2026  손대지 않고 보고만
  유형별로 규칙을 섞는 안(유형마다 가격추정을 켤지 끌지)도 후보에 넣되, val 에서 더 나은
  쪽만 채택한다. 현재 market 잔차에서 채택된 것은 **유형별 혼합 (|r|>=0.05)** 이다.
  유형당 표본이 적으므로 이 결과를 다른 시장·기간까지 일반화하지 않는다.
  같은 시장 쌍은 같은 세션, 교차시장 쌍은 NASDAQ(t-1)->KOSPI(t) 로 맞춘다.

현재 실측 (market 잔차, test 유효 쌍 310개)
  규칙                         val      test
  유형 기본값                  63.8%    57.4%
  전부 POSITIVE (파라미터 0개)   68.8%    63.5%
  유형별 혼합 (|r|>=0.05)       73.5%    71.9%
  채택 규칙의 test 유형별 적중률: COMPETE 95%, PARTNER 68%, SUPPLY 86%, INVEST 77%.
  COMPETE 는 전부 POSITIVE 를 채택한다. INVEST 는 전부 POSITIVE 84% 보다 낮다.

한계
  - 이건 인과가 아니라 **동조 방향**이다. event_study_propagation.py 에서 다음 세션 전파가
    0으로 나왔으므로 "A 가 오르면 B 가 오른다"가 아니라 "같이 움직인다"로 읽어야 한다.
  - 부호만 쓰면 세기를 버린다. 현재 U2 의 industry_signed IC 는 0.2022 (4,073이벤트),
    industry 는 0.2034 (3,410이벤트) 다. 유효 이벤트 수가 달라 이 두 평균을 그대로 빼면
    안 된다. pair_corr 의 U3 는 0.2054 다. 이 부호표는 DB 의 impact_direction 칸을 채우고,
    순위는 build_impact_scores.py 의 계수 크기로 정하는 것이 현재 제품용 선택이다.
  - 교차시장 쌍(29%)은 NASDAQ(t-1)->KOSPI(t) 한 정렬로만 추정했다. KOSPI->NASDAQ 방향은
    같은 달력날짜가 맞는 정렬인데 거기서는 따로 재지 않았다.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import pandas as pd

from build_correlation_edges import lag_align
from impact_dataset import RELATIONS, SCORES, Panel, build_panel

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "db" / "relationship_impact_direction.csv"
TYPE_DEFAULT = {"SUPPLY": "POSITIVE", "INVEST": "POSITIVE",
                "PARTNER": "POSITIVE", "COMPETE": "NEGATIVE"}
FIELDS = ["source_market", "source_stock_code", "target_market", "target_stock_code",
          "relationship_type_code", "impact_direction", "source", "corr_train", "n_train"]
MIN_DAYS = 60


def aligned_frames(panel: Panel) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(같은시장용 원본 패널, 교차시장용 NASDAQ(t-1)->KOSPI(t) 정렬 패널)."""
    resid = panel.resid
    kospi = [t for t in panel.tickers if panel.market[t] == "KOSPI"]
    nasdaq = [t for t in panel.tickers if panel.market[t] == "NASDAQ"]
    kospi_days = resid.loc[resid[kospi].notna().any(axis=1)].index
    cross = pd.concat([resid.loc[kospi_days, kospi], lag_align(resid, nasdaq, kospi_days)],
                      axis=1)
    return resid, cross


def pair_corr(same: pd.DataFrame, cross: pd.DataFrame, a: str, b: str, cross_mkt: bool,
              mask: pd.Series) -> tuple[float, int]:
    f = cross if cross_mkt else same
    m = mask.reindex(f.index, fill_value=False)
    d = f.loc[m, [a, b]].dropna()
    if len(d) < MIN_DAYS:
        return float("nan"), len(d)
    return float(d[a].corr(d[b])), len(d)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relations", default=RELATIONS, type=Path)
    ap.add_argument("--scores", default=SCORES, type=Path)
    ap.add_argument("--out", default=OUT, type=Path)
    ap.add_argument("--grid", nargs="*", type=float, default=[0.0, 0.03, 0.05, 0.08, 0.12, 0.20],
                    help="쌍별 부호를 믿을 |r| 임계 후보. 미만이면 전부 POSITIVE 로 둔다")
    ap.add_argument("--beta-mode", choices=("prior", "inyear"), default="prior")
    ap.add_argument("--factors", choices=("market", "market+industry", "none"), default="market",
                    help="부호를 추정할 잔차 공간. **소비하는 모델과 같아야 한다** (docstring 2번)")
    args = ap.parse_args()

    panel = build_panel(beta_mode=args.beta_mode, factors=args.factors)
    same, cross = aligned_frames(panel)
    masks = {s: pd.Series(panel.split.to_numpy() == s, index=panel.resid.index)
             for s in ("train", "val", "test")}

    rel = pd.read_csv(args.relations, dtype=str)
    recs = []
    for v in rel.itertuples(index=False):
        a, b = v.source_stock_code, v.target_stock_code
        xm = v.source_market != v.target_market
        r = {s: pair_corr(same, cross, a, b, xm, masks[s]) for s in masks}
        recs.append({"type": v.relationship_type_code, "a": a, "b": b, "cross": xm,
                     "src_mkt": v.source_market, "dst_mkt": v.target_market,
                     "default": TYPE_DEFAULT[v.relationship_type_code],
                     "r_train": r["train"][0], "n_train": r["train"][1],
                     "r_val": r["val"][0], "r_test": r["test"][0], "n_test": r["test"][1]})
    rep = pd.DataFrame(recs)

    def by_corr(frame: pd.DataFrame, thr: float) -> pd.Series:
        """train 상관 부호. |r|<thr 이면 판단을 보류하고 전부 POSITIVE 로 둔다."""
        use = np.isfinite(frame["r_train"]) & (frame["r_train"].abs() >= thr)
        return pd.Series(np.where(use & (frame["r_train"] < 0), "NEGATIVE", "POSITIVE"),
                         index=frame.index)

    def hit(chosen: pd.Series, split: str, frame: pd.DataFrame | None = None) -> float:
        f = rep if frame is None else frame
        m = np.isfinite(f[f"r_{split}"]) & (f["n_test"] >= MIN_DAYS)
        if not m.any():
            return float("nan")
        return float(((chosen[m] == "POSITIVE") == (f.loc[m, f"r_{split}"] > 0)).mean())

    # ---- 완성된 규칙들을 통째로 val 에서 비교한다
    cands: dict[str, pd.Series] = {
        "유형 기본값": rep["default"],
        "전부 POSITIVE": pd.Series("POSITIVE", index=rep.index),
    }
    for thr in args.grid:
        cands[f"쌍별 상관 부호 |r|>={thr}"] = by_corr(rep, thr)
    # 유형별 혼합안: 유형마다 val 에서 더 나은 쪽(전부POS / 쌍별)을 고른 뒤 이어붙인다
    thr_best = max(args.grid, key=lambda t: hit(by_corr(rep, t), "val"))
    mixed = pd.Series("POSITIVE", index=rep.index)
    for t, g in rep.groupby("type"):
        pick_corr = hit(by_corr(g, thr_best), "val", g)
        pick_pos = hit(pd.Series("POSITIVE", index=g.index), "val", g)
        mixed[g.index] = (by_corr(g, thr_best) if (np.isfinite(pick_corr)
                                                   and pick_corr > pick_pos)
                          else "POSITIVE")
    cands[f"유형별 혼합 (|r|>={thr_best})"] = mixed

    print(f"관계 {len(rel)}개 | 잔차 공간 factors={args.factors} | test 유효 쌍 "
          f"{int((np.isfinite(rep.r_test) & (rep.n_test >= MIN_DAYS)).sum())}")
    print(f"\n{'규칙':<26}{'val 적중':>10}{'test 적중':>11}{'NEG 개수':>9}")
    for name, pred in cands.items():
        print(f"{name:<26}{hit(pred, 'val'):>10.1%}{hit(pred, 'test'):>11.1%}"
              f"{int((pred == 'NEGATIVE').sum()):>9}")

    pick = max(cands, key=lambda k: hit(cands[k], "val"))
    chosen = cands[pick]
    rep["chosen"] = chosen
    print(f"\n  val 이 고른 규칙: **{pick}**  ->  test {hit(chosen, 'test'):.1%}")

    ok = rep[np.isfinite(rep["r_test"]) & (rep["n_test"] >= MIN_DAYS)]
    print(f"\n{'type':<10}{'n':>5}{'유형기본':>9}{'전부POS':>9}{'채택':>7}{'NEG':>6}")
    for t, g in ok.groupby("type"):
        gp = g["r_test"] > 0
        print(f"{t:<10}{len(g):>5}{((g['default'] == 'POSITIVE') == gp).mean():>9.0%}"
              f"{gp.mean():>9.0%}{((chosen[g.index] == 'POSITIVE') == gp).mean():>7.0%}"
              f"{int((chosen[g.index] == 'NEGATIVE').sum()):>6}")
    gp = ok["r_test"] > 0
    print(f"{'전체':<10}{len(ok):>5}{((ok['default'] == 'POSITIVE') == gp).mean():>9.0%}"
          f"{gp.mean():>9.0%}{((chosen[ok.index] == 'POSITIVE') == gp).mean():>7.0%}"
          f"{int((chosen[ok.index] == 'NEGATIVE').sum()):>6}")

    neg = rep[rep["chosen"] == "NEGATIVE"]
    if len(neg):
        nm = pd.read_csv(HERE.parent / "ner" / "data" / "company_industry.csv",
                         dtype=str).set_index("ticker")["name_official"]
        print(f"\n  NEGATIVE 로 간 {len(neg)}쌍 (train r -> test r)")
        for v in neg.sort_values("r_train").head(12).itertuples(index=False):
            print(f"    {nm.get(v.a, v.a):<16} - {nm.get(v.b, v.b):<16} {v.type:<8}"
                  f" {v.r_train:+.3f} -> {v.r_test:+.3f}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for v, ch in zip(rel.itertuples(index=False), chosen):
            i = rep.index[(rep["a"] == v.source_stock_code)
                          & (rep["b"] == v.target_stock_code)
                          & (rep["type"] == v.relationship_type_code)][0]
            rt = rep.at[i, "r_train"]
            w.writerow({"source_market": v.source_market,
                        "source_stock_code": v.source_stock_code,
                        "target_market": v.target_market,
                        "target_stock_code": v.target_stock_code,
                        "relationship_type_code": v.relationship_type_code,
                        "impact_direction": ch,
                        "source": "price" if ch != TYPE_DEFAULT[v.relationship_type_code]
                                  or np.isfinite(rt) else "fallback",
                        "corr_train": round(float(rt), 4) if np.isfinite(rt) else "",
                        "n_train": int(rep.at[i, "n_train"])})
    print(f"\n  -> {args.out}")


if __name__ == "__main__":
    main()
