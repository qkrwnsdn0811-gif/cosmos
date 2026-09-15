"""Download KOSPI and NASDAQ Composite price indexes for Story 3 labels.

Yahoo Finance distributes ^KS11/^IXIC. These are actual index levels rather
than the 202-company equal-weight proxy; they are price indexes, not total
return indexes. Stock labels still use adjusted closes. Preserve this mismatch
in documentation instead of claiming dividend-neutral event attribution.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import yfinance as yf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-09-12", help="exclusive")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    entries = {}
    for market, symbol in (("KOSPI", "^KS11"), ("NASDAQ", "^IXIC")):
        path = args.out / f"index_{market}.csv"
        if path.exists():
            raise FileExistsError(f"Preserve existing benchmark: {path}; choose a fresh output directory")
        frame = yf.Ticker(symbol).history(start=args.start, end=args.end, auto_adjust=False, actions=False)
        if frame.empty or frame.Close.isna().any() or (frame.Close <= 0).any() or frame.index.has_duplicates:
            raise ValueError(f"Invalid {market} index data")
        frame.to_csv(path)
        entries[market] = {"symbol": symbol, "rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "first": str(frame.index.min()), "last": str(frame.index.max())}
    manifest = {"source": "Yahoo Finance price-index Close", "yfinance": yf.__version__,
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "start": args.start,
                "end_exclusive": args.end, "markets": entries}
    (args.out / "index_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
