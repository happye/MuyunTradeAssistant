"""Explicit, bounded current official PDF qualification, isolated from research factors."""
import hashlib
import copy
import math
import re
import time
from queue import Empty, Queue
from threading import Thread
from datetime import date, datetime
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from urllib.parse import urlsplit

from src.core import cash_numeric_verification as numeric
from src.core.cash_material_intake import _encoded, _json, _public, _sid
from src.data.research_snapshot import EvidenceRecord, EvidenceSnapshot

VERSION = 'cash_source_check.v1'
SOURCE_VERSION = 'cninfo_exact_https_pdf.v1'
FIELD_VERSION = 'cninfo_q1_consolidated_total.v1'
FORMULA_VERSION = 'cash_ratio_decimal34_half_even.v1'
TIMEOUT = 30
JOINT_VERSION = 'cash_source_check_industry.v2'
CURRENT_FIELD_PROTOCOL = 'cash_current_official_context.v2'
_CURRENT_SEAL = object()


@dataclass(frozen=True)
class _CurrentCashContext:
    security_id: str
    material_id: str
    pdf_sha256: str
    source_uri: str
    snapshot: EvidenceSnapshot
    fields: tuple
    _seal: object


def _current_context(sid, material_id, pdf_hash, uri, snapshot, fields):
    """Successor identities add explicit period/role metadata; v41 is unchanged."""
    identities = {f['evidence_id']: hashlib.sha256(_encoded([CURRENT_FIELD_PROTOCOL,
                  material_id, pdf_hash, f['evidence_id']])).hexdigest() for f in fields}
    updated = copy.deepcopy(fields)
    for field in updated:
        old_id = field['evidence_id']
        field.update(evidence_id=identities[old_id], original_evidence_id=old_id,
                     qualification_version=CURRENT_FIELD_PROTOCOL, consolidation_scope='CONSOLIDATED')
        if 'operand_evidence_ids' in field:
            field['operand_evidence_ids'] = [identities[e] for e in field['operand_evidence_ids']]
    by_old = {f['original_evidence_id']: f for f in updated}
    records = []
    for original in snapshot.records:
        field = by_old[original.evidence_id]
        record = original.model_copy(deep=True, update=dict(evidence_id=field['evidence_id'],
            period_start=field['period_start'], column_role=field['role'],
            qualification_version=CURRENT_FIELD_PROTOCOL, consolidation_scope='CONSOLIDATED',
            original_evidence_id=original.evidence_id))
        if record.metric_or_claim == 'CFOToNP':
            record.lineage_ids = field['operand_evidence_ids']
            record.formula = copy.deepcopy(field)
        records.append(record)
    current = EvidenceSnapshot.build(sid, snapshot.as_of, records, strict=False)
    return _CurrentCashContext(sid, material_id, pdf_hash, uri, current, tuple(updated), _CURRENT_SEAL)


def validate_uri(uri):
    """Validate the sole approved source shape before any DNS or request."""
    if not isinstance(uri, str) or len(uri) > 2048:
        raise ValueError('source_uri_missing')
    if not re.fullmatch(r'https://static[.]cninfo[.]com[.]cn(?::443)?/finalpage/(20[0-9]{2}-[0-9]{2}-[0-9]{2})/[0-9]{10}[.]PDF', uri):
        raise ValueError('source_uri_not_allowed')
    parts = urlsplit(uri)
    date.fromisoformat(parts.path.split('/')[2])
    return uri


def _http_pdf(uri, *, timeout):
    """One request, verified TLS, no redirects or retries; memory only."""
    import requests
    deadline = time.monotonic() + timeout
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise ValueError('source_timeout')
        return left
    with requests.Session() as session:
        budget = remaining()
        with session.get(uri, timeout=(min(5, budget), min(5, budget)), allow_redirects=False,
                         stream=True, verify=True) as response:
            remaining()
            if response.status_code != 200:
                raise ValueError('http_status_' + str(response.status_code))
            mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
            if mime != 'application/pdf':
                raise ValueError('pdf_mime_invalid')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > numeric.PDF_LIMIT):
                raise ValueError('pdf_size_limit')
            data, size = [], 0
            iterator = iter(response.iter_content(65536))
            while True:
                remaining()
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                remaining()
                size += len(chunk)
                if size > numeric.PDF_LIMIT:
                    raise ValueError('pdf_size_limit')
                data.append(chunk)
            remaining()
            payload = b''.join(data)
            if not payload.startswith(b'%PDF-'):
                raise ValueError('pdf_header_invalid')
            return payload


def _bounded_download(uri, *, fetcher, timeout):
    """Shared daemon/memory bound for the two explicitly approved fetches only."""
    results = Queue(maxsize=1)
    def download():
        # No store, field constructor or success callback enters this thread.
        try:
            outcome = (True, fetcher(uri, timeout=timeout))
        except Exception as error:
            outcome = (False, error)
        results.put_nowait(outcome)
    Thread(target=download, name='cash-official-download', daemon=True).start()
    try:
        successful, data = results.get(timeout=timeout)
    except Empty:
        raise ValueError('source_timeout') from None
    if not successful:
        raise data
    return data


def _bounded_fetch(uri, *, fetcher=None):
    data = _bounded_download(uri, fetcher=fetcher or _http_pdf, timeout=TIMEOUT)
    if not isinstance(data, bytes) or len(data) > numeric.PDF_LIMIT or not data.startswith(b'%PDF-'):
        raise ValueError('pdf_header_or_size_invalid')
    return data


def _typed(value):
    number = float(value)
    if not math.isfinite(number) or (value != 0 and number == 0):
        raise ValueError('typed_representation_unavailable')
    return number


def _qualified_fields(sid, rows, *, source_uri, pdf_hash, material_id, observed_at, requested_as_of):
    """Private constructor, consumed only from this call's parsed PDF bytes."""
    records, fields, gaps = [], [], []
    for row in rows:
        if date.fromisoformat(row['period_end']) > observed_at.date():
            gaps.append(row['table'] + ':period_after_observed_as_of')
            continue
        value = numeric._number(row['token'])
        metric = 'netProfit' if row['table'] == 'NP' else 'operatingCashFlow'
        identity = hashlib.sha256(_encoded([VERSION, material_id, pdf_hash, row])).hexdigest()
        item = dict(metric=metric, decimal_value=str(value), display_value=str(value), evidence_id=identity,
                    unit='元', currency='CNY', **{k: v for k, v in row.items() if k != 'token'})
        fields.append(item)
        try:
            number = _typed(value)
        except (ValueError, OverflowError):
            item['typed_status'] = 'UNREPRESENTABLE'
            gaps.append(metric + ':typed_representation_unavailable')
            continue
        item['typed_status'] = 'OBSERVED'
        records.append(EvidenceRecord(evidence_id=identity, source_kind='financial', source_uri=source_uri,
            source_document_hash=pdf_hash, security_id=sid, subject_scope='security', metric_or_claim=metric,
            value=number, raw_value=str(value), unit='元', raw_unit='元', period_kind='cumulative',
            period_end=row['period_end'], period_basis='COMPARATIVE' if row['role'] == 'comparative' else 'YTD',
            value_kind='FLOW', available_at=observed_at, fetched_at=observed_at,
            source_version=SOURCE_VERSION, document_version_id=pdf_hash, revision_id=pdf_hash,
            quality_status='OBSERVED', semantic_status='VERIFIED', knowledge_basis='UNKNOWN',
            metric_definition_version=FIELD_VERSION, semantic_note='本证券本PDF季度合并总行；无历史发布证明',
            lineage_ids=[material_id, pdf_hash], field_locator=row, period_start=row['period_start'],
            column_role=row['role'], qualification_version=VERSION))
    for period in sorted({r['period_end'] for r in rows}):
        group = {f['table']: f for f in fields if f.get('period_end') == period and 'table' in f}
        if set(group) != {'NP', 'CFO'}:
            continue
        np, cfo = (Decimal(group[k]['decimal_value']) for k in ('NP', 'CFO'))
        if np == 0:
            gaps.append(period + ':zero_denominator')
            continue
        with localcontext() as context:
            context.prec = 34
            context.rounding = ROUND_HALF_EVEN
            ratio = cfo / np
        ids = [group[k]['evidence_id'] for k in ('CFO', 'NP')]
        identity = hashlib.sha256(_encoded([FORMULA_VERSION, ids])).hexdigest()
        item = dict(metric='CFOToNP', period_end=period, period_start=group['NP']['period_start'],
            role=group['NP']['role'], unit='倍', display_value=str(ratio), evidence_id=identity,
            expression='CFO / NP', operands={'CFO': str(cfo), 'NP': str(np)}, operand_evidence_ids=ids,
            precision='34 significant digits; ROUND_HALF_EVEN', formula_version=FORMULA_VERSION)
        fields.append(item)
        try:
            number = _typed(ratio)
        except (ValueError, OverflowError):
            item['typed_status'] = 'UNREPRESENTABLE'
            gaps.append('CFOToNP:typed_representation_unavailable')
            continue
        item['typed_status'] = 'OBSERVED'
        records.append(EvidenceRecord(evidence_id=identity, source_kind='derived', source_uri=source_uri,
            source_document_hash=pdf_hash, security_id=sid, subject_scope='security', metric_or_claim='CFOToNP',
            value=number, raw_value=str(ratio), unit='倍', raw_unit='倍', value_kind='RATIO',
            period_kind='cumulative', period_end=period, period_basis='COMPARATIVE' if item['role'] == 'comparative' else 'YTD',
            underlying_period_basis='YTD', available_at=observed_at, fetched_at=observed_at,
            source_version=SOURCE_VERSION, document_version_id=pdf_hash, revision_id=pdf_hash,
            quality_status='OBSERVED', semantic_status='VERIFIED', knowledge_basis='UNKNOWN',
            metric_definition_version=FORMULA_VERSION, lineage_ids=ids, formula=item))
    snapshot = EvidenceSnapshot.build(sid, requested_as_of or observed_at, records, strict=False)
    if requested_as_of is not None and observed_at > requested_as_of:
        fields = []
        gaps.append('available_after_requested_as_of')
    return snapshot, fields, gaps


def _material(store, sid, material_id, now):
    """Verify immutable dependencies once; supplier failure affects only cross-checks."""
    cache, choices = {}, []
    for reference in store.list_cash_material_receipts():
        try:
            receipt = _json(store.load_cash_material_bytes('receipts', reference))
            if receipt.get('security_id') != sid or receipt.get('material_id') != material_id:
                continue
            _public(receipt, reference, as_of=now)
            choices.append((reference, receipt))
        except (ValueError, OSError, TypeError, KeyError):
            continue
    if not choices:
        raise ValueError('material_missing_or_invalid')
    ref, receipt = sorted(choices)[0]
    for digest in [receipt['pdf_sha256'], *(s['blob_sha256'] for s in receipt['suppliers'])]:
        if digest not in cache:
            try:
                cache[digest] = store.load_cash_material_bytes('blobs', digest)
            except (OSError, ValueError):
                cache[digest] = None
    if cache[receipt['pdf_sha256']] is None:
        raise ValueError('material_pdf_dependency_invalid')
    return ref, receipt, cache


def _supplier_checks(receipt, blobs, rows, sid):
    from src.core.cash_material_intake import _supplier
    context, sanitized = {'suppliers': {}}, dict(receipt, suppliers=[])
    for descriptor in receipt['suppliers']:
        digest = descriptor['blob_sha256']
        try:
            raw = blobs[digest]
            actual = _supplier(raw, sid)
            content = __import__('json').loads(raw.decode('utf-8'), parse_float=Decimal, parse_int=Decimal)['content']
            context['suppliers'][digest] = {**actual, **numeric._compact_supplier_number(content['value'])}
            sanitized['suppliers'].append(dict(descriptor, status='LOCAL_PAYLOAD_CHECKED'))
        except (ValueError, TypeError, KeyError, AttributeError):
            sanitized['suppliers'].append(dict(descriptor, status='REJECTED_PENDING_REVIEW'))
    # v40 arithmetic needs paired rows. A partial row remains qualified independently.
    paired = [r for r in rows if len([q for q in rows if q['period_end'] == r['period_end']]) == 2]
    return numeric._calculate(paired, sanitized, context)


def check_source(store, security_id, material_id, *, requested_as_of=None, fetcher=None, extractor=None,
                 industry_check=False, industry_fetcher=None):
    if requested_as_of is not None and (not isinstance(requested_as_of, datetime) or requested_as_of.tzinfo is None):
        raise ValueError('aware requested_as_of required')
    sid, now = _sid(security_id), datetime.now().astimezone()
    ref, receipt, blobs = _material(store, sid, material_id, now)
    result = dict(schema=VERSION, security_id=sid, material_id=material_id,
        result='PENDING_REVIEW', qualified_field_observations=[], gaps=[],
        replay_status='EXECUTED_THIS_CALL', industry_premise=None, comparable_periods=0,
        cash_sustainability='UNKNOWN', published_at=None, version_available_at=None, knowledge_basis='UNKNOWN',
        summary='当次官方来源或报表字段待核；未授予历史或长期资格')
    rows, year, pdf, uri = [], None, None, receipt['declarations'].get('source_uri')
    try:
        validate_uri(uri)
        pdf = _bounded_fetch(uri, fetcher=fetcher)
        if hashlib.sha256(pdf).hexdigest() != receipt['pdf_sha256']:
            raise ValueError('official_bytes_mismatch')
        pages = (extractor or numeric._extract_pdf)(pdf, receipt['pages'])
        rows, parse_gaps, year = numeric._parse(pages, sid, partial=True)
        result['gaps'].extend(parse_gaps)
    except Exception as error:
        allowed = str(error)
        result['gaps'].append(allowed if re.fullmatch(r'[a-z_0-9:]+', allowed) and len(allowed) < 90 else 'source_or_extraction_unavailable')
    observed = datetime.now().astimezone()
    checks, cross_gaps = _supplier_checks(receipt, blobs, rows, sid)
    result['supplier_cross_checks'] = checks
    result['supplier_gaps'] = cross_gaps
    snapshot, fields, gaps = _qualified_fields(sid, rows, source_uri=uri or '', pdf_hash=receipt['pdf_sha256'],
        material_id=material_id, observed_at=observed, requested_as_of=requested_as_of)
    result['gaps'].extend(gaps)
    result.update(observed_as_of=observed.isoformat(), requested_as_of=requested_as_of.isoformat() if requested_as_of else None,
        source_uri=uri, pdf_sha256=receipt['pdf_sha256'], declared_published_at=receipt['declarations'].get('published_at'),
        http_status=200 if pdf is not None else None, fetched_byte_count=len(pdf) if pdf is not None else None,
        fetched_sha256=hashlib.sha256(pdf).hexdigest() if pdf is not None else None,
        qualified_field_observations=fields, field_snapshot=snapshot.model_dump(mode='json'),
        source_version=SOURCE_VERSION, parser_version=numeric.PARTIAL_PARSER_VERSION,
        field_version=FIELD_VERSION, formula_version=FORMULA_VERSION,
        numeric_comparison_version=numeric.CALCULATION_VERSION, report_year=year)
    if fields:
        result['result'] = 'CURRENT_OFFICIAL_FIELDS_QUALIFIED'
        result['summary'] = '当次官方字节一致并重提取；仅本PDF合并字段核定；历史可得性、行业及持续性仍待核'
    result['dependencies'] = numeric._dependencies(ref, receipt, {'suppliers': {}})
    # Source failure does not erase the separate local arithmetic result. This
    # fallback parses local bytes only and never reaches the field constructor.
    offline_rows, offline_gaps = (rows, []) if len(rows) == 4 else ([], ['target_row'])
    if pdf is None or not rows:
        try:
            local_pages = (extractor or numeric._extract_pdf)(blobs[receipt['pdf_sha256']], receipt['pages'])
            offline_rows, offline_gaps, _ = numeric._parse(local_pages, sid)
        except Exception:
            offline_rows, offline_gaps = [], ['extraction']
    offline_checks, offline_cross_gaps = _supplier_checks(receipt, blobs, offline_rows, sid)
    result['offline_numeric_result'] = numeric._result(offline_gaps + offline_cross_gaps)
    result['offline_numeric_checks'] = offline_checks
    if industry_check:
        from src.core.cash_industry_qualification import _obtain, _unavailable
        from src.core.cash_observations import observe_current_official
        context = _current_context(sid, material_id, receipt['pdf_sha256'], uri or '', snapshot, fields)
        # Partial typed fields may be displayed; no PDF qualification means no SSE request.
        current_industry = (_obtain(sid, requested_as_of=requested_as_of, fetcher=industry_fetcher)
                            if context.snapshot.records else _unavailable(sid, 'official_fields_unavailable'))
        observation = observe_current_official(context, current_industry, requested_as_of=requested_as_of)
        result.update(schema=JOINT_VERSION, field_context_version=CURRENT_FIELD_PROTOCOL,
            qualified_field_observations=list(context.fields),
            field_snapshot=context.snapshot.model_dump(mode='json'),
            industry_premise=current_industry.public, current_cash_observations=observation,
            comparable_periods=observation['comparable_periods'])
        result['dependencies']['industry_blob_sha256'] = None
        if current_industry.raw is not None:
            digest = store.save_cash_material_bytes('blobs', current_industry.raw)
            result['dependencies']['industry_blob_sha256'] = digest
        if observation['cash_groups']:
            result['summary'] += '；本次SSE转述行业条件下形成1个当前条件化可比组、2期；比较栏不证明历史行业或首发'
    record = dict(result, rows=rows)
    if industry_check:
        # Store each typed record once in the joint snapshot. Presentation repeats
        # fields across groups; immutable storage binds those points by identity.
        record['field_snapshot'] = dict(snapshot_id=context.snapshot.snapshot_id,
            as_of=context.snapshot.as_of.isoformat(), evidence_ids=[r.evidence_id for r in context.snapshot.records],
            storage_representation='members_in_joint_snapshot')
        compact = copy.deepcopy(observation)
        for group in compact['cash_groups']:
            group['points'] = [dict(point_id=p['point_id'], period_start=p['period_start'], period_end=p['period_end'],
                column_role=p['column_role'], evidence_ids=[f['evidence']['evidence_id'] for f in p['fields']]) for p in group['points']]
        record['current_cash_observations'] = compact
    if industry_check and len(_encoded(record)) > 65536:
        raise ValueError('source_check_record_resource_limit')
    record['source_check_id'] = hashlib.sha256(_encoded(record)).hexdigest()
    reference = store.save_cash_material_bytes('source_checks', _encoded(record))
    result.update(source_check_id=record['source_check_id'], storage_reference=reference)
    return result


def read_source_checks(store, security_id, *, as_of, _verified_context=None):
    """Historical summaries only. No amount, ratio or VERIFIED reconstruction."""
    from src.core.cash_material_intake import read_materials
    context = _verified_context
    if context is None:
        context = {}
        read_materials(store, security_id, as_of=as_of, _verified_context=context)
    views, errors, blob_cache = [], [], {}
    try:
        refs = store.list_cash_source_checks()
    except OSError:
        refs = []
        errors.append('source_check_directory_unreadable')
    for ref in refs:
        try:
            record = _json(store.load_cash_material_bytes('source_checks', ref))
            if record.get('security_id') != security_id:
                continue
            identity = dict(record)
            check_id = identity.pop('source_check_id')
            if record.get('schema') not in (VERSION, JOINT_VERSION) or hashlib.sha256(_encoded(identity)).hexdigest() != check_id:
                raise ValueError('source_check_identity_invalid')
            dependency = record['dependencies']['receipt_sha256']
            receipt = context.get('receipts', {}).get(dependency)
            if receipt is None or receipt['material_id'] != record['material_id'] or receipt['pdf_sha256'] != record['pdf_sha256']:
                raise ValueError('source_check_dependencies_invalid')
            expected = numeric._dependencies(dependency, receipt, {'suppliers': {}})
            if record['schema'] == JOINT_VERSION:
                industry_hash = record['dependencies'].get('industry_blob_sha256')
                expected['industry_blob_sha256'] = industry_hash
                if industry_hash is not None:
                    if industry_hash not in blob_cache:
                        blob_cache[industry_hash] = store.load_cash_material_bytes('blobs', industry_hash)
            if expected != record['dependencies']:
                raise ValueError('source_check_dependency_binding_invalid')
            view = dict(source_check_id=check_id, storage_reference=ref,
                recorded_result=record['result'], observed_as_of=record['observed_as_of'],
                dependency_status='HASH_CHECKED', replay_status='NOT_REPLAYED_READONLY',
                summary='留有官方字段核定历史记录；本次只读未重放，当前字段资格待显式取证')
            if record['schema'] == JOINT_VERSION:
                count = record.get('current_cash_observations', {}).get('comparable_periods', 0)
                if type(count) is not int or count not in (0, 2):
                    raise ValueError('historical_observation_count_invalid')
                view['historical_observation_periods'] = count
                view['summary'] = f'历史记录曾形成{count}期当前条件化观察；本次只读未重放'
            views.append(view)
        except (ValueError, KeyError, TypeError, OSError):
            errors.append('source_check_or_dependency_invalid')
    return dict(source_checks=views, read_errors=sorted(set(errors)), comparable_periods=0, cash_sustainability='UNKNOWN')
