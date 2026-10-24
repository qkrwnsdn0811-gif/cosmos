import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from nexus.fundamentals.dart import (ANNUAL, HALF, DartClient, DartError, Statement, interim_candidates,
                                     parse_corp_codes, parse_dividend, parse_statement, trailing_net_income, valid_key)
from nexus.fundamentals.metrics import compute, size_ranks


def _row(fs, sj, name, **amounts):
    return {"fs_div": fs, "sj_div": sj, "account_nm": name, **amounts}


ANNUAL_2025 = {"status": "000", "list": [
    _row("OFS", "IS", "당기순이익", thstrm_amount="50", frmtrm_amount="40"),
    _row("CFS", "IS", "당기순이익(손실)", thstrm_amount="1,000", frmtrm_amount="800"),
    _row("CFS", "BS", "자본총계", thstrm_amount="9,000"),
]}
HALF_2026 = {"status": "000", "list": [
    _row("CFS", "IS", "당기순이익", thstrm_amount="300", thstrm_add_amount="700", frmtrm_amount="200",
         frmtrm_add_amount="450"),
    _row("CFS", "BS", "자본총계", thstrm_amount="9,800"),
]}


def test_parse_statement_prefers_consolidated_and_year_to_date():
    annual = parse_statement(ANNUAL_2025, 2025, ANNUAL)
    assert (annual.net_income, annual.net_income_prior, annual.equity) == (1000, 800, 9000)
    half = parse_statement(HALF_2026, 2026, HALF)
    assert (half.net_income, half.net_income_prior, half.equity) == (700, 450, 9800)
    assert parse_statement({"status": "013", "message": "조회된 데이타가 없습니다."}, 2026, HALF) is None


def test_trailing_net_income():
    annual, half = parse_statement(ANNUAL_2025, 2025, ANNUAL), parse_statement(HALF_2026, 2026, HALF)
    assert trailing_net_income(annual, half) == (1250, "2025 사업보고서 + 2026 반기 − 2025 반기")
    assert trailing_net_income(annual, None) == (1000, "2025 사업보고서")
    stale = Statement(2025, HALF, 600, 400, 1)           # interim of the same year as the annual: not combinable
    assert trailing_net_income(annual, stale)[0] == 1000
    assert trailing_net_income(None, None) == (None, "")


def test_parse_dividend_common_share():
    payload = {"status": "000", "list": [
        {"se": "주당 현금배당금(원)", "stock_knd": "우선주", "thstrm": "1,500"},
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,444"},
    ]}
    assert parse_dividend(payload) == 1444
    assert parse_dividend({"status": "000", "list": [{"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "-"}]}) is None


def test_parse_corp_codes():
    xml = ("<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code>"
           "</list><list><corp_code>00999999</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list></result>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    assert parse_corp_codes(buf.getvalue()) == {"005930": "00126380"}


def test_key_validation():
    assert valid_key("a" * 40)
    assert not valid_key("a" * 56)
    with pytest.raises(DartError):
        DartClient("short")


def test_interim_candidates_follow_filing_deadlines():
    assert interim_candidates(date(2026, 10, 25)) == [(2026, HALF), (2026, "11013")]
    assert interim_candidates(date(2026, 3, 1)) == []


def test_size_ranks_by_market_without_preferred_shares():
    listing = pd.DataFrame({
        "Code": ["000010", "000020", "000025", "000030", "000040"],
        "Market": ["KOSPI", "KOSPI", "KOSPI", "KOSDAQ GLOBAL", "KOSDAQ"],
        "Marcap": [100, 300, 999, 50, 70],
    })
    ranks = size_ranks(listing)
    assert ranks["000020"] == (1, "LARGE") and ranks["000010"] == (2, "LARGE")
    assert "000025" not in ranks                       # preferred share
    assert ranks["000040"] == (1, "LARGE") and ranks["000030"] == (2, "LARGE")


def test_compute_ratios():
    f = compute(1000, (150, "MID"), 50.0, 100, 400, 2, "basis")
    assert (f.per, f.pbr, f.dividend_yield, f.size_class, f.market_cap_rank) == (10.0, 2.5, 0.04, "MID", 150)
    loss = compute(1000, None, 50.0, -10, 0, None, "")
    assert (loss.per, loss.pbr, loss.dividend_yield, loss.size_class) == (None, None, None, None)
