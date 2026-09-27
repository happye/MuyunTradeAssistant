"""F5 周期决策表与投资逻辑研究层回归测试（plan/fusion TASKS.md F5 验收条款）

锁死语义（DESIGN §4.1/§4.2，从上到下首个决定性条件优先）：
1. 同股票同证据不同周期结果可解释（MID/LONG 对同一事实组给不同且各有理由的行动）
2. 中期逻辑失效不能自动延长期限（invalidate TRUE → EXIT；UNKNOWN 不自动变 FALSE）
3. 长期短期技术噪声不触发未约定退出（行7：HOLD 留痕）
4. 长期缺财务不发质量合格结论（research INCOMPLETE → REVIEW，禁 OPEN/ADD）
5. 新计划需确认后才激活（accepted_at 空 → 只 REVIEW）
6. 未支持行业明确限制（NOT_APPLICABLE，不填通用数字）
7. 复核日只触发 REVIEW 不是强制卖；legacy 计划零写入继续读取（侧挂对象独立）
8. 策略ID fusion_mid_v1/fusion_long_v1 与旧 mode 正交

纯内存测试，无网络。跑法：pytest tests/core/test_horizon_policy.py -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest
from pydantic import ValidationError

from src.core.decision_contract import (
    DesiredAction, ExecutionStatus, Horizon, InvalidationRule, ResearchStatus,
    ThesisStatus, TruthValue,
)
from src.core.decision_policy import (
    POLICY_ID_LONG,
    POLICY_ID_MID,
    HorizonFacts,
    HorizonPlan,
    evaluate_horizon,
)
from src.core.research import (
    ThesisRecord,
    assess_thesis,
    evaluate_invalidation_rules,
    mid_to_long_requires_new_assessment,
)


def _plan(horizon=Horizon.MID, accepted=True, invalidate=None, policy=None) -> HorizonPlan:
    return HorizonPlan(
        plan_id="600519_f5test", horizon=horizon,
        policy_id=policy or (POLICY_ID_MID if horizon is Horizon.MID else POLICY_ID_LONG),
        intent="测试逻辑", accepted_at="2026-09-01T10:00:00" if accepted else None,
        invalidate_if=invalidate or [],
    )


_COMPLETE = dict(research_status=ResearchStatus.COMPLETE, thesis_status=ThesisStatus.VALID,
                 info_reliable=True)


def _facts(**kw) -> HorizonFacts:
    base = dict(_COMPLETE)
    base.update(kw)
    return HorizonFacts(**base)


# ── 1. 行0 激活门：新计划需确认后才激活 ──────────────────

def test_unaccepted_plan_only_reviews():
    p = evaluate_horizon(_plan(accepted=False), _facts(), "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.REVIEW, "未确认计划只 REVIEW 不出行动"
    assert "计划未确认" in p.reason_codes[0]


# ── 2. 行1：硬退出优先（含技术强势抵消场景）─────────────

def test_hard_exit_beats_everything_while_held():
    p = evaluate_horizon(_plan(), _facts(hard_exit_triggered=True, hard_exit_detail="被ST"),
                         "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.EXIT and p.target_weight == 0.0
    assert p.legacy_trace is None and "决策表行1" in p.reason_codes[0]


def test_hard_exit_without_position_waits():
    p = evaluate_horizon(_plan(), _facts(hard_exit_triggered=True),
                         "600519", confirmed_ratio=0.0)
    assert p.desired_action is DesiredAction.WAIT, "硬退出+无持仓 → WAIT 禁止新增"


# ── 3. 行2：信息不可靠 → 数据坏不等于零仓位也不等于看好 ──

def test_unreliable_info_reviews_while_held():
    p = evaluate_horizon(_plan(), _facts(info_reliable=False), "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.REVIEW
    assert p.desired_action is not DesiredAction.EXIT, "数据坏不等于退出"
    assert p.desired_action is not DesiredAction.HOLD, "数据坏不等于继续看好"


# ── 4. 行3：逻辑 INVALID → EXIT（不能被技术反弹抵消）────

def test_invalid_thesis_exits_despite_strength():
    p = evaluate_horizon(_plan(), _facts(thesis_status=ThesisStatus.INVALID), "600519",
                         confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.EXIT


# ── 5. 行4：证据 CONFLICTED / INCOMPLETE → REVIEW 冻结新增 ─

def test_conflicted_and_incomplete_freeze_new_risk():
    for rs in (ResearchStatus.CONFLICTED, ResearchStatus.INCOMPLETE):
        p = evaluate_horizon(_plan(), _facts(research_status=rs), "600519", confirmed_ratio=0.2)
        assert p.desired_action is DesiredAction.REVIEW, f"{rs} 持有→REVIEW"
        p2 = evaluate_horizon(_plan(), _facts(research_status=rs), "600519", confirmed_ratio=None)
        assert p2.desired_action is DesiredAction.REVIEW, \
            "行4 两列均 REVIEW（DESIGN §4.2 原表对齐，F5 审查 P2）"


def test_long_missing_financial_never_quality_pass():
    """TASKS F5 验收：长期缺财务不发质量合格结论——research INCOMPLETE 时
    即使 quality_valuation_ok=True 也不 OPEN/ADD（行4 先于 行8，两列均 REVIEW
    ——DESIGN §4.2 原表对齐，F5 审查 P2）。"""
    p = evaluate_horizon(
        _plan(Horizon.LONG),
        _facts(research_status=ResearchStatus.INCOMPLETE, quality_valuation_ok=True,
               price_in_buy_zone=True),
        "600519", confirmed_ratio=None)
    assert p.desired_action is DesiredAction.REVIEW, "证据不齐不给长期买入资格（两列均 REVIEW）"


# ── 6. 同证据不同周期：结果不同且各自可解释 ──────────────

def test_same_facts_different_horizons_explainable():
    """短期破均线 + 质量合格未进买区：MID 视角看技术退出成立 → REDUCE；
    LONG 视角看是技术噪声 → HOLD。两包 reason 各自指回自己的决策表行。"""
    facts = _facts(technical_exit_triggered=True, short_term_noise_only=True)
    mid = evaluate_horizon(_plan(Horizon.MID), facts, "600519", confirmed_ratio=0.2)
    lng = evaluate_horizon(_plan(Horizon.LONG), facts, "600519", confirmed_ratio=0.2)
    assert mid.desired_action is DesiredAction.REDUCE, "MID：预设技术退出成立不压长拿"
    assert lng.desired_action is DesiredAction.HOLD, "LONG：普通短期破均线是噪声"
    assert "决策表行5" in mid.reason_codes[0] and "决策表行7" in lng.reason_codes[0]
    assert mid.policy_id == POLICY_ID_MID and lng.policy_id == POLICY_ID_LONG


def test_long_short_term_noise_does_not_exit():
    """TASKS F5 验收：长期短期技术噪声不触发未约定退出——即使 technical_exit
    标志也置了，行7 只 HOLD（噪声不升级成退出）。"""
    p = evaluate_horizon(_plan(Horizon.LONG),
                         _facts(short_term_noise_only=True, technical_exit_triggered=True),
                         "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.HOLD


# ── 7. 行8/9：LONG 买价区间与"好公司≠现在买" ─────────────

def test_long_buy_zone_gate():
    """质量合格但未进买价区间 → 行9 HOLD（持有）/WAIT（未持有），不是 OPEN。"""
    p = evaluate_horizon(_plan(Horizon.LONG), _facts(quality_valuation_ok=True,
                                                     price_in_buy_zone=False),
                         "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.HOLD
    p2 = evaluate_horizon(_plan(Horizon.LONG), _facts(quality_valuation_ok=True,
                                                      price_in_buy_zone=False),
                          "600519", confirmed_ratio=0.0)
    assert p2.desired_action is DesiredAction.WAIT


def test_long_acute_risk_blocks_entry():
    p = evaluate_horizon(_plan(Horizon.LONG), _facts(quality_valuation_ok=True,
                                                     price_in_buy_zone=True,
                                                     acute_risk=True),
                         "600519", confirmed_ratio=0.0)
    assert p.desired_action is DesiredAction.WAIT, "急性风险排除入场（行8 排除项）"


def test_unknown_budget_no_precise_add():
    """预算未知（组合信息缺失）→ 不给精确加仓目标（target None / 有条件方向）。"""
    p = evaluate_horizon(_plan(Horizon.MID), _facts(entry_condition_met=True),
                         "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.HOLD, "budget_available=None → HOLD 不假增仓"
    p_open = evaluate_horizon(_plan(Horizon.MID), _facts(entry_condition_met=True),
                              "600519", confirmed_ratio=None)
    assert p_open.desired_action is DesiredAction.OPEN and p_open.target_weight is None


# ── 8. 未支持行业 ────────────────────────────────────────

def test_unsupported_industry_limited():
    """TASKS F5 验收：未支持行业明确限制——COMPLETE 证据被强制 NOT_APPLICABLE，
    不填通用数字强行纳入。"""
    p = evaluate_horizon(_plan(Horizon.LONG), _facts(unsupported_industry=True),
                         "600519", confirmed_ratio=0.0)
    assert p.research_status is ResearchStatus.NOT_APPLICABLE
    assert p.desired_action is DesiredAction.WAIT


# ── 9. 复核触发不是强制卖 ────────────────────────────────

def test_mid_invalidation_true_structurally_forces_exit():
    """F5 审查 P1-1 回归锁：失效 TRUE 在决策表端**结构化**推导——调用方漏接
    assess_thesis 时，plan 带 TRUE 规则仍必须 EXIT（不能 ADD/HOLD 无限延期）。"""
    plan = _plan(Horizon.MID, invalidate=[
        InvalidationRule(rule_id="inv1", condition="行业景气证伪", evaluation=TruthValue.TRUE)])
    # 调用方漏接：facts 仍声称 VALID + 入场条件 + 预算可用
    facts = _facts(thesis_status=ThesisStatus.VALID, entry_condition_met=True,
                   budget_available=True)
    p = evaluate_horizon(plan, facts, "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.EXIT, \
        f"TRUE 规则必须结构化强制 EXIT，实际 {p.desired_action}（原实现 ADD=账本虚假）"
    assert "决策表行3" in p.reason_codes[0]


def test_invalidation_unknown_reviews_not_add():
    """F5 审查 P1-1 配套：失效条件 UNKNOWN（未验证）→ REVIEW（需要核对），
    不自动变 FALSE 也不继续加仓。"""
    plan = _plan(Horizon.MID, invalidate=[
        InvalidationRule(rule_id="inv1", condition="现金流恶化", evaluation=TruthValue.UNKNOWN)])
    facts = _facts(thesis_status=ThesisStatus.VALID, entry_condition_met=True,
                   budget_available=True)
    p = evaluate_horizon(plan, facts, "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.REVIEW


def test_hard_exit_precedes_activation_gate():
    """F5 审查 P1-2 回归锁：未确认计划 + 已核实硬退出 → EXIT（不被行0 官僚门掩盖，
    DESIGN §2.2 REVIEW 不能掩盖已确认重大退出证据）。"""
    p = evaluate_horizon(_plan(accepted=False),
                         _facts(hard_exit_triggered=True, hard_exit_detail="被ST"),
                         "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.EXIT, "硬退出先于激活门"
    assert "决策表行1" in p.reason_codes[0]


def test_row6_unheld_without_budget_waits():
    """F5 审查 P1-3 回归锁：行6 条件含'且预算可用'——预算明确不可用 + 未持有 →
    WAIT（不反事实地说'预算可用'）。"""
    p = evaluate_horizon(_plan(Horizon.MID), _facts(entry_condition_met=True,
                                                    budget_available=False),
                         "600519", confirmed_ratio=0.0)
    assert p.desired_action is DesiredAction.WAIT
    assert "预算不可用" in p.reason_codes[0]


def test_row7_not_held_falls_to_entry_evaluation():
    """F5 审查 P1-4 回归锁：行7 未持有列=按长期入场策略评估（落行8/9）——
    不给未建仓用户'继续持有'（ADR-F08）。"""
    # 噪声 + 质量估值合格 + 进买区 → OPEN（经行8）
    p = evaluate_horizon(_plan(Horizon.LONG),
                         _facts(short_term_noise_only=True, quality_valuation_ok=True,
                                price_in_buy_zone=True),
                         "600519", confirmed_ratio=0.0)
    assert p.desired_action is DesiredAction.OPEN
    # 噪声 + 未进买区 → WAIT（经行9）
    p2 = evaluate_horizon(_plan(Horizon.LONG),
                          _facts(short_term_noise_only=True, quality_valuation_ok=True,
                                 price_in_buy_zone=False),
                          "600519", confirmed_ratio=0.0)
    assert p2.desired_action is DesiredAction.WAIT


def test_row8_long_entry_full_path():
    """F5 审查 P2 覆盖缺口：行8 正路径——质量合格+进买区+无急性风险 → OPEN/ADD。"""
    p_open = evaluate_horizon(_plan(Horizon.LONG), _facts(quality_valuation_ok=True,
                                                          price_in_buy_zone=True),
                              "600519", confirmed_ratio=0.0)
    assert p_open.desired_action is DesiredAction.OPEN and "决策表行8" in p_open.reason_codes[0]
    p_add = evaluate_horizon(_plan(Horizon.LONG), _facts(quality_valuation_ok=True,
                                                         price_in_buy_zone=True,
                                                         budget_available=True),
                             "600519", confirmed_ratio=0.2)
    assert p_add.desired_action is DesiredAction.ADD


def test_row10_unestablished_thesis():
    """F5 审查 P2 覆盖缺口：行10——逻辑未建立（UNESTABLISHED）→ REVIEW/WAIT。"""
    p = evaluate_horizon(_plan(), _facts(thesis_status=ThesisStatus.UNESTABLISHED),
                         "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.REVIEW
    assert "决策表行10" in p.reason_codes[0]


def test_review_due_populates_next_check():
    """F5 审查 P2 修复锁：review_due=True → next_check 落包（复核触发可见，
    依然不强制卖）。"""
    p = evaluate_horizon(_plan(), _facts(review_due=True), "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.HOLD
    assert p.next_check is not None and "复核" in p.next_check.due


def test_empty_accepted_at_counts_as_unactivated():
    """F5 审查 P2 修复锁：accepted_at 空串=未激活（激活门安全语义）。"""
    p = evaluate_horizon(_plan(accepted=False), _facts(), "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.REVIEW


def test_review_due_is_not_forced_sell():
    """复核日/财报触发只走 REVIEW 语义——不影响 VALID 逻辑的 HOLD 结论（行9 语义）。
    review_due 标记进 packet 供展示，但绝不产生 EXIT。"""
    p = evaluate_horizon(_plan(), _facts(review_due=True), "600519", confirmed_ratio=0.2)
    assert p.desired_action is DesiredAction.HOLD, "复核触发不改 VALID 持有结论"


# ── 10. 失效条件三值与研究层 ─────────────────────────────

def test_invalidation_three_value_semantics():
    """UNKNOWN 不可自动变 FALSE：全 UNKNOWN → UNKNOWN（REVIEW_REQUIRED）；任一 TRUE → INVALID。"""
    assert evaluate_invalidation_rules([
        InvalidationRule(rule_id="r1", condition="现金流恶化", evaluation=TruthValue.UNKNOWN),
        InvalidationRule(rule_id="r2", condition="订单取消", evaluation=TruthValue.FALSE),
    ]) is TruthValue.UNKNOWN
    assert evaluate_invalidation_rules([
        InvalidationRule(rule_id="r1", condition="现金流恶化", evaluation=TruthValue.TRUE),
    ]) is TruthValue.TRUE
    assert evaluate_invalidation_rules([
        InvalidationRule(rule_id="r1", condition="x", evaluation=TruthValue.FALSE),
    ]) is TruthValue.FALSE


def test_mid_invalidation_true_forces_exit():
    """TASKS F5 验收：中期逻辑失效不能自动延长期限——invalidate TRUE → INVALID → EXIT。"""
    plan = _plan(Horizon.MID, invalidate=[
        InvalidationRule(rule_id="inv1", condition="行业景气证伪", evaluation=TruthValue.TRUE)])
    facts = _facts(thesis_status=ThesisStatus.VALID)  # 表面 VALID
    p = evaluate_horizon(plan, facts, "600519", confirmed_ratio=0.2)
    # 调用方职责：把 invalidate 求值结果反映到 thesis_status——本测试锁定调用约定：
    # evaluate_horizon 读 facts.thesis_status（INVALID→EXIT 行3）；
    # assess_thesis 负责由 invalidate 推 INVALID（下一条测试）。
    assert p.desired_action is not DesiredAction.ADD


def test_assess_thesis_driven_by_evidence():
    """状态转移由证据与明确条件驱动：反证核实 → INVALID；UNKNOWN → REVIEW_REQUIRED；
    带**可解析证据引用**的观察事实 → VALID（R0 资格门，A03）；无引用文本 → UNESTABLISHED。"""
    t = ThesisRecord(thesis_id="t1", horizon="MID",
                     beneficiary_business="氮化镓快充", profit_mechanism="订单→营收")
    assert assess_thesis(t) is ThesisStatus.UNESTABLISHED
    t2 = t.model_copy(update={"facts_observed": ["Q2 订单落地（公告 P3）"]})
    # R0 资格止血（A03）：纯文本事实无可解析证据引用——文字存在≠逻辑成立，
    # 不再「一条 facts 文本即 VALID」（探针 P1 同根）
    assert assess_thesis(t2) is ThesisStatus.UNESTABLISHED
    t2_ref = t2.model_copy(update={
        "fact_evidence_refs": {"Q2 订单落地（公告 P3）": ["cninfo://ann/p3"]}})
    # J0/N3：「可解析」须接证据库实存解析——无 resolver 一律不 VALID（旧兼容路径降级）
    assert assess_thesis(t2_ref) is ThesisStatus.UNESTABLISHED
    # 带 resolver（引用实存且归属正确）→ 正式 VALID 路径
    resolver = lambda ref: ref == "cninfo://ann/p3"  # noqa: E731
    assert assess_thesis(t2_ref, evidence_resolver=resolver) is ThesisStatus.VALID
    assert assess_thesis(t2_ref, evidence_resolver=resolver,
                         invalidation_value=TruthValue.UNKNOWN) is ThesisStatus.REVIEW_REQUIRED
    assert assess_thesis(t2_ref, evidence_resolver=resolver,
                         invalidation_value=TruthValue.TRUE) is ThesisStatus.INVALID
    assert assess_thesis(t2_ref, evidence_resolver=resolver,
                         counter_evidence_verified=True) is ThesisStatus.INVALID
    t3 = t2_ref.model_copy(update={"counter_evidence": ["竞品降价 20%（新闻）"]})
    assert assess_thesis(t3, invalidation_value=TruthValue.FALSE) is ThesisStatus.REVIEW_REQUIRED


def test_assess_thesis_blank_and_unresolvable_refs_not_valid():
    """R0 资格门反例回归（探针 P1）：空白事实串、空引用串、引用键不匹配都不能 VALID。"""
    t = ThesisRecord(thesis_id="p1", horizon="LONG", beneficiary_business="",
                     profit_mechanism="", facts_observed=["   "])
    assert assess_thesis(t) is ThesisStatus.UNESTABLISHED
    t2 = ThesisRecord(thesis_id="p2", horizon="LONG", beneficiary_business="x",
                      profit_mechanism="y", facts_observed=["事实A"],
                      fact_evidence_refs={"事实A": ["", "   "]})
    assert assess_thesis(t2) is ThesisStatus.UNESTABLISHED
    # 键=去空白事实原文精确匹配——键对不上等于没引用
    t3 = ThesisRecord(thesis_id="p3", horizon="LONG", beneficiary_business="x",
                      profit_mechanism="y", facts_observed=["事实A"],
                      fact_evidence_refs={"事实B": ["ev-1"]})
    assert assess_thesis(t3) is ThesisStatus.UNESTABLISHED


def test_mid_to_long_requires_explicit_assessment():
    t = ThesisRecord(thesis_id="t1", horizon="MID", beneficiary_business="x", profit_mechanism="y")
    assert mid_to_long_requires_new_assessment(t) is True
    assert mid_to_long_requires_new_assessment(
        t.model_copy(update={"horizon": "LONG"})) is False


# ── 11. 侧挂独立：旧 TradePlan 零写入 ────────────────────

def test_plan_v2_is_sidecar_no_tradeplan_write():
    """HorizonPlan 是独立侧挂对象（不 import 不修改 TradePlan）——旧计划零写入即可
    继续读取（TASKS F5 验收）。耦合检查：模块命名空间不含 TradePlan。"""
    import src.core.decision_policy as dp
    assert not hasattr(dp, "TradePlan"), "决策表不得 import 旧 TradePlan（侧挂独立）"
    assert not hasattr(dp, "TradeLifecycle"), "决策表不得耦合旧生命周期语义"


def test_policy_ids_are_new_not_legacy_mode():
    assert POLICY_ID_MID == "fusion_mid_v1" and POLICY_ID_LONG == "fusion_long_v1"
    with pytest.raises(ValidationError):
        # 不用旧 mode 代替 horizon：horizon 只接受 MID/LONG（LEGACY 是 legacy 专属）
        HorizonPlan(plan_id="x", horizon="LEGACY", policy_id=POLICY_ID_MID, intent="x")
