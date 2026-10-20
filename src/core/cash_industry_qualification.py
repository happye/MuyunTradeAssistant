"""One current SSE-reported classification, never historical CAPCO verification."""
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from src.core.cash_material_intake import _encoded, _json
from src.data.research_snapshot import EvidenceRecord

VERSION = 'sse_reported_industry.v1'
API = 'https://query.sse.com.cn/commonQuery.do'
SQL_ID = 'COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GSGK_C'
LIMIT = 256 * 1024
TIMEOUT = 30
_INDUSTRY_SEAL = object()


@dataclass(frozen=True)
class _CurrentIndustry:
    security_id: str
    public: dict
    record: EvidenceRecord | None
    raw: bytes | None
    _seal: object


def _params(sid):
    if not isinstance(sid, str) or not re.fullmatch('[0-9]{6}', sid):
        raise ValueError('industry_security_invalid')
    return dict(isPagination='false', sqlId=SQL_ID, COMPANY_CODE=sid)


def _uri(sid):
    return API + '?' + urlencode(_params(sid))


def _http_json(uri, *, timeout):
    import requests
    match = re.fullmatch(re.escape(API) + r'\?isPagination=false&sqlId=' + SQL_ID + r'&COMPANY_CODE=([0-9]{6})', uri)
    if not match:
        raise ValueError('industry_uri_not_allowed')
    deadline = time.monotonic() + timeout
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise ValueError('industry_timeout')
        return left
    with requests.Session() as session:
        budget = remaining()
        with session.get(API, params=_params(match[1]), headers={'Referer': 'https://www.sse.com.cn/', 'User-Agent': 'Mozilla/5.0'},
                timeout=(min(5, budget), min(5, budget)), stream=True, verify=True, allow_redirects=False) as response:
            remaining()
            if response.status_code != 200:
                raise ValueError('industry_http_status_' + str(response.status_code))
            if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                raise ValueError('industry_json_mime_invalid')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > LIMIT):
                raise ValueError('industry_size_limit')
            chunks, size = [], 0
            iterator = iter(response.iter_content(16384))
            while True:
                remaining()
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                remaining()
                size += len(chunk)
                if size > LIMIT:
                    raise ValueError('industry_size_limit')
                chunks.append(chunk)
            remaining()
            return b''.join(chunks)


def _parse(raw, sid):
    if not isinstance(raw, bytes) or len(raw) > LIMIT:
        raise ValueError('industry_size_or_payload_invalid')
    obj = _json(raw)
    if (not isinstance(obj, dict) or obj.get('actionErrors') != [] or obj.get('actionMessages') != []
            or obj.get('fieldErrors') != {} or not isinstance(obj.get('result'), list) or len(obj['result']) != 1):
        raise ValueError('industry_envelope_or_unique_row_invalid')
    row = obj['result'][0]
    if not isinstance(row, dict) or row.get('COMPANY_CODE') != sid or row.get('A_STOCK_CODE') != sid:
        raise ValueError('industry_returned_security_mismatch')
    selected = {k: row.get(k) for k in ('CSRC_CODE', 'CSRC_CODE_DESC', 'CSRC_GREAT_CODE', 'CSRC_GREAT_CODE_DESC')}
    if any(not isinstance(v, str) or len(v) > 100 for v in selected.values()):
        raise ValueError('industry_classification_fields_invalid')
    key = tuple(selected.values())
    financial = {'66': '货币金融服务', '67': '资本市场服务', '68': '保险业', '69': '其他金融业'}
    if key == ('C', '制造业', '15', '酒、饮料和精制茶制造业'):
        status = 'CURRENT_SSE_REPORTED_NON_FINANCIAL'
    elif key[0] == 'J' and key[1] == '金融业' and financial.get(key[2]) == key[3]:
        status = 'NOT_APPLICABLE'
    else:
        status = 'UNKNOWN'
    return dict(status=status, security_id=sid, **selected, classification_authority='CAPCO', reported_by='SSE',
        verification_scope='CURRENT_SSE_REPORTED_ONLY_NOT_CAPCO_ORIGINAL_VERIFICATION',
        published_at=None, version_available_at=None, historical_validity='UNKNOWN',
        knowledge_basis='UNKNOWN', parser_version=VERSION)


def _unavailable(sid, reason):
    return _CurrentIndustry(sid, dict(status='UNKNOWN', security_id=sid, gaps=[reason],
        source_uri=_uri(sid), parser_version=VERSION, published_at=None, version_available_at=None,
        historical_validity='UNKNOWN', knowledge_basis='UNKNOWN'), None, None, _INDUSTRY_SEAL)


def _obtain(sid, *, requested_as_of=None, fetcher=None):
    from src.core.cash_source_qualification import _bounded_download
    raw = None
    try:
        raw = _bounded_download(_uri(sid), fetcher=fetcher or _http_json, timeout=TIMEOUT)
        if not isinstance(raw, bytes) or len(raw) > LIMIT:
            raw = None
            raise ValueError('industry_size_or_payload_invalid')
        public = _parse(raw, sid)
        observed = datetime.now().astimezone()
        digest = hashlib.sha256(raw).hexdigest()
        public.update(source_uri=_uri(sid), response_sha256=digest, observed_as_of=observed.isoformat(),
                      available_at=observed.isoformat(), http_status=200, fetched_byte_count=len(raw), gaps=[])
        if requested_as_of is not None and observed > requested_as_of:
            public['gaps'].append('industry_available_after_requested_as_of')
            public['status'] = 'UNKNOWN'
        identity = hashlib.sha256(_encoded([VERSION, sid, digest, public['status'], observed.isoformat()])).hexdigest()
        record = EvidenceRecord(evidence_id=identity, source_kind='announcement', source_uri=_uri(sid),
            source_document_hash=digest, security_id=sid, subject_scope='security',
            metric_or_claim='industry_classification', value=public['status'],
            available_at=observed, fetched_at=observed, document_version_id=digest,
            source_version=VERSION, quality_status='OBSERVED', semantic_status='VERIFIED' if public['status'] != 'UNKNOWN' else 'UNKNOWN',
            knowledge_basis='UNKNOWN', metric_definition_version=VERSION,
            semantic_note='SSE当次转述CAPCO分类；不证明历史分类或CAPCO原始核验')
        public['evidence_id'] = identity
        return _CurrentIndustry(sid, public, record, raw, _INDUSTRY_SEAL)
    except Exception as error:
        message = str(error)
        if message == 'source_timeout':
            message = 'industry_timeout'
        reason = message if re.fullmatch('[a-z_0-9]+', message) and len(message) < 90 else 'industry_response_or_fetch_invalid'
        result = _unavailable(sid, reason)
        if raw is not None:
            result.public['response_sha256'] = hashlib.sha256(raw).hexdigest()
        return _CurrentIndustry(sid, result.public, None, raw, _INDUSTRY_SEAL)
