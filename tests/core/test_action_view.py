"""F2 行动卡终态判定回归测试（plan/fusion TASKS.md F2 验收：PROBES A/B 转真接线）

锁死语义（DESIGN ADR-F02：所有入口消费同一终态）：
- PROBES A 复现场景转真回归：原始 SELL + entry_exit 卖点触发，但末端
  StrategyDecision 为 HOLD/HOLD_POSITION（PlanGuard 压制）→ 终态判定必须 HOLD，
  不得播报"卖出信号"
- PROBES B 复现场景转真回归：原始 HOLD、entry_exit 为空，但末端 SELL/CLOSE_ALL
  （后置强制退出/硬信号救回）→ 终态判定必须 EXIT，不得播报"什么都不用做"
- G03：EXIT + 执行受阻 → 保留退出意图 + 阻塞说明，不得写成"继续看好"
- 用真实 StrategyDecision/ExecutionEvaluation 对象（PlanGuard/Execution 输出的
  真实类型），不是 AST 摘函数——验收条款"真正的PlanGuard与Execution接线回归"

纯内存测试，无网络。跑法：pytest tests/core/test_action_view.py -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from types import SimpleNamespace

from src.cli.action_view import (
    VERDICT_ADD,
    VERDICT_EXIT,
    VERDICT_HOLD,
    VERDICT_OPEN,
    VERDICT_REDUCE,
    VERDICT_REVIEW,
    VERDICT_WAIT,
    render_action_card,
    terminal_verdict,
)
from src.core.execution_layer import ExecutionEvaluation
from src.data.models import (
    PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
)


def _sd(decision: SignalType, pos_action: PositionAction,
        entry_exit: dict | None = None, reasons=None, sell_path=None) -> StrategyDecision:
    """真实 StrategyDecision（末端：PlanGuard/force_exit 已应用的形态）。"""
    return StrategyDecision(
        decision=decision,
        position_action=pos_action,
        action_semantic="EXIT" if pos_action is PositionAction.CLOSE_ALL else None,
        sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD if has(pos_action) else TradeLifecycle.FLAT,
        lifecycle_after=TradeLifecycle.HOLD if has(pos_action) else TradeLifecycle.FLAT,
        strategy_reasons=reasons or [],
        entry_exit=entry_exit,
        new_state=StrategyState(),
    )


def has(pos_action: PositionAction) -> bool:
    return pos_action in (PositionAction.HOLD_POSITION, PositionAction.REDUCE,
                          PositionAction.CLOSE_ALL, PositionAction.ADD)


def _eval(blocked=False, reason="") -> ExecutionEvaluation:
    return ExecutionEvaluation(
        original_action=PositionAction.HOLD_POSITION,
        effective_action=PositionAction.HOLD_POSITION,
        blocked=blocked, block_reason=reason,
        slippage_pct=0.0, impact_cost_pct=0.0, total_cost_pct=0.0,
    )


# ── PROBES A：原始卖点被 PlanGuard 压制 → 终态 HOLD ─────────

def test_probe_a_suppressed_sell_shows_hold():
    raw_sell = type("R", (), {"decision": type("D", (), {"value": "SELL"})()})()
    sd = _sd(SignalType.HOLD, PositionAction.HOLD_POSITION,
             entry_exit={"exit_triggered": True, "exit_action": "EXIT",
                         "exit_type": "trend_break", "exit_reason": "技术破位"},
             reasons=["PlanGuard: 计划未失效，压制 weak_sell"])
    v = terminal_verdict(sd, _eval(False), has_position=True, fallback_decision=raw_sell)
    assert v["bucket"] == VERDICT_HOLD, f"终态必须是 HOLD（压制后），实际 {v['bucket']}"
    assert v["degraded"] is False
    card = render_action_card(v, reasons=[v["reason"]])
    assert "继续持有" in card
    assert "卖出信号" not in card, "压制后的原始卖点不得播报为卖出信号（PROBES A 病根）"
    assert "PlanGuard" in card, "压制原因作为决定理由保留（诊断可查）"


# ── PROBES B：后置强制退出 → 终态 EXIT ─────────────────────

def test_probe_b_forced_exit_shows_exit():
    raw_hold = type("R", (), {"decision": type("D", (), {"value": "HOLD"})()})()
    sd = _sd(SignalType.SELL, PositionAction.CLOSE_ALL,
             reasons=["force_exit 救回: chandelier_stop"], sell_path="trend_exit")
    v = terminal_verdict(sd, _eval(False), has_position=True, fallback_decision=raw_hold)
    assert v["bucket"] == VERDICT_EXIT, "强制退出救回后终态必须 EXIT"
    card = render_action_card(v, reasons=[v["reason"]])
    assert "卖出" in card
    assert "什么都不用做" not in card, "后置强制退出不得播报为无事可做（PROBES B 病根）"


# ── G03：受阻退出保留意图 ─────────────────────────────────

def test_exit_blocked_keeps_exit_intent():
    sd = _sd(SignalType.SELL, PositionAction.CLOSE_ALL, reasons=["止损触发"])
    v = terminal_verdict(sd, _eval(True, "跌停封板，卖出委托无法成交"), has_position=True)
    assert v["bucket"] == VERDICT_EXIT and v["blocked"] is True
    card = render_action_card(v)
    assert "无法成交" in card and "退出待办保留" in card, "G03：持仓记录不变+待办保留"
    assert "继续看好" not in card and "继续持有" not in card


# ── 完整动作映射表 ────────────────────────────────────────

def test_action_mapping_table():
    cases = [
        (SignalType.SELL, PositionAction.REDUCE, VERDICT_REDUCE),
        (SignalType.BUY, PositionAction.ADD, VERDICT_ADD),
        (SignalType.BUY, PositionAction.OPEN, VERDICT_OPEN),
        (SignalType.HOLD, PositionAction.HOLD_POSITION, VERDICT_HOLD),
        (SignalType.WATCH, PositionAction.STAY_OUT, VERDICT_REVIEW),
        (SignalType.HOLD, PositionAction.STAY_OUT, VERDICT_WAIT),
    ]
    for decision, pos_action, expected in cases:
        v = terminal_verdict(_sd(decision, pos_action), _eval(False),
                             has_position=pos_action is not PositionAction.OPEN)
        assert v["bucket"] == expected, f"{pos_action.value} 应映射 {expected}"


def test_degraded_fallback_when_no_terminal():
    """无末端结果（strategy_decision=None）→ 降级视图按原始信号给保守判定。"""
    raw = type("R", (), {"decision": type("D", (), {"value": "SELL"})()})()
    v = terminal_verdict(None, None, has_position=True, fallback_decision=raw)
    assert v["bucket"] == VERDICT_EXIT and v["degraded"] is True
    card = render_action_card(v)
    assert "降级视图" in card, "降级必须标明，不得冒充终态"


# ── 摘要全链：PROBES A/B 经 _print_plain_summary（真实 StrategyDecision）──

def test_plain_summary_probe_a_suppressed_sell(monkeypatch, capsys):
    """PROBES A 全链：原始 SELL + 卖点触发，末端 HOLD_POSITION → 摘要不得播报卖出。"""
    from src.cli import main as cli_main
    raw = SimpleNamespace(decision=SimpleNamespace(value="SELL"), warnings=[])
    sd = _sd(SignalType.HOLD, PositionAction.HOLD_POSITION,
             entry_exit={"exit_triggered": True, "exit_action": "EXIT",
                         "exit_reason": "技术破位"},
             reasons=["PlanGuard: 计划未失效，压制"])
    pos = SimpleNamespace(current_ratio=0.2, entry_price=10.0, trade_plan=None)
    stock = SimpleNamespace(stock_code="600519", stock_name="X", price=10.0)
    cli_main._print_plain_summary(raw, sd, stock, pos=pos)
    out = capsys.readouterr().out
    assert "继续持有" in out, "终态 HOLD 必须播报持有"
    assert "卖出信号" not in out, "PROBES A 病根：压制后不得播报卖出信号"
    assert "未采纳" in out, "原始卖点作为诊断注解保留"


def test_plain_summary_probe_b_forced_exit(monkeypatch, capsys):
    """PROBES B 全链：原始 HOLD、ee 为空，末端 CLOSE_ALL → 摘要必须播报卖出。"""
    from src.cli import main as cli_main
    raw = SimpleNamespace(decision=SimpleNamespace(value="HOLD"), warnings=[])
    sd = _sd(SignalType.SELL, PositionAction.CLOSE_ALL,
             reasons=["force_exit 救回: chandelier_stop"], sell_path="trend_exit")
    pos = SimpleNamespace(current_ratio=0.2, entry_price=10.0, trade_plan=None)
    stock = SimpleNamespace(stock_code="601318", stock_name="X", price=10.0)
    cli_main._print_plain_summary(raw, sd, stock, pos=pos)
    out = capsys.readouterr().out
    assert "卖出" in out, "PROBES B 病根：强制退出必须播报"
    assert "什么都不用做" not in out


def test_plain_summary_exit_blocked(monkeypatch, capsys):
    """G03 摘要级：EXIT + 执行受阻 → 播报受阻+待办保留，不播报继续看好。"""
    from src.cli import main as cli_main
    raw = SimpleNamespace(decision=SimpleNamespace(value="SELL"), warnings=[])
    sd = _sd(SignalType.SELL, PositionAction.CLOSE_ALL, reasons=["止损触发"])
    pos = SimpleNamespace(current_ratio=0.2, entry_price=10.0, trade_plan=None)
    stock = SimpleNamespace(stock_code="601318", stock_name="X", price=10.0)
    blocked_eval = ExecutionEvaluation(
        original_action=PositionAction.CLOSE_ALL, effective_action=PositionAction.CLOSE_ALL,
        blocked=True, block_reason="跌停封板",
        slippage_pct=0.0, impact_cost_pct=0.0, total_cost_pct=0.0)
    cli_main._print_plain_summary(raw, sd, stock, pos=pos, execution_eval=blocked_eval)
    out = capsys.readouterr().out
    assert "卖不出" in out and "待办保留" in out
    assert "继续看好" not in out
