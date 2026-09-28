"""Affected-company expansion: from what an article says to who is exposed, with the reason kept per company.

    from impact import ImpactExpander
    ex = ImpactExpander()                                   # company_industry.csv + ../graph/data/edges_*.csv
    rows = ex.expand(out["companies"], out["industries"])  # out = CompanyExtractor.extract(...)

Every affected company carries `source` and `reasons` so downstream (영향도 모델, 우주 화면) can tell them apart:
    direct    the article names the company            reason: {"alias": "...", "n_mentions": 2, "first_pos": "title"}
    industry  the article talks about its industry     reason: {"industry_id": "SEMI", "n_mentions": 3}
    graph     a named company is linked to it          reason: {"via": "005930", "edge_type": "ownership", "weight": 0.31}

Scores are NOT impact signs (호재/악재) — that is the impact model's job (Story 3). They are exposure strengths:
    direct   = matcher confidence (1.0 dict, 0.9 ticker, 0.7 product, 0.5 group default)
    industry = INDUSTRY_BASE * (1 + log(n_mentions)) / sqrt(industry size)   (wide industries dilute)
    graph    = source score * edge weight * EDGE_FACTOR[edge_type]          (1 hop only)
A company reached several ways keeps the max score and all reasons.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPH_DATA = HERE.parent / "graph" / "data"
INDUSTRY_BASE = 0.6
EDGE_FACTOR = {"ownership": 0.8, "co_mention": 0.5, "sector": 0.3, "correlation": 0.4}
MIN_GRAPH_SCORE = 0.05


class ImpactExpander:
    def __init__(self, company_industry=HERE / "data" / "company_industry.csv", edges_dir=GRAPH_DATA,
                 use_edges=("ownership", "co_mention", "sector"), min_edge_weight=0.2):
        self.name = {}
        self.industry_of = {}
        self.members = defaultdict(list)
        with Path(company_industry).open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                self.name[r["ticker"]] = r["name_official"]
                self.industry_of[r["ticker"]] = r["industry_id"]
                self.members[r["industry_id"]].append(r["ticker"])
        self.industry_ko = {}
        ind_path = HERE / "data" / "seeds" / "industries.csv"
        if ind_path.exists():
            for r in csv.DictReader(ind_path.open(encoding="utf-8-sig", newline="")):
                self.industry_ko[r["industry_id"]] = r["name_ko"]
        # undirected adjacency: ticker -> [(other, edge_type, weight)]
        self.adj = defaultdict(list)
        for et in use_edges:
            p = Path(edges_dir) / f"edges_{et}.csv"
            if not p.exists():
                continue
            for r in csv.DictReader(p.open(encoding="utf-8-sig", newline="")):
                w = float(r["weight"])
                if w < min_edge_weight:
                    continue
                self.adj[r["src_ticker"]].append((r["dst_ticker"], et, w))
                self.adj[r["dst_ticker"]].append((r["src_ticker"], et, w))

    def expand(self, companies: list[dict], industries: list[dict], max_graph_hops: int = 1) -> list[dict]:
        out: dict[str, dict] = {}

        def add(ticker, source, score, reason):
            row = out.setdefault(ticker, {"ticker": ticker, "name": self.name.get(ticker, ticker),
                                          "industry_id": self.industry_of.get(ticker, ""), "score": 0.0,
                                          "source": source, "reasons": []})
            if score > row["score"]:
                row["score"], row["source"] = round(score, 3), source
            row["reasons"].append({"source": source, "score": round(score, 3), **reason})

        for c in companies:
            add(c["ticker"], "direct", float(c["confidence"]),
                {"alias": ",".join(c.get("aliases", [])), "n_mentions": c["n_mentions"], "first_pos": c["first_pos"]})
        direct = {c["ticker"] for c in companies}

        for ind in industries:
            iid = ind["industry_id"]
            size = max(len(self.members.get(iid, [])), 1)
            score = INDUSTRY_BASE * (1 + math.log(max(ind["n_mentions"], 1))) / math.sqrt(size)
            for t in self.members.get(iid, []):
                if t in direct:
                    continue
                add(t, "industry", min(score, 0.6), {"industry_id": iid, "industry_ko": self.industry_ko.get(iid, iid),
                                                    "n_mentions": ind["n_mentions"], "first_pos": ind.get("first_pos", "")})

        if max_graph_hops >= 1:
            for c in companies:
                src_score = float(c["confidence"])
                for other, et, w in self.adj.get(c["ticker"], []):
                    if other in direct:
                        continue
                    s = src_score * w * EDGE_FACTOR.get(et, 0.3)
                    if et == "sector":
                        # a same-industry link is shared by every member of the industry; wide industries dilute
                        s /= math.sqrt(max(len(self.members.get(self.industry_of.get(other, ""), [])), 1))
                    if s >= MIN_GRAPH_SCORE:
                        add(other, "graph", s, {"via": c["ticker"], "via_name": self.name.get(c["ticker"], c["ticker"]),
                                                "edge_type": et, "weight": round(w, 3)})
        return sorted(out.values(), key=lambda r: -r["score"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="append", help="title|body (repeatable)")
    ap.add_argument("--no-ner", action="store_true")
    args = ap.parse_args()
    from pipeline import CompanyExtractor
    ex = CompanyExtractor(use_ner=not args.no_ner)
    ie = ImpactExpander()
    samples = args.text or [
        "반도체 수출 30% 증가|지난달 반도체 수출이 전년 대비 30% 늘었다. HBM 수요가 업황을 끌어올렸다는 분석이다.",
        "삼성전자, HBM4 양산 착수|삼성전자가 차세대 HBM4 양산을 시작했다. 엔비디아 공급이 유력하다.",
        "LG화학 배당 확대|LG화학이 배당을 늘린다. 배터리 자회사 실적 개선이 배경이다.",
    ]
    for s in samples:
        title, body = (s.split("|", 1) + [""])[:2]
        o = ex.extract("demo", title, body)
        rows = ie.expand(o["companies"], o["industries"])
        print(f"\n### {title}")
        print(f"   직접 언급: {[c['name'] for c in o['companies']]} | 산업: {[(i['industry_ko'], i['n_mentions']) for i in o['industries']]}")
        by_src = defaultdict(list)
        for r in rows:
            by_src[r["source"]].append(r)
        for src in ("direct", "graph", "industry"):
            if by_src[src]:
                shown = ", ".join(f"{r['name']}({r['score']}" + (f" via {r['reasons'][0].get('via_name','')}/{r['reasons'][0].get('edge_type','')}" if src == "graph" else "") + ")"
                                  for r in by_src[src][:8])
                more = f" … 외 {len(by_src[src]) - 8}" if len(by_src[src]) > 8 else ""
                print(f"   [{src:8}] {shown}{more}")


if __name__ == "__main__":
    main()
