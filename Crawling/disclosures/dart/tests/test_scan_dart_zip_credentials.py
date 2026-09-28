import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.scan_dart_zip_credentials import scan


class CredentialScanTests(unittest.TestCase):
    def test_one_configured_key_is_sufficient_and_not_printed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {}, clear=True):
            root = Path(directory)
            key = 'TEST_ONLY_NOT_A_REAL_KEY_0123456789ABCDEFG'
            key_file = root / 'private.env'
            key_file.write_text('DART_API_KEY=' + key, encoding='utf-8')
            archive = root / 'fixture.zip'
            with zipfile.ZipFile(archive, 'w') as target:
                target.writestr('company.csv', 'rcept_no\n20260101000001\n')
            stdout = io.StringIO()
            report = root / 'check.json'
            with contextlib.redirect_stdout(stdout):
                scan(archive, report, root, [key_file])
            result = json.loads(report.read_text())
            self.assertEqual(result['keys_checked'], 1)
            self.assertEqual(result['matches'], 0)
            self.assertNotIn(key, stdout.getvalue())
            self.assertNotIn(key, report.read_text())

    def test_known_key_inside_archive_blocks_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = 'TEST_ONLY_NOT_A_REAL_KEY_0123456789ABCDEFG'
            archive = root / 'fixture.zip'
            with zipfile.ZipFile(archive, 'w') as target:
                target.writestr('company.csv', 'accident=' + key)
            with patch.dict('os.environ', {'DART_API_KEY': key}), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, 'Known credential text found'):
                    scan(archive, root / 'check.json', root)
            self.assertEqual(json.loads((root / 'check.json').read_text())['credential_text_check'], 'failed')

    def test_no_known_keys_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {}, clear=True):
            root = Path(directory)
            archive = root / 'fixture.zip'
            with zipfile.ZipFile(archive, 'w') as target:
                target.writestr('company.csv', 'safe fixture')
            with self.assertRaisesRegex(ValueError, 'at least one known credential'):
                scan(archive, root / 'check.json', root)


if __name__ == '__main__':
    unittest.main()
