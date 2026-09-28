"""Normalize source metadata without fabricating analysis or company matches."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit, urlunsplit, unquote


class ValidationError(ValueError):
    """The input cannot safely represent a service document."""


def text(value, field, maximum=None, *, nullable=False):
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or not value.strip() or '\x00' in value
            or (maximum and len(value.strip()) > maximum)):
        raise ValidationError(f'{field} must be nonempty text' + (f' of at most {maximum} characters' if maximum else ''))
    return value.strip()


def timestamp(value, field, *, nullable=False):
    if value is None and nullable:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (AttributeError, TypeError, ValueError):
        raise ValidationError(f'{field} requires an ISO timestamp with timezone') from None


def canonical_url(value):
    value = text(value, 'URL', 16384)
    try:
        parsed = urlsplit(value)
        parsed.port  # Validate malformed/out-of-range ports as well.
    except ValueError:
        raise ValidationError('URL has an invalid host or port') from None
    if (parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValidationError('URL must be absolute HTTP(S) without credentials')
    if any(ord(ch) < 32 or ch.isspace() for ch in parsed.netloc):
        raise ValidationError('URL host must not contain whitespace or control characters')
    # Several providers return otherwise valid article URLs with literal spaces
    # or Unicode in the path/query. Preserve their identity by percent-encoding
    # those components instead of dropping the whole document. Existing escapes
    # and query ordering (including duplicate keys) are intentionally retained.
    path = quote(parsed.path, safe="/%:@-._~!$&'()*+,;=")
    query = quote(parsed.query, safe="=&?/:;+,%@-._~!$'()*")
    # Query parameters contain article IDs. Never remove or reorder them.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ''))


def company_ref(value):
    if not isinstance(value, Mapping):
        raise ValidationError('company reference must be an object')
    market = value.get('market')
    code = value.get('stock_code')
    pattern = r'[0-9A-Z]{6}' if market == 'KOSPI' else r'[A-Z0-9][A-Z0-9.-]{0,29}'
    if market not in {'KOSPI', 'NASDAQ'} or not isinstance(code, str) or not re.fullmatch(pattern, code):
        raise ValidationError('company reference requires an exact KOSPI/NASDAQ market and stock code')
    return {'market': market, 'stock_code': code}


def references(values):
    if not isinstance(values, list):
        raise ValidationError('company_refs must be a list')
    found = {tuple(company_ref(value).values()) for value in values}
    return [{'market': market, 'stock_code': code} for market, code in sorted(found)]


def analyzed_links(values, *, model_version, analyzed_at):
    if not isinstance(values, list):
        raise ValidationError('company_links must be a list')
    result, found = [], set()
    for value in values:
        if not isinstance(value, Mapping):
            raise ValidationError('company link must be an object')
        reference = company_ref(value)
        key = tuple(reference.values())
        confidence = value.get('confidence')
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not 0.5 <= confidence <= 1):
            raise ValidationError('AI company confidence must be between 0.5 and 1')
        if key in found:
            raise ValidationError('AI company links must be unique')
        found.add(key)
        result.append({**reference, 'mention_type': 'MENTION', 'confidence': float(confidence),
                       'model_version': model_version, 'analyzed_at': analyzed_at})
    return sorted(result, key=lambda value: (value['market'], value['stock_code']))


def hash_value(value, field, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValidationError(field + ' requires a lowercase SHA256')
    return value


def validate_records(records):
    result = []
    for number, row in enumerate(records, 1):
        try:
            if not isinstance(row, Mapping):
                raise ValidationError('document must be an object')
            kind = row.get('document_type')
            status = row.get('status', 'COLLECTED')
            if (kind not in {'NEWS', 'DISCLOSURE'} or status not in {'COLLECTED', 'ANALYZED'}
                    or (kind == 'DISCLOSURE' and status != 'COLLECTED')):
                raise ValidationError('document_type/status must describe a collected document or analyzed NEWS')
            raw_uri = text(row.get('hdfs_raw_uri'), 'hdfs_raw_uri', 16384)
            if '\x00' in raw_uri or any(ord(c) < 32 for c in raw_uri):
                raise ValidationError('raw URI contains control characters')
            parsed_uri = urlsplit(raw_uri)
            if parsed_uri.scheme not in {'hdfs', 'file'} or (parsed_uri.scheme == 'hdfs' and not parsed_uri.netloc):
                raise ValidationError('raw URI must be an absolute hdfs:// or file:// reference')
            if (not parsed_uri.path.startswith('/') or parsed_uri.path == '/' or parsed_uri.query
                    or (parsed_uri.scheme == 'file' and parsed_uri.netloc)
                    or '..' in PurePosixPath(unquote(parsed_uri.path)).parts):
                raise ValidationError('raw URI must reference an absolute file within its snapshot')
            if parsed_uri.fragment and (PurePosixPath(parsed_uri.fragment).is_absolute()
                                        or '..' in PurePosixPath(parsed_uri.fragment).parts):
                raise ValidationError('raw archive member must be a safe relative path')
            if parsed_uri.username or parsed_uri.password:
                raise ValidationError('raw URI must not include credentials')
            record = {
                'source_key': text(row.get('source_key'), 'source_key', 100),
                'document_type': kind, 'title': text(row.get('title'), 'title', 100000),
                'summary': text(row.get('summary'), 'summary', 100000, nullable=True),
                'original_url': canonical_url(row.get('original_url')),
                'published_at': timestamp(row.get('published_at'), 'published_at', nullable=True),
                'collected_at': timestamp(row.get('collected_at'), 'collected_at'),
                'content_hash': hash_value(row.get('content_hash'), 'content_hash', nullable=True),
                'hdfs_raw_uri': raw_uri, 'status': status,
                'company_refs': references(row.get('company_refs', [])),
            }
            if kind == 'NEWS':
                canonical = canonical_url(row.get('canonical_url'))
                digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
                if row.get('canonical_url_hash') not in (None, digest):
                    raise ValidationError('canonical_url_hash mismatch')
                record.update(canonical_url=canonical, canonical_url_hash=digest,
                              publisher=text(row.get('publisher'), 'publisher', 200, nullable=True),
                              author=text(row.get('author'), 'author', 200, nullable=True))
                if status == 'ANALYZED':
                    analysis_version = text(row.get('analysis_version'), 'analysis_version', 50)
                    model_version = text(row.get('model_version'), 'model_version', 50)
                    analyzed_at = timestamp(row.get('analyzed_at'), 'analyzed_at')
                    links = analyzed_links(row.get('company_links'), model_version=model_version,
                                           analyzed_at=analyzed_at)
                    if record['company_refs'] != references(links):
                        raise ValidationError('company_refs must exactly match AI company_links')
                    record.update(analysis_version=analysis_version, model_version=model_version,
                                  analyzed_at=analyzed_at, company_links=links)
                elif any(row.get(key) is not None for key in
                         ('analysis_version', 'model_version', 'analyzed_at', 'company_links')):
                    raise ValidationError('collected NEWS cannot assert AI analysis fields')
            else:
                filing_system = row.get('filing_system')
                if filing_system not in {'DART', 'SEC'}:
                    raise ValidationError('filing_system must be DART or SEC')
                raw_date = row.get('filing_date')
                if not isinstance(raw_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
                    raise ValidationError('filing_date requires YYYY-MM-DD')
                date.fromisoformat(raw_date)
                receipt, accession, cik = row.get('dart_receipt_no'), row.get('sec_accession_no'), row.get('sec_cik')
                if filing_system == 'DART':
                    if not isinstance(receipt, str) or not re.fullmatch(r'\d{14}', receipt) or accession is not None or cik is not None:
                        raise ValidationError('DART requires its receipt and no SEC identity')
                elif (receipt is not None or not isinstance(accession, str) or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession)
                      or not isinstance(cik, str) or not re.fullmatch(r'\d{10}', cik) or int(cik) == 0):
                    raise ValidationError('SEC requires its accession and a ten-digit nonzero CIK')
                issuer = company_ref(row.get('filing_company'))
                record.update(filing_system=filing_system, filing_company=issuer,
                              dart_receipt_no=receipt, sec_accession_no=accession, sec_cik=cik,
                              filing_date=raw_date, report_name=text(row.get('report_name'), 'report_name', 300),
                              report_code=text(row.get('report_code'), 'report_code', 30, nullable=True),
                              disclosure_type=text(row.get('disclosure_type'), 'disclosure_type', 50, nullable=True),
                              correction_status=text(row.get('correction_status'), 'correction_status', 30, nullable=True))
                record['company_refs'] = references(record['company_refs'] + [issuer])
            result.append(record)
        except (ValueError, TypeError) as error:
            raise ValidationError(f'record {number}: {error}') from None
    return result


def _issuer(kind, raw, company_map):
    key = str(raw.get('corp_code' if kind == 'dart' else 'cik', ''))
    if kind == 'sec':
        if not re.fullmatch(r'\d{1,10}', key) or int(key) == 0:
            raise ValidationError('SEC CIK is invalid')
        key = key.zfill(10)
    explicit = company_map.get(kind, {}).get(key)
    if explicit is not None:
        reference = company_ref(explicit)
        if reference['market'] != ('KOSPI' if kind == 'dart' else 'NASDAQ'):
            raise ValidationError('issuer mapping market conflicts with this collector universe')
        code = raw.get('stock_code') if kind == 'dart' else None
        if code and reference != {'market': 'KOSPI', 'stock_code': code}:
            raise ValidationError('explicit DART company mapping conflicts with source stock_code')
        symbols = raw.get('symbols', raw.get('nasdaq100Symbols', raw.get('tickers', [])))
        if kind == 'sec' and symbols and reference['stock_code'] not in symbols:
            raise ValidationError('explicit SEC company mapping conflicts with source symbols')
        return reference
    if kind == 'dart' and raw.get('stock_code'):
        return company_ref({'market': 'KOSPI', 'stock_code': raw['stock_code']})
    symbols = raw.get('symbols', raw.get('nasdaq100Symbols', raw.get('tickers', [])))
    if kind == 'sec' and isinstance(symbols, list) and len(symbols) == 1:
        return company_ref({'market': 'NASDAQ', 'stock_code': symbols[0]})
    raise ValidationError(f'{kind.upper()} issuer {key} requires an explicit company-map entry')


def normalize_batch(batch, *, company_map=None):
    """Convert verified source envelopes, retaining only source-confirmed metadata."""
    company_map = {} if company_map is None else company_map
    if not isinstance(company_map, Mapping):
        raise ValidationError('company_map must be an object')
    if set(company_map) - {'dart', 'sec'}:
        raise ValidationError('company_map supports only dart and sec issuer maps')
    for kind, entries in company_map.items():
        if not isinstance(entries, Mapping):
            raise ValidationError('each issuer map must be an object')
        for key, value in entries.items():
            width = 8 if kind == 'dart' else 10
            if not isinstance(key, str) or not re.fullmatch(r'\d{' + str(width) + r'}', key):
                raise ValidationError('issuer map keys must be zero-padded source identifiers')
            company_ref(value)
    normalized, sources = [], {}
    for envelope in batch['records']:
        kind, raw = envelope['kind'], dict(envelope['record'])
        provenance = envelope.get('provenance', {})
        if kind == 'news' and isinstance(raw.get('raw_json'), str):
            raw = json.loads(raw['raw_json'])
        collected = raw.get('collected_at', raw.get('collectedAt')) or provenance.get('collected_at') or provenance.get('captured_at') or provenance.get('completed_at')
        common = {'summary': None, 'company_refs': [], 'status': 'COLLECTED',
                  'collected_at': collected, 'hdfs_raw_uri': envelope['raw_uri']}
        if kind in {'news', 'news-analyzed', 'news-historical-analyzed'}:
            region, provider = raw.get('region'), raw.get('source')
            if region not in {'domestic', 'overseas'}:
                raise ValidationError('news region must be domestic or overseas')
            provider = text(provider, 'news source', 70)
            source_key = f'news:{region}:{provider}'
            body = raw.get('content')
            # The body remains in HDFS and is not written to PostgreSQL. Some
            # archived pages contain embedded NUL bytes, which PostgreSQL text
            # cannot store but which are valid in the immutable Parquet source.
            # Accept them here while still requiring real text and validating
            # the hash over the exact, unmodified source bytes.
            if not isinstance(body, str) or not body.strip():
                raise ValidationError('news content must be nonempty text')
            digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
            if raw.get('content_hash') not in (None, digest):
                raise ValidationError('news content_hash does not match preserved body')
            metadata = raw.get('metadata') or {}
            if not isinstance(metadata, Mapping):
                raise ValidationError('news metadata must be an object')
            links = []
            if kind == 'news':
                matches = metadata.get('matched_companies', [])
                if not isinstance(matches, list):
                    raise ValidationError('matched_companies must be a list')
                refs = []
                for match in matches:
                    if not isinstance(match, Mapping) or region != 'domestic':
                        raise ValidationError('unexpected confirmed company match')
                    fields = match.get('fields', [])
                    if not isinstance(fields, list) or not set(fields).intersection({'title', 'content'}):
                        raise ValidationError('company match lacks source title/body evidence')
                    refs.append({'market': 'KOSPI', 'stock_code': match.get('ticker')})
            else:
                refs = [{'market': match.get('market'), 'stock_code': match.get('stock_code')}
                        for match in raw.get('companies', [])]
                links = [{**reference, 'confidence': match.get('confidence')}
                         for reference, match in zip(refs, raw.get('companies', []))]
            publisher = raw.get('organization') or None
            # Some historical extraction output put the headline here. Omit an
            # unsupported publisher rather than treating a headline as a name.
            if isinstance(publisher, str) and (len(publisher.strip()) > 200 or publisher.strip() == str(raw.get('title', '')).strip()):
                publisher = None
            common.update(source_key=source_key, document_type='NEWS', title=raw.get('title'),
                          original_url=raw.get('url'), canonical_url=raw.get('url'),
                          published_at=raw.get('published_at'), content_hash=digest,
                          publisher=publisher, author=raw.get('author') or None,
                          company_refs=refs)
            if kind in {'news-analyzed', 'news-historical-analyzed'}:
                common.update(status='ANALYZED', analysis_version=raw.get('analysis_version'),
                              model_version=raw.get('model_version'), analyzed_at=raw.get('analyzed_at'),
                              company_links=links)
            sources[source_key] = {'name': source_key, 'source_type': 'NEWS', 'base_url': None}
        elif kind in {'dart', 'sec'}:
            issuer = _issuer(kind, raw, company_map)
            common.update(source_key=kind, document_type='DISCLOSURE', filing_company=issuer,
                          company_refs=[issuer], filing_system=kind.upper(), report_code=None,
                          disclosure_type=None, correction_status=None,
                          dart_receipt_no=None, sec_accession_no=None, sec_cik=None)
            if kind == 'dart':
                receipt = raw.get('rcept_no', raw.get('receipt'))
                day = raw.get('rcept_dt', '')
                if not isinstance(day, str) or not re.fullmatch(r'\d{8}', day):
                    raise ValidationError('DART filing date requires YYYYMMDD')
                common.update(title=raw.get('report_nm'), report_name=raw.get('report_nm'),
                              original_url=f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}',
                              published_at=None, content_hash=raw.get('raw_sha256'), dart_receipt_no=receipt,
                              filing_date=f'{day[:4]}-{day[4:6]}-{day[6:]}')
            else:
                form = text(raw.get('form'), 'SEC form', 30)
                common.update(title=f"{raw.get('company') or issuer['stock_code']} {form} {raw.get('filingDate')}",
                              report_name=raw.get('primaryDocDescription') or form, report_code=form,
                              disclosure_type=form, original_url=raw.get('sourceUrl'),
                              published_at=raw.get('acceptanceDateTime'), content_hash=raw.get('sha256'),
                              sec_accession_no=raw.get('accessionNumber'), sec_cik=str(raw['cik']).zfill(10),
                              filing_date=raw.get('filingDate'))
            sources[kind] = {'name': kind.upper(), 'source_type': 'DISCLOSURE',
                             'base_url': 'https://dart.fss.or.kr' if kind == 'dart' else 'https://www.sec.gov'}
        else:
            raise ValidationError('unsupported source envelope kind')
        normalized.append(common)
    return {'records': validate_records(normalized), 'sources': sources}
