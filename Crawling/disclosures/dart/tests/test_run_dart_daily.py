import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import collect_dart_rank_range as core
from scripts import run_dart_daily as daily
from tests.test_upload_dart_hdfs_snapshot import FakeHdfs


class DailyHdfs(FakeHdfs):
    fail_after_move = False

    def run(self, *args):
        if args[0] == '-ls':
            return '\n'.join('drwxr-xr-x - ubuntu group 0 2026-09-15 00:00 ' + path
                             for path in sorted(self.directories)
                             if path.startswith(args[1] + '/') and '/' not in path[len(args[1]) + 1:])
        result = super().run(*args)
        if args[0] == '-mv' and self.fail_after_move:
            self.fail_after_move = False
            raise RuntimeError('Injected crash after rename')
        return result


class Backend:
    def __init__(self):
        self.rows = {}
        self.unavailable = set()
        self.broken = set()
        self.empty = set()
        self.calls = []
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as handle:
            handle.writestr('report.xml', '<DOCUMENT><P>Valid fixture body</P></DOCUMENT>')
        self.zip = archive.getvalue()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as handle:
            handle.writestr('unsupported.bin', b'fixture without parseable text')
        self.unparseable_zip = archive.getvalue()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as handle:
            handle.writestr('report.xml', '<DOCUMENT><P>  </P></DOCUMENT>')
        self.empty_zip = archive.getvalue()

    def api_factory(self, *args, **kwargs):
        backend = self

        class Api(core.Api):
            def request(self, endpoint, params):
                self.reserve(endpoint, params)
                backend.calls.append((endpoint, dict(params)))
                if endpoint == 'list.json':
                    rows = backend.rows.get(params['bgn_de'], []) if params['corp_code'] == '00000001' else []
                    return json.dumps({'status': '000' if rows else '013', 'list': rows, 'total_page': 1}).encode()
                receipt = params['rcept_no']
                if receipt in backend.unavailable:
                    return b'<result><status>013</status></result>'
                if receipt in backend.broken:
                    return backend.unparseable_zip
                if receipt in backend.empty:
                    return backend.empty_zip
                return backend.zip

        return Api(*args, **kwargs)


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / 'daily'
        self.universe = self.root / 'universe.jsonl'
        self.universe.write_text(''.join(json.dumps({'universe_rank': n, 'corp_code': f'{n:08d}',
            'stock_code': f'{n:06d}', 'corp_name': f'Fixture {n}', 'snapshot_date': '2026-09-07'}) + '\n'
            for n in range(1, 101)), encoding='utf-8')
        self.key = self.root / 'key.env'
        self.key.write_text('DART_API_KEY=' + 'D' * 40, encoding='utf-8')
        self.backend, self.hdfs = Backend(), DailyHdfs()
        self.today = '20260915'

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, **kwargs):
        return daily.Runner(self.output, self.universe, self.today, self.root / 'shared-ledger', self.key,
                            hdfs=self.hdfs, interval=0, api_factory=self.backend.api_factory, **kwargs)

    def row(self, suffix='000001'):
        return {'rcept_no': self.today + suffix, 'rcept_dt': self.today, 'corp_code': '00000001',
                'corp_name': 'Fixture 1', 'report_nm': 'Fixture disclosure'}

    def document_calls(self, receipt):
        return sum(endpoint == 'document.xml' and params['rcept_no'] == receipt for endpoint, params in self.backend.calls)

    def published(self):
        return json.loads((self.output / 'checkpoint.json').read_text())['published']

    def test_today_is_requeried_and_only_new_receipts_are_published(self):
        first, second = self.row(), self.row('000002')
        self.backend.rows[self.today] = [first]
        self.assertEqual(self.runner().run(self.today), 0)
        self.backend.rows[self.today] = [first, second]
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(set(self.published()), {first['rcept_no'], second['rcept_no']})
        self.assertEqual(self.document_calls(first['rcept_no']), 1)
        self.assertEqual(self.document_calls(second['rcept_no']), 1)
        self.assertEqual(sum(endpoint == 'list.json' for endpoint, _ in self.backend.calls), 200)
        state = json.loads((self.output / 'checkpoint.json').read_text())
        self.assertEqual(len(state['snapshots']), 2)
        for path, content in self.hdfs.files.items():
            if path.endswith('/manifest.json'):
                manifest = json.loads(content)
                self.assertEqual((manifest['rank_from'], manifest['rank_to']), (1, 100))
                self.assertEqual(manifest['snapshot_documents'], 1)
            if path.endswith('/metadata/universe.json'):
                self.assertEqual(len(json.loads(content)), 100)

    def test_empty_today_does_not_freeze_future_polling(self):
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertFalse(self.hdfs.files)
        row = self.row()
        self.backend.rows[self.today] = [row]
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertIn(row['rcept_no'], self.published())

    def test_source_unavailable_is_retried_next_pass(self):
        row = self.row()
        self.backend.rows[self.today] = [row]
        self.backend.unavailable.add(row['rcept_no'])
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertFalse(self.published())
        state = json.loads((self.output / 'checkpoint.json').read_text())
        self.assertEqual(state['days'][self.today]['pending_count'], 1)
        self.backend.unavailable.clear()
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(self.document_calls(row['rcept_no']), 2)
        self.assertIn(row['rcept_no'], self.published())

    def test_crash_after_hdfs_rename_retries_same_snapshot_before_ack(self):
        row = self.row()
        self.backend.rows[self.today] = [row]
        self.hdfs.fail_after_move = True
        self.assertEqual(self.runner().run(self.today), 75)
        self.assertFalse(self.published())
        state = json.loads((self.output / 'checkpoint.json').read_text())
        self.assertIsNotNone(state['pending_upload'])
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(self.document_calls(row['rcept_no']), 1)
        self.assertEqual(sum(command[0] == '-put' for command in self.hdfs.commands), 1)
        self.assertIn(row['rcept_no'], self.published())

    def test_bad_hdfs_hash_never_advances_ack(self):
        self.backend.rows[self.today] = [self.row()]
        self.hdfs.corrupt = True
        self.assertEqual(self.runner().run(self.today), 75)
        self.assertFalse(self.published())
        self.hdfs.corrupt = False
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(len(self.published()), 1)

    def test_lost_checkpoint_rebuilds_ack_from_verified_hdfs_only(self):
        row = self.row()
        self.backend.rows[self.today] = [row]
        self.assertEqual(self.runner().run(self.today), 0)
        (self.output / 'checkpoint.json').unlink()
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(self.document_calls(row['rcept_no']), 1)
        self.assertEqual(sum(command[0] == '-put' for command in self.hdfs.commands), 1)

    def test_one_bad_document_does_not_block_valid_delta(self):
        first, second = self.row(), self.row('000002')
        self.backend.rows[self.today] = [first, second]
        self.backend.broken.add(first['rcept_no'])
        # Rows are newest-first; second is cached before first fails.
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(set(self.published()), {second['rcept_no']})
        state = json.loads((self.output / 'checkpoint.json').read_text())
        self.assertEqual(state['days'][self.today]['pending_receipts'], [first['rcept_no']])
        self.assertEqual(state['days'][self.today]['source_error_count'], 1)
        exception_files = [content for path, content in self.hdfs.files.items() if path.endswith('/metadata/source_exceptions.json')]
        self.assertEqual(json.loads(exception_files[0])['errors'][0]['status'], 'parse_error')
        self.backend.broken.clear()
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(self.document_calls(second['rcept_no']), 1)
        self.assertEqual(self.document_calls(first['rcept_no']), 2)

    def test_preparation_failure_reuses_only_hash_verified_originals(self):
        first, second = self.row(), self.row('000002')
        self.backend.rows[self.today] = [first, second]
        with patch.object(daily, 'prepare', side_effect=RuntimeError('Injected packaging crash')):
            self.assertEqual(self.runner().run(self.today), 75)
        self.assertFalse(self.published())
        cache = json.loads((self.output / 'raw-cache.json').read_text())
        Path(cache[first['rcept_no']]['path']).write_bytes(b'corrupt cached ZIP')
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(self.document_calls(first['rcept_no']), 2)
        self.assertEqual(self.document_calls(second['rcept_no']), 1)

    def test_empty_xml_stays_pending_while_valid_document_is_published(self):
        empty, valid = self.row(), self.row('000002')
        self.backend.rows[self.today] = [empty, valid]
        self.backend.empty.add(empty['rcept_no'])
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(set(self.published()), {valid['rcept_no']})
        state = json.loads((self.output / 'checkpoint.json').read_text())
        self.assertEqual(state['days'][self.today]['pending_receipts'], [empty['rcept_no']])
        raw_cache = json.loads((self.output / 'raw-cache.json').read_text())
        self.assertNotIn(empty['rcept_no'], raw_cache)
        self.backend.empty.clear()
        self.assertEqual(self.runner().run(self.today), 0)
        self.assertEqual(set(self.published()), {valid['rcept_no'], empty['rcept_no']})
        self.assertEqual(self.document_calls(empty['rcept_no']), 2)
        self.assertEqual(self.document_calls(valid['rcept_no']), 1)

    def test_maximum_one_checks_today_before_backlog(self):
        self.assertEqual(daily.dates_to_check('20260910', '20260915', {}, 3, 1), ['20260915'])

    def test_shared_quota_ledger_prevents_double_budget(self):
        api = core.Api('D' * 40, self.root / 'historical', interval=0,
                       ledger_dir=self.root / 'shared-ledger', daily_limit=2)
        api.reserve('list.json', {'fixture': 'historical reservation'})
        self.assertEqual(self.runner(daily_limit=2).run(self.today), 75)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertFalse(self.published())
        state = json.loads((self.output / 'checkpoint.json').read_text())
        self.assertEqual(state['stop_reason'], 'local_daily_request_limit_reached')

    def test_same_output_cannot_run_concurrently(self):
        with core.FileLock(self.output / '.daily-run.lock'):
            with self.assertRaises(core.CollectorError):
                self.runner().run(self.today)

    def test_key_file_wins_over_inherited_environment(self):
        with patch.dict('os.environ', {'DART_API_KEY': 'WRONG_INHERITED_VALUE'}):
            self.assertEqual(daily.read_key_file(self.key), 'D' * 40)

    def test_overlap_and_failed_older_date_are_bounded(self):
        days = {f'202609{day:02d}': {'status': 'checked', 'checked_at': '2026-09-14'} for day in range(10, 16)}
        days['20260910'] = {'status': 'checked', 'checked_at': '2026-09-10', 'pending_count': 1}
        self.assertEqual(daily.dates_to_check('20260910', '20260915', days, 3, 5),
                         ['20260915', '20260914', '20260913', '20260910'])


if __name__ == '__main__':
    unittest.main()
