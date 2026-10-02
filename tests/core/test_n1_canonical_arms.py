"""N1 按规范包记录两臂 + 有效版本资格回归测试（plan/fusion iteration7，DELIVERY_PLAN N1；R13 X2 反例）

锁死语义（R13 X2 转绿；shadow_v9 候选协议 + 决策表 v3；O1/O2 后为 shadow_v10 + v4；P0/Z1 后为 shadow_v11）：
1. 两臂各投影自己的 DecisionPacket——legacy 臂消费最终 legacy 包
   （action/target/blockers/execution_status 同枚举同语义），原始
   StrategyDecision 字段只留 legacy_trace；不再出现「规范包 target=None 而
   legacy 臂 .1/KNOWN」「包 execution=ELIGIBLE 而 legacy 字段是动作名」
2. 有效版本资格：仅当期已验证组合（生成版本 r4.research_service_v1 × 评估
   方法 ASSERTION_METHOD_VERSION × 当前决策表 v3）进有效分母；未知/不兼容
   版本 → diagnostic（版本字符串存在≠兼容；不改写历史计划）
3. 行5 UNKNOWN 技术退出 → REVIEW（不给无规模 REDUCE）；行6 HELD+未知权重
   即使 budget=True 仍 HOLD（预算已知不掩盖权重缺口）；不传三态路径明确迁移
4. 旧桶 MID/LONG 独立计数（diagnostic=两者皆无）；v8 不入 v9 分母；
   纯采集时间变化仍折叠、真实变化留痕（M1 语义在新协议保持）

隔离纪律同 M0/M1：持久化路径显式重定向；零网络零 AI。
跑法：pytest tests/core/test_n1_canonical_arms.py -q
"""
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.core.shadow_diff as shadow_module
import src.data.account_service as account_module
import src.data.horizon_plans as horizon_plans_module
import src.data.research_store as research_store_module
from src.core.analysis_service import build_decision_packet
from src.core.decision_contract import DesiredAction, ThesisStatus
from src.core.decision_policy import (
    DECISION_TABLE_VERSION, POLICY_ID_MID, HorizonFacts, HorizonPlan, evaluate_horizon,
)
from src.core.research import ThesisAssessment
from src.core.research_service import ASSERTION_METHOD_VERSION, RESEARCH_SERVICE_VERSION
from src.core.shadow_diff import (
    SHADOW_DERIVATION_VERSION, build_shadow_report, capture_shadow,
)
from src.data.models import (
    PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", tmp_path / "plans.json")
    monkeypatch.setattr(research_store_module, "RESEARCH_DIR", tmp_path / "research")
    return tmp_path


def _sd(pos_action="HOLD_POSITION", ratio=0.1, sell_path=None, decision="HOLD"):
    return StrategyDecision(
        decision=SignalType(decision), position_action=PositionAction(pos_action),
        position_ratio=ratio, sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"], new_state=StrategyState(current_position_ratio=ratio))


def _dr(decision="HOLD"):
    return SimpleNamespace(decision=SimpleNamespace(value=decision), score=0.6,
                           stock=SimpleNamespace(stock_code="600519", stock_name="测试股"),
                           warnings=[])


def _eval(blocked=False):
    from src.core.execution_layer import ExecutionEvaluation
    return ExecutionEvaluation(
        original_action=PositionAction.HOLD_POSITION,
        effective_action=PositionAction.HOLD_POSITION,
        blocked=blocked, block_reason="跌停封板" if blocked else "",
        slippage_pct=0.0, impact_cost_pct=0.0, total_cost_pct=0.0)


def _pos(ratio=0.1):
    return SimpleNamespace(stock_code="600519", stock_name="测试股",
                           current_ratio=ratio, trade_plan=None)


def _seed_accepted_mid(tmp_path, *, policy_version=RESEARCH_SERVICE_VERSION):
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    plan = HorizonPlan(plan_id="p2_600519_n1", security_id="600519", horizon="MID",
                       policy_id=POLICY_ID_MID, intent="N1 测试",
                       policy_version=policy_version)
    asm = ThesisAssessment(thesis_id="thesis_600519_MID", security_id="600519",
                           horizon="MID", snapshot_id="snap-n1",
                           status=ThesisStatus.VALID,
                           method_version=ASSERTION_METHOD_VERSION,
                           evaluated_as_of=datetime.now().astimezone() - timedelta(minutes=1))
    plan.assessment_id = asm_store.save(asm)
    plan.snapshot_id = asm.snapshot_id
    plans.save(plan)
    ok, msg = plans.accept("600519", "MID")
    assert ok, msg
    return plans, asm_store


def _capture(tmp_path, plans, asm_store, *, sd=None, ratio=0.1, packet=None):
    packet = packet or build_decision_packet(_dr(), sd or _sd(ratio=ratio), _eval(False),
                                             confirmed_ratio=ratio, source="test")
    return capture_shadow(_dr(sd.decision.value if sd else "HOLD"), sd or _sd(ratio=ratio),
                          _eval(False), _pos(ratio),
                          packet=packet, source="test",
                          config={"fusion": {"mode": "capture_only"}},
                          store_path=tmp_path / "shadow.jsonl",
                          plans_store=plans, assessment_store=asm_store,
                          account_version="v_n1", quote_as_of="2026-09-29",
                          quote_fetched_at="2026-09-29T15:00:00+08:00",
                          account_context=None)


# ── 1. 两臂各消费自己的规范包 ────────────────────────────────────────

def test_hold_arm_matches_canonical_packet(tmp_path, monkeypatch):
    """真实策略保护分支产出的 HOLD（比例 .1）：规范包 target=None——legacy 臂
    必须同为 None/UNKNOWN（不得拿 StrategyDecision.position_ratio 冒充 .1/KNOWN）。"""
    _isolate(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    # 真实策略分支：BUY+未知权重 → (HOLD_POSITION, 0.0) 占位（strategy_layer 权重
    # 未知阻精确新增）；装配阶段（行334）position_ratio 被覆盖为
    # new_state.current_position_ratio——X2 探针的「HOLD_POSITION/ratio=.1」
    # 正是这一真实装配形态（占位 0 被当前值 .1 顶替）
    from src.core.strategy_layer import StrategyLayer
    from src.data.models import DecisionResult, DecisionTrace, MarketState, StockData
    layer = StrategyLayer()
    dr_in = DecisionResult(
        stock=StockData(stock_code="600519", stock_name="测试股", price=10.0,
                        volume=100000, change_pct=1.0),
        state=MarketState.RISK_ON, decision=SignalType.BUY, score=0.7,
        signals=[], trace=[DecisionTrace(step="市场状态影响", description="test",
                                         data={"final_scores": {"BUY": 0.9, "SELL": 0.0}})],
        position_action=PositionAction.ADD, position_ratio=0.3)
    action, ratio_out = layer._calculate_position(
        SignalType.BUY, MarketState.RISK_ON, dr_in,
        StrategyState(lifecycle=TradeLifecycle.HOLD, entry_price=10.0,
                      current_position_ratio=None))
    assert (action, ratio_out) == (PositionAction.HOLD_POSITION, 0.0), \
        f"前置：真实保护分支占位形态: {(action, ratio_out)}"
    # 行334 装配覆盖：position_ratio ← new_state.current_position_ratio（X2 形态 .1）
    sd = StrategyDecision(decision=SignalType.BUY, position_action=action,
                          position_ratio=0.1, sell_path=None,
                          lifecycle_before=TradeLifecycle.HOLD,
                          lifecycle_after=TradeLifecycle.HOLD,
                          strategy_reasons=["真实策略保护分支 HOLD（装配覆盖 .1）"],
                          new_state=StrategyState(current_position_ratio=0.1))
    packet = build_decision_packet(_dr("HOLD"), sd, _eval(False),
                                   confirmed_ratio=0.1, source="test")
    assert packet.target_weight is None, "规范包 HOLD 不给数值目标"
    rec = _capture(tmp_path, plans, asm_store, sd=sd, packet=packet)
    arm = rec.mid_binding["arms"]["legacy"]
    assert arm["action"] == "HOLD"
    assert arm["target"] is None, \
        f"legacy 臂必须消费规范包 target（None），不得拿原始 .1: {arm}"
    assert arm["target_state"] == "UNKNOWN"
    assert arm["execution"] == "NOT_NEEDED", \
        f"legacy execution 必须是规范包 ExecutionStatus: {arm['execution']}"
    assert rec.mid_effective is True


def test_reduce_arm_execution_uses_status_enum(tmp_path, monkeypatch):
    """正常 REDUCE：规范包 execution=ELIGIBLE——legacy 臂同枚举（不是动作名 REDUCE）；
    target/blockers 与规范包逐字段一致。"""
    _isolate(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    sd = _sd("REDUCE", ratio=0.1, sell_path="take_profit_trim", decision="SELL")
    packet = build_decision_packet(_dr("SELL"), sd, _eval(False),
                                   confirmed_ratio=0.2, source="test")
    assert packet.execution_status.value == "ELIGIBLE"
    rec = _capture(tmp_path, plans, asm_store, sd=sd, packet=packet)
    arm = rec.mid_binding["arms"]["legacy"]
    assert arm["action"] == "REDUCE"
    assert arm["execution"] == "ELIGIBLE", \
        f"legacy execution 必须取规范包执行状态（同枚举）: {arm['execution']}"
    assert arm["target"] == packet.target_weight
    # 阻塞同语义：legacy 臂 = legacy 规范包（BLOCKED+阻塞原因）；
    # fusion 臂 = fusion 规范包自己的执行语义（两臂各投影各的包——不混用）
    packet_b = build_decision_packet(_dr("SELL"), sd, _eval(True),
                                     confirmed_ratio=0.2, source="test")
    rec_b = _capture(tmp_path, plans, asm_store, sd=sd, packet=packet_b)
    leg_b = rec_b.mid_binding["arms"]["legacy"]
    fus_b = rec_b.mid_binding["arms"]["fusion"]
    assert packet_b.execution_status.value == "BLOCKED"
    assert leg_b["execution"] == "BLOCKED", f"legacy 臂取自己包的执行状态: {leg_b}"
    assert leg_b["blockers"] == [b.detail for b in packet_b.blockers] and leg_b["blockers"]
    # fusion 臂消费 fusion 规范包自身执行语义（行5 REDUCE → ELIGIBLE）——不混用 legacy 阻塞
    assert fus_b["execution"] == "ELIGIBLE"
    assert fus_b["blockers"] == []


# ── 2. 有效版本资格（未知/不兼容 → diagnostic）───────────────────────

def test_unsupported_policy_version_stays_diagnostic(tmp_path, monkeypatch):
    """X2 版本负例：policy_version=UNSUPPORTED_FUTURE_VERSION（其余齐备）→
    eligible=False + 机器可读 drop_reasons（版本字符串存在≠兼容）。"""
    _isolate(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(
        tmp_path, policy_version="UNSUPPORTED_FUTURE_VERSION")
    rec = _capture(tmp_path, plans, asm_store)
    assert rec.mid_effective is not True, \
        f"未知生成版本不得进有效分母: {rec.mid_effective} {rec.v6_drop_reasons}"
    assert any("unsupported_policy_version" in d for d in rec.v6_drop_reasons), \
        f"必须给机器可读版本资格原因: {rec.v6_drop_reasons}"
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl", days=7)
    assert report["cur_mid_effective"] == 0, "当期分母不计未知版本"


def test_supported_combo_stays_eligible(tmp_path, monkeypatch):
    """已验证组合正例：RESEARCH_SERVICE_VERSION × ASSERTION_METHOD_VERSION ×
    当前决策表 → 仍 eligible（版本门不误伤当期正例）。"""
    _isolate(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    rec = _capture(tmp_path, plans, asm_store)
    assert rec.mid_binding["eligible"] is True, \
        f"当期已验证组合必须仍合格: {rec.v6_drop_reasons}"
    mb = rec.mid_binding
    assert mb["policy_version"] == RESEARCH_SERVICE_VERSION
    assert mb["method_version"] == ASSERTION_METHOD_VERSION
    assert mb["decision_rule_version"] == f"{POLICY_ID_MID}@{DECISION_TABLE_VERSION}"


# ── 3. 行5/行6 规范（决策表 v3）─────────────────────────────────────

def _hf(**kw):
    base = dict(research_status=None, thesis_status=ThesisStatus.VALID,
                hard_exit_triggered=False,
                hard_exit_detail="", technical_exit_triggered=False,
                entry_condition_met=False, budget_available=None)
    base.update(kw)
    if base["research_status"] is None:
        base["research_status"] = __import__(
            "src.core.decision_contract", fromlist=["ResearchStatus"]).ResearchStatus.COMPLETE
    return HorizonFacts(**base)


def _plan():
    return HorizonPlan(plan_id="p_n1", horizon=__import__(
        "src.core.decision_contract", fromlist=["Horizon"]).Horizon.MID,
        policy_id=POLICY_ID_MID, intent="N1", accepted_at="2026-09-01T00:00:00")


def test_row5_unknown_presence_tech_exit_reviews():
    """X2-P2：UNKNOWN（持仓未知）+ MID 技术退出 → REVIEW（不给无规模 REDUCE）。"""
    pkt = evaluate_horizon(_plan(), _hf(technical_exit_triggered=True), "600519",
                           confirmed_ratio=None, position_state="UNKNOWN")
    assert pkt.desired_action is DesiredAction.REVIEW, \
        f"UNKNOWN 技术减仓必须转复核: {pkt.desired_action}"


def test_row5_held_tech_exit_still_reduces():
    """HELD（已知/未知权重）+ 技术退出 → REDUCE 保留（行5 既有语义不回退）。"""
    pkt = evaluate_horizon(_plan(), _hf(technical_exit_triggered=True), "600519",
                           confirmed_ratio=None, position_state="HELD")
    assert pkt.desired_action is DesiredAction.REDUCE
    pkt2 = evaluate_horizon(_plan(), _hf(technical_exit_triggered=True), "600519",
                            confirmed_ratio=0.2, position_state="HELD")
    assert pkt2.desired_action is DesiredAction.REDUCE


def test_row6_held_unknown_weight_budget_true_holds():
    """X2-P2：HELD+未知权重 即使 budget=True 也 HOLD（预算已知不掩盖权重缺口）。"""
    pkt = evaluate_horizon(_plan(), _hf(entry_condition_met=True,
                                        budget_available=True), "600519",
                           confirmed_ratio=None, position_state="HELD")
    assert pkt.desired_action is DesiredAction.HOLD, \
        f"未知权重冻结新增（预算已知不弥补）: {pkt.desired_action}"
    assert pkt.target_weight is None


def test_row6_held_known_weight_budget_true_adds():
    """HELD+已知权重+budget=True → ADD 既有语义不变（不伤已跑通路径）。"""
    pkt = evaluate_horizon(_plan(), _hf(entry_condition_met=True,
                                        budget_available=True), "600519",
                           confirmed_ratio=0.2, position_state="HELD")
    assert pkt.desired_action is DesiredAction.ADD


def test_no_position_state_migration_is_explicit():
    """不传三态路径：行1/行3 旧语义保留；行5 confirmed=None 推导 UNKNOWN →
    REVIEW（明确迁移——v3 受影响行登记）。"""
    from src.core.decision_contract import ResearchStatus
    pkt = evaluate_horizon(_plan(), _hf(hard_exit_triggered=True), "600519",
                           confirmed_ratio=0.2)
    assert pkt.desired_action is DesiredAction.EXIT
    pkt2 = evaluate_horizon(_plan(), _hf(technical_exit_triggered=True), "600519",
                            confirmed_ratio=None)
    assert pkt2.desired_action is DesiredAction.REVIEW, \
        "无三态推导 UNKNOWN 的技术减仓随 v3 迁移为 REVIEW（旧行为 REDUCE）"


def test_decision_table_version_is_v4():
    assert DECISION_TABLE_VERSION == "v4"
    assert SHADOW_DERIVATION_VERSION == "shadow_v11"


# ── 4. 旧桶独立计数 + v9 分桶 + 去重回归 ─────────────────────────────

def test_old_bucket_counts_mid_and_long_independently(tmp_path, monkeypatch):
    """X2-P2：旧记录 MID/LONG 均有效 → 两列各计 1（不再 if/elif 只计 MID）。"""
    _isolate(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    store = tmp_path / "shadow.jsonl"
    old = {"security_id": "600519",
           "as_of": (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds"),
           "legacy_action": "HOLD_POSITION", "legacy_desired": "HOLD",
           "fusion_mid_action": "HOLD", "fusion_long_action": "HOLD",
           "derivation_version": "shadow_v8", "observation_kind": "effective",
           "mid_effective": True, "long_effective": True,
           "input_fingerprint": "v8_old", "output_fingerprint": "v8_old_out",
           "delta_reasons": ["agree"]}
    store.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    report = build_shadow_report(store_path=store, days=7)
    bucket = report["older_versions"].get("shadow_v8")
    assert bucket, f"v8 进 older_versions: {report['older_versions']}"
    assert bucket["mid_effective"] == 1 and bucket["long_effective"] == 1, \
        f"旧桶 MID/LONG 独立计数: {bucket}"


def test_v9_records_and_old_protocols_bucketed(tmp_path, monkeypatch):
    """v9 当期计分母；v8/v7 及更早只诊断不追认（M0/M1 旧记录原件保留）。"""
    _isolate(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    store = tmp_path / "shadow.jsonl"
    old_v7 = {"security_id": "600519",
              "as_of": (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="seconds"),
              "legacy_action": "CLOSE_ALL", "legacy_desired": "EXIT",
              "fusion_mid_action": "EXIT", "fusion_long_action": "REVIEW",
              "derivation_version": "shadow_v7", "observation_kind": "effective",
              "mid_effective": True, "long_effective": False,
              "input_fingerprint": "v7_old", "output_fingerprint": "v7_out",
              "delta_reasons": ["agree"]}
    store.write_text(json.dumps(old_v7, ensure_ascii=False) + "\n", encoding="utf-8")
    rec = _capture(tmp_path, plans, asm_store)
    assert rec.derivation_version == "shadow_v11"
    assert rec.append_status == "saved", "跨协议不互判重复"
    report = build_shadow_report(store_path=store, days=7)
    assert report["protocol_version"] == "shadow_v11"
    assert report["cur_mid_effective"] == 1
    assert report["legacy_records"] == 1
    assert report["older_versions"]["shadow_v7"]["records"] == 1


def test_fetch_time_change_still_deduped_under_v9(tmp_path, monkeypatch):
    """M1 语义在新协议保持：纯采集时间变化折叠、行情真实变化留痕。"""
    _isolate(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    rec1 = _capture(tmp_path, plans, asm_store)
    assert rec1.append_status == "saved"
    rec2 = _capture(tmp_path, plans, asm_store)
    assert rec2.append_status == "deduped", "同语义重复触发折叠（M1 回归）"
    # 行情真实变化：quote_cutoff 变 → 留痕
    from src.core.analysis_service import build_decision_packet as bdp
    packet2 = bdp(_dr(), _sd(), _eval(False), confirmed_ratio=0.1, source="test")
    rec3 = capture_shadow(_dr(), _sd(), _eval(False), _pos(),
                          packet=packet2, source="test",
                          config={"fusion": {"mode": "capture_only"}},
                          store_path=tmp_path / "shadow.jsonl",
                          plans_store=plans, assessment_store=asm_store,
                          account_version="v_n1", quote_as_of="2026-10-01",
                          quote_fetched_at="2026-10-01T15:00:00+08:00",
                          account_context=None)
    assert rec3.append_status == "saved", "行情真实变化必须留痕"
