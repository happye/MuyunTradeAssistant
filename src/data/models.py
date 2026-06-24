"""数据模型定义 - Pydantic"""

from typing import Optional, Literal
from pydantic import BaseModel, Field
from enum import Enum


class SignalType(str, Enum):
    """信号类型"""
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    WATCH = "WATCH"  # 观望


class MarketState(str, Enum):
    """市场状态"""
    RISK_ON = "RISK_ON"       # 积极状态（多数维度看多）
    RISK_OFF = "RISK_OFF"     # 谨慎状态（多数维度看空）
    PANIC = "PANIC"           # 恐慌状态（极端下跌）
    TRANSITION = "TRANSITION"  # 过渡状态（多空分歧，趋势不明）


class PositionAction(str, Enum):
    """仓位动作"""
    OPEN = "OPEN"                    # 试探建仓（首次买入）
    ADD = "ADD"                      # 加仓（趋势确认后追加）
    REDUCE = "REDUCE"                # 减仓（分批止盈等）
    CLOSE_ALL = "CLOSE_ALL"          # 全部清仓（止损/趋势反转）
    HOLD_POSITION = "HOLD_POSITION"  # 维持当前仓位
    STAY_OUT = "STAY_OUT"            # 空仓观望


def infer_action_semantic(
    position_action: "PositionAction | str | None",
    decision: "SignalType | str | None" = None,
    reasons: list[str] | None = None,
    trade_action: str | None = None,
) -> Optional[str]:
    """将旧仓位动作映射为收紧后的交易语义。"""
    action_value = position_action.value if isinstance(position_action, PositionAction) else str(position_action or "").strip().upper()
    decision_value = decision.value if isinstance(decision, SignalType) else str(decision or "").strip().upper()
    reason_blob = " ".join(reasons or []).lower()
    exit_markers = ("趋势退出", "trend_exit")
    stop_markers = ("stop_loss", "止损", "panic", "black_swan", "风控", "极端")

    if action_value == PositionAction.OPEN.value:
        return "ENTRY"
    if action_value == PositionAction.ADD.value:
        return "ADD"
    if action_value == PositionAction.REDUCE.value:
        return "TRIM"
    if action_value == PositionAction.CLOSE_ALL.value:
        if any(marker in reason_blob for marker in exit_markers):
            return "EXIT"
        return "STOP" if any(marker in reason_blob for marker in stop_markers) else "EXIT"
    if action_value == PositionAction.HOLD_POSITION.value:
        return "HOLD"
    if not action_value and str(trade_action or "").upper() == "SELL":
        if any(marker in reason_blob for marker in exit_markers):
            return "EXIT"
        return "STOP" if any(marker in reason_blob for marker in stop_markers) else "EXIT"
    if decision_value == SignalType.HOLD.value:
        return "HOLD"
    return None


class PositionTier(str, Enum):
    """仓位档位（v0.8.3 Phase D 金字塔仓位管理）"""
    FLAT = "FLAT"        # 空仓
    PILOT = "PILOT"      # 试探仓（10-15%，首次入场）
    BASE = "BASE"        # 基础仓（25-30%，趋势确认+试探盈利>3%）
    FULL = "FULL"        # 重仓（40-50%，强趋势+基础盈利>5%）


class TradeLifecycle(str, Enum):
    """交易生命周期状态（v0.7.2 Strategy Layer核心抽象）

    FLAT → OPEN → HOLD → EXIT → COOLDOWN → FLAT

    核心原则：
    - 信号可以变，决策必须稳定
    - 方向改变必须付出成本
    - 优先避免错误交易，而非追求机会最大化
    """
    FLAT = "FLAT"          # 空仓（无持仓，等待入场信号）
    OPEN = "OPEN"          # 新开仓（刚建仓，需要确认期）
    HOLD = "HOLD"          # 持仓中（确认趋势，持有为主）
    EXIT = "EXIT"          # 退出过程（收到退出信号，执行减仓/清仓）
    COOLDOWN = "COOLDOWN"  # 冷却期（平仓后限制短期内反向操作）


class TradePlan(BaseModel):
    """交易计划（v0.8.5 — TradePlan 子系统）

    建仓时根据当时所有信息（技术面/消息面/前景）一次性定下：
    入场理由、止损、止盈分档、失效条件、最长持有期。
    之后每天只检查"计划条件是否触发"，不再被日常波动牵着走。

    设计原则（来源：策略库望周知大纲 ch40-41 + 第48章止损位设置）：
    1. 七要素：买什么 / 为什么买 / 什么时候买 / 买多少 / 止盈目标 / 失效条件 / 最长持有期
    2. 止损单向上移：current_stop ≥ initial_stop，trailing 后只升不降
    3. 计划可调整但需"科学统计 + 金融分析"依据：每次 adjustments 必须有 source 锚点
    4. AI 辅助建议、用户最终决策：所有调整需 source=user 才真正生效
    """

    # 七要素（望周知大纲 ch40-41）
    plan_id: str = Field(description="计划唯一标识（uuid 或 stockcode_opentdate）")
    opened_at: str = Field(description="建仓日期 YYYY-MM-DD")
    why_buy: str = Field(description="为什么买 — thesis 文本（AI 生成 + 用户编辑）")
    when_buy: str = Field(description="什么时候买 — 进场触发条件描述（如：MA20 上方 + 量比 1.5）")
    how_much: float = Field(description="目标仓位比例（0-1）")
    when_sell_targets: list[float] = Field(
        default_factory=list,
        description="止盈分档目标价（绝对价，如 [1650.0, 1800.0]）"
    )
    when_sell_invalidate: list[str] = Field(
        default_factory=list,
        description="失效条件文本列表（如['跌破MA60+成交量异常', '出现重大利空']）"
    )

    # 风控核心
    locked_initial_stop: float = Field(description="建仓时定的初始止损价（绝对价，单向上移基线）")
    current_stop: float = Field(description="当前止损价（≥ initial_stop，trailing 后单调上移）")
    max_hold_days: int = Field(default=90, description="最长持有天数（中线 90 / 短线 30 / 趋势 180）")

    # 基本面/前景（AI 生成，用户可改）
    fundamental_outlook: Literal["bullish", "neutral", "bearish"] = Field(
        default="neutral",
        description="基本面前景判断 — 影响 PlanGuard 是否压制 weak_sell"
    )
    # v0.8.6.3 笨总剑宗/气宗模式（ISS-033 气宗持有，调研报告 v0.8.6.3 阶段）
    # None=未设定(向后兼容,PlanGuard 仅压 weak_sell) / qizong=气宗长期格局(压 trend_exit+weak_sell) /
    # jianzong=剑宗一波流(不压,破线即走)
    mode: Optional[Literal["qizong", "jianzong"]] = Field(
        default=None,
        description="笨总模式: qizong=气宗(长期格局,持有期长,压制技术卖出) / jianzong=剑宗(一波流,破线即走)"
    )
    thesis_sources: list[str] = Field(
        default_factory=list,
        description="AI 引用的策略库章节 / 新闻摘要锚点（如['ch48-止损方法', 'news-2026-06-15-消费板块修复']）"
    )

    # 调整审计
    adjustments: list[dict] = Field(
        default_factory=list,
        description="计划调整历史 [{date, field, old, new, reason, source}]，source: user / ai_suggested / auto_trailing"
    )


class StockData(BaseModel):
    """股票数据结构"""
    stock_code: str = Field(description="股票代码，如 600519")
    stock_name: str = Field(description="股票名称，如 贵州茅台")

    # 价格数据
    price: float = Field(description="当前价格")
    open: Optional[float] = Field(default=None, description="开盘价")
    high: Optional[float] = Field(default=None, description="最高价")
    low: Optional[float] = Field(default=None, description="最低价")

    # 均线数据
    ma5: Optional[float] = Field(default=None, description="5日均线")
    ma10: Optional[float] = Field(default=None, description="10日均线")
    ma20: Optional[float] = Field(default=None, description="20日均线")
    ma60: Optional[float] = Field(default=None, description="60日均线")
    ma120: Optional[float] = Field(default=None, description="120日均线")
    ma200: Optional[float] = Field(default=None, description="200日均线")

    # 成交量数据
    volume: float = Field(description="当前成交量")
    avg_volume_5: Optional[float] = Field(default=None, description="5日均量")
    avg_volume_20: Optional[float] = Field(default=None, description="20日均量")

    # 常用指标
    change_pct: Optional[float] = Field(default=None, description="涨跌幅(%)")

    # 位置信息
    high_60d: Optional[float] = Field(default=None, description="60日高点")
    low_60d: Optional[float] = Field(default=None, description="60日低点")
    high_120d: Optional[float] = Field(default=None, description="120日高点")
    low_120d: Optional[float] = Field(default=None, description="120日低点")

    # ===== MACD指标 =====
    macd_dif: Optional[float] = Field(default=None, description="MACD-DIF差值线")
    macd_dea: Optional[float] = Field(default=None, description="MACD-DEA信号线")
    macd_hist: Optional[float] = Field(default=None, description="MACD柱状图")

    # ===== RSI指标 =====
    rsi_6: Optional[float] = Field(default=None, description="RSI-6日")
    rsi_12: Optional[float] = Field(default=None, description="RSI-12日")
    rsi_24: Optional[float] = Field(default=None, description="RSI-24日")

    # ===== 布林带指标 =====
    boll_upper: Optional[float] = Field(default=None, description="布林带上轨")
    boll_mid: Optional[float] = Field(default=None, description="布林带中轨")
    boll_lower: Optional[float] = Field(default=None, description="布林带下轨")

    # ===== KDJ指标 =====
    kdj_k: Optional[float] = Field(default=None, description="KDJ-K值")
    kdj_d: Optional[float] = Field(default=None, description="KDJ-D值")
    kdj_j: Optional[float] = Field(default=None, description="KDJ-J值")


    # ===== ATR 波动率指标 =====
    atr_14: Optional[float] = Field(default=None, description="ATR(14) 平均真实波幅")
    high_since_entry: Optional[float] = Field(default=None, description="持仓期间最高价（追踪止损用，DataFeeder置None，回测引擎填充）")

    # 多时间框架数据
    monthly: Optional[dict] = Field(default=None, description="月线数据")
    weekly: Optional[dict] = Field(default=None, description="周线数据")

    # ===== 大盘环境数据 =====
    index_trend: Optional[str] = Field(default=None, description="大盘趋势: BULLISH/BEARISH/NEUTRAL")
    index_ma20: Optional[float] = Field(default=None, description="沪深300 MA20")
    index_ma60: Optional[float] = Field(default=None, description="沪深300 MA60")
    index_ma250: Optional[float] = Field(default=None, description="沪深300 MA250(年线，牛熊分界线)")
    index_close: Optional[float] = Field(default=None, description="沪深300最新收盘价")
    index_change_pct: Optional[float] = Field(default=None, description="沪深300涨跌幅(%)")
    index_high_250d: Optional[float] = Field(default=None, description="沪深300近250日最高价(用于计算回撤幅度)")

    class Config:
        extra = "allow"  # 允许额外字段


class StrategyState(BaseModel):
    """策略层状态（v0.7.2 Strategy Layer持久化状态）

    跨交易日持久化，实现"决策有状态"。
    每日分析时读取上一天的StrategyState，生成新的StrategyState。
    """
    # 交易生命周期
    lifecycle: TradeLifecycle = Field(default=TradeLifecycle.FLAT, description="当前交易生命周期状态")

    # 持仓信息
    entry_date: Optional[str] = Field(default=None, description="开仓日期")
    entry_price: Optional[float] = Field(default=None, description="开仓均价")
    current_position_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="当前仓位比例")

    # v0.8.3 Phase D: 金字塔仓位档位`n    position_tier: PositionTier = Field(default=PositionTier.FLAT, description=`"当前仓位档位（试探/基础/重仓）`")`n    unrealized_profit_pct: float = Field(default=0.0, description=`"浮动盈亏百分比`")`n    days_held: int = Field(default=0, description=`"持仓天数`")`n`n    # 信号历史（用于信号确认和稳定性评估）
    recent_signals: list[str] = Field(default_factory=list, description="最近N个交易日的信号序列(BUY/SELL/HOLD/WATCH)")
    signal_history_maxlen: int = Field(default=5, description="信号历史最大长度")

    # 决策惯性
    inertia_counter: int = Field(default=0, description="当前决策已持续的交易日数")
    last_decision: Optional[SignalType] = Field(default=None, description="上一个交易日的最终决策")

    # 冷却期
    cooldown_remaining: int = Field(default=0, description="冷却期剩余交易日数")
    cooldown_reason: Optional[str] = Field(default=None, description="冷却原因")

    # 反转成本累计
    reverse_count: int = Field(default=0, description="本轮交易中的方向反转次数")
    total_commission_paid: float = Field(default=0.0, description="本轮交易已支付的总佣金")

    # 信号稳定性
    signal_stability_score: float = Field(default=1.0, ge=0.0, le=1.0, description="信号稳定性评分(0-1)，频繁变化→低分")

    # 减仓保护期
    last_reduce_date: Optional[str] = Field(default=None, description="上次减仓日期")
    last_reduce_reason: Optional[str] = Field(default=None, description="上次减仓原因")
    reduce_protection_remaining: int = Field(default=0, description="减仓保护期剩余天数")
    min_hold_remaining: int = Field(default=0, description="最短持有窗口剩余天数")
    add_protection_remaining: int = Field(default=0, description="加仓保护期剩余天数")

    def push_signal(self, signal: SignalType):
        """记录今日信号，维护滑动窗口"""
        self.recent_signals.append(signal.value)
        if len(self.recent_signals) > self.signal_history_maxlen:
            self.recent_signals.pop(0)

    def update_stability(self):
        """根据信号历史更新稳定性评分

        评分逻辑：
        - 全部相同 = 1.0（最稳定）
        - 每次方向变化扣分
        - 最终评分 = 1 - 变化率
        """
        if len(self.recent_signals) < 2:
            self.signal_stability_score = 1.0
            return

        changes = 0
        for i in range(1, len(self.recent_signals)):
            if self.recent_signals[i] != self.recent_signals[i - 1]:
                changes += 1

        max_changes = len(self.recent_signals) - 1
        self.signal_stability_score = round(1.0 - (changes / max_changes), 2)

    def tick_cooldown(self):
        """冷却期递减（每个交易日调用）"""
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            if self.cooldown_remaining == 0:
                self.cooldown_reason = None

    def tick_reduce_protection(self):
        """减仓保护期递减"""
        if self.reduce_protection_remaining > 0:
            self.reduce_protection_remaining -= 1
            if self.reduce_protection_remaining == 0:
                self.last_reduce_reason = None

    def tick_min_hold(self):
        """最短持有窗口递减"""
        if self.min_hold_remaining > 0:
            self.min_hold_remaining -= 1

    def tick_add_protection(self):
        """加仓保护期递减"""
        if self.add_protection_remaining > 0:
            self.add_protection_remaining -= 1


class ExecutionConstraint(BaseModel):
    """执行约束（v0.7.2 Execution Layer）

    将"理想交易"转为"可执行交易"的现实约束。
    """
    # 滑点（与波动率正相关）
    slippage_pct: float = Field(default=0.001, description="基础滑点百分比")
    volatility_slippage: bool = Field(default=True, description="是否启用波动率滑点（滑点与ATR/波动率挂钩）")

    # 流动性
    min_volume_ratio: float = Field(default=0.3, description="最低成交量比率（低于20日均量的30%禁止交易）")

    # 涨跌停
    limit_up_blocked: bool = Field(default=True, description="涨停时禁止买入（A股：涨停封板买不到）")
    limit_down_blocked: bool = Field(default=True, description="跌停时禁止卖出（A股：跌停封板卖不出）")

    # 冲击成本
    impact_cost_enabled: bool = Field(default=True, description="是否启用冲击成本计算")
    impact_cost_rate: float = Field(default=0.001, description="冲击成本比率（与成交量比例相关）")


class StrategyDecision(BaseModel):
    """策略层决策结果（v0.7.2）

    Strategy Layer的输出，包含：
    - 经过惯性/确认/冷却/反转成本过滤后的稳定决策
    - 交易生命周期转换
    - 仓位建议
    """
    # 最终决策（经过策略层过滤后）
    decision: SignalType = Field(description="策略层最终决策")
    position_action: PositionAction = Field(default=PositionAction.STAY_OUT, description="仓位动作")
    action_semantic: Optional[str] = Field(default=None, description="收紧后的交易语义: ENTRY/ADD/HOLD/TRIM/EXIT/STOP")
    sell_path: Optional[str] = Field(default=None, description="卖出路径: flat_sell/stop_loss_trim/stop_loss_exit/take_profit_trim/trend_exit/weak_sell/top_signal")
    position_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="建议仓位比例")
    # 跳法A 阶段2: 高位止盈3维度大顶信号（宏观/板块/个股），非空=强制离场，PlanGuard 不可压制
    top_signal: Optional[str] = Field(default=None, description="高位止盈大顶信号描述（如'宏观:成交额破10万亿'），触发即强制 SELL")

    # 策略层过滤信息
    lifecycle_before: TradeLifecycle = Field(description="过滤前生命周期状态")
    lifecycle_after: TradeLifecycle = Field(description="过滤后生命周期状态")
    inertia_applied: bool = Field(default=False, description="是否应用了决策惯性（抑制方向变化）")
    confirmation_required: bool = Field(default=False, description="信号是否需要确认（单日信号未直接触发）")
    cooldown_blocked: bool = Field(default=False, description="冷却期是否阻止了操作")
    reverse_cost_paid: bool = Field(default=False, description="是否支付了反转成本")
    stability_adjusted: bool = Field(default=False, description="是否因信号不稳定调整了权重")

    # 更新后的策略状态
    new_state: StrategyState = Field(description="更新后的策略层状态")

    # 决策理由
    strategy_reasons: list[str] = Field(default_factory=list, description="策略层决策理由")

    # v0.8.3 Phase C: 买卖点计算结果
    entry_exit: Optional[dict] = Field(default=None, description="买卖点计算结果（EntryExitResult序列化）")

    # v0.8.3 收尾: 买卖点与AI情绪分歧
    divergence: Optional[dict] = Field(default=None, description="买卖点技术信号与AI情绪的分歧检测（type/technical_signal/ai_sentiment/warning）")


class AIModifierResult(BaseModel):
    """AI调节层输出（v0.8.3）

    AI只负责信息理解和情绪判断，不负责决策输出和交易执行。
    三层调节机制：
    1. 信号调节: buy_score *= (1 - confidence × sentiment_weight)
    2. 仓位调节: 高风险→max_position *= risk_position_cap
    3. 状态干预: event_type == "black_swan" → force PANIC

    v0.8.3 新增：
    4. tech_context_awareness: AI 是否结合技术面背景分析新闻
    5. tech_context_used: AI 引用了哪些技术面要素
    """
    sentiment: str = Field(default="neutral", description="情绪倾向: bullish/bearish/neutral")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度 0-1")
    risk_level: str = Field(default="low", description="风险等级: low/medium/high")
    narrative_shift: bool = Field(default=False, description="叙事是否发生重大转变")
    event_type: str = Field(default="none", description="事件类型: policy/war/earnings/macro/black_swan/none")
    summary: str = Field(default="", description="一句话摘要")
    key_events: list[str] = Field(default_factory=list, description="关键事件列表")
    source_count: int = Field(default=0, description="分析的新闻条数")
    adjusted: bool = Field(default=False, description="是否对信号进行了调节")

    # 调节结果（由 apply_modification 填充）
    score_adjustment: float = Field(default=0.0, description="信号评分调节量（负数=压制买入/加强卖出）")
    position_cap: float = Field(default=1.0, ge=0.0, le=1.0, description="仓位上限调节（1.0=不限制）")
    force_state: Optional[str] = Field(default=None, description="强制市场状态（如PANIC），None=不干预")

    # 技术面感知（v0.8.3 Phase B）
    tech_context_awareness: bool = Field(
        default=False,
        description="AI 是否在分析中结合了技术面背景"
    )
    tech_context_used: list[str] = Field(
        default_factory=list,
        description="AI 分析中引用的技术面要素（如: 趋势方向, 均线位置, 成交量, 大盘环境）"
    )


class SkillSignal(BaseModel):
    """技能信号输出"""
    skill_name: str = Field(description="技能名称")
    skill_alias: str = Field(description="技能中文别名")
    signal: SignalType = Field(description="信号类型")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度 0-1")
    reason: list[str] = Field(default_factory=list, description="判断理由")
    metadata: dict = Field(default_factory=dict, description="额外数据")
    skill_type: str = Field(default="base", description="技能类型: base/regulator/action")
    position_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="建议仓位比例(0-1)，None表示不指定")


class DecisionTrace(BaseModel):
    """决策追溯 - 记录决策引擎每一步的状态"""
    step: str = Field(description="步骤名称")
    description: str = Field(description="步骤描述")
    data: dict = Field(default_factory=dict, description="步骤数据（如分数、系数等）")


class DecisionResult(BaseModel):
    """最终决策结果"""
    stock: StockData = Field(description="股票数据")
    state: MarketState = Field(description="市场状态")
    decision: SignalType = Field(description="最终决策")
    score: float = Field(ge=0.0, le=1.0, description="综合评分")
    signals: list[SkillSignal] = Field(default_factory=list, description="各技能信号")
    reason: list[str] = Field(default_factory=list, description="决策理由")
    warnings: list[str] = Field(default_factory=list, description="风险提示")
    trace: list[DecisionTrace] = Field(default_factory=list, description="决策追溯路径")
    position_action: PositionAction = Field(default=PositionAction.STAY_OUT, description="仓位动作")
    position_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="建议仓位比例(0-1)")

    # v0.8.3 Phase C: 买卖点覆盖标记
    overridden_by: Optional[str] = Field(default=None, description="被覆盖来源（entry_exit/ai_modifier/stop_loss等）")
    overridden_reason: str = Field(default="", description="覆盖原因")


# ===== 回测相关模型 =====

class TradeRecord(BaseModel):
    """单笔交易记录"""
    date: str = Field(description="交易日期 YYYY-MM-DD")
    action: str = Field(description="操作: BUY/SELL")
    price: float = Field(description="成交价格")
    shares: int = Field(description="成交股数(手×100)")
    amount: float = Field(description="成交金额")
    reason: str = Field(default="", description="交易原因")
    signal_score: float = Field(default=0.0, description="信号评分")
    position_action: str = Field(default="", description="仓位动作: OPEN/ADD/REDUCE/CLOSE_ALL")
    action_semantic: Optional[str] = Field(default=None, description="收紧后的交易语义: ENTRY/ADD/HOLD/TRIM/EXIT/STOP")
    sell_path: Optional[str] = Field(default=None, description="卖出路径: flat_sell/stop_loss_trim/stop_loss_exit/take_profit_trim/trend_exit/weak_sell")
    position_ratio_after: float = Field(default=0.0, description="交易后仓位比例")


class DailySnapshot(BaseModel):
    """每日账户快照"""
    date: str = Field(description="日期")
    price: float = Field(description="当日收盘价")
    cash: float = Field(description="现金")
    position: int = Field(default=0, description="持仓股数")
    market_value: float = Field(default=0.0, description="持仓市值")
    total_value: float = Field(description="总资产(现金+市值)")
    return_pct: float = Field(default=0.0, description="累计收益率%")


class BacktestResult(BaseModel):
    """回测结果"""
    stock_code: str = Field(description="股票代码")
    stock_name: str = Field(default="", description="股票名称")
    start_date: str = Field(description="回测起始日期")
    end_date: str = Field(description="回测结束日期")
    initial_capital: float = Field(description="初始资金")
    layer_mode: str = Field(default="decision_strategy_execution", description="回测层级模式")
    final_value: float = Field(description="最终总资产")
    total_return_pct: float = Field(description="总收益率%")
    annualized_return_pct: float = Field(default=0.0, description="年化收益率%")
    max_drawdown_pct: float = Field(default=0.0, description="最大回撤%")
    win_rate: float = Field(default=0.0, description="胜率%")
    profit_loss_ratio: float = Field(default=0.0, description="盈亏比")
    sharpe_ratio: float = Field(default=0.0, description="夏普比率")
    total_trades: int = Field(default=0, description="总交易次数")
    buy_count: int = Field(default=0, description="买入次数")
    sell_count: int = Field(default=0, description="卖出次数")
    trades: list[TradeRecord] = Field(default_factory=list, description="交易记录")
    daily_snapshots: list[DailySnapshot] = Field(default_factory=list, description="每日快照")
    benchmark_return_pct: float = Field(default=0.0, description="基准收益率%(买入持有)")
    invested_return_pct: float = Field(default=0.0, description="投入资金收益率%(盈利/实际投入成本)")

    # v0.7.2 稳定性指标
    worst_case_return_pct: float = Field(default=0.0, description="最差收益%(Monte Carlo最差路径)")
    drawdown_stability: float = Field(default=0.0, description="回撤稳定性(回撤标准差，越小越稳定)")
    result_variance: float = Field(default=0.0, description="结果方差(Monte Carlo多次模拟的收益方差)")
    decision_stability: float = Field(default=0.0, description="决策稳定性(方向反转次数/总决策数)")
    mc_simulations: int = Field(default=0, description="Monte Carlo模拟次数(0=单次回测)")

    # v0.7.2 执行约束统计
    blocked_by_limit_up: int = Field(default=0, description="因涨停无法买入次数")
    blocked_by_limit_down: int = Field(default=0, description="因跌停无法卖出次数")
    blocked_by_liquidity: int = Field(default=0, description="因流动性不足被阻止交易次数")
    blocked_by_no_open_price: int = Field(default=0, description="因开盘价缺失跳过执行次数（v0.8.2前视偏差修复）")

    # ISS-034 高价股 0 交易诊断（2026-06-18）
    rejected_insufficient_funds: int = Field(default=0, description="BUY 信号因目标增量买不到 1 手被拒次数（典型为高价股资金不足）")
    rejected_below_target: int = Field(default=0, description="BUY 信号因已达目标仓位被拒次数")
    rejected_zero_shares: int = Field(default=0, description="BUY 信号因计算后买入手数为 0 被拒次数")

    # v0.8.2 诊断导出
    diagnostics: dict = Field(default_factory=dict, description="回测中间决策与执行诊断日志")


class ScanCandidate(BaseModel):
    """Scanner初筛候选股（v0.8.0 Phase 2）"""
    stock_code: str = Field(description="股票代码")
    stock_name: str = Field(description="股票名称")
    price: float = Field(default=0.0, description="最新价")
    change_pct: Optional[float] = Field(default=None, description="涨跌幅(%)")
    turnover_rate: Optional[float] = Field(default=None, description="换手率(%)")
    volume_ratio: Optional[float] = Field(default=None, description="量比")
    amplitude: Optional[float] = Field(default=None, description="振幅(%)")
    amount: Optional[float] = Field(default=None, description="成交额")
    pe_ratio: Optional[float] = Field(default=None, description="市盈率-动态")
    pb_ratio: Optional[float] = Field(default=None, description="市净率")
    total_mv: Optional[float] = Field(default=None, description="总市值")
    industry: Optional[str] = Field(default=None, description="所属行业")
    change_60d: Optional[float] = Field(default=None, description="60日涨跌幅(%) v0.8.4")
    matched_rules: list[str] = Field(default_factory=list, description="命中的规则名")


class MarketEvent(BaseModel):
    """市场事件（v0.8.0 Phase 3）

    Event Layer 主动检测的市场事件，影响交易策略调整。
    检测方式：关键词匹配 / AI分类 / 市场规则触发
    """
    event_type: str = Field(description="事件类型: policy/war/earnings/macro/black_swan/market_crash")
    sentiment: str = Field(default="neutral", description="情绪倾向: bullish/bearish/neutral")
    impact_level: int = Field(default=1, ge=1, le=5, description="影响等级 1-5 (5=黑天鹅)")
    scope: str = Field(default="market", description="影响范围: market/sector/stock")
    duration: str = Field(default="short", description="预期持续: short/medium/long")
    source: str = Field(default="", description="事件来源（新闻标题/规则名）")
    summary: str = Field(default="", description="事件摘要")
    affected_codes: list[str] = Field(default_factory=list, description="受影响的股票代码")
    affected_sectors: list[str] = Field(default_factory=list, description="受影响的行业")
    timestamp: str = Field(default="", description="检测时间 ISO8601")
    detection_method: str = Field(default="keyword", description="检测方式: keyword/ai/rule")
    # v0.8.6.3 笨总现象级事件四要素（ISS-041 events，仅 AI 检测的事件填充）
    # 真实性/传播性/规模性/时效性 各 0-100，None 表示未判定（关键词/规则事件）
    # 真实性是 AI 基于单条文本的疑似判断，非多源交叉验证，低真实性时事件降级+提示用户核实
    four_elements: Optional[dict] = Field(default=None, description="笨总四要素 {authenticity, virality, scale, timeliness}")


class DimensionScore(BaseModel):
    """排名维度评分（v0.8.0 Phase 4）"""
    name: str = Field(description="维度名: technical/sentiment/liquidity/volatility")
    score: float = Field(ge=0.0, le=100.0, description="维度得分 0-100")
    weight: float = Field(ge=0.0, le=1.0, description="实际权重（归一化后）")
    contribution: float = Field(description="维度贡献分 = score × weight")
    detail: str = Field(default="", description="评分说明（可解释性）")


class RankingResult(BaseModel):
    """排名结果（v0.8.0 Phase 4）"""
    stock_code: str = Field(description="股票代码")
    stock_name: str = Field(default="", description="股票名称")
    total_score: float = Field(ge=0.0, le=100.0, description="综合得分 0-100")
    rank: int = Field(ge=0, description="排名（1=最佳，0=未排名）")
    dimensions: list[DimensionScore] = Field(default_factory=list, description="各维度评分明细")
    decision: str = Field(default="", description="决策: BUY/SELL/HOLD/WATCH")
    ai_enabled: bool = Field(default=True, description="排名时AI是否启用")
