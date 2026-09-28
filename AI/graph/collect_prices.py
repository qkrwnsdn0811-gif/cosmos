"""Daily OHLCV for the 202-company universe, 2016-01-01 onward, for HDFS.

    PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe collect_prices.py               # everything
    PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe collect_prices.py --market KOSPI
    PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe collect_prices.py --combine     # only rebuild parquet

Two sources, because no single free one covers both halves of the universe:
    KOSPI  100종목 -> pykrx      (KRX 공식, 수정주가)
    NASDAQ 102종목 -> yfinance   (Yahoo, raw OHLC + Adj Close)

Runs locally: the EC2 has no pip, so collect here and upload the parquet with upload_prices.sh.

Per ticker one CSV under data/prices_raw/, so a interrupted run resumes instead of refetching.
--combine merges them into data/prices_daily.parquet with this schema:

    ticker          종목코드 / 티커          005930, AAPL
    market          KOSPI | NASDAQ
    trading_at      거래일 (date)
    interval_type   '1d'                    backend stock_price_history.interval_type와 동일
    open/high/low/close_price
    adj_close       배당·액면분할 반영 종가   수익률 계산은 반드시 이 컬럼으로
    trading_volume

close_price vs adj_close: KOSPI는 pykrx가 이미 수정주가를 주므로 둘이 같다. NASDAQ은 화면에
보여줄 실제 체결가(close_price)와 수익률용 조정가(adj_close)가 다르다. 상관관계 엣지와 GNN
라벨은 adj_close 기준으로 계산해야 액면분할이 가짜 급등락으로 잡히지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIVERSE = HERE.parent / "ner" / "data" / "companies.csv"
RAW = HERE / "data" / "prices_raw"
OUT = HERE / "data" / "prices_daily.parquet"
START = "2016-01-01"
FIELDS = ["ticker", "market", "trading_at", "interval_type",
          "open_price", "high_price", "low_price", "close_price", "adj_close", "trading_volume"]


def universe(market: str | None) -> list[tuple[str, str]]:
    with UNIVERSE.open(encoding="utf-8-sig", newline="") as f:
        rows = [(r["ticker"], r["market"]) for r in csv.DictReader(f)]
    return [(t, m) for t, m in rows if not market or m == market]


def write_rows(ticker: str, rows: list[dict]) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    with (RAW / f"{ticker}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def fetch_kospi(ticker: str, end: str) -> list[dict]:
    from pykrx import stock
    df = stock.get_market_ohlcv_by_date(START.replace("-", ""), end.replace("-", ""), ticker)
    out = []
    for idx, r in df.iterrows():
        if not r["거래량"] and not r["종가"]:
            continue  # halted / pre-listing padding
        close = float(r["종가"])
        out.append({"ticker": ticker, "market": "KOSPI", "trading_at": idx.date().isoformat(),
                    "interval_type": "1d", "open_price": float(r["시가"]), "high_price": float(r["고가"]),
                    "low_price": float(r["저가"]), "close_price": close, "adj_close": close,
                    "trading_volume": int(r["거래량"])})
    return out


def fetch_nasdaq(ticker: str, end: str) -> list[dict]:
    import yfinance as yf
    df = yf.Ticker(ticker).history(start=START, end=end, auto_adjust=False, actions=False)
    if df.empty:
        return []
    has_adj = "Adj Close" in df.columns
    out = []
    for idx, r in df.iterrows():
        close = float(r["Close"])
        out.append({"ticker": ticker, "market": "NASDAQ", "trading_at": idx.date().isoformat(),
                    "interval_type": "1d", "open_price": float(r["Open"]), "high_price": float(r["High"]),
                    "low_price": float(r["Low"]), "close_price": close,
                    "adj_close": float(r["Adj Close"]) if has_adj else close,
                    "trading_volume": int(r["Volume"])})
    return out


def collect(market: str | None, end: str, force: bool) -> None:
    targets = universe(market)
    print(f"대상 {len(targets)}종목 | {START} ~ {end}")
    failed = []
    for i, (ticker, mkt) in enumerate(targets, 1):
        path = RAW / f"{ticker}.csv"
        if path.exists() and not force:
            print(f"  [{i:3}/{len(targets)}] {ticker:8} skip (이미 있음)")
            continue
        for attempt in (1, 2, 3):
            try:
                rows = fetch_kospi(ticker, end) if mkt == "KOSPI" else fetch_nasdaq(ticker, end)
                write_rows(ticker, rows)
                span = f"{rows[0]['trading_at']}~{rows[-1]['trading_at']}" if rows else "(빈 결과)"
                print(f"  [{i:3}/{len(targets)}] {ticker:8} {mkt:7} {len(rows):>5}행  {span}")
                if not rows:
                    failed.append((ticker, mkt, "empty"))
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  [{i:3}/{len(targets)}] {ticker:8} {mkt:7} FAIL {type(e).__name__}: {e}")
                    failed.append((ticker, mkt, f"{type(e).__name__}: {e}"))
                else:
                    time.sleep(2 * attempt)
        time.sleep(0.3)  # be polite to KRX / Yahoo
    if failed:
        print(f"\n실패 {len(failed)}종목:")
        for t, m, why in failed:
            print(f"  {t} ({m}) {why}")


def combine() -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    files = sorted(RAW.glob("*.csv"))
    if not files:
        raise SystemExit("data/prices_raw/ 가 비어 있습니다. 먼저 수집하세요.")
    cols: dict[str, list] = {k: [] for k in FIELDS}
    per_ticker = {}
    for p in files:
        with p.open(encoding="utf-8-sig", newline="") as f:
            n = 0
            for r in csv.DictReader(f):
                for k in FIELDS:
                    v = r[k]
                    if k in ("open_price", "high_price", "low_price", "close_price", "adj_close"):
                        v = float(v)
                    elif k == "trading_volume":
                        v = int(v)
                    elif k == "trading_at":
                        v = date.fromisoformat(v)
                    cols[k].append(v)
                n += 1
        per_ticker[p.stem] = n
    schema = pa.schema([
        ("ticker", pa.string()), ("market", pa.string()), ("trading_at", pa.date32()),
        ("interval_type", pa.string()), ("open_price", pa.float64()), ("high_price", pa.float64()),
        ("low_price", pa.float64()), ("close_price", pa.float64()), ("adj_close", pa.float64()),
        ("trading_volume", pa.int64()),
    ])
    table = pa.Table.from_pydict(cols, schema=schema)
    pq.write_table(table, OUT, compression="snappy")
    empty = [t for t, n in per_ticker.items() if n == 0]
    print(f"\n{OUT}  {table.num_rows:,}행 / {len(per_ticker)}종목 / {OUT.stat().st_size/1e6:.1f}MB")
    print(f"  기간 {min(cols['trading_at'])} ~ {max(cols['trading_at'])}")
    if empty:
        print(f"  데이터 0행인 종목 {len(empty)}개: {', '.join(empty)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=("KOSPI", "NASDAQ"))
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--force", action="store_true", help="이미 받은 종목도 다시 받기")
    ap.add_argument("--combine", action="store_true", help="수집 생략, parquet만 다시 만들기")
    args = ap.parse_args()
    if not args.combine:
        collect(args.market, args.end, args.force)
    combine()


if __name__ == "__main__":
    main()
