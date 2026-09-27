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

import hashlib
import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.decision_contract import ThesisStatus, TruthValue


def _uuid_hex() -> str:
    return uuid.uuid4().hex


def _assertion_content_id(security_id: str, horizon: str, proposition_type: str,
                          description: str, importance: str,
                          evidence_requirements: list, thesis_version: str) -> str:
    """命题 id 内容派生（K0c/A1：同内容同 id——评估唯一真值 id 由此确定化；
    随机 uuid 会让同内容评估每次产生不同 assessment_id，重跑幂等比较面失效）。"""
    payload = {"security_id": security_id, "horizon": horizon,
               "proposition_type": proposition_type, "description": description,
               "importance": importance,
               "evidence_requirements": list(evidence_requirements),
               "thesis_version": thesis_version}
    return "asr_" + hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str)
        .encode("utf-8")).hexdigest()[:16]


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

    assertion_id: str = Field(
        default="", description="命题 id（缺省由内容派生——同内容同 id，跨 run 稳定；"
                                "K0c/A1 评估唯一真值 id 确定化的前提）")
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
    evaluation_note: str = Field(
        default="", description="求值说明（K0b/D5：UNKNOWN 的具体缺口或规则判定依据——人话，"
                                "随评估落盘；「能力可计算」不再自动等于「命题成立」）")
    evaluated_as_of: Optional[datetime] = Field(default=None, description="求值截止时点")

    @field_validator("evaluated_as_of")
    @classmethod
    def _aware_evaluated(cls, v):
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("evaluated_as_of 必须带时区")
        return v

    @model_validator(mode="after")
    def _derive_content_id(self):
        if not self.assertion_id:
            self.assertion_id = _assertion_content_id(
                self.security_id, self.horizon, self.proposition_type,
                self.description, self.importance, self.evidence_requirements,
                self.thesis_version)
        return self


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


# ──────────────── K0b/D5：命题规则（能力可计算 ≠ 命题成立）────────────────
#
# 每个可自动建立的必需命题登记确定性规则（rule_id/version/输入/适用范围/判定条件）；
# 规则未登记的命题一律 UNKNOWN（无规则默认 UNKNOWN——阈值有效性未验证时不凭空
# 冻结投资阈值，研究性判断保留在评估层与人工通道）。
ASSERTION_RULE_VERSION = "k0b.assertion_v1"
CASH_SUSTAINABILITY_MIN_PERIODS = 2


def _cap_status(cap) -> Optional[str]:
    """能力状态读取（FactorResult 形态或落盘 dict 形态同口径）。"""
    if cap is None:
        return None
    if isinstance(cap, dict):
        return cap.get("status")
    return getattr(cap, "status", None)


def _cap_provenance(cap) -> dict:
    if cap is None:
        return {}
    if isinstance(cap, dict):
        prov = cap.get("provenance")
        return prov if isinstance(prov, dict) else {}
    prov = getattr(cap, "provenance", None)
    return prov if isinstance(prov, dict) else {}


def _rule_cash_sustainability(assertion: ThesisAssertion,
                              available_capabilities: dict) -> tuple:
    """cash_sustainability 规则（rule_id=k0b.rule.cash_sustainability）。

    语义：盈利与现金创造的**多期**持续性——需要 ≥2 个报告期、方向一致
    （观察值 > 0）的 CFO 转化与 ROE 序列证据。单期观察只证明「该期可计算」，
    不得建立持续性（D5 主反例）；报告期序列由因子 provenance.periods_seen 提供，
    逐期方向由 values_by_period 提供（当前因子形态为单期观察 → 恒 UNKNOWN 并
    解释缺口——诚实降级，未来因子升格为序列后规则自动可建立）。
    阈值有效性未验证：本规则只判方向与期数，不做阈值级实证声明。"""
    for fid in assertion.evidence_requirements:
        cap = available_capabilities.get(fid)
        status = _cap_status(cap)
        if status != "OK":
            return (AssertionEvaluation.UNKNOWN,
                    f"因子 {fid} 状态 {status or '缺失'}——不满足规则输入")
        prov = _cap_provenance(cap)
        seen = prov.get("periods_seen") or {}
        if isinstance(seen, dict):
            raw_periods = seen.get(_metric_of(fid)) or seen.get(fid) or []
        else:  # 兼容直接给列表的因子形态
            raw_periods = seen
        periods = sorted({str(p) for p in raw_periods if p})
        if len(periods) < CASH_SUSTAINABILITY_MIN_PERIODS:
            return (AssertionEvaluation.UNKNOWN,
                    f"多期持续性需要 ≥{CASH_SUSTAINABILITY_MIN_PERIODS} 个报告期方向一致的 "
                    f"{fid} 序列；当前仅 {len(periods)} 期观察（{periods or '无报告期'}）"
                    "——单期可计算≠持续性成立")
        values = prov.get("values_by_period") or {}
        if not values:
            return (AssertionEvaluation.UNKNOWN,
                    f"{fid} 缺逐期方向证据（values_by_period 未提供）——持续性未建立")
        # K0b 审查 P3-4：逐期证据须覆盖全部已见报告期（缺期=方向未证，不置 TRUE）
        missing_periods = [p for p in periods if str(p) not in {str(k) for k in values}]
        if missing_periods:
            return (AssertionEvaluation.UNKNOWN,
                    f"{fid} 报告期 {missing_periods[:3]} 缺方向证据——持续性未建立")
        bad = sorted(p for p, v in values.items()
                     if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0)
        if bad:
            return (AssertionEvaluation.UNKNOWN,
                    f"报告期 {bad[:3]} 的 {fid} 观察值方向不符（≤0）——持续性未建立")
    return (AssertionEvaluation.TRUE,
            f"≥{CASH_SUSTAINABILITY_MIN_PERIODS} 期方向一致的 "
            f"{'/'.join(assertion.evidence_requirements)} 序列（规则判定，非收益承诺）")


def _metric_of(factor_id: str) -> str:
    """能力ID → 主输入指标名（provenance.periods_seen 键；与 factor_compute 对齐）。"""
    return {"cash_conversion_v1": "CFOToNP", "roe_observed_v1": "roeAvg"}.get(
        factor_id, factor_id)


_PROPOSITION_RULES: dict[str, tuple] = {
    "cash_sustainability": ("k0b.rule.cash_sustainability", _rule_cash_sustainability),
}


def evaluate_assertion(assertion: ThesisAssertion, *, available_capabilities: dict,
                       verified_evidence_ids: set, as_of) -> ThesisAssertion:
    """单命题求值（确定性；按能力ID匹配证据/因子，A08：非空数字≠能力满足）。

    available_capabilities: {能力ID/evidence_requirement: FactorResult或等价dict}——
    状态 OK 只表示**可计算**（K0b/D5：能力状态≠命题成立）；verified_evidence_ids:
    已核验（FACT_CHECKED 级）证据/claim id 集。
    求值规则版本 k0b.assertion_v1：
    - 有反证引用未核实 → UNKNOWN（升级到评估层处理）
    - 能力要求有缺失 或 引用的支撑证据未核验 → UNKNOWN（缺什么是缺口，不猜）
    - 支撑证据已核验（claim 路径——J0b 显式绑定语义不变）→ TRUE
    - 仅能力要求满足 → **命题规则**判定（登记于 _PROPOSITION_RULES）；无规则默认
      UNKNOWN 并解释缺口——「可计算」不再自动等于「命题成立」
    - 两者皆无 → UNKNOWN（没有任何依据的命题不自动成立）"""
    if getattr(as_of, "tzinfo", None) is None:
        raise ValueError("evaluated_as_of 必须带时区（naive 拒收）")
    missing = [req for req in assertion.evidence_requirements
               if not _requirement_satisfied(req, available_capabilities)]
    unverified = [rid for rid in assertion.supporting_evidence_ids
                  if rid not in verified_evidence_ids]
    if assertion.counter_evidence_ids:
        evaluation = AssertionEvaluation.UNKNOWN
        note = "存在未核实反证引用——先人工核实再评估"
    elif missing or unverified:
        evaluation = AssertionEvaluation.UNKNOWN
        note = ("缺能力/证据: " + ", ".join(missing)) if missing \
            else "支撑证据未全部核验（verified_evidence_ids 不含引用）"
    elif assertion.supporting_evidence_ids:
        evaluation = AssertionEvaluation.TRUE
        note = "支撑证据已核验（claim 路径——J0b 显式绑定）"
    elif assertion.evidence_requirements:
        rule_entry = _PROPOSITION_RULES.get(assertion.proposition_type)
        if rule_entry is None:
            evaluation = AssertionEvaluation.UNKNOWN
            note = (f"能力可计算但命题 {assertion.proposition_type} 无确定性规则"
                    f"（{ASSERTION_RULE_VERSION} 注册表未登记）——能力状态≠命题成立")
        else:
            rule_id, rule_fn = rule_entry
            evaluation, note = rule_fn(assertion, available_capabilities)
            note = f"[{rule_id}] {note}"
    else:
        evaluation = AssertionEvaluation.UNKNOWN
        note = "命题无任何依据（无证据引用、无能力要求）"
    return assertion.model_copy(update={
        "evaluation": evaluation,
        "evaluation_note": note,
        "evaluation_rule_version": ASSERTION_RULE_VERSION,
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
