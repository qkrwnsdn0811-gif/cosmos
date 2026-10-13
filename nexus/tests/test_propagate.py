import pytest

from nexus.models import CompanyMention, Edge, PathStep
from nexus.propagation.propagate import HOLD_COMENTION_ONLY, HOLD_LOW, propagate
from nexus.relations.graph import BaseGraph

BASE = [
    Edge("C", "A", "SUPPLY", 1.0, True, "NEWS"),
    Edge("A", "D", "COMPETE", 1.0, False, "COSMOS"),
    Edge("E", "C", "SUPPLY", 1.0, True, "COSMOS"),
    Edge("A", "F", "CO_MENTIONED", 0.9, False, "COMENTION"),
]
SEED_A = [CompanyMention("A", 1.0, ("title",))]


def by_key(impacts):
    return {i.key: i for i in impacts}


def test_reported_first():
    first = propagate(SEED_A, BaseGraph(BASE))[0]
    assert (first.key, first.basis, first.hop, first.path, first.score, first.status) == ("A", "REPORTED", 0, (), 1.0, "OK")


def test_one_hop_supply_score():
    c = by_key(propagate(SEED_A, BaseGraph(BASE)))["C"]
    assert (c.basis, c.hop, c.status) == ("INFERRED", 1, "OK")
    assert c.score == pytest.approx(0.8)
    assert c.path == (PathStep("C", "A", "SUPPLY"),)
    assert (c.path[0].source, c.path[0].strength) == ("NEWS", 1.0)   # evidence panel resolves the edge by source


def test_two_hop_score_and_path():
    e = by_key(propagate(SEED_A, BaseGraph(BASE)))["E"]
    assert e.hop == 2
    assert e.score == pytest.approx(0.64)
    assert e.path == (PathStep("C", "A", "SUPPLY"), PathStep("E", "C", "SUPPLY"))


def test_comention_only_is_hold():
    f = by_key(propagate(SEED_A, BaseGraph(BASE)))["F"]
    assert f.score == pytest.approx(0.36)
    assert (f.status, f.hold_reason) == ("HOLD", HOLD_COMENTION_ONLY)


def test_low_score_hold_and_drop():
    edges = BASE + [Edge("A", "G", "COMPETE", 0.5, False, "NEWS"), Edge("A", "H", "PARTNER", 0.2, False, "NEWS")]
    impacts = by_key(propagate(SEED_A, BaseGraph(edges)))
    assert impacts["G"].score == pytest.approx(0.25)
    assert (impacts["G"].status, impacts["G"].hold_reason) == ("HOLD", HOLD_LOW)
    assert "H" not in impacts
    assert impacts["D"].status == "OK"


def test_relational_path_preferred_over_comention():
    edges = [Edge("A", "F", "CO_MENTIONED", 1.0, False, "COMENTION"),
             Edge("A", "X", "SUPPLY", 1.0, True, "NEWS"), Edge("X", "F", "SUPPLY", 0.6, True, "NEWS")]
    f = by_key(propagate(SEED_A, BaseGraph(edges)))["F"]
    assert (f.status, f.hop) == ("OK", 2)
    assert f.score == pytest.approx(0.384)
    assert [s.rel for s in f.path] == ["SUPPLY", "SUPPLY"]


def test_seed_not_inferred():
    mentions = [CompanyMention("A", 1.0, ("title",)), CompanyMention("C", 0.6, ("orgs",))]
    impacts = propagate(mentions, BaseGraph(BASE))
    assert [(i.key, i.basis) for i in impacts if i.key in ("A", "C")] == [("A", "REPORTED"), ("C", "REPORTED")]
    assert sum(1 for i in impacts if i.key == "C") == 1


def test_cap_max_inferred():
    star = [Edge("A", f"N{i:02d}", "SUPPLY", 1.0, True, "NEWS") for i in range(30)]
    impacts = propagate(SEED_A, BaseGraph(star))
    assert sum(1 for i in impacts if i.basis == "INFERRED") == 20
