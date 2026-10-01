"""L0 观察协议合同回归测试（plan/fusion iteration5 L0 基础上经 M1 升 v8；R11 V1/V2 反例）

锁死语义（同一资格函数决定字段缺口、diagnostic/effective、报告分母——当期协议 v8）：
1. V1：缺行情时点仍计有效 → 关闭——quote_cutoff 缺失/未来 → drop_reasons 机器可读、
   eligible=False、报告当期协议 MID 有效分母不计；证据截止来自被消费评估
   （不等于捕获墙钟）；decision_rule_version 来自真实规则版本常量
2. V2：目标权重/阻塞变化被去重吞掉 → 关闭——输出指纹消费完整两臂绑定语义；
   仅捕获时点变化 → 真实重复折叠（WRITE 回执与磁盘一致）
3. 缺任一必要字段（policy_version/行情/证据截止等）→ 降级 diagnostic
4. 旧协议记录（v6/v7 及更早）仅诊断不追认——现行协议分母只数当期（v8）
5. 两臂（legacy/fusion）输出完整：action/target/target_state/blockers/execution
6. UI 报告显示具体缺口（人话）与两周期分别计数

全部路径构造前断言临时根；零网络零 AI，不读写真实 HOME。
跑法：pytest tests/core/test_l0_shadow_contract.py -q
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.analysis_service import build_decision_packet
from src.core.research_service import ASSERTION_METHOD_VERSION
from src.core.shadow_diff import (
    SHADOW_DERIVATION_VERSION,
    ShadowDiffRecord,
    _append_record,
    _output_fingerprint,
    build_shadow_report,
    capture_shadow,
    render_shadow_report,
)
from src.data.models import (
    PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
)
from src.data.research_store import AssessmentStore

AS_OF = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
QUOTE_OK = "2026-09-29"          # 最近已收盘交易日（日精度——live 正常形态）
POLICY_VERSION = "r4.research_service_v1"


def _isolation_assert(tmp_path):
    """构造前：tmp_path 自身健全性预断言（K1 审查 P3 同款——防恒真安慰断言）。"""
    root = str(tmp_path.resolve())
    assert "pytest" in root or tempfile.gettempdir().lower() in root.lower(), \
        f"tmp_path 不在系统临时目录（隔离可疑）: {root}"


def _assert_files_in_tmp(tmp_path):
    """测试尾部：实际落盘的文件全部在临时根内（隔离断言全路径）。"""
    root = str(tmp_path.resolve())
    for rel in ("plans.json", "shadow.jsonl"):
        f = tmp_path / rel
        if f.exists():
            assert os.path.abspath(str(f)).startswith(root), f"越出临时根: {f}"
    rd = tmp_path / "research"
    if rd.exists():
        assert os.path.abspath(str(rd)).startswith(root)


@pytest.fixture(autouse=True)
def _isolate_account_ledger(tmp_path, monkeypatch):
    """capture_shadow 的缺省账本读取绝不触真实 HOME。"""
    import src.data.account_service as _asvc
    monkeypatch.setattr(_asvc, "DEFAULT_LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    yield


def _sd(decision="HOLD", pos_action="HOLD_POSITION", ratio=0.1, sell_path=None):
    return StrategyDecision(
        decision=SignalType(decision), position_action=PositionAction(pos_action),
        position_ratio=ratio, sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"], divergence=None, new_state=StrategyState())


def _dr():
    return SimpleNamespace(
        decision=SimpleNamespace(value="HOLD"), score=0.6,
        stock=SimpleNamespace(stock_code="600519", stock_name="测试股"), warnings=[])


def _eval():
    return SimpleNamespace(effective_action=SimpleNamespace(value="HOLD"), blocked=False)


def _pos():
    return SimpleNamespace(stock_code="600519", stock_name="测试股",
                           current_ratio=0.1, trade_plan=None)


def _seed_accepted_plan(tmp_path, plans, asm_store, code="600519",
                        policy_version=POLICY_VERSION):
    """已接受+评估齐+策略版本齐的计划（v7 合格绑定的输入）。"""
    from src.core.decision_contract import ThesisStatus
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    from src.core.research import ThesisAssessment
    plan = HorizonPlan(plan_id=f"p2_{code}_l0", security_id=code,
                       horizon="MID", policy_id=POLICY_ID_MID,
                       intent="锂电需求回暖驱动盈利兑现",
                       policy_version=policy_version,
                       facts_observed=["6月订单环比+30%"])
    asm = ThesisAssessment(thesis_id=f"thesis_{code}_MID", security_id=code,
                           horizon="MID", snapshot_id="snap-l0",
                           status=ThesisStatus.VALID,
                           method_version=ASSERTION_METHOD_VERSION,
                           # 评估先于捕获（真实时序）——也让「证据截止≠捕获墙钟」可确定性断言
                           evaluated_as_of=datetime.now().astimezone() - timedelta(minutes=1))
    plan.assessment_id = asm_store.save(asm)
    plan.snapshot_id = asm.snapshot_id
    plans.save(plan)
    ok, msg = plans.accept(code, "MID")
    assert ok, msg
    return plans.get(code, "MID"), asm


def _capture(tmp_path, plans, asm_store, *, account_version="v_l0",
             quote_as_of=QUOTE_OK, sd=None):
    from src.data.horizon_plans import HorizonPlanStore
    assert isinstance(plans, HorizonPlanStore)
    decision_result = _dr()
    strategy_decision = sd or _sd()
    execution_eval = _eval()
    packet = build_decision_packet(decision_result, strategy_decision, execution_eval,
                                   confirmed_ratio=0.1, source="test")
    return capture_shadow(decision_result, strategy_decision, execution_eval, _pos(),
                          packet=packet, source="test",
                          config={"fusion": {"mode": "capture_only"}},
                          store_path=tmp_path / "shadow.jsonl",
                          plans_store=plans, assessment_store=asm_store,
                          account_version=account_version,
                          quote_as_of=quote_as_of)


# ── V1：缺行情时点不进有效分母（红灯：现行代码 quote_cutoff=None 仍 eligible）──

def test_v1_missing_quote_stays_diagnostic(tmp_path):
    """缺行情时点 → drop_reasons 机器可读、eligible=False、报告当期 MID 有效数=0。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, asm_store, quote_as_of="")
    mb = rec.mid_binding
    assert mb is not None
    assert mb["quote_cutoff"] is None, "未提供行情时点必须如实缺省（不拿捕获墙钟充数）"
    assert not mb["eligible"], "缺行情时点不得判 eligible（V1 反例）"
    assert rec.mid_effective is False
    assert any("quote" in r for r in mb["drop_reasons"]), \
        f"缺行情时点原因必须机器可读: {mb['drop_reasons']}"
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl", days=7)
    assert report["cur_mid_effective"] == 0, "当期协议 MID 有效分母不计缺时点记录"
    _assert_files_in_tmp(tmp_path)


def test_v1_full_fields_eligible_and_real_sources(tmp_path):
    """行情/证据截止/规则版本齐 → eligible；证据截止=被消费评估时点（≠捕获墙钟）；
    decision_rule_version=真实规则版本（policy_id@表版本），不是 policy_id 复制品。"""
    _isolation_assert(tmp_path)
    from src.core.decision_policy import DECISION_TABLE_VERSION, POLICY_ID_MID
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _plan, asm = _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, asm_store)
    mb = rec.mid_binding
    assert mb["eligible"] is True and rec.mid_effective is True
    assert mb["quote_cutoff"] == QUOTE_OK
    assert mb["evidence_cutoff"] == asm.evaluated_as_of.isoformat(timespec="seconds"), \
        "证据截止必须来自被消费评估（非捕获墙钟 as_of）"
    assert mb["evidence_cutoff"] != rec.as_of
    assert mb["decision_rule_version"] == f"{POLICY_ID_MID}@{DECISION_TABLE_VERSION}"
    assert mb["policy_version"] == POLICY_VERSION
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl", days=7)
    assert report["cur_mid_effective"] == 1 and report["cur_long_effective"] == 0
    _assert_files_in_tmp(tmp_path)


def test_v1_future_quote_degrades(tmp_path):
    """行情时点在未来 → 降级 diagnostic（未来时点不可比较）。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    future = (datetime.now().astimezone() + timedelta(days=1)).isoformat(timespec="seconds")
    rec = _capture(tmp_path, plans, asm_store, quote_as_of=future)
    mb = rec.mid_binding
    assert not mb["eligible"] and rec.mid_effective is False
    assert any("quote_cutoff_future" in r for r in mb["drop_reasons"]), \
        f"未来行情时点原因必须机器可读: {mb['drop_reasons']}"
    _assert_files_in_tmp(tmp_path)


def test_missing_policy_version_degrades(tmp_path):
    """计划缺策略版本（生成方未登记）→ 降级 diagnostic——不为凑样本放过。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store, policy_version="")
    rec = _capture(tmp_path, plans, asm_store)
    mb = rec.mid_binding
    assert not mb["eligible"] and rec.mid_effective is False
    assert any("policy_version" in r for r in mb["drop_reasons"]), \
        f"缺策略版本原因必须机器可读: {mb['drop_reasons']}"
    _assert_files_in_tmp(tmp_path)


# ── V2：权重/阻塞变化去重不吞（红灯：现行指纹不含 binding 语义）──────────

def test_v2_target_weight_change_retained(tmp_path):
    """同输入同分钟，MID fusion 臂目标权重 0.1→0.2：输出指纹必须不同、第二条必须
    落盘（R11 V2 反例——目标变化明确不可丢）。变异真实字段 arms.fusion.target
    （监督审查 P2：不变异幽灵散键，防指纹排除项精确回归时测试网失效）。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, asm_store)
    first = rec.model_copy(deep=True)
    second = rec.model_copy(deep=True)
    first.mid_binding["arms"]["fusion"]["target"] = 0.1
    second.mid_binding["arms"]["fusion"]["target"] = 0.2
    first.output_fingerprint = _output_fingerprint(first)
    second.output_fingerprint = _output_fingerprint(second)
    assert first.output_fingerprint != second.output_fingerprint, \
        "目标权重变化必须改变输出指纹（V2 反例）"
    destination = tmp_path / "target_change.jsonl"
    assert _append_record(destination, first) is True
    assert _append_record(destination, second) is True, \
        "同输入不同目标必须留痕（不被去重吞掉）"
    _assert_files_in_tmp(tmp_path)


def test_v2_blocker_change_retained(tmp_path):
    """fusion 臂阻塞变化（现金未知→冻结新增）同样必须留痕。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, asm_store)
    first = rec.model_copy(deep=True)
    second = rec.model_copy(deep=True)
    first.mid_binding["arms"]["fusion"]["blockers"] = []
    second.mid_binding["arms"]["fusion"]["blockers"] = ["组合预算未知——不给精确目标"]
    first.output_fingerprint = _output_fingerprint(first)
    second.output_fingerprint = _output_fingerprint(second)
    assert first.output_fingerprint != second.output_fingerprint, \
        "阻塞变化必须改变输出指纹"
    _assert_files_in_tmp(tmp_path)


def test_capture_time_only_change_deduped(tmp_path):
    """真实重复折叠：仅捕获时点变化（同日跨分钟）→ 输出指纹相同 → 去重；
    回执与磁盘一致（WRITE 成功才回执 saved）。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, asm_store)
    assert rec.append_status == "saved"
    lines = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0]).get("output_fingerprint")
    later = rec.model_copy(deep=True)
    later.as_of = (datetime.fromisoformat(rec.as_of) + timedelta(minutes=5)).isoformat(timespec="seconds")
    assert _output_fingerprint(later) == rec.output_fingerprint, \
        "仅捕获时点变化不得改变输出指纹（真实重复才可折叠）"
    assert _append_record(tmp_path / "shadow.jsonl", later) is False
    assert later.append_status == "deduped"
    assert len((tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    _assert_files_in_tmp(tmp_path)


# ── 两臂输出完整性与旧协议隔离 ─────────────────────────────────────

def test_arms_complete_in_binding(tmp_path):
    """两臂（legacy/fusion）各含 action/target/target_state/blockers/execution；
    legacy 目标权重来自 legacy 决策（KNOWN）；fusion 无数值目标按动作族定状态。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, asm_store, sd=_sd(ratio=0.1))
    arms = rec.mid_binding["arms"]
    for arm in ("legacy", "fusion"):
        a = arms[arm]
        for key in ("action", "target", "target_state", "blockers", "execution"):
            assert key in a, f"{arm} 臂缺 {key}: {a}"
    assert arms["legacy"]["action"] == "HOLD"
    assert arms["legacy"]["target"] == 0.1 and arms["legacy"]["target_state"] == "KNOWN"
    assert arms["fusion"]["action"] == "HOLD"
    assert arms["fusion"]["target"] is None and arms["fusion"]["target_state"] == "UNKNOWN"
    _assert_files_in_tmp(tmp_path)


def test_v6_old_records_diagnostic_only(tmp_path):
    """旧 shadow_v6 记录（含被旧合同判 effective 的）不进当期分母：legacy_records
    单列、v6_* 按记录原样计数、effective_observations 只数当期协议。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    store = tmp_path / "shadow.jsonl"
    old_v6 = {
        "security_id": "600519",
        "as_of": (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds"),
        "legacy_action": "HOLD_POSITION", "legacy_desired": "HOLD",
        "fusion_mid_action": "HOLD", "fusion_long_action": "REVIEW",
        "derivation_version": "shadow_v6",
        "observation_kind": "effective",
        "mid_effective": True,
        "long_effective": False,
        "input_fingerprint": "v6_old_input",
        "output_fingerprint": "v6_old_output",
        "delta_reasons": ["agree"],
    }
    store.write_text(json.dumps(old_v6, ensure_ascii=False) + "\n", encoding="utf-8")
    rec = _capture(tmp_path, plans, asm_store, account_version="v_l0_next")
    assert rec.derivation_version == "shadow_v8"
    assert rec.append_status == "saved", "新旧协议记录必须并存（不被去重吞掉）"
    report = build_shadow_report(store_path=store, days=7)
    assert report["protocol_version"] == "shadow_v8"
    assert report["legacy_records"] == 1, "旧协议记录只诊断（单列计数）"
    assert report["cur_mid_effective"] == 1, "当期分母只数当期协议"
    assert report["older_versions"]["shadow_v6"]["mid_effective"] == 1, "v6 计数按记录原样（仅供过渡观察）"
    assert report["effective_observations"] == 1, "行级有效观察不追认旧协议记录"
    text = render_shadow_report(report)
    assert "只诊断不追认" in text
    _assert_files_in_tmp(tmp_path)


def test_report_shows_specific_gaps(tmp_path):
    """UI 显示具体缺口（人话）与两周期分别计数（L0 验收：不笼统报错）。"""
    _isolation_assert(tmp_path)
    from src.data.horizon_plans import HorizonPlanStore
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    _capture(tmp_path, plans, asm_store, quote_as_of="")
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl", days=7)
    text = render_shadow_report(report)
    assert "缺行情时点" in text, f"UI 必须显示具体缺口: {text}"
    assert "有效 MID 0" in text and "LONG 0" in text
    _assert_files_in_tmp(tmp_path)
