"""OpenDART financial statements for PER, PBR and dividend yield (F8).

Two reports per company: the latest annual report and the latest interim report of the current year. Trailing
net income = annual + interim year-to-date − the same period a year earlier (the interim report carries both),
equity comes from the interim balance sheet. Consolidated statements (CFS) are used when the company files them.
Parsing is pure (dict in, numbers out) so it is tested without the network; `DartClient` only fetches JSON.
"""
from __future__ import annotations

import io
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date

import requests

BASE = "https://opendart.fss.or.kr/api"
ANNUAL, HALF, Q1, Q3 = "11011", "11012", "11013", "11014"
NET_INCOME = "당기순이익"
EQUITY = "자본총계"
KEY_LENGTH = 40


class DartError(RuntimeError):
    pass


def valid_key(key: str) -> bool:
    return len(key) == KEY_LENGTH and key.isalnum()


def parse_corp_codes(zip_bytes: bytes) -> dict[str, str]:
    """corpCode.xml (zipped) → listed stock code → DART corp code. The file comes only from the OpenDART endpoint;
    the stdlib parser (expat) does not fetch external entities and limits entity expansion, so no extra dependency."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        root = ET.fromstring(zf.read(zf.namelist()[0]))
    codes = {}
    for item in root.iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        if stock:
            codes[stock] = item.findtext("corp_code").strip()
    return codes


def _amount(value) -> int | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _rows(payload: dict) -> list[dict]:
    """Prefer consolidated (CFS) rows; fall back to separate (OFS) statements."""
    rows = payload.get("list") or []
    cfs = [r for r in rows if r.get("fs_div") == "CFS"]
    return cfs or [r for r in rows if r.get("fs_div") == "OFS"] or rows


def _account(rows: list[dict], name: str, statement: str) -> dict | None:
    for r in rows:
        if r.get("sj_div") == statement and str(r.get("account_nm", "")).replace(" ", "").startswith(name):
            return r
    return None


@dataclass(frozen=True)
class Statement:
    year: int
    report: str
    net_income: int | None          # this period (year-to-date for interim reports)
    net_income_prior: int | None    # same period a year earlier (interim) or previous year (annual)
    equity: int | None


def parse_statement(payload: dict, year: int, report: str) -> Statement | None:
    if payload.get("status") != "000":
        return None
    rows = _rows(payload)
    income = _account(rows, NET_INCOME, "IS")
    equity = _account(rows, EQUITY, "BS")
    if income is None and equity is None:
        return None
    if report == ANNUAL or report == Q1:
        current = _amount(income.get("thstrm_amount")) if income else None
        prior = _amount(income.get("frmtrm_amount")) if income else None
    else:   # half / Q3: the "add" columns are year-to-date
        current = _amount(income.get("thstrm_add_amount") or income.get("thstrm_amount")) if income else None
        prior = _amount(income.get("frmtrm_add_amount") or income.get("frmtrm_amount")) if income else None
    return Statement(year, report, current, prior, _amount(equity.get("thstrm_amount")) if equity else None)


def trailing_net_income(annual: Statement | None, interim: Statement | None) -> tuple[int | None, str]:
    """(net income over the last four quarters, how it was built). Falls back to the annual figure alone."""
    if annual and annual.net_income is not None and interim and interim.report != ANNUAL \
            and interim.year == annual.year + 1 and interim.net_income is not None and interim.net_income_prior is not None:
        label = {Q1: "1분기", HALF: "반기", Q3: "3분기"}[interim.report]
        return (annual.net_income + interim.net_income - interim.net_income_prior,
                f"{annual.year} 사업보고서 + {interim.year} {label} − {annual.year} {label}")
    if annual and annual.net_income is not None:
        return annual.net_income, f"{annual.year} 사업보고서"
    return None, ""


def parse_dividend(payload: dict) -> int | None:
    """Cash dividend per common share for the business year (alotMatter)."""
    if payload.get("status") != "000":
        return None
    for r in payload.get("list") or []:
        if "주당" in str(r.get("se", "")) and "현금배당금" in str(r.get("se", "")) and str(r.get("stock_knd", "보통주")).startswith("보통"):
            return _amount(r.get("thstrm"))
    return None


def interim_candidates(today: date) -> list[tuple[int, str]]:
    """Interim reports of the current year newest first; DART filing deadlines are 45 days after quarter end."""
    y = today.year
    out = []
    if today >= date(y, 11, 14):
        out.append((y, Q3))
    if today >= date(y, 8, 14):
        out.append((y, HALF))
    if today >= date(y, 5, 15):
        out.append((y, Q1))
    return out


class DartClient:
    def __init__(self, key: str, *, pause: float = 0.15, session: requests.Session | None = None):
        if not valid_key(key):
            raise DartError("DART_API_KEY 형식이 올바르지 않습니다 (발급받은 40자리 인증키여야 합니다)")
        self.key, self.pause = key, pause
        self.http = session or requests.Session()

    def _get(self, path: str, **params):
        res = self.http.get(f"{BASE}/{path}", params={"crtfc_key": self.key, **params}, timeout=20)
        res.raise_for_status()
        time.sleep(self.pause)
        return res

    def corp_codes(self) -> dict[str, str]:
        res = self._get("corpCode.xml")
        if not res.content.startswith(b"PK"):
            raise DartError(res.text[:200])
        return parse_corp_codes(res.content)

    def statement(self, corp_code: str, year: int, report: str) -> Statement | None:
        payload = self._get("fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=str(year), reprt_code=report).json()
        return parse_statement(payload, year, report)

    def investments(self, corp_code: str, year: int, report: str):
        """타법인 출자 현황 (ownership.py parses it)."""
        from .ownership import parse_investments
        return parse_investments(self._get("otrCprInvstmntSttus.json", corp_code=corp_code, bsns_year=str(year),
                                           reprt_code=report).json())

    def dividend(self, corp_code: str, year: int) -> int | None:
        return parse_dividend(self._get("alotMatter.json", corp_code=corp_code, bsns_year=str(year), reprt_code=ANNUAL).json())
