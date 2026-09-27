"""K1 shadow_v6 观察协议回归测试（plan/fusion iteration4，DELIVERY_PLAN K1）

锁死语义（架构师合同——冻结=记录可比较，不等于策略效果或发布门通过）：
1. shadow_v6 记录按 **MID/LONG 分别**登记完整绑定（plan_id/revision/content_hash/
   accepted_ref/assessment_id/status/policy_id/method_version/target_weight/blockers）
   ——字段缺失按 diagnostic，机器可读 drop_reasons
2. 分别判资格、分别计分母：一周期合格不把另一周期算入
3. 版本化读适配：旧 v5/v5_k0c 记录原样保留，只作诊断，不进 v6 有效分母
4. CLI shadow 报告显示 v6 有效/诊断数及阻塞原因

全部路径构造前断言临时根；零网络零 AI。
跑法：pytest tests/core/test_k1_shadow_v6.py -q
"""
import json
import os
import tempfile
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.analysis_service import build_decision_packet
from src.core.shadow_diff import (
    SHADOW_DERIVATION_VERSION,
    build_shadow_report,
    capture_shadow,
)
from src.data.horizon_plans import HorizonPlanStore
from src.data.models import (
    PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
)
from src.data.research_store import AssessmentStore

AS_OF = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


def _sd(decision="HOLD", pos_action="HOLD_POSITION", ratio=0.1, sell_path=None):
    return StrategyDecision(
        decision=SignalType(decision), position_action=PositionAction(pos_action),
        position_ratio=ratio, sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"], divergence=None, new_state=StrategyState())


def _pos():
    return SimpleNamespace(stock_code="600519", stock_name="测试股",
                           current_ratio=0.1, trade_plan=None)


def _isolation_assert(tmp_path):
    """构造前：tmp_path 自身健全性预断言（K1 审查 P3——防恒真安慰断言）。"""
    root = str(tmp_path.resolve())
    assert "pytest" in root or tempfile.gettempdir().lower() in root.lower(), \
        f"tmp_path 不在系统临时目录（隔离可疑）: {root}"


def _assert_files_in_tmp(tmp_path):
    """测试尾部：实际落盘的文件全部在临时根内。"""
    root = str(tmp_path.resolve())
    for rel in ("plans.json", "shadow.jsonl"):
        f = tmp_path / rel
        if f.exists():
            assert os.path.abspath(str(f)).startswith(root), f"越出临时根: {f}"
    rd = tmp_path / "research"
    if rd.exists():
        assert os.path.abspath(str(rd)).startswith(root)


def _seed_accepted_plan(tmp_path, plans, asm_store, code="600519"):
    """已接受+评估齐的计划（v6 合格绑定的输入）。"""
    from src.core.decision_contract import ThesisStatus
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    from src.core.research import ThesisAssessment
    from src.core.research_service import ASSERTION_METHOD_VERSION
    plan = HorizonPlan(plan_id=f"p2_{code}_v6", security_id=code,
                       horizon="MID", policy_id=POLICY_ID_MID,
                       intent="锂电需求回暖驱动盈利兑现",
                       facts_observed=["6月订单环比+30%"])
    asm = ThesisAssessment(thesis_id=f"thesis_{code}_MID", security_id=code,
                           horizon="MID", snapshot_id="snap-v6",
                           status=ThesisStatus.VALID,
                           method_version=ASSERTION_METHOD_VERSION,
                           evaluated_as_of=datetime.now().astimezone())
    plan.assessment_id = asm_store.save(asm)
    plan.snapshot_id = asm.snapshot_id
    plans.save(plan)
    ok, msg = plans.accept(code, "MID")
    assert ok, msg
    return plans.get(code, "MID"), asm


def _capture(tmp_path, plans, asm_store, *, account_version="v_v6"):
    decision_result = SimpleNamespace(
        decision=SimpleNamespace(value="HOLD"), score=0.6,
        stock=SimpleNamespace(stock_code="600519", stock_name="测试股"), warnings=[])
    strategy_decision = _sd()
    execution_eval = SimpleNamespace(effective_action=SimpleNamespace(value="HOLD"),
                                     blocked=False)
    packet = build_decision_packet(decision_result, strategy_decision, execution_eval,
                                   confirmed_ratio=0.1, source="test")
    return capture_shadow(decision_result, strategy_decision, execution_eval, _pos(),
                          packet=packet, source="test",
                          config={"fusion": {"mode": "opt_in", "shadow_capture": True}},
                          store_path=tmp_path / "shadow.jsonl",
                          plans_store=plans, assessment_store=asm_store,
                          account_version=account_version)


# ── 1. v6 记录：MID/LONG 分别登记完整绑定 ──────────────────

def test_k1_v6_record_registers_per_horizon_bindings(tmp_path):
    """v6 记录：MID 绑定齐全（accepted_ref/assessment/policy/method/target/blockers）；
    LONG 无计划 → LONG binding 缺席且 drop_reasons 机器可读。"""
    _isolation_assert(tmp_path)
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, asm_store)
    assert rec.derivation_version == "shadow_v6"
    assert rec.mid_binding, "MID 已接受计划必须登记完整绑定"
    mb = rec.mid_binding
    for key in ("plan_id", "plan_revision", "content_hash", "accepted_ref",
                "assessment_id", "thesis_status", "policy_id", "method_version"):
        assert mb.get(key), f"MID 绑定缺 {key}: {mb}"
    assert rec.long_binding is None, "LONG 无计划 → 无绑定"
    assert any("long" in r.lower() for r in rec.v6_drop_reasons), \
        f"LONG 缺席原因必须机器可读: {rec.v6_drop_reasons}"
    # 分别判资格：MID 合格、LONG 不合格——互不搭车
    assert rec.mid_binding.get("eligible") is True
    assert rec.long_effective is False
    _assert_files_in_tmp(tmp_path)


def test_k1_v6_missing_account_version_diagnostic(tmp_path):
    """账户版本缺失 → 两周期都不可 effective（drop_reasons 说明）——不冒充合格。"""
    _isolation_assert(tmp_path)
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec = _capture(tmp_path, plans, asm_store, account_version="")
    assert rec.mid_effective is False, "缺账户版本不得判 effective"
    assert any("account" in r.lower() or "账户" in r for r in rec.v6_drop_reasons), \
        f"阻塞原因机器可读: {rec.v6_drop_reasons}"


# ── 2. 版本化读适配：旧记录只诊断 ──────────────────────────

def test_k1_v6_report_counts_only_v6_effective(tmp_path):
    """报告分母：旧 v5_k0c 记录不进 v6 有效分母（原样保留、只诊断）；
    v6 effective 按周期分别计数。"""
    _isolation_assert(tmp_path)
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    rec_v6 = _capture(tmp_path, plans, asm_store)
    assert rec_v6.derivation_version == "shadow_v6"
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl", days=7)
    assert report["effective_observations"] >= 1, "v6 有效观察计入"
    assert report.get("v6_mid_effective", 0) >= 1, "MID 分母单独计数"
    assert report.get("v6_long_effective", 0) == 0, "LONG 不搭 MID 的车（分别计分母）"
    assert report.get("legacy_records", 0) == 0, "无旧版本记录时 legacy 计数为 0"


def test_k1_v6_old_records_kept_diagnostic(tmp_path):
    """旧 v5_k0c 记录原样保留（不改写、不追认），报告标注 legacy 计数。
    K1 审查 P1-2 修复：降版本后**改变账户版本**使第二次 capture 不被去重——
    新旧记录真实并存，分别断言计数与逐字段原样。"""
    _isolation_assert(tmp_path)
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)
    _capture(tmp_path, plans, asm_store)
    store_path = tmp_path / "shadow.jsonl"
    lines = store_path.read_text(encoding="utf-8").splitlines()
    old = json.loads(lines[0])
    old_snapshot = {k: v for k, v in old.items()}
    # 模拟旧数据在账：版本号降回 v5_k0c
    old["derivation_version"] = "shadow_v5_k0c"
    lines[0] = json.dumps(old, ensure_ascii=False)
    store_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 新捕获：换账户版本 → 输入指纹变化 → 不被去重（与旧记录真实并存）
    rec_v6 = _capture(tmp_path, plans, asm_store, account_version="v_v6_next")
    assert rec_v6.derivation_version == "shadow_v6"
    assert rec_v6.append_status == "saved", "新旧记录必须并存（不被去重吞掉）"
    report = build_shadow_report(store_path=store_path, days=7)
    _assert_files_in_tmp(tmp_path)
    assert report.get("legacy_records") == 1, "旧记录原样保留并单独计数"
    assert report["v6_mid_effective"] == 1, "v6 MID 有效观察独立累计（不来自旧记录）"
    assert report["v6_long_effective"] == 0, "LONG 不搭车"
    # 旧记录逐字段原样（除我们模拟修改的版本号）
    lines_now = store_path.read_text(encoding="utf-8").splitlines()
    old_now = json.loads(lines_now[0])
    for k, v in old_snapshot.items():
        if k == "derivation_version":
            continue
        assert old_now.get(k) == v, f"旧记录字段 {k} 被改写"
    # 新记录排在旧记录之后（追加序）
    assert json.loads(lines_now[1])["derivation_version"] == "shadow_v6"


def test_k1_v6_dual_horizon_both_eligible_counted_separately(tmp_path):
    """K1 审查 P2-2：MID+LONG 双周期并存、双 binding 都 eligible → v6_mid 与
    v6_long 各计 1（per-horizon 独立判定的正路径回归保护）。"""
    _isolation_assert(tmp_path)
    from src.core.decision_contract import ThesisStatus
    from src.core.decision_policy import POLICY_ID_LONG, HorizonPlan
    from src.core.research import ThesisAssessment
    from src.core.research_service import ASSERTION_METHOD_VERSION
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    _seed_accepted_plan(tmp_path, plans, asm_store)  # MID
    plan_l = HorizonPlan(plan_id="p2_600519_long_v6", security_id="600519",
                         horizon="LONG", policy_id=POLICY_ID_LONG,
                         intent="长期经营优势观察",
                         facts_observed=["6月订单环比+30%"])
    asm_l = ThesisAssessment(thesis_id="thesis_600519_LONG", security_id="600519",
                             horizon="LONG", snapshot_id="snap-v6-long",
                             status=ThesisStatus.VALID,
                             method_version=ASSERTION_METHOD_VERSION,
                             evaluated_as_of=datetime.now().astimezone())
    plan_l.assessment_id = asm_store.save(asm_l)
    plan_l.snapshot_id = asm_l.snapshot_id
    plans.save(plan_l)
    assert plans.accept("600519", "LONG")[0]
    rec = _capture(tmp_path, plans, asm_store)
    assert rec.mid_effective is True and rec.long_effective is True, \
        "双周期各自独立判定（MID 合格不替代 LONG 的独立核验）"
    assert rec.mid_binding["eligible"] and rec.long_binding["eligible"]
    assert rec.long_binding["plan_id"] == "p2_600519_long_v6", \
        "LONG binding 必须是 LONG 自己的计划（不误搭 MID 数据）"
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl", days=7)
    assert report["v6_mid_effective"] == 1 and report["v6_long_effective"] == 1, \
        "双周期分别计分母（各 1，不是合计 1）"
