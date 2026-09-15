"""News-conditioned labels and point-in-time graphs for the original Story 3.

This is separate from the source-only U3 experiment. No realized price return
is an input feature. Labels are 1/3 SESSION log returns of adj_close minus
KOSPI/NASDAQ Composite log returns when --index-dir is supplied (v2). The
original equal-weight leave-target-out proxy remains available for v1
reproduction only. No industry residualization or missing-index imputation.

Prepared news has only a date. Conservative availability is end-of-day Seoul
for domestic, end-of-day UTC-12 for overseas (unknown publisher timezone).
The first observed market close AFTER availability is the anchor, followed by
1/3 closes. This delayed close-to-close label deliberately excludes the anchor
session, and cannot measure immediate/intraday news effects. NASDAQ DST is
handled with America/New_York. Date-only cutoffs do not intersect early closes.

Annual graph snapshots strictly precede the article: past relation sentences,
past sampled co-mentions and PRIOR-year price correlations. Historical DART
holdings use the latest then-known accounting period and receipt/amendment.
Existing full-period
scored relations/correlations are NEVER used. Ownership publication dates and
sector observation dates are respected. A current industry is masked in the
past. Sparse graph candidates are shared by EVERY baseline/model; LightGBM gets
no path, relation, neighbor, or graph-derived numerical feature.
"""
from __future__ import annotations

import collections
import hashlib
import itertools
import json
import re
from functools import lru_cache
from datetime import timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from news_content import sanitize_article

HERE = Path(__file__).resolve().parent
OUT = HERE / "artifacts/news_impact"
TYPES = ["PARTNER", "INVEST", "COMPETE", "SUPPLY", "ownership", "sector", "co_mention", "correlation"]
POS = {"title": 0, "lead": 1, "body": 2}
SPLITS = {"train": ("2017-01-01", "2022-12-31"), "val": ("2023-01-01", "2023-12-31"),
          "test": ("2024-01-01", "2026-09-11")}


def availability(value: str, region: str) -> pd.Timestamp:
    if len(value) == 10:
        zone = "Asia/Seoul" if region == "domestic" else timezone(timedelta(hours=-12))
        return (pd.Timestamp(value).tz_localize(zone) + pd.Timedelta(days=1)).tz_convert("UTC")
    ts = pd.Timestamp(value)
    if ts.tz is None:
        raise ValueError("published_at requires an explicit timezone or a YYYY-MM-DD date")
    return ts.tz_convert("UTC")


def split_for(date: str) -> str | None:
    return next((s for s, (a, b) in SPLITS.items() if a <= date[:10] <= b), None)


def companies():
    return pd.read_csv(HERE.parent / "ner/data/company_industry.csv", dtype=str).sort_values("ticker").reset_index(drop=True)


def prices():
    p = pd.read_parquet(HERE / "data/prices_daily.parquet")
    p["date"] = pd.to_datetime(p.trading_at).dt.tz_localize(None).dt.normalize()
    return p.pivot(index="date", columns="ticker", values="adj_close").sort_index()


@lru_cache(maxsize=8)
def _calendar(market, first_year, last_year):
    import exchange_calendars as xc
    return xc.get_calendar("XKRX" if market == "KOSPI" else "XNAS",
                           start=f"{first_year}-01-01", end=f"{last_year}-12-31")


def close_times(dates, market):
    # Calendar includes DST, US early closes and historical KRX close-time
    # changes. Unknown/non-session price rows must fail, not invent a close.
    dates = pd.DatetimeIndex(dates)
    calendar = _calendar(market, dates.min().year, dates.max().year)
    closes = calendar.schedule["close"].reindex(dates)
    if closes.isna().any():
        raise ValueError(f"price rows outside {market} calendar: {dates[closes.isna()].tolist()[:5]}")
    return pd.DatetimeIndex(closes)


class Labels:
    def __init__(self, price, meta, benchmarks=None):
        self.meta = meta.set_index("ticker")
        self.panels = {}
        for market, group in meta.groupby("market"):
            p = price.reindex(columns=group.ticker).dropna(how="all")
            r = np.log(p).diff()  # no forward fill across missing target prices
            if benchmarks is None:
                count, total = r.count(axis=1), r.sum(axis=1)
                residual = r - r.rsub(total, axis=0).div(r.notna().rsub(count, axis=0), axis=0)
            else:
                index = benchmarks[market].reindex(p.index)
                if index.notna().sum() < 2 or (index <= 0).any():
                    raise ValueError(f"missing/nonpositive {market} benchmark series")
                # Preserve the market calendar. A missing benchmark invalidates
                # returns on that date AND the next date; label() excludes the
                # affected windows. Never fill levels or skip the missing session.
                residual = r.sub(np.log(index).diff(), axis=0)
            self.panels[market] = (p, residual, close_times(p.index, market))

    def label(self, ticker, published, split):
        market = self.meta.loc[ticker, "market"]
        p, residual, closes = self.panels[market]
        anchor = int(closes.searchsorted(published, side="right"))
        if anchor + 3 >= len(p) or not np.isfinite(p[ticker].iloc[anchor]):
            return None
        end = p.index[anchor + 3].strftime("%Y-%m-%d")
        if end > SPLITS[split][1]:
            return None  # purge labels crossing a split boundary (also end of data)
        r = residual[ticker].iloc[anchor + 1:anchor + 4].to_numpy()
        if not np.isfinite(r).all():
            return None
        return {"y_1d": float(r[0]), "y_3d": float(r.sum()), "anchor_date": p.index[anchor].strftime("%Y-%m-%d"),
                "label_end": end, "market": market}


def read_news(path):
    rows, seen, counts = [], set(), collections.Counter()
    for line in Path(path).open(encoding="utf8"):
        original = json.loads(line)
        r = sanitize_article(original)
        if r["title"] != (original.get("title") or "") or r["body"] != (original.get("body") or ""):
            counts["text_sanitized"] += 1
        # Representative-body rule plus a second exact normalized-text check
        # across splits; keep the earliest article, never a later duplicate.
        r["text_hash"] = hashlib.sha256(" ".join((r.get("body") or r.get("title") or "").split()).encode()).hexdigest()
        rows.append(r)
    result = []
    for r in sorted(rows, key=lambda r: (r["published_date"], r["record_id"])):
        if r["text_hash"] in seen:
            counts["duplicate_text"] += 1
            continue
        seen.add(r["text_hash"])
        if not r.get("companies") or len(r["companies"]) > 20:
            counts["no_company_or_broad_listing_over20"] += 1
            continue
        if not (r.get("title") or r.get("body")):
            counts["empty_text"] += 1
            continue
        result.append(r)
    return result, dict(counts)


def historical_holdings(history, cutoff):
    latest = {}
    def order(r):
        # A late amendment of an OLD accounting period does not erase a newer
        # period. Report title supplies period even when table parsing failed.
        period = re.search(r"\((\d{4})\.(\d{2})\)", r["report_name"])
        accounting = f"{period[1]}-{period[2]}" if period else (r.get("as_of_date") or "")[:7]
        return accounting, r["published_date"], r["report_id"]
    for r in history:
        if r["published_date"] >= cutoff:
            continue
        old = latest.get(r["filer"])
        if old is None or order(r) > order(old):
            latest[r["filer"]] = r
    return latest


def make_graph(cutoff, docs, meta, price, ownership_history=None):
    """Build a directed, typed, sparse graph using evidence BEFORE cutoff.

    Supply edges are reversed as separate messages with a reverse flag. They
    remain supply connections, never relabeled as the inverse economic claim.
    Sample co-mention strength is count/(count+5), NOT a population PMI estimate.
    Correlation estimates use >=60 overlapping PRIOR-year sessions, same market.
    Cross-market contemporaneous price correlation is intentionally excluded.
    """
    tickers = meta.ticker.tolist()
    valid = set(tickers)
    edges = []

    def add(a, b, kind, weight, sign, evidence, reverse=False):
        if a not in valid or b not in valid or a == b:
            return
        edges.append({"src": a, "dst": b, "type": kind, "weight": float(weight), "sign": float(sign),
                      "reverse": reverse, "evidence": evidence[:3]})

    rels = collections.defaultdict(list)
    for line in (HERE / "data/rel_hits.jsonl").open(encoding="utf8"):
        r = json.loads(line)
        if str(r.get("published_date", "9999"))[:10] < cutoff:
            rels[r["src_ticker"], r["dst_ticker"], r["rel_type"]].append(r)
    for (a, b, kind), rs in rels.items():
        w = float(np.mean([r["confidence"] for r in rs]))
        ev = [{"id": r["record_id"], "date": r["published_date"], "text": r["sentence"]} for r in rs]
        # This polarity is ONLY an explicit Stage1 heuristic; not a causal label.
        sign = -1 if kind == "COMPETE" else 1
        add(a, b, kind, w, sign, ev)
        add(b, a, kind, w, sign, ev, True)

    pairs = collections.defaultdict(list)
    for r in docs:
        if r["published_date"][:10] >= cutoff:
            continue
        cs = sorted({c["ticker"] for c in r["companies"] if c["ticker"] in valid})
        for a, b in itertools.combinations(cs, 2):
            pairs[a, b].append({"id": r["record_id"], "date": r["published_date"], "text": r["title"]})
    co = collections.defaultdict(list)
    for (a, b), ev in pairs.items():
        if len(ev) >= 3:
            w = len(ev) / (len(ev) + 5)
            co[a].append((w, b, ev)); co[b].append((w, a, ev))
    for a, neighbors in co.items():
        for w, b, ev in sorted(neighbors, key=lambda x: (-x[0], x[1]))[:4]:
            add(a, b, "co_mention", w, 1, ev)

    since = str(int(cutoff[:4]) - 1) + "-01-01"
    historical = np.log(price.loc[(price.index >= since) & (price.index < cutoff)]).diff()
    for market, group in meta.groupby("market"):
        corr = historical.reindex(columns=group.ticker).corr(min_periods=60)
        for a in corr:
            s = corr[a].drop(index=a).dropna()
            for b in s.abs().sort_values(ascending=False).head(4).index:
                if abs(s[b]) >= 0.05:
                    add(a, b, "correlation", abs(s[b]), np.sign(s[b]), [{
                        "id": f"prices:{since}:{cutoff}:{a}:{b}", "date": since,
                        "text": f"{a}/{b}: prior-year adjusted-return correlation {s[b]:.3f}; observations strictly before {cutoff}"}])

    if ownership_history is not None:
        # Latest available report per filer supersedes earlier holdings, including
        # an empty/failed table. Never merge stakes from different reporting years.
        latest = historical_holdings(ownership_history, cutoff)
        for r in latest.values():
            if not r["parsed"]:
                continue
            for h in r["positions"]:
                ev = [{"id": "dart:" + r["report_id"], "date": r["published_date"],
                       "text": f"{h['holder_name']}({h['src']}) → {h['dst']}: {r['as_of_date']} 기준 보통주 지분 {h['share_pct']}%; {r['report_name']}"}]
                add(h["src"], h["dst"], "ownership", h["weight"], 1, ev)
                add(h["dst"], h["src"], "ownership", h["weight"], 1, ev, True)

    for kind in (("sector",) if ownership_history is not None else ("ownership", "sector")):
        frame = pd.read_csv(HERE / f"data/edges_{kind}.csv", dtype=str)
        for r in frame.to_dict("records"):
            observed = r["as_of_date"] if pd.notna(r["as_of_date"]) else ""
            if kind == "ownership" and r["evidence"].startswith("dart:"):
                report = r["evidence"][5:13]
                observed = max(observed, f"{report[:4]}-{report[4:6]}-{report[6:8]}")
            if not observed or observed >= cutoff:
                continue
            a, b = r["src_ticker"], r["dst_ticker"]
            ev = [{"id": r["evidence"].split(";")[0], "date": observed, "text": r["evidence"]}]
            add(a, b, kind, float(r["weight"]), 1, ev)
            add(b, a, kind, float(r["weight"]), 1, ev, True)
    # Exactly one edge per directed pair/type/reverse flag.
    edges = list({(e["src"], e["dst"], e["type"], e["reverse"]): e for e in edges}.values())
    # Previous-day overseas dates may not be available until cutoff at 12 UTC.
    # Date-only training articles are available at/after 15 UTC (domestic) or
    # next-day 12 UTC (overseas), so this conservative snapshot time precedes them.
    return {"as_of": cutoff, "available_at": cutoff + "T12:00:00+00:00", "edges": edges,
            "industry_available": cutoff > "2026-09-09"}


def node_features(meta, graph):
    industries = sorted(meta.industry_id.unique())
    x = np.zeros((len(meta), 3 + len(industries)), dtype="float32")
    x[:, 0] = (meta.market == "KOSPI").to_numpy()
    x[:, 1] = (meta.market == "NASDAQ").to_numpy()
    if graph["industry_available"]:
        x[:, 2] = 1
        for i, industry in enumerate(meta.industry_id):
            x[i, 3 + industries.index(industry)] = 1
    return x


def graph_arrays(graph, tickers):
    idx = {t: i for i, t in enumerate(tickers)}
    edges = graph["edges"]
    ei = np.asarray([[idx[e["src"]], idx[e["dst"]]] for e in edges], dtype="int64").reshape(-1, 2).T
    ea = np.zeros((len(edges), len(TYPES) + 3), dtype="float32")
    for i, e in enumerate(edges):
        ea[i, TYPES.index(e["type"])] = 1
        ea[i, -3:] = e["weight"], e["sign"], e["reverse"]
    return ei, ea


def expand(mentions, graph, cap=32):
    direct = {m["ticker"]: m for m in mentions}
    paths = {t: {"path": [], "weight": 1.0, "sign": 1.0, "source": t} for t in direct}
    adjacency = collections.defaultdict(list)
    for e in graph["edges"]:
        adjacency[e["src"]].append(e)
    for _ in range(2):
        for a, prev in list(paths.items()):
            if len(prev["path"]) >= 2:
                continue
            for e in adjacency[a]:
                b = e["dst"]
                if b in direct or any(p["src"] == b for p in prev["path"]):
                    continue
                w = prev["weight"] * e["weight"] * 0.8
                if b not in paths or w > paths[b]["weight"]:
                    paths[b] = {"path": prev["path"] + [e], "weight": w,
                                "sign": prev["sign"] * e["sign"], "source": prev["source"]}
    chosen = sorted(paths, key=lambda t: (t not in direct, -paths[t]["weight"], t))[:max(cap, len(direct))]
    return {t: paths[t] for t in chosen}


def mention_array(mentions, tickers):
    x = np.zeros((len(tickers), 5), dtype="float32")
    idx = {t: i for i, t in enumerate(tickers)}
    for m in mentions:
        if m["ticker"] in idx:
            i = idx[m["ticker"]]
            x[i, 0] = 1
            x[i, 1] = np.log1p(m["n_mentions"]) / 5
            x[i, 2 + POS.get(m["first_pos"], 2)] = 1
    return x


def build_dataset(path, out=OUT, benchmarks=None, ownership_history=None):
    out.mkdir(parents=True, exist_ok=True)
    (out / "aliases.csv").write_bytes((HERE.parent / "ner/data/aliases.csv").read_bytes())
    docs, dropped = read_news(path)
    meta, p = companies(), prices()
    labels = Labels(p, meta, benchmarks)
    tickers = set(meta.ticker)
    graphs = {str(y): make_graph(f"{y}-01-01", docs, meta, p, ownership_history) for y in range(2017, 2027)}
    graphs["serving"] = make_graph("2026-09-14", docs, meta, p, ownership_history)
    (out / "graphs.json").write_text(json.dumps(graphs, ensure_ascii=False), encoding="utf8")
    records, rows = [], []
    for r in docs:
        split = split_for(r["published_date"])
        if split is None:
            continue
        ms = [m for m in r["companies"] if m["ticker"] in tickers]
        if not ms:
            continue
        graph_key = r["published_date"][:4]
        available = availability(r["published_date"], r["region"])
        targets = expand(ms, graphs[graph_key])
        pending = []
        for ticker, route in targets.items():
            label = labels.label(ticker, available, split)
            if label is None:
                continue
            pending.append({"article": len(records), "news_id": r["record_id"], "ticker": ticker,
                "split": split, "graph": graph_key, "is_direct": ticker in {m["ticker"] for m in ms},
                "path_length": len(route["path"]), "rule_weight": route["weight"], "rule_sign": route["sign"],
                "rule_source": route["source"], **label})
        if pending:
            r["companies"] = ms
            r["split"] = split
            r["graph"] = graph_key
            r["available_at"] = available.isoformat()
            records.append(r)
            rows.extend(pending)
    frame = pd.DataFrame(rows)
    frame.to_parquet(out / "pairs.parquet", index=False)
    with (out / "articles.jsonl").open("w", encoding="utf8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta.to_json(out / "companies.json", orient="records", force_ascii=False)
    audit = {"articles": len(records), "pairs": len(rows), "drop_counts": dropped,
        "splits": frame.groupby(["split", "market"]).size().to_dict(),
        "graph_edges": {k: len(g["edges"]) for k, g in graphs.items()},
        "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "label": "delayed 1/3-session " + ("KOSPI/NASDAQ Composite index" if benchmarks is not None else "market-proxy") + " adjusted log return after first available close",
        "neutral_threshold": "train-only abs-return 33rd percentile per horizon",
        "strength_scale": "train-only abs-return 95th percentile per horizon"}
    if benchmarks is not None:
        audit["missing_index_sessions"] = {market: [d.strftime("%Y-%m-%d") for d in panel[0].index
            if pd.isna(benchmarks[market].get(d))] for market, panel in labels.panels.items()}
    audit["splits"] = {"/".join(k): int(v) for k, v in audit["splits"].items()}
    (out / "dataset_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf8")
    print(json.dumps(audit, indent=2), flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--news", type=Path, default=OUT / "raw_news.jsonl")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--index-dir", type=Path)
    ap.add_argument("--ownership-history", type=Path)
    args = ap.parse_args()
    benchmarks = None
    if args.index_dir:
        benchmarks = {}
        for market in ("KOSPI", "NASDAQ"):
            f = pd.read_csv(args.index_dir / f"index_{market}.csv")
            series = pd.Series(f.Close.to_numpy(), index=pd.to_datetime(f.Date.str[:10]))
            if series.index.has_duplicates or not series.index.is_monotonic_increasing:
                raise ValueError("benchmark dates must be unique and ordered")
            benchmarks[market] = series
    history = [json.loads(l) for l in args.ownership_history.open(encoding="utf8")] if args.ownership_history else None
    build_dataset(args.news, args.out, benchmarks, history)
