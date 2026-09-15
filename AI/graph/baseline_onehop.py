"""Story 3 step 0b — GNN 이 넘어야 할 바닥선: 관계 가중치로 한 홉 전파하는 선형 베이스라인.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe baseline_onehop.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe baseline_onehop.py --permute 200

예측식 (학습 파라미터 0개)
  yhat_B[t] = Σ_{A∈N(B)} w_AB * s_AB * 잔차_A[A 가 t 시점에 알려진 세션]
  s_AB 는 impact_direction (POSITIVE=+1, NEGATIVE=-1), w_AB 는 엣지 weight.

두 지평을 따로 잰다. 둘은 제품에서 전혀 다른 기능이다.
  h=0 동시 (nowcast) : "A 에 뉴스가 났다 -> 지금 같이 움직이는 종목은 누구인가"
  h=1 다음 세션 (forecast): "A 에 뉴스가 났다 -> 내일 오를 종목은 누구인가"

시장별 세션 정렬은 event_study_propagation 과 같은 규약을 쓴다. 목표 세션 t 시점에 이미
공개된 소스 세션만 쓴다 — KRX 가 06:30 UTC 에 먼저 닫으므로
  목표 KOSPI  t <- NASDAQ 은 t 보다 앞선 마지막 세션
  목표 NASDAQ t <- KOSPI  는 t 이하인 마지막 세션 (같은 달력날짜가 이미 끝나 있다)

지표는 일별 횡단면 rank-IC(스피어만)와 그 시계열 t 값이다. 수준(bp)이 아니라 순위 상관을
쓰는 이유는 제품이 "영향받는 종목 순위"를 보여주기 때문이다.

════════════════════════════════════════════════════════════════════════════
실측 결과 (2016-01-04..2026-09-11, factors=market+industry, KOSPI 2,515세션 /
NASDAQ 2,601세션). event_study_propagation.py 의 결론과 정확히 같은 그림이다.
════════════════════════════════════════════════════════════════════════════
                        h=0 동시(nowcast)        h=1 다음 세션(forecast)
  엣지집합            KOSPI          NASDAQ      KOSPI         NASDAQ
  correlation      +0.279(64.0)  +0.279(70.7)  -0.005(-1.2)  +0.007(2.0)   ※
  ownership        +0.144(31.7)      -         +0.003(0.9)      -
  co_mention       +0.122(36.7)  +0.102(33.8)  +0.005(1.8)   +0.004(1.6)
  relation(all +)  +0.085(27.3)  +0.017(4.0)   +0.002(0.7)   +0.001(0.3)
  relation(score-w)+0.070(22.4)  -0.018(-4.1)  +0.004(1.5)   +0.002(0.5)
  relation(signed) +0.052(16.7)  -0.010(-2.3)  +0.002(1.0)   +0.002(0.4)
  sector           -0.102(-33.0) -0.172(-64.3) +0.014(5.7)   +0.002(1.1)
  ※ correlation 엣지는 2025-09~2026-09 가격으로 만들어 놓고 2016~2026 전체를 평가한 것이라
    미래정보가 들어가 있다. 이 숫자는 상한의 참고치일 뿐 베이스라인으로 쓰면 안 된다.

읽는 법
1) h=1 은 전 엣지집합에서 0이다. 최대가 sector +0.014 인데 nowcast 의 1/7 크기이고,
   부호가 양수인 이유도 전파가 아니라 산업 팩터를 뺀 잔차의 단기 반전으로 보인다.
   -> **다음날 수익률 예측을 목표로 GNN 을 학습시키면 넘을 베이스라인 자체가 0이다.**
      0을 이기는 모델은 만들 수 있지만 그 0.00x 는 거래비용 아래다.
2) h=0 은 크고 안정적이다. 학습 파라미터가 0개인 한 홉 선형 전파만으로 rank-IC 0.12
   (co_mention) ~ 0.14 (ownership) 가 나온다. **GNN 이 넘어야 할 실제 베이스라인은 이것이다.**
3) sector 가 h=0 에서 -0.10/-0.17 인 것은 버그가 아니다. 잔차에서 산업 공통분을 이미 뺐으니
   같은 산업 안에서는 합이 0에 가깝게 되고, 한 종목이 오르면 나머지는 기계적으로 내려간다.
   산업 팩터를 뺀 잔차를 타깃으로 쓰는 한 sector 엣지는 정보를 더하지 않는다.
4) relation 은 signed(0.052) < score-w(0.070) < all-positive(0.085) 순이다. 부호를 붙이면
   오히려 나빠진다. COMPETE 43쌍 중 32쌍이 실제로는 같이 움직이기 때문이다
   (event_study_propagation.py 의 COMPETE 절 참고). NASDAQ 에서는 signed 가 -0.010 으로
   아예 부호가 뒤집힌다.
5) relation 엣지가 붙은 타깃은 KOSPI 89 / NASDAQ 33 종목뿐이라 나머지는 예측이 0이다.
   co_mention 은 96/99 를 덮는다. 커버리지 차이도 위 숫자에 섞여 있다.

재배선 귀무분포 50회 (relation(signed) 의 dst 를 시장 안에서 섞음)
  h=0 KOSPI   실제 +0.0516 | 귀무 +0.0105±0.0123 | z=+3.3   ← 그래프가 진짜 정보를 준다
  h=0 NASDAQ  실제 -0.0098 | 귀무 +0.0003±0.0118 | z=-0.9   ← 부호를 붙이면 무작위만 못하다
  h=1 KOSPI   실제 +0.0023 | 귀무 +0.0002±0.0021 | z=+1.0   ← 무작위 그래프와 구분 안 됨
  h=1 NASDAQ  실제 +0.0016 | 귀무 +0.0010±0.0039 | z=+0.2
귀무분포 평균이 0이 아닌 것(+0.0105)에 주의. 시장 안에서 섞어도 남는 저 값은 "관계"가 아니라
"엣지가 많이 달린 종목이 원래 큰 종목"이라는 차수·규모 효과다. 진짜 기여분은 z 로 읽어야 한다.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from event_study_propagation import (INDUSTRY, PRICES, RELATIONS, SCORES, build_residuals,
                                     load_edges)

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "baseline_onehop.csv"
EDGE_FILES = {"sector": "edges_sector.csv", "co_mention": "edges_co_mention.csv",
              "correlation": "edges_correlation.csv", "ownership": "edges_ownership.csv"}


def source_index(tgt_days: np.ndarray, src_days: np.ndarray, tgt_mkt: str, src_mkt: str,
                 horizon: int) -> np.ndarray:
    """목표 세션마다 "그 시점에 이미 알려진" 소스 세션 인덱스. 없으면 -1.

    event_study_propagation.response_index 의 역방향이고 규약은 동일하다.
    """
    if tgt_mkt == src_mkt:
        idx = np.searchsorted(src_days, tgt_days, side="left")          # 자기 자신
    elif tgt_mkt == "KOSPI":                                            # src NASDAQ
        idx = np.searchsorted(src_days, tgt_days, side="left") - 1      # t 보다 앞선 마지막
    else:                                                               # src KOSPI
        idx = np.searchsorted(src_days, tgt_days, side="right") - 1     # t 이하인 마지막
    idx = idx - horizon
    return np.where(idx >= 0, idx, -1)


def load_undirected(path: Path, market: pd.Series, tickers: set) -> pd.DataFrame:
    """edges_*.csv (무방향, src<dst 로 한 번만 저장됨)를 양방향 행으로 편다."""
    e = pd.read_csv(path, dtype={"src_ticker": str, "dst_ticker": str})
    e = e[e.src_ticker.isin(tickers) & e.dst_ticker.isin(tickers)]
    f = e.rename(columns={"src_ticker": "src", "dst_ticker": "dst"})[["src", "dst", "weight"]]
    b = f.rename(columns={"src": "dst", "dst": "src"})
    out = pd.concat([f, b], ignore_index=True)
    out["sign"] = 1.0
    return out


def adjacency(rows: pd.DataFrame, tickers: list[str]) -> np.ndarray:
    """(n_tickers, n_tickers) 행렬 W[src, dst] = weight * sign."""
    pos = {t: i for i, t in enumerate(tickers)}
    W = np.zeros((len(tickers), len(tickers)))
    for v in rows.itertuples(index=False):
        if v.src in pos and v.dst in pos:
            W[pos[v.src], pos[v.dst]] += v.weight * v.sign
    return W


def daily_ic(pred: np.ndarray, actual: np.ndarray, min_n: int = 8):
    """세션별 횡단면 rank-IC 와 그 시계열 t. 예측이 상수인 날은 버린다."""
    ics = []
    for i in range(pred.shape[0]):
        p, a = pred[i], actual[i]
        m = np.isfinite(p) & np.isfinite(a) & (p != 0)
        if m.sum() < min_n or np.ptp(p[m]) == 0 or np.ptp(a[m]) == 0:
            continue
        ics.append(stats.spearmanr(p[m], a[m]).statistic)
    ics = np.array([v for v in ics if np.isfinite(v)])
    if len(ics) < 3:
        return float("nan"), float("nan"), len(ics)
    se = ics.std(ddof=1) / math.sqrt(len(ics))
    return float(ics.mean()), float(ics.mean() / se) if se > 0 else float("nan"), len(ics)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", default=PRICES, type=Path)
    ap.add_argument("--industry", default=INDUSTRY, type=Path)
    ap.add_argument("--relations", default=RELATIONS, type=Path)
    ap.add_argument("--scores", default=SCORES, type=Path)
    ap.add_argument("--out", default=OUT, type=Path)
    ap.add_argument("--winsor", type=float, default=0.005)
    ap.add_argument("--factors", choices=("market+industry", "market", "none"),
                    default="market+industry")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--permute", type=int, default=0, help="엣지 재배선 귀무분포 반복 횟수")
    args = ap.parse_args()

    resid, market, _ = build_residuals(args.prices, args.industry, args.winsor, args.factors)
    resid = resid.loc[resid.index >= pd.Timestamp(args.start)]
    tickers = list(resid.columns)
    pos = {t: i for i, t in enumerate(tickers)}
    days = resid.index.values
    R = resid.to_numpy(dtype=float)

    cols = {m: np.array([pos[t] for t in tickers if market[t] == m]) for m in ("KOSPI", "NASDAQ")}
    sess = {m: np.flatnonzero(np.isfinite(R[:, cols[m]]).any(axis=1)) for m in cols}
    sess_days = {m: days[sess[m]] for m in cols}

    # 엣지 집합들
    rel = load_edges(args.relations, args.scores)
    rel["weight"] = 1.0
    rel["sign"] = np.where(rel["impact"] == "NEGATIVE", -1.0, 1.0)
    sets = {
        "relation(signed)": rel[["src", "dst", "weight", "sign"]],
        "relation(all +)": rel.assign(sign=1.0)[["src", "dst", "weight", "sign"]],
        "relation(score-w)": rel.assign(weight=lambda d: d["score"] / 100.0)[
            ["src", "dst", "weight", "sign"]],
    }
    for name, fn in EDGE_FILES.items():
        p = HERE / "data" / fn
        if p.exists():
            sets[name] = load_undirected(p, market, set(tickers))

    def evaluate(rows: pd.DataFrame, horizon: int) -> dict:
        """목표 시장별로 예측/실측 패널을 만들고 rank-IC 를 잰다."""
        W = adjacency(rows, tickers)
        res = {}
        for tgt in ("KOSPI", "NASDAQ"):
            t_rows, t_cols = sess[tgt], cols[tgt]
            pred = np.zeros((len(t_rows), len(t_cols)))
            for src in ("KOSPI", "NASDAQ"):
                k = source_index(sess_days[tgt], sess_days[src], tgt, src, horizon)
                ok = k >= 0
                srow = np.where(ok, sess[src][np.clip(k, 0, len(sess[src]) - 1)], 0)
                X = np.nan_to_num(R[np.ix_(srow, cols[src])])     # 결측은 기여 0
                X[~ok] = 0.0
                pred += X @ W[np.ix_(cols[src], t_cols)]
            res[tgt] = daily_ic(pred, R[np.ix_(t_rows, t_cols)])
        return res

    records = []
    for name, rows in sets.items():
        deg = rows.groupby("dst").size()
        for h in (0, 1):
            r = evaluate(rows, h)
            for mkt, (ic, t, n) in r.items():
                records.append({"edges": name, "horizon": h, "target_market": mkt,
                                "rank_ic": round(ic, 4) if ic == ic else float("nan"),
                                "t_stat": round(t, 2) if t == t else float("nan"),
                                "n_sessions": n, "n_edges": len(rows),
                                "n_targets_with_edge": int(deg.index.isin(
                                    [tickers[c] for c in cols[mkt]]).sum())})
    sm = pd.DataFrame(records)

    null = {}
    if args.permute:
        rng = np.random.default_rng(20260914)
        base = sets["relation(signed)"]
        draws = {("KOSPI", 0): [], ("NASDAQ", 0): [], ("KOSPI", 1): [], ("NASDAQ", 1): []}
        for _ in range(args.permute):
            fake = base.copy()
            fake["_m"] = fake["dst"].map(market)
            fake["dst"] = fake.groupby("_m")["dst"].transform(
                lambda s: rng.permutation(s.to_numpy()))
            for h in (0, 1):
                for mkt, (ic, _, _) in evaluate(fake.drop(columns="_m"), h).items():
                    draws[(mkt, h)].append(ic)
        for k, v in draws.items():
            a = np.array([x for x in v if np.isfinite(x)])
            null[k] = (a.mean(), a.std(ddof=1))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sm.to_csv(args.out, index=False, encoding="utf-8")

    print(f"factors={args.factors} 기간 {resid.index.min().date()}..{resid.index.max().date()}")
    print("  yhat_B[t] = Σ w*s*잔차_A[알려진 세션]. h=0 동시(nowcast), h=1 다음 세션(forecast).")
    print("  rank_ic = 일별 횡단면 스피어만 상관의 평균, t 는 그 시계열의 t 값.\n")
    print(f"{'edges':<20}{'h':>3}{'market':>9}{'edges#':>8}{'targets':>9}"
          f"{'rank_ic':>10}{'t':>8}{'sessions':>10}")
    for v in sm.itertuples(index=False):
        print(f"{v.edges:<20}{v.horizon:>3}{v.target_market:>9}{v.n_edges:>8}"
              f"{v.n_targets_with_edge:>9}{v.rank_ic:>10.4f}{v.t_stat:>8.2f}{v.n_sessions:>10}")
    if null:
        print(f"\n  relation(signed) 재배선 귀무분포 {args.permute}회")
        for (mkt, h), (mu, sd) in sorted(null.items()):
            real = sm[(sm.edges == "relation(signed)") & (sm.horizon == h)
                      & (sm.target_market == mkt)]["rank_ic"].iloc[0]
            z = (real - mu) / sd if sd > 0 else float("nan")
            print(f"    h={h} {mkt:<8} 실제 {real:+.4f} | 귀무 {mu:+.4f}±{sd:.4f} | z={z:+.1f}")
    print(f"\n  -> {args.out}")


if __name__ == "__main__":
    main()
