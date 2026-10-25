"""NEXUS slice pipeline: build-master → collect → analyze → migrate → load → sentiment → train-impact → predict-impact,
publish-graph (BigKinds galaxy snapshot over every analyzed value chain) and cases (investor view: news types, past-case reactions, labels)."""
from __future__ import annotations

import argparse
import csv
import pickle
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse

from .bigkinds.client import BigKindsClient, BigKindsError, CacheOnlyClient
from .bigkinds.search import pair_hilights, relation_hilights, search_documents
from .config import REPO_ROOT, load_settings
from .entities.master import ALIAS_COLUMNS, alias_rows, build_master
from .entities.recognize import Recognizer
from .impact.abnormal import abnormal_returns, event_trading_date
from .impact.features import MentionTimeline, build_frame, category_vocab
from .impact.labels import build_price_contexts
from .impact.prices import BENCHMARK, PriceCache
from .ingest.normalize import to_article
from .ingest.store import ArticleStore
from .models import Article, Company, Edge, EventBundle, EvidenceSnippet
from .propagation.propagate import propagate
from .relations.activation import active_relations
from .relations.comention import sentence_comentions
from .relations.evidence import extract_evidence
from .relations.fragments import pair_fragments
from .relations.graph import BaseGraph, comention_edges, load_cosmos_edges, news_edges

KST = timezone(timedelta(hours=9))
CHAINS = {
    "semis": {"value_chain": "SEMICONDUCTOR", "label": "반도체", "category": ["경제>반도체"],
              "markets": ("KOSPI", "KOSDAQ"),            # domestic listings only (2026-10-18)
              "seed": Path(__file__).parent / "entities" / "data" / "semis_seed.csv"},
}
COSMOS_COMPANIES = REPO_ROOT / "AI" / "ner" / "data" / "companies.csv"
COSMOS_ALIASES = REPO_ROOT / "AI" / "ner" / "data" / "aliases.csv"
COSMOS_RELATIONS = REPO_ROOT / "AI" / "graph" / "data" / "db_full_20260922" / "company_relationship.csv"
PRICE_LOOKBACK = timedelta(days=14)
RELATION_RETRIES = 2              # BigKinds sometimes rejects a valid query once (seen 2026-10-18) — retry, then skip
RELATION_RETRY_WAIT = 5.0
PRICE_HISTORY = timedelta(days=150)       # 60 trading days of sigma before the earliest event
IMPACT_REPORT = REPO_ROOT / "docs" / "nexus" / "impact-model-report.md"


def _read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def search_terms(articles: list[Article], recognizer: Recognizer, companies: dict[str, Company]) -> dict[str, str]:
    """Per company, the alias that BigKinds' own entity lists use most often (e.g. 마이크론, not the rare
    transliteration 마이크론 테크놀로지); falls back to the official name."""
    seen: dict[str, Counter] = {}
    for article in articles:
        for org in article.orgs:
            key = recognizer.key_of_org(org)
            if key:
                seen.setdefault(key, Counter())[org] += 1
    return {key: (seen[key].most_common(1)[0][0] if key in seen else c.name) for key, c in companies.items()}


def candidate_pairs(articles: list[Article], recognizer: Recognizer, companies: dict[str, Company]) -> list[tuple[str, str]]:
    """Company pairs mentioned together in at least one article, most frequent first (keys sorted in each pair)."""
    counts: Counter = Counter()
    for article in articles:
        keys = sorted({m.key for m in recognizer.recognize(article) if m.key in companies})
        counts.update(combinations(keys, 2))
    return [pair for pair, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _relation_docs(client, a_term: str, b_term: str, *, start: date, end: date, today: date) -> list[dict] | None:
    """Relation-word search for one pair; a pair the API keeps rejecting is skipped instead of aborting analyze."""
    for attempt in range(RELATION_RETRIES + 1):
        try:
            return relation_hilights(client, a_term, b_term, start=start, end=end, today=today)
        except BigKindsError as exc:
            if attempt == RELATION_RETRIES:
                print(f"  skipped relation search {a_term} / {b_term}: {exc}", flush=True)
                return None
            time.sleep(RELATION_RETRY_WAIT)
    return None


UNDIRECTED_RELS = {"PARTNER", "COMPETE", "CO_MENTIONED"}


def _unique_snippets(snippets: list[EvidenceSnippet]) -> list[EvidenceSnippet]:
    """The plain and the relation-word search, or the rules and the classifier, can report the same article
    fragment; count it once and keep the first (rules come first). Undirected pairs compare in sorted order."""
    seen, unique = set(), []
    for s in snippets:
        src, dst = sorted((s.src, s.dst)) if s.rel in UNDIRECTED_RELS else (s.src, s.dst)
        key = (s.news_id, src, dst, s.rel, s.snippet)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def build_graph(articles: list[Article], recognizer: Recognizer, companies: dict[str, Company],
                client: BigKindsClient, *, start: date, end: date, today: date, cosmos_csv: Path,
                top_pairs: int = 300, predictor=None) -> tuple[BaseGraph, list[Edge], list[EvidenceSnippet]]:
    stats = sentence_comentions(articles, recognizer.key_of_org, min_count=3)
    terms = search_terms(articles, recognizer, companies)
    snippets: list[EvidenceSnippet] = []
    fragments = []
    for i, stat in enumerate(stats[:top_pairs], 1):
        a, b = companies[stat.a], companies[stat.b]
        docs = pair_hilights(client, terms[a.key], terms[b.key], start=start, end=end, today=today)
        snippets.extend(extract_evidence(docs, a, b, recognizer.aliases_of))
        if predictor:
            fragments.extend(pair_fragments(docs, a, b, recognizer.aliases_of))
        if i % 50 == 0:
            print(f"  pair hilights {i}/{min(top_pairs, len(stats))}, snippets {len(snippets)}", flush=True)
    # Every pair that shares an article also gets a relation-word search (2026-10-18: evidence relations were
    # only 15 with the plain search over the top 300 sentence pairs).
    pairs = candidate_pairs(articles, recognizer, companies)
    skipped = 0
    for i, (key_a, key_b) in enumerate(pairs, 1):
        a, b = companies[key_a], companies[key_b]
        docs = _relation_docs(client, terms[a.key], terms[b.key], start=start, end=end, today=today)
        if docs is None:
            skipped += 1
            continue
        snippets.extend(extract_evidence(docs, a, b, recognizer.aliases_of))
        if predictor:
            fragments.extend(pair_fragments(docs, a, b, recognizer.aliases_of))
        if i % 100 == 0:
            print(f"  relation hilights {i}/{len(pairs)}, snippets {len(snippets)}, skipped {skipped}", flush=True)
    print(f"  relation hilights done: {len(pairs)} pairs, skipped {skipped}", flush=True)
    if predictor:
        from .relations.kpfbert import model_snippets
        predict, tau = predictor
        unique = list({f.fragment_id: f for f in fragments}.values())
        added = model_snippets(unique, predict(unique), tau)
        print(f"  relation model: {len(unique):,} fragments, {len(added)} accepted at tau {tau:.2f}", flush=True)
        snippets.extend(added)
    snippets = _unique_snippets(snippets)
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
        impacts = propagate(mentions, graph, active_rels=active_relations(article))
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
    companies, unresolved = build_master(seed, fdr.StockListing("KRX"), _read_csv(COSMOS_COMPANIES), cfg["value_chain"],
                                         markets=cfg["markets"])
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
    from .relations.kpfbert import load_predictor
    predictor = load_predictor(p["data"] / "relations" / "model")
    print(f"relation model: {'on (tau %.2f)' % predictor[1] if predictor else 'off (rules only)'}", flush=True)
    graph, edges, snippets = build_graph(articles, recognizer, companies, _client(), start=first,
                                         end=min(last, today), today=today, cosmos_csv=COSMOS_RELATIONS,
                                         predictor=predictor)
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
    from .impact.sentiment import read_sentiment, sentiment_labels
    sentiment = sentiment_labels(read_sentiment(p["analyzed"] / "sentiment.parquet"))
    scored = sum(b.article.news_id in sentiment for b in bundles)
    print(f"article sentiment: {scored:,} of {len(bundles):,} events scored "
          "(unscored articles show as neutral; run `sentiment`, then `load` again)", flush=True)
    with psycopg.connect(args.database_url) as conn:
        ensure_relationship_types(conn)
        conn.commit()
        ids = ensure_companies(conn, list(companies.values()))
        print(f"relations written: {load_relations(conn, edges, snippets, ids)}", flush=True)
        conn.commit()
        for i in range(0, len(bundles), args.batch):
            load_events(conn, bundles[i:i + args.batch], ids, sentiment)
            conn.commit()
            print(f"events {min(i + args.batch, len(bundles)):,}/{len(bundles):,}", flush=True)
        pruned = prune_events(conn, CHAINS[args.chain]["value_chain"], {b.article.news_id for b in bundles})
        conn.commit()
        print(f"stale events removed: {pruned}", flush=True)
        print(f"price rows written: {load_prices(conn, ids, bars)}")
        conn.commit()
    return 0


def _load_bundles(chain: str) -> list[EventBundle]:
    # Written by `analyze` into the git-ignored local nexus/data directory (see cmd_load).
    with (_paths(chain)["analyzed"] / "events.pkl").open("rb") as f:
        return pickle.load(f)


def cmd_sentiment(args) -> int:
    from .impact.sentiment import load_finbert, score_articles
    out = _paths(args.chain)["analyzed"] / "sentiment.parquet"
    articles = [b.article for b in _load_bundles(args.chain)]
    print(f"articles: {len(articles):,} → {out}", flush=True)
    started = time.monotonic()
    frame = score_articles(articles, load_finbert(args.threads), out, batch_size=args.batch_size,
                           progress=lambda done, total: print(
                               f"  scored {done:,}/{total:,} ({time.monotonic() - started:,.0f}s)", flush=True))
    print(f"sentiment rows: {len(frame):,} ({time.monotonic() - started:,.0f}s)")
    return 0


def impact_dataset(bundles: list[EventBundle], companies: dict[str, Company], sentiment, prices: PriceCache,
                   vocab: list[str] | None, *, today: date):
    """Feature table for training (vocab=None: categories from the training period) or prediction (the model's vocab)."""
    from .impact.model import TRAIN_END
    if vocab is None:
        vocab = category_vocab([b for b in bundles if b.trading_date is not None and b.trading_date <= TRAIN_END])
    first = min((b.article.dateline.astimezone(KST).date() for b in bundles), default=today)
    start = first - PRICE_HISTORY
    contexts = build_price_contexts(companies, prices, start, today)
    calendar = sorted(prices.closes(BENCHMARK["KOSPI"], start, today).index)
    frame = build_frame(bundles, companies, sentiment, contexts, vocab, MentionTimeline(bundles), calendar)
    return frame, vocab


def _model_path(chain: str) -> Path:
    from .impact.model import MODEL_VERSION
    return _paths(chain)["data"] / "models" / f"{MODEL_VERSION}-{chain}.joblib"


def _require_sentiment(chain: str):
    """Both impact commands need sentiment: the model's strongest inputs would otherwise be silently NaN."""
    from .impact.sentiment import read_sentiment
    path = _paths(chain)["analyzed"] / "sentiment.parquet"
    sentiment = read_sentiment(path)
    if sentiment.empty:
        sys.exit(f"no sentiment scores in {path}: run the sentiment command first")
    return sentiment


def cmd_train_impact(args) -> int:
    from .impact.model import METHODS, train_and_evaluate
    from .impact.report import write_report
    from .impact.sentiment import MODEL_REVISION
    p = _paths(args.chain)
    companies, _ = _load_master(args.chain)
    sentiment = _require_sentiment(args.chain)
    started = time.monotonic()
    frame, vocab = impact_dataset(_load_bundles(args.chain), companies, sentiment, PriceCache(p["prices"]), None,
                                  today=datetime.now(KST).date())
    print(f"rows: {len(frame):,}, labeled: {int(frame['label'].notna().sum()):,}, categories: {len(vocab)} "
          f"({time.monotonic() - started:,.0f}s)", flush=True)
    model, ev = train_and_evaluate(frame, vocab, sentiment_revision=MODEL_REVISION, now=datetime.now(KST))
    model.save(_model_path(args.chain))
    write_report(ev, IMPACT_REPORT)
    status = "passed" if ev.tau_passed else "fallback"
    print(f"tau: {ev.tau:.2f} ({status}), coverage on test: {(ev.coverage or 0) * 100:.1f}%")
    for method in METHODS:
        s = ev.comparison[method]
        print(f"  {method}: accuracy {s['accuracy']}, macro-F1 {s['macro_f1']}, log-loss {s['log_loss']}")
    print(f"model: {_model_path(args.chain)}, report: {IMPACT_REPORT} ({time.monotonic() - started:,.0f}s)")
    return 0


def cmd_predict_impact(args) -> int:
    import psycopg
    from .impact.model import ImpactModel, outputs, predict_proba
    from .load.postgres import ensure_companies, write_predictions
    p = _paths(args.chain)
    companies, _ = _load_master(args.chain)
    sentiment = _require_sentiment(args.chain)
    model = ImpactModel.load(_model_path(args.chain))
    frame, _ = impact_dataset(_load_bundles(args.chain), companies, sentiment, PriceCache(p["prices"]), model.vocab,
                              today=datetime.now(KST).date())
    # Newer articles may legitimately lack a score; make the share visible instead of silent.
    print(f"rows: {len(frame):,}, with sentiment: {frame['sent_pos'].notna().mean() * 100:.1f}%", flush=True)
    results = outputs(predict_proba(model.estimator, frame, model.columns), model.tau)
    with psycopg.connect(args.database_url) as conn:
        ids = ensure_companies(conn, list(companies.values()))
        conn.commit()
        rows = [(news_id, ids[key], *result)
                for news_id, key, result in zip(frame["news_id"], frame["company_key"], results) if key in ids]
        updated = write_predictions(conn, CHAINS[args.chain]["value_chain"], rows)
        conn.commit()
    directions = Counter(direction or "HELD" for direction, _, _ in results)
    print(f"rows updated: {updated:,} of {len(rows):,}; directions: {dict(directions)}")
    return 0


CASE_REPORT = REPO_ROOT / "docs" / "nexus" / "case-reaction-report.md"


def cmd_cases(args) -> int:
    """News types, past-case reactions, direction labels and their hit rate (investor view, V12 tables)."""
    import psycopg
    from .cases.newstype import classify
    from .cases.reaction import (HORIZONS, TRAIN_END, WINDOWS, MoveBook, SignalBook, backtest, case_frame,
                                 move_backtest, position_of, reaction_stats, unique_cases)
    from .cases.report import write_report
    from .load.cases import (write_abnormal_returns, write_backtest, write_index_prices, write_move_backtest,
                             write_moves, write_news_types, write_signals, write_stats)
    from .load.postgres import ensure_companies
    p = _paths(args.chain)
    value_chain = CHAINS[args.chain]["value_chain"]
    companies, _ = _load_master(args.chain)
    bundles = _load_bundles(args.chain)
    started = time.monotonic()
    types = {b.article.news_id: classify(b.article) for b in bundles}
    print(f"events: {len(bundles):,}, typed: {Counter(t.code for t in types.values()).most_common(5)}", flush=True)

    prices, today = PriceCache(p["prices"]), datetime.now(KST).date()
    days = [b.trading_date for b in bundles if b.trading_date]
    start = min(days) - PRICE_LOOKBACK
    index_closes = {market: prices.closes(symbol, start, today) for market, symbol in BENCHMARK.items()}
    stock_closes, ar_cache = {}, {}

    def returns(key: str, t0: date) -> list:
        if (key, t0) not in ar_cache:
            company = companies.get(key)
            if not company or company.market not in BENCHMARK:
                ar_cache[(key, t0)] = []
            else:
                if company.ticker not in stock_closes:
                    stock_closes[company.ticker] = prices.closes(company.ticker, start, today)
                ar_cache[(key, t0)] = abnormal_returns(key, t0, stock_closes[company.ticker],
                                                       index_closes[company.market], company.market, windows=WINDOWS)
        return ar_cache[(key, t0)]

    frame = case_frame(bundles, {k: v.code for k, v in types.items()},
                       lambda key, t0: {r.window_days: r.abnormal_return for r in returns(key, t0)})
    cases = unique_cases(frame)
    stats = reaction_stats(cases)
    book = SignalBook(stats)
    tested = backtest(cases, book)
    moves = MoveBook(cases)
    moves_tested = move_backtest(cases, moves)
    test_days = [d for d in days if d > TRAIN_END]
    print(f"cases: {len(cases):,}, stats rows: {len(stats):,} ({time.monotonic() - started:,.0f}s)", flush=True)
    for r in tested[tested["news_type"].isna()].itertuples(index=False):
        hit = r.hit_count / r.labeled_count if r.labeled_count else float("nan")
        print(f"  {r.horizon}: labeled {r.labeled_count:,}/{r.case_count:,}, hit {hit:.3f}, "
              f"base down share {r.base_down_share:.3f}", flush=True)
    for r in moves_tested.itertuples(index=False):
        print(f"  move {r.horizon} {r.grade}: {r.case_count:,} days, predicted {r.predicted_move:.3f}, "
              f"realized {r.realized_move:.3f}", flush=True)
    write_report(CASE_REPORT, types=Counter(t.code for t in types.values()), stats=stats, backtest=tested,
                 moves=moves_tested, cases=len(cases), train_end=TRAIN_END, test_end=max(test_days, default=None),
                 now=datetime.now(KST))

    with psycopg.connect(args.database_url) as conn:
        ids = ensure_companies(conn, list(companies.values()))
        print(f"news types written: {write_news_types(conn, value_chain, [(k, v.code, v.confidence) for k, v in types.items()]):,}",
              flush=True)
        ar_rows, signal_rows = [], []
        for b in bundles:
            news_type = types[b.article.news_id].code
            for i in b.impacts:
                if i.key not in ids:
                    continue
                if b.trading_date:
                    ar_rows += [(b.article.news_id, ids[i.key], r) for r in returns(i.key, b.trading_date)]
                position = position_of(i.key, i.basis, i.path)
                for horizon in HORIZONS:
                    s = book.signal(news_type, position, i.key, horizon, status=i.status)
                    signal_rows.append((b.article.news_id, ids[i.key], horizon, position, s.label, s.hold_reason,
                                        s.scope, s.case_count, s.same_share, s.mean_ar, s.down_share))
        print(f"abnormal returns written: {write_abnormal_returns(conn, ar_rows):,}", flush=True)
        print(f"signals written: {write_signals(conn, value_chain, signal_rows):,}", flush=True)
        print(f"stats written: {write_stats(conn, value_chain, stats, ids, period_start=min(days), period_end=TRAIN_END):,}",
              flush=True)
        write_backtest(conn, value_chain, tested, train_end=TRAIN_END,
                       test_start=min(test_days, default=TRAIN_END), test_end=max(test_days, default=TRAIN_END))
        print(f"expected moves written: {write_moves(conn, value_chain, moves, ids, period_end=TRAIN_END)}", flush=True)
        write_move_backtest(conn, value_chain, moves_tested, train_end=TRAIN_END,
                            test_start=min(test_days, default=TRAIN_END), test_end=max(test_days, default=TRAIN_END))
        print(f"index closes written: {write_index_prices(conn, index_closes)}", flush=True)
    print(f"report: {CASE_REPORT} ({time.monotonic() - started:,.0f}s)")
    return 0


def cmd_fundamentals(args) -> int:
    """Market cap and size class from the KRX listing; PER, PBR and dividend yield from OpenDART when the key works."""
    import FinanceDataReader as fdr
    import psycopg
    from .config import dart_key
    from .fundamentals.dart import ANNUAL, DartClient, DartError, interim_candidates, trailing_net_income
    from .fundamentals.metrics import compute, size_ranks
    from .load.fundamentals import write_fundamentals
    from .load.postgres import ensure_companies
    companies, _ = _load_master(args.chain)
    listing = fdr.StockListing("KRX")
    ranks = size_ranks(listing)
    by_code = listing.set_index("Code")
    today = datetime.now(KST).date()

    dart, corp = None, {}
    try:
        dart = DartClient(dart_key())
        corp = dart.corp_codes()
        print(f"OpenDART: {len(corp):,} listed corp codes", flush=True)
    except (DartError, OSError, ValueError) as e:
        print(f"OpenDART unavailable — PER/PBR/dividend stay empty: {e}", flush=True)
        dart = None

    results, filled = {}, 0
    for key, c in companies.items():
        if c.ticker not in by_code.index:
            continue
        row = by_code.loc[c.ticker]
        market_cap, close = int(row["Marcap"]), float(row["Close"])
        net_income = equity = dps = None
        basis = ""
        if dart and c.ticker in corp:
            code = corp[c.ticker]
            try:
                annual = dart.statement(code, today.year - 1, ANNUAL)
                interim = next((s for y, r in interim_candidates(today) if (s := dart.statement(code, y, r))), None)
                net_income, basis = trailing_net_income(annual, interim)
                equity = (interim or annual).equity if (interim or annual) else None
                dps = dart.dividend(code, today.year - 1)
                filled += net_income is not None
            except (OSError, ValueError) as e:
                print(f"  {c.name}: OpenDART failed ({e})", flush=True)
        results[key] = compute(market_cap, ranks.get(c.ticker), close, net_income, equity, dps, basis)
    with psycopg.connect(args.database_url) as conn:
        ids = ensure_companies(conn, list(companies.values()))
        written = write_fundamentals(conn, {ids[k]: v for k, v in results.items() if k in ids}, price_date=today,
                                     source="KRX listing + OpenDART" if dart else "KRX listing")
    sizes = Counter(f.size_class for f in results.values())
    print(f"fundamentals written: {written} (with financials: {filled}), sizes: {dict(sizes)}")
    return 0


def galaxy_chains() -> list[str]:
    """Value chains that `analyze` has produced relations for; publish-graph puts them in one snapshot."""
    return [chain for chain in CHAINS if (_paths(chain)["analyzed"] / "relations.pkl").is_file()]


def cmd_publish_graph(args) -> int:
    # relations.pkl is written by `analyze` into the git-ignored local nexus/data directory (see cmd_load).
    import psycopg
    from .galaxy.build import WINDOWS, build_galaxy
    from .load.galaxy import publish_galaxy
    from .load.postgres import ensure_companies
    chains = galaxy_chains()
    if not chains:
        sys.exit("no analyzed value chain: run analyze first")
    started = time.monotonic()
    inputs, companies_all, roles = [], {}, {}
    for chain in chains:
        p = _paths(chain)
        companies, recognizer = _load_master(chain)
        store = ArticleStore(p["articles"])
        articles = store.read([x for x in store.partitions() if x.startswith(f"{chain}-")])
        with (p["analyzed"] / "relations.pkl").open("rb") as f:
            edges, snippets = pickle.load(f)
        inputs.append((articles, companies, recognizer.key_of_org, edges, snippets))
        companies_all.update(companies)
        roles.update({key: (CHAINS[chain]["label"], c.role) for key, c in companies.items()})
        print(f"{chain}: {len(articles):,} articles, {len(companies)} companies", flush=True)
    as_of, galaxy = build_galaxy(inputs)
    with psycopg.connect(args.database_url) as conn:
        ids = ensure_companies(conn, list(companies_all.values()))
        result = publish_galaxy(conn, as_of, galaxy, ids, roles)
        conn.commit()
    print(f"as_of {as_of.date()} ({', '.join(chains)})")
    for window in WINDOWS:
        kinds = Counter("함께 언급" if e.rel == "CO_MENTIONED" else "근거 관계" for e in galaxy if e.window == window)
        print(f"  {window}: {dict(kinds)}")
    print(f"evidence rows: {result['evidence_rows']:,} ({time.monotonic() - started:,.0f}s)")
    return 0


RELATION_REPORT = REPO_ROOT / "docs" / "nexus" / "relation-model-report.md"
REVIEW_OK, REVIEW_NO = {"o", "1", "y", "ok", "맞음"}, {"x", "0", "n", "no", "틀림"}


def _relations_dir() -> Path:
    return load_settings().data_dir / "relations"


def cmd_relation_fragments(args) -> int:
    from .relations.fragments import collect_fragments, sample_for_labeling, write_fragments
    from .relations.labels import write_sample
    p = _paths(args.chain)
    companies, recognizer = _load_master(args.chain)
    store = ArticleStore(p["articles"])
    articles = store.read([x for x in store.partitions() if x.startswith(f"{args.chain}-")])
    if not articles:
        sys.exit("no articles collected")
    today = datetime.now(KST).date()
    first = min(a.published_date for a in articles)
    last = max(a.published_date for a in articles) + timedelta(days=1)
    client = CacheOnlyClient(_client())
    fragments = collect_fragments(articles, recognizer, companies, client, start=first, end=min(last, today),
                                  today=today)
    out = _relations_dir()
    write_fragments(fragments, out / "fragments.parquet")
    sample = sample_for_labeling(fragments)
    write_sample(sample, out / "sample.jsonl", {k: c.name for k, c in companies.items()})
    rule_hits = sum(1 for f in fragments if f.rule_rel)
    print(f"fragments: {len(fragments):,} (rule-judged {rule_hits:,}), sample: {len(sample):,}, "
          f"cache misses: {client.misses} → {out}")
    return 0


def _review_agreement(path: Path) -> float | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8-sig", newline="") as f:
        marks = [row.get("ok", "").strip().lower() for row in csv.DictReader(f)]
    agree, disagree = sum(m in REVIEW_OK for m in marks), sum(m in REVIEW_NO for m in marks)
    return agree / (agree + disagree) if agree + disagree else None


def cmd_train_relations(args) -> int:
    from .relations.fragments import read_fragments
    from .relations.kpfbert import render_relation_report, train_relation_model
    from .relations.labels import (dedupe_near_duplicates, purge_seen_sentences, read_labels, should_apply,
                                   split_by_pair)
    rel = _relations_dir()
    labels_path = rel / "labels.jsonl"
    if not labels_path.is_file():
        sys.exit("no labels: label nexus/data/relations/sample.jsonl first")
    by_id = {f.fragment_id: f for f in read_fragments(rel / "fragments.parquet")}
    labels, dropped = read_labels(labels_path, set(by_id))
    print(f"labels: {len(labels):,} (dropped {dropped}) {dict(Counter(labels.values()))}", flush=True)
    ids_by_pair: dict[tuple[str, str], list[str]] = {}
    for fid in sorted(labels):
        f = by_id[fid]
        ids_by_pair.setdefault((f.a_key, f.b_key), []).append(fid)
    before = sum(len(v) for v in ids_by_pair.values())
    ids_by_pair = dedupe_near_duplicates(ids_by_pair, {fid: by_id[fid].text for fid in labels})
    after = sum(len(v) for v in ids_by_pair.values())
    print(f"near-duplicates grouped: {before:,} → {after:,} labelled sentences", flush=True)
    train_ids, valid_ids, test_ids, moved = purge_seen_sentences(*split_by_pair(ids_by_pair),
                                                                 {fid: by_id[fid].text for fid in labels})
    print(f"moved to train (near-duplicate already in train): valid {moved['valid']}, test {moved['test']}", flush=True)
    rows = lambda ids: [(by_id[fid], labels[fid]) for fid in ids]
    started = time.monotonic()
    # train beside the applied model; it is replaced only when the new one passes, and kept as model-prev
    new = rel / "model-new"
    if new.exists():
        shutil.rmtree(new)
    stats = train_relation_model(rows(train_ids), rows(valid_ids), rows(test_ids), new)
    stats["labels"] = dict(Counter(labels.values()))
    stats["dedup"] = (before, after)
    stats["moved"] = moved
    stats["review"] = _review_agreement(rel / "review.csv")
    precision = stats["test_precision"]
    stats["applied"] = should_apply(precision)
    if stats["applied"]:
        if (rel / "model").exists():
            if (rel / "model-prev").exists():
                shutil.rmtree(rel / "model-prev")
            (rel / "model").rename(rel / "model-prev")
        new.rename(rel / "model")
    else:
        # below APPLY_PRECISION on the test split the new model is not used; an applied model stays as it is
        if (rel / "model-rejected").exists():
            shutil.rmtree(rel / "model-rejected")
        new.rename(rel / "model-rejected")
    RELATION_REPORT.write_text(render_relation_report(stats), encoding="utf-8")
    print(f"tau {stats['tau']:.2f}, test precision {precision}, recall {stats['test_recall']}, "
          f"rules {stats['rule_precision']}/{stats['rule_recall']}, applied {stats['applied']} "
          f"({time.monotonic() - started:,.0f}s) → {RELATION_REPORT}")
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
    sentiment = sub.add_parser("sentiment")
    sentiment.add_argument("--chain", choices=sorted(CHAINS), required=True)
    sentiment.add_argument("--threads", type=int, default=8)
    sentiment.add_argument("--batch-size", type=int, default=32)
    sub.add_parser("train-impact").add_argument("--chain", choices=sorted(CHAINS), required=True)
    predict = sub.add_parser("predict-impact")
    predict.add_argument("--chain", choices=sorted(CHAINS), required=True)
    predict.add_argument("--database-url", required=True)
    sub.add_parser("publish-graph").add_argument("--database-url", required=True)
    for name in ("cases", "fundamentals"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--chain", choices=sorted(CHAINS), required=True)
        cmd.add_argument("--database-url", required=True)
    for name in ("relation-fragments", "train-relations"):
        sub.add_parser(name).add_argument("--chain", choices=sorted(CHAINS), required=True)
    args = parser.parse_args(argv)
    handler = {"build-master": cmd_build_master, "collect": cmd_collect, "analyze": cmd_analyze,
               "migrate": cmd_migrate, "load": cmd_load, "sentiment": cmd_sentiment,
               "train-impact": cmd_train_impact, "predict-impact": cmd_predict_impact,
               "publish-graph": cmd_publish_graph, "cases": cmd_cases, "fundamentals": cmd_fundamentals, "relation-fragments": cmd_relation_fragments,
               "train-relations": cmd_train_relations}[args.command]
    return handler(args)
