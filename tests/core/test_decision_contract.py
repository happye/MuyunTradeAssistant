"""F0 决策契约回归测试（plan/fusion TASKS.md F0 验收条款）

锁死语义（DESIGN.md §2.2/§2.3 不变量的机器化）：
1. 非法动作/仓位组合构造期即报错（EXIT目标非0 / REDUCE不降 / ADD不加 / OPEN已有持仓 /
   HOLD改比例 / WAIT·REVIEW带目标 / delta矛盾 / BLOCKED缺blockers / 逻辑失效加仓）
2. UNKNOWN 与 MISSING 不被转换为 0：无组合信息 target/confirmed 保持 None；
   FactStatus.MISSING / TruthValue.UNKNOWN 原样保留；契约无模糊 score 字段
3. JSON 往返稳定：dump→load 全字段相等，二次 dump 字节一致；未知字段保留（ADR-F09）
4. 四份官方样例（BUY/WAIT/HOLD/EXIT受阻）真实可构造且满足各自描述的不变量

纯内存测试，无网络无文件。跑法：pytest tests/core/test_decision_contract.py -q
"""
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.decision_contract import (
    FUSION_SCHEMA_VERSION,
    Blocker,
    DecisionPacket,
    DesiredAction,
    EvidenceRef,
    ExecutionStatus,
    FactStatus,
    Horizon,
    InvalidationRule,
    LegacyTrace,
    MarketPhase,
    NextCheck,
    ResearchStatus,
    ThesisStatus,
    TruthValue,
    sample_buy_packet,
    sample_exit_blocked_packet,
    sample_hold_packet,
    sample_wait_packet,
)

from datetime import datetime, timezone

AWARE = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def _pkt(**overrides) -> dict:
    """合法 HOLD 基线字段，测试按需覆盖单项构造非法组合。"""
    base = dict(
        security_id="600519",
        as_of=AWARE,
        policy_id="legacy_v1",
        desired_action=DesiredAction.HOLD,
        confirmed_weight=0.2,
        target_weight=None,
        delta_weight=None,
        execution_status=ExecutionStatus.NOT_NEEDED,
    )
    base.update(overrides)
    return base


# ── 1. 四份官方样例 ────────────────────────────────────────

def test_sample_buy_packet():
    p = sample_buy_packet()
    assert p.desired_action is DesiredAction.OPEN
    assert p.confirmed_weight == 0.0 and p.target_weight == 0.15
    assert p.delta_weight == 0.15
    assert p.execution_status is ExecutionStatus.ELIGIBLE
    assert p.executable_action is DesiredAction.OPEN
    assert p.exchange == "SH"


def test_sample_wait_packet():
    p = sample_wait_packet()
    assert p.desired_action is DesiredAction.WAIT
    assert p.research_status is ResearchStatus.INCOMPLETE
    # UNKNOWN/缺失语义：组合未知 → target/confirmed 保持 None（不是 0）
    assert p.confirmed_weight is None and p.target_weight is None and p.delta_weight is None
    assert p.execution_status is ExecutionStatus.NOT_NEEDED
    assert p.blockers, "证据缺失型 WAIT 必须说明缺什么"


def test_sample_hold_packet():
    p = sample_hold_packet()
    assert p.desired_action is DesiredAction.HOLD
    assert p.confirmed_weight == 0.2
    assert p.target_weight is None and p.delta_weight is None
    assert p.legacy_trace is not None and p.legacy_trace.action_strength == 0.42


def test_sample_exit_blocked_packet():
    p = sample_exit_blocked_packet()
    # G03：退出意图保留 + 阻塞原因保留 + 不伪装可执行
    assert p.desired_action is DesiredAction.EXIT
    assert p.target_weight == 0.0
    assert p.execution_status is ExecutionStatus.BLOCKED
    assert p.blockers and p.blockers[0].kind == "limit_down"
    assert p.executable_action is None
    assert p.legacy_trace.sell_path == "stop_loss_exit"


# ── 2. 非法动作/仓位组合：构造期报错 ───────────────────────

def test_exit_with_nonzero_target_rejected():
    with pytest.raises(ValidationError, match="EXIT"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.EXIT, target_weight=0.2))


def test_reduce_not_reducing_rejected():
    with pytest.raises(ValidationError, match="REDUCE"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.REDUCE,
                              confirmed_weight=0.2, target_weight=0.3))


def test_reduce_from_zero_position_rejected():
    with pytest.raises(ValidationError, match="REDUCE"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.REDUCE,
                              confirmed_weight=0.0, target_weight=None))


def test_add_not_increasing_rejected():
    with pytest.raises(ValidationError, match="ADD"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.ADD,
                              confirmed_weight=0.2, target_weight=0.15))


def test_add_to_zero_position_rejected():
    with pytest.raises(ValidationError, match="ADD"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.ADD,
                              confirmed_weight=0.0, target_weight=0.3))


def test_open_with_existing_position_rejected():
    with pytest.raises(ValidationError, match="OPEN"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.OPEN,
                              confirmed_weight=0.2, target_weight=0.3))


def test_hold_changing_ratio_rejected():
    with pytest.raises(ValidationError, match="HOLD"):
        DecisionPacket(**_pkt(confirmed_weight=0.2, target_weight=0.25))


def test_wait_with_target_rejected():
    with pytest.raises(ValidationError, match="WAIT"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.WAIT, target_weight=0.1))


def test_review_with_target_rejected():
    with pytest.raises(ValidationError, match="REVIEW"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.REVIEW, target_weight=0.1))


def test_delta_mismatch_rejected():
    with pytest.raises(ValidationError, match="delta_weight"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.OPEN,
                              confirmed_weight=0.0, target_weight=0.2, delta_weight=0.3))


def test_delta_without_target_rejected():
    with pytest.raises(ValidationError, match="delta_weight"):
        DecisionPacket(**_pkt(delta_weight=0.1))


def test_unknown_portfolio_with_precise_add_target_rejected():
    """缺组合信息：可给有条件方向，不允许精确目标（不能默认推荐 20%/50%）。"""
    with pytest.raises(ValidationError, match="target_weight"):
        DecisionPacket(**_pkt(desired_action=DesiredAction.ADD,
                              confirmed_weight=None, target_weight=0.3))


def test_unknown_portfolio_precise_reduce_open_hold_target_rejected():
    """F0 审查 🔴-1 回归锁：OPEN/HOLD 同样受缺组合信息守卫（不只是 ADD/REDUCE）。"""
    for act in (DesiredAction.OPEN, DesiredAction.HOLD):
        with pytest.raises(ValidationError, match="target_weight"):
            DecisionPacket(**_pkt(desired_action=act,
                                  confirmed_weight=None, target_weight=0.2))
    # 有条件方向（target=None）依旧合法
    p = DecisionPacket(**_pkt(desired_action=DesiredAction.OPEN,
                              confirmed_weight=None, target_weight=None))
    assert p.target_weight is None


def test_conditional_with_executable_action_rejected():
    """F0 审查 🔴-2 回归锁：条件未满足不能伪装成可执行动作。"""
    with pytest.raises(ValidationError, match="CONDITIONAL"):
        DecisionPacket(**_pkt(execution_status=ExecutionStatus.CONDITIONAL,
                              blockers=[Blocker(kind="price_condition", detail="次日触发价未到")],
                              executable_action=DesiredAction.EXIT))


def test_packet_is_frozen_after_construction():
    """F0 审查 🔴-3 回归锁：决策记录不可变（ADR-F02），构造后赋值即报错。"""
    p = sample_hold_packet()
    with pytest.raises(ValidationError):
        p.target_weight = 0.95
    with pytest.raises(ValidationError):
        p.desired_action = DesiredAction.EXIT
    assert p.desired_action is DesiredAction.HOLD  # 原值未被改动


def test_model_construct_bypass_rejected():
    """F0 审查 ⚠️-1 结构化锁：绕过校验的构造直接禁用。"""
    with pytest.raises(NotImplementedError):
        DecisionPacket.model_construct(confirmed_weight=-5.0)


def test_fullwidth_digit_security_id_rejected():
    """F0 审查 ⚠️-2 回归锁：全角数字不产出垃圾ID。"""
    with pytest.raises(ValidationError, match="security_id"):
        DecisionPacket(**_pkt(security_id="６００５１９"))


def test_blocked_without_blockers_rejected():
    with pytest.raises(ValidationError, match="BLOCKED"):
        DecisionPacket(**_pkt(execution_status=ExecutionStatus.BLOCKED))


def test_blocked_with_executable_action_rejected():
    with pytest.raises(ValidationError, match="BLOCKED"):
        DecisionPacket(**_pkt(execution_status=ExecutionStatus.BLOCKED,
                              blockers=[Blocker(kind="limit_down", detail="跌停")],
                              executable_action=DesiredAction.EXIT))


def test_eligible_with_blockers_rejected():
    with pytest.raises(ValidationError, match="ELIGIBLE"):
        DecisionPacket(**_pkt(execution_status=ExecutionStatus.ELIGIBLE,
                              blockers=[Blocker(kind="funds", detail="现金不足")]))


def test_eligible_executable_must_equal_desired():
    with pytest.raises(ValidationError, match="executable_action"):
        DecisionPacket(**_pkt(execution_status=ExecutionStatus.ELIGIBLE,
                              executable_action=DesiredAction.EXIT))


def test_invalid_thesis_with_add_rejected():
    """G06：已核实反证（逻辑失效）不能被技术反弹抵消——禁止加仓。"""
    with pytest.raises(ValidationError, match="INVALID"):
        DecisionPacket(**_pkt(thesis_status=ThesisStatus.INVALID,
                              desired_action=DesiredAction.ADD,
                              confirmed_weight=0.2, target_weight=0.3))


def test_out_of_range_and_nan_weights_rejected():
    with pytest.raises(ValidationError):
        DecisionPacket(**_pkt(target_weight=1.5))
    with pytest.raises(ValidationError):
        DecisionPacket(**_pkt(confirmed_weight=-0.1))
    with pytest.raises(ValidationError):
        DecisionPacket(**_pkt(delta_weight=float("nan")))


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError, match="时区"):
        DecisionPacket(**_pkt(as_of=datetime(2026, 9, 25, 15, 0)))


def test_bad_security_id_rejected():
    with pytest.raises(ValidationError, match="security_id"):
        DecisionPacket(**_pkt(security_id="600519.SH"))
    with pytest.raises(ValidationError, match="security_id"):
        DecisionPacket(**_pkt(security_id="茅台"))


def test_security_id_normalized():
    p = DecisionPacket(**_pkt(security_id="1"))
    assert p.security_id == "000001" and p.exchange == "SZ"
    p2 = DecisionPacket(**_pkt(security_id=600519))  # YAML int 键同款防御
    assert p2.security_id == "600519" and p2.exchange == "SH"
    assert DecisionPacket(**_pkt(security_id="830001")).exchange == "BJ"


# ── 3. UNKNOWN / MISSING 不转 0 ───────────────────────────

def test_missing_semantics_survive():
    """FactStatus.MISSING / TruthValue.UNKNOWN 是显式状态，原样保留。"""
    ev = EvidenceRef(evidence_id="ev-x", source_kind="financial",
                     fact_status=FactStatus.MISSING, as_of=AWARE)
    assert ev.fact_status is FactStatus.MISSING
    rule = InvalidationRule(rule_id="r1", condition="经营现金流连续两季为负",
                            evaluation=TruthValue.UNKNOWN)
    assert rule.evaluation is TruthValue.UNKNOWN  # 无任何路径自动变 FALSE
    ev2 = EvidenceRef(evidence_id="ev-y", source_kind="ai_extraction",
                      fact_status=FactStatus.MODEL_INFERRED, as_of=AWARE)
    assert ev2.fact_status is FactStatus.MODEL_INFERRED  # 不冒充 OBSERVED


def test_naive_evidence_as_of_rejected():
    with pytest.raises(ValidationError, match="时区"):
        EvidenceRef(evidence_id="e", source_kind="news",
                    as_of=datetime(2026, 9, 25, 12, 0))


def test_contract_has_no_ambiguous_score_field():
    """契约不再叫模糊的 score：只有具名诊断字段（legacy_trace.action_strength）。"""
    p = sample_hold_packet()
    assert "score" not in p.model_dump()
    assert "win_probability" not in p.model_dump()
    assert p.legacy_trace.action_strength is not None  # 语义在名字里


def test_none_weights_stay_none_after_roundtrip():
    p = sample_wait_packet()
    d = p.model_dump()
    assert d["confirmed_weight"] is None and d["target_weight"] is None


# ── 4. JSON 往返稳定 ──────────────────────────────────────

@pytest.mark.parametrize("sample", [sample_buy_packet, sample_wait_packet,
                                    sample_hold_packet, sample_exit_blocked_packet])
def test_json_roundtrip_stable(sample):
    p = sample()
    j1 = p.model_dump_json()
    p2 = DecisionPacket.model_validate_json(j1)
    assert p2.model_dump() == p.model_dump()
    assert p2.model_dump_json() == j1  # 二次序列化字节一致


def test_extra_fields_preserved_roundtrip():
    """ADR-F09：未知字段保留，不丢弃（forward-compatible 数据）。"""
    p = DecisionPacket.model_validate({**_pkt(), "future_field": {"a": 1}})
    p2 = DecisionPacket.model_validate_json(p.model_dump_json())
    assert p2.model_dump()["future_field"] == {"a": 1}


def test_decision_id_unique_and_schema_version_frozen():
    a, b = sample_buy_packet(), sample_buy_packet()
    assert a.decision_id != b.decision_id
    assert a.schema_version == FUSION_SCHEMA_VERSION == "fusion.v1"


def test_legacy_trace_is_projection_only():
    """legacy_trace 只是投影：改它不影响主字段；无 score 语义回流路径。"""
    p = sample_exit_blocked_packet()
    assert p.legacy_trace.decision == "SELL"          # 中间态被如实记录
    assert p.desired_action is DesiredAction.EXIT     # 终态独立于投影
    lt = LegacyTrace(decision="BUY", action_strength=0.9,
                     position_action="OPEN", position_ratio=0.2)
    q = DecisionPacket(**_pkt(desired_action=DesiredAction.WAIT, legacy_trace=lt))
    assert q.legacy_trace.decision == "BUY" and q.desired_action is DesiredAction.WAIT
