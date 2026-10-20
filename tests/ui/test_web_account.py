"""T01 Web business consumers: real isolated account, fake market and engines."""
from types import SimpleNamespace as NS

import pytest

from src.data import account_service, portfolio, proposals
from src.data.akshare_client import AKShareClient
from src.web import app as web


@pytest.fixture
def case_account(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio, 'DEFAULT_PORTFOLIO_PATH', str(tmp_path / 'portfolio.yaml'))
    monkeypatch.setattr(account_service, 'DEFAULT_LEDGER_PATH', tmp_path / 'account.jsonl')
    monkeypatch.setattr(proposals, 'DEFAULT_PROPOSALS_PATH', str(tmp_path / 'proposals.json'))
    def build(case):
        pm = portfolio.PortfolioManager()
        if case in {'ratio', 'aliases', 'partial', 'failure_zero', 'failure_positive'}:
            pm.add_position('600519', stock_name='测试股', entry_price=10, ratio=.1)
        if case == 'aliases':
            pm._data['positions']['sh600519'] = dict(pm._data['positions']['600519'])
            pm.add_position('000001', ratio=.2)
        if case == 'failure_zero':
            pm._data['positions']['600519']['current_ratio'] = 0
        if case == 'bad_metadata':
            from src.data.models import TradePlan
            plan = TradePlan(plan_id='web-test', opened_at='2026-09-20', why_buy='测试',
                             when_buy='测试', how_much=.1, locked_initial_stop=9,
                             current_stop=9, entry_price=10)
            pm._data['positions']['600519'] = {'entry_price': 'bad', 'trade_plan': plan.model_dump(mode='json')}
        pm._save()
        if case != 'ratio':
            lots = [] if case in {'empty', 'abnormal_empty', 'failure_zero', 'failure_positive'} else [
                {'security_id': '600519', 'quantity': 100, 'cost_price': 10, 'acquired_at': '2026-09-20'}]
            account_service.AccountService(account_service.DEFAULT_LEDGER_PATH).opening_import(
                opening_cash=10000, trade_date='2026-09-20', lots=lots)
            if case in {'partial', 'abnormal_empty'}:
                with account_service.DEFAULT_LEDGER_PATH.open('a', encoding='utf-8') as out:
                    out.write('{invalid}\n')
        calls = []
        original = account_service.AccountService.snapshot
        def snapshot(service):
            calls.append(1)
            if case.startswith('failure'):
                raise OSError('<script>ledger failure</script>')
            return original(service)
        monkeypatch.setattr(account_service.AccountService, 'snapshot', snapshot)
        monkeypatch.setattr(web, '_portfolio', pm)
        monkeypatch.setattr(web, '_init_engines', lambda: True)
        monkeypatch.setattr(pm, 'list_positions', lambda: pytest.fail('business consumer reread projection'))
        return pm, calls
    build.root = tmp_path
    return build


CASES = ['ratio', 'ledger', 'aliases', 'partial', 'failure_zero',
         'failure_positive', 'bad_metadata', 'empty', 'abnormal_empty']


def expected(case):
    codes = set() if case in {'empty', 'abnormal_empty'} else {'600519'}
    if case == 'aliases':
        codes.add('000001')
    incomplete = case in {'partial', 'failure_zero', 'failure_positive', 'bad_metadata', 'abnormal_empty'}
    state = 'NONE' if case == 'empty' else 'UNKNOWN' if case in {'failure_zero', 'abnormal_empty'} else 'HELD'
    return codes, incomplete, state


@pytest.mark.parametrize('case', CASES)
def test_scan_real_account_inputs_and_final_html(case_account, monkeypatch, case):
    pm, calls = case_account(case)
    before = {p.name: p.read_bytes() for p in case_account.root.iterdir() if p.is_file()}
    received = []
    monkeypatch.setattr(web, '_scanner', NS(quick_scan=lambda **kw: (received.append(kw) or [], {})))
    result = web._scan_work()
    codes, incomplete, _ = expected(case)
    assert received[0]['exclude_codes'] == codes
    monkeypatch.setattr(web, 'get_task', lambda _: {'status': 'done', 'result': result})
    html = web.app.test_client().get('/scan/fixture').get_data(as_text=True)
    assert ('候选可能仍含已有持仓' in html) is incomplete
    assert len(calls) == (0 if case == 'ratio' else 1)
    assert {p.name: p.read_bytes() for p in case_account.root.iterdir() if p.is_file()} == before


@pytest.mark.parametrize('case', CASES)
def test_analysis_real_account_inputs_and_final_html(case_account, monkeypatch, case):
    pm, calls = case_account(case)
    before = {p.name: p.read_bytes() for p in case_account.root.iterdir() if p.is_file()}
    contexts = []
    original_state = pm.to_strategy_state
    def state(code, **kw):
        contexts.append(kw['account_context'])
        return original_state(code, **kw)
    monkeypatch.setattr(pm, 'to_strategy_state', state)
    stock = NS(stock_code='600519', stock_name='测试股', price=10, quote_as_of='2026-09-30',
               change_pct=0, ma5=None)
    monkeypatch.setattr(AKShareClient, 'calculate_indicators', lambda _: stock)
    received, writes = [], []
    sd = NS(position_action=NS(value='HOLD_POSITION'))
    dr = NS(state=NS(value='neutral'), decision=NS(value='HOLD'), score=0, signals=[])
    monkeypatch.setattr(web, '_orchestrator', NS(analyze=lambda data, **kw: (received.append(kw) or dr, sd, None, None)))
    monkeypatch.setattr(pm, 'record_analysis_observation', lambda *a: writes.append('observation') or True)
    monkeypatch.setattr(pm, 'record_proposal', lambda *a, **kw: writes.append('proposal'))
    result = web._analyze_work('600519')
    _, incomplete, state = expected(case)
    kw = received[0]
    assert contexts == [result['account_context']]
    assert contexts[0] is result['account_context']
    assert kw['trade_plan'] is contexts[0].trade_plan
    assert kw['has_position'] is (state != 'NONE')
    weight = .1 if case == 'ratio' else 0. if case == 'empty' else None
    assert kw['current_position_ratio'] == weight
    assert kw['strategy_state'].current_position_ratio == weight
    if case == 'bad_metadata':
        assert kw['trade_plan'] is not None and kw['entry_price'] is None
    monkeypatch.setattr(web, 'get_task', lambda _: {'status': 'done', 'result': result})
    html = web.app.test_client().get('/analyze-task/fixture').get_data(as_text=True)
    assert state in html
    assert ('权重待对账/待重估' in html) is (weight is None)
    assert ('清单可能不全' in html) is incomplete
    assert '10.0%' not in html if weight is None else True
    assert '<script>' not in html
    assert len(calls) == (0 if case == 'ratio' else 1)
    assert writes == ([] if case in {'ledger', 'bad_metadata', 'empty', 'abnormal_empty'} else ['observation', 'proposal'])
    assert {p.name: p.read_bytes() for p in case_account.root.iterdir() if p.is_file()} == before


@pytest.mark.parametrize('case', CASES)
def test_calendar_real_filter_final_html(case_account, monkeypatch, case):
    pm, calls = case_account(case)
    before = {p.name: p.read_bytes() for p in case_account.root.iterdir() if p.is_file()}
    from src.core import expectation
    received = []
    monkeypatch.setattr(portfolio, 'PortfolioManager', lambda: pm)
    monkeypatch.setattr(web, '_init_engines', lambda: pytest.fail('calendar initialized engines'))
    monkeypatch.setattr(expectation, 'EventCalendar', lambda: NS(
        get_upcoming_events=lambda **kw: received.append(kw) or [], check_stale_static_events=lambda: []))
    monkeypatch.setattr(expectation, 'ExpectationOverdrawCalculator', lambda: NS(calculate=lambda: NS(data_status='failed')))
    html = web.app.test_client().get('/calendar').get_data(as_text=True)
    codes, incomplete, _ = expected(case)
    assert received[0]['codes'] == (sorted(codes) if codes else None)
    assert ('账户过滤不完整' in html) is incomplete
    if not codes:
        assert '通用日历' in html
    assert '<script>' not in html
    assert len(calls) == (0 if case == 'ratio' else 1)
    assert {p.name: p.read_bytes() for p in case_account.root.iterdir() if p.is_file()} == before


def test_context_construction_failure_stops_analysis(case_account, monkeypatch):
    pm, _ = case_account('empty')
    monkeypatch.setattr(pm, 'request_account_facts', lambda: (_ for _ in ()).throw(ValueError('<script>broken</script>')))
    monkeypatch.setattr(AKShareClient, 'calculate_indicators', lambda _: NS(price=10, quote_as_of=None))
    monkeypatch.setattr(web, '_orchestrator', NS(analyze=lambda *a, **kw: pytest.fail('fake flat suggestion')))
    result = web._analyze_work('600519')
    monkeypatch.setattr(web, 'get_task', lambda _: {'status': 'done', 'result': result})
    html = web.app.test_client().get('/analyze-task/fixture').get_data(as_text=True)
    assert '账户资格不可核' in html and '本次不生成分析建议' in html
    assert '<script>' not in html and '&lt;script&gt;' in html


@pytest.mark.parametrize('consumer', ['scan', 'calendar'])
def test_failed_account_list_visible_and_escaped(case_account, monkeypatch, consumer):
    pm, _ = case_account('empty')
    monkeypatch.setattr(pm, 'request_account_facts', lambda: (_ for _ in ()).throw(ValueError('<script>broken</script>')))
    if consumer == 'scan':
        received = []
        monkeypatch.setattr(web, '_scanner', NS(quick_scan=lambda **kw: (received.append(kw) or [], {})))
        result = web._scan_work()
        assert received[0]['exclude_codes'] == set()
        monkeypatch.setattr(web, 'get_task', lambda _: {'status': 'done', 'result': result})
        html = web.app.test_client().get('/scan/fixture').get_data(as_text=True)
        assert '候选可能仍含已有持仓' in html
    else:
        from src.core import expectation
        monkeypatch.setattr(portfolio, 'PortfolioManager', lambda: pm)
        received = []
        monkeypatch.setattr(expectation, 'EventCalendar', lambda: NS(
            get_upcoming_events=lambda **kw: received.append(kw) or [], check_stale_static_events=lambda: []))
        monkeypatch.setattr(expectation, 'ExpectationOverdrawCalculator', lambda: NS(calculate=lambda: NS(data_status='failed')))
        html = web.app.test_client().get('/calendar').get_data(as_text=True)
        assert received[0]['codes'] is None
        assert '账户过滤不完整' in html and '通用日历' in html
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_bad_state_metadata_keeps_known_account_and_independent_plan(case_account, monkeypatch):
    pm, calls = case_account('bad_metadata')
    monkeypatch.setattr(pm, 'to_strategy_state', lambda *a, **kw: (_ for _ in ()).throw(ValueError('<script>state</script>')))
    stock = NS(stock_code='600519', stock_name='测试股', price=10, quote_as_of='2026-09-30', change_pct=0, ma5=None)
    monkeypatch.setattr(AKShareClient, 'calculate_indicators', lambda _: stock)
    received = []
    dr = NS(state=NS(value='neutral'), decision=NS(value='HOLD'), score=0, signals=[])
    sd = NS(position_action=NS(value='HOLD_POSITION'))
    monkeypatch.setattr(web, '_orchestrator', NS(analyze=lambda data, **kw: (received.append(kw) or dr, sd, None, None)))
    result = web._analyze_work('600519')
    assert received[0]['has_position'] is True and received[0]['current_position_ratio'] is None
    assert received[0]['trade_plan'] is result['account_context'].trade_plan
    assert received[0]['trade_plan'] is not None
    monkeypatch.setattr(web, 'get_task', lambda _: {'status': 'done', 'result': result})
    html = web.app.test_client().get('/analyze-task/fixture').get_data(as_text=True)
    assert 'HELD' in html and '保留账户事实和独立计划' in html
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert len(calls) == 1


@pytest.mark.parametrize('consumer', ['scan', 'analysis', 'calendar'])
def test_next_request_refreshes_real_ledger(case_account, monkeypatch, consumer):
    pm, calls = case_account('ledger')
    received = []
    if consumer == 'scan':
        def scan(**kw):
            received.append(kw['exclude_codes'])
            return [NS(stock_code=code, stock_name=code, price=10, change_pct=0,
                       turnover_rate=None, volume_ratio=None)
                    for code in sorted({'600519', '000001'} - kw['exclude_codes'])], {}
        monkeypatch.setattr(web, '_scanner', NS(quick_scan=scan))
        def invoke():
            result = web._scan_work()
            monkeypatch.setattr(web, 'get_task', lambda _: {'status': 'done', 'result': result})
            return web.app.test_client().get('/scan/fixture').get_data(as_text=True)
    elif consumer == 'analysis':
        monkeypatch.setattr(AKShareClient, 'calculate_indicators', lambda _: NS(
            stock_code='000001', stock_name='测试股', price=10, quote_as_of='2026-09-30', change_pct=0, ma5=None))
        dr = NS(state=NS(value='neutral'), decision=NS(value='HOLD'), score=0, signals=[])
        sd = NS(position_action=NS(value='HOLD_POSITION'))
        monkeypatch.setattr(web, '_orchestrator', NS(analyze=lambda data, **kw: (received.append(kw['has_position']) or dr, sd, None, None)))
        def invoke():
            result = web._analyze_work('000001')
            monkeypatch.setattr(web, 'get_task', lambda _: {'status': 'done', 'result': result})
            return web.app.test_client().get('/analyze-task/fixture').get_data(as_text=True)
    else:
        from src.core import expectation
        monkeypatch.setattr(portfolio, 'PortfolioManager', lambda: pm)
        def upcoming(**kw):
            received.append(kw['codes'])
            return [NS(name=code, date='2026-10-20', days_to_event=5, event_type='other', impact=1, source='fixture')
                    for code in kw['codes']]
        monkeypatch.setattr(expectation, 'EventCalendar', lambda: NS(
            get_upcoming_events=upcoming, check_stale_static_events=lambda: []))
        monkeypatch.setattr(expectation, 'ExpectationOverdrawCalculator', lambda: NS(calculate=lambda: NS(data_status='failed')))
        invoke = lambda: web.app.test_client().get('/calendar').get_data(as_text=True)
    first_html = invoke()
    assert len(calls) == 1
    account_service.AccountService(account_service.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=10000, trade_date='2026-09-21', lots=[
            {'security_id': '000001', 'quantity': 200, 'cost_price': 5, 'acquired_at': '2026-09-21'}])
    next_html = invoke()
    assert len(calls) == 2
    if consumer == 'analysis':
        assert received == [False, True]
        assert 'NONE' in first_html and 'HELD' in next_html
        assert '权重待对账/待重估' in next_html
    elif consumer == 'scan':
        assert received == [{'600519'}, {'600519', '000001'}]
        assert '000001' in first_html and '000001' not in next_html
    else:
        assert received == [['600519'], ['000001', '600519']]
        assert '000001' not in first_html and '000001' in next_html


def test_alias_observation_and_proposal_persist_without_confirming_trade(case_account, monkeypatch):
    from copy import deepcopy
    from src.data.models import StrategyState, SignalType

    pm, calls = case_account('ratio')
    record = pm._data['positions'].pop('600519')
    record['entry_date'] = '2026-09-20'
    pm._data['positions']['sh600519'] = record
    assert pm._save().ok
    before = deepcopy(record)
    stock = NS(stock_code='600519', stock_name='测试股', price=12,
               quote_as_of='2026-09-30', change_pct=0, ma5=None)
    monkeypatch.setattr(AKShareClient, 'calculate_indicators', lambda _: stock)
    # 建议刻意带不同仓位/成本/日期；观察白名单不得把它们当成交回写。
    sd = NS(position_action=NS(value='ADD'), action_semantic='ADD', position_ratio=.8,
            strategy_reasons=['测试加仓建议'], sell_path=None, entry_exit={'highest_since_entry': 13},
            new_state=StrategyState(current_position_ratio=.8, entry_price=99,
                                    entry_date='2026-10-01', inertia_counter=3,
                                    last_decision=SignalType.BUY))
    dr = NS(state=NS(value='neutral'), decision=NS(value='BUY'), score=0, signals=[])
    monkeypatch.setattr(web, '_orchestrator', NS(analyze=lambda *a, **kw: (dr, sd, None, None)))
    result = web._analyze_work('600519')
    ctx = result['account_context']
    assert ctx.projection.stock_code == '600519'
    assert ctx.projection.raw_storage_key == 'sh600519'
    # 重新实例化后查磁盘结果，不能只断言 mock 调用或内存赋值。
    reloaded = portfolio.PortfolioManager()
    assert set(reloaded._data['positions']) == {'sh600519'}
    persisted = reloaded._data['positions']['sh600519']
    assert persisted['high_since_entry'] == 13
    assert persisted['strategy_state']['inertia_counter'] == 3
    assert persisted['strategy_state']['last_decision'] == 'BUY'
    for key in ('current_ratio', 'entry_price', 'entry_date', 'lifecycle'):
        assert persisted[key] == before[key]
    pending = reloaded.pending_proposals()
    assert len(pending) == 1
    assert pending[0].stock_code == 'sh600519'
    assert pending[0].position_action == 'ADD' and pending[0].target_ratio == .8
    assert pending[0].status == 'PROPOSED'
    assert calls == []
