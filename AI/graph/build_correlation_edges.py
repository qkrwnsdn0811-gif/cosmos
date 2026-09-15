"""Knowledge graph step 5 — price correlation edges from daily adjusted closes.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_correlation_edges.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_correlation_edges.py --window 504 --min-abs-corr 0.25
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_correlation_edges.py --factors market   # 비교용
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_correlation_edges.py --positive-only

Source: data/prices_daily.parquet
        = HDFS /datasets/prices/snapshots/20260911-krx-nasdaq-daily/data/prices_daily.parquet
202종목(KOSPI 100 + NASDAQ 102), 2016-01-04~2026-09-11, 480,723행.

수익률은 adj_close 로만 계산한다 — NASDAQ close_price 는 액면분할·배당이 반영돼 있지 않아
분할일이 -50% 폭락으로 잡힌다 (NASDAQ 행의 64.6%에서 close_price != adj_close).
KOSPI 는 pykrx 가 수정주가를 주므로 둘이 같다.

────────────────────────────────────────────────────────────────────────────
왜 원시 상관을 그대로 쓰면 안 되는가 (252일 창 실측)
────────────────────────────────────────────────────────────────────────────
1) 시장 베타. 원시 로그수익률 상관은 KOSPI 임의 쌍 평균 0.362, NASDAQ 0.107 이다.
   "같이 움직인다"의 대부분은 기업 관계가 아니라 지수다. 임계값 0.3 을 원시 상관에 걸면
   KOSPI 는 거의 전 쌍이 살아남고 NASDAQ 은 거의 다 죽어, 같은 숫자가 시장마다 다른 뜻이
   된다. → 시장별 동일가중 팩터에 회귀한 잔차를 쓴다. 잔차 평균은 두 시장 모두 0.00.

2) 섹터 로테이션. 시장 팩터만 빼면 잔차에 두 번째 공통 팩터가 그대로 남는다. 잔차 상관행렬
   PC1 이 NASDAQ 17.1% / KOSPI 11.4% 를 설명했다(순수 잡음이면 1.0%). PC1 양극단은
   NASDAQ [LRCX AMAT TER KLAC MU WDC STX] vs [ROP TRI WDAY ADSK ADP ADBE PAYX],
   KOSPI  [000660 402340 298040 009150 005930] vs [055550 033780 323410 086790 024110]
   — 즉 AI 하드웨어 대 소프트웨어·은행 로테이션 하나다. 이것 때문에 아무 관계도 없는
   ADP-TER 가 -0.699, LRCX-WDAY 가 -0.589 로 강한 엣지가 됐다. 관계가 아니라 1년간
   로테이션의 반대편에 서 있었을 뿐이다.
   → 시장 팩터를 뺀 뒤 산업 팩터(company_industry.csv 18분류, 시장 내 5종목 이상인
     산업만)를 한 번 더 뺀다. PC1 은 17.1%→8.5% / 11.4%→8.4% 로 내려가고
     ADP-TER -0.699→-0.373, LRCX-WDAY -0.589→-0.109 로 무너진다.
     반면 진짜 쌍별 관계는 거의 그대로다:
       GOOG-GOOGL(동일기업 이중상장) 0.995→0.994
       000660-402340(SK하이닉스-SK스퀘어 지분)  0.824→0.778
       AMAT-LRCX(동종 장비)  0.819→0.572   ← 업종 설명분만큼만 깎임
   산업 공통분을 빼면 이 엣지는 sector 엣지와 겹치지 않는 정보만 남는다. "같은 업종이라
   같이 움직인다"는 이미 edges_sector.csv 가 말하고 있으므로, 여기서는 업종으로 설명되지
   않는 쌍별 동조만 남기는 게 그래프에 정보를 더한다.

3) 교차시장 시차. KRX 는 NASDAQ 보다 먼저 닫는다(15:30 KST = 06:30 UTC 마감, NASDAQ 은
   같은 날짜 14:30 UTC 개장). 달력 날짜 t 로 맞추면 KOSPI×NASDAQ 상관이 평균 -0.006 /
   p95 0.137 로 거의 0 이다 — 관계가 없어서가 아니라 시점이 어긋나서다. NASDAQ 을 직전
   거래일로 당겨(t-1) 붙이면 평균 0.085 / p95 0.285 로 살아난다. 교차쌍은
   merge_asof(backward, 당일 제외)로 "KOSPI 거래일 t 직전의 NASDAQ 거래일"을 붙인다.
   행 shift 가 아니라 날짜 기준이라 공휴일이 어긋나도 안전하다.

────────────────────────────────────────────────────────────────────────────
Edge schema (지식그래프 설계 6장 확정안) + 진단 컬럼:
  src_ticker, dst_ticker, edge_type=correlation, weight, as_of_date, evidence,
  corr_resid, corr_mkt, corr_raw, n_days, alignment, same_market, same_industry,
  window_start, window_end
무방향이므로 쌍마다 한 번, src < dst (문자열 순서)로 저장한다.
weight = |corr_resid| — 부호는 corr_resid 에 남긴다. corr_raw / corr_mkt 를 같이 실어서
보정이 무엇을 깎았는지 리뷰에서 바로 볼 수 있게 한다.

4) 음의 잔차상관은 기본적으로 버린다 (--include-negative 로 켤 수 있음).
   이유: 임계값을 넘긴 음의 엣지 77개 중 70개가 같은 산업이었고, 그 중 95%가 시장 보정만
   했을 때보다 산업 보정 뒤에 오히려 더 강해졌다(양의 엣지는 같은 현상이 14%뿐). 관계가
   진짜라면 팩터를 뺀다고 상관이 커질 이유가 없다. 원인은 산업 분류 입도다 — SW 23종목
   안에 AI 인프라(CRWV, NBIS)와 전통 SaaS(ADP, PAYX, WDAY, ADBE, INTU)가 같이 들어
   있어서, 한 덩어리의 평균을 빼면 두 하위그룹이 기계적으로 서로 음수가 된다. FIN(증권 대
   은행) 22개, SW 16개, TRANSPORT 8개가 이렇게 만들어졌다. 경쟁 관계의 증거가 아니라
   분류 입도의 부산물이므로 그래프에 넣지 않는다.

알려진 한계
  - 교차시장 엣지가 0개다. KOSPI×NASDAQ 10,000쌍을 모두 평가했지만 산업 팩터까지 뺀 뒤
    최대 |r| 이 0.285 로 임계값 0.30 에 못 미친다(시장 팩터만 뺐을 땐 11개가 남았다).
    한국·미국 종목의 연결은 쌍별 관계가 아니라 거의 전부 섹터·테마 수준이라는 뜻이다.
    교차시장 엣지가 필요하면 --factors market 으로 뽑되, 그건 "같은 테마"라는 뜻이지
    "이 두 회사가 관계 있다"는 뜻이 아니다.
  - 창 하나(252일)는 한 국면이다. 2025-09~2026-09 는 AI 랠리 구간이라 반도체·AI 인프라
    쌍이 과대표집된다. 정기 갱신 시 --window 로 민감도를 같이 볼 것.
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PRICES = HERE / "data" / "prices_daily.parquet"
INDUSTRY = HERE.parent / "ner" / "data" / "company_industry.csv"
OUT = HERE / "data" / "edges_correlation.csv"
FIELDS = ["src_ticker", "dst_ticker", "edge_type", "weight", "as_of_date", "evidence",
          "corr_resid", "corr_mkt", "corr_raw", "n_days", "alignment", "same_market",
          "same_industry", "window_start", "window_end"]
MIN_GROUP = 5          # 이보다 작은 산업은 팩터를 만들지 않는다 (잔차가 과하게 깎인다)
MIN_FIT = 30           # beta 추정에 필요한 최소 관측일


def log_returns(px: pd.DataFrame, winsor: float) -> pd.DataFrame:
    """Log returns from adjusted closes, winsorized per ticker.

    단일 갭데이(실적 쇼크, 유상증자)가 쌍의 상관 전체를 결정하지 않게 꼬리를 눌러둔다.
    0.5%면 252일 창에서 한쪽 1~2일만 클리핑된다.
    """
    r = np.log(px).diff()
    if winsor > 0:
        r = r.clip(lower=r.quantile(winsor), upper=r.quantile(1 - winsor), axis=1)
    return r


def residualize(frame: pd.DataFrame, groups: dict[object, list[str]]) -> pd.DataFrame:
    """Regress out each group's leave-one-out equal-weight factor, in place of the group's members.

    자기 자신을 팩터에서 빼지 않으면(100종목 중 1%가 자기 자신) beta 가 위로 편향돼 잔차가
    과하게 깎인다. leave-one-out 평균은 (합 - 자기) / (개수 - 1) 로 싸게 구한다.
    MIN_GROUP 미만인 그룹은 건너뛴다 — 3종목짜리 산업에서 자기를 뺀 평균에 회귀하면
    잔차에 남는 게 거의 없다.
    """
    out = frame.copy()
    for _, cols in sorted(groups.items(), key=lambda kv: str(kv[0])):
        cols = [c for c in cols if c in frame.columns]
        if len(cols) < MIN_GROUP:
            continue
        block = frame[cols]
        tot, cnt = block.sum(axis=1, skipna=True), block.count(axis=1)
        for c in cols:
            x = block[c]
            loo = (tot - x.fillna(0.0)) / (cnt - x.notna().astype(int)).replace(0, np.nan)
            d = pd.concat([x, loo], axis=1).dropna()
            if len(d) < MIN_FIT or d.iloc[:, 1].var() == 0:
                out[c] = np.nan      # 상장 직후라 팩터에 회귀할 표본이 없는 종목
                continue
            beta = d.iloc[:, 0].cov(d.iloc[:, 1]) / d.iloc[:, 1].var()
            out[c] = x - beta * loo
    return out


def lag_align(frame: pd.DataFrame, cols: list[str], target_days: pd.Index) -> pd.DataFrame:
    """For each KOSPI trading day t, take the NASDAQ row from the latest NASDAQ day < t."""
    nas = frame[cols].dropna(how="all")
    right = nas.reset_index()
    right.columns = ["d"] + list(nas.columns)
    right["d"] = pd.to_datetime(right["d"])
    left = pd.DataFrame({"d": pd.to_datetime(target_days)}).sort_values("d")
    merged = pd.merge_asof(left, right.sort_values("d"), on="d",
                           direction="backward", allow_exact_matches=False)
    return merged.set_index("d")[cols]


def corr_pack(frame: pd.DataFrame, kospi: list[str], nasdaq: list[str],
              kospi_days: pd.Index, min_days: int, cross_lag: bool) -> dict:
    """Correlation + overlap-count matrices for same-market (same day) and cross-market (lagged)."""
    same = frame.corr(min_periods=min_days)
    flags = frame.notna().astype(int)
    same_n = flags.T.dot(flags)
    if not cross_lag:
        return {"same": same, "same_n": same_n, "cross": same, "cross_n": same_n}
    joined = pd.concat([frame.loc[kospi_days, kospi], lag_align(frame, nasdaq, kospi_days)], axis=1)
    jflags = joined.notna().astype(int)
    return {"same": same, "same_n": same_n,
            "cross": joined.corr(min_periods=min_days), "cross_n": jflags.T.dot(jflags)}


def critical_r(n: int, n_tests: int, alpha: float = 0.05) -> float:
    """Bonferroni-corrected two-sided critical |r| for sample size n over n_tests pairs."""
    from scipy import stats
    if n <= 3 or n_tests <= 0:
        return float("nan")
    t = stats.t.ppf(1 - alpha / (2 * n_tests), n - 2)
    return float(t / math.sqrt(n - 2 + t * t))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", default=PRICES, type=Path)
    ap.add_argument("--industry", default=INDUSTRY, type=Path)
    ap.add_argument("--out", default=OUT, type=Path)
    ap.add_argument("--window", type=int, default=252, help="거래일 수 (252 ~= 1년)")
    ap.add_argument("--min-days", type=int, default=120, help="쌍이 겹쳐야 하는 최소 거래일")
    ap.add_argument("--min-abs-corr", type=float, default=0.3)
    ap.add_argument("--winsor", type=float, default=0.005, help="꼬리 절단 비율, 0이면 끔")
    ap.add_argument("--factors", choices=("market+industry", "market", "none"),
                    default="market+industry", help="제거할 공통 팩터")
    ap.add_argument("--include-negative", action="store_true",
                    help="음의 잔차상관도 내보냄 (기본은 제외, 위 4) 참고)")
    ap.add_argument("--no-cross-lag", action="store_true", help="교차시장도 같은 날짜로 맞춤(권장 안 함)")
    ap.add_argument("--as-of", default=None, help="기본값은 창의 마지막 거래일")
    args = ap.parse_args()

    df = pd.read_parquet(args.prices, columns=["ticker", "market", "trading_at", "adj_close"])
    market = df.drop_duplicates("ticker").set_index("ticker")["market"]
    ind = pd.read_csv(args.industry, dtype=str).set_index("ticker")["industry_id"]
    px = df.pivot_table(index="trading_at", columns="ticker", values="adj_close")
    px.index = pd.to_datetime(px.index)
    px = px.sort_index()

    r = log_returns(px, args.winsor).tail(args.window)
    win_start, win_end = r.index.min().date(), r.index.max().date()
    as_of = args.as_of or win_end.isoformat()

    by_market: dict[object, list[str]] = {}
    by_industry: dict[object, list[str]] = {}
    for c in r.columns:
        by_market.setdefault(market[c], []).append(c)
        by_industry.setdefault((market[c], ind.get(c, "NA")), []).append(c)   # 산업 팩터는 시장 안에서

    resid_mkt = r if args.factors == "none" else residualize(r, by_market)
    resid = residualize(resid_mkt, by_industry) if args.factors == "market+industry" else resid_mkt

    kospi = [c for c in r.columns if market[c] == "KOSPI"]
    nasdaq = [c for c in r.columns if market[c] == "NASDAQ"]
    kospi_days = r[kospi].dropna(how="all").index
    lag = not args.no_cross_lag
    P = corr_pack(resid, kospi, nasdaq, kospi_days, args.min_days, lag)
    P_mkt = corr_pack(resid_mkt, kospi, nasdaq, kospi_days, args.min_days, lag)
    P_raw = corr_pack(r, kospi, nasdaq, kospi_days, args.min_days, lag)
    alignment = "nasdaq(t-1)->kospi(t)" if lag else "same-day"

    tickers = sorted(r.columns)
    rows, skipped_short = [], 0
    for i, a in enumerate(tickers):
        for b in tickers[i + 1:]:
            x = market[a] != market[b]
            ck, nk = ("cross", "cross_n") if x else ("same", "same_n")
            try:
                cr, n_days = P[ck].at[a, b], int(P[nk].at[a, b])
            except KeyError:
                continue
            if pd.isna(cr) or n_days < args.min_days:
                skipped_short += 1
                continue
            if abs(cr) < args.min_abs_corr or (cr < 0 and not args.include_negative):
                continue
            c_mkt, c_raw = P_mkt[ck].at[a, b], P_raw[ck].at[a, b]
            align = alignment if x else "same-day"
            ia, ib = ind.get(a), ind.get(b)
            same_ind = bool(ia) and ia == ib      # 둘 다 미배정이면 '같은 산업'이 아니다
            rows.append({
                "src_ticker": a, "dst_ticker": b, "edge_type": "correlation",
                "weight": round(abs(float(cr)), 4), "as_of_date": as_of,
                "evidence": (f"correlation:r={cr:.3f};r_mkt={c_mkt:.3f};r_raw={c_raw:.3f};"
                             f"n={n_days};win={win_start}..{win_end};align={align}"),
                "corr_resid": round(float(cr), 4), "corr_mkt": round(float(c_mkt), 4),
                "corr_raw": round(float(c_raw), 4), "n_days": n_days, "alignment": align,
                "same_market": str(not x).lower(),
                "same_industry": str(same_ind).lower(),
                "window_start": win_start.isoformat(), "window_end": win_end.isoformat(),
            })
    rows.sort(key=lambda v: -v["weight"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    n_pairs = len(tickers) * (len(tickers) - 1) // 2
    med_n = int(np.median([v["n_days"] for v in rows])) if rows else args.window
    neg = sum(1 for v in rows if v["corr_resid"] < 0)
    xm = sum(1 for v in rows if v["same_market"] == "false")
    si = sum(1 for v in rows if v["same_industry"] == "true")
    print(f"window {win_start}..{win_end} ({len(r)} 거래일) | tickers={len(tickers)} | "
          f"factors={args.factors} | winsor={args.winsor}")
    print(f"edges={len(rows)} / {n_pairs} 쌍 ({len(rows)/n_pairs:.1%})  "
          f"임계 |r|>={args.min_abs_corr} (Bonferroni 유의 |r|>={critical_r(med_n, n_pairs):.3f})")
    print(f"  같은 산업 {si} | 교차시장 {xm} | 음의 상관 {neg} | 표본 부족 제외 {skipped_short}")
    print(f"  -> {args.out}")
    for v in rows[:15]:
        print(f"  {v['src_ticker']:>7} - {v['dst_ticker']:<7} r={v['corr_resid']:+.3f} "
              f"(mkt {v['corr_mkt']:+.3f} raw {v['corr_raw']:+.3f}) n={v['n_days']:>3} "
              f"{'同산업' if v['same_industry']=='true' else '      '} {v['alignment']}")


if __name__ == "__main__":
    main()
