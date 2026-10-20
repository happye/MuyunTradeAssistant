"""ISS-116/T01 today uses one request's normalized account facts, read-only."""
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

import test_o0_exceptional_account as o0
from src.cli import main as cli
from src.cli import session_state
from src.cli.today_service import build_today_view, render_today
from src.data import portfolio, account_service
import start


def tree_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def setup_case(tmp_path, monkeypatch, case):
    o0._isolate(tmp_path, monkeypatch)
    pm = portfolio.PortfolioManager()
    if case in {'projection', 'mixed_alias', 'unknown_weight', 'partial', 'read_failure'}:
        pm.add_position('600519', stock_name='测试股', entry_price=10, ratio=.1)
    if case == 'read_failure':
        pm._data['positions']['600519']['current_ratio'] = 0
        pm._save()
    if case == 'mixed_alias':
        pm._data['positions']['sh600519'] = dict(pm._data['positions']['600519'])
        pm.add_position('000001', stock_name='投影独有', entry_price=10, ratio=.2)
        pm._save()
    if case == 'bad_record':
        pm._data = {'positions': {'600519': 42, '000001': {'current_ratio': .2}}}
        pm._save()
    if case != 'projection':
        service = account_service.AccountService(account_service.DEFAULT_LEDGER_PATH)
        service.opening_import(opening_cash=10000, trade_date='2026-09-20',
                               lots=[] if case in {'empty', 'partial', 'read_failure'} else
                               [{'security_id': '600519',
                                 'quantity': 100, 'cost_price': 10, 'acquired_at': '2026-09-20'}])
        if case == 'partial':
            with account_service.DEFAULT_LEDGER_PATH.open('a', encoding='utf-8') as out:
                out.write('{invalid_event}\n')
    calls = []
    original = account_service.AccountService.snapshot
    def snapshot(service):
        calls.append(service.ledger_path if hasattr(service, 'ledger_path') else 'snapshot')
        if case == 'read_failure':
            raise OSError('fixture ledger read failure')
        return original(service)
    monkeypatch.setattr(account_service.AccountService, 'snapshot', snapshot)
    return pm, calls


@pytest.mark.parametrize('case,codes,state,quantity,weight,incomplete', [
    ('ledger', {'600519'}, 'HELD', 100, None, False),
    ('projection', {'600519'}, 'HELD', None, .1, False),
    ('mixed_alias', {'600519', '000001'}, 'HELD', 100, None, False),
    ('bad_record', {'600519', '000001'}, 'HELD', 100, None, True),
    ('empty', set(), 'NONE', 0, 0., False),
    ('partial', {'600519'}, 'HELD', None, None, True),
    ('read_failure', {'600519'}, 'UNKNOWN', None, None, True),
    ('unknown_weight', {'600519'}, 'HELD', 100, None, False),
])
def test_today_fixed_account_matrix(tmp_path, monkeypatch, case, codes, state, quantity, weight, incomplete):
    pm, calls = setup_case(tmp_path, monkeypatch, case)
    before = tree_bytes(tmp_path)
    facts = pm.request_account_facts()
    entries, actual_incomplete = facts.holding_entries()
    assert {p.stock_code for p in entries} == codes
    assert actual_incomplete is incomplete
    ctx = facts.context_for('600519')
    assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == (state, quantity, weight)
    view = build_today_view(pm, account_facts=facts, risk_profile={'per_stock_max': .05})
    cards = view.needs_action + view.holding
    assert {c.stock_code for c in cards} == codes
    assert len(cards) == len(codes)
    assert view.holdings_incomplete is incomplete
    assert view.account_version == facts.account_version
    text = render_today(view)
    for card in cards:
        actual = facts.context_for(card.stock_code)
        assert (card.position_state, card.quantity, card.confirmed_weight, card.weight_reason,
                card.account_version) == (actual.position_state, actual.quantity,
                                         actual.confirmed_weight, actual.weight_reason, actual.account_version)
        assert actual.weight_reason in text
    if weight is None and codes:
        assert '权重待对账/待重估' in text and '10%' not in text
    if quantity == 100:
        assert '持仓 100股' in text and '持有 100 股' in text
    if incomplete:
        assert '清单可能不全' in text and '当前无持仓' not in text
    if case == 'bad_record':
        assert '600519 持仓记录读取失败' in text
    if case == 'read_failure':
        assert '账户账本读取失败' in text and '持仓待核' in text
    if facts.account_version:
        assert f'账户版本 {facts.account_version}' in text
    assert len(calls) == (0 if case == 'projection' else 1)
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize('case', ['bad_record', 'read_failure', 'partial'])
def test_today_real_repl_exceptional_readonly(tmp_path, monkeypatch, case):
    pm, calls = setup_case(tmp_path, monkeypatch, case)
    monkeypatch.setattr(cli, 'PortfolioManager', lambda: pm)
    monkeypatch.setattr(cli, '_fusion_risk_profile', lambda: {'per_stock_max': .05})
    monkeypatch.setattr(session_state, 'get_watch_active', lambda: [])
    captured = []
    real_request = pm.request_account_facts
    def request():
        facts = real_request()
        captured.append(facts)
        return facts
    monkeypatch.setattr(pm, 'request_account_facts', request)
    stream = StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=stream, width=300, color_system=None))
    before = tree_bytes(tmp_path)
    mode, args = start.parse_input('today')
    assert mode == 'today'
    start.run_cli(mode, args)
    text = stream.getvalue()
    assert len(calls) == 1 and len(captured) == 1
    assert '今日工作台' in text and '清单可能不全' in text
    assert '当前无持仓' not in text and '10%' not in text
    if case == 'bad_record':
        assert '600519' in text and '持仓 100股' in text
        assert captured[0].context_for('600519').position_state == 'HELD'
    if case == 'read_failure':
        assert '持仓待核' in text and '读取失败' in text
        assert captured[0].context_for('600519').position_state == 'UNKNOWN'
        assert captured[0].account_version == ''
    if captured[0].account_version:
        assert f'账户版本 {captured[0].account_version}' in text
    assert tree_bytes(tmp_path) == before


def test_today_next_request_reads_new_snapshot(tmp_path, monkeypatch):
    pm, calls = setup_case(tmp_path, monkeypatch, 'ledger')
    monkeypatch.setattr(cli, 'PortfolioManager', lambda: pm)
    monkeypatch.setattr(cli, '_fusion_risk_profile', lambda: None)
    monkeypatch.setattr(session_state, 'get_watch_active', lambda: [])
    stream = StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=stream, width=300, color_system=None))
    before = tree_bytes(tmp_path)
    start.run_cli(*start.parse_input('today'))
    assert tree_bytes(tmp_path) == before
    first = stream.getvalue()
    first_version = pm.request_account_facts().account_version
    assert f'账户版本 {first_version}' in first
    account_service.AccountService(account_service.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=10000, trade_date='2026-09-21',
        lots=[{'security_id': '000001', 'quantity': 200, 'cost_price': 5, 'acquired_at': '2026-09-21'}])
    before = tree_bytes(tmp_path)
    stream.seek(0); stream.truncate(0)
    calls.clear()
    start.run_cli(*start.parse_input('today'))
    assert len(calls) == 1
    second = stream.getvalue()
    assert '000001' in second and '持仓 200股' in second
    assert f'账户版本 {first_version}' not in second
    assert tree_bytes(tmp_path) == before


def test_today_pending_alias_uses_same_context_and_full_gaps(tmp_path, monkeypatch):
    pm, calls = setup_case(tmp_path, monkeypatch, 'unknown_weight')
    proposal = pm.record_proposal('600519', '测试股', SimpleNamespace(
        position_action=SimpleNamespace(value='REDUCE'), action_semantic='TRIM',
        sell_path='take_profit_trim', position_ratio=.05, strategy_reasons=['建议'],
        new_state=SimpleNamespace(lifecycle=SimpleNamespace(value='HOLD'),
                                  cooldown_remaining=0, cooldown_reason=None,
                                  current_position_ratio=.05)), source='l')
    assert proposal is not None
    proposal.stock_code = 'sh600519'
    monkeypatch.setattr(pm, 'pending_proposals', lambda: [proposal])
    for n in range(15):
        pm._data['positions'][f'{100000+n}'] = 42
    pm._save()
    before = tree_bytes(tmp_path)
    facts = pm.request_account_facts()
    view = build_today_view(pm, account_facts=facts)
    assert len(view.needs_action) == 1 and view.holding == []
    card = view.needs_action[0]
    assert card.stock_code == '600519' and card.quantity == 100
    assert card.confirmed_weight is None and card.account_version == facts.account_version
    text = render_today(view)
    assert '尚未记为成交' in text and '10%' not in text
    for code, reason in facts.projection_gaps.items():
        assert f'{code} {reason}' in text
    assert len(facts.projection_gaps) == 15 and len(calls) == 1
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize('case', ['partial', 'read_failure'])
def test_today_incomplete_without_surviving_entries_not_empty_account(tmp_path, monkeypatch, case):
    pm, calls = setup_case(tmp_path, monkeypatch, case)
    pm._data = {'positions': {}}
    pm._save()
    before = tree_bytes(tmp_path)
    facts = pm.request_account_facts()
    assert facts.context_for('600519').position_state == 'UNKNOWN'
    assert facts.holding_entries() == ([], True)
    view = build_today_view(pm, account_facts=facts)
    assert view.holding == [] and view.needs_action == []
    text = render_today(view)
    assert '不能证明空仓' in text and '当前无持仓' not in text
    assert len(calls) == 1 and tree_bytes(tmp_path) == before


def test_today_failed_request_does_not_retry_but_next_request_recovers(tmp_path, monkeypatch):
    pm, calls = setup_case(tmp_path, monkeypatch, 'ledger')
    snapshot = account_service.AccountService.snapshot
    fail = [True]
    def sometimes(service):
        if fail[0]:
            calls.append('failed')
            raise OSError('fixture first command failure')
        return snapshot(service)
    monkeypatch.setattr(account_service.AccountService, 'snapshot', sometimes)
    monkeypatch.setattr(cli, 'PortfolioManager', lambda: pm)
    monkeypatch.setattr(cli, '_fusion_risk_profile', lambda: None)
    monkeypatch.setattr(session_state, 'get_watch_active', lambda: [])
    stream = StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=stream, width=300, color_system=None))
    before = tree_bytes(tmp_path)
    start.run_cli(*start.parse_input('today'))
    assert calls == ['failed']
    assert '读取失败' in stream.getvalue() and '账户版本' not in stream.getvalue()
    fail[0] = False
    stream.seek(0); stream.truncate(0)
    start.run_cli(*start.parse_input('today'))
    assert len(calls) == 2
    assert '持仓 100股（事件账本）' in stream.getvalue() and '账户版本' in stream.getvalue()
    assert '读取失败' not in stream.getvalue()
    assert tree_bytes(tmp_path) == before


def explicit_snapshot(**changes):
    from src.data.account_snapshot import AccountSnapshot
    values = dict(as_of='2026-10-16T10:00:00', cash_available=4321,
                  account_version='explicit-A', data_completeness='QUANTITY_LEVEL',
                  holdings=[dict(security_id='000001', quantity=234, avg_cost=8)])
    values.update(changes)
    return AccountSnapshot(**values)


def test_today_explicit_snapshot_is_one_source(tmp_path, monkeypatch):
    pm, calls = setup_case(tmp_path, monkeypatch, 'ledger')
    snap = explicit_snapshot()
    original = snap.model_dump()
    before = tree_bytes(tmp_path)
    view = build_today_view(pm, account=snap)
    assert calls == []
    assert view.account_version == 'explicit-A'
    assert [(c.stock_code, c.quantity, c.account_version) for c in view.holding] == [
        ('000001', 234, 'explicit-A')]
    text = render_today(view)
    assert '4,321' in text and '000001 持有 234 股' in text
    assert '600519' not in text and '100股' not in text
    assert snap.model_dump() == original and tree_bytes(tmp_path) == before


def test_today_facts_have_priority_over_conflicting_snapshot(tmp_path, monkeypatch):
    pm, calls = setup_case(tmp_path, monkeypatch, 'ledger')
    facts = pm.request_account_facts()
    calls.clear()
    view = build_today_view(pm, account=explicit_snapshot(), account_facts=facts)
    assert calls == []
    assert view.account_version == facts.account_version
    assert [(c.stock_code, c.quantity) for c in view.holding] == [('600519', 100)]
    assert '4,321' not in render_today(view) and 'explicit-A' not in render_today(view)


def test_today_explicit_partial_does_not_confirm_old_zero(tmp_path, monkeypatch):
    pm, calls = setup_case(tmp_path, monkeypatch, 'read_failure')
    snap = explicit_snapshot(holdings=[], data_completeness='PARTIAL')
    view = build_today_view(pm, account=snap)
    assert calls == []
    assert view.holdings_incomplete and view.account_version == 'explicit-A'
    assert [(c.position_state, c.quantity, c.confirmed_weight) for c in view.holding] == [
        ('UNKNOWN', None, None)]
    assert '不能证明空仓' in render_today(view)


@pytest.mark.parametrize('kind', ['object', 'dict', 'mutated', 'constructed'])
def test_today_bad_explicit_snapshot_does_not_read_default(tmp_path, monkeypatch, kind):
    pm, calls = setup_case(tmp_path, monkeypatch, 'read_failure')
    snap = explicit_snapshot()
    if kind == 'object':
        snap = object()
    elif kind == 'dict':
        snap = snap.model_dump()
    elif kind == 'mutated':
        snap.holdings = [42]
    else:
        snap = type(snap).model_construct(as_of='invalid', holdings=[42])
    before = tree_bytes(tmp_path)
    view = build_today_view(pm, account=snap)
    assert calls == []
    assert view.account_version == '' and view.holdings_incomplete
    assert [(c.position_state, c.quantity, c.confirmed_weight) for c in view.holding] == [
        ('UNKNOWN', None, None)]
    assert '读取失败' in render_today(view) and '不能证明空仓' in render_today(view)
    assert tree_bytes(tmp_path) == before


def test_explicit_request_copies_snapshot_and_next_request_refreshes(tmp_path, monkeypatch):
    pm, calls = setup_case(tmp_path, monkeypatch, 'ledger')
    snap = explicit_snapshot(nav=10000, nav_priced_at='2026-09-20T10:00:00')
    original = snap.model_dump()
    before = tree_bytes(tmp_path)
    facts = pm.request_account_facts(account_snapshot=snap)
    assert snap.model_dump() == original and facts.account_snapshot is not snap
    snap.holdings[0].quantity = 456
    snap.cash_available = 9876
    snap.nav = 20000
    snap.nav_priced_at = None
    snap.account_version = 'explicit-C'
    first = build_today_view(pm, account_facts=facts)
    assert facts.context_for('000001').quantity == 234
    assert facts.context_for('000001', price=10, price_as_of='2026-09-20').confirmed_weight == .234
    assert '4,321' in render_today(first) and first.account_version == 'explicit-A'
    second = build_today_view(pm, account=snap)
    assert second.holding[0].quantity == 456 and second.account_version == 'explicit-C'
    assert '9,876' in render_today(second) and calls == []
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize('holdings', [
    [('000001', 100), ('sz000001', 0)],
    [('sz000001', 0), ('000001', 100)],
    [('000001', 100), ('000001', 0)],
    [('000001', 0), ('000001', 100)],
    [('000001', 100), ('sz000001', 100)],
    [('sz000001', 100), ('000001', 100)],
    [('000001', -1)],
])
def test_explicit_invalid_quantities_fail_whole_request(tmp_path, monkeypatch, holdings):
    pm, calls = setup_case(tmp_path, monkeypatch, 'ledger')
    snap = explicit_snapshot(holdings=[dict(security_id=code, quantity=qty)
                                       for code, qty in holdings])
    before_snapshot = snap.model_dump()
    before = tree_bytes(tmp_path)
    facts = pm.request_account_facts(account_snapshot=snap)
    view = build_today_view(pm, account_facts=facts)
    assert calls == [] and facts._read_failed and facts.account_snapshot is None
    assert facts.account_version == '' and facts.holding_entries() == ([], True)
    ctx = facts.context_for('000001')
    assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == ('UNKNOWN', None, None)
    assert '读取失败' in render_today(view) and '可用现金' not in render_today(view)
    assert snap.model_dump() == before_snapshot and tree_bytes(tmp_path) == before


@pytest.mark.parametrize('field', ['cash_available', 'cash_reserved'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_explicit_nonfinite_cash_fails_whole_request(tmp_path, monkeypatch, field, value):
    pm, calls = setup_case(tmp_path, monkeypatch, 'ledger')
    snap = explicit_snapshot()
    # Mutation is permitted by the existing model; the injection boundary must recheck it.
    setattr(snap, field, value)
    before_snapshot = snap.model_dump_json()
    before = tree_bytes(tmp_path)
    facts = pm.request_account_facts(account_snapshot=snap)
    view = build_today_view(pm, account_facts=facts)
    assert calls == [] and facts._read_failed and facts.account_snapshot is None
    assert facts.account_version == '' and facts.holding_entries() == ([], True)
    ctx = facts.context_for('000001')
    assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == ('UNKNOWN', None, None)
    assert '读取失败' in render_today(view) and '可用现金' not in render_today(view)
    assert snap.model_dump_json() == before_snapshot and tree_bytes(tmp_path) == before


@pytest.mark.parametrize('cash', [0, None])
@pytest.mark.parametrize('completeness', ['QUANTITY_LEVEL', 'RATIO_ONLY'])
def test_explicit_legal_zero_unknown_cash_and_quantity_remain_valid(
        tmp_path, monkeypatch, cash, completeness):
    pm, calls = setup_case(tmp_path, monkeypatch, 'ledger')
    snap = explicit_snapshot(cash_available=cash, cash_reserved=0,
                             holdings=[dict(security_id='000001', quantity=0)],
                             data_completeness=completeness)
    before_snapshot = snap.model_dump()
    before = tree_bytes(tmp_path)
    facts = pm.request_account_facts(account_snapshot=snap)
    view = build_today_view(pm, account_facts=facts)
    assert calls == [] and not facts._read_failed
    assert facts.account_version == 'explicit-A' and facts.holding_entries() == ([], False)
    ctx = facts.context_for('000001')
    assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == ('NONE', 0, 0.)
    text = render_today(view)
    assert ('可用现金 0 元' in text) if cash == 0 else ('现金未知' in text)
    assert snap.model_dump() == before_snapshot and tree_bytes(tmp_path) == before
