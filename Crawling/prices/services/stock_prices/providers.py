"""Public daily-price adapters with explicit source and adjustment semantics.

The collector runs each call in a separate process. A lock additionally protects
the short-lived requests transport hook used by libraries without timeout hooks.
No fallback source, KRX login, or retry is performed here.
"""
from __future__ import annotations

from calendar import timegm
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import ast
import importlib
import math
from numbers import Integral, Real
import re
from threading import RLock
from types import SimpleNamespace
from urllib.parse import urlsplit

import pandas as pd
import requests


class ProviderError(RuntimeError):
    """A safe error code and structured details; never raw response bodies."""

    def __init__(self, kind: str, *, halt_source: bool = False, details: dict | None = None):
        self.kind = kind
        self.halt_source = halt_source
        self.details = details or {}
        super().__init__(kind)


_LOCK = RLock()
_SOURCES = {
    'KOSPI': ('naver_chart_sisejson', 'naver_chart_ohlc', 'KRW', 'Asia/Seoul'),
    'NASDAQ': ('finance_datareader_yahoo', 'yahoo_close_and_adj_close', 'USD', 'America/New_York'),
}
_COLUMNS = {
    'KOSPI': {'open': '시가', 'high': '고가', 'low': '저가', 'close': '종가', 'volume': '거래량'},
    'NASDAQ': {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'},
}


@contextmanager
def _public_transport(market: str, ticker: str):
    """Apply timeout/status checks to the selected public source only."""
    original = requests.sessions.Session.request
    expected = (('api.finance.naver.com', '/siseJson.naver') if market == 'KOSPI'
                else ('query2.finance.yahoo.com', '/v8/finance/chart/' + ticker))
    count = 0

    def request(session, method, url, **kwargs):
        nonlocal count
        parsed = urlsplit(url)
        if (method.upper() != 'GET' or (parsed.hostname, parsed.path) != expected
                or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password):
            raise ProviderError('unexpected_endpoint', halt_source=True)
        count += 1
        if count > 1:
            raise ProviderError('unexpected_extra_request', halt_source=True)
        kwargs['timeout'] = (5, 30)
        kwargs['allow_redirects'] = False
        # A public request must not inherit .netrc, proxy credentials, a KRX
        # session, explicit auth, or preloaded cookies. Restore the caller's
        # exact session objects even if the request fails.
        saved = {name: getattr(session, name) for name in ('trust_env', 'auth', 'cookies', 'headers', 'proxies', 'cert')}
        credential_headers = {'authorization', 'proxy-authorization', 'cookie'}
        kwargs['auth'] = None
        kwargs['cookies'] = {}
        kwargs['proxies'] = {}
        kwargs['cert'] = None
        kwargs['headers'] = {key: value for key, value in (kwargs.get('headers') or {}).items()
                             if key.lower() not in credential_headers}
        session.trust_env = False
        session.auth = None
        session.cookies = requests.cookies.RequestsCookieJar()
        session.headers = requests.structures.CaseInsensitiveDict(
            (key, value) for key, value in saved['headers'].items() if key.lower() not in credential_headers)
        session.proxies = {}
        session.cert = None
        try:
            response = original(session, method, url, **kwargs)
        finally:
            for name, value in saved.items():
                setattr(session, name, value)
        status = response.status_code
        if status in (401, 403, 429):
            raise ProviderError('rate_limited' if status == 429 else 'access_denied',
                                halt_source=True, details={'http_status': status})
        if 300 <= status < 400:
            raise ProviderError('unexpected_redirect', halt_source=True, details={'http_status': status})
        if status >= 400:
            raise ProviderError('http_error', details={'http_status': status})
        return response

    requests.sessions.Session.request = request
    try:
        yield
    finally:
        requests.sessions.Session.request = original


def _naver_frame(content: bytes) -> pd.DataFrame:
    """Parse Naver's array literal without executing JavaScript or filling data.

    The official response uses single-quoted column labels and may have a
    trailing comma. Accept JSON null/booleans as literals, then let the normal
    field validation reject inappropriate types. Never evaluate calls/names.
    """
    class JsonLiterals(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id not in ('null', 'true', 'false'):
                raise ValueError('unexpected literal')
            return ast.copy_location(ast.Constant(value={'null': None, 'true': True, 'false': False}[node.id]), node)

    try:
        tree = ast.parse(content.decode('utf-8-sig').strip(), mode='eval')
        values = ast.literal_eval(JsonLiterals().visit(tree))
    except (SyntaxError, ValueError, TypeError, UnicodeDecodeError) as error:
        raise ProviderError('invalid_response', details={'reason': 'invalid_naver_array'}) from error
    if not isinstance(values, list):
        raise ProviderError('invalid_response', details={'reason': 'invalid_naver_array'})
    if not values:
        return pd.DataFrame()
    header, records = values[0], values[1:]
    expected = ['날짜', '시가', '고가', '저가', '종가', '거래량']
    if (not isinstance(header, list) or not all(isinstance(column, str) for column in header)
            or header[:6] != expected or len(set(header)) != len(header)
            or any(not isinstance(row, list) or not 6 <= len(row) <= len(header) for row in records)):
        raise ProviderError('invalid_response', details={'reason': 'invalid_naver_columns'})
    if any(not isinstance(row[0], str) or not re.fullmatch(r'[0-9]{8}', row[0]) for row in records):
        raise ProviderError('invalid_data', details={'reason': 'invalid_date_index'})
    # Old source rows omit the optional foreign-ownership percentage. All
    # six required date/OHLCV fields must exist; no required value is filled.
    # Object dtype preserves large integer volumes even beside null rows.
    frame = pd.DataFrame([row[:6] for row in records], columns=expected, dtype=object)
    try:
        frame.index = pd.to_datetime(frame.pop('날짜'), format='%Y%m%d', errors='raise')
    except (ValueError, TypeError) as error:
        raise ProviderError('invalid_data', details={'reason': 'invalid_date_index'}) from error
    return frame


def _read_frame(market: str, ticker: str, start: date, end: date) -> pd.DataFrame:
    with _public_transport(market, ticker):
        if market == 'KOSPI':
            # Same date-range request implemented in Naver's official chart
            # bundle, with its 1980 lower bound. One response supplies the
            # requested history; no fchart/Yahoo series are spliced into it.
            # https://financial-vn.pstatic.net/client-chart/pc/live/4.4.6/js/chartiq.js
            floor = date(1980, 1, 1)
            if end < floor:
                return pd.DataFrame()
            response = requests.get('https://api.finance.naver.com/siseJson.naver', params={
                'symbol': ticker, 'requestType': '1', 'startTime': max(start, floor).strftime('%Y%m%d'),
                'endTime': end.strftime('%Y%m%d'), 'timeframe': 'day'})
            return _naver_frame(response.content)
        reader = importlib.import_module('FinanceDataReader')
        # FDR 0.9.202 calls time.mktime, which fails before the Unix epoch on
        # Windows and otherwise depends on the host timezone. Replace only
        # the Yahoo module's time reference, never the global time module.
        yahoo = importlib.import_module('FinanceDataReader.yahoo.data')
        original_time = yahoo.time
        yahoo.time = SimpleNamespace(mktime=timegm)
        try:
            # Yahoo period2 is exclusive; filter both inclusive edges below.
            return reader.DataReader('YAHOO:' + ticker, start.isoformat(), (end + timedelta(days=1)).isoformat())
        finally:
            yahoo.time = original_time


def _number(value, field: str, trading_date: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ProviderError('invalid_data', details={'field': field, 'trading_date': trading_date})
    number = float(value)
    if not math.isfinite(number):
        raise ProviderError('invalid_data', details={'field': field, 'trading_date': trading_date})
    return number


def _volume(value, trading_date: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ProviderError('invalid_data', details={'field': 'volume', 'trading_date': trading_date})
    if isinstance(value, Integral):
        volume = int(value)
    elif math.isfinite(float(value)) and float(value).is_integer():
        volume = int(value)
    else:
        raise ProviderError('invalid_data', details={'field': 'volume', 'trading_date': trading_date})
    if not 0 <= volume <= 9223372036854775807:
        raise ProviderError('invalid_data', details={'field': 'volume', 'trading_date': trading_date})
    return volume


def _adjusted_close(value, trading_date: str) -> float | None:
    if pd.isna(value):
        return None
    adjusted = _number(value, 'adj_close', trading_date)
    if adjusted <= 0:
        raise ProviderError('invalid_data', details={'field': 'adj_close', 'trading_date': trading_date})
    return adjusted


def _normalize(frame, market: str, ticker: str, start: date, end: date) -> list[dict]:
    if not isinstance(frame, pd.DataFrame):
        raise ProviderError('invalid_data', details={'reason': 'not_dataframe'})
    if frame.empty:
        return []
    columns = _COLUMNS[market]
    required = set(columns.values()) | ({'Adj Close'} if market == 'NASDAQ' else set())
    if not required.issubset(frame.columns) or not frame.columns.is_unique:
        raise ProviderError('invalid_data', details={'reason': 'missing_or_duplicate_columns'})
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ProviderError('invalid_data', details={'reason': 'invalid_date_index'})
    provider, basis, currency, timezone = _SOURCES[market]
    rows, seen = [], set()
    for stamp, values in zip(frame.index, frame.to_dict('records')):
        if pd.isna(stamp):
            raise ProviderError('invalid_data', details={'reason': 'missing_date'})
        trading_day = stamp.tz_convert(timezone).date() if stamp.tzinfo else stamp.date()
        if not start <= trading_day <= end:
            continue
        day = trading_day.isoformat()
        # Korea's Saturday closure began 1998-12-07. Preserve historical
        # Saturday bars actually supplied by Naver without inventing dates.
        # KRX KIND industry history: external/2012/07/17/000038/20120717000087/10601.htm
        historical_kospi_saturday = market == 'KOSPI' and trading_day.weekday() == 5 and trading_day < date(1998, 12, 7)
        if day in seen or (trading_day.weekday() >= 5 and not historical_kospi_saturday):
            raise ProviderError('invalid_data', details={'reason': 'duplicate_or_weekend_date', 'trading_date': day})
        seen.add(day)
        identity = {'ticker': ticker, 'market': market, 'trading_date': day, 'interval_type': '1D',
                    'currency': currency, 'provider': provider, 'price_basis': basis}
        source_prices = [values[column] for column in columns.values()]
        if market == 'NASDAQ':
            source_prices.append(values['Adj Close'])
        if all(pd.isna(value) for value in source_prices):
            # A dated placeholder with no source price/volume is evidence of
            # missing data, not a zero-valued bar. Preserve it for quarantine
            # using JSON nulls; partially missing rows are handled below.
            rows.append({**identity, **{field + '_price': None for field in ('open', 'high', 'low', 'close')},
                         'trading_volume': None, 'adj_close': None, 'quality_flags': ['source_missing_ohlcv']})
            continue
        missing = {field: pd.isna(values[column]) for field, column in columns.items()}
        if any(missing.values()) and not all(missing.values()):
            partial_ohlc = {field: None if missing[field] else _number(values[columns[field]], field, day)
                            for field in ('open', 'high', 'low', 'close')}
            if any(value is not None and value <= 0 for value in partial_ohlc.values()):
                raise ProviderError('invalid_data', details={'reason': 'invalid_partial_price', 'trading_date': day})
            volume = None if missing['volume'] else _volume(values[columns['volume']], day)
            adjusted = _adjusted_close(values['Adj Close'], day) if market == 'NASDAQ' else None
            # Incomplete source rows are never chart/loader bars. Keep every
            # present value, including its original OHLC relationships, while
            # missing values become JSON null instead of inferred prices.
            rows.append({**identity, **{field + '_price': value for field, value in partial_ohlc.items()},
                         'trading_volume': volume, 'adj_close': adjusted, 'quality_flags': ['source_incomplete_ohlcv']})
            continue
        ohlc = {field: _number(values[column], field, day) for field, column in columns.items() if field != 'volume'}
        volume = _volume(values[columns['volume']], day)
        if ohlc['close'] <= 0:
            raise ProviderError('invalid_data', details={'reason': 'invalid_close_or_volume', 'trading_date': day})
        flags = []
        if all(ohlc[field] == 0 for field in ('open', 'high', 'low')) and volume == 0:
            flags.append('halted_ohl_zero')
        elif (market == 'KOSPI' and all(ohlc[field] == 0 for field in ('open', 'high', 'low'))
              and ohlc['close'].is_integer() and volume > 0):
            # Source reports trades without usable O/H/L values. Keep the
            # zeros and trade volume in quarantine, separately from halts.
            flags.append('source_zero_ohl_with_volume')
        else:
            tolerance = 0 if market == 'KOSPI' else max(ohlc.values()) * 1e-9
            if (min(ohlc.values()) <= 0 or ohlc['high'] + tolerance < max(ohlc['open'], ohlc['close'], ohlc['low'])
                    or ohlc['low'] - tolerance > min(ohlc['open'], ohlc['close'], ohlc['high'])):
                # Naver's adjusted series can contain a one-won OHLC range
                # mismatch. Preserve, flag, and quarantine that exact source
                # shape; do not widen the range or change any price.
                mismatch = max(0, ohlc['open'] - ohlc['high'], ohlc['close'] - ohlc['high'],
                               ohlc['low'] - ohlc['open'], ohlc['low'] - ohlc['close'])
                if (market == 'KOSPI' and all(value > 0 and value.is_integer() for value in ohlc.values())
                        and ohlc['high'] >= ohlc['low'] and mismatch == 1):
                    flags.append('ohlc_range_mismatch')
                elif market == 'KOSPI' and all(value > 0 and value.is_integer() for value in ohlc.values()):
                    # Broader historical source inconsistencies remain
                    # invalid chart prices. Preserve their exact values in
                    # a distinct quarantine category, never repair them.
                    flags.append('source_inconsistent_ohlc')
                else:
                    raise ProviderError('invalid_data', details={'reason': 'inconsistent_ohlc', 'trading_date': day})
        adj_close = _adjusted_close(values['Adj Close'], day) if market == 'NASDAQ' else None
        rows.append({**identity,
                     **{field + '_price': value for field, value in ohlc.items()},
                     'trading_volume': volume, 'adj_close': adj_close, 'quality_flags': flags})
    return sorted(rows, key=lambda row: row['trading_date'])


def fetch_prices(market: str, ticker: str, start: date, end: date) -> list[dict]:
    """Return daily source prices within the inclusive [start, end] interval.

    KOSPI uses one Naver siseJson date-range response. NASDAQ Close and Adj
    Close remain separate. Halt-like all-zero OHL rows retain their source values
    and carry a quality flag so the collector can quarantine/count them.
    KOSPI positive integer prices with an exact one-won range mismatch are
    likewise flagged for quarantine with every original price preserved.
    Other inconsistent KOSPI positive integer OHLC ranges are separately
    quarantined as source_inconsistent_ohlc, never accepted as normal bars.
    KOSPI zero O/H/L with positive integer close and trade volume is separately
    quarantined as source_zero_ohl_with_volume, without replacing the zeros.
    Dated all-missing OHLCV (and US adjusted close) placeholders are returned
    as Python None values with source_missing_ohlcv for separate quarantine.
    Partly missing OHLCV is separately flagged source_incomplete_ohlcv after
    validating every present value, without imputing or repairing any field.
    An empty valid response returns []; the collector decides cache policy.
    """
    if not isinstance(market, str) or market not in _SOURCES or not isinstance(ticker, str):
        raise ProviderError('invalid_input')
    pattern = r'[0-9A-Z]{6}' if market == 'KOSPI' else r'[A-Z][A-Z0-9.-]{0,14}'
    if not re.fullmatch(pattern, ticker):
        raise ProviderError('invalid_input')
    if (not isinstance(start, date) or isinstance(start, datetime) or not isinstance(end, date)
            or isinstance(end, datetime) or start > end or end == date.max):
        raise ProviderError('invalid_input')
    try:
        with _LOCK:
            frame = _read_frame(market, ticker, start, end)
        return _normalize(frame, market, ticker, start, end)
    except ProviderError:
        raise
    except ImportError as error:
        raise ProviderError('dependency_missing') from error
    except requests.Timeout as error:
        raise ProviderError('timeout') from error
    except requests.RequestException as error:
        raise ProviderError('network_error') from error
    except Exception as error:
        raise ProviderError('invalid_response', details={'error_type': type(error).__name__}) from error
