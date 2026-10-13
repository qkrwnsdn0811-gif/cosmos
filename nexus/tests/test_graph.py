from datetime import datetime, timedelta, timezone

import pytest

from nexus.models import Edge, EvidenceSnippet
from nexus.relations.graph import BaseGraph, load_cosmos_edges, news_edges

KST = timezone(timedelta(hours=9))


def snip(src, dst, rel, confidence, news_id="N1"):
    return EvidenceSnippet(news_id, src, dst, rel, "s", "p", confidence, "t", "p",
                           datetime(2026, 10, 13, tzinfo=KST), "")


def test_cosmos_edges_filtered_by_keys(tmp_path):
    csv = tmp_path / "rel.csv"
    csv.write_text(
        "source_market,source_stock_code,target_market,target_stock_code,relationship_type_code\n"
        "KOSPI,000660,KOSPI,005930,COMPETE\n"
        "KOSPI,042700,KOSPI,000660,SUPPLY\n"
        "KOSPI,042700,KOSPI,111111,PARTNER\n", encoding="utf-8")
    edges = load_cosmos_edges(csv, {"KOSPI:000660", "KOSPI:005930", "KOSPI:042700"})
    assert edges == [
        Edge("KOSPI:000660", "KOSPI:005930", "COMPETE", 1.0, False, "COSMOS"),
        Edge("KOSPI:042700", "KOSPI:000660", "SUPPLY", 1.0, True, "COSMOS"),
    ]


def test_news_edge_strength_combines():
    [edge] = news_edges([snip("A", "B", "SUPPLY", 0.7), snip("A", "B", "SUPPLY", 0.5, "N2")])
    assert (edge.src, edge.dst, edge.rel, edge.directed, edge.source) == ("A", "B", "SUPPLY", True, "NEWS")
    assert edge.strength == pytest.approx(0.85)


def test_news_edge_below_min_dropped():
    assert news_edges([snip("A", "B", "SUPPLY", 0.4)]) == []


def test_undirected_merges_both_orders():
    [edge] = news_edges([snip("B", "A", "PARTNER", 0.5), snip("A", "B", "PARTNER", 0.6, "N2")])
    assert (edge.src, edge.dst, edge.directed) == ("A", "B", False)
    assert edge.strength == pytest.approx(0.8)


def test_neighbors_traverse_reverse_direction():
    supply = Edge("A", "B", "SUPPLY", 1.0, True, "NEWS")
    graph = BaseGraph([supply])
    assert graph.neighbors("B") == [("A", supply)]
    assert graph.neighbors("A") == [("B", supply)]


def test_weight_uses_type_weight():
    graph = BaseGraph([])
    assert graph.weight(Edge("A", "B", "SUPPLY", 0.5, True, "NEWS")) == pytest.approx(0.4)
