"""J0 事实/评估唯一真值回归测试（plan/fusion iteration3，DELIVERY_PLAN J0 验收）

锁死语义：
1. N1：无关摘录不得 FACT_CHECKED——自由文本最高 EXCERPT_GROUNDED（类型化事实范围门）
2. 三类扩展反例：真实引文结论不被支持 / 数字为其他指标或主体 / 缺公开时间
3. N2：REFUTES/否定主张绝不进正面支撑；正式评估状态在草稿/shadow 同或更保守
   （shadow 不再调用旧 facts+refs 简化判断救成 VALID）
4. N3：引用必须经实存解析才 VALID（无 resolver/不存在的 ID/实体错配都不行）
5. checkpoint 合法建立渠道：window_and_refutation 经「经核实材料 + 用户确认风险意愿」
   才可建立；无材料/未确认保持 UNKNOWN，不因 accept 自动通过
6. AssessmentStore：不可变、内容寻址、幂等；shadow 消费同一评估（错配/过期→待复核）
7. 因子绑定：未绑定合格快照的能力不进命题评估（raw dict 旁路封堵）
8. 缓存全量键（改负债分量/规则版本即失效）+ 返回副本防原地污染
9. 充分且合法的 MID 合成资料包达 VALID；缺任一必需命题降级；LONG 诚实 UNESTABLISHED

纯内存+临时文件测试，无网络无 AI，不读写真实 HOME。跑法：pytest tests/core/test_j0_facts_assessment.py -q
"""
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.claim_extraction import (
    ClaimRecord,
    SourceDocument,
    VerificationLevel,
    verify_claim_tiered,
)
from src.core.decision_contract import ThesisStatus
from src.core.research import (
    CheckpointCondition,
    ThesisRecord,
    assess_thesis,
)
from src.core.research_service import ResearchService
from src.core.shadow_diff import _thesis_status_for
from src.data.research_store import AssessmentStore

AS_OF = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)
PUB = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _make_claim(body: str, statement: str, **kw) -> tuple[ClaimRecord, SourceDocument]:
    doc = SourceDocument(canonical_uri="probe://doc",
                         content_hash=SourceDocument.body_hash(body),
                         security_ids=["600000"], published_at=kw.pop("doc_published", PUB),
                         body=body)
    c = ClaimRecord(security_id="600000", subject="600000", event_type=kw.pop("event_type", "order"),
                    statement=statement, citation_uri=doc.canonical_uri,
                    citation_hash=doc.content_hash, quote_text=body,
                    published_at=kw.pop("published_at", PUB), **kw)
    return c, doc


# ── 1. N1 + 扩展反例：类型化事实范围门 ──────────────────────

def test_n1_unrelated_excerpt_not_fact_checked():
    """N1：真实摘录（召开年度会议）支撑不了推论主张（不可动摇的竞争优势）
    ——自由文本最高 EXCERPT_GROUNDED，不得 FACT_CHECKED。"""
    c, d = _make_claim("Company held its annual meeting.",
                       "Company has an unassailable competitive advantage.")
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is VerificationLevel.EXCERPT_GROUNDED
    assert "excerpt_grounded" in r.checks, "摘录真实存在——已达摘录级"
    assert any("自由文本" in f or "核验类型" in f for f in r.failures)


def test_free_text_chinese_inference_not_fact_checked():
    """同类（中文）：真实订单摘录支撑不了「护城河」推论主张。"""
    body = "公司公告：签订 900 万元销售订单。"
    c, d = _make_claim(body, "公司形成难以撼动的护城河", value=900, unit="万元")
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is not VerificationLevel.FACT_CHECKED
    assert r.level is VerificationLevel.EXCERPT_GROUNDED


def test_number_of_other_metric_not_fact_checked():
    """扩展反例②：数字为其他指标——净利润主张配「其他业务收入」摘录，
    主张谓词与摘录指标不一致 → 不得 FACT_CHECKED。"""
    c, d = _make_claim("公司其他业务收入 900 万元。", "公司净利润 900 万元",
                       event_type="earnings", value=900, unit="万元")
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is not VerificationLevel.FACT_CHECKED
    assert any("指标" in f or "谓词" in f for f in r.failures)


def test_number_of_other_subject_flagged():
    """扩展反例②（其他主体）：摘录含同行/竞对表述时数值归属存疑 → 不得 FACT_CHECKED。"""
    c, d = _make_claim("同行公司获得 900 万元订单。", "公司获得 900 万元订单",
                       value=900, unit="万元")
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is not VerificationLevel.FACT_CHECKED
    assert any("主体" in f for f in r.failures)


def test_missing_public_time_not_fact_checked():
    """扩展反例③：来源文档无公开时点 → 类型化事实缺时点资格 → 不得 FACT_CHECKED。"""
    c, d = _make_claim("公司公告：签订 900 万元销售订单。", "公司获得 900 万元订单",
                       value=900, unit="万元", doc_published=None)
    assert d.published_at is None
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is not VerificationLevel.FACT_CHECKED
    assert any("时点" in f for f in r.failures)


def test_numeric_boundary_not_fooled_by_substring():
    """数值词元边界：主张 900 万元不得因「1900 万元」含子串「900 万元」而通过。"""
    body = "公司公告：前期累计投入 1900 万元。"
    c, d = _make_claim(body, "公司获得 900 万元订单", value=900, unit="万元")
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is not VerificationLevel.FACT_CHECKED


def test_typed_numeric_event_still_reaches_fact_checked():
    """正向：类型齐备（主体+谓词+数值单位+时点+摘录定位）的订单事实仍可达 FACT_CHECKED。"""
    body = "公司公告：签订 900 万元销售订单。"
    c, d = _make_claim(body, "公司获得 900 万元订单", value=900, unit="万元",
                       fact_stage="signed")
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is VerificationLevel.FACT_CHECKED, f"failures={r.failures}"


def test_typed_negation_still_reaches_fact_checked():
    """正向：否定型类型化事实（无新订单）仍可达 FACT_CHECKED。"""
    c, d = _make_claim("本期无新订单。", "公司本期无新订单", negation_flag=True)
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is VerificationLevel.FACT_CHECKED, f"failures={r.failures}"


# ── 2. N3：引用实存解析 ───────────────────────────────────

def test_n3_fake_ref_not_valid_without_resolver():
    """N3：facts 引用不存在的非空 ID 不得 VALID（无 resolver=不可确认）。"""
    t = ThesisRecord(thesis_id="p", horizon="MID", beneficiary_business="", profit_mechanism="",
                     facts_observed=["unsupported"],
                     fact_evidence_refs={"unsupported": ["does-not-exist"]})
    assert assess_thesis(t) is ThesisStatus.UNESTABLISHED


def test_n3_resolver_resolves_only_existing_refs():
    """合法 ID 经 resolver 解析 → VALID；合法 ID 但实体错配（resolver 拒绝）→ 不 VALID。"""
    from src.core.decision_contract import ThesisStatus
    t = ThesisRecord(thesis_id="p", horizon="MID", beneficiary_business="", profit_mechanism="",
                     facts_observed=["Q2 订单落地"],
                     fact_evidence_refs={"Q2 订单落地": ["claim-ok"]})
    assert assess_thesis(t, evidence_resolver=lambda ref: ref == "claim-ok") is ThesisStatus.VALID
    t_wrong = t.model_copy(update={
        "fact_evidence_refs": {"Q2 订单落地": ["claim-of-other-security"]}})
    assert assess_thesis(t_wrong, evidence_resolver=lambda ref: False) is not ThesisStatus.VALID


# ── 3. N2：REFUTES 不进正面支撑 + shadow 消费同一评估 ────────

_REFUTES_CLAIM_BODY = "Company reports no new order."


def _refutes_input():
    c, d = _make_claim(_REFUTES_CLAIM_BODY, "公司没有新订单",
                       negation_flag=True, relation="REFUTES")
    return [{"claim": c, "documents": [d]}]


def test_n2_refutes_never_positive_support():
    """N2：relation=REFUTES 的「没有新订单」不得成为 real_exposure/change_to_profit
    的正面支撑——对应命题 UNKNOWN（评估不 TRUE）。"""
    bundle = ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                   documents=_refutes_input())
    mid = bundle.assessments["MID"]
    by_type = {a["proposition_type"]: a for a in mid["required_assertions"]}
    assert by_type["real_exposure"]["evaluation"] == "UNKNOWN"
    assert by_type["change_to_profit"]["evaluation"] == "UNKNOWN"
    assert mid["status"] == "UNESTABLISHED"


def test_n2_shadow_consumes_same_assessment_not_rejudge(tmp_path):
    """N2：草稿进 shadow 后状态与正式评估一致（UNESTABLISHED）——不再经
    facts+refs 简化判断救成 VALID。（as_of 用当下——影子消费核对含未来时点检查，
    固定历史时点会让评估成为「未来评估」被判待复核，时钟敏感。）"""
    store = AssessmentStore(tmp_path / "research")
    bundle = ResearchService().run("600000", as_of=datetime.now().astimezone(),
                                   evidence_records=[],
                                   documents=_refutes_input(), assessment_store=store)
    plan = SimpleNamespace(**bundle.plan_drafts[0])
    assert plan.assessment_id
    assert _thesis_status_for(plan, "MID", "600000",
                              assessment_store=store).value == \
        bundle.assessments["MID"]["status"]


def test_n2_shadow_without_store_is_conservative(tmp_path):
    """评估未持久化/找不到 → shadow 明确待复核（REVIEW_REQUIRED），绝不 VALID。"""
    bundle = ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                   documents=_refutes_input())
    plan = SimpleNamespace(**bundle.plan_drafts[0])
    status = _thesis_status_for(plan, "MID", "600000",
                                assessment_store=AssessmentStore(tmp_path / "r"))
    assert status.value == "REVIEW_REQUIRED"


def test_shadow_legacy_plan_without_assessment_degrades(tmp_path):
    """旧计划（无 assessment_id）：facts+refs 简化判断退役——即便引用非空也
    UNESTABLISHED（计划文字保留，降级研究不销毁资料）。"""
    from types import SimpleNamespace as _NS
    plan = _NS(plan_id="p", facts_observed=["6月订单环比+30%"],
               fact_evidence_refs={"6月订单环比+30%": ["cninfo://ann/123"]},
               assessment_id="", snapshot_id="")
    status = _thesis_status_for(plan, "MID", "600519",
                                assessment_store=AssessmentStore(tmp_path / "r"))
    assert status.value == "UNESTABLISHED"


def test_shadow_mismatched_assessment_is_review(tmp_path):
    """评估实体错配（security/horizon/snapshot 不符）→ REVIEW_REQUIRED 待复核。"""
    from src.core.research import ThesisAssessment
    store = AssessmentStore(tmp_path / "research")
    asm = ThesisAssessment(thesis_id="t", horizon="MID", security_id="000002",
                           snapshot_id="snap-x", status=ThesisStatus.VALID,
                           method_version="r3.assertion_v1",
                           evaluated_as_of=AS_OF)
    aid = store.save(asm)
    from types import SimpleNamespace as _NS
    plan = _NS(plan_id="p", assessment_id=aid, snapshot_id="snap-x")
    assert _thesis_status_for(plan, "MID", "600519", assessment_store=store).value == "REVIEW_REQUIRED"


# ── 4. checkpoint 合法建立渠道 ────────────────────────────

def _typed_claims_bundle(service, store=None, checkpoints=()):
    """exposure + order 两类已核验主张的合成资料包（带正文可定位摘录）。"""
    exp_c, exp_d = _make_claim("公司公告：氮化镓快充业务收入占比 40%。",
                               "公司氮化镓业务真实暴露（业务收入占比 40%）",
                               event_type="exposure", value=40, unit="%")
    ord_c, ord_d = _make_claim("公司公告：签订 900 万元销售订单。",
                               "公司获得 900 万元订单", value=900, unit="万元")
    documents = [{"claim": exp_c, "documents": [exp_d]},
                 {"claim": ord_c, "documents": [ord_d]}]
    return service.run("600000", as_of=AS_OF, evidence_records=[], documents=documents,
                       assessment_store=store, checkpoints=list(checkpoints))


def test_checkpoint_channel_requires_material_and_confirmation():
    """无 checkpoint 材料 → window_and_refutation 保持 UNKNOWN；有材料但用户未确认
    风险意愿 → 仍 UNKNOWN（不因 accept 自动通过）。"""
    svc = ResearchService()
    bundle = _typed_claims_bundle(svc)
    w = next(a for a in bundle.assessments["MID"]["required_assertions"]
             if a["proposition_type"] == "window_and_refutation")
    assert w["evaluation"] == "UNKNOWN"

    svc2 = ResearchService()
    unconfirmed = CheckpointCondition(
        security_id="600000", horizon="MID", proposition_type="window_and_refutation",
        description="2026-10-30 三季报披露核验订单转化",
        evidence_refs=[], derived_from="disclosure_schedule", user_confirmed=False)
    bundle2 = _typed_claims_bundle(svc2, checkpoints=[unconfirmed])
    w2 = next(a for a in bundle2.assessments["MID"]["required_assertions"]
              if a["proposition_type"] == "window_and_refutation")
    assert w2["evaluation"] == "UNKNOWN"


def test_checkpoint_with_verified_material_and_confirmation_true():
    """经核实材料（evidence_refs 全部已核验）+ 用户已确认风险意愿 → checkpoint 命题
    可合法 TRUE（条件不是已发生公司事实，不进 facts_observed）。"""
    exp_c, exp_d = _make_claim("公司公告：氮化镓快充业务收入占比 40%。",
                               "公司氮化镓业务真实暴露（业务收入占比 40%）",
                               event_type="exposure", value=40, unit="%")
    ord_c, ord_d = _make_claim("公司公告：签订 900 万元销售订单。",
                               "公司获得 900 万元订单", value=900, unit="万元")
    cp = CheckpointCondition(
        security_id="600000", horizon="MID", proposition_type="window_and_refutation",
        description="2026-10-30 三季报披露核验订单转化",
        evidence_refs=[exp_c.claim_id, ord_c.claim_id],
        derived_from="disclosure_schedule", user_confirmed=True)
    bundle = ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                   documents=[{"claim": exp_c, "documents": [exp_d]},
                                              {"claim": ord_c, "documents": [ord_d]}],
                                   checkpoints=[cp])
    w = next(a for a in bundle.assessments["MID"]["required_assertions"]
             if a["proposition_type"] == "window_and_refutation")
    assert w["evaluation"] == "TRUE", f"failures={w}"
    # 条件不进 facts_observed（草稿事实仍只来自已核验主张 statement）
    draft = bundle.plan_drafts[0]
    assert "2026-10-30" not in "".join(draft["facts_observed"])


def test_mid_fully_legal_pack_reaches_valid_and_downgrades_when_missing():
    """充分且合法的 MID 合成资料包（exposure 暴露 + order 变化 + 已确认检查点）
    → VALID；去掉任一必需命题依据 → 降级（不为让 VALID 出现而降门槛）。"""
    exp_c, exp_d = _make_claim("公司公告：氮化镓快充业务收入占比 40%。",
                               "公司氮化镓业务真实暴露（业务收入占比 40%）",
                               event_type="exposure", value=40, unit="%")
    ord_c, ord_d = _make_claim("公司公告：签订 900 万元销售订单。",
                               "公司获得 900 万元订单", value=900, unit="万元")
    cp = CheckpointCondition(
        security_id="600000", horizon="MID", proposition_type="window_and_refutation",
        description="2026-10-30 三季报披露核验订单转化",
        evidence_refs=[exp_c.claim_id, ord_c.claim_id],
        derived_from="disclosure_schedule", user_confirmed=True)
    documents = [{"claim": exp_c, "documents": [exp_d]},
                 {"claim": ord_c, "documents": [ord_d]}]
    bundle = ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                   documents=documents, checkpoints=[cp])
    assert bundle.assessments["MID"]["status"] == "VALID", \
        f"MID 应合法 VALID: {bundle.assessments['MID']['unresolved_gaps']}"
    # LONG：估值区间能力未具备 → 诚实 UNESTABLISHED（不为覆盖而伪造能力）
    assert bundle.assessments["LONG"]["status"] == "UNESTABLISHED"
    # 缺 order（变化命题无依据）→ 降级
    bundle2 = ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                    documents=[{"claim": exp_c, "documents": [exp_d]}],
                                    checkpoints=[cp])
    assert bundle2.assessments["MID"]["status"] != "VALID"


def test_single_claim_binds_single_required_proposition():
    """一个订单不能同时证明业务纯度、利润兑现和长期优势——单主张至多绑定一个
    required 命题（本包无 exposure 主张 → real_exposure 不得被订单主张顶替）。"""
    ord_c, ord_d = _make_claim("公司公告：签订 900 万元销售订单。",
                               "公司获得 900 万元订单", value=900, unit="万元")
    bundle = ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                   documents=[{"claim": ord_c, "documents": [ord_d]}])
    by_type = {a["proposition_type"]: a for a in bundle.assessments["MID"]["required_assertions"]}
    assert by_type["change_to_profit"]["evaluation"] == "TRUE"
    assert by_type["real_exposure"]["evaluation"] == "UNKNOWN", \
        "订单主张不得同时冒充真实暴露"
    long_by_type = {a["proposition_type"]: a
                    for a in bundle.assessments["LONG"]["required_assertions"]}
    assert long_by_type["moat"]["evaluation"] == "UNKNOWN", \
        "订单主张不得冒充长期优势"


# ── 5. AssessmentStore：不可变 / 幂等 / 隔离 ───────────────

def test_assessment_store_idempotent_and_isolated(tmp_path):
    from src.core.research import ThesisAssessment
    from src.core.decision_contract import ThesisStatus
    store = AssessmentStore(tmp_path / "research")
    asm = ThesisAssessment(thesis_id="t", horizon="MID", security_id="600519",
                           snapshot_id="s1", status=ThesisStatus.UNESTABLISHED,
                           method_version="r3.assertion_v1", evaluated_as_of=AS_OF)
    aid1 = store.save(asm)
    aid2 = store.save(asm.model_copy())  # 同内容幂等（同 id 不覆盖）
    assert aid1 == aid2
    loaded = store.load(aid1)
    assert loaded is not None and loaded.status is ThesisStatus.UNESTABLISHED
    assert loaded.security_id == "600519"
    # 内容变 → 新 id（不可变：旧评估不被改写）
    asm2 = asm.model_copy(update={"status": ThesisStatus.VALID})
    assert store.assessment_id_of(asm2) != aid1
    # 坏 id / 不存在 → None（不崩）
    assert store.load("does-not-exist") is None
    assert store.load("") is None
    assert store.load("../../etc/passwd") is None


# ── 6. 因子绑定合格快照 ───────────────────────────────────

class _Cap:
    def __init__(self, value=1.0, status="OK"):
        self.value, self.status, self.note = value, status, ""
        self.unit = ""
        self.components = {"liability_component": 0.7}


def _run_with_caps(caps, bindings=None):
    ord_c, ord_d = _make_claim("公司公告：签订 900 万元销售订单。",
                               "公司获得 900 万元订单", value=900, unit="万元")
    return ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                 documents=[{"claim": ord_c, "documents": [ord_d]}],
                                 factor_capabilities=caps, factor_bindings=bindings)


def test_unbound_factor_capabilities_do_not_satisfy_requirements():
    """未绑定合格快照的因子（裸 raw dict 通道）不满足 evidence_requirements——
    gaps 明示「未绑定合格快照」。"""
    bundle = _run_with_caps({"balance_risk_v1": _Cap()})
    assert any("未绑定合格快照" in g for g in bundle.gaps), bundle.gaps
    long_by_type = {a["proposition_type"]: a
                    for a in bundle.assessments["LONG"]["required_assertions"]}
    assert long_by_type["capital_constraint"]["evaluation"] == "UNKNOWN"


def test_bound_factor_capabilities_satisfy_requirements():
    """绑定与 run 同快照/实体/时点的能力正常满足要求。"""
    ord_c, ord_d = _make_claim("公司公告：签订 900 万元销售订单。",
                               "公司获得 900 万元订单", value=900, unit="万元")
    probe = ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                  documents=[{"claim": ord_c, "documents": [ord_d]}])
    snap_id = probe.snapshot_id
    bundle = _run_with_caps(
        {"balance_risk_v1": _Cap()},
        bindings={"balance_risk_v1": {"snapshot_id": snap_id, "security_id": "600000",
                                      "as_of": AS_OF}})
    long_by_type = {a["proposition_type"]: a
                    for a in bundle.assessments["LONG"]["required_assertions"]}
    assert long_by_type["capital_constraint"]["evidence_requirements"] == ["balance_risk_v1"]
    # 要求满足 + 命题其他依据缺 → 命题仍 UNKNOWN，但缺口清单不再点名 balance_risk_v1
    assert not any("balance_risk_v1" in g and "缺能力" in g for g in bundle.gaps), bundle.gaps


# ── 7. 缓存全量键 + 返回副本防污染 ─────────────────────────

def test_cache_key_includes_factor_components_and_rule_versions():
    """只改负债分量（components）即新 run_id；规则版本进键。"""
    b1 = _run_with_caps({"balance_risk_v1": _Cap()})
    cap2 = _Cap()
    cap2.components = {"liability_component": 0.9}  # 只改分量
    b2 = _run_with_caps({"balance_risk_v1": cap2})
    assert b1.run_id != b2.run_id, "负债分量变化必须使缓存失效"


def _run_with_caps_on(svc, caps, bindings=None):
    ord_c, ord_d = _make_claim("公司公告：签订 900 万元销售订单。",
                               "公司获得 900 万元订单", value=900, unit="万元")
    return svc.run("600000", as_of=AS_OF, evidence_records=[],
                   documents=[{"claim": ord_c, "documents": [ord_d]}],
                   factor_capabilities=caps, factor_bindings=bindings)


def test_cached_bundle_copy_not_pollutable():
    """缓存命中返回深副本——消费者原地修改不得污染缓存与后续命中。"""
    svc = ResearchService()
    x1 = _run_with_caps_on(svc, {})
    fresh_gaps = list(x1.gaps)
    x1.gaps.append("__noise__")
    x1.security_id = "999999"
    x2 = _run_with_caps_on(svc, {})  # 同 svc 同输入 → 走缓存
    assert x2 is not x1
    assert "__noise__" not in x2.gaps, "缓存命中不得被消费者污染"
    assert x2.security_id == "600000"
    assert x2.gaps == fresh_gaps


# ── 8. 服务草稿带真实 assessment_id（store 持久化）──────────

def test_service_persists_assessment_and_draft_references_it(tmp_path):
    store = AssessmentStore(tmp_path / "research")
    bundle = _typed_claims_bundle(ResearchService(), store=store)
    for d in bundle.plan_drafts:
        assert d["assessment_id"].startswith("asm_")
        assert store.load(d["assessment_id"]) is not None, "草稿引用的评估已持久化"


# ── P1/P2 审查修复回归（J0 监督审查：声明绕过/实体错配/旧格式 id）──────

def test_declared_scope_without_components_capped():
    """P1：显式声明 verification_scope 但组件不齐——自报范围不构成绕过
    （数值型：无 value 不得 FACT_CHECKED；否定型：flag 未置真 + 摘录语义相反
    不得 FACT_CHECKED）。"""
    # 数值型声明 + 无数值组件
    c, d = _make_claim("公司公告：签订 900 万元销售订单。", "公司获得大额订单",
                       verification_scope="typed_numeric_event")
    r = verify_claim_tiered(c, [d], as_of=AS_OF)
    assert r.level is not VerificationLevel.FACT_CHECKED
    assert r.level is VerificationLevel.EXCERPT_GROUNDED
    # 否定型声明 + flag 未置真 + 摘录语义相反
    c2, d2 = _make_claim("公司公告：签订 900 万元销售订单。", "公司取消订单",
                         event_type="other", verification_scope="typed_negation")
    r2 = verify_claim_tiered(c2, [d2], as_of=AS_OF)
    assert r2.level is not VerificationLevel.FACT_CHECKED


def test_checkpoint_of_other_security_does_not_establish():
    """P2：异证券的 checkpoint 不得建立本证券命题（实体错配不通过）。"""
    exp_c, exp_d = _make_claim("公司公告：氮化镓快充业务收入占比 40%。",
                               "公司氮化镓业务真实暴露（业务收入占比 40%）",
                               event_type="exposure", value=40, unit="%")
    ord_c, ord_d = _make_claim("公司公告：签订 900 万元销售订单。",
                               "公司获得 900 万元订单", value=900, unit="万元")
    cp = CheckpointCondition(
        security_id="000002",  # 异证券
        horizon="MID", proposition_type="window_and_refutation",
        description="2026-10-30 三季报披露核验订单转化",
        evidence_refs=[exp_c.claim_id, ord_c.claim_id],
        derived_from="disclosure_schedule", user_confirmed=True)
    bundle = ResearchService().run("600000", as_of=AS_OF, evidence_records=[],
                                   documents=[{"claim": exp_c, "documents": [exp_d]},
                                              {"claim": ord_c, "documents": [ord_d]}],
                                   checkpoints=[cp])
    w = next(a for a in bundle.assessments["MID"]["required_assertions"]
             if a["proposition_type"] == "window_and_refutation")
    assert w["evaluation"] == "UNKNOWN", "异证券检查点不得建立本证券命题"


def test_shadow_rejects_empty_security_or_wrong_method_version(tmp_path):
    """P2：评估 security_id 为空 / method_version 不符 → 待复核（不冒充当前语义）。"""
    from src.core.research import ThesisAssessment
    store = AssessmentStore(tmp_path / "research")
    from types import SimpleNamespace as _NS
    # 空 security 的 VALID 评估——任意证券不得消费
    asm_empty = ThesisAssessment(thesis_id="t", horizon="MID", security_id="",
                                 snapshot_id="snap-x", status=ThesisStatus.VALID,
                                 method_version="r3.assertion_v1",
                                 evaluated_as_of=datetime.now().astimezone())
    plan = _NS(plan_id="p", assessment_id=store.save(asm_empty), snapshot_id="snap-x")
    assert _thesis_status_for(plan, "MID", "999999", assessment_store=store).value == \
        "REVIEW_REQUIRED"
    # 旧方法版本的评估——不冒充当前语义
    asm_old = ThesisAssessment(thesis_id="t", horizon="MID", security_id="600519",
                               snapshot_id="snap-x", status=ThesisStatus.VALID,
                               method_version="r2.ancient_v0",
                               evaluated_as_of=datetime.now().astimezone())
    plan2 = _NS(plan_id="p", assessment_id=store.save(asm_old), snapshot_id="snap-x")
    assert _thesis_status_for(plan2, "MID", "600519", assessment_store=store).value == \
        "REVIEW_REQUIRED"


def test_shadow_legacy_format_assessment_id_degrades(tmp_path):
    """P2：R3 旧草稿合成串（{horizon}:{snapshot_id}）不是评估库 id——按旧计划
    降级 UNESTABLISHED（不是待复核；文档语义与实现对齐）。"""
    from types import SimpleNamespace as _NS
    plan = _NS(plan_id="p", assessment_id="MID:snap_legacy", snapshot_id="snap_legacy",
               facts_observed=["x"], fact_evidence_refs={"x": ["ref-1"]})
    status = _thesis_status_for(plan, "MID", "600519",
                                assessment_store=AssessmentStore(tmp_path / "r"))
    assert status.value == "UNESTABLISHED"


# ── P1–P4 回归锚（保持不回归的快速锚点）──────────────────

def test_p1_blank_facts_still_unestablished():
    assert assess_thesis(ThesisRecord(thesis_id="p", horizon="LONG", beneficiary_business="",
                                      profit_mechanism="", facts_observed=["   "])).value \
        == "UNESTABLISHED"
