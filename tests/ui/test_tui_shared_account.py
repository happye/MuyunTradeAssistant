"""T01 TUI business reads use a single isolated, real account snapshot."""
from types import SimpleNamespace

import pytest

import src.cli.main  # Import real by-value bindings before any monkeypatch.
from src.data import account_service, portfolio, proposals
from src.data.akshare_client import AKShareClient
from src.data.models import StockData
from src.tui.app import MuyunTUI


def seed(tmp_path, monkeypatch, case):
    monkeypatch.setattr(portfolio, 'DEFAULT_PORTFOLIO_PATH', str(tmp_path / 'portfolio.yaml'))
    monkeypatch.setattr(proposals, 'DEFAULT_PROPOSALS_PATH', str(tmp_path / 'proposals.json'))
    monkeypatch.setattr(account_service, 'DEFAULT_LEDGER_PATH', tmp_path / 'ledger.jsonl')
    pm = portfolio.PortfolioManager()
    if case in {'ratio', 'ratio_zero', 'alias', 'partial', 'failed_zero', 'failed_positive', 'bad_meta'}:
        pm.add_position('600519', stock_name='测试股', entry_price=10, ratio=.1)
    if case == 'alias':
        pm._data['positions']['sh600519'] = dict(pm._data['positions']['600519'])
        pm._save()
    if case in {'failed_zero', 'ratio_zero'}:
        pm._data['positions']['600519']['current_ratio'] = 0
        pm._save()
    if case == 'bad_meta':
        pm._data['positions']['600519']['entry_price'] = 'bad'
        pm._save()
    if case not in {'ratio', 'ratio_zero'}:
        account_service.AccountService(account_service.DEFAULT_LEDGER_PATH).opening_import(
            opening_cash=10000, trade_date='2026-09-20',
            lots=[] if case in {'empty', 'failed_empty', 'partial_empty'} else [
                {'security_id': '600519', 'quantity': 100, 'cost_price': 10,
                 'acquired_at': '2026-09-20'}])
    if case in {'partial', 'partial_empty'}:
        with account_service.DEFAULT_LEDGER_PATH.open('a', encoding='utf-8') as out:
            out.write('{invalid}\n')
    calls = []
    original = account_service.AccountService.snapshot
    def snapshot(service):
        calls.append('snapshot')
        if case.startswith('failed_'):
            raise OSError('isolated snapshot failure')
        return original(service)
    monkeypatch.setattr(account_service.AccountService, 'snapshot', snapshot)
    # No legacy second read is allowed, even when it would return the same records.
    monkeypatch.setattr(pm, 'list_positions', lambda: pytest.fail('legacy position read'))
    return pm, calls


def app_for(monkeypatch, pm):
    scan_calls, engine_calls, contexts = [], [], []
    original = pm.to_strategy_state
    def state(code, **kwargs):
        contexts.append(kwargs['account_context'])
        return original(code, **kwargs)
    monkeypatch.setattr(pm, 'to_strategy_state', state)
    monkeypatch.setattr(AKShareClient, 'calculate_indicators', lambda code: StockData(
        stock_code=code, stock_name='测试股', price=10, volume=100000,
        quote_as_of='2026-09-30'))
    monkeypatch.setattr(AKShareClient, 'get_realtime_quote', lambda code: pytest.fail('unexpected quote'))
    dr = SimpleNamespace(state=SimpleNamespace(value='NORMAL'),
                         decision=SimpleNamespace(value='HOLD'), score=.5, signals=[])
    sd = SimpleNamespace(position_action=SimpleNamespace(value='HOLD_POSITION'))
    def analyze(stock, **kwargs):
        engine_calls.append(kwargs)
        return dr, sd, None, None
    def scan(**kwargs):
        scan_calls.append(kwargs)
        return [SimpleNamespace(stock_code='600519', stock_name='测试股', price=10,
                                change_pct=0, turnover_rate=0, volume_ratio=0)], {}
    def init(app):
        app._portfolio = pm
        app._scanner = SimpleNamespace(quick_scan=scan)
        app._orchestrator = SimpleNamespace(analyze=analyze)
        app._engines_ready = True
    monkeypatch.setattr(MuyunTUI, '_init_engines', init)
    monkeypatch.setattr(pm, 'record_analysis_observation', lambda *a: True)
    monkeypatch.setattr(pm, 'record_proposal', lambda *a, **kw: None)
    return MuyunTUI(), scan_calls, engine_calls, contexts


def panel_text(app):
    return '\n'.join(line.text for line in app.query_one('#analysis-panel').lines)


@pytest.mark.asyncio
@pytest.mark.parametrize('case,state,weight,incomplete', [
    ('ratio', 'HELD', .1, False), ('ledger', 'HELD', None, False),
    ('alias', 'HELD', None, False), ('partial', 'HELD', None, True),
    ('failed_zero', 'UNKNOWN', None, True), ('failed_positive', 'HELD', None, True),
    ('bad_meta', 'HELD', None, True), ('empty', 'NONE', 0., False),
    ('ratio_zero', 'NONE', 0., False),
    ('failed_empty', 'UNKNOWN', None, True), ('partial_empty', 'UNKNOWN', None, True),
])
async def test_tui_real_account_matrix(tmp_path, monkeypatch, case, state, weight, incomplete):
    pm, calls = seed(tmp_path, monkeypatch, case)
    app, scans, engines, contexts = app_for(monkeypatch, pm)
    async with app.run_test(size=(180, 50)) as pilot:
        await app.action_scan().wait()
        await pilot.pause()
        expected = set() if case in {'empty', 'failed_empty', 'partial_empty'} else {'600519'}
        assert scans[0]['exclude_codes'] == expected
        assert len(calls) == (0 if case in {'ratio', 'ratio_zero'} else 1)
        scan_text = panel_text(app) + app.sub_title
        assert ('候选可能仍含已有持仓' in scan_text) is incomplete
        if incomplete:
            assert '清单可能不全' in scan_text
        await app.action_analyze().wait()
        await pilot.pause()
        assert len(calls) == (0 if case in {'ratio', 'ratio_zero'} else 2)
        assert len(engines) == len(contexts) == 1
        ctx, received = contexts[0], engines[0]
        assert ctx.position_state == state
        assert received['current_position_ratio'] == weight
        assert received['has_position'] is (state != 'NONE')
        assert received['trade_plan'] is ctx.trade_plan
        assert received['strategy_state'].current_position_ratio == weight
        text = panel_text(app)
        assert f'账户状态:{state}' in text
        assert ctx.weight_reason in text
        if weight is None:
            assert '权重待核' in text and '10.0%' not in text
        if incomplete:
            assert '清单可能不全' in text


@pytest.mark.asyncio
async def test_scan_next_request_refreshes_account(tmp_path, monkeypatch):
    pm, calls = seed(tmp_path, monkeypatch, 'ledger')
    app, scans, _, _ = app_for(monkeypatch, pm)
    async with app.run_test() as pilot:
        await app.action_scan().wait()
        assert scans[-1]['exclude_codes'] == {'600519'}
        account_service.AccountService(account_service.DEFAULT_LEDGER_PATH).opening_import(
            opening_cash=10000, trade_date='2026-09-21', lots=[
                {'security_id': '000001', 'quantity': 200, 'cost_price': 5,
                 'acquired_at': '2026-09-21'}])
        await app.action_scan().wait()
        await pilot.pause()
        assert scans[-1]['exclude_codes'] == {'600519', '000001'}
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_context_construction_failure_is_visible_and_does_not_analyze(tmp_path, monkeypatch):
    pm, _ = seed(tmp_path, monkeypatch, 'ledger')
    app, _, engines, _ = app_for(monkeypatch, pm)
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise ValueError('isolated context construction error')
    monkeypatch.setattr(pm, 'request_account_facts', fail)
    async with app.run_test(size=(180, 50)) as pilot:
        await app.action_scan().wait()
        await app.action_analyze().wait()
        await pilot.pause()
        assert not engines
        assert '账户资格不可核' in panel_text(app)
        assert 'isolated context construction error' in panel_text(app)
        assert {'estimate_nav': True} in calls


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['entry_price', 'state_assembly'])
async def test_independent_plan_survives_metadata_failure(tmp_path, monkeypatch, damage):
    pm, calls = seed(tmp_path, monkeypatch, 'alias')
    raw = pm._data['positions']['600519']
    raw['trade_plan'] = {
        'plan_id': 'original-plan', 'opened_at': '2026-09-20', 'why_buy': 'accepted',
        'when_buy': 'condition', 'how_much': .1, 'locked_initial_stop': 8,
        'current_stop': 9, 'when_sell_targets': [12], 'entry_price': 10}
    del pm._data['positions']['sh600519']
    if damage == 'entry_price':
        raw['entry_price'] = 'bad'
    pm._save()
    app, _, engines, _ = app_for(monkeypatch, pm)
    if damage == 'state_assembly':
        def fail_state(*a, **kw):
            raise ValueError('isolated state metadata failure')
        monkeypatch.setattr(pm, 'to_strategy_state', fail_state)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    async with app.run_test(size=(180, 50)) as pilot:
        await app.action_scan().wait()
        await app.action_analyze().wait()
        await pilot.pause()
        assert len(calls) == 2 and len(engines) == 1
        received = engines[0]
        assert received['has_position'] and received['current_position_ratio'] is None
        assert received['strategy_state'].current_position_ratio is None
        assert received['trade_plan'].plan_id == 'original-plan'
        assert received['trade_plan'].current_stop == 9
        text = panel_text(app)
        assert '账户状态:HELD' in text and '权重待核' in text
        if damage == 'state_assembly':
            assert '策略元数据读取失败' in text and '保留账户事实与独立计划' in text
        else:
            assert '清单可能不全' in text
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.asyncio
async def test_analysis_next_request_refreshes_account(tmp_path, monkeypatch):
    pm, calls = seed(tmp_path, monkeypatch, 'ledger')
    app, _, engines, contexts = app_for(monkeypatch, pm)
    async with app.run_test(size=(180, 50)) as pilot:
        await app.action_scan().wait()
        calls.clear()
        await app.action_analyze().wait()
        assert len(calls) == 1 and contexts[-1].quantity == 100
        first_version = contexts[-1].account_version
        account_service.AccountService(account_service.DEFAULT_LEDGER_PATH).opening_import(
            opening_cash=10000, trade_date='2026-09-21', lots=[
                {'security_id': '600519', 'quantity': 200, 'cost_price': 5,
                 'acquired_at': '2026-09-21'}])
        before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        await app.action_analyze().wait()
        await pilot.pause()
        assert len(calls) == 2 and contexts[-1].quantity == 300
        assert contexts[-1].account_version != first_version
        assert len(engines) == 2 and engines[-1]['current_position_ratio'] is None
        assert contexts[-1].weight_reason in panel_text(app)
        assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
