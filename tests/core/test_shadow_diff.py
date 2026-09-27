"""影子差异捕获回归测试（plan/fusion 影子阶段前置，src/core/shadow_diff.py）

锁死语义：
1. facts 映射 v1：hard_exit（fundamental_alert/top_signal）/技术退出/入场条件/
   research_status 透传；thesis 恒 UNESTABLISHED（不拿评分冒充逻辑）
2. 行1 硬退出先于激活门：legacy 被 PlanGuard 压制成 HOLD 时 fusion 双周期仍 EXIT
   ——EXIT 意图不被影子流程吞掉（G02 对照观察的本体）
3. 不把 legacy mode 映射成 horizon：每个持仓同时评估 MID 与 LONG（policy_id 断言）
4. 记录 schema：derivation_version/shadow_disclosure/分周期 reason 全落
5. 存储：追加式 JSONL，同股同分钟幂等；坏行隔离不崩
6. 开关：fusion.shadow_capture=false / 读取失败 / 无持仓 → 零写入
7. 报告：分原因聚合 + 差异率，无总收益比较（DESIGN 硬要求）

纯内存+临时文件测试，无网络。跑法：pytest tests/core/test_shadow_diff.py -q
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.analysis_service import build_decision_packet
from src.core.execution_layer import ExecutionEvaluation
from src.core.research_service import ASSERTION_METHOD_VERSION
from src.core.shadow_diff import (
    REASON_AGREE,
    REASON_FACTS_UNVERIFIED,
    REASON_HARD_EXIT,
    REASON_RESEARCH,
    REASON_THESIS,
    SHADOW_DERIVATION_VERSION,
    ShadowDiffRecord,
    _append_record,
    build_shadow_report,
    capture_shadow,
    render_shadow_report,
)
from src.data.models import (
    PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
)

_ON = {"fusion": {"shadow_capture": True}}
_OFF = {"fusion": {"shadow_capture": False}}


@pytest.fixture(autouse=True)
def _isolate_account_ledger(tmp_path, monkeypatch):
    """J5 审查 P2-3：隔离账户账本路径——capture_shadow 的缺省读取绝不触真实 HOME。"""
    import src.data.account_service as _asvc
    monkeypatch.setattr(_asvc, "DEFAULT_LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    yield


def _dr(decision="HOLD", score=0.5, warnings=None):
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision), score=score,
        stock=SimpleNamespace(stock_code="601318"),
        warnings=warnings or [],
    )


def _sd(decision="HOLD", pos_action="HOLD_POSITION", ratio=0.0, sell_path=None):
    return StrategyDecision(
        decision=SignalType(decision),
        position_action=PositionAction(pos_action),
        position_ratio=ratio,
        sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"],
        divergence=None,
        new_state=StrategyState(),
    )


def _eval(blocked=False) -> ExecutionEvaluation:
    return ExecutionEvaluation(
        original_action=PositionAction.HOLD_POSITION,
        effective_action=PositionAction.HOLD_POSITION,
        blocked=blocked, block_reason="跌停" if blocked else "",
        slippage_pct=0.0, impact_cost_pct=0.0, total_cost_pct=0.0,
    )


def _pos(code="601318", ratio=0.2):
    return SimpleNamespace(stock_code=code, current_ratio=ratio,
                           trade_plan=None, stock_name="测试股")


def _packet(dr=None, sd=None, ev=None, confirmed=0.2):
    return build_decision_packet(
        dr or _dr(), sd or _sd(), ev or _eval(),
        confirmed_ratio=confirmed, source="test")


_UNSET = object()


def _capture(tmp_path, sd=None, dr=None, pos=_UNSET, config=_ON, packet=None,
             plans_store=None, assessment_store=_UNSET):
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    store = plans_store if plans_store is not None else HorizonPlanStore(tmp_path / "plans.json")
    # J0b v4：评估存储默认隔离到 tmp（绝不读真实 HOME）
    asm_store = AssessmentStore(tmp_path / "research") if assessment_store is _UNSET \
        else assessment_store
    return capture_shadow(
        dr or _dr(), sd or _sd(), _eval(),
        _pos() if pos is _UNSET else pos,
        packet=packet or _packet(dr=dr, sd=sd),
        source="test", config=config,
        store_path=tmp_path / "shadow.jsonl",
        plans_store=store, assessment_store=asm_store)


# ── 1. facts 映射 ───────────────────────────────────────────

def test_hard_exit_maps_exit_in_both_horizons(tmp_path):
    """G02 对照本体：legacy 被压制说 HOLD，fusion 双周期仍 EXIT（行1 先于激活门）。"""
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="fundamental_alert"))
    assert rec.fusion_mid_action == "EXIT" and rec.fusion_long_action == "EXIT"
    assert rec.hard_exit is True
    assert REASON_HARD_EXIT in rec.delta_reasons
    assert "决策表行1" in rec.fusion_mid_reason


def test_top_signal_is_hard_exit(tmp_path):
    rec = _capture(tmp_path, sd=_sd("SELL", "CLOSE_ALL", sell_path="top_signal"))
    assert rec.hard_exit is True
    # legacy 已是退出族 → 无硬退出分歧；动作一致
    assert rec.fusion_mid_action == "EXIT"
    assert rec.delta_reasons == [REASON_AGREE]


def test_technical_exit_with_unestablished_thesis_gives_review(tmp_path):
    """trend_exit 属技术退出，但 v1 无投资逻辑 → 决策表落行10 REVIEW（不冒充 VALID）。"""
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="trend_exit"))
    assert rec.technical_exit is True
    assert rec.fusion_mid_action == "REVIEW"
    assert REASON_THESIS in rec.delta_reasons


def test_weak_sell_is_technical_exit_but_suppressed_legacy_holds(tmp_path):
    """weak_sell 被 PlanGuard 压制成 HOLD 的场景：影子记录 legacy HOLD vs fusion REVIEW
    （v1 无逻辑，不升级为 REDUCE——真 thesis 接入后此差异点变化即观察目标）。"""
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="weak_sell"))
    assert rec.technical_exit is True
    assert rec.legacy_desired == "HOLD"
    assert rec.fusion_mid_action == "REVIEW"


def test_flat_sell_not_technical_exit(tmp_path):
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="flat_sell"))
    assert rec.technical_exit is False


def test_entry_condition_from_open_add(tmp_path):
    rec = _capture(tmp_path, dr=_dr("BUY"), sd=_sd("BUY", "ADD", 0.3))
    # 行6 需 thesis VALID——v1 逻辑未立 → 落行10 REVIEW（不加仓）
    assert rec.fusion_mid_action == "REVIEW"
    assert REASON_THESIS in rec.delta_reasons
    assert rec.fusion_mid_reason  # reason 非空


def test_research_gap_reason(tmp_path):
    dr = _dr(warnings=["新闻获取失败"])
    rec = _capture(tmp_path, dr=dr)
    assert rec.research_status == "INCOMPLETE"
    assert REASON_RESEARCH in rec.delta_reasons


# ── 2. 记录 schema 与 horizon 正交 ──────────────────────────

def test_record_schema_and_version(tmp_path):
    rec = _capture(tmp_path)
    assert rec.derivation_version == SHADOW_DERIVATION_VERSION
    assert rec.shadow_disclosure == "shadow 模拟计划（非用户确认，仅对照观察）"
    assert rec.mid_thesis_status == "UNESTABLISHED" and rec.long_thesis_status == "UNESTABLISHED"
    # v3：来源逐周期标注（simulated 显式落字段）
    assert rec.mid_plan_source == "simulated" and rec.long_plan_source == "simulated"
    assert rec.mid_thesis_status == "UNESTABLISHED" and rec.long_thesis_status == "UNESTABLISHED"
    assert rec.legacy_action == "HOLD_POSITION"
    assert rec.legacy_desired == "HOLD"
    assert rec.fusion_mid_reason and rec.fusion_long_reason


def test_both_horizons_evaluated_policy_ids(tmp_path):
    """不把 mode 映射成 horizon：MID 与 LONG 各出一包（policy_id 可分）。"""
    pos = _pos()
    pos.trade_plan = SimpleNamespace(mode="qizong", description="气宗持有计划")
    rec = _capture(tmp_path, pos=pos)
    assert rec.fusion_mid_action and rec.fusion_long_action
    from src.core.decision_policy import POLICY_ID_LONG, POLICY_ID_MID, HorizonFacts, evaluate_horizon
    from src.core.decision_contract import Horizon
    from src.core.shadow_diff import _derive_shadow_plan
    from datetime import datetime
    plan = _derive_shadow_plan(Horizon.LONG, "601318", datetime.now().astimezone(), trade_plan=pos.trade_plan)
    assert plan.policy_id == POLICY_ID_LONG
    assert plan.legacy_mode == "qizong"  # 仅对照字段
    assert plan.plan_id.startswith("shadow_601318_long")
    assert plan.activated  # 模拟激活（披露字段锁在 schema）
    mid_plan = _derive_shadow_plan(Horizon.MID, "601318", datetime.now().astimezone())
    assert mid_plan.policy_id == POLICY_ID_MID


# ── 3. 存储与幂等 ───────────────────────────────────────────

def test_jsonl_append_and_idempotent_same_minute(tmp_path):
    """K0c/A3/A4 去重合同（旧口径「同股同分钟即重复」被 A3 反例证伪）：
    只折叠 同输入+同输出 的重复触发；同分钟输入或输出任一变化必须留痕。"""
    store = tmp_path / "shadow.jsonl"
    rec = _capture(tmp_path)
    lines = store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["security_id"] == "601318"
    assert saved["derivation_version"] == SHADOW_DERIVATION_VERSION
    assert saved["output_fingerprint"], "输出指纹必须落盘（A4）"
    assert rec.append_status == "saved", "落盘回执可见（K0c-3）"
    # 同股同分钟 + 同输入+同输出 → 幂等不追加
    dup = ShadowDiffRecord(security_id="601318", as_of=rec.as_of,
                           legacy_action="HOLD_POSITION", legacy_desired="HOLD",
                           fusion_mid_action="REVIEW", fusion_long_action="REVIEW",
                           input_fingerprint=rec.input_fingerprint,
                           output_fingerprint=rec.output_fingerprint)
    assert _append_record(store, dup) is False
    assert dup.append_status == "deduped"
    assert len(store.read_text(encoding="utf-8").strip().splitlines()) == 1
    # 同分钟但输入指纹不同（计划/账户版本变更）→ 留痕（A3）
    changed = ShadowDiffRecord(security_id="601318", as_of=rec.as_of,
                               legacy_action="HOLD_POSITION", legacy_desired="HOLD",
                               fusion_mid_action="REVIEW", fusion_long_action="REVIEW",
                               input_fingerprint="fp_other")
    assert _append_record(store, changed) is True, "同分钟输入变化必须留痕（A3）"
    assert len(store.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_bad_jsonl_line_isolated(tmp_path):
    store = tmp_path / "shadow.jsonl"
    store.write_text("{broken json\n", encoding="utf-8")
    _capture(tmp_path)
    lines = store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # 坏行保留（G14：不丢弃），新记录照写


# ── 4. 开关与边界 ───────────────────────────────────────────

def test_switch_off_no_write(tmp_path):
    rec = _capture(tmp_path, config=_OFF)
    assert rec is None
    assert not (tmp_path / "shadow.jsonl").exists()


def test_non_holding_skipped(tmp_path):
    rec = _capture(tmp_path, pos=None)
    assert rec is None
    assert not (tmp_path / "shadow.jsonl").exists()


def test_switch_read_failure_disables_capture(tmp_path, monkeypatch):
    import src.cli.main as cli_main
    def _boom():
        raise RuntimeError("config broken")
    monkeypatch.setattr(cli_main, "load_config", _boom)
    rec = capture_shadow(_dr(), _sd(), _eval(), _pos(), packet=_packet(),
                         source="test", config=None,
                         store_path=tmp_path / "shadow.jsonl")
    assert rec is None
    assert not (tmp_path / "shadow.jsonl").exists()


def test_capture_returns_typed_record(tmp_path):
    rec = _capture(tmp_path)
    assert isinstance(rec, ShadowDiffRecord)
    assert rec.source == "test"


# ── 5. 报告聚合 ────────────────────────────────────────────

def test_report_aggregates_reasons_no_returns(tmp_path):
    store = tmp_path / "shadow.jsonl"
    rec = _capture(tmp_path)
    report = build_shadow_report(store_path=store)
    assert report["total"] == 1 and report["stocks"] == 1
    assert report["diff_rate"] == 1.0  # thesis 未立 → 非 agree
    assert REASON_THESIS in report["by_reason"]
    text = render_shadow_report(report)
    assert "影子对照报告" in text
    assert "模拟计划" in text  # 披露前置
    assert "收益" not in text  # 报告不比较总收益（DESIGN 硬要求）
    assert "需人工复核" in text  # ISS-106：动作/原因中文化


def test_report_empty_store(tmp_path):
    report = build_shadow_report(store_path=tmp_path / "none.jsonl")
    assert report["total"] == 0
    text = render_shadow_report(report)
    assert "无影子记录" in text
    assert "这是干什么的" in text  # ISS-106：定位说明前置
    assert "一个字都不会变" in text  # 不改主结论的人话声明


def test_report_skips_old_records(tmp_path):
    """超出 days 窗口的记录不进聚合（窗口由查询侧控制）。"""
    store = tmp_path / "shadow.jsonl"
    from datetime import datetime, timedelta
    old = ShadowDiffRecord(
        security_id="000001", as_of=(datetime.now() - timedelta(days=30)).isoformat(),
        legacy_action="HOLD_POSITION", legacy_desired="HOLD",
        fusion_mid_action="REVIEW", fusion_long_action="REVIEW",
        delta_reasons=[REASON_THESIS])
    _append_record(store, old)
    _capture(tmp_path)
    report = build_shadow_report(store_path=store, days=7)
    assert report["total"] == 1


# ── v2：用户 plan2 计划消费（真判断前提）────────────────────

def _mk_plan(tmp_path, code="601318", horizon="MID", facts=None, accepted=True):
    from src.core.decision_contract import Horizon
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    from datetime import datetime
    plan = HorizonPlan(
        plan_id=f"p2_{code}_t", security_id=code,
        accepted_at=(datetime.now().isoformat(timespec="seconds") if accepted else None),
        horizon=Horizon[horizon],
        policy_id=POLICY_ID_MID if horizon == "MID" else plan_policy_long(),
        intent="锂电需求回暖驱动盈利兑现",
        facts_observed=facts or [],
    )
    return plan


def plan_policy_long():
    from src.core.decision_policy import POLICY_ID_LONG
    return POLICY_ID_LONG


def _mk_assessed_plan(tmp_path, asm_store, code="601318", horizon="MID", facts=None,
                      accepted=True, status_value="VALID"):
    """带评估唯一真值的用户计划（J0b v4 合法真判断路径）：
    计划盖 assessment_id + snapshot_id，评估落 AssessmentStore（caller 负责存计划）。"""
    from datetime import datetime
    from src.core.decision_contract import ThesisStatus
    from src.core.research import ThesisAssessment
    plan = _mk_plan(tmp_path, code=code, horizon=horizon, facts=facts, accepted=accepted)
    asm = ThesisAssessment(
        thesis_id=f"thesis_{code}_{horizon}", security_id=code, horizon=horizon,
        snapshot_id="snap-test", status=ThesisStatus[status_value],
        # K0b：用当前方法版本常量（测试模拟「现行方法评估」——硬编码字符串会随版本升位过期）
        method_version=ASSERTION_METHOD_VERSION,
        evaluated_as_of=datetime.now().astimezone())
    plan.assessment_id = asm_store.save(asm)
    plan.snapshot_id = asm.snapshot_id
    return plan, asm


def _save_accepted(store, plan, horizon="MID"):
    """K0c-5：真实接受流（save → accept 生成 accepted_refs 绑定）——直接写
    accepted_at 的捷径在精确接受引用核对下不再算已接受。返回接受后版本。"""
    store.save(plan)
    ok, msg = store.accept(plan.security_id, horizon)
    assert ok, msg
    return store.get(plan.security_id, horizon)


def test_accepted_plan_plain_facts_no_longer_valid(tmp_path):
    """R0 资格止血（A03）：已激活计划+纯文本事实（无证据引用）→ thesis UNESTABLISHED
    → 行10 REVIEW——「一条 facts 文本即 VALID」不再成立。"""
    from src.data.horizon_plans import HorizonPlanStore
    store = HorizonPlanStore(tmp_path / "plans.json")
    _save_accepted(store, _mk_plan(tmp_path, facts=["6月订单环比+30%"]))
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="trend_exit"),
                   plans_store=store)
    assert rec.mid_plan_source == "user_plan_accepted"
    assert rec.mid_plan_source == "user_plan_accepted"
    assert rec.mid_thesis_status == "UNESTABLISHED"
    assert rec.fusion_mid_action == "REVIEW"  # 行10（原 v2 误升级行5 REDUCE）
    assert REASON_FACTS_UNVERIFIED in rec.delta_reasons
    text = render_shadow_report(build_shadow_report(store_path=tmp_path / "shadow.jsonl"))
    assert "事实未挂证据引用" in text  # R0 验收3：具体原因，不是笼统错误


def test_accepted_plan_with_assessment_gives_real_verdict(tmp_path):
    """J0b v4 合法真判断路径：计划带 assessment_id 且评估已落 AssessmentStore
    → shadow 消费同一 status（MID VALID → 技术退出落行5 REDUCE）。"""
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    asm_store = AssessmentStore(tmp_path / "research")
    store = HorizonPlanStore(tmp_path / "plans.json")
    plan, _asm = _mk_assessed_plan(tmp_path, asm_store, facts=["6月订单环比+30%"])
    _save_accepted(store, plan)
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="trend_exit"),
                   plans_store=store, assessment_store=asm_store)
    assert rec.mid_thesis_status == "VALID"
    assert rec.fusion_mid_action == "REDUCE"  # 行5：MID VALID+技术退出 → REDUCE
    # 仅 MID 有计划（LONG 模拟）→ 混合来源披露逐周期明示，不再整行盖「真判断」
    assert "MID=用户已确认计划——真判断" in rec.shadow_disclosure
    assert "LONG=模拟计划" in rec.shadow_disclosure
    assert "决策表行5" in rec.fusion_mid_reason


def test_draft_plan_stays_review(tmp_path):
    """草稿计划（未激活）→ 行0 激活门只产出 REVIEW（激活语义不被影子绕过）。"""
    from src.data.horizon_plans import HorizonPlanStore
    store = HorizonPlanStore(tmp_path / "plans.json")
    store.save(_mk_plan(tmp_path, facts=["6月订单环比+30%"], accepted=False))
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="trend_exit"),
                   plans_store=store)
    assert rec.mid_plan_source == "user_plan_draft"
    assert rec.mid_plan_source == "user_plan_draft"
    assert rec.fusion_mid_action == "REVIEW" and rec.fusion_long_action == "REVIEW"
    # 仅 MID 草稿（LONG 模拟）→ 混合披露逐周期明示
    assert "MID=草稿计划未激活" in rec.shadow_disclosure
    assert "LONG=模拟计划" in rec.shadow_disclosure


def test_hard_exit_still_wins_with_user_plan(tmp_path):
    """有用户计划时行1 硬退出仍先于一切（G02 对照不被官僚流程掩盖）。"""
    from src.data.horizon_plans import HorizonPlanStore
    store = HorizonPlanStore(tmp_path / "plans.json")
    store.save(_mk_plan(tmp_path, facts=["6月订单环比+30%"]))
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="fundamental_alert"),
                   plans_store=store)
    assert rec.fusion_mid_action == "EXIT" and rec.fusion_long_action == "EXIT"
    assert REASON_HARD_EXIT in rec.delta_reasons


def test_accepted_plan_long_side_stays_simulated_and_unestablished(tmp_path):
    """R0 验收2：同股仅 MID 接受时——LONG 影子明确模拟（long_plan_source=simulated、
    thesis UNESTABLISHED），不计真计划样本；MID 真判断不受累（J0b v4：MID VALID
    经 AssessmentStore 合法路径）。"""
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    asm_store = AssessmentStore(tmp_path / "research")
    store = HorizonPlanStore(tmp_path / "plans.json")
    plan, _asm = _mk_assessed_plan(tmp_path, asm_store, horizon="MID",
                                   facts=["6月订单环比+30%"])
    _save_accepted(store, plan)
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="trend_exit"),
                   plans_store=store, assessment_store=asm_store)
    assert rec.mid_plan_source == "user_plan_accepted" and rec.mid_thesis_status == "VALID"
    assert rec.long_plan_source == "simulated" and rec.long_thesis_status == "UNESTABLISHED"
    assert rec.fusion_mid_action == "REDUCE"   # MID 真判断（行5）
    assert rec.fusion_long_action == "REVIEW"  # LONG 模拟 → 行10
    assert REASON_THESIS in rec.delta_reasons  # LONG 模拟侧如实打「逻辑未立」


def test_report_counts_plan_source_per_horizon(tmp_path):
    """报告按周期计数来源：仅 MID 接受时 LONG 计入模拟（不再整行聚合——R0 验收2）。"""
    from src.data.horizon_plans import HorizonPlanStore
    store = HorizonPlanStore(tmp_path / "plans.json")
    plan = _mk_plan(tmp_path, horizon="MID", facts=["6月订单环比+30%"])
    plan.fact_evidence_refs = {"6月订单环比+30%": ["cninfo://ann/123"]}
    _save_accepted(store, plan)
    _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="trend_exit"),
             plans_store=store)
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl")
    text = render_shadow_report(report)
    assert "真计划对照 MID 1 条" in text and "LONG 0 条" in text
    assert "模拟计划 MID 0 条｜LONG 1 条" in text


# ── R9：fusion.mode 单一解析器 ─────────────────────────────

def test_fusion_mode_resolver_migration_and_gates():
    """旧开关迁移不并存两套语义；未知 mode 回退；J4：requested≠effective——
    opt_in/default 意愿登记不等于生效，消费者只据 effective。"""
    from src.core.shadow_diff import resolve_fusion_mode, resolve_fusion_mode_full
    assert resolve_fusion_mode({"fusion": {"shadow_capture": True}}) == \
        ("capture_only", "经旧开关 fusion.shadow_capture 迁移")
    assert resolve_fusion_mode({"fusion": {"shadow_capture": False}})[0] == "legacy_only"
    assert resolve_fusion_mode({"fusion": {"mode": "legacy_only"}})[1] == ""
    # J4：opt_in = 意愿登记——effective 一律 capture_only + blocking_gates 明示
    full = resolve_fusion_mode_full({"fusion": {"mode": "opt_in"}})
    assert full.requested_mode == "opt_in" and full.effective_mode == "capture_only"
    assert full.blocking_gates, "未达发布门必须明示"
    mode, note = resolve_fusion_mode({"fusion": {"mode": "opt_in"}})
    assert mode == "capture_only" and "未达发布门" in note
    mode2, note2 = resolve_fusion_mode({"fusion": {"mode": "bogus"}})
    assert mode2 == "capture_only" and "回退" in note2
    assert resolve_fusion_mode(None)[0] == "capture_only"
