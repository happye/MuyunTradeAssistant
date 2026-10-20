"""Bounded v41 current official source qualification contract."""
from datetime import datetime, timezone
import io
import json
import os
from decimal import Decimal

import pytest
from rich.console import Console

from src.core import cash_source_qualification as source
from src.core import cash_numeric_verification as numeric
from src.core import cash_material_intake as intake
from src.core.research_application import ResearchApplicationService
from src.data.research_store import ResearchStore
from src.data.research_snapshot import EvidenceSnapshot
import src.cli.main as cli

URI = 'https://static.cninfo.com.cn/finalpage/2024-04-27/1219875828.PDF'


def pages(code='000001', np='80', cfo='20'):
    return [dict(page=0, text=f'证券代码：{code}\n2024 年第一季度报告'),
        dict(page=1, text='合并利润表\n2024 年1至3 月\n单位：元 币种：人民币\n项目 2024 年第一季度 2023 年第一季度'),
        dict(page=2, text=f'五、净利润 {np} {np}'),
        dict(page=3, text='合并现金流量表\n2024 年1至3 月\n单位：元 币种：人民币\n项目 2024 年第一季度 2023 年第一季度'),
        dict(page=4, text=f'经营活动产生的现金流量净额 {cfo} {cfo}')]


def material(tmp_path, *, code='000001', np='80', cfo='20', uri=URI):
    original = tmp_path / 'original.pdf'
    original.write_bytes(b'%PDF-1.7\ntest seam only')
    manifest = tmp_path / 'manifest.json'
    declaration = dict(path=original.name, published_at='2024-04-27')
    if uri is not None:
        declaration['source_uri'] = uri
    manifest.write_bytes(intake._encoded(dict(schema=intake.VERSION, original=declaration, pages=[0, 1, 2, 3, 4])))
    store = ResearchStore(tmp_path / 'store')
    app = ResearchApplicationService(store=store, plans_store=None, assessment_store=None)
    text = pages(code, np, cfo)
    receipt = app.intake_cash_materials(code, manifest, as_of=datetime.now(timezone.utc),
        extractor=lambda *a: '\n'.join(p['text'] for p in text))
    return app, receipt['material_id'], text, original.read_bytes()


def execute(app, identity, text, pdf, **kwargs):
    return app.check_cash_source('000001', identity, fetcher=lambda *a, **k: pdf,
                                 extractor=lambda *a: text, **kwargs)


def test_strict_official_uri():
    assert source.validate_uri('https://static.cninfo.com.cn/finalpage/2024-04-27/1219875828.PDF')


def test_qualified_fields_keep_exact_nonzero_ratio():
    now = datetime.now(timezone.utc)
    rows = [dict(table=k, token=v, period_start='2024-01-01', period_end='2024-03-31',
                 role='current', column=0, page=1, line=1, end_line=1, header_page=1, header_line=0)
            for k, v in [('NP', '3000000000000'), ('CFO', '1')]]
    snapshot, fields, gaps = source._qualified_fields('000001', rows, source_uri='https://static.cninfo.com.cn/finalpage/2024-04-27/1219875828.PDF',
        pdf_hash='a'*64, material_id='b'*64, observed_at=now, requested_as_of=None)
    ratio = next(f for f in fields if f['metric'] == 'CFOToNP')
    assert ratio['display_value'] != '0.000000'
    assert next(r for r in snapshot.records if r.metric_or_claim == 'CFOToNP').value > 0
    assert len(snapshot.records) == 3


@pytest.mark.parametrize('uri', [None, '', URI + '?a=1', URI + '#fragment',
    URI.replace('https:', 'http:'), URI.replace('static.cninfo.com.cn', 'static.cninfo.com.cn.evil'),
    URI.replace('static.cninfo.com.cn', 'u@static.cninfo.com.cn'), URI.replace('.cn/', '.cn:444/'),
    URI.replace('2024-04-27', '2024-02-31'), URI.replace('.PDF', '.pdf')])
def test_bad_uri_early_no_fetch(tmp_path, uri):
    app, identity, text, pdf = material(tmp_path, uri=uri)
    def forbidden(*a, **k):
        raise AssertionError('invalid URI reached HTTP')
    result = app.check_cash_source('000001', identity, fetcher=forbidden)
    assert not result['qualified_field_observations'] and not result['field_snapshot']['records']


def test_other_security_and_supplier_missing_do_not_block(tmp_path):
    app, identity, text, pdf = material(tmp_path, code='600000', np='19', cfo='7')
    result = app.check_cash_source('600000', identity, fetcher=lambda *a, **k: pdf, extractor=lambda *a: text)
    assert len(result['field_snapshot']['records']) == 6
    assert result['supplier_gaps'] == ['supplier_missing']
    assert {r['security_id'] for r in result['field_snapshot']['records']} == {'600000'}
    assert {f['display_value'] for f in result['qualified_field_observations'] if f['metric'] == 'netProfit'} == {'19'}


@pytest.mark.parametrize('change,expected', [('missing_np', 2), ('bad_np_unit', 2), ('missing_cfo', 2),
    ('wrong_security', 0), ('duplicate_np', 2), ('wrong_np_columns', 2), ('parent_np', 2)])
def test_partial_rows_and_table_semantics(tmp_path, change, expected):
    app, identity, text, pdf = material(tmp_path)
    if change == 'missing_np': text[2]['text'] = '归属于母公司股东的净利润 80 80'
    if change == 'bad_np_unit': text[1]['text'] = text[1]['text'].replace('单位：元', '单位：万元')
    if change == 'missing_cfo': text[4]['text'] = '投资活动产生的现金流量净额 20 20'
    if change == 'wrong_security': text[0]['text'] = text[0]['text'].replace('000001', '600519')
    if change == 'duplicate_np': text[2]['text'] += '\n五、净利润 80 80'
    if change == 'wrong_np_columns': text[1]['text'] = text[1]['text'].replace('2024 年第一季度 2023', '2023 年第一季度 2024')
    if change == 'parent_np': text[1]['text'] = text[1]['text'].replace('合并利润表', '母公司利润表')
    result = execute(app, identity, text, pdf)
    assert len(result['field_snapshot']['records']) == expected


@pytest.mark.parametrize('np,cfo,ratio', [('0', '20', None), ('80', '0', '0'), ('3', '1', '0.3333333333333333333333333333333333'),
    ('3000000000000', '1', '3.333333333333333333333333333333333E-13')])
def test_decimal_formula_and_zero(tmp_path, np, cfo, ratio):
    app, identity, text, pdf = material(tmp_path, np=np, cfo=cfo)
    result = execute(app, identity, text, pdf)
    derived = [r for r in result['qualified_field_observations'] if r['metric'] == 'CFOToNP']
    assert [r['display_value'] for r in derived] == ([] if ratio is None else [ratio, ratio])
    if derived:
        assert derived[0]['operands'] == {'CFO': cfo, 'NP': np}


@pytest.mark.parametrize('token', ['1' + '0'*190, '0.' + '0'*190 + '1'])
def test_typed_extreme_ratio_does_not_become_fake_zero(tmp_path, token):
    # Each source token is bounded; the quotient can exceed float's range.
    reciprocal = '0.' + '0'*190 + '1' if token[0] == '1' else '1' + '0'*190
    app, identity, text, pdf = material(tmp_path, np=token, cfo=reciprocal)
    result = execute(app, identity, text, pdf)
    derived = [r for r in result['qualified_field_observations'] if r['metric'] == 'CFOToNP']
    assert all(Decimal(r['display_value']) != 0 and r['typed_status'] == 'UNREPRESENTABLE' for r in derived)
    assert not [r for r in result['field_snapshot']['records'] if r['metric_or_claim'] == 'CFOToNP']


def test_explicit_old_cutoff_and_strict_history(tmp_path):
    app, identity, text, pdf = material(tmp_path)
    result = execute(app, identity, text, pdf)
    snapshot = EvidenceSnapshot.model_validate(result['field_snapshot'])
    assert all(r.published_at is None and r.version_available_at is None and r.knowledge_basis == 'UNKNOWN' for r in snapshot.records)
    assert not EvidenceSnapshot.build('000001', snapshot.as_of, snapshot.records, strict=True).records
    old = execute(app, identity, text, pdf, requested_as_of=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert not old['qualified_field_observations'] and not old['field_snapshot']['records']
    assert old['field_snapshot']['as_of'].startswith('2025-01-01')
    assert {f['role'] for f in result['qualified_field_observations']} == {'current', 'comparative'}


def test_history_readonly_single_blob_read_and_no_current_values(tmp_path, monkeypatch):
    app, identity, text, pdf = material(tmp_path)
    result = execute(app, identity, text, pdf)
    before = {p: p.read_bytes() for p in app.store.dir.rglob('*') if p.is_file()}
    def forbidden(*a, **k): raise AssertionError('read triggered action')
    monkeypatch.setattr(source, '_bounded_fetch', forbidden)
    monkeypatch.setattr(numeric, '_extract_pdf', forbidden)
    monkeypatch.setattr(ResearchStore, 'save_cash_material_bytes', forbidden)
    original, reads = ResearchStore.load_cash_material_bytes, []
    def counted(self, kind, digest):
        reads.append((kind, digest))
        return original(self, kind, digest)
    monkeypatch.setattr(ResearchStore, 'load_cash_material_bytes', counted)
    history = app.get_cash_observations('000001', as_of=datetime.now(timezone.utc))
    assert len(reads) == len(set(reads))
    item = history['cash_source_checks']['source_checks'][0]
    assert item['replay_status'] == 'NOT_REPLAYED_READONLY'
    assert 'display_value' not in json.dumps(history) and 'VERIFIED' not in json.dumps(history)
    assert before == {p: p.read_bytes() for p in app.store.dir.rglob('*') if p.is_file()}


def test_self_consistent_history_cannot_bypass_execution(tmp_path):
    app, identity, text, pdf = material(tmp_path)
    previous = execute(app, identity, text, pdf)
    record = intake._json(app.store.load_cash_material_bytes('source_checks', previous['storage_reference']))
    record['qualified_field_observations'][0]['display_value'] = '999999'
    record.pop('source_check_id')
    record['source_check_id'] = intake._sha(intake._encoded(record))
    app.store.save_cash_material_bytes('source_checks', intake._encoded(record))
    calls = []
    def failed(*a, **k):
        calls.append(True)
        raise ValueError('http_status_429')
    result = app.check_cash_source('000001', identity, fetcher=failed, extractor=lambda *a: (_ for _ in ()).throw(AssertionError('must not extract')))
    assert calls and not result['qualified_field_observations']


def test_storage_failure_never_promises_success(tmp_path, monkeypatch):
    app, identity, text, pdf = material(tmp_path)
    def failed(*a, **k): raise OSError('storage fault')
    monkeypatch.setattr(ResearchStore, 'save_cash_material_bytes', failed)
    with pytest.raises(OSError): execute(app, identity, text, pdf)


@pytest.mark.parametrize('mode', ['bytes', 'header', 'extraction'])
def test_hash_or_worker_failure_no_fields(tmp_path, mode):
    app, identity, text, pdf = material(tmp_path)
    if mode == 'bytes': pdf += b'changed'
    if mode == 'header': pdf = b'not PDF'
    if mode == 'extraction': text = []
    result = execute(app, identity, text, pdf)
    assert not result['qualified_field_observations']


def test_timeout_returns_without_late_storage(tmp_path, monkeypatch):
    import threading
    app, identity, text, pdf = material(tmp_path)
    release = threading.Event()
    monkeypatch.setattr(source, 'TIMEOUT', 0.02)
    def delayed(*a, **k):
        release.wait(2)
        return pdf
    try:
        result = app.check_cash_source('000001', identity, fetcher=delayed, extractor=lambda *a: text)
        before = {p: p.read_bytes() for p in app.store.dir.rglob('*') if p.is_file()}
        assert result['gaps'] == ['source_timeout'] and not result['qualified_field_observations']
    finally: release.set()
    assert before == {p: p.read_bytes() for p in app.store.dir.rglob('*') if p.is_file()}


@pytest.mark.parametrize('rest', [['000001', '--source-check'], ['000001', '--cash-materials', 'x', '--source-check'],
    ['000001', '--cash-verify', 'a'*64, '--source-check', '--source-check'],
    ['000001', '--cash-verify', 'a'*64, '--source-check', '--capture']])
def test_cli_invalid_combinations_early(tmp_path, monkeypatch, rest):
    def forbidden(*a, **k): raise AssertionError('invalid flags reached app')
    monkeypatch.setattr(ResearchApplicationService, 'run', forbidden)
    monkeypatch.setattr(ResearchApplicationService, 'check_cash_source', forbidden)
    output = io.StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=output))
    cli.research_command(rest + ['--json'])


def test_cli_json_and_render_use_live_separate_method(tmp_path, monkeypatch, capsys):
    app, identity, text, pdf = material(tmp_path)
    result = execute(app, identity, text, pdf)
    calls = []
    def checked(self, code, material_id, **kwargs):
        calls.append((code, material_id, kwargs))
        return result
    monkeypatch.setattr(ResearchApplicationService, 'check_cash_source', checked)
    cli.research_command(['000001', '--cash-verify', identity, '--source-check', '--json'])
    assert json.loads(capsys.readouterr().out)['field_snapshot']['records']
    output = io.StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=output, width=240))
    cli.research_command(['000001', '--cash-verify', identity, '--source-check'])
    assert 'operatingCashFlow' in output.getvalue() and 'SHA256' in output.getvalue()
    assert '独立离线数值比较：PENDING_REVIEW' in output.getvalue()
    assert calls == [('000001', identity, {}), ('000001', identity, {})]
    result.update(result='PENDING_REVIEW', qualified_field_observations=[], offline_numeric_result='LOCAL_NUMERIC_CONSISTENT')
    output.seek(0)
    output.truncate()
    cli.research_command(['000001', '--cash-verify', identity, '--source-check'])
    assert '独立离线数值比较：LOCAL_NUMERIC_CONSISTENT' in output.getvalue()
    assert 'operatingCashFlow' not in output.getvalue()


@pytest.mark.parametrize('status,mime,length,payload', [(302, 'application/pdf', None, b'%PDF-'),
    (429, 'application/pdf', None, b'%PDF-'), (200, 'text/html', None, b'%PDF-'),
    (200, 'application/pdf', '20971521', b'%PDF-'), (200, 'application/pdf', None, b'html'),
    (200, 'application/pdf', 'invalid', b'%PDF-')])
def test_http_envelope_fail_closed(monkeypatch, status, mime, length, payload):
    import requests
    class Response:
        status_code = status
        headers = {'Content-Type': mime, **({'Content-Length': length} if length else {})}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield payload
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, uri, **kwargs):
            assert uri == URI and kwargs['allow_redirects'] is False and kwargs['verify'] is True
            return Response()
    monkeypatch.setattr(requests, 'Session', Session)
    with pytest.raises(ValueError): source._http_pdf(URI, timeout=30)


def test_repl_real_routing_to_isolated_product_path(tmp_path, monkeypatch, capsys):
    import start
    import src.data.research_store as stores
    app, identity, text, pdf = material(tmp_path)
    monkeypatch.setattr(stores, 'RESEARCH_DIR', app.store.dir)
    monkeypatch.setattr(source, '_http_pdf', lambda *a, **k: pdf)
    monkeypatch.setattr(numeric, '_extract_pdf', lambda *a: text)
    def forbidden(*a, **k): raise AssertionError('research run invoked')
    monkeypatch.setattr(ResearchApplicationService, 'run', forbidden)
    mode, args = start.parse_input(f'research 000001 --cash-verify {identity} --source-check --json')
    start.run_cli(mode, args)
    result = json.loads(capsys.readouterr().out)
    assert len(result['field_snapshot']['records']) == 6


@pytest.mark.parametrize('case', ['missing', 'mismatch', 'wrong_security', 'conflict'])
def test_supplier_failures_preserve_official_fields(tmp_path, case):
    app, identity, text, pdf = material(tmp_path)
    refs = app.store.list_cash_material_receipts()
    record = intake._json(app.store.load_cash_material_bytes('receipts', refs[0]))
    content = dict(metric='netProfit', value='999' if case == 'mismatch' else '80', unit='元', period_end='2024-03-31',
        source_version='baostock_financial_v1', source_uri='baostock.query_profit_data(code=sz.000001,year=2024,quarter=1)', published_at='2024-04-27')
    code = '600519' if case == 'wrong_security' else '000001'
    payload = dict(schema_version=1, sha256=intake._sha(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()), content=content,
        metadata=dict(stock_code=code, bs_code='sz.000001', interface=content['source_uri'], metric='netProfit', year=2024,
                      quarter=1, period_end='2024-03-31', source_version='baostock_financial_v1'))
    digests = []
    for value in ([content['value'], '81'] if case == 'conflict' else [content['value']]):
        payload['content']['value'] = value
        payload['sha256'] = intake._sha(json.dumps(payload['content'], ensure_ascii=False, sort_keys=True).encode())
        raw = intake._encoded(payload)
        digest = intake._sha(raw)
        if case != 'missing': app.store.save_cash_material_bytes('blobs', raw)
        descriptor = dict(blob_sha256=digest, status='REJECTED_PENDING_REVIEW', reason='supplier_invalid_format')
        record['suppliers'].append(descriptor)
        digests.append(digest)
    record['material_id'] = intake._material_id(record)
    app.store.save_cash_material_bytes('receipts', intake._encoded(record))
    result = execute(app, record['material_id'], text, pdf)
    assert len(result['field_snapshot']['records']) == 6 and result['supplier_gaps']


def test_failed_source_keeps_fresh_separate_offline_comparison(tmp_path):
    app, identity, text, pdf = material(tmp_path)
    record = intake._json(app.store.load_cash_material_bytes('receipts', app.store.list_cash_material_receipts()[0]))
    for year in (2023, 2024):
        for metric, value in [('netProfit', '80'), ('CFOToNP', '0.25')]:
            interface = f"baostock.query_{'profit' if metric == 'netProfit' else 'cash_flow'}_data(code=sz.000001,year={year},quarter=1)"
            content = dict(metric=metric, value=value, unit='元' if metric == 'netProfit' else '倍',
                period_end=f'{year}-03-31', source_version='baostock_financial_v1', source_uri=interface, published_at='2024-04-27')
            payload = dict(schema_version=1, sha256=intake._sha(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()), content=content,
                metadata=dict(stock_code='000001', bs_code='sz.000001', interface=interface, metric=metric, year=year,
                              quarter=1, period_end=f'{year}-03-31', source_version='baostock_financial_v1'))
            raw = intake._encoded(payload)
            digest = app.store.save_cash_material_bytes('blobs', raw)
            record['suppliers'].append(dict(blob_sha256=digest, status='LOCAL_PAYLOAD_CHECKED', **intake._supplier(raw, '000001')))
    record['material_id'] = intake._material_id(record)
    app.store.save_cash_material_bytes('receipts', intake._encoded(record))
    result = app.check_cash_source('000001', record['material_id'],
        fetcher=lambda *a, **k: (_ for _ in ()).throw(ValueError('http_status_429')), extractor=lambda *a: text)
    assert result['offline_numeric_result'] == 'LOCAL_NUMERIC_CONSISTENT'
    assert not result['qualified_field_observations'] and not result['field_snapshot']['records']


@pytest.mark.skipif(os.environ.get('MUYUN_V41_EXIT_PROBE') != '1', reason='requires isolated explicit subprocess exit guard')
def test_never_finishing_fetch_does_not_block_cli_process_exit():
    """Run only under the Temp runner which authorizes this exact child command."""
    import subprocess
    import sys
    command = ('from src.core import cash_source_qualification as source; '
        'import threading; source.TIMEOUT=0.02; '
        'event=threading.Event(); '
        '\ntry: source._bounded_fetch("https://static.cninfo.com.cn/finalpage/2024-04-27/1219875828.PDF", '
        'fetcher=lambda *a, **k: event.wait())'
        '\nexcept ValueError as error: assert str(error)=="source_timeout"'
        '\nelse: raise AssertionError("timeout missing")'
        '\nprint("EXIT_AFTER_SOURCE_TIMEOUT")')
    result = subprocess.run([sys.executable, '-B', '-c', command], capture_output=True,
        text=True, encoding='utf-8', timeout=4, check=True)
    assert result.stdout.strip() == 'EXIT_AFTER_SOURCE_TIMEOUT'


def test_http_slow_stream_obeys_total_monotonic_deadline(monkeypatch):
    import requests
    ticks = iter([0, 0, 0, 0, 31])
    monkeypatch.setattr(source.time, 'monotonic', lambda: next(ticks))
    class Response:
        status_code = 200
        headers = {'Content-Type': 'application/pdf'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield b'%PDF-'
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, uri, **kwargs): return Response()
    monkeypatch.setattr(requests, 'Session', Session)
    with pytest.raises(ValueError, match='source_timeout'):
        source._http_pdf(URI, timeout=30)
