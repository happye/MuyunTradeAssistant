"""O2 LONG 未知权重边界回归（plan/fusion iteration8；R14 Y4 反例，决策表 v4）

锁死语义（Y4 转绿合同；与 v3 行6 MID 同口径收敛到行8）：
1. 行8（LONG）：HELD + 权重未知 + VALID + 质量/买区合格 + 无急性风险——
   即使 budget_available=True 也 HOLD（预算已知不掩盖权重缺口），不给精确目标。
2. 已知权重 + budget=True 仍 ADD（正例保留）；budget=False/None → HOLD。
3. 硬退出（行1）先于行8——行8 冻结不回退退出方向；MID 行5/6 不顺改。
4. position_state=None 旧推导路径零变化（旧行为兼容：held 蕴含 confirmed 非 None，
   行8 not known 分支不可达）。
5. 版本登记：DECISION_TABLE_VERSION == "v4"（受影响行=行8）。

诚实边界：Y4 是公开策略接口可复现边界——当前 shadow 捕获不产生 LONG 质量/买区/
预算 True 组合，不宣称实盘事故（R14 披露口径）。

隔离纪律：纯函数调用，无持久化、零网络零 AI。
跑法：pytest tests/core/test_o2_long_unknown_weight.py -q
"""
import pytest

from src.core.decision_contract import DesiredAction, ExecutionStatus
from src.core.decision_policy import (
    DECISION_TABLE_VERSION, HorizonFacts, HorizonPlan, evaluate_horizon,
    POLICY_ID_LONG, POLICY_ID_MID,
)


def _plan(horizon="LONG"):
    return HorizonPlan(
        plan_id=f"o2_{horizon.lower()}", intent="O2 合同合成计划",
        horizon=horizon, accepted_at="2026-10-01",
        policy_id=POLICY_ID_LONG if horizon == "LONG" else POLICY_ID_MID)


def _long_row8_facts(**kw):
    """行8 全前提成立的事实底座（质量/买区/无急性风险）。"""
    base = dict(research_status="COMPLETE", thesis_status="VALID",
                quality_valuation_ok=True, price_in_buy_zone=True,
                acute_risk=False)
    base.update(kw)
    return HorizonFacts(**base)


# ── 1. Y4 主反例与行8 冻结矩阵（LONG）────────────────────────────────

def test_y4_long_held_unknown_weight_budget_true_frozen_to_hold():
    """R14 Y4：LONG+HELD+weight=None+VALID+质量/买区合格+budget=True → HOLD/None。"""
    pkt = evaluate_horizon(_plan(), _long_row8_facts(budget_available=True),
                           "600519", position_state="HELD", confirmed_ratio=None)
    assert pkt.desired_action is DesiredAction.HOLD, \
        f"行8 未知权重必须冻结为 HOLD（v4）: {pkt.desired_action} {pkt.reason_codes}"
    assert pkt.target_weight is None, f"未知权重不给精确目标: {pkt.target_weight}"
    assert pkt.execution_status is not ExecutionStatus.ELIGIBLE, \
        f"冻结不得给执行资格: {pkt.execution_status}"


@pytest.mark.parametrize("budget", [True, False, None])
def test_long_held_unknown_weight_always_frozen(budget):
    """HELD+未知权重：budget True/False/None 全部 HOLD（预算不弥补权重缺口）。"""
    pkt = evaluate_horizon(_plan(), _long_row8_facts(budget_available=budget),
                           "600519", position_state="HELD", confirmed_ratio=None)
    assert pkt.desired_action is DesiredAction.HOLD, \
        f"budget={budget} 时未知权重必须 HOLD: {pkt.desired_action}"


def test_long_held_known_weight_budget_true_still_adds():
    """已知权重 + budget=True 仍 ADD（正例保留——冻结只针对未知）。"""
    pkt = evaluate_horizon(_plan(), _long_row8_facts(budget_available=True),
                           "600519", position_state="HELD", confirmed_ratio=0.2)
    assert pkt.desired_action is DesiredAction.ADD, \
        f"已知权重正例不得被误伤: {pkt.desired_action} {pkt.reason_codes}"
    assert pkt.execution_status is ExecutionStatus.ELIGIBLE


def test_long_held_known_weight_budget_false_or_none_holds():
    """已知权重但预算不可用/未知 → HOLD（既有语义，回归）。"""
    for budget in (False, None):
        pkt = evaluate_horizon(_plan(), _long_row8_facts(budget_available=budget),
                               "600519", position_state="HELD", confirmed_ratio=0.2)
        assert pkt.desired_action is DesiredAction.HOLD, f"budget={budget}"


@pytest.mark.parametrize("state,known_ratio", [("NONE", 0.0), ("UNKNOWN", None)])
def test_long_not_held_still_open_candidate(state, known_ratio):
    """NONE（已知空仓）/UNKNOWN（持仓未知）+ budget=True → OPEN 候选（既有语义）。"""
    pkt = evaluate_horizon(_plan(), _long_row8_facts(budget_available=True),
                           "600519", position_state=state,
                           confirmed_ratio=known_ratio)
    assert pkt.desired_action is DesiredAction.OPEN, \
        f"state={state} 不得被行8 冻结误伤: {pkt.desired_action}"


# ── 2. MID 行6 对照（v3 既有保护不顺改）───────────────────────────────

def test_mid_held_unknown_weight_budget_true_still_frozen():
    """MID 行6（v3）：HELD+未知权重+budget=True → HOLD（既有保护，回归不回退）。"""
    pkt = evaluate_horizon(_plan("MID"),
                           HorizonFacts(research_status="COMPLETE", thesis_status="VALID",
                                        entry_condition_met=True, budget_available=True),
                           "600519", position_state="HELD", confirmed_ratio=None)
    assert pkt.desired_action is DesiredAction.HOLD


def test_mid_held_known_weight_budget_true_adds():
    """MID 行6 已知权重正例（既有语义，回归）。"""
    pkt = evaluate_horizon(_plan("MID"),
                           HorizonFacts(research_status="COMPLETE", thesis_status="VALID",
                                        entry_condition_met=True, budget_available=True),
                           "600519", position_state="HELD", confirmed_ratio=0.2)
    assert pkt.desired_action is DesiredAction.ADD


# ── 3. 有序规则：行8 不覆盖硬退出 ────────────────────────────────────

def test_long_hard_exit_precedes_row8_freeze():
    """硬退出触发 + 行8 全前提 + HELD 未知权重 → 行1 EXIT（冻结不吞退出方向）。"""
    pkt = evaluate_horizon(_plan(),
                           _long_row8_facts(budget_available=True,
                                            hard_exit_triggered=True,
                                            hard_exit_detail="基本面告警"),
                           "600519", position_state="HELD", confirmed_ratio=None)
    assert pkt.desired_action is DesiredAction.EXIT, \
        f"硬退出必须先于行8: {pkt.desired_action} {pkt.reason_codes}"
    assert "行1" in pkt.reason_codes[0]


def test_long_hard_exit_known_weight_still_exit():
    """硬退出 + 已知权重 → 行1 EXIT（既有语义回归——v4 不改行1）。"""
    pkt = evaluate_horizon(_plan(),
                           _long_row8_facts(budget_available=True,
                                            hard_exit_triggered=True,
                                            hard_exit_detail="基本面告警"),
                           "600519", position_state="HELD", confirmed_ratio=0.2)
    assert pkt.desired_action is DesiredAction.EXIT


# ── 4. 旧推导路径（不传 position_state）兼容 ─────────────────────────

def test_legacy_path_known_ratio_budget_true_still_adds():
    """position_state=None：confirmed_ratio>0 推导 held+known——行8 ADD（旧行为零变化）。"""
    pkt = evaluate_horizon(_plan(), _long_row8_facts(budget_available=True),
                           "600519", confirmed_ratio=0.2)
    assert pkt.desired_action is DesiredAction.ADD


def test_legacy_path_no_ratio_goes_open():
    """position_state=None：confirmed_ratio=None → 未持有+未知 → OPEN（旧行为零变化）。"""
    pkt = evaluate_horizon(_plan(), _long_row8_facts(budget_available=True),
                           "600519", confirmed_ratio=None)
    assert pkt.desired_action is DesiredAction.OPEN


# ── 5. 版本登记 ──────────────────────────────────────────────────────

def test_decision_table_version_bumped_to_v4():
    """规则版本 v4：受影响行=行8（登记口径——版本变更必须可见）。"""
    assert DECISION_TABLE_VERSION == "v4", \
        f"O2 资格语义变更必须 bump 决策表版本: {DECISION_TABLE_VERSION}"
