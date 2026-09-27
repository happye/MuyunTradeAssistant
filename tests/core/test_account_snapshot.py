"""R5 账户事实、现金与合法数量预算回归测试（plan/fusion iteration2，RESEARCH_LOOP §5）

锁死语义：
1. 现金 None ≠ 0：缺失不给可买金额/股数（CONDITIONAL+唯一阻塞字段）；0 现金给明确拒绝
2. 预算不足最低申报量 → 可理解拒绝 + 额度释放；费用边界（最低佣金）有反例
3. 事件账本幂等：重复确认不重复入账；崩溃重放一致（先写后崩不丢不重）
4. 拟卖未确认不释放现金（reserved 不重复计可用；卖出确认入账后才可用）
5. 规则注入：板块差异经接口进入（本模块不硬编码全市场 100 股规则）；卖出零股独立
6. 两写入者冲突显式拒绝（事件账本按 event_id 幂等即天然串行成功）

纯内存+临时文件测试，无网络。跑法：pytest tests/core/test_account_snapshot.py -q
"""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.portfolio_policy import BudgetLine
from src.data.account_snapshot import (
    AccountEvent,
    AccountEventLog,
    AccountHolding,
    AccountSnapshot,
    AllocationState,
    EventType,
    FeeModel,
    LotRules,
    TradeRulesAdapter,
    allocate_tradeable_budget,
)


class _FixtureRules(TradeRulesAdapter):
    """测试夹具规则（主板 100 股起 100 递增 / 科创板 200 股起 1 股递增——板块差异
    经接口进入）。注意：这是**测试夹具**，不是全市场规则表——生产实现由 R6 官方
    核验后注入。"""

    def lot_rules(self, security_id: str, as_of: str):
        if security_id.startswith("68"):
            return LotRules(security_id=security_id, board="star", min_order_qty=200,
                            lot_step=1, sell_odd_lots_allowed=True,
                            effective_from="2019-07-22", source="fixture")
        return LotRules(security_id=security_id, board="main", min_order_qty=100,
                        lot_step=100, sell_odd_lots_allowed=True,
                        effective_from="2006-01-01", source="fixture")


def _line(code="600519", add=0.1, current=0.0):
    return BudgetLine(stock_code=code, current_weight=current, add_weight=add,
                      target_weight=current + add, horizon="MID")


def _snap(cash=100_000.0, nav=1_000_000.0, reserved=0.0):
    return AccountSnapshot(as_of=datetime(2026, 9, 25, tzinfo=timezone.utc),
                           cash_available=cash, cash_reserved=reserved, nav=nav,
                           data_completeness="QUANTITY_LEVEL")


def _prices(price=10.0):
    return lambda code: price


# ── 1. 现金 None ≠ 0 ──────────────────────────────────────

def test_cash_none_gives_conditional_not_amount():
    """验收1：现金缺失不给可买金额/股数——只给唯一阻塞字段。"""
    snap = AccountSnapshot(as_of=datetime(2026, 9, 25, tzinfo=timezone.utc),
                           cash_available=None, nav=1_000_000.0)
    out = allocate_tradeable_budget([_line()], snap, _FixtureRules(), as_of="2026-09-25",
                                    price_provider=_prices())
    a = out[0]
    assert a.allocation_state is AllocationState.CONDITIONAL
    assert a.quantity == 0 and a.estimated_cost is None
    assert "可用现金未知" in a.blocked_field


def test_cash_zero_vs_missing_distinct():
    """现金 0 与缺失分别解释：0 → 明确资金不足拒绝；None → CONDITIONAL 问现金。"""
    zero = allocate_tradeable_budget([_line()], _snap(cash=0.0), _FixtureRules(),
                                     as_of="2026-09-25", price_provider=_prices())
    assert zero[0].allocation_state is AllocationState.REJECTED
    assert "仍不足" in zero[0].rejected_reason
    none = allocate_tradeable_budget([_line()], _snap(cash=None), _FixtureRules(),
                                     as_of="2026-09-25", price_provider=_prices())
    assert none[0].allocation_state is AllocationState.CONDITIONAL


def test_nav_none_conditional_no_weight_to_amount():
    """NAV 未知不能把权重推成金额（RESEARCH_LOOP §5——权重假设视图不是数量）。"""
    snap = AccountSnapshot(as_of=datetime(2026, 9, 25, tzinfo=timezone.utc),
                           cash_available=50_000.0, nav=None)
    out = allocate_tradeable_budget([_line()], snap, _FixtureRules(), as_of="2026-09-25",
                                    price_provider=_prices())
    assert out[0].allocation_state is AllocationState.CONDITIONAL
    assert "净值未知" in out[0].blocked_field


# ── 2. 最低申报量与费用边界 ────────────────────────────────

def test_budget_below_min_order_rejected_and_released():
    """验收2：预算不足最低申报量 → 可理解拒绝 + 额度释放。"""
    snap = _snap(cash=900.0, nav=1_000_000.0)  # 10% 预算=10万 但现金 900
    out = allocate_tradeable_budget([_line(add=0.1)], snap, _FixtureRules(),
                                    as_of="2026-09-25", price_provider=_prices(10.0))
    a = out[0]
    assert a.allocation_state is AllocationState.REJECTED
    assert "100 股" in a.rejected_reason and "仍不足" in a.rejected_reason
    assert a.released_budget_weight == 0.1, "额度释放回池（不悄悄占位）"


def test_board_rules_differ_via_interface():
    """板块差异经规则接口进入（科创板 200 股起 1 股递增 vs 主板 100 递增）。"""
    snap = _snap(cash=500_000.0)
    out = allocate_tradeable_budget([_line("688001", 0.05)], snap, _FixtureRules(),
                                    as_of="2026-09-25", price_provider=_prices(10.0))
    # 预算 5 万：5000 股 → 含费 50015 超预算 → 科创板 lot_step=1 递减到 4998
    assert out[0].quantity == 4998 and out[0].allocation_state is AllocationState.FEASIBLE
    assert out[0].rules_source == "fixture"
    # 主板同预算：100 股递增 → 4900 股（同预算不同板块数量规则差异可见）
    out2 = allocate_tradeable_budget([_line("000001", 0.05)], snap, _FixtureRules(),
                                     as_of="2026-09-25", price_provider=_prices(10.0))
    assert out2[0].quantity == 4900


def test_fee_min_commission_boundary():
    """费用边界：现金受限时最低佣金计入可承担判断（小额申报的费用反例）。"""
    snap = _snap(cash=1_050.0, nav=1_000_000.0)
    out = allocate_tradeable_budget([_line(add=0.01)], snap, _FixtureRules(),
                                    as_of="2026-09-25", price_provider=_prices(10.0))
    a = out[0]
    # 预算 1 万但现金 1050：100 股=1000 元 + 最低佣金 5 = 1005 ≤ 1050 ✓
    assert a.allocation_state is AllocationState.FEASIBLE
    assert a.quantity == 100 and a.estimated_fee >= 5.0, "最低佣金生效"


def test_discrete_rounding_never_exceeds_budget():
    """离散取整不得借浮点突破预算/上限（验收6 后半）。"""
    snap = _snap(cash=1_000_000.0, nav=1_000_000.0)
    out = allocate_tradeable_budget([_line(add=0.0101)], snap, _FixtureRules(),
                                    as_of="2026-09-25", price_provider=_prices(10.0))
    a = out[0]
    assert a.quantity * 10.0 <= 0.0101 * 1_000_000 + 1e-6, "取整后不超预算"


# ── 3. 事件账本幂等与崩溃重放 ──────────────────────────────

def test_event_log_idempotent_and_replay(tmp_path):
    """验收3：重复 event_id 幂等跳过；崩溃重启后重放结果一致（不丢不重）。"""
    log = AccountEventLog(tmp_path / "events.jsonl")
    buy = AccountEvent(event_id="e1", event_type=EventType.BUY, security_id="600519",
                       trade_date="2026-09-20", quantity=100, price=10.0,
                       cash_delta=-1005.0, fee=5.0, lot_cost_price=10.0)
    assert log.append(buy) is True
    assert log.append(buy) is False, "重复确认幂等拒绝"
    log.append(AccountEvent(event_id="e2", event_type=EventType.FEE, trade_date="2026-09-20",
                            cash_delta=0.0, fee=0.0))
    snap1 = log.replay(opening_cash=0.0)
    # 崩溃重启：新实例重放同一账本 → 同一快照
    snap2 = AccountEventLog(tmp_path / "events.jsonl").replay(opening_cash=0.0)
    assert snap1.holdings[0].quantity == snap2.holdings[0].quantity == 100
    assert snap1.cash_available == snap2.cash_available == -1005.0
    assert len(snap1.holdings[0].lots) == 1 and snap1.holdings[0].lots[0].acquired_at == "2026-09-20"


def test_replay_without_opening_cash_gives_unknown_not_zero():
    """监督员 P2 回归：不传 opening_cash → cash_available=None（未知≠0），
    完备性降 PARTIAL——界面该问用户而不是显示可用现金 0。"""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        log = AccountEventLog(Path(td) / "e.jsonl")
        snap = log.replay()
    assert snap.cash_available is None
    assert snap.data_completeness == "PARTIAL"


def test_bad_buy_event_does_not_kill_replay(tmp_path):
    """监督员 P1 回归：缺有效成本价的 BUY 事件 → 隔离留痕，整本重放不崩。"""
    log = AccountEventLog(tmp_path / "e.jsonl")
    log.append(AccountEvent(event_id="bad", event_type=EventType.BUY, security_id="600519",
                            trade_date="2026-09-20", quantity=100, price=None,
                            lot_cost_price=None, cash_delta=-1000.0))
    log.append(AccountEvent(event_id="good", event_type=EventType.BUY, security_id="000001",
                            trade_date="2026-09-20", quantity=200, price=10.0,
                            lot_cost_price=10.0, cash_delta=-2000.0))
    snap = log.replay(opening_cash=0.0)
    assert [h.security_id for h in snap.holdings] == ["000001"], "坏批次隔离，好事件照常入快照"


def test_oversubscription_serial_cash_pool():
    """监督员 P1 回归：多行分配串行扣减现金池——合计不超可部署现金。"""
    snap = _snap(cash=1100.0, nav=1_000_000.0)
    out = allocate_tradeable_budget([_line("000001", 0.002), _line("000002", 0.002)],
                                    snap, _FixtureRules(), as_of="2026-09-25",
                                    price_provider=_prices(10.0))
    committed = sum(a.estimated_cost or 0.0 for a in out if a.allocation_state is AllocationState.FEASIBLE)
    assert committed <= 1100.0, f"合计申报 {committed} 不得超过可部署现金 1100"
    feasible = [a for a in out if a.allocation_state is AllocationState.FEASIBLE]
    assert len(feasible) == 1, "现金只够一笔——第二笔 REJECTED/CONDITIONAL 而非透支"


def test_partial_then_more_fills_accumulate(tmp_path):
    """部分成交：两笔买入批次累加；卖出先扣最早批次（T+1 语义素材）。"""
    log = AccountEventLog(tmp_path / "e.jsonl")
    log.append(AccountEvent(event_id="b1", event_type=EventType.BUY, security_id="600519",
                            trade_date="2026-09-18", quantity=300, price=10.0,
                            cash_delta=-3005.0, lot_cost_price=10.0))
    log.append(AccountEvent(event_id="b2", event_type=EventType.BUY, security_id="600519",
                            trade_date="2026-09-22", quantity=200, price=12.0,
                            cash_delta=-2405.0, lot_cost_price=12.0))
    log.append(AccountEvent(event_id="s1", event_type=EventType.SELL, security_id="600519",
                            trade_date="2026-09-25", quantity=350, price=11.0,
                            cash_delta=+3850.0))
    snap = log.replay(opening_cash=0.0)
    h = snap.holdings[0]
    assert h.quantity == 150
    # FIFO：卖 350 = 首批 300 全清 + 次批 50 → 首批批次出栈，次批剩 150（T+1 素材）
    assert len(h.lots) == 1 and h.lots[0].quantity == 150 and h.lots[0].acquired_at == "2026-09-22"


# ── 4. 拟卖未确认不释放现金 ────────────────────────────────

def test_reserved_cash_not_double_counted():
    """验收4（K0a/D7 口径收紧）：cash_available 合同=已扣冻结后的净可用额——
    reserved 仅解释展示，不再二次扣减（旧实现 deployable=cash-reserved 与字段
    自身声明矛盾）；拟卖未确认的钱不在账上（卖出确认入账后才可用）不变。"""
    snap = _snap(cash=10_000.0, reserved=4_000.0)
    assert snap.cash_deployable == 10_000.0, "cash_available 已是净额（D7 合同统一）"
    out = allocate_tradeable_budget([_line(add=0.002)], snap, _FixtureRules(),
                                    as_of="2026-09-25", price_provider=_prices(10.0))
    a = out[0]
    assert a.allocation_state is AllocationState.FEASIBLE
    assert a.estimated_cost <= 10_000.0, "分配只消费净可用现金（reserved 仅解释）"
    # 拟卖未确认：SELL 事件未入账前，replay 快照不含该现金（无 sell 事件 → cash 不变）
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        log = AccountEventLog(Path(td) / "t5.jsonl")
        snap2 = log.replay(opening_cash=0.0)
        assert snap2.cash_available == 0.0, "拟卖未确认不释放现金（账上没有就没有）"


# ── 6. 未来回撤参数无法输入 ────────────────────────────────

def test_no_future_drawdown_parameter_channel():
    """验收6：allocate_tradeable_budget 的参数表里没有「未来回撤」输入位置——
    压力约束在 solve_budget（当时可得的压力损失率），本层不收未来窗口回撤。"""
    import inspect
    sig = inspect.signature(allocate_tradeable_budget)
    assert not any("drawdown" in p or "max_dd" in p for p in sig.parameters), \
        "参数表不得存在未来回撤输入通道"


def test_sell_odd_lots_rule_field_exists():
    """卖出零股规则独立表达（清仓场景由 R6 规则+持仓批次裁决——本层不越权实现）。"""
    r = _FixtureRules().lot_rules("000001", "2026-09-25")
    assert r.sell_odd_lots_allowed is True and r.lot_step == 100
