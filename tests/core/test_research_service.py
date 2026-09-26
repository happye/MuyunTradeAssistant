"""R3 自动研究服务与双周期计划回归测试（plan/fusion iteration2，RESEARCH_LOOP §1/§2/§4）

锁死语义：
1. 不填 --facts 也生成完整草稿；缺关键资料 → 带具体缺口的草稿，不强行 VALID（验收1）
2. 同一事实同时服务中长期；必需命题不同，MID VALID 不使 LONG 自动 VALID（验收2）
3. 未核实重大反证→REVIEW_REQUIRED；失效条件 TRUE→INVALID（验收3，优先级链）
4. 接受绑定 plan_id+revision+content_hash；新事实只生成修订草稿，不沿用旧接受（验收4）
5. 持仓主意图生命周期：设置需已接受精确版本；清仓后再建仓不沿用旧轮授权（验收5）
6. 分析重试幂等（同输入同 run_id 走缓存）；两进程写计划指纹冲突拒绝不丢更新（验收6）
7. PolicyIntent 剥执行语义——影子/意图不透传 ELIGIBLE（验收7）

纯内存+临时文件测试，无网络无 AI。跑法：pytest tests/core/test_research_service.py -q
"""
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.claim_extraction import ClaimRecord, SourceDocument
from src.core.decision_contract import DesiredAction, ResearchStatus, ThesisStatus
from src.core.decision_policy import (
    HorizonFacts,
    PolicyIntent,
    evaluate_horizon,
    evaluate_horizon_intent,
)
from src.core.research import (
    AssertionEvaluation,
    ThesisAssertion,
    assess_thesis_by_assertions,
    evaluate_assertion,
)
from src.core.research_service import ResearchBundle, ResearchService

AS_OF = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)


class _Cap:
    """FactorResult 形状的轻量替身（status 口径与 R4 一致）。"""

    def __init__(self, value=1.0, status="OK"):
        self.value, self.status, self.note = value, status, ""


def _claim(claim_id="c1", event_type="order", security_id="600519",
           statement="公司获 900 万元订单", **kw):
    return ClaimRecord(claim_id=claim_id, security_id=security_id, subject=security_id,
                       event_type=event_type, statement=statement, **kw)


def _doc(claim_ids_hash="doc-hash-1"):
    body = "公司公告：签订 900 万元销售订单。"
    return SourceDocument(canonical_uri="cninfo://ann/1",
                          content_hash=SourceDocument.body_hash(body),
                          security_ids=["600519"], body=body,
                          published_at=datetime(2026, 9, 1, tzinfo=timezone.utc))


def _service_input(claims=None, capabilities=None):
    """构造服务输入：claim 走 tiered 核验（FACT_CHECKED 需 quote 在正文）。"""
    body = "公司公告：签订 900 万元销售订单。"
    doc = SourceDocument(canonical_uri="cninfo://ann/1",
                         content_hash=SourceDocument.body_hash(body),
                         security_ids=["600519"],
                         published_at=datetime(2026, 9, 1, tzinfo=timezone.utc), body=body)
    docs = []
    for c in (claims or []):
        docs.append({"claim": c, "documents": [doc]})
    return docs, (capabilities or {})


def _run_service(claims=None, capabilities=None, horizons=("MID", "LONG")):
    docs, caps = _service_input(claims, capabilities)
    svc = ResearchService()
    bundle = svc.run("600519", as_of=AS_OF, horizons=horizons,
                     evidence_records=[], documents=docs, factor_capabilities=caps)
    return svc, bundle


# ── 1. 自动草稿（无 --facts）与诚实缺口 ────────────────────

def test_auto_draft_without_user_facts():
    """验收1：不填 --facts 也生成 MID/LONG 草稿；已核验主张带证据引用进 fact_evidence_refs。"""
    c = _claim(statement="公司获 900 万元订单")
    c = c.model_copy(update={"quote_text": "签订 900 万元销售订单",
                             "citation_uri": "cninfo://ann/1",
                             "citation_hash": _doc().content_hash})
    svc, bundle = _run_service(claims=[c])
    assert [d["horizon"] for d in bundle.plan_drafts] == ["MID", "LONG"]
    mid = bundle.plan_drafts[0]
    assert mid["accepted_at"] is None, "系统草稿永远未激活"
    assert mid["facts_observed"] == ["公司获 900 万元订单"]
    assert mid["fact_evidence_refs"]["公司获 900 万元订单"] == ["c1"], "核验引用正式化"
    assert mid["snapshot_id"] == bundle.snapshot_id
    assert bundle.gaps, "无手写事实也有自动草稿——缺口来自命题模板"


def test_missing_data_gives_gaps_not_valid():
    """验收1：缺关键资料 → 带具体缺口的草稿，不强行 VALID。"""
    svc, bundle = _run_service(claims=[], capabilities={})  # 无主张无因子
    assert bundle.assessments["MID"]["status"] == "UNESTABLISHED"
    assert bundle.assessments["LONG"]["status"] == "UNESTABLISHED"
    assert any("valuation_range_v1" in g for g in bundle.gaps), "LONG 缺估值区间能力——具体点名"
    draft = [d for d in bundle.plan_drafts if d["horizon"] == "LONG"][0]
    assert draft["gaps"], "草稿带缺口清单"


# ── 2. 共享事实、分别评估（架构师裁决③）───────────────────

def test_mid_valid_does_not_leak_to_long():
    """验收2（断言级）：MID 必需命题全 TRUE → VALID；LONG 缺估值区间能力 → 诚实
    UNESTABLISHED——同一已核验主张服务两周期，必需命题集合不同。"""
    from src.core.research_service import _default_assertions
    mid = _default_assertions("600519", "MID", "run_t")
    long_ = _default_assertions("600519", "LONG", "run_t")
    caps = {"relative_return_v2": _Cap(), "trading_capacity_v1": _Cap(),
            "cash_conversion_v1": _Cap(), "roe_observed_v1": _Cap(),
            "balance_risk_v1": _Cap()}
    # 共享事实：同一已核验主张 id 挂两周期相关命题（c1 订单主张）
    def _attach(a, sup):
        return a.model_copy(update={"supporting_evidence_ids": sup})

    mid_ev = [evaluate_assertion(
        _attach(a, ["c1"] if a.proposition_type in
                ("real_exposure", "change_to_profit", "moat") else []),
        available_capabilities=caps, verified_evidence_ids={"c1"}, as_of=AS_OF)
        for a in mid if a.proposition_type != "window_and_refutation"]
    # window_and_refutation 无依据 → MID 仍缺——先验证它诚实 UNKNOWN
    w = evaluate_assertion(mid[2], available_capabilities=caps,
                           verified_evidence_ids=set(), as_of=AS_OF)
    assert w.evaluation is AssertionEvaluation.UNKNOWN
    # 补上依据（checkpoint 登记——用户确认时窗反证条款）→ MID 全 TRUE → VALID
    w_ok = w.model_copy(update={"supporting_evidence_ids": ["c1"]})
    w_ok = evaluate_assertion(w_ok, available_capabilities=caps,
                              verified_evidence_ids={"c1"}, as_of=AS_OF)
    mid_all = [a for a in mid_ev] + [w_ok]
    assert assess_thesis_by_assertions(mid_all) is ThesisStatus.VALID
    # LONG：同一主张挂 moat/cash_sustainability，但 valuation_range_v1 不可得 → UNESTABLISHED
    long_ev = []
    for a in long_:
        sup = ["c1"] if a.proposition_type in ("moat",) else []
        long_ev.append(evaluate_assertion(
            _attach(a, sup), available_capabilities=caps,
            verified_evidence_ids={"c1"}, as_of=AS_OF))
    assert assess_thesis_by_assertions(long_ev) is ThesisStatus.UNESTABLISHED, \
        "MID VALID 不使 LONG 自动 VALID"
    assert {a.proposition_type for a in mid} != {a.proposition_type for a in long_}


# ── 3. 反证/失效优先级链 ──────────────────────────────────

def test_unverified_counter_evidence_gives_review_required():
    """验收3：未核实重大反证 → REVIEW_REQUIRED（优先级在 UNESTABLISHED 之前——
    RESEARCH_LOOP §2：关键反证未核实→REVIEW_REQUIRED）。"""
    a = ThesisAssertion(horizon="MID", proposition_type="change_to_profit",
                        description="订单进入收入", importance="required",
                        evidence_requirements=[], supporting_evidence_ids=["c1"],
                        counter_evidence_ids=["rumor-1"])
    ev = evaluate_assertion(a, available_capabilities={}, verified_evidence_ids={"c1"},
                            as_of=AS_OF)
    assert ev.evaluation is AssertionEvaluation.UNKNOWN, "反证未核实命题不 TRUE"
    status = assess_thesis_by_assertions([ev], invalidation_value=None,
                                         verified_counter_evidence=False,
                                         unverified_material_counter=True)
    assert status is ThesisStatus.REVIEW_REQUIRED


def test_verified_invalidation_true_gives_invalid():
    """验收3：已核实失效条件成立 → INVALID（技术反弹不能覆盖——决策表层已有行3锁）。"""
    a = ThesisAssertion(horizon="MID", proposition_type="change_to_profit",
                        description="订单进入收入", importance="required",
                        supporting_evidence_ids=["c1"])
    status = assess_thesis_by_assertions([a], invalidation_value=None,
                                         verified_counter_evidence=True)
    assert status is ThesisStatus.INVALID
    from src.core.decision_contract import TruthValue
    status2 = assess_thesis_by_assertions([a], invalidation_value=TruthValue.TRUE,
                                          verified_counter_evidence=False)
    assert status2 is ThesisStatus.INVALID


# ── 6. 幂等与并发 ─────────────────────────────────────────

def test_service_retry_idempotent():
    """验收6：同输入重跑同 run_id（走缓存）；不同输入新 run_id。"""
    c = _claim(statement="公司获 900 万元订单").model_copy(
        update={"quote_text": "签订 900 万元销售订单", "citation_uri": "cninfo://ann/1",
                "citation_hash": _doc().content_hash})
    docs, caps = _service_input(claims=[c])
    svc = ResearchService()
    b1 = svc.run("600519", as_of=AS_OF, evidence_records=[], documents=docs,
                 factor_capabilities=caps)
    n_run = svc.stats["bundle_runs"]
    b2 = svc.run("600519", as_of=AS_OF, evidence_records=[], documents=docs,
                 factor_capabilities=caps)
    assert b1.run_id == b2.run_id, "同输入同 run_id（重试幂等）"
    assert b2 is b1 or b2.model_dump() == b1.model_dump()
    assert svc.stats["bundle_cache_hits"] >= 1, "第二次命中 bundle 缓存"
    b3 = svc.run("600519", as_of=AS_OF, evidence_records=[], documents=[],
                 factor_capabilities=caps)
    assert b3.run_id != b1.run_id, "输入变化 → 新 run"


def test_two_writers_conflict_rejected(tmp_path):
    """验收6：两进程同时改计划——指纹冲突拒绝后写入者重读，先写者更新不丢。"""
    from src.core.decision_policy import HorizonPlan, POLICY_ID_MID
    from src.data.horizon_plans import HorizonPlanStore
    path = tmp_path / "plans.json"
    w1, w2 = HorizonPlanStore(path), HorizonPlanStore(path)
    plan = HorizonPlan(plan_id="p1", security_id="600519", horizon="MID",
                       policy_id=POLICY_ID_MID, intent="v1")
    assert w1.save(plan) and w2.get("600519", "MID") is not None  # w2 此时未重读
    w2_new = plan.model_copy(update={"intent": "w2 改的 v2"})
    w2_new.created_at = plan.created_at  # 排除时间戳噪声
    assert w2.save(w2_new) is True
    w1_new = plan.model_copy(update={"intent": "w1 改的 v2"})
    w1_new.created_at = plan.created_at
    assert w1.save(w1_new) is False, "外部修改→指纹冲突拒绝（不静默覆盖）"
    assert w1.get("600519", "MID").intent == "w2 改的 v2", "重读后先写者更新仍在（不丢更新）"


# ── 4/5. 接受绑定与主意图生命周期 ─────────────────────────

def _mk_plan(code="600519", horizon="MID", intent="v1"):
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    from src.core.decision_contract import Horizon
    return HorizonPlan(plan_id=f"p_{code}_{horizon}", security_id=code,
                       horizon=Horizon[horizon], policy_id=POLICY_ID_MID,
                       intent=intent)


def test_accept_binds_exact_version(tmp_path):
    """验收4：接受绑定精确版本；新事实生成修订草稿后 is_accepted_version=False，
    接受账目保留可审计（旧版本对照）。"""
    from src.data.horizon_plans import HorizonPlanStore
    store = HorizonPlanStore(tmp_path / "plans.json")
    plan = _mk_plan()
    store.save(plan)
    ok, _ = store.accept("600519")
    assert ok is True
    accepted = store.get("600519", "MID")
    assert store.is_accepted_version(accepted) is True
    # 新事实 → 修订草稿（同 plan_id revision 递增）
    revised = accepted.model_copy(update={"intent": "v2——新事实修订", "revision": accepted.revision + 1})
    store.save(revised)
    latest = store.get("600519", "MID")
    assert latest.intent == "v2——新事实修订"
    assert store.is_accepted_version(latest) is False, "草稿不能沿用旧接受状态"
    # 接受账目保留（plan_id/revision/hash 可审计——旧版本对照不丢）
    ref = store._load()["accepted_refs"]["600519:MID"]
    assert ref["plan_id"] == accepted.plan_id and ref["revision"] == accepted.revision
    assert ref["content_hash"] == accepted.content_hash()


def test_active_ref_lifecycle(tmp_path):
    """验收5：主意图设置需已接受精确版本；清仓清除后重新建仓需重新接受——不沿用旧授权。"""
    from src.data.horizon_plans import HorizonPlanStore
    store = HorizonPlanStore(tmp_path / "plans.json")
    plan = _mk_plan()
    store.save(plan)
    assert store.set_active_ref("600519", plan)[0] is False, "未接受不能设主意图"
    store.accept("600519")
    plan_now = store.get("600519", "MID")
    assert store.set_active_ref("600519", plan_now)[0] is True
    ref = store.get_active_ref("600519")
    assert ref["plan_id"] == plan_now.plan_id and ref["horizon"] == "MID"
    # 清仓退出 → 清除引用；重新建仓（新计划）需重新接受+重设——旧授权不沿用
    assert store.clear_active_ref("600519") is True
    assert store.get_active_ref("600519") is None
    new_round = _mk_plan(intent="新一轮").model_copy(update={"plan_id": "p_600519_MID_r2"})
    store.save(new_round)
    store.accept("600519")
    assert store.set_active_ref("600519", store.get("600519", "MID"))[0] is True
    ref2 = store.get_active_ref("600519")
    assert ref2["plan_id"] == store.get("600519", "MID").plan_id, "新轮引用=新接受版本"


# ── 7. PolicyIntent 剥执行语义 ────────────────────────────

def test_policy_intent_strips_execution_semantics():
    """验收7：意图与执行资格分离——INTENT 无 execution_status/executable_action；
    影子层预算未知不得称可执行精确仓位。"""
    plan = _mk_plan()
    plan = plan.model_copy(update={"accepted_at": "2026-09-25T08:00:00"})  # 已激活
    facts = HorizonFacts(research_status=ResearchStatus.COMPLETE,
                         thesis_status=ThesisStatus.VALID,
                         entry_condition_met=True, budget_available=None)
    intent = evaluate_horizon_intent(plan, facts, "600519", confirmed_ratio=None)
    assert intent.desired_action is DesiredAction.OPEN, "同表裁决（行6 组合未知分支）"
    assert intent.reason_codes, "reason 可解释"
    assert not hasattr(intent, "execution_status") and not hasattr(intent, "executable_action"), \
        "中间意图不携带执行资格字段"
    assert intent.target_weight is None, "组合未知不给精确目标"
    pkt = evaluate_horizon(plan, facts, "600519", confirmed_ratio=None)
    assert pkt.desired_action is intent.desired_action
    # EXIT 分支：唯一有值的目标（target=0 契约）在意图上保留——清仓意图不丢
    from src.core.decision_contract import InvalidationRule, TruthValue
    plan_inv = plan.model_copy(update={"invalidate_if": [
        InvalidationRule(rule_id="r1", condition="行业景气证伪", evaluation=TruthValue.TRUE)]})
    intent_exit = evaluate_horizon_intent(plan_inv, facts, "600519", confirmed_ratio=0.2)
    assert intent_exit.desired_action is DesiredAction.EXIT
    assert intent_exit.target_weight == 0.0, "EXIT 意图 target=0（契约不变量在意图层保留）"
