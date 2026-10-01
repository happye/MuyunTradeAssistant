"""周期策略决策表与 PlanV2（plan/fusion F5，DESIGN §4.1/§4.2）：中期与长期正交。

纪律（DESIGN/TASKS F5 原文约束）：
- **从上到下首个决定性条件优先**（有序裁决，不是加权投票）；每行输出可解释 reason
- 复核日到达只触发 REVIEW——**review_due 不是强制卖**；与 legacy 180/30 自然日到期
  清仓分开；legacy 气宗/剑宗/Chandelier 行为零改动（独立策略ID）
- **新增策略ID** fusion_mid_v1 / fusion_long_v1——不用旧 mode（气宗/剑宗）代替 horizon
- 旧 TradePlan 零写入即可继续读取（侧挂对象独立，不改 TradePlan 任何字段）
- 中期逻辑失效不能自动延长期限（invalidate_if 求值 TRUE → thesis INVALID → EXIT）
- 长期：普通短期破均线（技术噪声）不触发未约定退出；缺财务不发质量合格结论；
  未支持行业显式 NOT_APPLICABLE 限制
- 新计划需用户确认（accepted_at 非空）后才激活；未激活计划只产出 REVIEW 不出行动

shadow/opt_in 回滚口径：本模块是纯函数策略层，接入由调用方开关控制；版本对象保留，
不反写旧计划、不删除历史。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.decision_contract import (
    Blocker,
    DecisionPacket,
    DesiredAction,
    ExecutionStatus,
    Horizon,
    InvalidationRule,
    NextCheck,
    ResearchStatus,
    ThesisStatus,
    TruthValue,
)
from src.core.research import evaluate_invalidation_rules

POLICY_ID_MID = "fusion_mid_v1"
POLICY_ID_LONG = "fusion_long_v1"

# 决策表语义版本（行0–行10 的裁决规则本体）。行语义变更必须 bump——shadow 观察绑定
# 的 decision_rule_version 真实来源（L0/V1：不拿 policy_id 复制品冒充规则版本）。
# v2（M0，2026-10-01）：position_state 输入维度接入——数量账户人群（W1 实证）的
# 行2/5/6/8/9/10 裁决在 HELD+未知权重下翻转（WAIT→REVIEW/OPEN→HOLD 等）；
# position_state=None 路径与 v1 逐字节等价（旧行为兼容锚在
# test_horizon_backward_compat_without_state）。
DECISION_TABLE_VERSION = "v2"


class HorizonPlan(BaseModel):
    """PlanV2 侧挂对象（DESIGN §4.1）：保留原 TradePlan，本对象独立存在不改旧字段。"""

    model_config = ConfigDict(extra="allow")  # ADR-F09

    plan_id: str
    revision: int = 1
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    accepted_at: Optional[str] = Field(default=None, description="用户确认时点；None=未激活（只 REVIEW 不出行动）")
    horizon: Horizon = Field(description="MID/LONG——与气宗/剑宗 legacy_mode 正交")
    policy_id: str = Field(description=f"{POLICY_ID_MID} / {POLICY_ID_LONG}")
    intent: str = Field(description="投资逻辑简述（受益业务+收益机制）")
    thesis_id: str = Field(default="", description="绑定的 ThesisRecord ID（research.py）")
    required_evidence_refs: List[str] = Field(default_factory=list, description="必需证据类型（引用 DATA_COVERAGE 类别；允许时效在条目内以 'metric@N天' 形式登记）")
    entry_policy_id: Optional[str] = Field(default=None, description="入场策略ID（F7 计划编辑流程细化）")
    exit_policy_id: Optional[str] = Field(default=None, description="退出策略ID（行5 REDUCE 量级依此细化，F7）")
    risk_profile_id: Optional[str] = Field(default=None, description="用户风险档ID（F7 组合预算输入）")
    accepted_risk_limits: List[str] = Field(default_factory=list, description="用户明确接受的风险界限（禁止自动放宽）")
    review_triggers: List[str] = Field(default_factory=list, description="财报披露/新事件/指定复核日期（只触发 REVIEW）")
    invalidate_if: List[InvalidationRule] = Field(default_factory=list, description="结构化失效条件（UNKNOWN 不自动变 FALSE）")
    max_hold_until: Optional[str] = Field(default=None, description="催化/资金期限；仅确实有期限才填（不是到天数机械清仓）")
    buy_zone: Optional[str] = Field(default="", description="预先定义买价区间描述（LONG 入场判据的人话+可计算形式）")
    legacy_mode: Optional[str] = Field(default=None, description="仅兼容与对照（气宗/剑宗），不参与 horizon 裁决")
    # ── R3 研究契约字段（此前经 extra 隐式携带——正式声明；旧 JSON 缺省兼容）──
    fact_evidence_refs: dict = Field(default_factory=dict, description="事实→证据引用（R0 资格门数据源正式化：statement→claim_id/原文引用）")
    assessment_id: str = Field(default="", description="关联 ThesisAssessment（J0b：AssessmentStore 唯一真值 id asm_<hash>；旧格式 horizon:snapshot_id 视为无评估引用——影子降级处理）")
    snapshot_id: str = Field(default="", description="研究依据的证据快照 id")
    policy_version: str = Field(default="", description="研究服务/方法版本（草稿生成方登记）")
    supersedes_ref: Optional[str] = Field(default=None, description="被本草稿取代的旧版本引用（版本沿革）")

    @property
    def activated(self) -> bool:
        """空串视为未激活（激活门是安全关键路径，F5 审查 P2）。"""
        return bool(self.accepted_at and self.accepted_at.strip())

    def content_hash(self) -> str:
        """计划内容 hash（不含 accepted_at/created_at——激活状态与创建时刻都不是
        内容；版本身份由 revision 承担。监督员 P2：含 created_at 会让 store 外重建的
        同逻辑计划 hash 假阴性）。绑定精确版本用：plan_id+revision+content_hash。"""
        import hashlib as _h
        d = self.model_dump(mode="json")
        d.pop("accepted_at", None)
        d.pop("created_at", None)
        return _h.sha256(json.dumps(d, ensure_ascii=False, sort_keys=True,
                                    default=str).encode("utf-8")).hexdigest()[:16]

    @field_validator("horizon")
    @classmethod
    def _horizon_mid_or_long(cls, v):
        """PlanV2 只服务中期/长期（LEGACY 计划走 legacy_v1 旧路径，不建 PlanV2）——
        不用旧 mode 代替 horizon（TASKS F5）。"""
        if v not in (Horizon.MID, Horizon.LONG):
            raise ValueError(f"PlanV2.horizon 只接受 MID/LONG，收到 {v}（LEGACY 走旧策略，不建 PlanV2）")
        return v


class HorizonFacts(BaseModel):
    """一次评估的事实输入（由调用方从证据快照/技术层/组合预算汇集——纯数据）。"""

    model_config = ConfigDict(extra="allow")

    research_status: ResearchStatus = ResearchStatus.INCOMPLETE
    thesis_status: ThesisStatus = ThesisStatus.UNESTABLISHED
    hard_exit_triggered: bool = Field(default=False, description="已验证硬退出/用户硬风险界限（有证据ID支撑才置 True）")
    hard_exit_detail: str = ""
    info_reliable: bool = Field(default=True, description="必需价格/持仓信息可靠性（False→REVIEW 行）")
    technical_exit_triggered: bool = Field(default=False, description="预设技术退出成立（MID 行；如 Chandelier/趋势破坏——技术层判定）")
    entry_condition_met: bool = Field(default=False, description="入场条件成立（MID 行6）")
    short_term_noise_only: bool = Field(default=False, description="LONG：只有普通短期破均线（行7 的噪声标记）")
    quality_valuation_ok: bool = Field(default=False, description="LONG：质量与估值研究合格（行8 前提）")
    price_in_buy_zone: bool = Field(default=False, description="LONG：进入预先定义买价区间")
    acute_risk: bool = Field(default=False, description="急性风险（行8 排除项；定义须版本化验证）")
    budget_available: Optional[bool] = Field(default=None, description="组合预算可用；None=组合信息未知（不给精确加仓目标）")
    unsupported_industry: bool = Field(default=False, description="行业无适配器（LONG 质量评估 NOT_APPLICABLE）")
    review_due: bool = Field(default=False, description="复核触发（财报/事件/日期）——只触发 REVIEW 不强制卖")


def evaluate_horizon(plan: HorizonPlan, facts: HorizonFacts, security_id: str,
                     *, confirmed_ratio: Optional[float] = None,
                     position_state: Optional[str] = None,
                     as_of: Optional[datetime] = None) -> DecisionPacket:
    """周期决策表（DESIGN §4.2，从上到下首个决定性条件优先）→ DecisionPacket。

    Args:
        plan: 已确认（或待确认）的 PlanV2
        facts: 本轮事实输入
        confirmed_ratio: 已确认权重（None=权重未知——不给精确目标）
        position_state: 账户事实三态 HELD/NONE/UNKNOWN（M0 同源上下文；None=旧
            调用方按 confirmed_ratio 推导）。HELD+未知权重保留 EXIT/REDUCE、
            冻结新增；NONE 已知空仓不做退出；UNKNOWN 持仓未知保留退出、
            减仓转复核——与最终包适配器同一规范动作视图（R12 W1）
        as_of: 决策截止时点（缺省取当前带时区时间）

    Returns:
        DecisionPacket（policy_id 绑定 plan 的策略ID；reason 首条 = 命中的决策表行）
    """
    as_of = as_of or datetime.now().astimezone()
    if position_state is None:
        held = confirmed_ratio is not None and confirmed_ratio > 1e-9
        known = confirmed_ratio is not None
    else:
        held = position_state == "HELD"
        known = (confirmed_ratio is not None) or position_state == "NONE"
    reasons: list[str] = []

    # 未支持行业：质量/估值研究口径不适用（DESIGN §4.2 行4b）——不填通用数字强行纳入
    if facts.unsupported_industry and facts.research_status is ResearchStatus.COMPLETE:
        facts = facts.model_copy(update={"research_status": ResearchStatus.NOT_APPLICABLE})

    # F5 审查 P1-1 结构化保障：失效条件在决策表端直接推导，不依赖调用方自觉接线——
    # 任一 TRUE → thesis 强制 INVALID（行3）；有 UNKNOWN（未验证）→ REVIEW_REQUIRED（行4b）。
    # 调用方仍可经 assess_thesis 预先推导（结果一致，双保险）。
    _inv = evaluate_invalidation_rules(plan.invalidate_if)
    if _inv is TruthValue.TRUE:
        facts = facts.model_copy(update={"thesis_status": ThesisStatus.INVALID})
    elif _inv is TruthValue.UNKNOWN and facts.thesis_status is ThesisStatus.VALID:
        facts = facts.model_copy(update={"thesis_status": ThesisStatus.REVIEW_REQUIRED})

    # ── 行1（先于激活门，F5 审查 P1-2）：已验证硬退出不被官僚流程掩盖——
    # DESIGN §2.2 "REVIEW 不能掩盖已确认重大退出证据"；行1 不依赖计划激活状态
    if facts.hard_exit_triggered:
        if held or not known:
            reasons.append(f"决策表行1: 已验证硬退出触发——{facts.hard_exit_detail}"
                           f"（EXIT，再查可卖约束；先于计划激活门）")
            return _packet(plan, facts, DesiredAction.EXIT, confirmed_ratio, None,
                           ExecutionStatus.UNKNOWN if not facts.info_reliable else ExecutionStatus.ELIGIBLE,
                           reasons, as_of, research=facts.research_status,
                           thesis=facts.thesis_status, research_problems=[],
                           invalidations=plan.invalidate_if, security_id=security_id)
        reasons.append("决策表行1: 硬退出条件成立但无持仓——WAIT 禁止新增")
        return _packet(plan, facts, DesiredAction.WAIT, confirmed_ratio, None,
                       ExecutionStatus.NOT_NEEDED, reasons, as_of,
                       research=facts.research_status, thesis=facts.thesis_status,
                       research_problems=[], invalidations=plan.invalidate_if,
                       security_id=security_id)

    # 行 0（激活门）：未确认计划只 REVIEW——新计划需确认后才激活（TASKS F5 验收）
    if not plan.activated:
        reasons.append("决策表行0: 计划未确认（accepted_at 空）——仅可复核，不出行动建议")
        return _packet(plan, facts, DesiredAction.REVIEW, confirmed_ratio, None,
                       ExecutionStatus.NOT_NEEDED, reasons, as_of, research=facts.research_status,
                       thesis=facts.thesis_status, research_problems=[],
                       invalidations=plan.invalidate_if, review_due=True, security_id=security_id)

    def act(desired: DesiredAction, reason: str, **kw) -> DecisionPacket:
        reasons.append(reason)
        return _packet(plan, facts, desired, confirmed_ratio, kw.get("target"),
                       kw.get("exec_status", ExecutionStatus.NOT_NEEDED), reasons, as_of,
                       research=kw.get("research", facts.research_status),
                       thesis=kw.get("thesis", facts.thesis_status),
                       research_problems=kw.get("research_problems", []),
                       blockers=kw.get("blockers"),
                       invalidations=plan.invalidate_if,
                       review_due=kw.get("review_due", facts.review_due),
                       security_id=security_id)

    # ── 行2：必需价格/持仓信息不可靠 → 保留可独立成立的退出告警；其余 REVIEW ──
    if not facts.info_reliable:
        if facts.hard_exit_triggered:  # 不可达（行1 已提前接住），防御保留
            return act(DesiredAction.EXIT, "决策表行2: 信息不可靠但硬退出独立成立——EXIT",
                       exec_status=ExecutionStatus.UNKNOWN)  # F5 审查 P2：EXIT 需执行语义
        reasons.append("决策表行2: 必需价格/持仓信息不可靠——数据坏不等于零仓位，也不等于继续看好；其余 REVIEW")
        return act(DesiredAction.REVIEW if held else DesiredAction.WAIT,
                   "决策表行2: 持有→REVIEW（保留可独立成立的退出告警）；未持有→WAIT/REVIEW",
                   exec_status=ExecutionStatus.NOT_NEEDED)

    # ── 行3：投资逻辑 INVALID → 依已接受退出策略 EXIT / WAIT ──
    if facts.thesis_status is ThesisStatus.INVALID:
        if held or not known:
            return act(DesiredAction.EXIT, "决策表行3: 投资逻辑 INVALID——依已接受退出策略 EXIT（不能被技术反弹抵消）",
                       exec_status=ExecutionStatus.ELIGIBLE)
        return act(DesiredAction.WAIT, "决策表行3: 逻辑 INVALID 且无持仓——WAIT", exec_status=ExecutionStatus.NOT_NEEDED)

    # ── 行4：关键逻辑证据 CONFLICTED 或过期 → 两列均 REVIEW（对齐 DESIGN §4.2 原表，
    # F5 审查 P2）——不因未知就清仓；未持有同样需要核对而不是默默 WAIT
    if facts.research_status in (ResearchStatus.CONFLICTED, ResearchStatus.INCOMPLETE):
        label = "CONFLICTED" if facts.research_status is ResearchStatus.CONFLICTED else "INCOMPLETE"
        return act(DesiredAction.REVIEW,
                   f"决策表行4: 关键证据 {label}——REVIEW，冻结新增风险"
                   f"（不因未知清仓；长期缺财务不发质量合格结论）")

    if facts.research_status is ResearchStatus.NOT_APPLICABLE:
        return act(DesiredAction.WAIT if not held else DesiredAction.REVIEW,
                   "决策表行4b: 研究口径不适用（如未支持行业）——不填通用数字强行纳入",
                   exec_status=ExecutionStatus.NOT_NEEDED)

    # ── 行4b：thesis REVIEW_REQUIRED（失效条件 UNKNOWN=需人工确认）→ REVIEW ──
    if facts.thesis_status is ThesisStatus.REVIEW_REQUIRED:
        return act(DesiredAction.REVIEW,
                   "决策表行4b: 逻辑需人工核对（失效条件 UNKNOWN——UNKNOWN 不自动变 FALSE）")

    # 到此 thesis_status 必为 VALID（UNESTABLISHED 落行10）；按技术/价位推进
    valid = facts.thesis_status is ThesisStatus.VALID

    # ── 行5（MID）：逻辑 VALID 且预设技术退出成立 → REDUCE/EXIT ──
    if plan.horizon is Horizon.MID and valid and facts.technical_exit_triggered:
        if held or not known:
            return act(DesiredAction.REDUCE,
                       "决策表行5: MID 逻辑 VALID 且预设技术退出成立——不通过'长拿'口号压制此周期的退出",
                       exec_status=ExecutionStatus.ELIGIBLE)
        return act(DesiredAction.WAIT, "决策表行5: 技术退出成立但无持仓——WAIT")

    # ── 行6（MID）：逻辑 VALID、入场条件成立且预算可用 → ADD 或 HOLD / OPEN ──
    if plan.horizon is Horizon.MID and valid and facts.entry_condition_met:
        if held:
            if facts.budget_available is True:
                return act(DesiredAction.ADD, "决策表行6: MID 逻辑 VALID+入场条件+预算可用——依加仓计划 ADD",
                           exec_status=ExecutionStatus.ELIGIBLE)
            return act(DesiredAction.HOLD, "决策表行6: 逻辑与入场条件成立但预算不可用/未知——HOLD（不假增仓）")
        if facts.budget_available is False:
            # F5 审查 P1-3：DESIGN 行6 条件含"且预算可用"——明确不可用落行9 WAIT（修反事实 reason）
            return act(DesiredAction.WAIT, "决策表行9: 入场条件成立但预算不可用——WAIT，等下次触发条件")
        if not known:
            return act(DesiredAction.OPEN, "决策表行6: MID 逻辑 VALID+入场条件成立（组合未知——有条件方向）",
                       exec_status=ExecutionStatus.ELIGIBLE)
        return act(DesiredAction.OPEN, "决策表行6: MID 逻辑 VALID+入场条件成立且预算可用——OPEN",
                   exec_status=ExecutionStatus.ELIGIBLE)

    # ── 行7（LONG）：逻辑 VALID、未触发硬退出、只有普通短期破均线 → HOLD ──
    if (plan.horizon is Horizon.LONG and valid and facts.short_term_noise_only
            and not facts.quality_valuation_ok):
        if held or not known:
            return act(DesiredAction.HOLD,
                       "决策表行7: LONG 逻辑 VALID——普通短期破均线是技术噪声，留痕但不擅自转短线策略",
                       exec_status=ExecutionStatus.NOT_NEEDED)
        # 未持有：落到行8/行9 评估（噪声标的仍可按长期入场策略评估是否 OPEN/WAIT；
        # ADR-F08 不给未建仓用户"继续持有"，F5 审查 P1-4）

    # ── 行8（LONG）：质量与估值合格 + 进买价区间 + 无急性风险 → ADD/OPEN 候选 ──
    if (plan.horizon is Horizon.LONG and valid and facts.quality_valuation_ok
            and facts.price_in_buy_zone and not facts.acute_risk):
        if held:
            if facts.budget_available is True:
                return act(DesiredAction.ADD, "决策表行8: LONG 质量估值合格+进买价区间+无急性风险+剩余预算——ADD",
                           exec_status=ExecutionStatus.ELIGIBLE)
            return act(DesiredAction.HOLD, "决策表行8: 条件成立但无剩余预算——HOLD")
        if not known:
            return act(DesiredAction.OPEN, "决策表行8: LONG 条件成立（组合未知——OPEN 候选，有条件方向）",
                       exec_status=ExecutionStatus.ELIGIBLE)
        return act(DesiredAction.OPEN, "决策表行8: LONG 质量估值合格+进买价区间+无急性风险——OPEN 候选",
                   exec_status=ExecutionStatus.ELIGIBLE)

    # ── 行9：逻辑 VALID 但价位/预算/时机不合适 → HOLD / WAIT（给下次触发条件）──
    if valid:
        if held:
            return act(DesiredAction.HOLD,
                       "决策表行9: 逻辑有效但价位/预算/时机不合适——'好公司'和'现在适合买'分开")
        return act(DesiredAction.WAIT, "决策表行9: 逻辑有效但价位/时机不合适——WAIT，等下次触发条件")

    # ── 行10：没有足够逻辑支持 → REVIEW / WAIT-REVIEW ──
    return act(DesiredAction.REVIEW if held else DesiredAction.WAIT,
               "决策表行10: 没有足够逻辑支持——技术候选保留研究资格，不直接升级投资建议")


def _packet(plan: HorizonPlan, facts: HorizonFacts, desired: DesiredAction,
            confirmed_ratio: Optional[float], target: Optional[float],
            exec_status: ExecutionStatus, reasons: list[str], as_of: datetime,
            *, research: ResearchStatus, thesis: ThesisStatus,
            research_problems: list[str], blockers=None, invalidations=None,
            review_due: bool = False, security_id: str = "") -> DecisionPacket:
    """决策表行 → 合法 DecisionPacket（组合未知时不给精确目标；失效条件随包下发）。"""
    blockers_out = list(blockers or [])
    for p in research_problems:
        blockers_out.append(Blocker(kind="data", detail=p))
    if desired is DesiredAction.EXIT and target is None:
        target = 0.0  # EXIT 意味目标 0（契约不变量；组合未知也成立）
    next_check = (NextCheck(due="计划复核触发（财报披露/新事件/指定复核日期）",
                            reason="复核触发只产生 REVIEW 语义，不强制卖出")
                  if review_due else None)
    packet = DecisionPacket(
        security_id=security_id,
        as_of=as_of,
        policy_id=plan.policy_id,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        horizon=plan.horizon,
        research_status=research,
        thesis_status=thesis,
        desired_action=desired,
        confirmed_weight=confirmed_ratio,
        target_weight=target,
        delta_weight=None,
        execution_status=exec_status,
        executable_action=(desired if exec_status is ExecutionStatus.ELIGIBLE else None),
        blockers=blockers_out,
        reason_codes=reasons,
        next_check=next_check,
        invalidation_rules=list(invalidations or []),
    )
    return packet


# ──────────────── R3：中间策略意图与最终执行资格分离（RESEARCH_LOOP §5）────────────────

@dataclass
class PolicyIntent:
    """周期决策表的中间输出（内部值对象）——**不是可展示终态**。

    desired_action/reason_codes/计划目标/证据引用归这里；execution_status/
    executable_action/target 不在这里——最终 DecisionPacket 由账户预算与交易规则
    （R5/R8）完成后才生成。新实现不得把中间 ELIGIBLE 透传为已通过全部执行门。"""

    desired_action: DesiredAction
    reason_codes: List[str]
    plan_id: str
    horizon: Horizon
    policy_id: str
    research_status: ResearchStatus
    thesis_status: ThesisStatus
    target_weight: Optional[float] = None
    evidence_ids: List[str] = field(default_factory=list)  # R3 期恒空——决策表行接通证据引用后填充（R8）
    blockers: List[str] = field(default_factory=list)      # R3 期恒空——同上


def evaluate_horizon_intent(plan: HorizonPlan, facts: HorizonFacts, security_id: str,
                            *, confirmed_ratio: Optional[float] = None,
                            position_state: Optional[str] = None,
                            as_of: Optional[datetime] = None) -> PolicyIntent:
    """周期决策表 → PolicyIntent（与 evaluate_horizon 同一张表裁决，剥执行语义：
    execution_status/executable_action 不产出——影子/工作台展示意图，执行资格由
    账户预算+交易规则后的 DecisionPacket 单独给出，R3 验收7）。"""
    pkt = evaluate_horizon(plan, facts, security_id, confirmed_ratio=confirmed_ratio,
                           position_state=position_state, as_of=as_of)
    return PolicyIntent(
        desired_action=pkt.desired_action,
        reason_codes=list(pkt.reason_codes),
        plan_id=pkt.plan_id or plan.plan_id,
        horizon=pkt.horizon,
        policy_id=pkt.policy_id,
        research_status=pkt.research_status,
        thesis_status=pkt.thesis_status,
        target_weight=pkt.target_weight if pkt.desired_action is DesiredAction.EXIT else None,
        evidence_ids=list(pkt.evidence_ids),
        blockers=[b.detail for b in pkt.blockers],
    )
