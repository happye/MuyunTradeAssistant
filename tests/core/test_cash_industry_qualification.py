"""v42 finite current-only official industry and cash consumer contract."""
import json
import copy
import io
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from rich.console import Console
from src.core import cash_industry_qualification as industry
from src.core import cash_source_qualification as source
from src.core import cash_material_intake as intake
from src.core.cash_observations import observe_current_official
from src.core.research_application import ResearchApplicationService
from src.data.research_store import ResearchStore
from src.data.research_snapshot import EvidenceSnapshot
import src.cli.main as cli

PDF_URI = 'https://static.cninfo.com.cn/finalpage/2024-04-27/1219875828.PDF'


def sse_bytes(code='000001', **changes):
    row = dict(COMPANY_CODE=code, A_STOCK_CODE=code, CSRC_CODE='C', CSRC_CODE_DESC='制造业',
        CSRC_GREAT_CODE='15', CSRC_GREAT_CODE_DESC='酒、饮料和精制茶制造业', INVESTOR_PHONE='PRIVATE-NOT-PUBLIC')
    row.update(changes)
    return intake._encoded(dict(actionErrors=[], actionMessages=[], fieldErrors={}, result=[row]))


def setup(tmp_path, code='000001', np='80', cfo='20'):
    pdf = b'%PDF-1.7\nindustry test seam only'
    (tmp_path/'original.pdf').write_bytes(pdf)
    manifest = tmp_path/'manifest.json'
    manifest.write_bytes(intake._encoded(dict(schema=intake.VERSION, original=dict(path='original.pdf',
        source_uri=PDF_URI, published_at='2024-04-27'), pages=[0, 1, 2, 3, 4])))
    pages = [dict(page=0, text=f'证券代码：{code}\n2024 年第一季度报告'),
        dict(page=1, text='合并利润表\n2024 年1至3 月\n单位：元 币种：人民币\n项目 2024 年第一季度 2023 年第一季度'),
        dict(page=2, text=f'五、净利润 {np} {np}'),
        dict(page=3, text='合并现金流量表\n2024 年1至3 月\n单位：元 币种：人民币\n项目 2024 年第一季度 2023 年第一季度'),
        dict(page=4, text=f'经营活动产生的现金流量净额 {cfo} {cfo}')]
    app = ResearchApplicationService(store=ResearchStore(tmp_path/'store'), plans_store=None, assessment_store=None)
    receipt = app.intake_cash_materials(code, manifest, as_of=datetime.now(timezone.utc),
        extractor=lambda *a: '\n'.join(p['text'] for p in pages))
    return app, receipt['material_id'], pages, pdf, code


def execute(data, raw=None, **kwargs):
    app, material_id, pages, pdf, code = data
    return app.check_cash_source(code, material_id, fetcher=lambda *a, **k: pdf, extractor=lambda *a: pages,
        industry_check=True, industry_fetcher=lambda *a, **k: sse_bytes(code) if raw is None else raw, **kwargs)


def captured(data, monkeypatch):
    contexts = []
    build = source._current_context
    def capture(*a, **k):
        value = build(*a, **k)
        contexts.append(value)
        return value
    monkeypatch.setattr(source, '_current_context', capture)
    result = execute(data)
    current = industry._obtain(data[-1], fetcher=lambda *a, **k: sse_bytes(data[-1]))
    return result, contexts[-1], current


def test_current_sse_nonfinancial_is_narrow_and_not_historical():
    raw = json.dumps(dict(actionErrors=[], actionMessages=[], fieldErrors={}, result=[dict(
        COMPANY_CODE='600519', A_STOCK_CODE='600519', CSRC_CODE='C', CSRC_CODE_DESC='制造业',
        CSRC_GREAT_CODE='15', CSRC_GREAT_CODE_DESC='酒、饮料和精制茶制造业')]), ensure_ascii=False).encode()
    result = industry._parse(raw, '600519')
    assert result['status'] == 'CURRENT_SSE_REPORTED_NON_FINANCIAL'
    assert result['published_at'] is None and result['historical_validity'] == 'UNKNOWN'


def test_product_two_period_consumer_and_successor_roles(tmp_path):
    data = setup(tmp_path)
    result = execute(data)
    view = result['current_cash_observations']
    assert view['group_count'] == 1 and view['comparable_periods'] == 2
    assert result['schema'] == source.JOINT_VERSION
    assert len(view['cash_groups'][0]['points']) == 2
    comparative = view['cash_groups'][0]['points'][0]
    assert comparative['column_role'] == 'comparative'
    assert {f['evidence']['period_basis'] for f in comparative['fields']} == {'COMPARATIVE'}
    assert {f['evidence']['source_kind'] for f in comparative['fields']} == {'financial', 'derived'}
    assert all(f['evidence']['qualification_version'] == source.CURRENT_FIELD_PROTOCOL for f in comparative['fields'])
    assert view['cash_sustainability'] == 'UNKNOWN' and view['roe_status'] == 'NOT_ASSESSED'
    assert 'values_by_period' not in json.dumps(result) and 'PRIVATE-NOT-PUBLIC' not in json.dumps(result)
    assert not EvidenceSnapshot.build(data[-1], datetime.now(timezone.utc),
        EvidenceSnapshot.model_validate(view['joint_snapshot']).records, strict=True).records
    stored = data[0].store.load_cash_material_bytes('source_checks', result['storage_reference'])
    assert len(stored) < 65536


def test_another_security_different_values_seam(tmp_path):
    result = execute(setup(tmp_path, code='600000', np='19', cfo='7'))
    assert result['current_cash_observations']['group_count'] == 1
    assert {r['security_id'] for r in result['field_snapshot']['records']} == {'600000'}
    assert {f['display_value'] for f in result['qualified_field_observations'] if f['metric'] == 'netProfit'} == {'19'}


@pytest.mark.parametrize('changes,status', [
    (dict(CSRC_CODE='J', CSRC_CODE_DESC='金融业', CSRC_GREAT_CODE='66', CSRC_GREAT_CODE_DESC='货币金融服务'), 'NOT_APPLICABLE'),
    (dict(CSRC_CODE='J', CSRC_CODE_DESC='金融业', CSRC_GREAT_CODE='67', CSRC_GREAT_CODE_DESC='资本市场服务'), 'NOT_APPLICABLE'),
    (dict(CSRC_CODE='J', CSRC_CODE_DESC='金融业', CSRC_GREAT_CODE='68', CSRC_GREAT_CODE_DESC='保险业'), 'NOT_APPLICABLE'),
    (dict(CSRC_CODE='J', CSRC_CODE_DESC='金融业', CSRC_GREAT_CODE='69', CSRC_GREAT_CODE_DESC='其他金融业'), 'NOT_APPLICABLE'),
    (dict(CSRC_CODE='A'), 'UNKNOWN'), (dict(CSRC_GREAT_CODE='99'), 'UNKNOWN'),
    (dict(CSRC_GREAT_CODE_DESC='制造业'), 'UNKNOWN')])
def test_narrow_nonfinancial_financial_and_unknown(tmp_path, changes, status):
    result = execute(setup(tmp_path), raw=sse_bytes(**changes))
    assert result['industry_premise']['status'] == status
    assert result['current_cash_observations']['group_count'] == 0
    assert len(result['qualified_field_observations']) == 6


@pytest.mark.parametrize('case', ['wrong_company', 'wrong_a_code', 'rows', 'errors', 'missing_envelope', 'duplicate', 'utf8', 'size'])
def test_strict_json_envelope_and_actual_subject(tmp_path, case):
    raw = sse_bytes()
    obj = json.loads(raw)
    if case == 'wrong_company': obj['result'][0]['COMPANY_CODE'] = '600519'
    if case == 'wrong_a_code': obj['result'][0]['A_STOCK_CODE'] = '600519'
    if case == 'rows': obj['result'].append(obj['result'][0])
    if case == 'errors': obj['actionErrors'] = ['bad']
    if case == 'missing_envelope': obj.pop('fieldErrors')
    raw = intake._encoded(obj)
    if case == 'duplicate': raw = raw.replace(b'"fieldErrors":{}', b'"fieldErrors":{"x":1,"x":1}')
    if case == 'utf8': raw = b'\xff'
    if case == 'size': raw = b'x'*(industry.LIMIT+1)
    result = execute(setup(tmp_path), raw=raw)
    assert result['industry_premise']['status'] == 'UNKNOWN'
    assert result['current_cash_observations']['group_count'] == 0
    assert len(result['qualified_field_observations']) == 6


def test_source_failure_never_requests_industry(tmp_path):
    data = setup(tmp_path)
    def forbidden(*a, **k): raise AssertionError('SSE after failed PDF')
    result = data[0].check_cash_source(data[-1], data[1], fetcher=lambda *a, **k: b'%PDF-changed',
        extractor=lambda *a: data[2], industry_check=True, industry_fetcher=forbidden)
    assert not result['qualified_field_observations']
    assert result['industry_premise']['gaps'] == ['official_fields_unavailable']


@pytest.mark.parametrize('np,cfo,complete', [('0','20',False), ('80','0',True), ('-80','-20',True),
    ('3','1',True), ('3000000000000','1',True), ('1'+'0'*190, '0.'+'0'*190+'1',False)])
def test_decimal_zero_negative_small_and_representation(tmp_path, np, cfo, complete):
    result = execute(setup(tmp_path, np=np, cfo=cfo))
    assert bool(result['current_cash_observations']['cash_groups']) is complete
    if np == '3':
        assert [f['display_value'] for f in result['qualified_field_observations'] if f['metric'] == 'CFOToNP'] == ['0.3333333333333333333333333333333333']*2
    if cfo == '0':
        assert all(f['display_value'] == '0' for f in result['qualified_field_observations'] if f['metric'] == 'CFOToNP')


@pytest.mark.parametrize('case', ['basis', 'definition', 'currency', 'pdf', 'formula', 'operand', 'arbitrary_derived', 'missing_cfo', 'window'])
def test_controlled_consumer_rechecks_fields_and_formula(tmp_path, monkeypatch, case):
    result, context, current = captured(setup(tmp_path), monkeypatch)
    snapshot = context.snapshot.model_copy(deep=True)
    record = next(r for r in snapshot.records if r.metric_or_claim == ('CFOToNP' if case in {'formula','operand','arbitrary_derived'} else 'operatingCashFlow'))
    if case == 'basis': record.period_basis = 'YTD'
    if case == 'definition': record.metric_definition_version = 'made_up'
    if case == 'currency': record.currency = 'USD'
    if case == 'pdf': record.source_document_hash = 'f'*64
    if case == 'formula': record.formula['expression'] = 'NP / CFO'
    if case == 'operand': record.lineage_ids = ['fake', 'fake']
    if case == 'arbitrary_derived': record.metric_or_claim = 'fake_ratio'
    if case == 'missing_cfo': snapshot.records.remove(record)
    if case == 'window': record.period_end = '2023-09-30'
    snapshot.snapshot_id = snapshot._content_hash()
    view = observe_current_official(replace(context, snapshot=snapshot), current)
    assert view['group_count'] == 0


def test_duplicate_points_never_inflate_and_json_is_not_context(tmp_path, monkeypatch):
    _, context, current = captured(setup(tmp_path), monkeypatch)
    snapshot = EvidenceSnapshot.build(context.security_id, context.snapshot.as_of,
        context.snapshot.records*2, strict=False)
    view = observe_current_official(replace(context, snapshot=snapshot, fields=context.fields*2), current)
    assert view['comparable_periods'] == 2 and view['group_count'] == 1
    for fake in [context.snapshot, context.snapshot.model_dump(mode='json'), {'result':'PASS'}]:
        with pytest.raises(TypeError): observe_current_official(fake, current)


def test_old_cutoff_rejects_and_does_not_request_sse(tmp_path):
    result = execute(setup(tmp_path), requested_as_of=datetime(2025,1,1,tzinfo=timezone.utc))
    assert result['industry_premise']['gaps'] == ['official_fields_unavailable']
    assert result['current_cash_observations']['comparable_periods'] == 0
    assert result['current_cash_observations']['as_of'].startswith('2025-01-01')


def test_getter_history_only_and_single_blob_read_with_v41_compatibility(tmp_path, monkeypatch):
    data = setup(tmp_path)
    app, identity, pages, pdf, code = data
    old = app.check_cash_source(code, identity, fetcher=lambda *a, **k: pdf, extractor=lambda *a: pages)
    result = execute(data)
    before = {p:p.read_bytes() for p in app.store.dir.rglob('*') if p.is_file()}
    def forbidden(*a, **k): raise AssertionError('getter performed action')
    monkeypatch.setattr(source, '_bounded_download', forbidden)
    monkeypatch.setattr(ResearchStore, 'save_cash_material_bytes', forbidden)
    original, reads = ResearchStore.load_cash_material_bytes, []
    def counted(self, kind, digest):
        reads.append((kind,digest)); return original(self,kind,digest)
    monkeypatch.setattr(ResearchStore,'load_cash_material_bytes',counted)
    history = app.get_cash_observations(code,as_of=datetime.now(timezone.utc))
    assert len(reads) == len(set(reads))
    summaries = history['cash_source_checks']['source_checks']
    assert len(summaries) == 2 and all(s['replay_status'] == 'NOT_REPLAYED_READONLY' for s in summaries)
    assert any(s.get('historical_observation_periods') == 2 for s in summaries)
    assert 'display_value' not in json.dumps(history) and 'CURRENT_SSE_REPORTED_NON_FINANCIAL' not in json.dumps(history)
    assert before == {p:p.read_bytes() for p in app.store.dir.rglob('*') if p.is_file()}


def test_storage_failure_preserves_old_receipt_and_never_returns_success(tmp_path, monkeypatch):
    data = setup(tmp_path)
    first = execute(data)
    old = data[0].store.load_cash_material_bytes('source_checks',first['storage_reference'])
    original = ResearchStore.save_cash_material_bytes
    def fail(self,kind,content):
        if kind == 'source_checks': raise OSError('half write')
        return original(self,kind,content)
    monkeypatch.setattr(ResearchStore,'save_cash_material_bytes',fail)
    with pytest.raises(OSError): execute(data)
    assert data[0].store.load_cash_material_bytes('source_checks',first['storage_reference']) == old


@pytest.mark.parametrize('rest', [['000001','--industry-check'], ['000001','--cash-verify','a'*64,'--industry-check'],
    ['000001','--cash-verify','a'*64,'--source-check','--industry-check','--industry-check'],
    ['000001','--cash-materials','x','--industry-check'],
    ['000001','--cash-verify','a'*64,'--source-check','--industry-check','--capture']])
def test_cli_flags_reject_before_app(monkeypatch, rest, capsys):
    def forbidden(*a,**k): raise AssertionError('invalid flags reached app')
    monkeypatch.setattr(ResearchApplicationService,'check_cash_source',forbidden)
    monkeypatch.setattr(ResearchApplicationService,'run',forbidden)
    cli.research_command(rest+['--json'])
    assert json.loads(capsys.readouterr().out)['result'] == 'FAILED'


def test_repl_cli_json_and_text_same_consumer(tmp_path,monkeypatch,capsys):
    import start
    import src.data.research_store as stores
    data = setup(tmp_path)
    monkeypatch.setattr(stores,'RESEARCH_DIR',data[0].store.dir)
    monkeypatch.setattr(source,'_http_pdf',lambda *a,**k:data[3])
    monkeypatch.setattr(source.numeric,'_extract_pdf',lambda *a:data[2])
    monkeypatch.setattr(industry,'_http_json',lambda *a,**k:sse_bytes())
    mode,args = start.parse_input(f'research 000001 --cash-verify {data[1]} --source-check --industry-check --json')
    start.run_cli(mode,args)
    result = json.loads(capsys.readouterr().out)
    assert result['current_cash_observations']['comparable_periods'] == 2
    output=io.StringIO(); monkeypatch.setattr(cli,'console',Console(file=output,width=240))
    cli.research_command(['000001','--cash-verify',data[1],'--source-check','--industry-check'])
    assert '1个当前条件化可比组、2期' in output.getvalue() and 'SSE转述CAPCO' in output.getvalue()


@pytest.mark.parametrize('status,mime,length,payload', [(302,'application/json',None,b'{}'),
    (429,'application/json',None,b'{}'), (200,'text/html',None,b'{}'),
    (200,'application/json',str(industry.LIMIT+1),b'{}'),
    (200,'application/json',None,b'x'*(industry.LIMIT+1))],
    ids=['redirect','throttle','mime','declared-size','stream-size'])
def test_fixed_http_envelope_bounds_no_redirect_retry_or_source_change(monkeypatch,status,mime,length,payload):
    import requests
    calls=[]
    class Response:
        status_code=status
        headers={'Content-Type':mime, **({'Content-Length':length} if length else {})}
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def iter_content(self,size): yield payload
    class Session:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def get(self,uri,**kwargs):
            calls.append((uri,kwargs))
            assert uri==industry.API
            assert kwargs['params']==dict(isPagination='false',sqlId=industry.SQL_ID,COMPANY_CODE='000001')
            assert kwargs['verify'] is True and kwargs['allow_redirects'] is False
            return Response()
    monkeypatch.setattr(requests,'Session',Session)
    with pytest.raises(ValueError): industry._http_json(industry._uri('000001'),timeout=30)
    assert len(calls)==1
    with pytest.raises(ValueError): industry._http_json('https://evil.invalid',timeout=30)
    assert len(calls)==1


def test_industry_timeout_late_result_has_no_storage_callback(tmp_path,monkeypatch):
    import threading
    data=setup(tmp_path)
    release=threading.Event()
    monkeypatch.setattr(industry,'TIMEOUT',0.02)
    def delayed(*a,**k):
        release.wait(2)
        return sse_bytes()
    try:
        result=data[0].check_cash_source(data[-1],data[1],fetcher=lambda *a,**k:data[3],extractor=lambda *a:data[2],
            industry_check=True,industry_fetcher=delayed)
        before={p:p.read_bytes() for p in data[0].store.dir.rglob('*') if p.is_file()}
        assert result['industry_premise']['gaps']==['industry_timeout']
        assert not result['current_cash_observations']['cash_groups'] and len(result['qualified_field_observations'])==6
    finally: release.set()
    assert before=={p:p.read_bytes() for p in data[0].store.dir.rglob('*') if p.is_file()}


def test_industry_explicit_old_cutoff_and_unknown_publication(tmp_path,monkeypatch):
    _,context,_=captured(setup(tmp_path),monkeypatch)
    late=industry._obtain(context.security_id,requested_as_of=context.snapshot.as_of,fetcher=lambda *a,**k:sse_bytes())
    assert late.public['status']=='UNKNOWN' and late.public['published_at'] is None
    view=observe_current_official(context,late,requested_as_of=context.snapshot.as_of)
    assert view['group_count']==0 and view['as_of']==context.snapshot.as_of.isoformat()


def test_industry_monotonic_total_budget(monkeypatch):
    import requests
    ticks=iter([0,0,0,0,31])
    monkeypatch.setattr(industry.time,'monotonic',lambda:next(ticks))
    class Response:
        status_code=200
        headers={'Content-Type':'application/json'}
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def iter_content(self,size): yield b'{}'
    class Session:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def get(self,*a,**k): return Response()
    monkeypatch.setattr(requests,'Session',Session)
    with pytest.raises(ValueError,match='industry_timeout'):
        industry._http_json(industry._uri('000001'),timeout=30)


def test_self_consistent_stored_success_never_replays_current_industry(tmp_path):
    data=setup(tmp_path)
    previous=execute(data)
    store=data[0].store
    record=intake._json(store.load_cash_material_bytes('source_checks',previous['storage_reference']))
    record['qualified_field_observations'][0]['display_value']='999999'
    record.pop('source_check_id')
    record['source_check_id']=intake._sha(intake._encoded(record))
    store.save_cash_material_bytes('source_checks',intake._encoded(record))
    calls=[]
    def failed(*a,**k):
        calls.append(True)
        raise ValueError('industry_http_status_429')
    result=data[0].check_cash_source(data[-1],data[1],fetcher=lambda *a,**k:data[3],extractor=lambda *a:data[2],
        industry_check=True,industry_fetcher=failed)
    assert calls==[True] and result['current_cash_observations']['group_count']==0
    assert all(f['display_value']!='999999' for f in result['qualified_field_observations'])


@pytest.mark.parametrize('bad_count',[3,True],ids=['out-of-range','boolean'])
def test_invalid_historical_count_leaves_no_hash_checked_summary(tmp_path,bad_count):
    data=setup(tmp_path)
    previous=execute(data)
    store=data[0].store
    record=intake._json(store.load_cash_material_bytes('source_checks',previous['storage_reference']))
    record['current_cash_observations']['comparable_periods']=bad_count
    record.pop('source_check_id')
    record['source_check_id']=intake._sha(intake._encoded(record))
    store.save_cash_material_bytes('source_checks',intake._encoded(record))
    history=data[0].get_cash_observations(data[-1],as_of=datetime.now(timezone.utc))['cash_source_checks']
    assert record['source_check_id'] not in {s['source_check_id'] for s in history['source_checks']}
    assert history['read_errors']==['source_check_or_dependency_invalid']
    assert len(history['source_checks'])==1
