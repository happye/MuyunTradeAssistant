"""统一决策契约（plan/fusion F0，DESIGN.md ADR-F02）：DecisionPacket 最小类型集。

三对象严格分离（DESIGN §2.1）：
- SignalAssessment：检测到什么信号（允许相互矛盾）——现有 SkillSignal/DecisionResult 承担
- DecisionPacket：基于指定计划与账户快照，系统建议什么行动、为何、受何约束 —— 本模块
- HoldingSnapshot / ConfirmedFill：已确认的持仓/成交事实 —— 分析只生成建议，不创建成交

F0 范围（TASKS.md F0）：只冻结契约与风险不变量，不接入任何生产调用方、不改策略参数。
- 非法动作/仓位组合在构造期即报错（DESIGN §2.3 不变量的机器化）
- UNKNOWN/MISSING 是显式状态，绝不落成 0 或 50（auto_scorer 50 兜底的教训不进新契约）
- JSON 往返稳定；未知字段保留（ADR-F09：extra="allow"，不丢弃 forward-compatible 数据）
- legacy_trace 只作旧七层结果投影（专家展开/回归），不参与第二次裁决（ADR-F01）
- 契约不再叫模糊的 `score`：只有具名诊断字段（legacy_action_strength 等），无 win_probability

字段语义唯一事实源 = plan/fusion/DESIGN.md §2.2；改契约必须同步该表 + tests/core/test_decision_contract.py。
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 契约版本（F0 冻结）。升级语义不兼容时 bump 主版本号，旧记录按 legacy 投影读取。
FUSION_SCHEMA_VERSION = "fusion.v1"

_WEIGHT_EPS = 1e-9  # 权重相等比较容差（浮点 target/confirmed 一致性）


class ResearchStatus(str, Enum):
    """研究/证据资格状态（DESIGN §2.2）：不能用 0 或 50 代替缺失。"""

    COMPLETE = "COMPLETE"          # 决策所需证据齐备
    INCOMPLETE = "INCOMPLETE"      # 必需证据缺失——禁止发强结论（G04 场景）
    CONFLICTED = "CONFLICTED"      # 关键证据相互矛盾——REVIEW，冻结新增风险
    NOT_APPLICABLE = "NOT_APPLICABLE"  # 该研究口径不适用（如未支持行业的长期评估）


class ThesisStatus(str, Enum):
    """投资逻辑状态（DESIGN §2.2）：长期逻辑不随日涨跌自动翻转。"""

    UNESTABLISHED = "UNESTABLISHED"  # 尚无足够证据建立逻辑
    VALID = "VALID"                  # 逻辑成立
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # 需要核对（冲突/过期证据）
    INVALID = "INVALID"              # 逻辑已失效——技术反弹不能抵消（G06 场景）


class DesiredAction(str, Enum):
    """研究与策略想做什么（DESIGN §2.2 desired_action）。"""

    OPEN = "OPEN"
    ADD = "ADD"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    WAIT = "WAIT"
    REVIEW = "REVIEW"


class ExecutionStatus(str, Enum):
    """时点可行性判断（DESIGN §2.2）：不是已成交状态；BLOCKED/UNKNOWN 不能伪装成行动。"""

    ELIGIBLE = "ELIGIBLE"        # 当前许可执行
    BLOCKED = "BLOCKED"          # 受阻（跌停/停牌/无可卖份额/资金）——保留意图与阻塞原因
    CONDITIONAL = "CONDITIONAL"  # 条件候选（如下一时段触发价）——不是"明日必买/必卖"
    UNKNOWN = "UNKNOWN"          # 可行性未知（数据缺失）——不发精确指令
    NOT_NEEDED = "NOT_NEEDED"    # 无需执行（HOLD/WAIT/REVIEW）


class Horizon(str, Enum):
    """研究语境的持有周期（DESIGN §4.1）：与气宗/剑宗 legacy_mode 正交，不是到天数机械清仓。"""

    LEGACY = "LEGACY"
    MID = "MID"
    LONG = "LONG"


class MarketPhase(str, Enum):
    """决策截止时点所处的市场阶段（DESIGN §2.2：收盘评估一般是下一时段条件计划）。"""

    PRE_OPEN = "PRE_OPEN"
    INTRADAY = "INTRADAY"
    CLOSE = "CLOSE"


class FactStatus(str, Enum):
    """事实状态（DESIGN §5.1）：OBSERVED 与 MODEL_INFERRED 不得混用；MISSING 是状态不是 0。"""

    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    MODEL_INFERRED = "MODEL_INFERRED"
    USER_ASSERTED = "USER_ASSERTED"
    MISSING = "MISSING"
    STALE = "STALE"
    CONFLICTED = "CONFLICTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TruthValue(str, Enum):
    """三值真值（DESIGN §5.1：失效条件 evaluation）——UNKNOWN 不可自动变 FALSE。"""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class EvidenceRef(BaseModel):
    """证据引用（F0 最小版；完整 EvidenceRecord/时点资格在 F3 落地，DESIGN §5.1）。"""

    model_config = ConfigDict(extra="allow")

    evidence_id: str = Field(description="证据唯一ID（事件谱系共用，防同一公告重复计票）")
    source_kind: str = Field(description="来源类别：announcement/financial/market/news/rag/ai_extraction...")
    fact_status: FactStatus = Field(default=FactStatus.OBSERVED, description="事实状态；MODEL_INFERRED 不能冒充 OBSERVED")
    as_of: datetime = Field(description="证据可得时点（available_at）；严格PIT要求 available_at <= decision.as_of")
    note: str = Field(default="", description="人话备注（引用定位/摘要；关键公开证据不可只存模型摘要）")

    @field_validator("as_of")
    @classmethod
    def _as_of_must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("as_of 必须带时区（无时区时间无法做严格PIT比较）")
        return v


class Blocker(BaseModel):
    """行动阻塞项（DESIGN §2.2 blockers）：展示层直接消费，不再猜 sell_path。"""

    model_config = ConfigDict(extra="allow")

    kind: str = Field(description="阻塞类别：funds/sellable_shares/data/limit_down/limit_up/horizon/price_condition...")
    detail: str = Field(description="人话说明（如：跌停封板无法卖出）")


class InvalidationRule(BaseModel):
    """失效条件（DESIGN §5.1）：结构化、可计算或需人工确认；UNKNOWN 不可自动变 FALSE。"""

    model_config = ConfigDict(extra="allow")

    rule_id: str = Field(description="规则ID（复核与diff定位用）")
    condition: str = Field(description="条件描述（人话；可计算字段由 F5 策略层登记）")
    evaluation: TruthValue = Field(default=TruthValue.UNKNOWN, description="当前评估值；UNKNOWN 只能经证据评估变更")


class NextCheck(BaseModel):
    """下次复核触发（DESIGN §2.2 next_check）：复核日到达只触发 REVIEW，不是强制卖（DESIGN §4.2）。"""

    model_config = ConfigDict(extra="allow")

    due: str = Field(description="复核时点（YYYY-MM-DD 或事件描述，如：新财报披露后）")
    reason: str = Field(default="", description="为什么到时要看")


class LegacyTrace(BaseModel):
    """旧七层结果投影（DESIGN §2.2 legacy_trace）：仅供专家展开与回归对照，不参与第二次裁决。
    分数语义登记（F0 基线，唯一事实源见 plan/fusion/EXECUTION_RECORD.md）：
    action_strength = 现有 DecisionResult.score —— **选定动作桶的强度**（0-1），
    不是买入价值、不是上涨概率；AI/事件层的 adjustment 直接加在该值上
    （orchestrator.py AI合并分支），SELL 高分=强卖出意愿。命名显式化以杜绝
    当作综合买入分复用（RESEARCH §3.1 探针 C/D 实证）。
    """

    model_config = ConfigDict(extra="allow")

    decision: Optional[str] = Field(default=None, description="旧 DecisionResult.decision（BUY/SELL/HOLD/WATCH）——中间态，非终态")
    action_strength: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="旧 DecisionResult.score：选定动作强度 0-1")
    position_action: Optional[str] = Field(default=None, description="旧 StrategyDecision.position_action（OPEN/ADD/REDUCE/CLOSE_ALL/...）")
    position_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="旧建议仓位比例 0-1")
    sell_path: Optional[str] = Field(default=None, description="旧卖出路径（weak_sell/trend_exit/stop_loss_* 等）")


class DecisionPacket(BaseModel):
    """唯一终态决策包（DESIGN §2.2）：所有入口（CLI/chat/Web/TUI/证据/diff）消费同一对象。

    不可变（DESIGN ADR-F02"公共不可变结果类型"/§2.2"决策记录不可变"）：frozen=True，
    构造后任何字段赋值即报错——要改结论就生成新 decision_id 的新记录，不就地改写。
    model_construct 一并禁用（绕过校验的构造会让全部不变量失效，F2 起尤其危险）。

    不变量（DESIGN §2.3，构造期校验）：
    - EXIT 意味目标 0；REDUCE 目标必须小于已确认当前；ADD 目标必须大于当前；HOLD 不变
    - 缺组合信息（confirmed_weight=None）时可给有条件方向，target_weight 必须为 None——
      不能默认推荐 20% 或 50%（ADD/REDUCE/OPEN/HOLD 全覆盖；EXIT 例外：目标恒 0）
    - desired=EXIT + execution=BLOCKED 保留退出意图、阻塞原因与现有持仓（G03 场景）
    - thesis=INVALID 时禁止 OPEN/ADD（不能被技术反弹抵消，G06 场景）
    - 规则原因与数值由后端生成；本契约不含 AI 自由文本目标价/份额/止损
    """

    model_config = ConfigDict(extra="allow", frozen=True)  # 未知字段保留；记录不可变

    @classmethod
    def model_construct(cls, *args, **kwargs):
        """禁用绕过校验的构造（F0 对抗审查 🔴 补丁）：不变量只在真实校验路径生效。"""
        raise NotImplementedError(
            "DecisionPacket 禁止 model_construct（绕过全部不变量校验）；"
            "请走构造函数或 model_validate。确有性能需求须新 ADR。")

    # ── 身份与版本 ──
    schema_version: str = Field(default=FUSION_SCHEMA_VERSION)
    decision_id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="决策唯一ID；重算是新记录")
    security_id: str = Field(description="规范证券ID：纯6位数字代码（如 600519）；名称仅展示，不做ID")
    exchange: Optional[str] = Field(default=None, description="SH/SZ/BJ；缺省按代码段推导")
    as_of: datetime = Field(description="决策截止时点（带时区）——非『生成日期』等价")
    market_phase: MarketPhase = Field(default=MarketPhase.CLOSE)

    # ── 复现依据（DESIGN §2.2 snapshot/revision）──
    snapshot_id: Optional[str] = Field(default=None, description="数据快照内容hash")
    portfolio_revision: Optional[str] = Field(default=None, description="账户快照版本；批量结果须来自同一版本")

    # ── 策略与计划绑定 ──
    policy_id: str = Field(description="策略ID（如 legacy_v1 / fusion_mid_v2）——不混用分数")
    parameter_version: Optional[str] = Field(default=None)
    plan_id: Optional[str] = Field(default=None, description="绑定的已确认持有意图；无计划时 None")
    plan_revision: Optional[int] = Field(default=None)
    horizon: Horizon = Field(default=Horizon.LEGACY)

    # ── 研究与逻辑资格 ──
    research_status: ResearchStatus = Field(default=ResearchStatus.INCOMPLETE)
    thesis_status: ThesisStatus = Field(default=ThesisStatus.UNESTABLISHED)

    # ── 行动与仓位 ──
    desired_action: DesiredAction = Field(description="研究与策略想做什么")
    confirmed_weight: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, allow_inf_nan=False,
        description="已确认当前权重（账户净资产为分母，来自 HoldingSnapshot）；未知=None 不是 0")
    target_weight: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, allow_inf_nan=False,
        description="目标权重；无组合信息时必须 None")
    delta_weight: Optional[float] = Field(
        default=None, ge=-1.0, le=1.0, allow_inf_nan=False,
        description="delta = target - 已确认当前；未知则 None")

    # ── 执行可行性 ──
    execution_status: ExecutionStatus = Field(default=ExecutionStatus.UNKNOWN)
    executable_action: Optional[DesiredAction] = Field(
        default=None, description="当前许可动作；仅 ELIGIBLE 可给（且等于 desired_action），否则 None")
    blockers: list[Blocker] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list, description="有序结构化原因码（展示层消费）")

    # ── 证据引用 ──
    evidence_ids: list[str] = Field(default_factory=list, description="支持证据（共享事件不重复计票）")
    dissent_ids: list[str] = Field(default_factory=list, description="反证")

    # ── 复核与失效 ──
    next_check: Optional[NextCheck] = None
    invalidation_rules: list[InvalidationRule] = Field(default_factory=list)

    # ── 旧七层投影（诊断）──
    legacy_trace: Optional[LegacyTrace] = None

    # ──────────────── 校验 ────────────────

    @field_validator("security_id", mode="before")
    @classmethod
    def _normalize_security_id(cls, v):
        """证券ID规范化：半角纯数字补零到6位；拒绝任意字符串切割（'600519.SH' 必须拆exchange）。

        YAML int 键陷阱同款防御（portfolio G04）：600519 这类 int 输入统一转 str；
        全角数字（'６００５１９'，str.isdigit 为 True）是本项目登记在案的坑面，显式拒绝。
        """
        if isinstance(v, bool):
            raise ValueError("security_id 不能是布尔值")
        if isinstance(v, int):
            v = str(v)
        if not isinstance(v, str):
            raise ValueError(f"security_id 需为字符串/整数代码，收到 {type(v).__name__}")
        code = v.strip()
        if not (code.isascii() and code.isdigit()):
            raise ValueError(f"security_id 必须是半角纯数字代码（exchange 单独字段），收到 {v!r}")
        if len(code) > 6:
            raise ValueError(f"security_id 超过6位：{code!r}（如需后缀请放 exchange 字段）")
        return code.zfill(6)

    @field_validator("exchange", mode="after")
    @classmethod
    def _validate_exchange(cls, v):
        """显式传入时只接受三所合法值；缺省派生在 _derive_exchange（model 级，必跑）。"""
        if v is None:
            return None
        v = str(v).strip().upper() or None
        if v is not None and v not in ("SH", "SZ", "BJ"):
            raise ValueError(f"exchange 只接受 SH/SZ/BJ，收到 {v!r}")
        return v

    @field_validator("as_of")
    @classmethod
    def _as_of_must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("as_of 必须带时区（naive datetime 无法保证终态时点可比）")
        return v

    @model_validator(mode="before")
    @classmethod
    def _derive_exchange(cls, data):
        """exchange 缺省按代码段推导（frozen 契约下不能构造后赋值，改在输入 dict 上派生）。

        在 field 校验前运行，security_id 可能是 int/未补零——此处只做轻量识别，
        规范化仍由 _normalize_security_id 负责。
        """
        if isinstance(data, dict) and data.get("exchange") is None:
            code = data.get("security_id")
            code = str(code).strip() if code is not None else ""
            if code.isascii() and code.isdigit() and len(code) <= 6:
                code = code.zfill(6)
                if code.startswith("6"):
                    data["exchange"] = "SH"
                elif code.startswith(("0", "3")):
                    data["exchange"] = "SZ"
                elif code.startswith(("4", "8")):
                    data["exchange"] = "BJ"
                # 未知代码段不猜，保持 None
        return data

    @model_validator(mode="after")
    def _check_action_weight_invariants(self):
        """DESIGN §2.3 不变量的构造期机器化（对应 tests/core/test_decision_contract.py）。"""
        act = self.desired_action
        cur, tgt = self.confirmed_weight, self.target_weight
        unknown_portfolio = cur is None  # 账户/组合信息缺失

        # 缺组合信息：可给有条件方向，但不允许精确目标（不能默认推荐 20%/50%）
        # EXIT 例外：目标恒 0，不依赖组合信息（DESIGN §2.3"EXIT意味着目标0"）
        if unknown_portfolio and tgt is not None and act in (
                DesiredAction.ADD, DesiredAction.REDUCE,
                DesiredAction.OPEN, DesiredAction.HOLD):
            raise ValueError(
                f"已确认权重未知时 {act.value} 不允许给 target_weight（有条件方向请置 None）；"
                f"收到 {tgt}")

        if act is DesiredAction.EXIT and tgt is not None and abs(tgt) > _WEIGHT_EPS:
            raise ValueError(f"EXIT 意味目标 0，收到 target_weight={tgt}")
        if act is DesiredAction.REDUCE:
            if cur is not None and cur <= _WEIGHT_EPS:
                raise ValueError(f"REDUCE 需要已确认持仓 > 0，收到 confirmed_weight={cur}（空仓建仓请用 OPEN）")
            if cur is not None and tgt is not None and tgt >= cur - _WEIGHT_EPS:
                raise ValueError(f"REDUCE 目标必须小于已确认当前：target={tgt} >= confirmed={cur}")
        if act is DesiredAction.ADD:
            if cur is not None and cur <= _WEIGHT_EPS:
                raise ValueError(f"ADD 需要已确认持仓 > 0，收到 confirmed_weight={cur}（空仓买入请用 OPEN）")
            if cur is not None and tgt is not None and tgt <= cur + _WEIGHT_EPS:
                raise ValueError(f"ADD 目标必须大于已确认当前：target={tgt} <= confirmed={cur}")
        if act is DesiredAction.OPEN:
            if cur is not None and cur > _WEIGHT_EPS:
                raise ValueError(f"OPEN 要求当前无持仓，收到 confirmed_weight={cur}（已持有加仓请用 ADD）")
            if tgt is not None and tgt <= _WEIGHT_EPS:
                raise ValueError(f"OPEN 目标必须 > 0，收到 {tgt}")
        if act is DesiredAction.HOLD:
            if cur is not None and tgt is not None and abs(tgt - cur) > _WEIGHT_EPS:
                raise ValueError(f"HOLD 不建议比例变化：target={tgt} != confirmed={cur}")
        if act in (DesiredAction.WAIT, DesiredAction.REVIEW) and tgt is not None:
            raise ValueError(f"{act.value} 不提出仓位变化，target_weight 必须为 None，收到 {tgt}")

        # delta 一致性：三者齐且矛盾 → 拒绝（delta = target - confirmed）
        if self.delta_weight is not None:
            if tgt is None:
                raise ValueError("delta_weight 有值但 target_weight 为 None，语义矛盾")
            if cur is not None and abs(self.delta_weight - (tgt - cur)) > 1e-6:
                raise ValueError(
                    f"delta_weight={self.delta_weight} 与 target-confirmed={tgt - cur} 不一致")

        # 逻辑失效不能被技术反弹抵消（G06）：INVALID 禁止开仓/加仓
        if self.thesis_status is ThesisStatus.INVALID and act in (DesiredAction.OPEN, DesiredAction.ADD):
            raise ValueError("thesis_status=INVALID 时禁止 OPEN/ADD（已核实反证优先于技术强势）")

        # 执行状态一致性：BLOCKED/UNKNOWN/CONDITIONAL 不能伪装成许可动作（DESIGN §2.2）
        st = self.execution_status
        if st is ExecutionStatus.BLOCKED:
            if not self.blockers:
                raise ValueError("execution_status=BLOCKED 必须给出 blockers（阻塞原因不可省略）")
            if self.executable_action is not None:
                raise ValueError("BLOCKED 时 executable_action 必须为 None（不能伪装成可执行）")
        if st is ExecutionStatus.CONDITIONAL:
            if not self.blockers:
                raise ValueError("CONDITIONAL 需要说明触发条件（blockers 记录条件本身）")
            if self.executable_action is not None:
                raise ValueError("CONDITIONAL 时 executable_action 必须为 None（条件未满足，不能伪装成可执行）")
        if st is ExecutionStatus.ELIGIBLE:
            if self.blockers:
                raise ValueError("ELIGIBLE 不应携带 blockers（有阻塞就不是 ELIGIBLE）")
            if self.executable_action is not None and self.executable_action is not act:
                raise ValueError(
                    f"ELIGIBLE 的 executable_action 只能等于 desired_action 或 None："
                    f"{self.executable_action.value} != {act.value}")
        if st in (ExecutionStatus.UNKNOWN, ExecutionStatus.NOT_NEEDED) and self.executable_action is not None:
            raise ValueError(f"{st.value} 时 executable_action 必须为 None")
        return self


# ──────────────── 四份官方样例（TASKS.md F0 验收；同时是契约用法人话文档）───────────────

def sample_buy_packet() -> DecisionPacket:
    """样例①BUY：空仓已确认（confirmed=0）、研究齐备、许可开仓——带精确目标。

    展示：OPEN 要求 confirmed=0；delta=target-confirmed；ELIGIBLE 时 executable=desired。
    """
    from datetime import timezone
    return DecisionPacket(
        security_id="600519",
        as_of=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
        market_phase=MarketPhase.CLOSE,
        policy_id="legacy_v1",
        plan_id=None,
        horizon=Horizon.LEGACY,
        research_status=ResearchStatus.COMPLETE,
        thesis_status=ThesisStatus.VALID,
        desired_action=DesiredAction.OPEN,
        confirmed_weight=0.0,
        target_weight=0.15,
        delta_weight=0.15,
        execution_status=ExecutionStatus.ELIGIBLE,
        executable_action=DesiredAction.OPEN,
        reason_codes=["entry_condition_met"],
        evidence_ids=["ev-ann-001"],
        next_check=NextCheck(due="新财报披露后", reason="验证景气证据是否延续"),
    )


def sample_wait_packet() -> DecisionPacket:
    """样例②WAIT：必需证据缺失（research=INCOMPLETE）——禁止新增风险，不给数字目标。

    展示：UNKNOWN/缺失语义不落成 0 或 50；confirmed 未知 → target 必须 None（G04 场景）。
    """
    from datetime import timezone
    return DecisionPacket(
        security_id="002192",
        as_of=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
        policy_id="fusion_mid_v1",
        horizon=Horizon.MID,
        research_status=ResearchStatus.INCOMPLETE,
        thesis_status=ThesisStatus.UNESTABLISHED,
        desired_action=DesiredAction.WAIT,
        confirmed_weight=None,      # 未持有且组合信息未知——不是 0
        target_weight=None,         # 不发精确目标
        delta_weight=None,
        execution_status=ExecutionStatus.NOT_NEEDED,
        blockers=[Blocker(kind="data", detail="行业景气必需证据缺失（经营侧数据未取得）")],
        reason_codes=["required_evidence_missing"],
        next_check=NextCheck(due="行业景气月度数据发布后", reason="补齐必需证据再评估"),
    )


def sample_hold_packet() -> DecisionPacket:
    """样例③HOLD：已持有 20%、逻辑成立、无比例变化建议。

    展示：HOLD 的 target/eddta 均 None（不建议比例变化）；execution=NOT_NEEDED。
    """
    from datetime import timezone
    return DecisionPacket(
        security_id="300750",
        as_of=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
        policy_id="legacy_v1",
        plan_id="300750_2026-09-01",
        plan_revision=2,
        horizon=Horizon.LEGACY,
        research_status=ResearchStatus.COMPLETE,
        thesis_status=ThesisStatus.VALID,
        desired_action=DesiredAction.HOLD,
        confirmed_weight=0.2,
        target_weight=None,
        delta_weight=None,
        execution_status=ExecutionStatus.NOT_NEEDED,
        reason_codes=["thesis_valid_no_trigger"],
        evidence_ids=["ev-fin-014"],
        legacy_trace=LegacyTrace(decision="HOLD", action_strength=0.42,
                                 position_action="HOLD_POSITION", position_ratio=0.2),
    )


def sample_exit_blocked_packet() -> DecisionPacket:
    """样例④EXIT受阻：已核实退出条件触发但跌停卖不出——保留退出意图与持仓事实（G03）。

    展示：EXIT → target=0；BLOCKED 必须带 blockers；executable=None（不能伪装已执行）；
    不改成『逻辑继续看好』。
    """
    from datetime import timezone
    return DecisionPacket(
        security_id="601318",
        as_of=datetime(2026, 9, 25, 14, 55, tzinfo=timezone.utc),
        market_phase=MarketPhase.INTRADAY,
        policy_id="legacy_v1",
        plan_id="601318_2026-08-10",
        plan_revision=1,
        horizon=Horizon.LEGACY,
        research_status=ResearchStatus.COMPLETE,
        thesis_status=ThesisStatus.INVALID,   # 已核实硬退出——INVALID 与 EXIT 一致
        desired_action=DesiredAction.EXIT,
        confirmed_weight=0.2,
        target_weight=0.0,
        delta_weight=-0.2,
        execution_status=ExecutionStatus.BLOCKED,
        executable_action=None,               # 卖不出就是卖不出，不发假指令
        blockers=[Blocker(kind="limit_down", detail="跌停封板，卖出委托无法成交")],
        reason_codes=["hard_exit_triggered", "sell_blocked"],
        evidence_ids=["ev-ann-077"],
        dissent_ids=[],
        next_check=NextCheck(due="下一交易时段开盘", reason="重新检查可成交性"),
        legacy_trace=LegacyTrace(decision="SELL", action_strength=0.85,
                                 position_action="CLOSE_ALL", position_ratio=0.0,
                                 sell_path="stop_loss_exit"),
    )
