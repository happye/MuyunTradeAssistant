"""F2 分析服务回归测试（plan/fusion TASKS.md F2：末端 Strategy+Execution → DecisionPacket）

锁死语义：
1. 终态映射：position_action→desired_action；position_ratio→target（EXIT 恒 0）
2. 防御钳制：旧七层非法组合（空仓 ADD/REDUCE、已仓 OPEN、REDUCE 目标未降、
   已空仓 CLOSE_ALL）钳制为合法包 + reason_code 留痕，绝不产出非法包
3. 执行状态：execution_eval.blocked → BLOCKED+blocker；HOLD/WAIT/REVIEW → NOT_NEEDED；
   其余 ELIGIBLE 且 executable=desired
4. research_status：warnings→INCOMPLETE；divergence→CONFLICTED
5. legacy_trace 投影：原始 decision/score/position_action/position_ratio/sell_path
   全部可追（专家展开），不参与二次裁决

纯内存测试，无网络。跑法：pytest tests/core/test_analysis_service.py -q
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.analysis_service import POLICY_ID_LEGACY, build_decision_packet
from src.core.decision_contract import DesiredAction, ExecutionStatus, ResearchStatus
from src.core.execution_layer import ExecutionEvaluation
from src.data.models import (
    PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
)


def _dr(decision="HOLD", score=0.5, warnings=None):
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision), score=score,
        stock=SimpleNamespace(stock_code="601318"),
        warnings=warnings or [],
    )


def _sd(decision="HOLD", pos_action="HOLD_POSITION", ratio=0.0, reasons=None,
        sell_path=None, divergence=None):
    return StrategyDecision(
        decision=SignalType(decision),
        position_action=PositionAction(pos_action),
        position_ratio=ratio,
        sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=reasons or ["策略理由"],
        divergence=divergence,
        new_state=StrategyState(),
    )


def _eval(blocked=False, reason="") -> ExecutionEvaluation:
    return ExecutionEvaluation(
        original_action=PositionAction.HOLD_POSITION,
        effective_action=PositionAction.HOLD_POSITION,
        blocked=blocked, block_reason=reason,
        slippage_pct=0.0, impact_cost_pct=0.0, total_cost_pct=0.0,
    )


# ── 1. 终态映射 ───────────────────────────────────────────

def test_exit_packet_with_target_zero():
    p = build_decision_packet(_dr("SELL", 0.85), _sd("SELL", "CLOSE_ALL", 0.0, sell_path="stop_loss_exit"),
                              _eval(False), confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.EXIT
    assert p.target_weight == 0.0 and p.delta_weight == -0.2
    assert p.legacy_trace.action_strength == 0.85 and p.legacy_trace.sell_path == "stop_loss_exit"


def test_reduce_packet():
    p = build_decision_packet(_dr("SELL"), _sd("SELL", "REDUCE", 0.1), _eval(False),
                              confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.REDUCE and p.target_weight == 0.1
    assert p.execution_status is ExecutionStatus.ELIGIBLE
    assert p.executable_action is DesiredAction.REDUCE


def test_hold_packet_not_needed():
    p = build_decision_packet(_dr("HOLD"), _sd("HOLD", "HOLD_POSITION"), _eval(False),
                              confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.HOLD
    assert p.target_weight is None
    assert p.execution_status is ExecutionStatus.NOT_NEEDED
    assert p.executable_action is None


def test_add_packet():
    p = build_decision_packet(_dr("BUY"), _sd("BUY", "ADD", 0.3), _eval(False),
                              confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.ADD and p.target_weight == 0.3
    assert p.delta_weight == 0.1


def test_stay_out_watch_maps_review():
    p = build_decision_packet(_dr("WATCH"), _sd("WATCH", "STAY_OUT"), _eval(False),
                              confirmed_ratio=0.0)
    assert p.desired_action is DesiredAction.REVIEW
    p2 = build_decision_packet(_dr("HOLD"), _sd("HOLD", "STAY_OUT"), _eval(False),
                               confirmed_ratio=0.0)
    assert p2.desired_action is DesiredAction.WAIT


# ── 2. 防御钳制（旧七层非法组合 → 合法包 + 留痕）──────────

def test_clamp_add_without_position_to_open():
    p = build_decision_packet(_dr("BUY"), _sd("BUY", "ADD", 0.2), _eval(False),
                              confirmed_ratio=0.0)
    assert p.desired_action is DesiredAction.OPEN
    assert any("ADD 转 OPEN" in r for r in p.reason_codes)


def test_clamp_open_with_position_to_add():
    p = build_decision_packet(_dr("BUY"), _sd("BUY", "OPEN", 0.3), _eval(False),
                              confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.ADD


def test_clamp_reduce_without_position_to_wait():
    p = build_decision_packet(_dr("SELL"), _sd("SELL", "REDUCE", 0.0), _eval(False),
                              confirmed_ratio=0.0)
    assert p.desired_action is DesiredAction.WAIT and p.target_weight is None


def test_clamp_close_all_on_empty_to_wait():
    p = build_decision_packet(_dr("SELL"), _sd("SELL", "CLOSE_ALL", 0.0), _eval(False),
                              confirmed_ratio=0.0)
    assert p.desired_action is DesiredAction.WAIT
    assert any("清仓建议转 WAIT" in r for r in p.reason_codes)


def test_clamp_reduce_target_not_below_current():
    """REDUCE 目标未低于当前（旧七层 bug）→ target=None 保包合法，方向保留。"""
    p = build_decision_packet(_dr("SELL"), _sd("SELL", "REDUCE", 0.25), _eval(False),
                              confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.REDUCE and p.target_weight is None
    assert any("置 None" in r for r in p.reason_codes)


def test_unknown_portfolio_keeps_conditional_direction():
    """组合信息未知（confirmed=None）→ 有条件方向，target 必须 None。"""
    p = build_decision_packet(_dr("BUY"), _sd("BUY", "ADD", 0.2), _eval(False),
                              confirmed_ratio=None)
    assert p.confirmed_weight is None and p.target_weight is None
    assert p.desired_action is DesiredAction.OPEN  # 无持仓信息按 OPEN 方向


# ── 3. 执行状态 ───────────────────────────────────────────

def test_blocked_exit_packet():
    p = build_decision_packet(_dr("SELL"), _sd("SELL", "CLOSE_ALL", 0.0), _eval(True, "跌停封板"),
                              confirmed_ratio=0.2)
    assert p.execution_status is ExecutionStatus.BLOCKED
    assert p.executable_action is None, "受阻不能伪装可执行"
    assert p.blockers and "跌停" in p.blockers[0].detail
    assert p.desired_action is DesiredAction.EXIT, "退出意图保留（G03）"


# ── 4. research_status ───────────────────────────────────

def test_research_status_incomplete_on_warnings():
    p = build_decision_packet(_dr("HOLD", warnings=["量比缺失"]),
                              _sd("HOLD", "HOLD_POSITION"), _eval(False), confirmed_ratio=0.1)
    assert p.research_status is ResearchStatus.INCOMPLETE


def test_research_status_conflicted_on_divergence():
    div = {"type": "exit_vs_bullish_ai", "technical_signal": "卖点触发"}
    p = build_decision_packet(_dr("SELL"), _sd("SELL", "CLOSE_ALL", 0.0, divergence=div),
                              _eval(False), confirmed_ratio=0.2)
    assert p.research_status is ResearchStatus.CONFLICTED


# ── 5. 策略ID与 legacy 投影 ───────────────────────────────

def test_policy_id_is_legacy_and_trace_projection():
    p = build_decision_packet(_dr("SELL", 0.9), _sd("SELL", "CLOSE_ALL", 0.0,
                                                    reasons=["止损触发", "PlanGuard 放行"],
                                                    sell_path="stop_loss_exit"),
                              _eval(False), confirmed_ratio=0.2)
    assert p.policy_id == POLICY_ID_LEGACY == "legacy_v1"
    assert p.legacy_trace.decision == "SELL"
    assert p.legacy_trace.position_action == "CLOSE_ALL"
    assert p.legacy_trace.position_ratio == 0.0
    assert any("止损触发" == r for r in p.reason_codes)


# ── 6. analyze_packet 适配出口（DESIGN ADR-F01）─────────────

def test_orchestrator_analyze_packet_exit_exists():
    """适配出口在位且签名可调用（不跑全分析，仅锁协议存在性——真实接线测试归
    analyze() 集成域，这里防"出口被删/改名"的契约漂移）。"""
    import inspect
    from src.core.orchestrator import Orchestrator
    sig = inspect.signature(Orchestrator.analyze_packet)
    params = set(sig.parameters)
    assert {"self", "data", "confirmed_ratio", "source"} <= params, \
        f"analyze_packet 协议漂移: {params}"
    src = inspect.getsource(Orchestrator.analyze_packet)
    assert "build_decision_packet" in src, "出口必须经 analysis_service 构造终态"
    assert "self.analyze(" in src, "出口必须复用旧 analyze 协议（不复制七层流程）"
