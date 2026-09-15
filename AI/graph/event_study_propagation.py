"""Story 3 step 0 — GNN 을 짜기 전에 "전파가 실재하는가"를 먼저 판정한다.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe event_study_propagation.py --profile -3 3
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe event_study_propagation.py --permute 200
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe event_study_propagation.py --metric absz --profile -3 3
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe event_study_propagation.py --factors none   # 반례 확인용

--profile 없이 lag+1 하나만 보면 결론을 반대로 낸다. 아래 "실측 결과"를 먼저 읽을 것.

질문
  A 의 비정상 수익률이 관계 엣지를 타고 다음 거래일 B 로 전파되는가?
  전파된다면 COMPETE(impact_direction=NEGATIVE)는 반대 부호로 전파되는가?

설계
  1) 비정상 수익률 = build_correlation_edges.residualize() 의 잔차. 시장 팩터 + 산업 팩터를
     2단으로 뺀다. 원시 수익률을 쓰면 KOSPI 임의 쌍 평균 상관 0.362 를 전파로 착각한다.
     베타는 달력연도마다 다시 추정한다 — 10년 하나로 묶으면 2016년 베타로 2026년을 깎는다.
  2) 이벤트 = 종목 A 의 |잔차| 가 그 종목 상위 --pct % 인 날 D.
  3) 반응 = 그 다음 "거래 가능한" 세션에서의 B 의 잔차. 시장별로 다르다(아래).
  4) 대조군 = 같은 이벤트 날짜 D, 같은 대상 시장 안에서 A 와 관계 엣지가 없는 전 종목의 평균.
     쌍을 무작위로 뽑는 대신 전수 평균을 쓴다 — 표집 잡음이 없고 "무작위 쌍의 기대값"과 같다.
     날짜를 맞췄으므로 시장 전체가 움직인 날(2020-03 등)이 결과를 만들지 못한다.
  5) 통계량 = excess = sign(잔차_A[D]) * 잔차_B[D'] - (같은 이벤트의 대조군 평균).
     POSITIVE 관계면 excess > 0, COMPETE 면 excess < 0 이어야 한다.
     표준오차는 이벤트 날짜로 클러스터링한다. 같은 날 이벤트 수백 개는 독립 관측이 아니다.

거래일 매핑 (이 부분이 틀리면 라벨 전체가 무의미해지는데 겉으로는 티가 안 난다)
  KRX 00:00~06:30 UTC, NASDAQ 13:30~20:00 UTC. 같은 달력날짜에 KRX 가 먼저 닫는다.
    KOSPI 이벤트(D 06:30 UTC) -> KOSPI 반응 : D 보다 큰 첫 KOSPI 거래일
    KOSPI 이벤트(D 06:30 UTC) -> NASDAQ 반응: D 이상인 첫 NASDAQ 거래일  ← 같은 날짜가 정답
    NASDAQ 이벤트(D 20:00 UTC) -> KOSPI 반응 : D 보다 큰 첫 KOSPI 거래일
    NASDAQ 이벤트(D 20:00 UTC) -> NASDAQ 반응: D 보다 큰 첫 NASDAQ 거래일
  교차시장 정렬은 build_correlation_edges.lag_align() 의 nasdaq(t-1)->kospi(t) 규약과 같다.

수익률은 adj_close 로만 만든다. NASDAQ close_price 는 액면분할이 반영돼 있지 않아
(NASDAQ 행의 64.6%에서 불일치) 분할일이 -50% 폭락으로 잡힌다.

════════════════════════════════════════════════════════════════════════════
실측 결과 (2016-01-04..2026-09-11, 이벤트 4,788건/199종목, 방향엣지 634,
쌍x이벤트 14,395건, 이벤트 날짜 1,407일, factors=market+industry, 상위 1%)
════════════════════════════════════════════════════════════════════════════
결론: **동시(같은 세션) 전파는 크고 확실하다. 다음 세션 전파는 0이다.**

이벤트 창 프로파일 (excess_bp, 괄호 안 t)
  group      lag-3      lag-2      lag-1       lag0      lag+1      lag+2      lag+3
  ALL       0.9(0.3)   2.7(0.8)   9.8(2.9)  28.1(8.2)   8.0(2.6)   2.7(0.9)   4.6(1.5)
  COMPETE  14.2(1.6)   4.4(0.5)  18.7(1.9)  91.0(9.0)  24.6(2.8)   7.0(0.8) -10.9(-1.1)
  INVEST   15.3(2.1)  18.0(2.3)  21.8(2.6) 101.9(11.4)  5.8(0.7)  -1.9(-0.2) -1.1(-0.2)
  PARTNER  -2.8(-0.8)  3.0(0.9)   3.1(0.9)   7.6(2.3)   3.9(1.3)   3.3(1.0)   5.1(1.6)
  SUPPLY    6.4(0.5)  -2.0(-0.2)  6.7(0.6)  73.1(5.8)  15.5(1.3)  13.6(1.0)   8.5(0.7)

lag+1 만 보면 ALL +8.0bp (t=2.6) 이라 "전파가 있다"고 읽게 된다. 그게 이 설계의 함정이다.
같은 크기가 **이벤트 이전**인 lag-1 에도 +9.8bp (t=2.9) 로 있다. 이벤트가 원인이라면 이전
세션에 나타날 수 없다. 즉 lag+1 의 +8bp 는 전파가 아니라 잔차에 남은 쌍별 상시 동조가
이벤트 창 양쪽에 똑같이 새어든 것이다. lag+1 단독 검정은 이 둘을 구분하지 못한다.

짝지은 차분 (같은 쌍·같은 이벤트에서 사후 lag+1 - 사전 lag-1). 상시 동조는 양쪽에 같이
실리므로 지워지고, 이벤트에 귀속되는 순전파만 남는다.
  ALL       -1.50bp (t=-0.33)     COMPETE  +5.91bp (t=0.42)
  INVEST   -15.37bp (t=-1.34)     PARTNER  +0.86bp (t=0.18)
  SUPPLY    +8.27bp (t=0.48)
  악재만    +0.93bp (t=0.12)      호재만   -3.51bp (t=-0.66)
전부 0이다. "악재가 먼저 퍼진다"는 통설도 여기서는 성립하지 않는다.

민감도 — 설정을 바꿔도 순전파는 계속 0이다.
  factors        lag+1        순전파(사후-사전)
  market+industry  +8.0(2.6)   -1.50(-0.33)   ← 기본
  market           +11.6(3.6)  +3.24(0.67)
  none             +12.0(3.4)  +7.93(1.46)    ← 팩터를 안 빼면 전파처럼 보인다
  상위 2%          +7.5(3.5)   +1.50(0.47)
  상위 5%          +4.9(3.5)   +2.92(1.46)
팩터를 빼지 않을수록 lag+1 이 커지는데 순전파는 안 커진다. 커진 부분은 전부 "다 같이
움직이는 것"이다 (KOSPI 임의 쌍 원시 상관 0.362). 이게 원시 수익률을 쓰면 안 되는 이유다.

재배선 위약검정 200회 (dst 를 시장 안에서 섞고 lag+1 로 잰 값)
  ALL     실제 +7.99 | 귀무 +1.36±2.86 | p=0.010
  COMPETE 실제 +24.58 | 귀무 +1.93±7.79 | p=0.000
  INVEST  실제 +5.79 | 귀무 +1.38±5.74 | p=0.285
  PARTNER 실제 +3.93 | 귀무 +1.37±3.09 | p=0.230
  SUPPLY  실제 +15.52 | 귀무 -0.72±9.94 | p=0.110
읽는 법에 주의. 이건 "엣지로 이어진 쌍이 무작위 쌍과 다르다"만 말한다 (그래프는 진짜다).
"다음 세션에 전파된다"는 뜻이 아니다 — 같은 검정을 lag-1 에 돌려도 통과한다.

변동성 전파도 없다 (--metric absz, |잔차|/σ 를 %σ 단위로)
  프로파일 ALL: lag-3 308(3.0) lag-2 284(2.7) lag-1 482(4.1) lag0 923(8.4)
                lag+1 375(3.5) lag+2 340(3.4) lag+3 161(1.5)
  순전파(사후-사전) ALL -124(-0.87), INVEST -518(-1.43), PARTNER -144(-0.98),
                    COMPETE -79(-0.20), SUPPLY +108(0.25)
  "방향은 못 맞춰도 누가 흔들릴지는 맞춘다" 쪽으로 목표를 바꿔도 같은 벽이다. 창 전체가
  들려 있을 뿐(이어진 쌍은 원래 같이 변동성이 크다) 이벤트에 귀속되는 증가분은 없다.

COMPETE 의 impact_direction=NEGATIVE 는 가격이 지지하지 않는다
  43쌍 중 32쌍이 동시(lag0) 부호맞춤 반응이 **양수**다.
    삼성에스디에스-LG씨엔에스 +812bp, 삼성전자-SK하이닉스 +547, NAVER-카카오 +253,
    신한지주-KB금융 +236, HD한국조선해양-한화오션 +201
  음수인 쌍은 진짜 정면 대체재뿐이다.
    Intel-AMD -186bp, Apple-삼성전자 -63, 삼성전자-Intel -47, Broadcom-삼성전자 -25
  국내 "경쟁사"는 대체로 같은 산업 충격을 같이 맞는 쪽이 커서 같이 움직인다. COMPETE 에
  일괄로 NEGATIVE 를 주면 32/43 에서 부호가 반대로 붙는다. baseline_onehop.py 에서 이게
  실제로 성능을 깎는 것도 확인된다 (relation signed 0.052 < all-positive 0.085).
  산업 팩터를 이미 뺀 잔차라 같은 산업 안에서는 기계적으로 음의 편향이 걸려 있는데도
  양수가 나왔다 — 실제 동조는 이 숫자보다 더 강하다.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from build_correlation_edges import log_returns, residualize

HERE = Path(__file__).resolve().parent
PRICES = HERE / "data" / "prices_daily.parquet"
INDUSTRY = HERE.parent / "ner" / "data" / "company_industry.csv"
RELATIONS = HERE / "data" / "db" / "company_relationship.csv"
SCORES = HERE / "data" / "db" / "relationship_score_current.csv"
OUT = HERE / "data" / "event_study_summary.csv"

# relationship_type.csv 의 directionality. UNDIRECTED 는 양방향 모두 테스트한다.
UNDIRECTED = {"PARTNER", "COMPETE"}


def build_residuals(prices: Path, industry: Path, winsor: float, factors: str):
    """연도별로 베타를 다시 추정하면서 시장 -> 산업 순으로 공통 팩터를 제거한 잔차."""
    df = pd.read_parquet(prices, columns=["ticker", "market", "trading_at", "adj_close"])
    market = df.drop_duplicates("ticker").set_index("ticker")["market"]
    ind = pd.read_csv(industry, dtype=str).set_index("ticker")["industry_id"]

    px = df.pivot_table(index="trading_at", columns="ticker", values="adj_close")
    px.index = pd.to_datetime(px.index)
    r = log_returns(px.sort_index(), winsor)

    by_market: dict[object, list[str]] = {}
    by_industry: dict[object, list[str]] = {}
    for c in r.columns:
        by_market.setdefault(market[c], []).append(c)
        by_industry.setdefault((market[c], ind.get(c, "NA")), []).append(c)

    if factors == "none":
        return r, market, ind
    parts = []
    for _, chunk in r.groupby(r.index.year):
        x = residualize(chunk, by_market)
        parts.append(residualize(x, by_industry) if factors == "market+industry" else x)
    return pd.concat(parts).sort_index(), market, ind


def response_index(event_days: np.ndarray, dst_days: np.ndarray, src_mkt: str, dst_mkt: str,
                   lag: int) -> np.ndarray:
    """이벤트 날짜마다 반응을 읽을 대상 시장 세션의 인덱스. 범위 밖이면 -1.

    lag=1 이 기본 — "정보가 공개된 뒤 처음으로 거래 가능한 세션". KOSPI 가 먼저 닫으므로
    KOSPI 이벤트 -> NASDAQ 은 같은 달력날짜가 그 세션이다.
    lag=0 은 한 세션 앞, lag=-1 은 두 세션 앞. 같은 시장이면 lag=0 이 이벤트 당일 자신이라
    기계가 제대로 붙었는지 보는 양성 대조군이 되고, lag=-1 은 이벤트 전이라 0 이 나와야 하는
    위약 대조군이 된다.
    """
    side = "left" if (src_mkt == "KOSPI" and dst_mkt == "NASDAQ") else "right"
    idx = np.searchsorted(dst_days, event_days, side=side) + (lag - 1)
    return np.where((idx >= 0) & (idx < len(dst_days)), idx, -1)


def load_edges(relations: Path, scores: Path) -> pd.DataFrame:
    """관계 엣지를 방향 있는 (src -> dst) 행으로 편다. UNDIRECTED 는 양쪽 다."""
    rel = pd.read_csv(relations, dtype=str)
    sc = pd.read_csv(scores, dtype=str)
    key = ["source_market", "source_stock_code", "target_market", "target_stock_code",
           "relationship_type_code"]
    rel = rel.merge(sc[key + ["impact_direction", "score", "confidence"]], on=key, how="left")
    rows = []
    for v in rel.itertuples(index=False):
        base = dict(rel_type=v.relationship_type_code, impact=v.impact_direction,
                    score=float(v.score), confidence=float(v.confidence))
        rows.append(dict(src=v.source_stock_code, dst=v.target_stock_code,
                         orientation="forward", **base))
        if v.relationship_type_code in UNDIRECTED:
            rows.append(dict(src=v.target_stock_code, dst=v.source_stock_code,
                             orientation="forward", **base))
        else:
            rows.append(dict(src=v.target_stock_code, dst=v.source_stock_code,
                             orientation="reverse", **base))
    return pd.DataFrame(rows).drop_duplicates(["src", "dst", "rel_type", "orientation"])


def fama_macbeth(frame: pd.DataFrame, col: str = "excess") -> tuple[float, float, int]:
    """이벤트 날짜별로 먼저 평균을 낸 뒤, 날짜 시계열에 t 검정.

    같은 날의 쌍 수백 개는 독립 관측이 아니다 — 그날의 시장 상태를 공유한다. 관측을 그대로
    풀링해 t 를 구하면 표본이 실제보다 수십 배 많은 셈이 돼 t 가 부풀려진다. 날짜를 하나의
    관측으로 세는 Fama-MacBeth 가 이 설계의 표준이다.
    """
    m = frame.groupby("day")[col].mean().to_numpy()
    if len(m) < 3:
        return float(m.mean()) if len(m) else float("nan"), float("nan"), len(m)
    se = m.std(ddof=1) / math.sqrt(len(m))
    return float(m.mean()), float(m.mean() / se) if se > 0 else float("nan"), len(m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", default=PRICES, type=Path)
    ap.add_argument("--industry", default=INDUSTRY, type=Path)
    ap.add_argument("--relations", default=RELATIONS, type=Path)
    ap.add_argument("--scores", default=SCORES, type=Path)
    ap.add_argument("--out", default=OUT, type=Path)
    ap.add_argument("--pct", type=float, default=1.0, help="이벤트로 잡을 |잔차| 상위 %")
    ap.add_argument("--winsor", type=float, default=0.005)
    ap.add_argument("--factors", choices=("market+industry", "market", "none"),
                    default="market+industry")
    ap.add_argument("--start", default="2016-01-01", help="이 날짜 이후 이벤트만")
    ap.add_argument("--lag", type=int, default=1,
                    help="1=정보 공개 뒤 첫 거래세션(기본), 0=한 세션 앞(양성 대조), -1=위약")
    ap.add_argument("--permute", type=int, default=0, help="엣지 재배선 위약검정 반복 횟수")
    ap.add_argument("--profile", nargs=2, type=int, metavar=("FROM", "TO"), default=None,
                    help="이벤트 창 프로파일을 이 lag 범위로 뽑는다 (예: --profile -3 3)")
    ap.add_argument("--metric", choices=("signed", "absz"), default="signed",
                    help="signed=부호맞춤 수익률(방향 전파), absz=|잔차|/σ (변동성 전파)")
    args = ap.parse_args()
    # signed 는 수익률이라 bp, absz 는 이미 시그마 단위라 %σ 로 찍는다.
    SCALE, UNIT = (1e4, "bp") if args.metric == "signed" else (1e2, "%σ")

    resid, market, _ = build_residuals(args.prices, args.industry, args.winsor, args.factors)
    resid = resid.loc[resid.index >= pd.Timestamp(args.start)]
    edges = load_edges(args.relations, args.scores)

    tickers = list(resid.columns)
    pos = {t: i for i, t in enumerate(tickers)}
    days = resid.index.values
    R = resid.to_numpy(dtype=float)                     # (n_days, n_tickers)
    Z = R / np.nanstd(R, axis=0, keepdims=True)         # --metric absz 용 시그마 단위

    cols = {m: np.array([pos[t] for t in tickers if market[t] == m]) for m in ("KOSPI", "NASDAQ")}
    # 시장별 거래일 = 그 시장 종목의 잔차가 하나라도 있는 행. 휴장일이 서로 다르므로 분리한다.
    sess = {m: np.flatnonzero(np.isfinite(R[:, cols[m]]).any(axis=1)) for m in cols}
    sess_days = {m: days[sess[m]] for m in cols}

    # 1) 이벤트: 종목별 |잔차| 상위 pct%
    ev_src, ev_day, ev_sign, ev_mag = [], [], [], []
    for t in tickers:
        x = resid[t].dropna()
        if len(x) < 250:
            continue
        thr = np.nanpercentile(np.abs(x.to_numpy()), 100 - args.pct)
        hit = x[np.abs(x) >= thr]
        ev_src += [t] * len(hit)
        ev_day.append(hit.index.values)
        ev_sign.append(np.sign(hit.to_numpy()))
        ev_mag.append(np.abs(hit.to_numpy()))
    ev = pd.DataFrame({"src": ev_src, "day": np.concatenate(ev_day),
                       "sign": np.concatenate(ev_sign), "mag": np.concatenate(ev_mag)})
    ev["src_mkt"] = ev["src"].map(market)

    # 2) 이벤트 x 대상시장 마다 "종목별 부호맞춤 반응" 행렬을 만든다.
    #    ev_resp[(src_mkt, dst_mkt)] -> (그 시장 이벤트 수, 대상시장 종목 수)
    ev_idx = {}                       # (src_mkt, dst_mkt) -> ev 행 index (정렬됨)
    for s_mkt in ("KOSPI", "NASDAQ"):
        sub = ev.index[ev["src_mkt"] == s_mkt].to_numpy()
        for d_mkt in ("KOSPI", "NASDAQ"):
            if len(sub):
                ev_idx[(s_mkt, d_mkt)] = sub

    def response_matrices(lag: int):
        resp, respday = {}, {}
        for (s_mkt, d_mkt), sub in ev_idx.items():
            e_days = ev.loc[sub, "day"].to_numpy()
            e_sign = ev.loc[sub, "sign"].to_numpy()
            k = response_index(e_days, sess_days[d_mkt], s_mkt, d_mkt, lag)
            good = k >= 0
            rrow = np.where(good, sess[d_mkt][np.clip(k, 0, len(sess[d_mkt]) - 1)], 0)
            mat = np.full((len(sub), len(cols[d_mkt])), np.nan)
            if args.metric == "signed":
                mat[good] = R[np.ix_(rrow[good], cols[d_mkt])] * e_sign[good, None]
            else:
                # 방향이 아니라 "흔들림이 옮는가". 종목별 표준편차로 나눠 시그마 단위로 본다.
                mat[good] = np.abs(Z[np.ix_(rrow[good], cols[d_mkt])])
            resp[(s_mkt, d_mkt)] = mat
            respday[(s_mkt, d_mkt)] = np.where(good, days[rrow], np.datetime64("NaT", "ns"))
        return resp, respday

    # 3) 각 엣지 쌍 x 이벤트의 excess = 반응 - (A 와 무관한 같은 시장 종목들의 그날 평균)
    ev_by_src = {t: g.to_numpy() for t, g in ev.groupby("src").groups.items()}
    dst_pos = {m: {tickers[c]: j for j, c in enumerate(cols[m])} for m in cols}
    neighbors: dict[str, set[str]] = {}
    for v in edges.itertuples(index=False):
        neighbors.setdefault(v.src, set()).add(v.dst)

    def collect(edge_frame: pd.DataFrame, lag: int) -> pd.DataFrame:
        """엣지 목록마다 (쌍 x 이벤트) 관측을 만든다. 대조군 제외집합은 항상 실제 이웃으로
        고정한다 — 무작위 재배선과 비교할 때 대조군 자체는 같아야 차이가 엣지에서만 온다."""
        ev_resp, ev_respday = response_matrices(lag)
        records = []
        for v in edge_frame.itertuples(index=False):
            rows = ev_by_src.get(v.src)
            if rows is None or len(rows) == 0:
                continue
            s_mkt, d_mkt = market[v.src], market[v.dst]
            if (s_mkt, d_mkt) not in ev_idx:
                continue
            sel = np.searchsorted(ev_idx[(s_mkt, d_mkt)], rows)   # ev_idx 는 정렬돼 있다
            mat = ev_resp[(s_mkt, d_mkt)][sel]                    # (n_ev, n_dst)
            treated = mat[:, dst_pos[d_mkt][v.dst]]
            # 대조군: 같은 시장에서 A 자신과 A 의 관계 이웃을 뺀 전 종목
            drop = [dst_pos[d_mkt][t] for t in (neighbors.get(v.src, set()) | {v.src})
                    if t in dst_pos[d_mkt]]
            ctrl_mask = np.ones(mat.shape[1], dtype=bool)
            ctrl_mask[drop] = False
            block = mat[:, ctrl_mask]
            with np.errstate(invalid="ignore"):
                # 그 세션에 대상 시장이 통째로 결측인 이벤트는 대조군이 비어 NaN 이 된다.
                # 아래 keep 마스크에서 걸러지므로 경고만 끈다.
                cnt = np.isfinite(block).sum(axis=1)
                ctrl = np.where(cnt > 0, np.nansum(block, axis=1) / np.maximum(cnt, 1), np.nan)
            keep = np.isfinite(treated) & np.isfinite(ctrl)
            if not keep.any():
                continue
            records.append(pd.DataFrame({
                "src": v.src, "dst": v.dst, "rel_type": v.rel_type, "impact": v.impact,
                "orientation": v.orientation, "cross": s_mkt != d_mkt, "lag": lag,
                "day": ev.loc[rows[keep], "day"].to_numpy(),
                "ev_sign": ev.loc[rows[keep], "sign"].to_numpy(),
                "ev_mag": ev.loc[rows[keep], "mag"].to_numpy(),
                "resp_day": ev_respday[(s_mkt, d_mkt)][sel][keep],
                "treated": treated[keep], "control": ctrl[keep],
                "excess": treated[keep] - ctrl[keep],
            }))
        return pd.concat(records, ignore_index=True) if records else pd.DataFrame()

    obs = collect(edges, args.lag)

    # 4) 집계
    def agg(frame: pd.DataFrame, label: str, expect: str) -> dict:
        mean, t, n_days = fama_macbeth(frame)
        return {"group": label, "expect": expect, "n_pairs": frame.groupby(["src", "dst"]).ngroups,
                "n_obs": len(frame), "n_event_days": n_days,
                "excess_bp": round(mean * SCALE, 2), "t_stat": round(t, 2) if t == t else float("nan"),
                "raw_signed_bp": round(frame["treated"].mean() * SCALE, 2),
                "share_positive": round(float((frame["excess"] > 0).mean()), 4)}

    summary = [agg(obs, "ALL", "mixed")]
    for rt, g in obs.groupby("rel_type"):
        summary.append(agg(g, rt, g["impact"].iloc[0]))
    for rt, g in obs.groupby("rel_type"):
        for c, gg in g.groupby("cross"):
            summary.append(agg(gg, f"  {rt}/{'cross' if c else 'same'}-mkt", g["impact"].iloc[0]))
    for rt in ("INVEST", "SUPPLY"):
        for o, gg in obs[obs.rel_type == rt].groupby("orientation"):
            summary.append(agg(gg, f"  {rt}/{o}", gg["impact"].iloc[0]))
    sm = pd.DataFrame(summary)

    # 5) 재배선 위약검정 — 같은 이벤트, 같은 대조군, dst 만 시장 안에서 섞는다.
    null = {}
    if args.permute:
        rng = np.random.default_rng(20260914)
        keys = ["ALL"] + sorted(obs["rel_type"].unique())
        draws = {k: [] for k in keys}
        for _ in range(args.permute):
            fake = edges.copy()
            fake["_m"] = fake["dst"].map(market)
            fake["dst"] = fake.groupby("_m")["dst"].transform(
                lambda s: rng.permutation(s.to_numpy()))
            o = collect(fake.drop(columns="_m"), args.lag)
            draws["ALL"].append(fama_macbeth(o)[0] * SCALE)
            for rt, g in o.groupby("rel_type"):
                draws[rt].append(fama_macbeth(g)[0] * SCALE)
        for k, v in draws.items():
            a = np.array(v)
            real = sm.loc[sm["group"] == k, "excess_bp"].iloc[0]
            null[k] = (a.mean(), a.std(ddof=1), float((np.abs(a) >= abs(real)).mean()))

    # 6) 이벤트 창 프로파일. 진짜 전파라면 0 에서 솟고 +1 이 남았다가 꺼진다. 쌍 자체가 그냥
    #    더 상관돼 있을 뿐이라면 -2..+3 이 평평하게 들려 있다. 둘은 lag=+1 만 봐서는 구분이 안 된다.
    prof, paired = None, None
    if args.profile:
        rows = []
        keep_cols = ["src", "dst", "rel_type", "day", "ev_sign", "excess"]
        by_lag = {}
        for lg in range(args.profile[0], args.profile[1] + 1):
            o = collect(edges, lg)
            by_lag[lg] = o[keep_cols]
            for label, g in [("ALL", o)] + list(o.groupby("rel_type")):
                m, t, nd = fama_macbeth(g)
                rows.append({"group": label, "lag": lg, "excess_bp": round(m * SCALE, 2),
                             "t_stat": round(t, 2), "n_obs": len(g), "n_event_days": nd})
        prof = pd.DataFrame(rows)

        # 결정적 검정: 같은 (쌍, 이벤트)에서 사후(+1) - 사전(-1). 이벤트가 원인이 아닌
        # 상시 동조는 양쪽에 똑같이 실리므로 차분하면 지워진다. 남는 것만이 전파다.
        if -1 in by_lag and 1 in by_lag:
            key = ["src", "dst", "rel_type", "day", "ev_sign"]
            d = (by_lag[1].merge(by_lag[-1], on=key, suffixes=("_post", "_pre")))
            d["excess"] = d["excess_post"] - d["excess_pre"]
            groups = [("ALL", d)] + list(d.groupby("rel_type"))
            # 악재만 전파된다는 통설도 같이 본다 — 호재/악재를 갈라도 남는 게 없어야 결론이 선다.
            groups += [(f"ALL/{'악재' if s < 0 else '호재'}", g)
                       for s, g in d.groupby("ev_sign")]
            out = []
            for label, g in groups:
                m, t, nd = fama_macbeth(g)
                out.append({"group": label, "net_bp": round(m * SCALE, 2), "t_stat": round(t, 2),
                            "n_obs": len(g), "n_event_days": nd})
            paired = pd.DataFrame(out)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sm.to_csv(args.out, index=False, encoding="utf-8")
    obs.to_csv(args.out.with_name("event_study_pairs.csv"), index=False, encoding="utf-8")
    if prof is not None:
        prof.to_csv(args.out.with_name("event_study_profile.csv"), index=False, encoding="utf-8")

    print(f"factors={args.factors} winsor={args.winsor} lag={args.lag} "
          f"이벤트=|잔차| 상위 {args.pct}%  기간 {resid.index.min().date()}..{resid.index.max().date()}")
    print(f"이벤트 {len(ev):,}건 / {ev['src'].nunique()}종목 | 방향 엣지 {len(edges)} "
          f"({edges['rel_type'].value_counts().to_dict()})")
    print(f"쌍x이벤트 관측 {len(obs):,}건, 이벤트 날짜 {obs['day'].nunique():,}일")
    print()
    print(f"  excess = sign(잔차_A[D]) * 잔차_B[반응세션] - 같은날 무관계쌍 평균 (단위 {UNIT})")
    print("  t 는 이벤트 날짜를 관측 1개로 세는 Fama-MacBeth. POSITIVE 는 +, COMPETE 는 - 를 기대.")
    print(f"{'group':<24}{'expect':<9}{'pairs':>6}{'obs':>8}{'days':>6}"
          f"{'excess('+UNIT+')':>13}{'t':>7}{'raw':>9}{'P(+)':>7}")
    for v in sm.itertuples(index=False):
        print(f"{v.group:<24}{str(v.expect):<9}{v.n_pairs:>6}{v.n_obs:>8}{v.n_event_days:>6}"
              f"{v.excess_bp:>13.2f}{v.t_stat:>7.2f}{v.raw_signed_bp:>9.2f}{v.share_positive:>7.3f}")
    if null:
        print(f"\n  재배선 위약검정 {args.permute}회 (dst 를 시장 안에서 섞음)")
        for k, (mu, sd, p) in null.items():
            real = sm.loc[sm["group"] == k, "excess_bp"].iloc[0]
            print(f"    {k:<10} 실제 {real:+7.2f} bp | 귀무 {mu:+6.2f}±{sd:.2f} bp | p={p:.3f}")

    if prof is not None:
        lags = sorted(prof["lag"].unique())
        print(f"\n  이벤트 창 프로파일 (excess {UNIT}, 괄호는 t). lag 0 = 이벤트 세션 자신.")
        print(f"  전파라면 0 에서 솟고 +1 만 남는다. 상시 동조면 -2..+2 가 평평하게 들린다.")
        print(f"{'group':<12}" + "".join(f"{('lag' + str(l)):>16}" for l in lags))
        for label in ["ALL"] + sorted(obs["rel_type"].unique()):
            g = prof[prof["group"] == label].set_index("lag")
            cells = "".join(f"{g.loc[l, 'excess_bp']:>10.1f}({g.loc[l, 't_stat']:>4.1f})"
                            if l in g.index else f"{'-':>16}" for l in lags)
            print(f"{label:<12}{cells}")

    if paired is not None:
        print(f"\n  사후(+1) - 사전(-1) 짝지은 차분 = 이벤트에 귀속되는 순전파")
        print(f"{'group':<12}{'net('+UNIT+')':>12}{'t':>7}{'obs':>8}{'days':>7}")
        for v in paired.itertuples(index=False):
            print(f"{v.group:<12}{v.net_bp:>12.2f}{v.t_stat:>7.2f}{v.n_obs:>8}{v.n_event_days:>7}")

    print(f"\n  -> {args.out}")


if __name__ == "__main__":
    main()
