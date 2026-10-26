import io
import zipfile
from datetime import date, datetime, timezone
from uuid import uuid4

import pandas as pd
import pytest

from nexus.fundamentals.dart import (ANNUAL, HALF, DartClient, DartError, Statement, interim_candidates,
                                     parse_corp_codes, parse_dividend, parse_shares, parse_statement,
                                     trailing_net_income, valid_key)
from nexus.fundamentals.dart_only import ListedClose, dart_fundamentals
from nexus.fundamentals.metrics import compute, compute_from_shares, size_ranks
from nexus.load.fundamentals import listed_with_close


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


def test_parse_shares_takes_issued_common_shares():
    payload = {"status": "000", "list": [
        {"se": "보통주", "isu_stock_totqy": "50,000,000", "istc_totqy": "16,264,300", "tesstk_co": "1,000"},
        {"se": "우선주", "istc_totqy": "-"},
        {"se": "합계", "istc_totqy": "16,264,300"},
    ]}
    assert parse_shares(payload) == 16264300
    assert parse_shares({"status": "013", "message": "조회된 데이타가 없습니다."}) is None


def test_compute_from_shares_uses_issued_shares_times_close():
    f = compute_from_shares(1000, 50.0, 1250, 9800, 1, "basis")
    assert (f.market_cap, f.per, f.pbr, f.dividend_yield) == (50000, 40.0, 5.102, 0.02)
    assert (f.market_cap_rank, f.size_class, f.close_price) == (None, None, 50.0)
    no_shares = compute_from_shares(None, 50.0, 1250, 9800, 1, "basis")
    assert (no_shares.market_cap, no_shares.per, no_shares.pbr, no_shares.dividend_yield) == (None, None, None, 0.02)


class FakeDart:
    """Statements for corp C1, a network failure for C3; records which report the share count came from."""

    def __init__(self):
        self.share_reports = []

    def statement(self, code, year, report):
        if code == "C3":
            raise OSError("connection reset")
        return {(2025, ANNUAL): parse_statement(ANNUAL_2025, 2025, ANNUAL),
                (2026, HALF): parse_statement(HALF_2026, 2026, HALF)}.get((year, report))

    def shares(self, code, year, report):
        self.share_reports.append((code, year, report))
        return 1000

    def dividend(self, code, year):
        return 1


def test_dart_fundamentals_builds_rows_from_statements_shares_and_db_close():
    a, b, c = uuid4(), uuid4(), uuid4()
    day = date(2026, 10, 23)
    listed = [ListedClose(a, "알파", "000010", 50.0, day), ListedClose(b, "베타", "000020", 10.0, day),
              ListedClose(c, "감마", "000030", 20.0, day)]
    dart = FakeDart()
    rows = dart_fundamentals(listed, dart, {"000010": "C1", "000030": "C3"}, date(2026, 10, 26))
    assert (rows[a].net_income_ttm, rows[a].equity, rows[a].market_cap, rows[a].per) == (1250, 9800, 50000, 40.0)
    assert rows[a].fiscal_basis == "2025 사업보고서 + 2026 반기 − 2025 반기"
    assert dart.share_reports == [("C1", 2026, HALF)]          # same report as the equity figure
    assert (rows[b].market_cap, rows[b].per, rows[b].close_price) == (None, None, 10.0)   # no DART corp code
    assert (rows[c].net_income_ttm, rows[c].market_cap, rows[c].close_price) == (None, None, 20.0)  # DART failed


def test_listed_with_close_takes_last_daily_close_of_active_listed(pg):
    def company(name, code, market, status="ACTIVE"):
        return pg.execute("INSERT INTO company (name, stock_code, market, status) VALUES (%s, %s, %s, %s) "
                          "RETURNING company_id", (name, code, market, status)).fetchone()[0]

    def price(company_id, day, close, interval="1D"):
        pg.execute("INSERT INTO stock_price_history (company_id, trading_at, interval_type, open_price, high_price, "
                   "low_price, close_price, trading_volume) VALUES (%s, %s, %s, %s, %s, %s, %s, 1)",
                   (company_id, datetime(2026, 10, day, tzinfo=timezone.utc), interval, close, close, close, close))

    alpha = company("알파", "000010", "KOSPI")
    beta = company("베타", "000020", "KOSDAQ")
    old = company("비활성", "000030", "KOSPI", status="INACTIVE")
    foreign = company("해외", "NVDA", "NASDAQ")
    price(alpha, 22, 100)
    price(alpha, 23, 110)
    price(alpha, 24, 999, interval="1W")
    price(old, 23, 5)
    price(foreign, 23, 7)
    rows = listed_with_close(pg)
    assert [(r.company_id, r.stock_code, r.close, r.price_date) for r in rows] == [(alpha, "000010", 110.0, date(2026, 10, 23))]
    assert beta not in {r.company_id for r in rows}            # no price yet
