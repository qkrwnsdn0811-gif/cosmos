"""Resume a single-key OpenDART collection for a fixed common-stock rank range.

The list cache retains all report types and corrections. Only the two personal
officer/shareholder report titles are excluded from CSV and original documents.
No third-party dependency is required. --plan-only collects lists without
requesting original documents. All quotas are conservative local estimates.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from scripts.dart_documents import parse_document_zip  # noqa: E402

KST = timezone(timedelta(hours=9))
BASE_URL = "https://opendart.fss.or.kr/api/"
STOP_STATUSES = {"010", "011", "012", "020", "901", "800"}
UNAVAILABLE_STATUSES = {"013", "014"}
DEFAULT_UNIVERSE = PROJECT / "config/universe-20260907.jsonl"
DEFAULT_SOURCES = []  # Existing collection caches are opt-in via --reuse-source.
CSV_FIELDS = ["rcept_no", "corp_code", "corp_name", "stock_code", "universe_rank",
              "report_nm", "rcept_dt", "flr_nm", "corp_cls", "rm", "source_url"]


def now():
    return datetime.now(KST).isoformat()


def retry_file_io(operation, *args, **kwargs):
    """Retry transient Windows sharing violations for at most 1.55 seconds.

    This helper is used only for local file operations. Permanent permission
    failures retain their original exception type and message.
    """
    delays = (0.05, 0.1, 0.2, 0.4, 0.8)
    for attempt in range(len(delays) + 1):
        try:
            return operation(*args, **kwargs)
        except PermissionError:
            if attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def read_json(path, default=None):
    try:
        return json.loads(retry_file_io(Path(path).read_text, encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    retry_file_io(temporary.write_text, json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    retry_file_io(os.replace, temporary, path)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with retry_file_io(temporary.open, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    retry_file_io(os.replace, temporary, path)


def load_key(key_file):
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        try:
            contents = Path(key_file).read_text(encoding="utf-8-sig")
        except OSError:
            raise ValueError("DART_API_KEY is missing; set the environment or --key-file") from None
        match = re.search(r"^\s*(?:export\s+)?DART_API_KEY\s*=\s*(.*?)\s*$", contents, re.M)
        key = match.group(1).strip().strip("\"'") if match else ""
    if not re.fullmatch(r"[A-Za-z0-9]{40}", key):
        raise ValueError("DART_API_KEY must be a 40-character key")
    return key


class CollectorError(RuntimeError):
    """Only fixed, credential-free error codes are stored in this exception."""


class RequestStopped(CollectorError):
    pass


class FileLock:
    """OS-backed nonblocking file lock, released even when a process crashes.

    The lock file is deliberately retained: unlinking it can introduce a race
    between processes holding handles to different files with the same name.
    """
    def __init__(self, path, blocking=False):
        self.path = Path(path)
        self.blocking = blocking
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        while True:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if not self.blocking:
                    self.handle.close()
                    self.handle = None
                    raise CollectorError("another_collector_holds_the_output_lock") from None
                time.sleep(0.05)

    def __exit__(self, *_):
        if self.handle is not None:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class Api:
    def __init__(self, key, output, interval=0.3, daily_limit=19500,
                 max_new_requests=None, ledger_dir=None, initial_used=0):
        self._key = key
        self.output = Path(output)
        self.interval = interval
        self.daily_limit = daily_limit
        self.max_new_requests = max_new_requests
        self.initial_used = initial_used
        self.fingerprint = hashlib.sha256(key.encode()).hexdigest()[:16]
        ledger_dir = Path(ledger_dir or PROJECT / "output/opendart-rank-request-ledgers")
        ledger_dir.mkdir(parents=True, exist_ok=True)
        self.quota_path = ledger_dir / (self.fingerprint + ".json")
        self.ledger_path = ledger_dir / (self.fingerprint + ".jsonl")
        self.quota_lock = ledger_dir / (self.fingerprint + ".lock")
        self.lock = threading.RLock()
        self.used = 0
        self.cache_hits = 0
        self.stop_reason = ""
        self.run_id = now()
        with FileLock(self.quota_lock, blocking=True):
            state = self._quota_state()
            # Explicit externally used allowance is a floor for this key/day,
            # not an increment repeated on every resumed invocation.
            if state["count"] < initial_used:
                state["count"] = initial_used
                state["external_usage_floor"] = initial_used
                write_json(self.quota_path, state)
            self.daily_used = state["count"]

    def _quota_state(self):
        day = datetime.now(KST).date().isoformat()
        state = read_json(self.quota_path, {})
        if state.get("date") != day:
            return {"date": day, "count": 0, "last_request_epoch": state.get("last_request_epoch", 0)}
        return state

    def stop(self, reason):
        with self.lock:
            if not self.stop_reason:
                self.stop_reason = reason

    def check_status(self, status):
        if status in STOP_STATUSES:
            self.stop("opendart_status_" + status)
            raise RequestStopped(self.stop_reason)

    def reserve(self, endpoint, params):
        with self.lock:
            if self.stop_reason:
                raise RequestStopped(self.stop_reason)
            if self.max_new_requests is not None and self.used >= self.max_new_requests:
                self.stop("local_run_request_budget_reached")
                raise RequestStopped(self.stop_reason)
            # The key-specific lock also coordinates separate output runs.
            with FileLock(self.quota_lock, blocking=True):
                state = self._quota_state()
                self.daily_used = state["count"]
                if self.daily_used >= self.daily_limit:
                    self.stop("local_daily_request_limit_reached")
                    raise RequestStopped(self.stop_reason)
                delay = max(0, self.interval - (time.time() - state.get("last_request_epoch", 0)))
                time.sleep(min(delay, self.interval))
                state.update(count=state["count"] + 1, last_request_epoch=time.time())
                # Persist the count before dispatch. A crash can overcount one
                # reservation, but cannot erase an already dispatched request.
                write_json(self.quota_path, state)
                self.used += 1
                self.daily_used = state["count"]
                with self.ledger_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"at": now(), "run_id": self.run_id,
                                             "endpoint": endpoint, "params": params}) + "\n")
                    handle.flush()

    def request(self, endpoint, params):
        for attempt in range(3):
            self.reserve(endpoint, params)
            query = urllib.parse.urlencode({"crtfc_key": self._key, **params})
            request = urllib.request.Request(BASE_URL + endpoint + "?" + query,
                                             headers={"User-Agent": "OpenDART-rank-collector/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == 2:
                    # urllib exception messages can contain the secret URL.
                    raise CollectorError("network_error_" + type(exc).__name__) from None
                time.sleep(attempt + 1)

    def json(self, endpoint, params):
        signature = hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()[:24]
        cached = self.output / "raw/list_responses" / (signature + ".json")
        previous = read_json(cached)
        if previous and previous.get("response", {}).get("status") in {"000", "013"}:
            self.cache_hits += 1
            return previous["response"]
        try:
            result = json.loads(self.request(endpoint, params))
        except (ValueError, UnicodeError):
            raise CollectorError("invalid_json_response") from None
        # Raw API responses contain no request URL or key.
        write_json(cached, {"fetched_at": now(), "endpoint": endpoint,
                            "params": params, "response": result})
        status = str(result.get("status", "missing_status"))
        self.check_status(status)
        if status not in {"000", "013"}:
            raise CollectorError("opendart_status_" + (status if re.fullmatch(r"\d{3}", status) else "unknown"))
        return result


def normalize_report_type(title):
    # NFKC maps Korean compatibility araea U+318D to U+119E, so fold
    # middle-dot spellings before normalization as well as after it.
    title = (title or "").replace("ㆍ", "·").replace("ᆞ", "·")
    title = unicodedata.normalize("NFKC", title)
    # DART uses both punctuation variants and several correction wrappers.
    for pattern in (r"\[[^\]]*\]", r"\([^)]*\)", r"【[^】]*】", r"〔[^〕]*〕"):
        title = re.sub(pattern, "", title)
    title = re.sub(r"\s+", "", title)
    return title.replace("ㆍ", "·").replace("・", "·").replace("･", "·")


EXCLUDED_TYPES = {
    "임원·주요주주특정증권등소유상황보고서": "D002",
    "임원·주요주주특정증권등거래계획보고서": "D005",
}


def exclusion_code(row):
    return EXCLUDED_TYPES.get(normalize_report_type(row.get("report_nm", "")))


def load_universe(path, rank_from, rank_to):
    with Path(path).open(encoding="utf-8-sig") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    selected = sorted((row for row in rows if rank_from <= int(row["universe_rank"]) <= rank_to),
                      key=lambda row: int(row["universe_rank"]))
    if [int(row["universe_rank"]) for row in selected] != list(range(rank_from, rank_to + 1)):
        raise ValueError("Requested common-stock ranks are missing or duplicated in the universe")
    for row in selected:
        if not re.fullmatch(r"\d{8}", row.get("corp_code", "")) or not re.fullmatch(r"\d{6}", row.get("stock_code", "")):
            raise ValueError("Universe requires valid corp_code and stock_code values")
    return selected


def date_shift(value, days):
    return (datetime.strptime(value, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def uncovered_windows(begin, end, intervals):
    windows = [(begin, end)]
    for left, right in sorted(intervals):
        next_windows = []
        for start, stop in windows:
            if right < start or left > stop:
                next_windows.append((start, stop))
            else:
                if start < left:
                    next_windows.append((start, date_shift(left, -1)))
                if right < stop:
                    next_windows.append((date_shift(right, 1), stop))
        windows = next_windows
    return windows


def unique_rows(rows, company, begin, end):
    unique = {}
    for row in rows:
        receipt = row.get("rcept_no", "")
        if not re.fullmatch(r"\d{14}", receipt):
            raise CollectorError("invalid_receipt_in_list")
        if begin <= row.get("rcept_dt", "") <= end:
            unique[receipt] = {**row, "stock_code": company["stock_code"],
                               "universe_rank": company["universe_rank"],
                               "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt}
    return sorted(unique.values(), key=lambda row: row["rcept_no"], reverse=True)


def collect_company_list(api, company, begin, end, sources, on_page=None):
    target = api.output / "metadata/company_lists" / (company["stock_code"] + ".json")
    rows, intervals, reused = [], [], []
    for source_path in [target, *[source / "metadata/company_lists" / target.name for source in sources]]:
        cached = read_json(source_path)
        if not cached or not cached.get("complete"):
            continue
        left, right = cached.get("begin", ""), cached.get("end", "")
        if not re.fullmatch(r"\d{8}", left) or not re.fullmatch(r"\d{8}", right):
            continue
        if right >= begin and left <= end:
            rows.extend(row for row in cached.get("rows", []) if left <= row.get("rcept_dt", "") <= right)
            intervals.append((left, right))
            reused.append({"path": str(source_path), "begin": left, "end": right})
    complete, error = True, None
    try:
        for start, stop in uncovered_windows(begin, end, intervals):
            page = 1
            while True:
                response = api.json("list.json", {"corp_code": company["corp_code"], "bgn_de": start,
                                                   "end_de": stop, "last_reprt_at": "N",
                                                   "page_no": page, "page_count": 100})
                rows.extend(response.get("list", []))
                if on_page:
                    on_page({"begin": start, "end": stop, "page_no": page,
                             "total_page": int(response.get("total_page", 0)), "rows_loaded": len(rows)})
                if response["status"] == "013" or page >= int(response.get("total_page", 1)):
                    break
                page += 1
    except (RequestStopped, CollectorError) as exc:
        complete, error = False, str(exc)
    result = {"stock_code": company["stock_code"], "begin": begin, "end": end,
              "complete": complete, "error": error, "reused_lists": reused, "updated_at": now(),
              "rows": unique_rows(rows, company, begin, end)}
    write_json(target, result)
    return result


def existing_zips(sources):
    result = {}
    for source in sources:
        for path in sorted((source / "raw/documents").glob("*.zip")):
            match = re.search(r"(?<!\d)(\d{14})(?!\d)", path.name)
            if match:
                result.setdefault(match.group(1), path)
    return result


def company_stem(company):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", company.get("corp_name") or company["stock_code"]).rstrip(" .")
    return f'{int(company["universe_rank"]):03d}_{company["stock_code"]}_{name}'


def save_company_csv(output, company, rows):
    path = output / "companies" / (company_stem(company) + ".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.tmp")
    with retry_file_io(temporary.open, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    retry_file_io(os.replace, temporary, path)


def load_detail_receipts(path):
    """Recover a partial last JSONL line after interruption without duplicates."""
    receipts = set()
    if not path.exists():
        return receipts
    valid_bytes = 0
    with path.open("rb") as handle:
        while line := handle.readline():
            try:
                row = json.loads(line)
                receipt = row["rcept_no"]
                if not line.endswith(b"\n"):
                    raise ValueError("unfinished_line")
            except (ValueError, KeyError):
                if handle.read(1):
                    raise CollectorError("invalid_interior_detail_jsonl_line") from None
                break
            receipts.add(receipt)
            valid_bytes = handle.tell()
    if path.stat().st_size != valid_bytes:
        with path.open("r+b") as handle:
            handle.truncate(valid_bytes)
    return receipts


def document_one(api, row, prior_zips):
    receipt = row["rcept_no"]
    raw = api.output / "raw/documents" / (receipt + ".zip")
    state_path = api.output / "state/documents" / (receipt + ".json")
    raw.parent.mkdir(parents=True, exist_ok=True)
    source = "current_local_collection"
    try:
        if raw.exists() and not zipfile.is_zipfile(raw):
            raw.rename(raw.with_name(raw.name + ".corrupt." + str(time.time_ns())))
        if not raw.exists():
            previous = read_json(state_path, {})
            if previous.get("status") == "unavailable":
                return {"status": "unavailable", "api_status": previous.get("api_status")}
            prior = prior_zips.get(receipt)
            if prior and zipfile.is_zipfile(prior):
                temporary = raw.with_suffix(".zip.tmp")
                shutil.copyfile(prior, temporary)
                retry_file_io(os.replace, temporary, raw)
                source = "prior_local_collection"
            else:
                content = api.request("document.xml", {"rcept_no": receipt})
                if not zipfile.is_zipfile(io.BytesIO(content)):
                    match = re.search(rb"<status>\s*(\d{3})\s*</status>", content)
                    status = match.group(1).decode() if match else "non_zip_response"
                    api.check_status(status)
                    state = {"rcept_no": receipt, "status": "unavailable" if status in UNAVAILABLE_STATUSES else "error",
                             "api_status": status, "at": now()}
                    write_json(state_path, state)
                    return state
                temporary = raw.with_suffix(".zip.tmp")
                temporary.write_bytes(content)
                retry_file_io(os.replace, temporary, raw)
                source = "OpenDART document.xml"
        parsed = parse_document_zip(raw)
        if not parsed["files"] or parsed.get("errors"):
            state = {"rcept_no": receipt, "status": "parse_error", "at": now(),
                     "error": "no_text_files" if not parsed["files"] else "attachment_parse_failed"}
            write_json(state_path, state)
            return state
        if not any(item.get("text", "").strip() for item in parsed["files"]):
            state = {"rcept_no": receipt, "status": "parse_error", "at": now(),
                     "error": "empty_document_text"}
            write_json(state_path, state)
            return state
        detail = {**row, "files": [{"filename": item["filename"], "text": item["text"]} for item in parsed["files"]],
                  "fetched_at": now(), "raw_zip": "raw/documents/" + raw.name, "source": source}
        return {"status": "collected", "detail": detail, "source": source}
    except RequestStopped:
        return {"status": "pending"}
    except (CollectorError, OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        state = {"rcept_no": receipt, "status": "error", "at": now(),
                 "error": str(exc) if isinstance(exc, CollectorError) else type(exc).__name__}
        write_json(state_path, state)
        return state


class Progress:
    def __init__(self, api, args, universe):
        self.api = api
        self.output = api.output
        self.last_write = 0.0
        self.state = {"version": "1.0", "started_at": now(), "updated_at": now(), "status": "running",
                      "phase": "lists", "rank_basis": "universe_rank (KOSPI common stocks)",
                      "rank_from": args.rank_from, "rank_to": args.rank_to,
                      "begin": args.begin, "end": args.end, "universe_file": str(args.universe),
                      "snapshot_dates": sorted({row.get("snapshot_date", "unknown") for row in universe}),
                      "excluded_title_types": EXCLUDED_TYPES, "corrections_included": True,
                      "workers": args.workers, "interval_seconds": args.interval,
                      "daily_limit": args.daily_limit, "key_fingerprint": api.fingerprint,
                      "quota_note": "Local reservations only; other clients using this key are not visible.",
                      "companies": [{"universe_rank": row["universe_rank"], "stock_code": row["stock_code"],
                                     "corp_name": row["corp_name"], "list_complete": False, "total_listed": 0,
                                     "selected": 0, "excluded": 0, "collected": 0,
                                     "unavailable": 0, "not_collected": 0, "errors": 0,
                                     "unavailable_by_api_status": {"013": 0, "014": 0}}
                                    for row in universe]}

    def save(self, force=False):
        if not force and time.monotonic() - self.last_write < 5:
            return
        self.state.update(updated_at=now(), new_requests_this_run=self.api.used,
                          local_requests_today=self.api.daily_used, list_cache_hits=self.api.cache_hits,
                          stop_reason=self.api.stop_reason or None)
        self.state["totals"] = {key: sum(company[key] for company in self.state["companies"])
                                for key in ("total_listed", "selected", "excluded", "collected", "unavailable", "not_collected", "errors")}
        self.state["lists_complete"] = all(company["list_complete"] for company in self.state["companies"])
        self.state["complete_except_source_unavailable"] = (self.state["lists_complete"] and
                                                            self.state["totals"]["not_collected"] == 0)
        write_json(self.output / "progress.json", self.state)
        write_json(self.output / "manifest.json", self.state)
        self.last_write = time.monotonic()


def document_company(api, company, rows, prior_zips, stats, progress, workers):
    detail_path = api.output / "companies" / (company_stem(company) + "_detail.jsonl")
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_detail_receipts(detail_path)
    queue = []
    stats.update(collected=0, unavailable=0, not_collected=len(rows), errors=0,
                 unavailable_by_api_status={"013": 0, "014": 0})
    for row in rows:
        receipt = row["rcept_no"]
        raw = api.output / "raw/documents" / (receipt + ".zip")
        if receipt in existing and raw.exists() and zipfile.is_zipfile(raw):
            stats["collected"] += 1
            stats["not_collected"] -= 1
            continue
        previous = read_json(api.output / "state/documents" / (receipt + ".json"), {})
        if previous.get("status") == "unavailable":
            stats["unavailable"] += 1
            stats["not_collected"] -= 1
            code = previous.get("api_status")
            if code in UNAVAILABLE_STATUSES:
                stats["unavailable_by_api_status"][code] += 1
        else:
            queue.append(row)
    progress.save(force=True)
    iterator = iter(queue)
    with detail_path.open("a", encoding="utf-8", newline="\n") as detail_file:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            active = {}

            def schedule():
                while len(active) < workers and not api.stop_reason:
                    row = next(iterator, None)
                    if row is None:
                        break
                    active[pool.submit(document_one, api, row, prior_zips)] = row

            schedule()
            while active:
                done, _ = wait(active, timeout=5, return_when=FIRST_COMPLETED)
                for future in done:
                    row = active.pop(future)
                    result = future.result()
                    status = result["status"]
                    if status == "collected":
                        if row["rcept_no"] not in existing:
                            detail_file.write(json.dumps(result["detail"], ensure_ascii=False, separators=(",", ":")) + "\n")
                            detail_file.flush()
                            existing.add(row["rcept_no"])
                        write_json(api.output / "state/documents" / (row["rcept_no"] + ".json"),
                                   {"rcept_no": row["rcept_no"], "status": "collected", "at": now(), "source": result["source"]})
                        stats["collected"] += 1
                        stats["not_collected"] -= 1
                    elif status == "unavailable":
                        stats["unavailable"] += 1
                        stats["not_collected"] -= 1
                        if result.get("api_status") in UNAVAILABLE_STATUSES:
                            stats["unavailable_by_api_status"][result["api_status"]] += 1
                    elif status != "pending":
                        stats["errors"] += 1
                schedule()
                progress.save()
    progress.save(force=True)


def collect(api, args, universe):
    output = api.output
    write_jsonl(output / "metadata/universe.jsonl", universe)
    progress = Progress(api, args, universe)
    progress.save(force=True)
    selected_by_code = {}
    all_rows, excluded_rows = [], []
    try:
        for company, stats in zip(universe, progress.state["companies"]):
            progress.state["current_company"] = {"universe_rank": company["universe_rank"], "stock_code": company["stock_code"]}
            def page_progress(page):
                progress.state["current_list_page"] = page
                progress.save()
            result = collect_company_list(api, company, args.begin, args.end, args.reuse_source, page_progress)
            rows = result["rows"]
            selected = [row for row in rows if not exclusion_code(row)]
            excluded = [{**row, "excluded_type": exclusion_code(row)} for row in rows if exclusion_code(row)]
            stats.update(list_complete=result["complete"], list_error=result["error"],
                         total_listed=len(rows), selected=len(selected), excluded=len(excluded),
                         not_collected=len(selected))
            all_rows.extend(rows)
            excluded_rows.extend(excluded)
            selected_by_code[company["stock_code"]] = selected
            save_company_csv(output, company, selected)
            progress.save(force=True)
            print(f'LIST rank={company["universe_rank"]} stock={company["stock_code"]} total={len(rows)} selected={len(selected)} complete={result["complete"]} requests={api.used}', flush=True)
            if api.stop_reason:
                break
        write_jsonl(output / "metadata/disclosures.jsonl", all_rows)
        write_jsonl(output / "metadata/excluded.jsonl", excluded_rows)
        write_jsonl(output / "metadata/selected.jsonl", [row for rows in selected_by_code.values() for row in rows])
        if not args.plan_only and all(company["list_complete"] for company in progress.state["companies"]):
            progress.state["phase"] = "documents"
            prior_zips = existing_zips(args.reuse_source)
            for company, stats in zip(universe, progress.state["companies"]):
                progress.state["current_company"] = {"universe_rank": company["universe_rank"], "stock_code": company["stock_code"]}
                document_company(api, company, selected_by_code[company["stock_code"]], prior_zips, stats, progress, args.workers)
                print(f'DOCUMENTS rank={company["universe_rank"]} stock={company["stock_code"]} collected={stats["collected"]} unavailable={stats["unavailable"]} pending={stats["not_collected"]} requests={api.used}', flush=True)
                if api.stop_reason:
                    break
        progress.save(force=True)
        if api.stop_reason:
            progress.state["status"] = "stopped"
        elif not progress.state["lists_complete"]:
            progress.state["status"] = "incomplete_lists"
        elif args.plan_only:
            progress.state["status"] = "planned"
        elif progress.state["complete_except_source_unavailable"]:
            progress.state["status"] = "complete"
        else:
            progress.state["status"] = "incomplete"
    except BaseException as exc:
        api.stop("interrupted" if isinstance(exc, KeyboardInterrupt) else "collector_error_" + type(exc).__name__)
        progress.state["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
        raise
    finally:
        progress.state["finished_at"] = now()
        progress.save(force=True)
    return progress.state


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    result.add_argument("--rank-from", type=int, default=51)
    result.add_argument("--rank-to", type=int, default=100)
    result.add_argument("--begin", default="20160101")
    result.add_argument("--end", default=datetime.now(KST).strftime("%Y%m%d"))
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--key-file", type=Path, default=PROJECT / ".env.dart.local")
    result.add_argument("--workers", type=int, default=4)
    result.add_argument("--interval", type=float, default=0.3)
    result.add_argument("--daily-limit", type=int, default=19500)
    result.add_argument("--max-new-requests", type=int)
    result.add_argument("--initial-used", type=int, default=0, help="minimum known total already used today; do not add again on resume")
    result.add_argument("--reuse-source", type=Path, action="append", default=None)
    result.add_argument("--plan-only", action="store_true", help="collect lists and CSVs only; skip document.xml")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        for value in (args.begin, args.end):
            if not re.fullmatch(r"\d{8}", value):
                raise ValueError("Dates must be YYYYMMDD")
            datetime.strptime(value, "%Y%m%d")
        if args.begin > args.end or args.begin < "19990101":
            raise ValueError("Date range must start at or after 19990101 and end at or after begin")
        if args.rank_from < 1 or args.rank_to < args.rank_from:
            raise ValueError("Invalid rank range")
        if args.workers < 1 or args.workers > 16 or args.interval < 0 or args.daily_limit < 1 or args.initial_used < 0:
            raise ValueError("Invalid workers, interval, daily limit, or initial usage")
        if args.max_new_requests is not None and args.max_new_requests < 0:
            raise ValueError("Request budget must be nonnegative")
        args.output = args.output.resolve()
        args.reuse_source = args.reuse_source or DEFAULT_SOURCES
        universe = load_universe(args.universe, args.rank_from, args.rank_to)
        key = load_key(args.key_file)
        with FileLock(args.output / ".collector.lock"):
            prior_manifest = read_json(args.output / "manifest.json", {})
            for field in ("rank_from", "rank_to", "begin", "end"):
                if field in prior_manifest and prior_manifest[field] != getattr(args, field):
                    raise ValueError("Output already belongs to a different rank/date range; use another --output")
            api = Api(key, args.output, args.interval, args.daily_limit, args.max_new_requests,
                      initial_used=args.initial_used)
            result = collect(api, args, universe)
        print(json.dumps({"status": result["status"], "totals": result["totals"],
                          "new_requests_this_run": api.used, "local_requests_today": api.daily_used,
                          "stop_reason": api.stop_reason or None, "output": str(args.output)}, ensure_ascii=False), flush=True)
        return 0 if result["status"] in {"planned", "complete"} else 2
    except KeyboardInterrupt:
        print("Collection interrupted; rerun the same command to resume.", file=sys.stderr)
        return 130
    except (CollectorError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        # Never print a traceback or an arbitrary network exception URL.
        print("collector_error_" + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
