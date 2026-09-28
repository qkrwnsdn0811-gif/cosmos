"""Validate one published snapshot, then optionally load its document metadata."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

from .contract import normalize_batch, ValidationError
from .postgres import load_documents, LoadError
from .sources import load_source


def json_file(path, label):
    if path is None:
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValidationError(label + ' must contain a JSON object')
    return value


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    for name in ('validate', 'load'):
        command = commands.add_parser(name)
        command.add_argument('--kind', required=True,
                             choices=('news', 'news-analyzed', 'news-historical-analyzed',
                                      'dart', 'sec', 'sec-incremental', 'sec-batch'))
        command.add_argument('--input', required=True, help='Completed HDFS snapshot URI or local directory')
        command.add_argument('--hdfs-bin', default=os.environ.get('HDFS_BIN', 'hdfs'))
        command.add_argument('--source-uri', help='Original HDFS URI when validating a local mirror')
        command.add_argument('--expected-index-sha256', help='Optional independently obtained legacy SEC index hash')
        command.add_argument('--company-map', type=Path, help='Explicit issuer mapping for ambiguous/missing symbols')
        command.add_argument('--sources-file', type=Path, help='Override source registry names for existing data_source rows')
        if name == 'load':
            command.add_argument('--register-sources', action='store_true', help='Create missing explicitly described sources in the same transaction')
            command.add_argument('--commit', action='store_true', help='Commit writes; the default runs all SQL then rolls back')
    return result


def execute(args):
    batch = load_source(args.kind, args.input, hdfs_bin=args.hdfs_bin,
                        expected_index_sha256=args.expected_index_sha256, source_uri=args.source_uri)
    normalized = normalize_batch(batch, company_map=json_file(args.company_map, 'company map'))
    sources = json_file(args.sources_file, 'sources file')
    if sources is None:
        sources = normalized['sources']
    result = {
        'format': batch['format'], 'input_uri': batch['input_uri'],
        'batch_id': batch['batch_id'], 'manifest_sha256': batch['manifest_sha256'],
        'records': len(normalized['records']),
        'document_types': dict(Counter(row['document_type'] for row in normalized['records'])),
        'required_companies': sorted({(ref['market'], ref['stock_code'])
                                      for row in normalized['records'] for ref in row['company_refs']}),
        'sources': sources, 'verification': batch['verification'],
    }
    if args.command == 'validate':
        result['status'] = 'validated'
        return result
    dsn = os.environ.get('DOCUMENT_DATABASE_URL')
    if not dsn:
        raise ValidationError('DOCUMENT_DATABASE_URL must be set outside the repository')
    import psycopg
    with psycopg.connect(dsn, connect_timeout=10) as connection:
        result['load'] = load_documents(connection, normalized['records'], batch_id=batch['batch_id'],
            manifest_sha256=batch['manifest_sha256'], source_uri=batch['input_uri'], sources=sources,
            register_sources=args.register_sources, commit=args.commit)
    result['status'] = 'dry_run' if not args.commit else ('already_loaded' if result['load']['already_loaded'] else 'loaded')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ValueError, OSError) as exc:
        # Input errors are controlled messages. Never print raw DB exceptions or a DSN.
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__,
                          'message': 'Document load failed; retry the same batch to confirm its checkpoint.',
                          'sqlstate': getattr(exc, 'sqlstate', None)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
