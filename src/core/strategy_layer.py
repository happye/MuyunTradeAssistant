"""策略层（Strategy Layer） - v0.7.2 核心新增模块

系统目标从"提高预测准确率"升级为"构建具备一致性、抗噪性与现实约束的交易决策系统"。

Strategy Layer 的本质不是"分析"，而是：
- 约束行为
- 延迟决策
- 抑制噪声
- 维持一致性

核心机制：
1. 决策惯性（Inertia）：已持仓时，对反向信号提高门槛，防止短期噪声触发反转
2. 信号确认（Confirmation）：单日信号不直接触发决策，必须连续出现或增强
3. 冷却机制（Cooldown）：平仓后限制短期内反向操作，防止来回打脸
4. 反转成本（Reverse Cost）：每次反向操作必须"付费"，成本与持仓时间、波动率相关
5. 信号稳定性评估：信号变化频繁→权重下降，稳定信号→权重上升

决策原则（高于所有规则）：
- 原则1：信号可以变，决策必须稳定
- 原则2：方向改变必须付出成本
- 原则3：优先避免错误交易，而非追求机会最大化
"""

import logging
from typing import Optional
from copy import deepcopy

from src.data.models import (PositionTier,
    SignalType, MarketState, PositionAction,
    TradeLifecycle, StrategyState, StrategyDecision,
    infer_action_semantic,
    DecisionResult, StockData
)

logger = logging.getLogger(__name__)


class StrategyLayer:
    """策略层 - 约束行为、延迟决策、抑制噪声、维持一致性

    输入：Decision Layer的信号聚合结果（决策信号+评分）
    输出：经过策略层过滤后的稳定决策（StrategyDecision）

    与旧架构的关键区别：
    - 旧：每日独立判断 → 行为不连续
    - 新：有状态决策 → 行为连续、一致、可控
    """

    # ===== 参数配置 =====

    # 决策惯性参数
    INERTIA_MIN_DAYS = 3           # 最小惯性天数：决策持续不到3天时，反向信号门槛提高
    INERTIA_BUY_THRESHOLD_BUMP = 0.10   # 持仓时反向(SELL)信号门槛提高10%
    INERTIA_SELL_THRESHOLD_BUMP = 0.10  # 空仓时反向(BUY)信号门槛提高10%

    # 信号确认参数
    CONFIRMATION_MIN_CONSECUTIVE = 2     # 连续出现N天相同信号才确认
    CONFIRMATION_WEAK_SIGNAL_THRESHOLD = 0.30  # 弱信号阈值（低于此值需要确认）

    # 冷却期参数
    COOLDOWN_AFTER_CLOSE_DAYS = 5       # 清仓后冷却天数（防止来回打脸）
    COOLDOWN_AFTER_REDUCE_DAYS = 10     # 减仓后冷却天数

    # 反转成本参数
    REVERSE_COST_BASE_PCT = 0.003       # 反转基础成本（0.3%，约一次往返佣金）
    REVERSE_COST_PER_REVERSE = 0.002    # 每次额外反转增加0.2%成本

    # 信号稳定性参数
    STABILITY_LOW_THRESHOLD = 0.4       # 稳定性低于0.4时，信号权重打折
    STABILITY_DISCOUNT = 0.7            # 不稳定信号的权重打折系数

    # 极端情况自动感知参数（v0.7.2新增）
    EXTREME_DROP_THRESHOLD = -0.05      # 单日跌幅超过5%视为极端
    EXTREME_RISE_THRESHOLD = 0.07       # 单日涨幅超过7%视为极端
    EXTREME_PANIC_SHORTEN_INERTIA = 1   # 极端时惯性天数从3→1（止损要快）
    EXTREME_PANIC_SKIP_CONFIRM = True   # 极端时止损信号跳过确认（不用等2天）
    EXTREME_PANIC_SHORTEN_COOLDOWN = 2  # 极端时冷却期从5→2天（快速离场后可更快重评）

    # 仓位管理参数（从decision_engine迁移过来）
    POSITION_CAPS = {
        MarketState.RISK_ON: 0.60,
        MarketState.TRANSITION: 0.30,
        MarketState.RISK_OFF: 0.15,
        MarketState.PANIC: 0.0,
    }
    OPEN_RATIO = 0.20
    ADD_RATIO = 0.20
    # ISS-033 调参（2026-06-18）：第二轮回测显示宁德/工行错过 50pp 牛市，
    # 根因 take_profit 在 5% 浮盈即触发分批 + 60% 保留导致仓位反复掉落。
    # 0.60→0.75（少卖 15pp）+ 0.05→0.10（10% 浮盈门槛）让趋势仓位活久点。
    TAKE_PROFIT_KEEP = 0.75
    NORMAL_REDUCE_KEEP = 0.65
    STOP_LOSS_REDUCE_THRESHOLD = 0.70
    STOP_LOSS_EXIT_THRESHOLD = 0.85
    TAKE_PROFIT_TRIM_THRESHOLD = 0.70
    TAKE_PROFIT_MIN_GAIN_PCT = 0.10
    STOP_LOSS_EXIT_LOSS_PCT = -0.05
    STRONG_SELL_EXIT_THRESHOLD = 0.55
    SELL_DOMINANCE_GAP = 0.15
    TREND_EXIT_BREAK_PCT = -0.02

    # ISS-033 二阶段（2026-06-18）：MarketState 感知的仓位执行参数
    # 牛市少卖晚卖让趋势仓位活久点 / 熊市多卖快卖控回撤 / 震荡按当前默认。
    # 这是把 take_profit_trim 与 trend_exit 这两条 sell_path 与市场状态接通的最小切片。
    STATE_TUNED_PARAMS = {
        MarketState.RISK_ON: {
            "tier_label": "牛市档",
            "take_profit_keep": 0.85,        # 卖 15%（vs 默认卖 25%）
            "take_profit_min_gain_pct": 0.15,# 浮盈 ≥15% 才走止盈分批（vs 10%）
            "trend_exit_break_pct": -0.05,   # 浮亏 -5% 才考虑趋势退出（vs -2%）
        },
        MarketState.TRANSITION: {
            "tier_label": "震荡档",
            "take_profit_keep": 0.75,
            "take_profit_min_gain_pct": 0.10,
            "trend_exit_break_pct": -0.02,
        },
        MarketState.RISK_OFF: {
            "tier_label": "熊市档",
            "take_profit_keep": 0.50,        # 卖 50% 快速回血
            "take_profit_min_gain_pct": 0.05,# 浮盈 ≥5% 即可止盈
            "trend_exit_break_pct": -0.01,   # 浮亏 -1% 即考虑趋势退出
        },
        MarketState.PANIC: {
            "tier_label": "恐慌档",
            "take_profit_keep": 0.30,        # 几乎全卖
            "take_profit_min_gain_pct": 0.03,
            "trend_exit_break_pct": 0.0,     # 任何浮亏即退出
        },
    }
    # 跳法A（MarketState 降级）：气宗固定长持参数，不随市况分档。
    # 取牛市档口径（晚卖少卖、宽趋势退出），让中长期格局仓位拿住整波，
    # 卖出交由 PlanGuard 致命止损 / 时间止损 / 高位止盈3维度（P0/P1/P2）。
    QIZONG_FIXED_PARAMS = {
        "tier_label": "气宗档",
        "take_profit_keep": 0.85,
        "take_profit_min_gain_pct": 0.15,
        "trend_exit_break_pct": -0.05,
    }
    MIN_HOLD_DAYS = 5
    ADD_PROTECTION_DAYS = 5
    REDUCE_PROTECTION_DAYS = {
        "stop_loss_trim": 15,
        "take_profit_trim": 10,
        "weak_sell": 15,
    }
    MIN_HOLD_BLOCK_SELL_PATHS = {
        "stop_loss_trim",
        "take_profit_trim",
        "weak_sell",
    }

    def __init__(self, pyramid_config: Optional[dict] = None):
        """初始化策略层"""
        self._active_mode = None  # 跳法A: 当前持仓笨总 mode（process 透传），气宗走固定长持参数
        # pyramid_config 保留参数（向后兼容调用方）；金字塔仓位管理已移除（从未生效，审查 chat-M3）


    def get_params(self) -> dict:
        """返回策略层所有参数的快照（用于偏差审计）

        Returns:
            dict: 参数名→参数值映射
        """
        return {
            "INERTIA_MIN_DAYS": self.INERTIA_MIN_DAYS,
            "INERTIA_BUY_THRESHOLD_BUMP": self.INERTIA_BUY_THRESHOLD_BUMP,
            "INERTIA_SELL_THRESHOLD_BUMP": self.INERTIA_SELL_THRESHOLD_BUMP,
            "CONFIRMATION_MIN_CONSECUTIVE": self.CONFIRMATION_MIN_CONSECUTIVE,
            "CONFIRMATION_WEAK_SIGNAL_THRESHOLD": self.CONFIRMATION_WEAK_SIGNAL_THRESHOLD,
            "COOLDOWN_AFTER_CLOSE_DAYS": self.COOLDOWN_AFTER_CLOSE_DAYS,
            "COOLDOWN_AFTER_REDUCE_DAYS": self.COOLDOWN_AFTER_REDUCE_DAYS,
            "REVERSE_COST_BASE_PCT": self.REVERSE_COST_BASE_PCT,
            "REVERSE_COST_PER_REVERSE": self.REVERSE_COST_PER_REVERSE,
            "STABILITY_LOW_THRESHOLD": self.STABILITY_LOW_THRESHOLD,
            "STABILITY_DISCOUNT": self.STABILITY_DISCOUNT,
            "EXTREME_DROP_THRESHOLD": self.EXTREME_DROP_THRESHOLD,
            "EXTREME_RISE_THRESHOLD": self.EXTREME_RISE_THRESHOLD,
            "EXTREME_PANIC_SHORTEN_INERTIA": self.EXTREME_PANIC_SHORTEN_INERTIA,
            "EXTREME_PANIC_SKIP_CONFIRM": self.EXTREME_PANIC_SKIP_CONFIRM,
            "EXTREME_PANIC_SHORTEN_COOLDOWN": self.EXTREME_PANIC_SHORTEN_COOLDOWN,
            "POSITION_CAPS": {k.value: v for k, v in self.POSITION_CAPS.items()},
            "OPEN_RATIO": self.OPEN_RATIO,
            "ADD_RATIO": self.ADD_RATIO,
            "TAKE_PROFIT_KEEP": self.TAKE_PROFIT_KEEP,
            "NORMAL_REDUCE_KEEP": self.NORMAL_REDUCE_KEEP,
            "STOP_LOSS_REDUCE_THRESHOLD": self.STOP_LOSS_REDUCE_THRESHOLD,
            "STOP_LOSS_EXIT_THRESHOLD": self.STOP_LOSS_EXIT_THRESHOLD,
            "TAKE_PROFIT_TRIM_THRESHOLD": self.TAKE_PROFIT_TRIM_THRESHOLD,
            "TAKE_PROFIT_MIN_GAIN_PCT": self.TAKE_PROFIT_MIN_GAIN_PCT,
            "STOP_LOSS_EXIT_LOSS_PCT": self.STOP_LOSS_EXIT_LOSS_PCT,
            "STRONG_SELL_EXIT_THRESHOLD": self.STRONG_SELL_EXIT_THRESHOLD,
            "SELL_DOMINANCE_GAP": self.SELL_DOMINANCE_GAP,
            "TREND_EXIT_BREAK_PCT": self.TREND_EXIT_BREAK_PCT,
            "MIN_HOLD_DAYS": self.MIN_HOLD_DAYS,
            "ADD_PROTECTION_DAYS": self.ADD_PROTECTION_DAYS,
        }

    def process(
        self,
        decision_result: DecisionResult,
        strategy_state: StrategyState,
        data: StockData,
    ) -> StrategyDecision:
        """处理信号聚合结果，输出经过策略层过滤的稳定决策

        核心流程：
        1. 更新信号历史和稳定性评分
        2. 应用决策惯性（抑制方向变化）
        3. 应用信号确认（单日信号需确认）
        4. 检查冷却期（阻止频繁操作）
        5. 计算反转成本（方向改变需付费）
        6. 计算仓位管理
        7. 更新交易生命周期状态
        8. 生成策略层决策结果

        Args:
            decision_result: Decision Layer输出的信号聚合结果
            strategy_state: 上一交易日的策略层状态
            data: 当前股票数据

        Returns:
            StrategyDecision: 策略层最终决策
        """
        # 深拷贝状态，避免修改原始数据
        new_state = deepcopy(strategy_state)

        # 冷却期递减
        new_state.tick_cooldown()
        new_state.tick_reduce_protection()
        new_state.tick_min_hold()
        new_state.tick_add_protection()

        # 当前决策（Decision Layer输出）
        raw_decision = decision_result.decision
        raw_score = decision_result.score
        market_state = decision_result.state

        # ===== 极端情况自动感知（v0.7.2） =====
        is_extreme = self._detect_extreme_condition(data, market_state, decision_result)

        # Step 1: 更新信号历史和稳定性
        new_state.push_signal(raw_decision)
        new_state.update_stability()

        # Step 2: 应用信号稳定性调整
        stability_adjusted = False
        adjusted_decision = raw_decision
        if new_state.signal_stability_score < self.STABILITY_LOW_THRESHOLD:
            # 信号不稳定：BUY/SELL降级为WATCH
            stability_adjusted = True
            if raw_decision == SignalType.BUY:
                adjusted_decision = SignalType.WATCH
                logger.info(f"信号不稳定(稳定性={new_state.signal_stability_score})，BUY降级为WATCH")
            elif raw_decision == SignalType.SELL:
                adjusted_decision = SignalType.HOLD
                logger.info(f"信号不稳定(稳定性={new_state.signal_stability_score})，SELL降级为HOLD")

        # Step 3: 应用决策惯性（极端情况下缩短惯性期）
        inertia_applied = False
        inertia_min_days = self.EXTREME_PANIC_SHORTEN_INERTIA if is_extreme else self.INERTIA_MIN_DAYS
        if self._should_apply_inertia(adjusted_decision, new_state, inertia_min_days):
            adjusted_decision = self._apply_inertia(adjusted_decision, new_state)
            inertia_applied = True

        # Step 4: 应用信号确认（极端情况下止损信号跳过确认）
        confirmation_required = False
        skip_confirm = (is_extreme and self.EXTREME_PANIC_SKIP_CONFIRM
                        and adjusted_decision == SignalType.SELL)
        if not skip_confirm and self._needs_confirmation(adjusted_decision, new_state, raw_score):
            adjusted_decision = self._apply_confirmation(adjusted_decision, new_state)
            confirmation_required = True

        # Step 5: 检查冷却期（极端情况下缩短冷却期）
        cooldown_blocked = False
        if self._is_in_cooldown(new_state, adjusted_decision, is_extreme):
            adjusted_decision = self._apply_cooldown(new_state, adjusted_decision)
            cooldown_blocked = True

        # Step 6: 计算反转成本
        reverse_cost_paid = False
        if self._is_direction_change(adjusted_decision, new_state):
            adjusted_decision = self._apply_reverse_cost(
                adjusted_decision, new_state, decision_result
            )
            reverse_cost_paid = True

        # Step 7: 计算仓位管理
        position_action, position_ratio = self._calculate_position(
            adjusted_decision, market_state, decision_result, new_state
        )

        sell_path = self._infer_sell_path(
            adjusted_decision,
            position_action,
            decision_result,
            strategy_state,
            market_state,
        )
        repeated_reduce_blocked = self._should_block_repeated_reduce(
            new_state,
            position_action,
            sell_path,
        )
        early_tactical_sell_blocked = self._should_block_early_tactical_sell(
            strategy_state,
            position_action,
            sell_path,
        )
        post_add_tactical_sell_blocked = self._should_block_post_add_tactical_sell(
            new_state,
            position_action,
            sell_path,
        )
        if repeated_reduce_blocked:
            position_action = PositionAction.HOLD_POSITION
            position_ratio = new_state.current_position_ratio
            sell_path = None
        elif early_tactical_sell_blocked:
            position_action = PositionAction.HOLD_POSITION
            position_ratio = new_state.current_position_ratio
            sell_path = None
        elif post_add_tactical_sell_blocked:
            position_action = PositionAction.HOLD_POSITION
            position_ratio = new_state.current_position_ratio
            sell_path = None

        # Step 8: 更新交易生命周期
        lifecycle_before = new_state.lifecycle
        new_state = self._update_lifecycle(
            new_state, adjusted_decision, position_action, data, position_ratio, sell_path
        )
        lifecycle_after = new_state.lifecycle

        # 更新last_decision（必须先对比旧值再写入，避免计数永远递增）
        prev_decision = new_state.last_decision
        new_state.last_decision = adjusted_decision
        if prev_decision is None or adjusted_decision != prev_decision:
            new_state.inertia_counter = 1
        else:
            new_state.inertia_counter += 1

        # 收集策略层决策理由
        strategy_reasons = self._generate_strategy_reasons(
            raw_decision, adjusted_decision, new_state,
            inertia_applied, confirmation_required,
            cooldown_blocked, reverse_cost_paid, stability_adjusted,
            is_extreme
        )
        position_reason = self._describe_position_action(
            adjusted_decision,
            position_action,
            sell_path,
            market_state,
            decision_result,
            strategy_state,
        )
        if position_reason:
            strategy_reasons.append(position_reason)
        if repeated_reduce_blocked:
            strategy_reasons.append("减仓保护期: 同一减仓原因未重复执行")
        if early_tactical_sell_blocked:
            strategy_reasons.append("最短持有窗口: 新开仓阶段不执行战术性减仓")
        if post_add_tactical_sell_blocked:
            strategy_reasons.append("加仓保护窗口: 刚加仓后不执行战术性减仓")

        # ISS-033 二阶段（用户可感知）：在策略理由首条标注当前用的参数档
        # 例如 "参数档[牛市档]: 止盈保留85% / 浮盈门槛15% / 趋势退出-5%"
        # 让用户跑 -l/-b 时能直接看见"现在系统认为这是什么市况、用了哪一档"
        tier_params = self._get_state_params(market_state)
        tier_label = tier_params["tier_label"]
        cap = self.POSITION_CAPS.get(market_state, 0.30)
        # 阶段 3.3：质量信号判定（与 _calculate_position 同口径）
        # 高质量定义：价格 > MA20 > MA60 — Weinstein S2 上升期
        _hq = (
            data.ma20 is not None and data.ma60 is not None
            and data.price > data.ma20 and data.ma20 > data.ma60
        )
        # 三阶段+3.3：强 BUY 时实际上限（牛市+高质量才走 cap，其余走 OPEN+ADD）
        buy_cap_actual = cap if (market_state == MarketState.RISK_ON and _hq) else min(self.OPEN_RATIO + self.ADD_RATIO, cap)
        # 阶段 3.3：质量信号是否成立（用户可感知）
        quality_label = "高质量(MA20+S2)" if _hq else "普通信号"
        tier_summary = (
            f"参数档[{tier_label}]: "
            f"止盈保留{int(tier_params['take_profit_keep']*100)}% / "
            f"浮盈门槛{int(tier_params['take_profit_min_gain_pct']*100)}% / "
            f"趋势退出{tier_params['trend_exit_break_pct']*100:+.0f}% / "
            f"强买上限{int(buy_cap_actual*100)}% / "
            f"{quality_label}"
        )
        strategy_reasons.insert(0, tier_summary)

        return StrategyDecision(
            decision=adjusted_decision,
            position_action=position_action,
            action_semantic=infer_action_semantic(position_action, adjusted_decision, strategy_reasons),
            sell_path=sell_path,
            position_ratio=position_ratio,
            lifecycle_before=lifecycle_before,
            lifecycle_after=lifecycle_after,
            inertia_applied=inertia_applied,
            confirmation_required=confirmation_required,
            cooldown_blocked=cooldown_blocked,
            reverse_cost_paid=reverse_cost_paid,
            stability_adjusted=stability_adjusted,
            new_state=new_state,
            strategy_reasons=strategy_reasons,
        )

    # ===== 决策惯性（Inertia） =====

    def _should_apply_inertia(self, current_decision: SignalType, state: StrategyState,
                               min_days: int = None) -> bool:
        """判断是否需要应用决策惯性

        条件：
        - 当前决策与上一次决策方向相反
        - 上一次决策持续时间 < min_days（默认INERTIA_MIN_DAYS，极端时缩短）

        Args:
            current_decision: 当前决策
            state: 策略状态
            min_days: 惯性最小天数（None=使用默认值，极端情况传入缩短值）
        """
        if state.last_decision is None:
            return False

        threshold = min_days if min_days is not None else self.INERTIA_MIN_DAYS
        if state.inertia_counter >= threshold:
            return False  # 持续够久了，允许变化

        # 方向相反
        return self._is_opposite_direction(current_decision, state.last_decision)

    def _apply_inertia(self, current_decision: SignalType, state: StrategyState) -> SignalType:
        """应用决策惯性：对反向决策提高门槛

        逻辑：
        - 持仓(BUY方向)时，SELL需要更高置信度 → 降级为HOLD
        - 空仓(SELL/WATCH方向)时，BUY需要更高置信度 → 降级为WATCH
        """
        if current_decision == SignalType.SELL and state.last_decision in (SignalType.BUY, SignalType.HOLD):
            if state.current_position_ratio > 0:
                # 持仓中收到SELL信号但惯性期未过 → 降级为HOLD（观察而非行动）
                return SignalType.HOLD
        elif current_decision == SignalType.BUY and state.last_decision in (SignalType.SELL, SignalType.WATCH):
            # 空仓收到BUY信号但惯性期未过 → 降级为WATCH（等确认再入场）
            return SignalType.WATCH

        return current_decision

    # ===== 信号确认（Confirmation） =====

    def _needs_confirmation(
        self, decision: SignalType, state: StrategyState, score: float
    ) -> bool:
        """判断信号是否需要确认

        条件（满足任一）：
        - 信号置信度低于弱信号阈值
        - 信号历史上前一天不是同方向（新出现的方向信号）
        - 交易生命周期在OPEN状态（刚建仓需要确认）
        """
        if decision in (SignalType.HOLD, SignalType.WATCH):
            return False  # 不需要确认保守信号

        # 弱信号需要确认
        if score < self.CONFIRMATION_WEAK_SIGNAL_THRESHOLD:
            return True

        # 新出现的方向信号（前一天不是同方向）
        if state.recent_signals and state.recent_signals[-1] != decision.value:
            return True

        return False

    def _apply_confirmation(self, decision: SignalType, state: StrategyState) -> SignalType:
        """应用信号确认：单日信号不直接触发决策

        逻辑：
        - 如果最近2天都是同方向 → 确认通过，保持原决策
        - 否则 → 降级
          - BUY → WATCH（等确认再入场）
          - SELL → HOLD（等确认再行动）
        """
        consecutive = 0
        for sig in reversed(state.recent_signals):
            if sig == decision.value:
                consecutive += 1
            else:
                break

        if consecutive >= self.CONFIRMATION_MIN_CONSECUTIVE:
            return decision  # 确认通过

        # 未确认：降级
        if decision == SignalType.BUY:
            return SignalType.WATCH
        elif decision == SignalType.SELL:
            return SignalType.HOLD
        return decision

    # ===== 冷却机制（Cooldown） =====

    def _is_in_cooldown(self, state: StrategyState, decision: SignalType,
                         is_extreme: bool = False) -> bool:
        """判断是否在冷却期内且被阻止操作

        冷却期规则：
        - 清仓后COOLDOWN_AFTER_CLOSE_DAYS天内不允许买入
        - 减仓后COOLDOWN_AFTER_REDUCE_DAYS天内不允许加仓
        - 极端情况下冷却期缩短（更快重评）

        Args:
            state: 策略状态
            decision: 当前决策
            is_extreme: 是否为极端行情
        """
        if state.cooldown_remaining <= 0:
            return False

        # 极端情况下冷却期缩短
        cooldown_threshold = self.EXTREME_PANIC_SHORTEN_COOLDOWN if is_extreme else 0

        # 清仓冷却期：不允许买入（极端情况下冷却期<2天才放行）
        if decision == SignalType.BUY and state.cooldown_reason == "close_all":
            if is_extreme and state.cooldown_remaining <= cooldown_threshold:
                return False  # 极端情况下冷却期已够短，放行
            return True

        # 减仓保护期：不允许加仓
        if decision == SignalType.BUY and state.reduce_protection_remaining > 0:
            return True

        return False

    def _apply_cooldown(self, state: StrategyState, decision: SignalType) -> SignalType:
        """应用冷却期：阻止操作"""
        if decision == SignalType.BUY:
            return SignalType.WATCH  # 冷却期内不买入 → 观望
        return decision

    # ===== 反转成本（Reverse Cost） =====

    def _is_direction_change(self, decision: SignalType, state: StrategyState) -> bool:
        """判断是否是方向反转"""
        if state.last_decision is None:
            return decision == SignalType.BUY  # 从空仓到买入不算反转

        return self._is_opposite_direction(decision, state.last_decision)

    def _apply_reverse_cost(
        self,
        decision: SignalType,
        state: StrategyState,
        decision_result: DecisionResult,
    ) -> SignalType:
        """应用反转成本：每次反转提高门槛

        成本 = 基础成本 + 每次反转的额外成本
        如果信号置信度不足以覆盖成本 → 降级
        """
        cost = self.REVERSE_COST_BASE_PCT + state.reverse_count * self.REVERSE_COST_PER_REVERSE
        score = decision_result.score

        if score < cost:
            # 置信度不足以支付反转成本 → 降级
            if decision == SignalType.BUY:
                return SignalType.WATCH
            elif decision == SignalType.SELL:
                return SignalType.HOLD

        return decision

    # ===== 仓位管理 =====

    def _calculate_position(
        self,
        final_signal: SignalType,
        state: MarketState,
        decision_result: DecisionResult,
        strategy_state: StrategyState,
    ) -> tuple[PositionAction, float]:
        """根据最终决策、市场状态和策略状态计算仓位

        与旧版本的关键区别：
        - 仓位管理逻辑从DecisionEngine迁移到StrategyLayer
        - 增加了交易生命周期感知（FLAT/OPEN/HOLD/EXIT/COOLDOWN）
        - 增加了策略状态感知（当前仓位、反转次数等）
        """
        cap = self.POSITION_CAPS.get(state, 0.30)
        current_position_ratio = strategy_state.current_position_ratio

        # 获取决策引擎的加权分数
        weighted_scores = {}
        for trace in decision_result.trace:
            if trace.step == "市场状态影响" and "final_scores" in trace.data:
                weighted_scores = trace.data["final_scores"]
                break

        buy_score = weighted_scores.get("BUY", 0.0)
        sell_score = weighted_scores.get("SELL", 0.0)

        # 判断止损/止盈是否触发
        action_signals = [s for s in decision_result.signals if s.skill_type == "action"]
        stop_loss_confidence = self._get_sell_action_confidence(action_signals, "stop_loss")
        take_profit_confidence = self._get_sell_action_confidence(action_signals, "take_profit")
        current_return_pct = self._get_unrealized_return_pct(strategy_state, decision_result.stock)
        has_stop_loss = (
            stop_loss_confidence >= self.STOP_LOSS_REDUCE_THRESHOLD
            and self._has_stop_loss_trim_loss(current_return_pct)
        )
        has_take_profit = take_profit_confidence >= self.TAKE_PROFIT_TRIM_THRESHOLD
        valid_take_profit = has_take_profit and self._has_take_profit_buffer(current_return_pct, state)
        stop_loss_exit = (
            stop_loss_confidence >= self.STOP_LOSS_EXIT_THRESHOLD
            and self._has_stop_loss_exit_loss(current_return_pct)
        )
        strong_sell_exit = (
            sell_score >= self.STRONG_SELL_EXIT_THRESHOLD
            and sell_score >= buy_score + self.SELL_DOMINANCE_GAP
        )
        trend_exit = self._is_trend_exit_condition(
            decision_result.stock,
            strategy_state,
            current_return_pct,
            strong_sell_exit,
            state,
        )

        # ISS-033 二阶段：take_profit_keep 按市场状态分档
        take_profit_keep = self._get_state_params(state)["take_profit_keep"]

        # v0.8.5 阶段 3.3: BUY 信号质量过滤
        # 只有"高质量信号"才允许在 RISK_ON 时走高仓位上限（避免容量放大错股亏损）
        # 高质量定义：价格 > MA20 上方 + MA20 > MA60（多头排列，即 Weinstein S2）
        stock = decision_result.stock
        high_quality_signal = (
            stock is not None
            and stock.ma20 is not None and stock.ma60 is not None
            and stock.price > stock.ma20
            and stock.ma20 > stock.ma60
        )

        if final_signal == SignalType.SELL:
            if current_position_ratio <= 0:
                return PositionAction.STAY_OUT, 0.0
            if has_stop_loss:
                if stop_loss_exit:
                    return PositionAction.CLOSE_ALL, 0.0
                elif trend_exit:
                    return PositionAction.CLOSE_ALL, 0.0
                else:
                    target = current_position_ratio * self.NORMAL_REDUCE_KEEP
                    return PositionAction.REDUCE, target
            elif trend_exit:
                return PositionAction.CLOSE_ALL, 0.0
            elif valid_take_profit:
                target = current_position_ratio * take_profit_keep
                return PositionAction.REDUCE, target
            else:
                target = current_position_ratio * self.NORMAL_REDUCE_KEEP
                return PositionAction.REDUCE, target

        elif final_signal == SignalType.BUY:
            # v0.8.5 阶段 3.3: 高仓位上限只给 (RISK_ON + 高质量信号) 双条件
            if buy_score >= 0.4:
                target = cap if (state == MarketState.RISK_ON and high_quality_signal) else min(self.OPEN_RATIO + self.ADD_RATIO, cap)
                if current_position_ratio > 0:
                    return PositionAction.ADD, target
                return PositionAction.OPEN, target
            elif buy_score >= 0.25:
                target = min(self.OPEN_RATIO, cap)
                return PositionAction.OPEN, target
            else:
                target = min(self.OPEN_RATIO * 0.5, cap)
                return PositionAction.OPEN, target

        elif final_signal == SignalType.HOLD:
            if buy_score > sell_score * 1.5:
                # 同上：HOLD+强 BUY 偏向时也按双条件判定
                target = cap if (state == MarketState.RISK_ON and high_quality_signal) else min(self.OPEN_RATIO + self.ADD_RATIO, cap)
                if current_position_ratio > 0:
                    return PositionAction.ADD, target
                else:
                    # 空仓时不能"加仓"，改为建仓
                    return PositionAction.OPEN, min(self.OPEN_RATIO, cap)
            else:
                return PositionAction.HOLD_POSITION, 0.0

        else:  # WATCH
            return PositionAction.STAY_OUT, 0.0

    def _describe_position_action(
        self,
        final_signal: SignalType,
        position_action: PositionAction,
        sell_path: Optional[str],
        market_state: MarketState,
        decision_result: DecisionResult,
        strategy_state: StrategyState,
    ) -> Optional[str]:
        if final_signal != SignalType.SELL:
            return None

        current_position_ratio = strategy_state.current_position_ratio
        if current_position_ratio <= 0 or sell_path == "flat_sell" or position_action == PositionAction.STAY_OUT:
            return "空仓卖出信号: 不执行减仓"
        if sell_path == "stop_loss_exit":
            return "止损触发: 清仓保护本金"
        if sell_path == "stop_loss_trim":
            return "止损触发: 先减仓控制风险"
        if sell_path == "take_profit_trim":
            return "止盈触发: 分批落袋"
        if sell_path == "trend_exit" or position_action == PositionAction.CLOSE_ALL:
            trend_reason = self._describe_trend_exit_reason(decision_result.stock, strategy_state, market_state)
            return f"趋势退出: {trend_reason}，执行清仓({market_state.value})"
        if sell_path == "weak_sell" or position_action == PositionAction.REDUCE:
            return "弱卖出: 卖压存在但趋势未破坏，先减仓观察"
        return None

    def _infer_sell_path(
        self,
        final_signal: SignalType,
        position_action: PositionAction,
        decision_result: DecisionResult,
        strategy_state: StrategyState,
        market_state: MarketState = MarketState.TRANSITION,
    ) -> Optional[str]:
        if final_signal != SignalType.SELL:
            return None

        if strategy_state.current_position_ratio <= 0 or position_action == PositionAction.STAY_OUT:
            return "flat_sell"

        action_signals = [s for s in decision_result.signals if s.skill_type == "action"]
        stop_loss_confidence = self._get_sell_action_confidence(action_signals, "stop_loss")
        take_profit_confidence = self._get_sell_action_confidence(action_signals, "take_profit")
        current_return_pct = self._get_unrealized_return_pct(strategy_state, decision_result.stock)

        if (
            stop_loss_confidence >= self.STOP_LOSS_EXIT_THRESHOLD
            and self._has_stop_loss_exit_loss(current_return_pct)
            and position_action == PositionAction.CLOSE_ALL
        ):
            return "stop_loss_exit"
        if (
            stop_loss_confidence >= self.STOP_LOSS_REDUCE_THRESHOLD
            and self._has_stop_loss_trim_loss(current_return_pct)
            and position_action == PositionAction.REDUCE
        ):
            return "stop_loss_trim"
        if (
            take_profit_confidence >= self.TAKE_PROFIT_TRIM_THRESHOLD
            and self._has_take_profit_buffer(current_return_pct, market_state)
            and position_action == PositionAction.REDUCE
        ):
            return "take_profit_trim"
        if position_action == PositionAction.CLOSE_ALL:
            # 审查修复 M-C：CLOSE_ALL 三源（stop_loss_exit / trend_exit / stop_loss+trend_exit）。
            # 原默认全标 "trend_exit"，致 stop_loss 驱动（conf 0.70-0.85 浅亏 + trend_exit）的
            # CLOSE_ALL 被气宗压（trend_exit 在 suppressible_paths）-> stop_loss 安全网被借壳压制。
            # 有 stop_loss 信号（>=REDUCE 门槛 + 浅亏，未达 EXIT 0.85 门槛）标 stop_loss_exit（不可压）。
            if (stop_loss_confidence >= self.STOP_LOSS_REDUCE_THRESHOLD
                    and self._has_stop_loss_trim_loss(current_return_pct)):
                return "stop_loss_exit"
            return "trend_exit"
        if position_action == PositionAction.REDUCE:
            return "weak_sell"
        return None

    def _get_sell_action_confidence(self, action_signals: list, skill_name: str) -> float:
        candidates = [
            s.confidence
            for s in action_signals
            if s.skill_name == skill_name and s.signal == SignalType.SELL
        ]
        return max(candidates, default=0.0)

    def _get_unrealized_return_pct(self, strategy_state: StrategyState, stock: StockData) -> Optional[float]:
        if not strategy_state.entry_price or strategy_state.entry_price <= 0:
            return None
        return (stock.price - strategy_state.entry_price) / strategy_state.entry_price

    def _get_state_params(self, market_state: MarketState) -> dict:
        """ISS-033 二阶段：根据市场状态返回当前档位的仓位执行参数。

        跳法A（MarketState 降级）：气宗 mode 下不随市况分档摆动，走固定长持参数
        （等同牛市档：晚卖少卖、宽趋势退出阈值），持有行为完全由 mode+硬规则决定。
        命中即返回该档；未命中回退到震荡档默认值，避免 KeyError。
        """
        if self._active_mode == "qizong":
            return self.QIZONG_FIXED_PARAMS
        return self.STATE_TUNED_PARAMS.get(
            market_state,
            self.STATE_TUNED_PARAMS[MarketState.TRANSITION],
        )

    def _has_take_profit_buffer(
        self, current_return_pct: Optional[float], market_state: MarketState = MarketState.TRANSITION
    ) -> bool:
        if current_return_pct is None:
            return False
        threshold = self._get_state_params(market_state)["take_profit_min_gain_pct"]
        return current_return_pct >= threshold

    def _has_stop_loss_exit_loss(self, current_return_pct: Optional[float]) -> bool:
        return current_return_pct is not None and current_return_pct <= self.STOP_LOSS_EXIT_LOSS_PCT

    def _has_stop_loss_trim_loss(self, current_return_pct: Optional[float]) -> bool:
        return current_return_pct is not None and current_return_pct < 0

    def _describe_trend_exit_reason(
        self, stock: StockData, strategy_state: StrategyState,
        market_state: MarketState = MarketState.TRANSITION,
    ) -> str:
        current_return_pct = self._get_unrealized_return_pct(strategy_state, stock)
        break_pct = self._get_state_params(market_state)["trend_exit_break_pct"]
        facts: list[str] = []

        if stock.ma60 is not None and stock.price < stock.ma60:
            facts.append("跌破MA60")
        if (
            stock.ma20 is not None
            and stock.ma60 is not None
            and stock.ma20 <= stock.ma60
            and stock.price < stock.ma20
        ):
            facts.append("MA20失守且短中期转弱")
        elif stock.ma20 is not None and stock.price < stock.ma20:
            facts.append("跌破MA20")
        if current_return_pct is not None and current_return_pct <= break_pct:
            facts.append("浮亏扩大")
        if strategy_state.lifecycle == TradeLifecycle.EXIT:
            facts.append("生命周期已转EXIT")

        if not facts:
            return "卖压占优"
        return "、".join(dict.fromkeys(facts))

    def _is_trend_exit_condition(
        self,
        stock: StockData,
        strategy_state: StrategyState,
        current_return_pct: Optional[float],
        strong_sell_exit: bool,
        market_state: MarketState = MarketState.TRANSITION,
    ) -> bool:
        if not strong_sell_exit:
            return False

        break_pct = self._get_state_params(market_state)["trend_exit_break_pct"]
        broke_ma60 = stock.ma60 is not None and stock.price < stock.ma60
        broke_ma20 = stock.ma20 is not None and stock.price < stock.ma20
        ma20_lost_trend = (
            stock.ma20 is not None
            and stock.ma60 is not None
            and stock.ma20 <= stock.ma60
            and stock.price < stock.ma20
        )
        losing_hold = current_return_pct is not None and current_return_pct <= break_pct
        ma60_lost_trend = broke_ma60 and (ma20_lost_trend or losing_hold)

        return ma60_lost_trend or ma20_lost_trend or (broke_ma20 and losing_hold)

    # ===== 交易生命周期更新 =====

    def _update_lifecycle(
        self,
        state: StrategyState,
        decision: SignalType,
        position_action: PositionAction,
        data: StockData,
        position_ratio: float = 0.0,
        sell_path: Optional[str] = None,
    ) -> StrategyState:
        """更新交易生命周期状态

        FLAT → OPEN → HOLD → EXIT → COOLDOWN → FLAT

        转换规则：
        - FLAT + BUY → OPEN（新开仓）
        - OPEN + HOLD/BUY → HOLD（建仓确认）
        - HOLD + SELL → EXIT（开始退出）
        - HOLD + BUY → HOLD（加仓/继续持有）
        - EXIT + CLOSE_ALL → COOLDOWN（清仓后进入冷却期）
        - EXIT + REDUCE → HOLD（减仓后继续持有）
        - COOLDOWN (到期) → FLAT（冷却期结束）
        """
        new_state = deepcopy(state)
        lifecycle = state.lifecycle

        if lifecycle == TradeLifecycle.FLAT:
            if decision == SignalType.BUY and position_action == PositionAction.OPEN:
                new_state.lifecycle = TradeLifecycle.OPEN
                new_state.entry_date = data.stock_code  # 简化，实际应是日期
                new_state.entry_price = data.price
                new_state.reverse_count = 0
                new_state.total_commission_paid = 0.0
                new_state.current_position_ratio = position_ratio
                new_state.min_hold_remaining = self.MIN_HOLD_DAYS
            elif position_action == PositionAction.OPEN:
                # 防御：HOLD等信号但仓位动作为OPEN时，也应建仓
                new_state.lifecycle = TradeLifecycle.OPEN
                new_state.entry_date = data.stock_code
                new_state.entry_price = data.price
                new_state.reverse_count = 0
                new_state.total_commission_paid = 0.0
                new_state.current_position_ratio = position_ratio
                new_state.min_hold_remaining = self.MIN_HOLD_DAYS
            elif position_action == PositionAction.ADD:
                # 防御：FLAT状态下不应出现ADD，修正为OPEN
                new_state.lifecycle = TradeLifecycle.OPEN
                new_state.entry_date = data.stock_code
                new_state.entry_price = data.price
                new_state.reverse_count = 0
                new_state.total_commission_paid = 0.0
                new_state.current_position_ratio = position_ratio
                new_state.min_hold_remaining = self.MIN_HOLD_DAYS
            # FLAT + 非BUY → 保持FLAT

        elif lifecycle == TradeLifecycle.OPEN:
            if position_action in (PositionAction.CLOSE_ALL,):
                new_state.lifecycle = TradeLifecycle.COOLDOWN
                new_state.cooldown_remaining = self.COOLDOWN_AFTER_CLOSE_DAYS
                new_state.cooldown_reason = "close_all"
                new_state.current_position_ratio = 0.0
                new_state.min_hold_remaining = 0
                new_state.add_protection_remaining = 0
            elif decision in (SignalType.HOLD, SignalType.BUY) and position_action in (PositionAction.HOLD_POSITION, PositionAction.ADD):
                new_state.lifecycle = TradeLifecycle.HOLD  # 确认建仓成功
                new_state.current_position_ratio = position_ratio
            elif decision == SignalType.SELL and position_action == PositionAction.REDUCE:
                new_state.lifecycle = TradeLifecycle.HOLD  # 减仓但仍持有
                new_state.current_position_ratio = position_ratio

        elif lifecycle == TradeLifecycle.HOLD:
            if position_action == PositionAction.CLOSE_ALL:
                new_state.lifecycle = TradeLifecycle.COOLDOWN
                new_state.cooldown_remaining = self.COOLDOWN_AFTER_CLOSE_DAYS
                new_state.cooldown_reason = "close_all"
                new_state.current_position_ratio = 0.0
                new_state.entry_date = None
                new_state.entry_price = None
                new_state.last_reduce_reason = None
                new_state.reduce_protection_remaining = 0
                new_state.min_hold_remaining = 0
                new_state.add_protection_remaining = 0
            elif decision == SignalType.SELL and position_action == PositionAction.REDUCE:
                new_state.lifecycle = TradeLifecycle.HOLD
                new_state.last_reduce_date = data.stock_code  # 简化
                new_state.last_reduce_reason = sell_path
                new_state.reduce_protection_remaining = self._get_reduce_protection_days(sell_path)
                new_state.current_position_ratio = position_ratio
            elif position_action == PositionAction.ADD:
                new_state.current_position_ratio = position_ratio  # 加仓，继续HOLD
                new_state.add_protection_remaining = self.ADD_PROTECTION_DAYS

        elif lifecycle == TradeLifecycle.EXIT:
            if position_action == PositionAction.CLOSE_ALL:
                new_state.lifecycle = TradeLifecycle.COOLDOWN
                new_state.cooldown_remaining = self.COOLDOWN_AFTER_CLOSE_DAYS
                new_state.cooldown_reason = "close_all"
                new_state.current_position_ratio = 0.0
                new_state.entry_date = None
                new_state.entry_price = None
                new_state.last_reduce_reason = None
                new_state.reduce_protection_remaining = 0
                new_state.min_hold_remaining = 0
                new_state.add_protection_remaining = 0
            elif position_action == PositionAction.REDUCE:
                # 兼容旧状态：继续减仓后回到持有态，由减仓保护期控制频率
                new_state.lifecycle = TradeLifecycle.HOLD
                new_state.last_reduce_reason = sell_path
                new_state.reduce_protection_remaining = self._get_reduce_protection_days(sell_path)
                new_state.current_position_ratio = position_ratio
            elif decision in (SignalType.BUY, SignalType.HOLD) and position_action in (PositionAction.HOLD_POSITION, PositionAction.ADD):
                # 方向转回看多 → 回到HOLD
                new_state.lifecycle = TradeLifecycle.HOLD
                new_state.current_position_ratio = position_ratio
            # EXIT状态下如果已清仓 → COOLDOWN
            if new_state.current_position_ratio <= 0:
                new_state.lifecycle = TradeLifecycle.COOLDOWN
                new_state.cooldown_remaining = self.COOLDOWN_AFTER_CLOSE_DAYS
                new_state.cooldown_reason = "close_all"

        elif lifecycle == TradeLifecycle.COOLDOWN:
            if new_state.cooldown_remaining <= 0:
                new_state.lifecycle = TradeLifecycle.FLAT
                new_state.cooldown_reason = None
                new_state.reverse_count = 0
                new_state.total_commission_paid = 0.0

        return new_state

    def _get_reduce_protection_days(self, sell_path: Optional[str]) -> int:
        return self.REDUCE_PROTECTION_DAYS.get(sell_path or "", self.COOLDOWN_AFTER_REDUCE_DAYS)

    def _should_block_repeated_reduce(
        self,
        strategy_state: StrategyState,
        position_action: PositionAction,
        sell_path: Optional[str],
    ) -> bool:
        return (
            position_action == PositionAction.REDUCE
            and bool(sell_path)
            and strategy_state.reduce_protection_remaining > 0
            and strategy_state.last_reduce_reason == sell_path
        )

    def _should_block_early_tactical_sell(
        self,
        strategy_state: StrategyState,
        position_action: PositionAction,
        sell_path: Optional[str],
    ) -> bool:
        return (
            strategy_state.min_hold_remaining > 0
            and position_action == PositionAction.REDUCE
            and sell_path in self.MIN_HOLD_BLOCK_SELL_PATHS
        )

    def _should_block_post_add_tactical_sell(
        self,
        strategy_state: StrategyState,
        position_action: PositionAction,
        sell_path: Optional[str],
    ) -> bool:
        return (
            strategy_state.add_protection_remaining > 0
            and position_action == PositionAction.REDUCE
            and sell_path in self.MIN_HOLD_BLOCK_SELL_PATHS
        )

    # ===== 辅助方法 =====

    def _detect_extreme_condition(
        self, data: StockData, market_state: MarketState,
        decision_result: DecisionResult
    ) -> bool:
        """自动检测极端行情（v0.7.2）

        检测标准（满足任一即视为极端）：
        1. 市场状态为PANIC
        2. 个股单日跌幅超过5%
        3. 个股单日涨幅超过7%
        4. 决策评分极高（≥0.80）且方向为SELL → 强烈止损信号
        5. 止损信号置信度≥0.90 → 极端止损

        极端行情下系统自动调整：
        - 惯性期缩短（止损要快，不能等3天）
        - 止损信号跳过确认（不用等2天确认）
        - 冷却期缩短（快速离场后可更快重评）
        """
        # 1. PANIC状态
        if market_state == MarketState.PANIC:
            logger.info("极端行情检测: PANIC市场状态")
            return True

        # 2. 个股单日大跌
        if data.change_pct is not None and data.change_pct <= self.EXTREME_DROP_THRESHOLD * 100:
            logger.info(f"极端行情检测: 单日跌幅 {data.change_pct:.1f}%")
            return True

        # 3. 个股单日大涨
        if data.change_pct is not None and data.change_pct >= self.EXTREME_RISE_THRESHOLD * 100:
            logger.info(f"极端行情检测: 单日涨幅 {data.change_pct:.1f}%")
            return True

        # 4. 强烈止损信号
        if decision_result.decision == SignalType.SELL and decision_result.score >= 0.80:
            # 检查是否有高置信度止损
            action_signals = [s for s in decision_result.signals if s.skill_type == "action"]
            has_strong_stop = self._get_sell_action_confidence(action_signals, "stop_loss") >= self.STOP_LOSS_EXIT_THRESHOLD
            if has_strong_stop:
                logger.info(f"极端行情检测: 强烈止损信号(conf≥{self.STOP_LOSS_EXIT_THRESHOLD:.2f})")
                return True

        return False

    @staticmethod
    def _is_opposite_direction(a: SignalType, b: SignalType) -> bool:
        """判断两个信号是否方向相反"""
        bullish = {SignalType.BUY, SignalType.HOLD}
        bearish = {SignalType.SELL, SignalType.WATCH}
        return (a in bullish and b in bearish) or (a in bearish and b in bullish)

    def _generate_strategy_reasons(
        self,
        raw_decision: SignalType,
        final_decision: SignalType,
        state: StrategyState,
        inertia_applied: bool,
        confirmation_required: bool,
        cooldown_blocked: bool,
        reverse_cost_paid: bool,
        stability_adjusted: bool,
        is_extreme: bool = False,
    ) -> list[str]:
        """生成策略层决策理由"""
        reasons = []

        if is_extreme:
            reasons.append("⚠ 极端行情模式（自动缩短惯性/跳过止损确认/缩短冷却期）")

        if raw_decision != final_decision:
            reasons.append(f"策略层修正: {raw_decision.value} → {final_decision.value}")

        if stability_adjusted:
            reasons.append(f"信号不稳定(稳定性={state.signal_stability_score:.0%})，降级处理")

        if inertia_applied:
            reasons.append(f"决策惯性(持续{state.inertia_counter}天)，抑制方向变化")

        if confirmation_required:
            reasons.append("信号需确认(单日信号未直接触发)")

        if cooldown_blocked:
            reasons.append(f"冷却期内(剩余{state.cooldown_remaining}天)，阻止操作")

        if reverse_cost_paid:
            reasons.append(f"反转成本(累计{state.reverse_count}次反转)")

        if not reasons:
            reasons.append("策略层: 信号通过所有约束，维持原决策")

        return reasons
