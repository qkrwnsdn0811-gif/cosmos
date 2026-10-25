import json
from datetime import date

import pytest

from nexus.bigkinds.client import BigKindsClient
from nexus.models import Company
from nexus.relations.fragments import (Fragment, fragment_id, pair_fragments, read_fragments, sample_for_labeling,
                                       write_fragments)
from nexus.bigkinds.client import CacheOnlyClient

A = Company("KOSPI:009150", "KOSPI", "009150", "삼성전기", "SEMICONDUCTOR", "부품")
B = Company("KOSDAQ:357780", "KOSDAQ", "357780", "솔브레인", "SEMICONDUCTOR", "소재")
T = Company("KOSDAQ:095610", "KOSDAQ", "095610", "테스", "SEMICONDUCTOR", "장비")
aliases = lambda key: {A.key: ["삼성전기"], B.key: ["솔브레인"], T.key: ["테스"]}[key]


def doc(hilight, news_id="N1"):
    return {"news_id": news_id, "title": "제목", "hilight": hilight, "dateline": "2026-07-01T10:00:00.000+09:00",
            "provider": "합성일보", "provider_link_page": ""}


def test_cache_only_client_never_calls_the_network(tmp_path):
    real = BigKindsClient("key", tmp_path, transport=lambda path, body: pytest.fail("network call"))
    client = CacheOnlyClient(real)
    assert client.post("/search/news", {"query": "x"}, cache=True) == {"documents": []} and client.misses == 1
    real._cache_file("/search/news", {"query": "y"}).parent.mkdir(parents=True, exist_ok=True)
    real._cache_file("/search/news", {"query": "y"}).write_text(json.dumps({"return_object": {"documents": [1]}}),
                                                                  encoding="utf-8")
    assert client.post("/search/news", {"query": "y"}, cache=True) == {"documents": [1]}


def test_pair_fragments_orders_pair_and_keeps_rule_judgement():
    frags = pair_fragments([doc("<b>삼성전기</b>는 국내 재료 회사인 <b>솔브레인</b>과 협력해 연구에 착수했다.")], B, A, aliases)
    [f] = frags
    assert (f.a_key, f.b_key) == tuple(sorted((A.key, B.key)))
    assert f.rule_rel == "PARTNER" and f.rule_pattern == "clause+kw"
    assert f.fragment_id == fragment_id(f.text, f.a_key, f.b_key) and len(f.fragment_id) == 16


def test_pair_fragments_records_direction_in_ab_terms():
    [f] = pair_fragments([doc("<b>삼성전기</b>는 <b>솔브레인</b>에 장비를 납품한다.")], A, B, aliases)
    expected = "SUPPLY_AB" if f.a_key == A.key else "SUPPLY_BA"
    assert f.rule_rel == expected


def test_pair_fragments_skips_names_inside_other_words():
    assert pair_fragments([doc("<b>삼성전기</b>는 신규 <b>테스</b>트를 마쳤다.")], A, T, aliases) == []


def test_fragments_roundtrip(tmp_path):
    [f] = pair_fragments([doc("<b>삼성전기</b>는 <b>솔브레인</b>에 장비를 납품한다.")], A, B, aliases)
    write_fragments([f], tmp_path / "f.parquet")
    assert read_fragments(tmp_path / "f.parquet") == [f]


def make(i, pair, rule=None):
    a, b = pair
    return Fragment(f"{i:016x}", a, b, "가", "나", f"문장 {i}", f"N{i}", "", "", None, "", rule, rule and "p")


def test_sample_keeps_rule_hits_and_caps_each_pair():
    frags = [make(i, ("A", "B")) for i in range(50)] + [make(100 + i, ("A", "C")) for i in range(5)]
    frags += [make(200, ("A", "B"), rule="PARTNER")]
    sample = sample_for_labeling(frags, size=30, per_pair=20)
    assert any(f.rule_rel for f in sample)
    assert sum(1 for f in sample if (f.a_key, f.b_key) == ("A", "B")) <= 20
    assert len({f.fragment_id for f in sample}) == len(sample) == 25          # 20 (상한) + 5
