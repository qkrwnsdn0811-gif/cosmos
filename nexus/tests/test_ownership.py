from nexus.fundamentals.ownership import Investment, match_ownerships, name_index, normalize_name, parse_investments


def _payload(*rows, status="000"):
    return {"status": status, "list": [dict(inv_prm=n, trmend_blce_qota_rt=s, invstmnt_purps=p, stlm_dt="2026-06-30",
                                            rcept_no="20260814000123") for n, s, p in rows]}


def test_normalize_name_strips_corporate_marks():
    assert normalize_name("삼성전기㈜") == "삼성전기"
    assert normalize_name("(주)이노메이트") == "이노메이트"
    assert normalize_name("하나머티리얼즈(주)") == "하나머티리얼즈"
    assert normalize_name("주식회사 원익 아이피에스") == "원익아이피에스"
    assert normalize_name("SK hynix Inc.") == "skhynix"


def test_parse_investments_reads_name_stake_and_purpose():
    rows = parse_investments(_payload(("삼성전기㈜", "23.7", "경영참여"), ("(주)이노메이트", "-", "경영참여"),
                                      ("스테코㈜", "1,000.0", "단순\n투자")))
    assert rows[0] == Investment("삼성전기㈜", 23.7, "경영참여", "2026-06-30", "20260814000123")
    assert rows[1].stake is None
    assert rows[2].stake == 1000.0
    assert rows[2].purpose == "단순투자"
    assert parse_investments(_payload(("x", "1", "y"), status="013")) == []


def test_match_ownerships_keeps_listed_peers_only():
    index = name_index({"KOSPI:005930": "삼성전자", "KOSPI:009150": "삼성전기", "KOSDAQ:166090": "하나머티리얼즈"},
                       [{"alias": "삼성전기", "ticker": "KOSPI:009150", "ambiguity": "safe"},
                        {"alias": "삼성", "ticker": "KOSPI:005930", "ambiguity": "ambiguous"}])
    investments = [
        Investment("삼성전기㈜", 23.7, "경영참여", "2026-06-30", "r1"),
        Investment("삼성전기 주식회사", 20.0, "경영참여", "2026-06-30", "r1"),   # 같은 상대 → 큰 지분율 하나
        Investment("삼성전자㈜", 1.0, "단순투자", "2026-06-30", "r1"),         # 자기 자신
        Investment("스테코㈜", 70.0, "경영참여", "2026-06-30", "r1"),          # 61개사 밖
        Investment("하나머티리얼즈(주)", None, "경영참여", "2026-06-30", "r1"),  # 지분율 없음
        Investment("삼성", 5.0, "단순투자", "2026-06-30", "r1"),               # 모호한 별칭은 쓰지 않는다
        Investment("하나머티리얼즈(주)", 0.0002, "단순투자", "2026-06-30", "r1"),  # 1% 미만 극소 지분
    ]
    out = match_ownerships("KOSPI:005930", investments, index)
    assert [(o.investor, o.investee, o.stake) for o in out] == [("KOSPI:005930", "KOSPI:009150", 23.7)]
    assert out[0].purpose == "경영참여"
    assert out[0].settled == "2026-06-30"


def test_ownership_edges_cover_every_window_and_replace_news_invest():
    from nexus.fundamentals.ownership import Ownership, merge_ownership, ownership_edges
    from nexus.galaxy.build import GalaxyEdge

    owned = [Ownership("KOSPI:005930", "KOSPI:009150", 23.7, "경영참여", "2026-06-30", "r1")]
    dart = ownership_edges(owned, ("30D", "1Y", "10Y"))
    assert [(e.src, e.dst, e.rel, e.source, e.window, e.score, e.docs) for e in dart] == [
        ("KOSPI:005930", "KOSPI:009150", "INVEST", "DART", w, 23.7, ()) for w in ("30D", "1Y", "10Y")]
    news = [GalaxyEdge("KOSPI:005930", "KOSPI:009150", "INVEST", "NEWS", "1Y", 80.0, 0.9, ()),
            GalaxyEdge("KOSPI:009150", "KOSPI:005930", "INVEST", "NEWS", "1Y", 70.0, 0.9, ()),
            GalaxyEdge("KOSPI:005930", "KOSPI:009150", "SUPPLY", "NEWS", "1Y", 60.0, 0.9, ())]
    merged = merge_ownership(news, dart)
    kinds = sorted((e.src[-6:], e.dst[-6:], e.rel, e.source, e.window) for e in merged)
    # 같은 방향 뉴스 지분 관계는 공시가 대신하고, 반대 방향 지분·다른 유형은 남는다
    assert ("005930", "009150", "INVEST", "NEWS", "1Y") not in kinds
    assert ("009150", "005930", "INVEST", "NEWS", "1Y") in kinds
    assert ("005930", "009150", "SUPPLY", "NEWS", "1Y") in kinds
    assert sum(1 for k in kinds if k[3] == "DART") == 3


def test_write_ownership_replaces_rows(pg):
    from nexus.fundamentals.ownership import Ownership
    from nexus.load.ownership import write_ownership
    from nexus.load.postgres import ensure_companies
    from nexus.models import Company

    a = Company("KOSPI:900001", "KOSPI", "900001", "가상전자", "SEMICONDUCTOR", "제조")
    b = Company("KOSDAQ:900002", "KOSDAQ", "900002", "하늘반도체", "SEMICONDUCTOR", "장비")
    ids = ensure_companies(pg, [a, b])
    write_ownership(pg, [Ownership(a.key, b.key, 23.7, "경영참여", "2026-06-30", "r1")], ids)
    write_ownership(pg, [Ownership(a.key, b.key, 30.0, "경영참여", "2026-06-30", "r2")], ids)
    rows = pg.execute("SELECT investor_company_id, investee_company_id, stake_pct, purpose, settled_on, receipt_no "
                      "FROM company_ownership").fetchall()
    assert [(r[0], r[1], float(r[2]), r[3], str(r[4]), r[5]) for r in rows] == [
        (ids[a.key], ids[b.key], 30.0, "경영참여", "2026-06-30", "r2")]
