"""Read every ZIP member, validate CRC, and check known local key strings without printing them."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


def scan(archive, report, project, key_files=()):
    archive, report = Path(archive).resolve(), Path(report).resolve()
    before = archive.stat()
    keys = set()
    for key_file in set(Path(project).glob('.env.dart*.local')) | set(map(Path, key_files)):
        for line in key_file.read_text(encoding='utf-8-sig').splitlines():
            if re.match(r'\s*DART_API_KEY\s*=', line):
                value = line.split('=', 1)[1].strip().strip('\"\'')
                if value:
                    keys.add(value.encode())
    if os.environ.get('DART_API_KEY', '').strip():
        keys.add(os.environ['DART_API_KEY'].strip().encode())
    if not keys:
        raise ValueError('Provide at least one known credential via DART_API_KEY or --key-file')
    longest = max(map(len, keys))
    matches = 0
    scanned = 0
    names = set()
    members = []
    with zipfile.ZipFile(archive, metadata_encoding='cp949') as package:
        for info in package.infolist():
            name = info.filename
            path = PurePosixPath(name)
            if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name
                    or name in names or info.flag_bits & 1
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError('Unsafe ZIP member metadata')
            names.add(name)
            if info.is_dir():
                continue
            member_hash = hashlib.sha256()
            count, tail = 0, b''
            with package.open(info) as stream:
                while block := stream.read(4 * 1024 * 1024):
                    combined = tail + block
                    matches += sum(key in combined for key in keys)
                    tail = combined[-(longest - 1):]
                    member_hash.update(block)
                    count += len(block)
            if count != info.file_size:
                raise ValueError('ZIP member length mismatch')
            scanned += count
            members.append({'name': name, 'bytes': count, 'crc32': f'{info.CRC:08x}', 'sha256': member_hash.hexdigest()})
            if len(members) % 10 == 0:
                print(json.dumps({'members_checked': len(members), 'uncompressed_bytes_checked': scanned}), flush=True)
    archive_hash = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            archive_hash.update(block)
    after = archive.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Archive changed during scan')
    value = {'archive_name': archive.name, 'archive_bytes': after.st_size,
             'archive_sha256': archive_hash.hexdigest(), 'crc_check': 'passed',
             'keys_checked': len(keys), 'matches': matches,
             'credential_text_check': 'passed' if matches == 0 else 'failed',
             'zip_metadata_encoding': 'cp949', 'member_count': len(members),
             'uncompressed_bytes': scanned, 'members': members,
             'checked_at': datetime.now(timezone.utc).isoformat()}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in value.items() if k != 'members'}), flush=True)
    if matches:
        raise ValueError('Known credential text found; upload must not proceed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, action='append', default=[], help='Known private key file; repeat for keys used with this archive')
    args = parser.parse_args()
    scan(args.archive, args.report, Path(__file__).resolve().parents[1], args.key_file)
