"""J4 有效模式与有效观察回归测试（plan/fusion iteration3，DELIVERY_PLAN J4 验收）

锁死语义：
1. 模式解析 requested/effective 拆分：opt_in/default 意愿登记不等于生效
   （effective=capture_only + blocking_gates 明示）；消费者只据 effective
2. 有效观察判定：observation_kind=effective ⇔ 真实接受计划 + 评估可解析
   （security/horizon/method 核对通过）+ 账户版本齐；缺任一 → diagnostic（不进
   有效比较分母）；无计划 → none
3. 同输入同日重复捕获去重（计划/账户版本变更才产生新观察——同输入重复不是
   独立观察）
4. 报告口径：0 有效样本显示「尚无证据」，不宣称稳定性

纯内存+临时文件测试，零网络零 AI，不读写真实 HOME（账户版本显式注入/
monkeypatch 隔离）。
跑法：pytest tests/core/test_j4_mode_observation.py -q
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.research_service import ASSERTION_METHOD_VERSION
from src.core.shadow_diff import (
    build_shadow_report,
    capture_shadow,
    render_shadow_report,
    resolve_fusion_mode_full,
)

_ON = {"fusion": {"mode": "capture_only"}}


def _dr(decision="HOLD"):
    return SimpleNamespace(decision=SimpleNamespace(value=decision), score=0.5,
                           stock=SimpleNamespace(stock_code="601318"), warnings=[])


def _sd():
    from src.data.models import PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle
    return StrategyDecision(
        decision=SignalType("HOLD"), position_action=PositionAction("HOLD_POSITION"),
        position_ratio=0.2, sell_path=None, lifecycle_before=TradeLifecycle.HOLD,
        lifecycle_after=TradeLifecycle.HOLD, strategy_reasons=["理由"],
        divergence=None, new_state=StrategyState())


def _eval():
    from src.core.execution_layer import ExecutionEvaluation
    from src.data.models import PositionAction
    return ExecutionEvaluation(original_action=PositionAction.HOLD_POSITION,
                               effective_action=PositionAction.HOLD_POSITION,
                               blocked=False, block_reason="", slippage_pct=0.0,
                               impact_cost_pct=0.0, total_cost_pct=0.0)


def _pos(code="601318"):
    return SimpleNamespace(stock_code=code, current_ratio=0.2, trade_plan=None,
                           stock_name="测试股")


def _packet():
    from src.core.analysis_service import build_decision_packet
    return build_decision_packet(_dr(), _sd(), _eval(), confirmed_ratio=0.2, source="test")


def _capture(tmp_path, plans_store, assessment_store=None, account_version="v_test"):
    return capture_shadow(_dr(), _sd(), _eval(), _pos(), packet=_packet(),
                          source="test", config=_ON,
                          store_path=tmp_path / "shadow.jsonl",
                          plans_store=plans_store, assessment_store=assessment_store,
                          account_version=account_version)


# ── 1. 模式解析（requested/effective 拆分）────────────────

def test_requested_opt_in_effective_capture():
    full = resolve_fusion_mode_full({"fusion": {"mode": "opt_in"}})
    assert full.requested_mode == "opt_in"
    assert full.effective_mode == "capture_only"
    assert full.blocking_gates, "意愿登记≠生效——未达门必须明示"


def test_passthrough_modes_have_no_gates():
    for m in ("legacy_only", "capture_only", "shadow"):
        full = resolve_fusion_mode_full({"fusion": {"mode": m}})
        assert full.effective_mode == m and not full.blocking_gates


# ── 2. 有效观察判定 ───────────────────────────────────────

def _seed_accepted_plan(tmp_path, plans_store, assessment_store, code="601318"):
    """带评估唯一真值的已激活计划（真判断路径）。"""
    from src.core.decision_contract import ThesisStatus
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    from src.core.research import ThesisAssessment
    plan = HorizonPlan(plan_id=f"p2_{code}_t", security_id=code,
                       accepted_at=datetime.now().isoformat(timespec="seconds"),
                       horizon="MID", policy_id=POLICY_ID_MID,
                       intent="锂电需求回暖驱动盈利兑现",
                       facts_observed=["6月订单环比+30%"])
    asm = ThesisAssessment(thesis_id=f"thesis_{code}_MID", security_id=code,
                           horizon="MID", snapshot_id="snap-j4",
                           status=ThesisStatus.VALID, method_version=ASSERTION_METHOD_VERSION,
                           evaluated_as_of=datetime.now().astimezone())
    plan.assessment_id = assessment_store.save(asm)
    plan.snapshot_id = asm.snapshot_id
    plans_store.save(plan)
    return plan, asm


def test_effective_observation_requires_assessment_and_account(tmp_path):
    """接受计划+评估核对通过+账户版本齐 → effective；缺账户版本 → diagnostic。"""
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, assessment_store=asm_store, account_version="v_abc")
    assert rec.observation_kind == "effective"
    assert rec.mid_active_plan_revision >= 1
    assert rec.mid_assessment_id.startswith("asm_")
    assert rec.account_version == "v_abc"
    # 缺账户版本（账本不存在——monkeypatch 隔离真实 HOME）→ diagnostic
    import src.data.account_service as _asvc
    orig = _asvc.DEFAULT_LEDGER_PATH
    try:
        _asvc.DEFAULT_LEDGER_PATH = tmp_path / "no-such-ledger.jsonl"
        rec2 = _capture(tmp_path, plans, assessment_store=asm_store, account_version="")
        assert rec2.observation_kind == "diagnostic", "缺账户版本不进有效比较"
    finally:
        _asvc.DEFAULT_LEDGER_PATH = orig


def test_mismatched_assessment_not_effective(tmp_path):
    """评估实体错配 → 引用不登记 → 观察降级 diagnostic（不冒充有效）。"""
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    plan, _ = _seed_accepted_plan(tmp_path, plans, asm_store)
    # 评估 security 写成别家 → capture 核对拒绝 → 引用为空
    from src.core.research import ThesisAssessment
    from src.core.decision_contract import ThesisStatus
    bad = ThesisAssessment(thesis_id="t", security_id="000002", horizon="MID",
                           snapshot_id="snap-j4", status=ThesisStatus.VALID,
                           method_version=ASSERTION_METHOD_VERSION,
                           evaluated_as_of=datetime.now().astimezone())
    plan.assessment_id = asm_store.save(bad)
    plans.save(plan)
    rec = _capture(tmp_path, plans, assessment_store=asm_store, account_version="v_abc")
    assert rec.observation_kind == "diagnostic"
    assert rec.mid_assessment_id == ""


# ── 3. 同输入去重 ─────────────────────────────────────────

def test_same_input_same_day_dedup(tmp_path, monkeypatch):
    """同输入（计划版本+账户版本不变）同日重复捕获 → 账本只记一条（不是独立
    观察）；计划版本变更 → 新观察。时间用 monkeypatch 控制——跨过同分钟规则
    单独验证 J4 指纹规则。（capture_shadow 始终返回 record——落盘条数才是判据。）"""
    import json as _json
    import src.core.shadow_diff as _sd
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)

    _state = {"n": 0}

    def _stamped_now(tz=None):
        # 每约 3 次调用推进一分钟（capture 内多处取 now）——保证 r2/r3 与 r1
        # 跨过同分钟规则，单独验证 J4 指纹去重
        _state["n"] += 1
        return datetime(2026, 9, 27, 10, min(_state["n"] // 3, 59), tzinfo=tz)

    monkeypatch.setattr(_sd, "datetime", type("DT", (datetime,), {"now": staticmethod(_stamped_now)}))
    _capture(tmp_path, plans, assessment_store=asm_store)          # 10:00
    _capture(tmp_path, plans, assessment_store=asm_store)          # 10:01 同输入
    _n_lines = lambda: sum(1 for _ in open(tmp_path / "shadow.jsonl", encoding="utf-8"))
    assert _n_lines() == 1, "同输入重复（跨分钟）不是独立观察（J4 指纹规则）"
    first_fp = _json.loads(open(tmp_path / "shadow.jsonl", encoding="utf-8").readline())["input_fingerprint"]
    # 计划修订（content 变）→ 新观察
    plan = plans.get("601318", "MID")
    plans.save(plan.model_copy(update={"intent": plan.intent + "（修订）"}))
    r3 = _capture(tmp_path, plans, assessment_store=asm_store)     # 10:30
    assert _n_lines() == 2 and r3 is not None
    assert r3.input_fingerprint != first_fp, "指纹随计划版本变化"
    monkeypatch.undo()


# ── 4. 报告口径 ───────────────────────────────────────────

def test_report_zero_effective_says_no_evidence(tmp_path):
    rec = _capture(tmp_path, _plans(tmp_path), account_version="v_test")
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl")
    text = render_shadow_report(report)
    assert report["effective_observations"] == 0
    assert "尚无证据" in text, "0 有效样本必须显示尚无证据"


def _plans(tmp_path):
    from src.data.horizon_plans import HorizonPlanStore
    return HorizonPlanStore(tmp_path / "plans.json")


def test_report_counts_effective(tmp_path):
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    _capture(tmp_path, plans, assessment_store=asm_store, account_version="v_abc")
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl")
    assert report["effective_observations"] == 1
    assert "有效观察 1 条" in render_shadow_report(report)
