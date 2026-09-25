"""投资逻辑研究层（plan/fusion F5，DESIGN §5.2）：证据到投资逻辑状态。

ThesisRecord 最小集：受益业务、收益机制、预期实现区间、已发生/尚待发生事实、
关键反证、估值假设、下一可验证节点。状态转移由**证据与明确条件**驱动——
AI 只提取候选事实、提出解释，不自己改变已接受的风险界限（DESIGN §5.2 原文；
AI 证据接入是 F6，本模块先行落地确定性骨架）。

失效条件求值（invalidate_if）：三值 TruthValue——UNKNOWN 不可自动变 FALSE（F0 契约）；
评估输入由调用方给（结构化可计算条件或人工确认结果），本模块不猜。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from src.core.decision_contract import ThesisStatus, TruthValue


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


def assess_thesis(thesis: ThesisRecord,
                  invalidation_value: Optional[TruthValue] = None,
                  counter_evidence_verified: bool = False) -> ThesisStatus:
    """证据驱动的逻辑状态转移（AI 不改状态；状态由事实与明确条件驱动）。

    规则（DESIGN §5.2 + §4.2）：
    - 失效条件求值 TRUE 或 已核实反证存在 → INVALID（技术反弹不能抵消）
    - 失效条件求值 UNKNOWN（给了规则但未验证）→ REVIEW_REQUIRED（需要核对）
    - invalidation_value=None（未给规则）或 FALSE → 按事实评估：
      有关键反证未核实 → REVIEW_REQUIRED；有已观察事实 → VALID；否则 UNESTABLISHED

    注意：状态只是**评估结果**；真正进入决策表的是调用方把 status 传给
    decision_policy.HorizonFacts.thesis_status。
    """
    if invalidation_value is TruthValue.TRUE or counter_evidence_verified:
        return ThesisStatus.INVALID
    if invalidation_value is TruthValue.UNKNOWN:
        return ThesisStatus.REVIEW_REQUIRED
    if thesis.counter_evidence:
        return ThesisStatus.REVIEW_REQUIRED
    if thesis.facts_observed:
        return ThesisStatus.VALID
    return ThesisStatus.UNESTABLISHED


def mid_to_long_requires_new_assessment(thesis: ThesisRecord) -> bool:
    """MID 转 LONG 需要新的长期资格评估与用户确认（DESIGN §4.1：浮亏和希望回本
    不是有效理由）。本函数只是显式断言该门槛的语义锚——转移本身是用户操作，
    不在本模块自动发生。"""
    return thesis.horizon == "MID"
