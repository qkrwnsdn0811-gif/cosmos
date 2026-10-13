"""NEXUS slice pipeline: build-master → collect → analyze → migrate → load."""
from __future__ import annotations

import argparse
import csv
import pickle
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .bigkinds.client import BigKindsClient
from .bigkinds.search import pair_hilights, search_documents
from .config import REPO_ROOT, load_settings
from .entities.master import ALIAS_COLUMNS, alias_rows, build_master
from .entities.recognize import Recognizer
from .impact.abnormal import abnormal_returns, event_trading_date
from .impact.prices import BENCHMARK, PriceCache
from .ingest.normalize import to_article
from .ingest.store import ArticleStore
from .models import Article, Company, Edge, EventBundle, EvidenceSnippet
from .propagation.propagate import propagate
from .relations.comention import sentence_comentions
from .relations.evidence import extract_evidence
from .relations.graph import BaseGraph, comention_edges, load_cosmos_edges, news_edges

KST = timezone(timedelta(hours=9))
CHAINS = {
    "semis": {"value_chain": "SEMICONDUCTOR", "category": ["경제>반도체"],
              "seed": Path(__file__).parent / "entities" / "data" / "semis_seed.csv"},
}
COSMOS_COMPANIES = REPO_ROOT / "AI" / "ner" / "data" / "companies.csv"
COSMOS_ALIASES = REPO_ROOT / "AI" / "ner" / "data" / "aliases.csv"
COSMOS_RELATIONS = REPO_ROOT / "AI" / "graph" / "data" / "db_full_20260922" / "company_relationship.csv"
HANGUL = re.compile(r"[가-힣]")
PRICE_LOOKBACK = timedelta(days=14)


def _read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _search_name(company: Company, recognizer: Recognizer) -> str:
    """Korean articles name foreign firms in Hangul, so NASDAQ pairs search by the longest Hangul alias."""
    if company.market == "NASDAQ":
        hangul = [a for a in recognizer.aliases_of(company.key) if HANGUL.search(a)]
        if hangul:
            return hangul[0]
    return company.name


def build_graph(articles: list[Article], recognizer: Recognizer, companies: dict[str, Company],
                client: BigKindsClient, *, start: date, end: date, today: date, cosmos_csv: Path,
                top_pairs: int = 300) -> tuple[BaseGraph, list[Edge], list[EvidenceSnippet]]:
    stats = sentence_comentions(articles, recognizer.key_of_org, min_count=3)
    snippets: list[EvidenceSnippet] = []
    for i, stat in enumerate(stats[:top_pairs], 1):
        a, b = companies[stat.a], companies[stat.b]
        docs = pair_hilights(client, _search_name(a, recognizer), _search_name(b, recognizer),
                             start=start, end=end, today=today)
        snippets.extend(extract_evidence(docs, a, b, recognizer.aliases_of))
        if i % 50 == 0:
            print(f"  pair hilights {i}/{min(top_pairs, len(stats))}, snippets {len(snippets)}", flush=True)
    edges = news_edges(snippets) + comention_edges(stats) + load_cosmos_edges(cosmos_csv, set(companies))
    return BaseGraph(edges), edges, snippets


def run_analyze(articles: list[Article], recognizer: Recognizer, companies: dict[str, Company],
                graph: BaseGraph, prices: PriceCache, *, value_chain: str) -> list[EventBundle]:
    if not articles:
        return []
    start = min(a.dateline.astimezone(KST).date() for a in articles) - PRICE_LOOKBACK
    end = max(a.dateline.astimezone(KST).date() for a in articles) + PRICE_LOOKBACK
    index_closes = {market: prices.closes(symbol, start, end) for market, symbol in BENCHMARK.items()}
    trading_days = sorted(index_closes["KOSPI"].index)
    stock_closes: dict[str, object] = {}
    ar_cache: dict[tuple[str, date], list] = {}

    def returns_for(key: str, t0: date) -> list:
        if (key, t0) not in ar_cache:
            company = companies.get(key)
            if not company or company.market not in BENCHMARK:
                ar_cache[(key, t0)] = []
            else:
                if company.ticker not in stock_closes:
                    stock_closes[company.ticker] = prices.closes(company.ticker, start, end)
                ar_cache[(key, t0)] = abnormal_returns(key, t0, stock_closes[company.ticker],
                                                       index_closes[company.market], company.market)
        return ar_cache[(key, t0)]

    bundles = []
    for article in articles:
        mentions = [m for m in recognizer.recognize(article)
                    if m.key in companies and companies[m.key].value_chain == value_chain]
        if not mentions:
            continue
        impacts = propagate(mentions, graph)
        t0 = event_trading_date(article.dateline, trading_days)
        returns = [r for i in impacts for r in returns_for(i.key, t0)] if t0 else []
        bundles.append(EventBundle(article, value_chain, mentions, impacts, returns, t0))
    return bundles


# ------------------------------------------------------------------ commands

def _paths(chain: str) -> dict[str, Path]:
    data = load_settings().data_dir
    return {"data": data, "master": data / "master", "articles": data / "articles",
            "analyzed": data / "analyzed" / chain, "cache": data / "cache" / "bigkinds", "prices": data / "prices",
            "companies_csv": data / "master" / f"{chain}_companies.csv",
            "aliases_csv": data / "master" / f"{chain}_aliases.csv"}


def _load_master(chain: str) -> tuple[dict[str, Company], Recognizer]:
    p = _paths(chain)
    companies = [Company(r["key"], r["market"], r["ticker"], r["name"], r["value_chain"], r["role"])
                 for r in _read_csv(p["companies_csv"])]
    return {c.key: c for c in companies}, Recognizer(companies, _read_csv(p["aliases_csv"]))


def _client() -> BigKindsClient:
    settings = load_settings()
    if not settings.bigkinds_key:
        sys.exit("BIGKINDS_ACCESS_KEY is missing (repo-root .env)")
    return BigKindsClient(settings.bigkinds_key, settings.data_dir / "cache" / "bigkinds")


def cmd_build_master(args) -> int:
    import FinanceDataReader as fdr
    cfg, p = CHAINS[args.chain], _paths(args.chain)
    seed = _read_csv(cfg["seed"])
    companies, unresolved = build_master(seed, fdr.StockListing("KRX"), _read_csv(COSMOS_COMPANIES), cfg["value_chain"])
    _write_csv(p["companies_csv"], [c.__dict__ for c in companies],
               ["key", "market", "ticker", "name", "value_chain", "role"])
    _write_csv(p["aliases_csv"], alias_rows(companies, seed, _read_csv(COSMOS_ALIASES)), ALIAS_COLUMNS)
    print(f"companies: {len(companies)} ({Counter(c.market for c in companies)})")
    if unresolved:
        print("unresolved seed names:", ", ".join(unresolved))
        return 1
    return 0


def _months(start: date, end: date):
    """Month windows from the month containing `start`. Each partition is rewritten whole, so windows always
    begin on the 1st; a mid-month --start would otherwise replace the partition with a partial month."""
    cur = start.replace(day=1)
    while cur < end:
        nxt = (cur + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt, end), cur.strftime("%Y-%m")
        cur = nxt


def cmd_collect(args) -> int:
    cfg, p = CHAINS[args.chain], _paths(args.chain)
    client, store = _client(), ArticleStore(p["articles"])
    today = datetime.now(KST).date()
    started, total = time.monotonic(), 0
    for m_start, m_end, label in _months(args.start, args.end):
        docs = search_documents(client, start=m_start, end=m_end, category=cfg["category"], today=today)
        n = store.write((to_article(d) for d in docs), partition=f"{args.chain}-{label}")
        total += n
        print(f"{label}: {n:,} articles (total {total:,}, {time.monotonic() - started:,.0f}s)", flush=True)
    return 0


def cmd_analyze(args) -> int:
    p = _paths(args.chain)
    companies, recognizer = _load_master(args.chain)
    store = ArticleStore(p["articles"])
    articles = store.read([x for x in store.partitions() if x.startswith(f"{args.chain}-")])
    if not articles:
        sys.exit("no articles collected")
    today = datetime.now(KST).date()
    first = min(a.published_date for a in articles)
    last = max(a.published_date for a in articles) + timedelta(days=1)
    print(f"articles: {len(articles):,}", flush=True)
    # Evidence searches end today (exclusive) so they are cacheable; reruns then cost no API calls.
    graph, edges, snippets = build_graph(articles, recognizer, companies, _client(), start=first,
                                         end=min(last, today), today=today, cosmos_csv=COSMOS_RELATIONS)
    print(f"edges: {Counter(e.source for e in edges)}, snippets: {len(snippets)}", flush=True)
    prices = PriceCache(p["prices"])
    bundles = run_analyze(articles, recognizer, companies, graph, prices, value_chain=CHAINS[args.chain]["value_chain"])
    bars = {c.key: prices.bars(c.ticker, first - PRICE_LOOKBACK, today)
            for c in companies.values() if c.market in BENCHMARK}
    p["analyzed"].mkdir(parents=True, exist_ok=True)
    for name, obj in (("events", bundles), ("relations", (edges, snippets)), ("prices", bars)):
        with (p["analyzed"] / f"{name}.pkl").open("wb") as f:
            pickle.dump(obj, f)
    impacts = Counter((i.basis, i.status) for b in bundles for i in b.impacts)
    print(f"events: {len(bundles):,}, impacts: {dict(impacts)}, returns: {sum(len(b.returns) for b in bundles):,}")
    return 0


def _jdbc(url: str) -> tuple[str, str, str]:
    u = urlparse(url)
    host = "host.docker.internal" if u.hostname in ("localhost", "127.0.0.1") else u.hostname
    return f"jdbc:postgresql://{host}:{u.port or 5432}{u.path}", u.username or "", u.password or ""


def cmd_migrate(args) -> int:
    from .load.postgres import MIGRATIONS_DIR
    jdbc, user, password = _jdbc(args.database_url)
    cmd = ["docker", "run", "--rm", "-v", f"{MIGRATIONS_DIR.as_posix()}:/flyway/sql", "flyway/flyway:11",
           f"-url={jdbc}", f"-user={user}", f"-password={password}", "migrate"]
    return subprocess.call(cmd)


def cmd_load(args) -> int:
    # The .pkl files are written by `analyze` into the git-ignored local nexus/data directory and are
    # never received from outside, so loading them with pickle does not cross a trust boundary.
    import psycopg
    from .load.postgres import (ensure_companies, ensure_relationship_types, load_events, load_prices,
                                load_relations, prune_events)
    p = _paths(args.chain)
    companies, _ = _load_master(args.chain)
    with (p["analyzed"] / "events.pkl").open("rb") as f:
        bundles = pickle.load(f)
    with (p["analyzed"] / "relations.pkl").open("rb") as f:
        edges, snippets = pickle.load(f)
    with (p["analyzed"] / "prices.pkl").open("rb") as f:
        bars = pickle.load(f)
    with psycopg.connect(args.database_url) as conn:
        ensure_relationship_types(conn)
        conn.commit()
        ids = ensure_companies(conn, list(companies.values()))
        print(f"relations written: {load_relations(conn, edges, snippets, ids)}", flush=True)
        conn.commit()
        for i in range(0, len(bundles), args.batch):
            load_events(conn, bundles[i:i + args.batch], ids)
            conn.commit()
            print(f"events {min(i + args.batch, len(bundles)):,}/{len(bundles):,}", flush=True)
        pruned = prune_events(conn, CHAINS[args.chain]["value_chain"], {b.article.news_id for b in bundles})
        conn.commit()
        print(f"stale events removed: {pruned}", flush=True)
        print(f"price rows written: {load_prices(conn, ids, bars)}")
        conn.commit()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nexus")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build-master", "collect", "analyze", "load"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--chain", choices=sorted(CHAINS), required=True)
        if name == "collect":
            cmd.add_argument("--start", type=date.fromisoformat, required=True)
            cmd.add_argument("--end", type=date.fromisoformat, required=True)
        if name == "load":
            cmd.add_argument("--database-url", required=True)
            cmd.add_argument("--batch", type=int, default=2000)
    sub.add_parser("migrate").add_argument("--database-url", required=True)
    args = parser.parse_args(argv)
    handler = {"build-master": cmd_build_master, "collect": cmd_collect, "analyze": cmd_analyze,
               "migrate": cmd_migrate, "load": cmd_load}[args.command]
    return handler(args)
