"""投资逻辑研究层（plan/fusion F5 + iteration2 R3，DESIGN §5.2 + RESEARCH_LOOP §2）：
证据到投资逻辑状态。

ThesisRecord 最小集：受益业务、收益机制、预期实现区间、已发生/尚待发生事实、
关键反证、估值假设、下一可验证节点。状态转移由**证据与明确条件**驱动——
AI 只提取候选事实、提出解释，不自己改变已接受的风险界限（DESIGN §5.2 原文）。

失效条件求值（invalidate_if）：三值 TruthValue——UNKNOWN 不可自动变 FALSE（F0 契约）；
评估输入由调用方给（结构化可计算条件或人工确认结果），本模块不猜。

R3 命题级评估（RESEARCH_LOOP §2）：ThesisAssertion（可验证命题，按周期模板组建）→
ThesisAssessment（周期研究评估；状态优先级链）——shadow/CLI/服务层统一消费本模块，
不再自写资格判断。R0 资格门（fact_evidence_refs）保持：无证据引用的文本事实
不作为逻辑成立依据。
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.decision_contract import ThesisStatus, TruthValue


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class ThesisRecord(BaseModel):
    """一条投资逻辑（每个逻辑最少包括 DESIGN §5.2 列举的七要素）。"""

    model_config = ConfigDict(extra="allow")  # ADR-F09

    thesis_id: str
    security_id: str = ""
    horizon: str = Field(description="MID/LONG（与气宗/剑宗 legacy_mode 正交）")
    beneficiary_business: str = Field(description="受益业务（真实暴露，不是概念蹭边）")
    profit_mechanism: str = Field(description="收益机制（需求/订单→业务占比→产能/毛利→财报验证）")
    expected_window: str = Field(default="", description="预期实现区间（研究语境，不是到天数清仓）")
    facts_observed: list[str] = Field(default_factory=list, description="已经发生的事实（带证据引用）")
    facts_pending: list[str] = Field(default_factory=list, description="尚待发生的事实（下一验证节点）")
    fact_evidence_refs: dict[str, list[str]] = Field(
        default_factory=dict,
        description="事实→可解析证据引用（键=去空白事实原文；值=evidence_id/原文hash/URI）。"
                    "R0 资格门：无可解析引用的事实文本不作为逻辑成立依据（A03 止血）；R3 自动研究正式化")
    counter_evidence: list[str] = Field(default_factory=list, description="关键反证")
    valuation_assumption: str = Field(default="", description="估值假设（可审计情景；LONG 用）")
    next_checkpoint: str = Field(default="", description="下一可验证节点（财报/交付/事件）")
    status: ThesisStatus = Field(default=ThesisStatus.UNESTABLISHED)
    # MID 例子（DESIGN §5.2）：需求/订单变化→公司对应业务占比→产能/毛利转化→财报或交付验证


def evaluate_invalidation_rules(rules) -> TruthValue:
    """失效条件组求值：任一规则 evaluation=TRUE → TRUE（逻辑失效）；
    否则任一 UNKNOWN → UNKNOWN（不自动变 FALSE）；全 FALSE → FALSE。"""
    values = [r.evaluation for r in rules]
    if TruthValue.TRUE in values:
        return TruthValue.TRUE
    if TruthValue.UNKNOWN in values:
        return TruthValue.UNKNOWN
    return TruthValue.FALSE


def fact_has_resolvable_reference(fact: str, refs: dict[str, list[str]],
                                  resolver=None) -> bool:
    """事实是否带**实存可解析**的证据引用（R0 资格门 + J0/N3 收紧）。

    规则：键=去空白事实原文**精确匹配**；值中引用串去空白后非空，且必须经
    resolver 实存解析为真（引用指向的证据库中确实存在、归属正确）。
    resolver=None（调用方没有证据库）时一律不可解析——「非空」不等于「可解析」，
    旧兼容路径只能降级（J0 验收：N3 不存在的 ID 不得 VALID）。"""
    key = str(fact).strip()
    if not key:
        return False
    for r in (refs.get(key) or []):
        ref = str(r).strip()
        if ref and resolver is not None and resolver(ref):
            return True
    return False


def assess_thesis(thesis: ThesisRecord,
                  invalidation_value: Optional[TruthValue] = None,
                  counter_evidence_verified: bool = False, *,
                  evidence_resolver=None) -> ThesisStatus:
    """证据驱动的逻辑状态转移（AI 不改状态；状态由事实与明确条件驱动）。

    规则（DESIGN §5.2 + §4.2 + R0 资格止血）：
    - 失效条件求值 TRUE 或 已核实反证存在 → INVALID（技术反弹不能抵消）
    - 失效条件求值 UNKNOWN（给了规则但未验证）→ REVIEW_REQUIRED（需要核对）
    - invalidation_value=None（未给规则）或 FALSE → 按事实评估：
      有关键反证未核实 → REVIEW_REQUIRED；
      有**带可解析证据引用**的已观察事实 → VALID；
      否则（含无引用的纯文本事实/空白串）→ UNESTABLISHED

    R0 资格门（A03，架构师裁决②）：「一条 facts 文本即 VALID」不成立——VALID 需要
    fact_evidence_refs 里可解析的证据引用；用户确认的是目标与风险意愿，不是把一句话
    变成客观事实。兼容读策略：旧序列化记录无该字段 → 默认 {} → 保守 UNESTABLISHED。
    R3 自动研究给事实挂证据后这是正式路径，不是永久封锁。
    J0/N3 收紧：「可解析」须接证据库实存解析（evidence_resolver）——无 resolver
    一律不 VALID（旧兼容路径只能降级）。

    注意：状态只是**评估结果**；真正进入决策表的是调用方把 status 传给
    decision_policy.HorizonFacts.thesis_status。
    """
    if invalidation_value is TruthValue.TRUE or counter_evidence_verified:
        return ThesisStatus.INVALID
    if invalidation_value is TruthValue.UNKNOWN:
        return ThesisStatus.REVIEW_REQUIRED
    if thesis.counter_evidence:
        return ThesisStatus.REVIEW_REQUIRED
    if any(fact_has_resolvable_reference(f, thesis.fact_evidence_refs,
                                         resolver=evidence_resolver)
           for f in thesis.facts_observed):
        return ThesisStatus.VALID
    return ThesisStatus.UNESTABLISHED


def mid_to_long_requires_new_assessment(thesis: ThesisRecord) -> bool:
    """MID 转 LONG 需要新的长期资格评估与用户确认（DESIGN §4.1：浮亏和希望回本
    不是有效理由）。本函数只是显式断言该门槛的语义锚——转移本身是用户操作，
    不在本模块自动发生。"""
    return thesis.horizon == "MID"


# ──────────────── R3 命题级评估（iteration2 RESEARCH_LOOP §2：从文字列表到可验证命题）──

class AssertionEvaluation(str, Enum):
    TRUE = "TRUE"        # 该命题在冻结证据和方法下被支持——不是未来一定兑现
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class SupportBinding(BaseModel):
    """支持/反驳绑定（J0b：关联是显式可审计数据，不再是 event_type 隐式路由）。

    DELIVERY_PLAN J0：支持/反驳要绑定 assertion_id + evidence_ids + relation +
    rule_version + justification；relation=REFUTES/否定主张绝不无条件进入正面支撑。"""

    model_config = ConfigDict(extra="allow")

    assertion_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    relation: Literal["SUPPORTS", "REFUTES"] = "SUPPORTS"
    rule_version: str = Field(default="", description="绑定规则版本（保守映射/检查点渠道各有版本）")
    justification: str = Field(default="", description="绑定依据（人话——为什么这些证据支撑该命题）")


class CheckpointCondition(BaseModel):
    """检查点条件（J0b：window_and_refutation / longterm_invalidation 的合法建立渠道）。

    从经核实事件日历/披露安排提出检查点，系统生成可验证条件，用户确认风险意愿；
    这些条件**不是已发生公司事实**——不进 facts_observed；没有材料时保持未知，
    不因用户 accept 自动通过（DELIVERY_PLAN J0 原文）。"""

    model_config = ConfigDict(extra="allow")

    condition_id: str = Field(default_factory=_uuid_hex)
    security_id: str = ""
    horizon: str = Field(default="", description="MID/LONG；空=两周期通用")
    proposition_type: str = Field(description="window_and_refutation / longterm_invalidation")
    description: str = Field(description="可验证条件（何时验证、什么事实出现会推翻）")
    evidence_refs: list[str] = Field(default_factory=list,
                                     description="生成该条件的经核实材料（claim/evidence id）")
    derived_from: Literal["verified_calendar", "disclosure_schedule",
                          "user_confirmed_risk"] = Field(
        description="条件来源（经核实日历/披露安排/用户确认的风险意愿）")
    user_confirmed: bool = Field(default=False,
                                 description="用户已确认风险意愿——缺省 False 不自动通过")


class ThesisAssertion(BaseModel):
    """一条可验证命题（研究评估的最小单元——不再是「一堆事实文字」）。

    proposition_type 示例：real_exposure(真实受益)/change_to_profit(变化与兑现)/
    window_and_refutation(时窗与反证)/price_context(价格与容量背景)（MID）；
    cash_sustainability(盈利与现金持续)/capital_constraint(资本约束)/moat(经营优势)/
    valuation_assumption(估值区间及反向)/longterm_invalidation(长期失效)（LONG）。"""

    model_config = ConfigDict(extra="allow")  # ADR-F09

    assertion_id: str = Field(default_factory=_uuid_hex)
    security_id: str = ""
    horizon: str = Field(description="MID/LONG——命题归属周期")
    thesis_version: str = Field(default="", description="所属研究版本（run_id/assessment 版本）")
    proposition_type: str = Field(description="命题类型（模板见 RESEARCH_LOOP §2）")
    description: str = Field(description="命题内容（可判定，不是形容词）")
    importance: Literal["required", "supporting"] = Field(default="required")
    evidence_requirements: list[str] = Field(
        default_factory=list,
        description="所需证据/因子能力ID（引用 R4 因子能力或 DATA_COVERAGE 类别——"
                    "按能力ID匹配，非空数字不自动满足能力，A08）")
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    support_bindings: list[dict] = Field(
        default_factory=list,
        description="支持绑定审计记录（SupportBinding dump——J0b：关联显式化，"
                    "含 relation/rule_version/justification）")
    evaluation: AssertionEvaluation = Field(default=AssertionEvaluation.UNKNOWN)
    evaluation_rule_version: str = Field(default="", description="求值方法版本")
    evaluated_as_of: Optional[datetime] = Field(default=None, description="求值截止时点")

    @field_validator("evaluated_as_of")
    @classmethod
    def _aware_evaluated(cls, v):
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("evaluated_as_of 必须带时区")
        return v


class ThesisAssessment(BaseModel):
    """一次周期研究评估（research.assess_thesis 的命题级升级；shadow/CLI 消费唯一入口）。"""
    model_config = ConfigDict(extra="allow")

    thesis_id: str = Field(default="", description="关联 ThesisRecord/计划 thesis_id")
    security_id: str = Field(default="", description="主体证券（J0b：shadow 消费核对用）")
    horizon: str = Field(description="MID/LONG")
    snapshot_id: str = Field(default="", description="评估所依据的证据快照（可复现）")
    status: ThesisStatus = Field(default=ThesisStatus.UNESTABLISHED)
    required_assertions: list[ThesisAssertion] = Field(default_factory=list)
    supporting_assertions: list[ThesisAssertion] = Field(default_factory=list)
    unresolved_gaps: list[str] = Field(default_factory=list, description="具体缺口（人话——缺哪个证据/哪个命题无法建立）")
    material_counter_evidence: list[str] = Field(default_factory=list, description="未核实的重大反证（→REVIEW_REQUIRED）")
    invalidation_results: list[dict] = Field(default_factory=list, description="失效条件求值记录 {rule_id, condition, evaluation}")
    next_checks: list[str] = Field(default_factory=list, description="下一可验证节点")
    method_version: str = Field(default="", description="评估方法版本")
    evaluated_as_of: Optional[datetime] = None

    @field_validator("evaluated_as_of")
    @classmethod
    def _aware_eval(cls, v):
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("evaluated_as_of 必须带时区")
        return v


def evaluate_assertion(assertion: ThesisAssertion, *, available_capabilities: dict,
                       verified_evidence_ids: set, as_of) -> ThesisAssertion:
    """单命题求值（确定性；按能力ID匹配证据/因子，A08：非空数字≠能力满足）。

    available_capabilities: {能力ID/evidence_requirement: FactorResult或等价dict}——
    状态 OK 才计满足；verified_evidence_ids: 已核验（FACT_CHECKED 级）证据/claim id 集。
    求值规则版本 r3.assertion_v1：
    - 有反证引用未核实 → UNKNOWN（升级到评估层处理）
    - 能力要求有缺失 或 引用的支撑证据未核验 → UNKNOWN（缺什么是缺口，不猜）
    - 支撑证据已核验（或能力要求全部满足且非空）→ TRUE——「被支持」指冻结证据与
      方法下成立，不是未来一定兑现（RESEARCH_LOOP §2）
    - 两者皆无 → UNKNOWN（没有任何依据的命题不自动成立）"""
    if getattr(as_of, "tzinfo", None) is None:
        raise ValueError("evaluated_as_of 必须带时区（naive 拒收）")
    missing = [req for req in assertion.evidence_requirements
               if not _requirement_satisfied(req, available_capabilities)]
    unverified = [rid for rid in assertion.supporting_evidence_ids
                  if rid not in verified_evidence_ids]
    if assertion.counter_evidence_ids:
        evaluation = AssertionEvaluation.UNKNOWN
    elif missing or unverified:
        evaluation = AssertionEvaluation.UNKNOWN
    elif assertion.supporting_evidence_ids or assertion.evidence_requirements:
        evaluation = AssertionEvaluation.TRUE
    else:
        evaluation = AssertionEvaluation.UNKNOWN
    return assertion.model_copy(update={
        "evaluation": evaluation,
        "evaluation_rule_version": "r3.assertion_v1",
        "evaluated_as_of": as_of,
    })


def _requirement_satisfied(req: str, available_capabilities: dict) -> bool:
    """能力要求满足判定：能力ID 存在且状态 OK（A08：值非空不算——状态/口径对才算；
    dict 形态（ResearchBundle.factors 落盘态）同样只认 status=OK）。"""
    cap = available_capabilities.get(req)
    if cap is None:
        return False
    status = getattr(cap, "status", None)
    if status is not None:
        return status == "OK"
    if isinstance(cap, dict):
        return cap.get("status") == "OK"
    return False


def assess_thesis_by_assertions(assess_assertions: list[ThesisAssertion], *,
                                invalidation_value=None,
                                verified_counter_evidence: bool = False,
                                unverified_material_counter: bool = False) -> ThesisStatus:
    """命题级状态转移（RESEARCH_LOOP §2 优先级链；研究评估唯一入口的正式实现）：

    0. 空命题列表 → UNESTABLISHED（没有任何命题的论文不成立——边界防「空洞为真」）
    1. 已核实的预设失效条件成立（invalidation TRUE）→ INVALID（技术反弹不能抵消）
    2. 失效条件 UNKNOWN（给了规则未验证）→ REVIEW_REQUIRED
    3. 关键反证未核实（unverified_material_counter）→ REVIEW_REQUIRED（先于
       UNESTABLISHED——重大反证待人工核对是更紧急状态）
    4. 必需命题无法建立（任一 required=UNKNOWN）→ UNESTABLISHED
    5. 必需命题被反驳（required=FALSE）→ REVIEW_REQUIRED
       （FALSE 的产生渠道：人工核实反证后由评估/录入方显式写入 evaluation=FALSE——
       确定性求值只产 TRUE/UNKNOWN，R8 接线人工通道时落实）
    6. 全部必需命题 TRUE 且无未解决重大反证 → VALID
    周期隔离：MID/LONG 各自的 required_assertions 独立求值——MID VALID 不使 LONG
    自动 VALID（共享事实、分别评估，架构师裁决③）。
    """
    from src.core.decision_contract import TruthValue as _TV, ThesisStatus as _TS
    if not assess_assertions:
        return _TS.UNESTABLISHED
    if invalidation_value is _TV.TRUE or verified_counter_evidence:
        return _TS.INVALID
    if invalidation_value is _TV.UNKNOWN:
        return _TS.REVIEW_REQUIRED
    if unverified_material_counter:
        return _TS.REVIEW_REQUIRED
    if any(a.importance == "required" and a.evaluation is AssertionEvaluation.UNKNOWN
           for a in assess_assertions):
        return _TS.UNESTABLISHED
    if any(a.importance == "required" and a.evaluation is AssertionEvaluation.FALSE
           for a in assess_assertions):
        return _TS.REVIEW_REQUIRED  # 必需命题被反驳——重大反证待人工
    return _TS.VALID
