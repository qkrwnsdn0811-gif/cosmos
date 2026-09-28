from datetime import date, datetime
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services'))
from stock_prices import providers


DAY = date(2026, 9, 14)


def frame(market='NASDAQ', dates=('2026-09-14',), **changes):
    values = {'Open': 100., 'High': 110., 'Low': 95., 'Close': 105., 'Volume': 1000, 'Adj Close': 103.}
    values.update(changes)
    result = pd.DataFrame([values for _ in dates], index=pd.to_datetime(list(dates)))
    if market == 'KOSPI':
        result = result.rename(columns={'Open': '시가', 'High': '고가', 'Low': '저가', 'Close': '종가', 'Volume': '거래량'})
        result = result.drop(columns='Adj Close')
    return result


def naver_body(rows=None):
    header = ['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율']
    return repr([header, *(rows if rows is not None else [['20260914', 100, 110, 95, 105, 1000, 50.5]])]).encode('utf-8')


class NormalizationTests(unittest.TestCase):
    def fetch(self, value, market='NASDAQ', ticker='AAPL', start=DAY, end=DAY):
        with patch.object(providers, '_read_frame', return_value=value):
            return providers.fetch_prices(market, ticker, start, end)

    def test_domestic_chart_source_and_alphanumeric_ticker(self):
        row = self.fetch(frame('KOSPI'), market='KOSPI', ticker='0126Z0')[0]
        self.assertEqual((row['ticker'], row['market'], row['currency']), ('0126Z0', 'KOSPI', 'KRW'))
        self.assertEqual(row['price_basis'], 'naver_chart_ohlc')
        self.assertEqual(row['provider'], 'naver_chart_sisejson')
        self.assertEqual(row['close_price'], 105.)
        self.assertIsNone(row['adj_close'])
        self.assertEqual(row['quality_flags'], [])
        self.assertIsInstance(row['trading_volume'], int)

    def test_us_close_and_adjusted_close_remain_distinct(self):
        row = self.fetch(frame())[0]
        self.assertEqual((row['close_price'], row['adj_close']), (105., 103.))
        self.assertEqual(row['provider'], 'finance_datareader_yahoo')
        self.assertEqual(row['price_basis'], 'yahoo_close_and_adj_close')
        self.assertEqual((row['currency'], row['interval_type']), ('USD', '1D'))
        self.assertEqual(set(row), {'ticker', 'market', 'trading_date', 'interval_type',
            'open_price', 'high_price', 'low_price', 'close_price', 'trading_volume',
            'adj_close', 'currency', 'provider', 'price_basis', 'quality_flags'})

    def test_inclusive_date_filter_removes_extra_yahoo_edge_rows(self):
        rows = self.fetch(frame(dates=('2026-09-11', '2026-09-14', '2026-09-15')))
        self.assertEqual([r['trading_date'] for r in rows], ['2026-09-14'])

    def test_empty_response_preserved_for_collector_policy(self):
        self.assertEqual(self.fetch(pd.DataFrame()), [])

    def test_sort_dates_without_manufacturing_missing_sessions(self):
        rows = self.fetch(frame(dates=('2026-09-14', '2026-09-10')), start=date(2026, 9, 10))
        self.assertEqual([r['trading_date'] for r in rows], ['2026-09-10', '2026-09-14'])

    def test_observed_historical_kospi_saturday_is_retained(self):
        rows = self.fetch(frame('KOSPI', dates=('1998-12-04', '1998-12-05', '1998-12-07')),
                          market='KOSPI', ticker='005930', start=date(1998, 12, 4), end=date(1998, 12, 7))
        self.assertEqual([row['trading_date'] for row in rows], ['1998-12-04', '1998-12-05', '1998-12-07'])

    def test_historical_sunday_modern_saturday_and_us_weekend_are_rejected(self):
        for market, ticker, day in [('KOSPI', '005930', date(1998, 12, 6)),
                                    ('KOSPI', '005930', date(1998, 12, 12)),
                                    ('NASDAQ', 'AAPL', date(1998, 12, 5))]:
            with self.subTest(market=market, day=day), self.assertRaises(providers.ProviderError):
                self.fetch(frame(market, dates=(day.isoformat(),)), market=market, ticker=ticker, start=day, end=day)

    def test_halted_ohl_zero_preserved_and_flagged(self):
        row = self.fetch(frame(Open=0., High=0., Low=0., Volume=0))[0]
        self.assertEqual((row['open_price'], row['high_price'], row['low_price'], row['close_price'], row['trading_volume']), (0., 0., 0., 105., 0))
        self.assertEqual(row['quality_flags'], ['halted_ohl_zero'])

    def test_actual_one_won_kospi_source_mismatch_preserves_all_prices(self):
        value = frame('KOSPI', dates=('2025-05-07',), Open=819275, High=876387, Low=814351, Close=876388, Volume=475710)
        row = self.fetch(value, market='KOSPI', ticker='012450', start=date(2025, 5, 7), end=date(2025, 5, 7))[0]
        self.assertEqual((row['open_price'], row['high_price'], row['low_price'], row['close_price'], row['trading_volume']),
                         (819275., 876387., 814351., 876388., 475710))
        self.assertEqual(row['quality_flags'], ['ohlc_range_mismatch'])

    def test_exact_one_won_low_boundary_mismatch_is_also_quarantined(self):
        row = self.fetch(frame('KOSPI', Open=94, Low=95), market='KOSPI', ticker='012450')[0]
        self.assertEqual((row['open_price'], row['low_price']), (94., 95.))
        self.assertEqual(row['quality_flags'], ['ohlc_range_mismatch'])

    def test_kospi_inconsistent_source_must_still_have_positive_integer_prices(self):
        invalid = [dict(High=104, Low=95.5),
                   dict(High=1, Low=0, Open=1, Close=2), dict(High=104, Low=-1),
                   dict(High=104.5)]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(providers.ProviderError) as raised:
                self.fetch(frame('KOSPI', **changes), market='KOSPI', ticker='012450')
            self.assertEqual(raised.exception.kind, 'invalid_data')

    def test_actual_positive_integer_source_range_errors_are_preserved_for_quarantine(self):
        examples = [('012450', '1990-05-18', (18704,18935,18704,18937,48277)),
                    ('001440', '1994-10-25', (772122,797874,772122,798157,22572))]
        for ticker, stamp, values in examples:
            with self.subTest(ticker=ticker):
                raw = dict(zip(('Open','High','Low','Close','Volume'), values))
                day = date.fromisoformat(stamp)
                row = self.fetch(frame('KOSPI', dates=(stamp,), **raw), market='KOSPI', ticker=ticker, start=day, end=day)[0]
                self.assertEqual(tuple(row[key] for key in ('open_price','high_price','low_price','close_price','trading_volume')), values)
                self.assertEqual(row['quality_flags'], ['source_inconsistent_ohlc'])

    def test_positive_integer_inverted_range_is_quarantined_without_reordering_prices(self):
        row = self.fetch(frame('KOSPI', Open=96, High=95, Low=96, Close=95), market='KOSPI', ticker='012450')[0]
        self.assertEqual((row['high_price'], row['low_price']), (95.,96.))
        self.assertEqual(row['quality_flags'], ['source_inconsistent_ohlc'])

    def test_source_inconsistent_range_does_not_relax_volume_or_zero_price_rules(self):
        for volume in [-1, 1.5, True, float('inf')]:
            with self.subTest(volume=volume), self.assertRaises(providers.ProviderError):
                self.fetch(frame('KOSPI', High=103, Volume=volume), market='KOSPI', ticker='012450')
        with self.assertRaises(providers.ProviderError):
            self.fetch(frame('KOSPI', Open=0, High=0, Low=0, Close=47700.5, Volume=141), market='KOSPI', ticker='047810')

    def test_actual_zero_ohl_with_trades_is_separately_quarantined_without_filling_prices(self):
        cases = [('047810',date(2017,10,11),47700,141), ('007660',date(2003,10,6),1344,600312)]
        for ticker, day, close, volume in cases:
            with self.subTest(ticker=ticker):
                row = self.fetch(frame('KOSPI', dates=(day.isoformat(),), Open=0, High=0, Low=0, Close=close, Volume=volume),
                                 market='KOSPI', ticker=ticker, start=day, end=day)[0]
                self.assertEqual((row['open_price'],row['high_price'],row['low_price'],row['close_price'],row['trading_volume']),
                                 (0.,0.,0.,float(close),volume))
                self.assertEqual(row['quality_flags'], ['source_zero_ohl_with_volume'])

    def test_zero_ohl_with_trades_exception_has_strict_types_and_market(self):
        bad = [dict(Open=False), dict(High=False), dict(Low=True), dict(Close=0), dict(Close=-1),
               dict(Close=105.5), dict(Close=float('inf')), dict(Volume=True), dict(Volume=-1),
               dict(Volume=1.5), dict(Volume=2**63), dict(High=1)]
        for changes in bad:
            values = {'Open':0, 'High':0, 'Low':0, 'Close':105, 'Volume':1000, **changes}
            with self.subTest(changes=changes), self.assertRaises(providers.ProviderError):
                self.fetch(frame('KOSPI', **values), market='KOSPI', ticker='047810')
        with self.assertRaises(providers.ProviderError):
            self.fetch(frame(Open=0, High=0, Low=0, Close=105, Volume=1000))

    def test_one_won_exception_does_not_apply_to_foreign_market(self):
        with self.assertRaises(providers.ProviderError) as raised:
            self.fetch(frame(High=104))
        self.assertEqual(raised.exception.details['reason'], 'inconsistent_ohlc')

    def test_invalid_ohlc_and_volume_fail_without_partial_output(self):
        bad = [dict(Close=0), dict(Open=float('inf')), dict(Open=-1),
               dict(High=104), dict(Low=106), dict(Open=0), dict(Open=0, High=0, Low=0, Volume=1),
               dict(Volume=-1), dict(Volume=1.5), dict(Volume=True),
               dict(Volume=2 ** 63), dict(Close='105')]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(providers.ProviderError) as raised:
                self.fetch(frame(**changes))
            self.assertEqual(raised.exception.kind, 'invalid_data')

    def test_missing_and_nonpositive_adjusted_price_handling(self):
        self.assertIsNone(self.fetch(frame(**{'Adj Close': float('nan')}))[0]['adj_close'])
        for value in [0, -1, float('inf')]:
            with self.subTest(value=value), self.assertRaises(providers.ProviderError):
                self.fetch(frame(**{'Adj Close': value}))

    def test_all_missing_source_ohlcv_is_preserved_as_json_nulls(self):
        missing = {key: float('nan') for key in ('Open', 'High', 'Low', 'Close', 'Volume', 'Adj Close')}
        for market, ticker in [('NASDAQ', 'KHC'), ('KOSPI', '012450')]:
            with self.subTest(market=market):
                row = self.fetch(frame(market, **missing), market=market, ticker=ticker)[0]
                for key in ('open_price', 'high_price', 'low_price', 'close_price', 'trading_volume', 'adj_close'):
                    self.assertIsNone(row[key])
                self.assertEqual(row['quality_flags'], ['source_missing_ohlcv'])
                self.assertEqual(row['trading_date'], '2026-09-14')
                serialized = json.dumps(row, allow_nan=False)
                self.assertNotIn('NaN', serialized)
                self.assertEqual(json.loads(serialized), row)

    def test_partial_missing_source_ohlcv_preserves_present_values(self):
        targets = {'Open': 'open_price', 'High': 'high_price', 'Low': 'low_price', 'Close': 'close_price', 'Volume': 'trading_volume'}
        for key in ('Open', 'High', 'Low', 'Close', 'Volume'):
            with self.subTest(key=key):
                row = self.fetch(frame(**{key: float('nan')}))[0]
                self.assertIsNone(row[targets[key]])
                self.assertEqual(row['quality_flags'], ['source_incomplete_ohlcv'])
                self.assertEqual(row['adj_close'], 103.)
                json.dumps(row, allow_nan=False)

    def test_actual_khc_partial_null_source_row_is_preserved(self):
        row = self.fetch(frame(Open=24.40999984741211, High=24.989999771118164, Low=24.270000457763672,
                               Close=float('nan'), Volume=20331188, **{'Adj Close': float('nan')}), ticker='KHC')[0]
        self.assertEqual((row['open_price'], row['high_price'], row['low_price'], row['trading_volume']),
                         (24.40999984741211, 24.989999771118164, 24.270000457763672, 20331188))
        self.assertIsNone(row['close_price'])
        self.assertIsNone(row['adj_close'])
        self.assertEqual(row['quality_flags'], ['source_incomplete_ohlcv'])
        self.assertNotIn('NaN', json.dumps(row, allow_nan=False))

    def test_partial_missing_rows_validate_every_present_value(self):
        invalid = [dict(High=0), dict(Low=-1), dict(High=float('inf')), dict(Close='105'),
                   dict(Volume=-1), dict(Volume=1.5), dict(Volume=True), dict(Volume=float('inf')),
                   {'Adj Close': -1}, {'Adj Close': float('inf')}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(providers.ProviderError):
                self.fetch(frame(Open=float('nan'), **changes))

    def test_partial_source_does_not_repair_original_ohlc_relationships(self):
        row = self.fetch(frame(Open=float('nan'), High=95, Low=110, Close=105))[0]
        self.assertEqual((row['high_price'], row['low_price'], row['close_price']), (95., 110., 105.))
        self.assertEqual(row['quality_flags'], ['source_incomplete_ohlcv'])

    def test_all_missing_ohlcv_with_present_adjusted_close_remains_invalid(self):
        missing_ohlc = {key: float('nan') for key in ('Open', 'High', 'Low', 'Close')}
        with self.assertRaises(providers.ProviderError):
            self.fetch(frame(**missing_ohlc, Volume=float('nan'), **{'Adj Close': 103.}))
        row = self.fetch(frame(**missing_ohlc, Volume=0, **{'Adj Close': float('nan')}))[0]
        self.assertEqual(row['trading_volume'], 0)
        self.assertEqual(row['quality_flags'], ['source_incomplete_ohlcv'])

    def test_all_missing_values_do_not_make_invalid_dates_valid(self):
        missing = {key: float('nan') for key in ('Open', 'High', 'Low', 'Close', 'Volume', 'Adj Close')}
        with self.assertRaises(providers.ProviderError):
            self.fetch(frame(dates=('2026-09-13',), **missing), start=date(2026, 9, 13))
        with self.assertRaises(providers.ProviderError):
            self.fetch(frame(dates=(None,), **missing))

    def test_missing_source_columns_fail(self):
        with self.assertRaises(providers.ProviderError):
            self.fetch(frame().drop(columns='Adj Close'))
        with self.assertRaises(providers.ProviderError):
            self.fetch(frame().drop(columns='Open'))

    def test_duplicate_weekend_missing_or_invalid_index_fail(self):
        for dates in [('2026-09-14', '2026-09-14'), ('2026-09-13',), (None,)]:
            with self.subTest(dates=dates), self.assertRaises(providers.ProviderError):
                self.fetch(frame(dates=dates), start=date(2026, 9, 13))
        invalid = frame().reset_index(drop=True)
        with self.assertRaises(providers.ProviderError):
            self.fetch(invalid)

    def test_explicit_market_and_single_safe_ticker_required(self):
        for market, ticker in [('US', 'AAPL'), ('KOSPI', 'YAHOO:AAPL'), ('NASDAQ', 'AAPL,MSFT'),
                               ('NASDAQ', 'AAPL?token=value'), ('NASDAQ', 'aapl'), ('KOSPI', '00593')]:
            with self.subTest(market=market, ticker=ticker), patch.object(providers, '_read_frame') as fetch:
                with self.assertRaises(providers.ProviderError) as raised:
                    providers.fetch_prices(market, ticker, DAY, DAY)
                self.assertEqual(raised.exception.kind, 'invalid_input')
                fetch.assert_not_called()

    def test_invalid_date_arguments_do_not_fetch(self):
        for start, end in [(datetime(2026, 9, 14), DAY), (DAY, '2026-09-14'), (DAY, date(2026, 9, 13))]:
            with self.subTest(start=start, end=end), patch.object(providers, '_read_frame') as fetch:
                with self.assertRaises(providers.ProviderError):
                    providers.fetch_prices('NASDAQ', 'AAPL', start, end)
                fetch.assert_not_called()


class AdapterTests(unittest.TestCase):
    def test_naver_range_is_one_public_request_without_library_login_import(self):
        response = SimpleNamespace(status_code=200, content=naver_body())
        with patch.object(requests.sessions.Session, 'request', return_value=response) as network:
            with patch.object(providers.importlib, 'import_module') as importing:
                rows = providers.fetch_prices('KOSPI', '0126Z0', DAY, DAY)
            importing.assert_not_called()
        network.assert_called_once()
        self.assertEqual(network.call_args.args[1:], ('get', 'https://api.finance.naver.com/siseJson.naver'))
        self.assertEqual(network.call_args.kwargs['params'], {'symbol':'0126Z0', 'requestType':'1',
            'startTime':'20260914', 'endTime':'20260914', 'timeframe':'day'})
        self.assertEqual(network.call_args.kwargs['timeout'], (5, 30))
        self.assertEqual(len(rows), 1)

    def test_naver_request_uses_official_1980_floor_and_requested_end(self):
        response = SimpleNamespace(status_code=200, content=naver_body())
        with patch.object(requests.sessions.Session, 'request', return_value=response) as network:
            providers.fetch_prices('KOSPI', '005930', date(1900, 1, 1), DAY)
        self.assertEqual(network.call_args.kwargs['params']['startTime'], '19800101')
        self.assertEqual(network.call_args.kwargs['params']['endTime'], '20260914')
        network.assert_called_once()

    def test_range_entirely_before_naver_floor_does_not_request(self):
        with patch.object(requests.sessions.Session, 'request') as network:
            self.assertEqual(providers.fetch_prices('KOSPI', '005930', date(1900, 1, 1), date(1979, 12, 31)), [])
        network.assert_not_called()

    def test_naver_literal_parser_preserves_historical_prices_and_large_volume(self):
        payload = naver_body([['19981205', 101, 104, 100, 105, 2**53+1, 20.0],
                              ['19981207', 0, 0, 0, 105, 0, 20.0]])
        # Match the source's permissible trailing comma without executing JS.
        value = providers._naver_frame(payload[:-1] + b',]')
        rows = providers._normalize(value, 'KOSPI', '005930', date(1998, 12, 5), date(1998, 12, 7))
        self.assertEqual(rows[0]['trading_volume'], 2**53+1)
        self.assertEqual(rows[0]['quality_flags'], ['ohlc_range_mismatch'])
        self.assertEqual(rows[1]['quality_flags'], ['halted_ohl_zero'])
        self.assertEqual(rows[1]['open_price'], 0.)

    def test_naver_json_nulls_remain_null_and_do_not_round_large_volumes(self):
        payload = naver_body([['20260911', 100, 110, 95, 105, 2**53+1, 20.0],
                              ['20260914', None, None, None, None, None, None]]).replace(b'None', b'null')
        rows = providers._normalize(providers._naver_frame(payload), 'KOSPI', '005930', date(2026,9,11), DAY)
        self.assertEqual(rows[0]['trading_volume'], 2**53+1)
        self.assertEqual(rows[1]['quality_flags'], ['source_missing_ohlcv'])
        self.assertIsNone(rows[1]['close_price'])
        json.dumps(rows, allow_nan=False)

    def test_actual_1990_source_row_omits_only_optional_foreign_percentage(self):
        payload = naver_body([['19900103', 44000, 45000, 43200, 44800, 26240]])
        rows = providers._normalize(providers._naver_frame(payload), 'KOSPI', '005930', date(1990,1,1), DAY)
        self.assertEqual((rows[0]['open_price'], rows[0]['high_price'], rows[0]['low_price'],
                          rows[0]['close_price'], rows[0]['trading_volume']), (44000., 45000., 43200., 44800., 26240))
        self.assertEqual(rows[0]['quality_flags'], [])

    def test_naver_empty_array_and_header_only_are_empty(self):
        self.assertTrue(providers._naver_frame(b'[]').empty)
        self.assertTrue(providers._naver_frame(naver_body([])).empty)

    def test_naver_invalid_literal_shape_and_dates_fail_safely(self):
        bad = [b'not_javascript()', b'{"error":"failed"}', b'[[1,2]]',
               naver_body([['20260914', 100]]), naver_body([['20260230',100,110,95,105,1000,0]]),
               naver_body([[20260914,100,110,95,105,1000,0]])]
        for content in bad:
            with self.subTest(content=content), self.assertRaises(providers.ProviderError):
                providers._naver_frame(content)

    def test_naver_boolean_numeric_fields_remain_invalid(self):
        payload = naver_body([['20260914',100,110,95,105,True,0]]).replace(b'True',b'true')
        with self.assertRaises(providers.ProviderError):
            providers._normalize(providers._naver_frame(payload), 'KOSPI', '005930', DAY, DAY)

    def test_yahoo_prefix_single_call_and_end_exclusive_conversion(self):
        reader = SimpleNamespace(DataReader=Mock(return_value=frame()), time=time)
        with patch.object(providers.importlib, 'import_module', return_value=reader):
            providers.fetch_prices('NASDAQ', 'AAPL', DAY, DAY)
        reader.DataReader.assert_called_once_with('YAHOO:AAPL', '2026-09-14', '2026-09-15')

    def test_yahoo_pre_epoch_dates_use_portable_utc_conversion_and_restore_module(self):
        original_mktime = time.mktime
        yahoo = SimpleNamespace(time=time)
        epochs = []

        def read(symbol, start, end):
            epochs.append(yahoo.time.mktime(date.fromisoformat(start).timetuple()))
            self.assertIs(time.mktime, original_mktime)
            return frame()

        reader = SimpleNamespace(DataReader=Mock(side_effect=read))
        modules = {'FinanceDataReader': reader, 'FinanceDataReader.yahoo.data': yahoo}
        with patch.object(providers.importlib, 'import_module', side_effect=modules.__getitem__):
            providers.fetch_prices('NASDAQ', 'AAPL', date(1900, 1, 1), DAY)
            providers.fetch_prices('NASDAQ', 'AAPL', date(1970, 1, 1), DAY)
        self.assertEqual(epochs, [-2208988800, 0])
        self.assertIs(yahoo.time, time)
        self.assertIs(time.mktime, original_mktime)

    def test_yahoo_clock_reference_restores_after_request_error(self):
        yahoo = SimpleNamespace(time=time)
        reader = SimpleNamespace(DataReader=Mock(side_effect=requests.ReadTimeout))
        modules = {'FinanceDataReader': reader, 'FinanceDataReader.yahoo.data': yahoo}
        with patch.object(providers.importlib, 'import_module', side_effect=modules.__getitem__):
            with self.assertRaises(providers.ProviderError):
                providers.fetch_prices('NASDAQ', 'AAPL', date(1900, 1, 1), DAY)
        self.assertIs(yahoo.time, time)

    def test_naver_never_reads_ambient_krx_credentials(self):
        response = SimpleNamespace(status_code=200, content=naver_body())
        with patch.dict(os.environ, {'KRX_ID':'test-id', 'KRX_PW':'test-password'}):
            with patch.object(requests.sessions.Session, 'request', return_value=response):
                with patch.object(providers.importlib, 'import_module') as importing:
                    providers.fetch_prices('KOSPI', '005930', DAY, DAY)
                importing.assert_not_called()

    def test_network_timeout_and_dependency_failure_are_safe_codes(self):
        for error, expected in [(requests.ReadTimeout('sensitive response'), 'timeout'),
                                (requests.ConnectionError('sensitive URL'), 'network_error'),
                                (ImportError('dependency'), 'dependency_missing')]:
            with self.subTest(expected=expected), patch.object(providers, '_read_frame', side_effect=error):
                with self.assertRaises(providers.ProviderError) as raised:
                    providers.fetch_prices('NASDAQ', 'AAPL', DAY, DAY)
                self.assertEqual(str(raised.exception), expected)
                self.assertFalse(raised.exception.halt_source)


class TransportTests(unittest.TestCase):
    def test_netrc_auth_injected_cookies_and_auth_headers_are_not_sent(self):
        session = requests.Session()
        session.auth = ('session-id', 'session-password')
        session.cookies.set('session-cookie', 'sensitive', domain='query2.finance.yahoo.com')
        session.headers.update({'Authorization': 'session-auth', 'Cookie': 'session-cookie=header', 'Proxy-Authorization': 'proxy-auth'})
        session.proxies = {'https': 'https://proxy-id:proxy-password@proxy.invalid'}
        session.cert = 'private-client-cert.pem'
        saved = {key: getattr(session, key) for key in ('trust_env', 'auth', 'cookies', 'headers', 'proxies', 'cert')}

        def send(prepared, **kwargs):
            for name in ('Authorization', 'Proxy-Authorization', 'Cookie'):
                self.assertNotIn(name, prepared.headers)
            self.assertEqual(prepared.headers['X-Requested-With'], 'XMLHttpRequest')
            self.assertFalse(session.trust_env)
            self.assertIsNone(session.auth)
            self.assertEqual(kwargs['proxies'], {})
            self.assertIsNone(kwargs['cert'])
            return SimpleNamespace(status_code=200)

        with patch.object(session, 'send', side_effect=send) as transport, patch('requests.sessions.get_netrc_auth') as netrc:
            with providers._public_transport('NASDAQ', 'AAPL'):
                session.get('https://query2.finance.yahoo.com/v8/finance/chart/AAPL',
                            auth=('request-id', 'request-password'), cookies={'request-cookie': 'sensitive'},
                            headers={'aUtHoRiZaTiOn': 'request-auth', 'cOoKiE': 'request-cookie=header',
                                     'X-Requested-With': 'XMLHttpRequest'})
            transport.assert_called_once()
            netrc.assert_not_called()
        for name, value in saved.items():
            self.assertIs(getattr(session, name), value)

    def test_session_authentication_objects_are_restored_after_timeout(self):
        session = requests.Session()
        session.auth = ('session-id', 'session-password')
        session.cookies.set('session-cookie', 'sensitive')
        saved = {key: getattr(session, key) for key in ('trust_env', 'auth', 'cookies', 'headers', 'proxies', 'cert')}
        with patch.object(session, 'send', side_effect=requests.ReadTimeout):
            with self.assertRaises(requests.ReadTimeout), providers._public_transport('NASDAQ', 'AAPL'):
                session.get('https://query2.finance.yahoo.com/v8/finance/chart/AAPL')
        for name, value in saved.items():
            self.assertIs(getattr(session, name), value)

    def test_timeout_is_applied_and_hook_restored(self):
        response = SimpleNamespace(status_code=200)
        original = requests.sessions.Session.request
        with patch.object(requests.sessions.Session, 'request', return_value=response) as network:
            with providers._public_transport('NASDAQ', 'AAPL'):
                self.assertIs(requests.get('https://query2.finance.yahoo.com/v8/finance/chart/AAPL'), response)
            self.assertIs(requests.sessions.Session.request, network)
            self.assertEqual(network.call_args.kwargs['timeout'], (5, 30))
            self.assertFalse(network.call_args.kwargs['allow_redirects'])
        self.assertIs(requests.sessions.Session.request, original)

    def test_access_and_rate_denials_halt_source_without_retry(self):
        for status in [401, 403, 429]:
            with self.subTest(status=status), patch.object(requests.sessions.Session, 'request', return_value=SimpleNamespace(status_code=status)) as network:
                with self.assertRaises(providers.ProviderError) as raised, providers._public_transport('KOSPI', '005930'):
                    requests.get('https://api.finance.naver.com/siseJson.naver')
                self.assertTrue(raised.exception.halt_source)
                self.assertEqual(raised.exception.details['http_status'], status)
                network.assert_called_once()

    def test_non_denial_http_error_is_distinct(self):
        with patch.object(requests.sessions.Session, 'request', return_value=SimpleNamespace(status_code=503)):
            with self.assertRaises(providers.ProviderError) as raised, providers._public_transport('NASDAQ', 'AAPL'):
                requests.get('https://query2.finance.yahoo.com/v8/finance/chart/AAPL')
        self.assertEqual(raised.exception.kind, 'http_error')
        self.assertFalse(raised.exception.halt_source)

    def test_redirects_are_not_followed_and_extra_requests_are_rejected(self):
        with patch.object(requests.sessions.Session, 'request', return_value=SimpleNamespace(status_code=302)) as network:
            with self.assertRaises(providers.ProviderError) as raised, providers._public_transport('NASDAQ', 'AAPL'):
                requests.get('https://query2.finance.yahoo.com/v8/finance/chart/AAPL')
            self.assertEqual(raised.exception.kind, 'unexpected_redirect')
            network.assert_called_once()
        with patch.object(requests.sessions.Session, 'request', return_value=SimpleNamespace(status_code=200)) as network:
            with self.assertRaises(providers.ProviderError) as raised, providers._public_transport('NASDAQ', 'AAPL'):
                requests.get('https://query2.finance.yahoo.com/v8/finance/chart/AAPL')
                requests.get('https://query2.finance.yahoo.com/v8/finance/chart/AAPL')
            self.assertEqual(raised.exception.kind, 'unexpected_extra_request')
            network.assert_called_once()

    def test_krx_login_or_alternate_source_never_reaches_transport(self):
        with patch.object(requests.sessions.Session, 'request') as network:
            with self.assertRaises(providers.ProviderError) as raised, providers._public_transport('KOSPI', '005930'):
                requests.post('https://data.krx.co.kr/login')
            self.assertEqual(raised.exception.kind, 'unexpected_endpoint')
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
