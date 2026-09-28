#!/usr/bin/env python3
"""Collect Nasdaq-100 company metadata and SEC EDGAR filing text.

The collector intentionally uses only Python's standard library.  It resolves the
current Nasdaq-100 securities from Nasdaq's official page, maps them to SEC CIKs,
collapses multiple share classes into companies, and downloads a balanced number
of filings per company.
"""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import gzip
import hashlib
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


NASDAQ100_URL = "https://indexes.nasdaq.com/Index/Weighting/NDX"
NASDAQ100_DATA_URL = "https://indexes.nasdaq.com/Index/WeightingData"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
SEC_ARCHIVE_BASE_URL = "https://www.sec.gov/Archives/edgar/data"

DEFAULT_FORMS = ("10-K", "10-Q", "8-K", "20-F", "40-F", "6-K")
DEFAULT_REQUEST_DELAY = 0.25  # four requests/second, below the SEC's 10 rps ceiling
DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 5
NASDAQ_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
)


class CollectorError(RuntimeError):
    """Raised when source data cannot be collected or safely validated."""


@dataclass(frozen=True)
class NasdaqSecurity:
    position: int
    name: str
    symbol: str


class JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_json_ld = False
        self._chunks: list[str] = []
        self.documents: list[Any] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if attributes.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._in_json_ld:
            return
        raw = "".join(self._chunks).strip()
        if raw:
            try:
                self.documents.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
        self._in_json_ld = False
        self._chunks = []


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        request_delay: float = DEFAULT_REQUEST_DELAY,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.user_agent = user_agent
        self.request_delay = max(0.0, request_delay)
        self.timeout = timeout
        self.retries = retries
        self._last_request_at = 0.0
        self._pace_lock = threading.Lock()

    def get_bytes(self, url: str, allow_404: bool = False) -> bytes | None:
        return self._request_bytes(url, allow_404=allow_404)

    def post_form_json(self, url: str, fields: dict[str, str], referer: str) -> Any:
        body = urllib.parse.urlencode(fields).encode("ascii")
        raw = self._request_bytes(
            url,
            data=body,
            extra_headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if raw is None:
            raise CollectorError(f"빈 응답: {url}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise CollectorError(f"JSON 응답 파싱 실패: {url}") from error

    def _request_bytes(
        self,
        url: str,
        allow_404: bool = False,
        data: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes | None:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._pace()
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "application/json,text/plain,text/html,*/*",
                "Accept-Encoding": "gzip",
            }
            headers.update(extra_headers or {})
            request = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST" if data is not None else "GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                    return body
            except urllib.error.HTTPError as error:
                if error.code == 404 and allow_404:
                    return None
                if error.code == 403:
                    raise CollectorError(
                        "SEC/Nasdaq 서버가 요청을 거부했습니다(HTTP 403). "
                        "SEC_EDGAR_USER_AGENT에 앱 이름과 실제 연락 이메일을 설정하세요. "
                        f"URL={url}"
                    ) from error
                last_error = error
                if error.code != 429 and error.code < 500:
                    break
                retry_after = error.headers.get("Retry-After") if error.headers else None
                wait_seconds = _retry_wait_seconds(retry_after, attempt)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                last_error = error
                wait_seconds = min(30, 2**attempt)

            if attempt < self.retries:
                print(
                    f"    [재시도] {type(last_error).__name__}: "
                    f"{wait_seconds:.1f}초 후 재시도 ({attempt}/{self.retries})",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)

        if allow_404:
            return None
        raise CollectorError(f"요청 실패: {url} ({last_error})")

    def get_json(self, url: str) -> Any:
        raw = self.get_bytes(url)
        if raw is None:
            raise CollectorError(f"빈 응답: {url}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise CollectorError(f"JSON 응답 파싱 실패: {url}") from error

    def get_text(self, url: str) -> str:
        raw = self.get_bytes(url)
        if raw is None:
            raise CollectorError(f"빈 응답: {url}")
        return raw.decode("utf-8", errors="replace")

    def _pace(self) -> None:
        with self._pace_lock:
            remaining = self.request_delay - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request_at = time.monotonic()


def _retry_wait_seconds(retry_after: str | None, attempt: int) -> float:
    if retry_after:
        try:
            return min(60.0, max(1.0, float(retry_after)))
        except ValueError:
            pass
    return float(min(30, 2**attempt))


def parse_nasdaq_securities(html: str) -> list[NasdaqSecurity]:
    parser = JsonLdParser()
    parser.feed(html)

    item_lists: list[dict[str, Any]] = []
    for document in parser.documents:
        nodes: Iterable[Any]
        if isinstance(document, dict) and isinstance(document.get("@graph"), list):
            nodes = document["@graph"]
        elif isinstance(document, list):
            nodes = document
        else:
            nodes = [document]
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "ItemList":
                item_lists.append(node)

    candidates: list[NasdaqSecurity] = []
    for item_list in item_lists:
        elements = item_list.get("itemListElement")
        if not isinstance(elements, list):
            continue
        parsed: list[NasdaqSecurity] = []
        for fallback_position, element in enumerate(elements, 1):
            if not isinstance(element, dict):
                continue
            name = clean_text(element.get("name"))
            symbol = clean_text(element.get("description")).upper()
            position = element.get("position", fallback_position)
            if name:
                parsed.append(NasdaqSecurity(int(position), name, symbol))
        if len(parsed) > len(candidates):
            candidates = parsed

    if not (100 <= len(candidates) <= 110):
        raise CollectorError(
            f"Nasdaq 공식 페이지에서 예상한 100~110개 구성종목 대신 {len(candidates)}개를 찾았습니다."
        )
    return sorted(candidates, key=lambda security: security.position)


def parse_nasdaq_as_of(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    match = re.search(r"DATA AS OF\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
    if not match:
        raise CollectorError("Nasdaq Global Index Watch에서 구성 기준일을 찾지 못했습니다.")
    parsed = datetime.strptime(match.group(1), "%m/%d/%Y").date()
    return parsed.isoformat()


def parse_nasdaq_weighting_payload(payload: Any) -> list[NasdaqSecurity]:
    rows = payload.get("aaData") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise CollectorError("Nasdaq WeightingData 응답에 aaData가 없습니다.")
    securities: list[NasdaqSecurity] = []
    for position, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        name = clean_text(row.get("Name"))
        symbol = clean_text(row.get("Symbol")).upper()
        if name and symbol:
            securities.append(NasdaqSecurity(position, name, symbol))
    if not (100 <= len(securities) <= 110):
        raise CollectorError(
            f"Nasdaq WeightingData에서 예상한 100~110개 증권 대신 {len(securities)}개를 찾았습니다."
        )
    return securities


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_company_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.upper().replace("&", " AND ")
    value = re.sub(r"\b(?:CL(?:ASS)?\s+[A-Z]|CMN|STK|CAP|ADS|OS|ORD|SHS?)\b", " ", value)
    value = re.sub(
        r"\b(?:INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|LIMITED|LTD|PLC|LLC|NV|N V|SA|SE)\b",
        " ",
        value,
    )
    return re.sub(r"[^A-Z0-9]+", "", value)


def parse_sec_registry(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list):
        raise CollectorError("SEC 회사 목록의 fields가 올바르지 않습니다.")
    if not isinstance(payload.get("data"), list):
        raise CollectorError("SEC 회사 목록의 data가 올바르지 않습니다.")

    field_indexes = {str(name): index for index, name in enumerate(payload["fields"])}
    required = {"cik", "name", "ticker", "exchange"}
    if not required.issubset(field_indexes):
        raise CollectorError(f"SEC 회사 목록에 필수 필드가 없습니다: {sorted(required - field_indexes.keys())}")

    rows: list[dict[str, Any]] = []
    for raw_row in payload["data"]:
        if not isinstance(raw_row, list):
            continue
        try:
            cik = str(int(raw_row[field_indexes["cik"]]))
            name = clean_text(raw_row[field_indexes["name"]])
            ticker = clean_text(raw_row[field_indexes["ticker"]]).upper()
            exchange = clean_text(raw_row[field_indexes["exchange"]])
        except (IndexError, TypeError, ValueError):
            continue
        if cik and name and ticker:
            rows.append({"cik": cik, "name": name, "ticker": ticker, "exchange": exchange})
    if len(rows) < 5_000:
        raise CollectorError(f"SEC 회사 목록이 비정상적으로 작습니다: {len(rows)}개")
    return rows


def build_company_universe(
    securities: list[NasdaqSecurity],
    registry_rows: list[dict[str, Any]],
    expected_companies: int | None = 100,
) -> list[dict[str, Any]]:
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_cik: dict[str, list[dict[str, Any]]] = {}
    for row in registry_rows:
        by_ticker.setdefault(row["ticker"], []).append(row)
        by_name.setdefault(normalized_company_name(row["name"]), []).append(row)
        by_cik.setdefault(row["cik"], []).append(row)

    companies: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    for security in securities:
        matches = by_ticker.get(security.symbol, []) if security.symbol else []
        if not matches:
            matches = by_name.get(normalized_company_name(security.name), [])
        if not matches:
            unresolved.append(f"{security.symbol or '?'} ({security.name})")
            continue
        match = sorted(matches, key=lambda row: (row["exchange"].lower() != "nasdaq", row["ticker"]))[0]
        cik = match["cik"]
        company = companies.setdefault(
            cik,
            {
                "cik": cik,
                "cik10": cik.zfill(10),
                "sec_name": match["name"],
                "nasdaq_symbols": [],
                "nasdaq_names": [],
                "constituent_positions": [],
                "sec_tickers": sorted({row["ticker"] for row in by_cik[cik]}),
                "sec_exchanges": sorted({row["exchange"] for row in by_cik[cik] if row["exchange"]}),
            },
        )
        symbol = security.symbol or match["ticker"]
        if symbol not in company["nasdaq_symbols"]:
            company["nasdaq_symbols"].append(symbol)
        if security.name not in company["nasdaq_names"]:
            company["nasdaq_names"].append(security.name)
        company["constituent_positions"].append(security.position)

    if unresolved:
        raise CollectorError("SEC CIK로 매핑하지 못한 종목: " + ", ".join(unresolved))

    universe = sorted(companies.values(), key=lambda company: min(company["constituent_positions"]))
    if expected_companies is not None and len(universe) != expected_companies:
        raise CollectorError(
            f"Nasdaq 구성종목을 CIK로 합친 기업 수가 {len(universe)}개입니다 "
            f"(예상 {expected_companies}개). 구성 변경 또는 매핑 오류를 확인하세요."
        )
    for index, company in enumerate(universe, 1):
        company["company_order"] = index
    return universe


def rows_from_filings_block(block: Any) -> list[dict[str, Any]]:
    if not isinstance(block, dict):
        return []
    accession_numbers = block.get("accessionNumber", [])
    if not isinstance(accession_numbers, list):
        return []
    fields = (
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "act",
        "form",
        "fileNumber",
        "filmNumber",
        "items",
        "size",
        "isXBRL",
        "isInlineXBRL",
        "primaryDocument",
        "primaryDocDescription",
    )
    rows: list[dict[str, Any]] = []
    for index, accession_number in enumerate(accession_numbers):
        if not accession_number:
            continue
        row: dict[str, Any] = {}
        for field in fields:
            values = block.get(field, [])
            row[field] = values[index] if isinstance(values, list) and index < len(values) else ""
        rows.append(row)
    return rows


def form_is_allowed(form: str, allowed_forms: set[str] | None) -> bool:
    if allowed_forms is None:
        return True
    normalized = form.upper().strip()
    base = normalized[:-2] if normalized.endswith("/A") else normalized
    return normalized in allowed_forms or base in allowed_forms


def select_filings(
    rows: list[dict[str, Any]],
    allowed_forms: set[str] | None,
    start_date: str | None,
    end_date: str | None,
    count: int | None,
) -> list[dict[str, Any]]:
    selected = []
    for row in rows:
        filing_date = str(row.get("filingDate", ""))
        if start_date and filing_date < start_date:
            continue
        if end_date and filing_date > end_date:
            continue
        if not form_is_allowed(str(row.get("form", "")), allowed_forms):
            continue
        selected.append(row)
    selected.sort(
        key=lambda row: (
            str(row.get("filingDate", "")),
            str(row.get("acceptanceDateTime", "")),
            str(row.get("accessionNumber", "")),
        ),
        reverse=True,
    )
    return selected if count is None else selected[:count]


def load_company_filings(
    client: HttpClient,
    submission: dict[str, Any],
    allowed_forms: set[str] | None,
    start_date: str | None,
    end_date: str | None,
    count: int | None,
) -> list[dict[str, Any]]:
    filings = submission.get("filings", {})
    rows = rows_from_filings_block(filings.get("recent", {}))
    selected = select_filings(rows, allowed_forms, start_date, end_date, count)
    if count is not None and len(selected) >= count:
        return selected

    for page in filings.get("files", []):
        name = page.get("name") if isinstance(page, dict) else None
        if not name:
            continue
        filing_from = clean_text(page.get("filingFrom"))
        filing_to = clean_text(page.get("filingTo"))
        if start_date and filing_to and filing_to < start_date:
            continue
        if end_date and filing_from and filing_from > end_date:
            continue
        page_payload = client.get_json(SEC_SUBMISSIONS_PAGE_URL.format(name=name))
        rows.extend(rows_from_filings_block(page_payload))
        selected = select_filings(rows, allowed_forms, start_date, end_date, count)
        if count is not None and len(selected) >= count:
            break
    return selected


def company_metadata(company: dict[str, Any], submission: dict[str, Any]) -> dict[str, Any]:
    return {
        "companyOrder": company["company_order"],
        "cik": company["cik"],
        "cik10": company["cik10"],
        "name": submission.get("name") or company["sec_name"],
        "entityType": submission.get("entityType", ""),
        "sic": submission.get("sic", ""),
        "sicDescription": submission.get("sicDescription", ""),
        "ownerOrg": submission.get("ownerOrg", ""),
        "ein": submission.get("ein", ""),
        "description": submission.get("description", ""),
        "website": submission.get("website", ""),
        "investorWebsite": submission.get("investorWebsite", ""),
        "category": submission.get("category", ""),
        "fiscalYearEnd": submission.get("fiscalYearEnd", ""),
        "stateOfIncorporation": submission.get("stateOfIncorporation", ""),
        "stateOfIncorporationDescription": submission.get("stateOfIncorporationDescription", ""),
        "phone": submission.get("phone", ""),
        "flags": submission.get("flags", ""),
        "tickers": submission.get("tickers") or company["sec_tickers"],
        "exchanges": submission.get("exchanges") or company["sec_exchanges"],
        "nasdaq100Symbols": company["nasdaq_symbols"],
        "nasdaq100Names": company["nasdaq_names"],
        "mailingAddress": submission.get("addresses", {}).get("mailing", {}),
        "businessAddress": submission.get("addresses", {}).get("business", {}),
        "formerNames": submission.get("formerNames", []),
        "submissionsUrl": SEC_SUBMISSIONS_URL.format(cik10=company["cik10"]),
    }


def complete_submission_urls(cik: str, accession_number: str) -> list[str]:
    cik_plain = str(int(cik))
    cik10 = cik_plain.zfill(10)
    accession_no_dash = accession_number.replace("-", "")
    return [
        f"{SEC_ARCHIVE_BASE_URL}/{cik_plain}/{accession_no_dash}/{accession_number}.txt",
        f"{SEC_ARCHIVE_BASE_URL}/{cik10}/{accession_no_dash}/{accession_number}.txt",
        f"{SEC_ARCHIVE_BASE_URL}/{cik_plain}/{accession_number}.txt",
        f"{SEC_ARCHIVE_BASE_URL}/{cik10}/{accession_number}.txt",
    ]


def download_complete_submission(
    client: HttpClient, cik: str, accession_number: str
) -> tuple[bytes, str]:
    for url in complete_submission_urls(cik, accession_number):
        body = client.get_bytes(url, allow_404=True)
        if body is not None:
            if len(body) < 100:
                raise CollectorError(f"공시 원문 응답이 너무 작습니다: {url} ({len(body)} bytes)")
            return body, url
    raise CollectorError(f"공시 원문을 찾지 못했습니다: CIK={cik}, accession={accession_number}")


def safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "unknown"


def filing_relative_path(company: dict[str, Any], filing: dict[str, Any]) -> Path:
    accession_number = str(filing["accessionNumber"])
    symbols = "_".join(company["nasdaq_symbols"])
    form = safe_path_part(str(filing.get("form", "unknown")))
    filename = f"{filing.get('filingDate', 'unknown')}_{form}_{accession_number}.txt"
    return Path("raw") / f"{company['company_order']:03d}_{safe_path_part(symbols)}" / filename


def selected_filing_metadata(company: dict[str, Any], filing: dict[str, Any]) -> dict[str, Any]:
    accession_number = str(filing["accessionNumber"])
    return {
        "companyOrder": company["company_order"],
        "company": company["sec_name"],
        "cik": company["cik"],
        "symbols": company["nasdaq_symbols"],
        **filing,
        "sourceUrl": complete_submission_urls(company["cik"], accession_number)[0],
        "localPath": filing_relative_path(company, filing).as_posix(),
    }


def download_filing_record(
    client: HttpClient,
    output_dir: Path,
    company: dict[str, Any],
    filing: dict[str, Any],
) -> dict[str, Any]:
    accession_number = str(filing["accessionNumber"])
    relative_path = filing_relative_path(company, filing)
    local_path = output_dir / relative_path
    if local_path.exists() and local_path.stat().st_size >= 100:
        body = local_path.read_bytes()
        source_url = complete_submission_urls(company["cik"], accession_number)[0]
        reused = True
    else:
        body, source_url = download_complete_submission(client, company["cik"], accession_number)
        write_bytes_atomic(local_path, body)
        reused = False
    return {
        "companyOrder": company["company_order"],
        "company": company["sec_name"],
        "cik": company["cik"],
        "symbols": company["nasdaq_symbols"],
        **filing,
        "sourceUrl": source_url,
        "localPath": relative_path.as_posix(),
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "reused": reused,
    }


def write_bytes_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(body)
    temporary.replace(path)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def parse_forms(raw: str) -> set[str] | None:
    if raw.strip().lower() == "all":
        return None
    forms = {value.strip().upper() for value in raw.split(",") if value.strip()}
    if not forms:
        raise argparse.ArgumentTypeError("--forms는 쉼표로 구분한 양식 또는 all이어야 합니다.")
    return forms


def valid_iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError("날짜 형식은 YYYY-MM-DD여야 합니다.") from error


def validate_user_agent(value: str) -> str:
    value = clean_text(value)
    if not value or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise CollectorError(
            "SEC_EDGAR_USER_AGENT 환경변수 또는 --user-agent에 "
            "'앱이름 실제이메일'을 설정해야 합니다."
        )
    if "example.com" in value.lower():
        raise CollectorError("SEC User-Agent에는 example.com이 아닌 실제 연락 이메일을 사용하세요.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="나스닥-100 100개 기업정보와 SEC EDGAR 공시 원문을 수집합니다."
    )
    parser.add_argument(
        "--out-dir",
        default=f"output/sec-edgar-nasdaq100-{date.today():%Y%m%d}",
        help="결과 폴더",
    )
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_EDGAR_USER_AGENT", ""),
        help="SEC 요청 식별자(기본: SEC_EDGAR_USER_AGENT 환경변수)",
    )
    parser.add_argument(
        "--forms",
        default=",".join(DEFAULT_FORMS),
        help="대상 양식. 쉼표 구분 또는 all (기본: 핵심 정기/수시 공시)",
    )
    parser.add_argument(
        "--per-company",
        type=int,
        default=1,
        help="기업별 공시 건수. 0이면 기간 내 전부 (기본: 1)",
    )
    parser.add_argument(
        "--total-limit",
        type=int,
        default=100,
        help="전체 공시 최대 건수. 0이면 제한 없음 (기본: 100)",
    )
    parser.add_argument("--start-date", type=valid_iso_date, default=None, help="접수일 시작 YYYY-MM-DD")
    parser.add_argument("--end-date", type=valid_iso_date, default=None, help="접수일 종료 YYYY-MM-DD")
    parser.add_argument(
        "--company-limit",
        type=int,
        default=None,
        help="시험 실행용 기업 수 제한. 전체 유니버스 검증 후 앞에서부터 제한",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY,
        help="모든 HTTP 요청 사이 대기 초 (기본: 0.25)",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="기업정보와 대상 공시목록만 만들고 원문은 받지 않음",
    )
    parser.add_argument(
        "--download-workers",
        type=int,
        default=4,
        help="동시 원문 다운로드 수 (기본: 4, 최대: 8)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="원문 진행상황 출력 간격 (기본: 25건)",
    )
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.per_company < 0:
        raise CollectorError("--per-company는 0 이상이어야 합니다.")
    if args.total_limit < 0:
        raise CollectorError("--total-limit는 0 이상이어야 합니다.")
    if args.company_limit is not None and args.company_limit < 1:
        raise CollectorError("--company-limit는 1 이상이어야 합니다.")
    if args.request_delay < 0.1:
        raise CollectorError("SEC 공정접근 정책을 위해 --request-delay는 0.1초 이상이어야 합니다.")
    if not (1 <= args.download_workers <= 8):
        raise CollectorError("--download-workers는 1~8이어야 합니다.")
    if args.progress_every < 1:
        raise CollectorError("--progress-every는 1 이상이어야 합니다.")
    if args.start_date and args.end_date and args.start_date > args.end_date:
        raise CollectorError("--start-date는 --end-date보다 늦을 수 없습니다.")

    user_agent = validate_user_agent(args.user_agent)
    allowed_forms = parse_forms(args.forms)
    output_dir = Path(args.out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    client = HttpClient(user_agent=user_agent, request_delay=args.request_delay)
    nasdaq_client = HttpClient(
        user_agent=NASDAQ_BROWSER_USER_AGENT,
        request_delay=args.request_delay,
    )
    started_at = datetime.now(timezone.utc)

    print("[1/4] Nasdaq Global Index Watch에서 현재 구성종목을 읽는 중...")
    weighting_page = nasdaq_client.get_text(NASDAQ100_URL)
    nasdaq_as_of = parse_nasdaq_as_of(weighting_page)
    weighting_payload = nasdaq_client.post_form_json(
        NASDAQ100_DATA_URL,
        {
            "id": "NDX",
            "tradeDate": f"{nasdaq_as_of}T00:00:00.000",
            "timeOfDay": "SOD",
        },
        referer=NASDAQ100_URL,
    )
    securities = parse_nasdaq_weighting_payload(weighting_payload)
    print(f"      기준일 {nasdaq_as_of}, 구성증권 {len(securities)}개")

    print("[2/4] SEC 공식 티커 목록으로 CIK를 검증하는 중...")
    registry = parse_sec_registry(client.get_json(SEC_TICKERS_URL))
    full_universe = build_company_universe(securities, registry, expected_companies=None)
    if not (95 <= len(full_universe) <= 105):
        raise CollectorError(f"CIK 기준 기업 수가 비정상적입니다: {len(full_universe)}개")
    print(f"      CIK 기준 기업 {len(full_universe)}개 (복수 주식종류 통합)")
    universe = full_universe
    if args.company_limit is not None:
        universe = universe[: args.company_limit]

    company_rows: list[dict[str, Any]] = []
    selected_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    selection_failures: list[dict[str, Any]] = []
    print(f"[3/4] {len(universe)}개 기업정보와 공시 목록을 수집하는 중...")
    per_company_count = None if args.per_company == 0 else args.per_company
    for index, company in enumerate(universe, 1):
        submission_url = SEC_SUBMISSIONS_URL.format(cik10=company["cik10"])
        try:
            submission = client.get_json(submission_url)
            if not isinstance(submission, dict):
                raise CollectorError("submissions 응답이 객체가 아닙니다.")
            company_rows.append(company_metadata(company, submission))
            filings = load_company_filings(
                client,
                submission,
                allowed_forms,
                args.start_date,
                args.end_date,
                per_company_count,
            )
            if per_company_count is not None and len(filings) < per_company_count:
                selection_failures.append(
                    {
                        "stage": "selection",
                        "cik": company["cik"],
                        "symbols": company["nasdaq_symbols"],
                        "error": f"조건에 맞는 공시 {len(filings)}건 (요청 {per_company_count}건)",
                    }
                )
            for filing in filings:
                selected_rows.append((company, filing))
        except Exception as error:  # keep the other 99 companies collectible
            selection_failures.append(
                {
                    "stage": "company_metadata",
                    "cik": company["cik"],
                    "symbols": company["nasdaq_symbols"],
                    "error": str(error),
                }
            )
        if index == 1 or index == len(universe) or index % 10 == 0:
            print(f"      [{index:03d}/{len(universe):03d}] {'/'.join(company['nasdaq_symbols'])}")

    available_filing_count = len(selected_rows)
    if args.total_limit and len(selected_rows) > args.total_limit:
        selected_rows.sort(
            key=lambda pair: (
                str(pair[1].get("filingDate", "")),
                str(pair[1].get("acceptanceDateTime", "")),
                str(pair[1].get("accessionNumber", "")),
            ),
            reverse=True,
        )
        selected_rows = selected_rows[: args.total_limit]

    write_json_atomic(output_dir / "companies.json", company_rows)
    selected_metadata_rows = [
        selected_filing_metadata(company, filing) for company, filing in selected_rows
    ]
    write_jsonl_atomic(output_dir / "selected_filings.jsonl", selected_metadata_rows)
    if args.metadata_only:
        print(f"[4/4] 메타데이터 전용: 대상 공시 {len(selected_rows)}건 목록 저장 완료")
    else:
        print(f"[4/4] 선택된 공시 {len(selected_rows)}건의 원문을 받는 중...")
    filing_rows: list[dict[str, Any]] = []
    download_failures: list[dict[str, Any]] = []
    download_rows = [] if args.metadata_only else selected_rows
    if download_rows:
        futures: dict[Future[dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=args.download_workers) as executor:
            for company, filing in download_rows:
                future = executor.submit(download_filing_record, client, output_dir, company, filing)
                futures[future] = (company, filing)
            for index, future in enumerate(as_completed(futures), 1):
                company, filing = futures[future]
                accession_number = str(filing["accessionNumber"])
                try:
                    record = future.result()
                    filing_rows.append(record)
                    if index == 1 or index == len(download_rows) or index % args.progress_every == 0:
                        status = "재사용" if record["reused"] else "완료"
                        downloaded_bytes = sum(row["bytes"] for row in filing_rows)
                        print(
                            f"      [{index:05d}/{len(download_rows):05d}] "
                            f"성공 {len(filing_rows):05d} 실패 {len(download_failures):03d} "
                            f"{downloaded_bytes / (1024**3):.2f}GB "
                            f"{'/'.join(company['nasdaq_symbols'])} {filing.get('form', '')} {status}"
                        )
                except Exception as error:
                    failure = {
                        "stage": "download",
                        "cik": company["cik"],
                        "symbols": company["nasdaq_symbols"],
                        "accessionNumber": accession_number,
                        "error": str(error),
                    }
                    download_failures.append(failure)
                    print(
                        f"      [{index:05d}/{len(download_rows):05d}] "
                        f"{'/'.join(company['nasdaq_symbols'])} 실패: {error}",
                        file=sys.stderr,
                    )

    filing_rows.sort(
        key=lambda row: (
            int(row["companyOrder"]),
            str(row.get("filingDate", "")),
            str(row.get("accessionNumber", "")),
        )
    )

    failures = selection_failures + download_failures
    write_jsonl_atomic(output_dir / "filings.jsonl", filing_rows)
    write_jsonl_atomic(output_dir / "failures.jsonl", failures)

    completed_at = datetime.now(timezone.utc)
    expected_filings = (
        available_filing_count if per_company_count is None else len(universe) * per_company_count
    )
    if args.total_limit:
        expected_filings = min(expected_filings, args.total_limit)
    manifest = {
        "dataset": "sec-edgar-nasdaq100",
        "startedAt": started_at.isoformat(),
        "completedAt": completed_at.isoformat(),
        "nasdaqConstituentSource": NASDAQ100_URL,
        "nasdaqConstituentDataSource": NASDAQ100_DATA_URL,
        "nasdaqAsOf": nasdaq_as_of,
        "secCompanyRegistrySource": SEC_TICKERS_URL,
        "secSubmissionsTemplate": SEC_SUBMISSIONS_URL,
        "forms": "all" if allowed_forms is None else sorted(allowed_forms),
        "startDate": args.start_date,
        "endDate": args.end_date,
        "requestDelaySeconds": args.request_delay,
        "downloadWorkers": args.download_workers,
        "securityCount": len(securities),
        "officialCompanyCountByCik": len(full_universe),
        "companyCount": len(company_rows),
        "expectedCompanyCount": len(universe),
        "expectedFilingCount": expected_filings,
        "availableFilingCount": available_filing_count,
        "selectedFilingCount": len(selected_rows),
        "filingCount": len(filing_rows),
        "metadataOnly": bool(args.metadata_only),
        "failureCount": len(failures),
        "totalBytes": sum(row["bytes"] for row in filing_rows),
    }
    write_json_atomic(output_dir / "manifest.json", manifest)
    actual_start = min((row["filingDate"] for row in selected_rows), default="없음")
    actual_end = max((row["filingDate"] for row in selected_rows), default="없음")
    readme = f"""# Nasdaq-100 SEC EDGAR 수집 결과

- 수집 완료(UTC): {completed_at.isoformat()}
- Nasdaq 구성 기준일: {nasdaq_as_of}
- 요청 날짜 범위: {args.start_date or '제한 없음'} ~ {args.end_date or '제한 없음'}
- 실제 공시일 범위: {actual_start} ~ {actual_end}
- 공식 Nasdaq 구성종목: {len(securities)}개 종목코드
- CIK 기준 현재 구성기업: {len(full_universe)}개
- 이번 실행 대상 기업: {len(universe)}개
- 확보한 기업정보: {len(company_rows)}개
- 선택한 공시: {len(selected_rows)}개
- 확보한 공시 원문: {len(filing_rows)}개 / 목표 {expected_filings}개
- 원문 총크기: {manifest['totalBytes']:,} bytes ({manifest['totalBytes'] / 1024**3:.2f} GiB)
- 실패: {len(failures)}개
- 양식: {('all' if allowed_forms is None else ', '.join(sorted(allowed_forms)))}

`companies.json`은 SEC 기업정보, `selected_filings.jsonl`은 수집 대상 목록,
`filings.jsonl`은 확보한 공시 메타데이터와 원문 상대경로,
`raw/`는 SEC Complete submission text 원문이다. `manifest.json`에는 범위와 건수,
`failures.jsonl`에는 재시도 대상이 기록된다. 각 원문의 무결성은 `sha256`으로 확인할 수 있다.

출처: [Nasdaq-100 Companies]({NASDAQ100_URL}),
[SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[SEC Developer Resources](https://www.sec.gov/about/developer-resources)
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")

    actual_filing_count = len(selected_rows) if args.metadata_only else len(filing_rows)
    if len(company_rows) != len(universe) or actual_filing_count != expected_filings:
        raise CollectorError(
            f"목표 미달: 기업 {len(company_rows)}/{len(universe)}, "
            f"공시 {actual_filing_count}/{expected_filings}. failures.jsonl을 확인하세요."
        )
    return manifest


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args()
    try:
        manifest = run(args)
    except (CollectorError, argparse.ArgumentTypeError) as error:
        print(f"[오류] {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
