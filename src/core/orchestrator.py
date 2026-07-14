"""编排层 - 协调数据、技能、决策、事件、AI调节、策略、执行流程

v0.8.1 架构：
  Data Layer
     ↓
  Signal Layer（Skill Engine）
     ↓
  Decision Layer（信号聚合器）
     ↓
  Event Layer（事件检测+预警，v0.8.1 RAG增强）
     ↓
  AI Modifier Layer（新闻分析+情绪调节，v0.8.1 RAG增强）
     ↓
  Strategy Layer（约束行为、延迟决策、抑制噪声）
     ↓
  Execution Layer（现实约束建模）

Orchestrator 协调完整的七层流程。
v0.8.1 新增 rag_service 参数，传递给 EventLayer 和 AIModifier。
"""

import logging
from typing import Optional

from src.data.models import (
    StockData, DecisionResult, StrategyState, StrategyDecision,
    ExecutionConstraint, TradeLifecycle, AIModifierResult, MarketEvent,
    SignalType, PositionAction
)
from src.core.skill_engine import SkillEngine
from src.core.decision_engine import DecisionEngine, StateMachine
from src.core.strategy_layer import StrategyLayer
from src.core.execution_layer import ExecutionLayer, ExecutionEvaluation

logger = logging.getLogger(__name__)


class Orchestrator:
    """编排器 - 协调七层分析流程（v0.8.0 含事件层+AI调节层）"""

    def __init__(
        self,
        skills_dir: str = "./src/skills",
        enabled_skills: Optional[list[str]] = None,
        signal_weights: Optional[dict[str, float]] = None,
        skill_types: Optional[dict[str, str]] = None,
        execution_constraint: Optional[ExecutionConstraint] = None,
        ai_config: Optional[dict] = None,
        event_config: Optional[dict] = None,
        rag_service=None,  # v0.8.1: RAG服务实例
        entry_exit_config: Optional[dict] = None,  # v0.8.3 Phase C
        pyramid_config: Optional[dict] = None,     # v0.8.3 Phase D
    ):
        # v0.8.1: RAG服务（可选，用于策略知识增强）
        self.rag_service = rag_service

        # 初始化技能引擎（Signal Layer）
        self.skill_engine = SkillEngine(skills_dir, skill_types)
        self.skill_engine.load_skills(enabled_skills)

        # 初始化信号聚合器（Decision Layer）
        self.decision_engine = DecisionEngine(signal_weights)

        # 初始化AI调节层（AI Modifier Layer, v0.8.0新增, v0.8.1增加RAG）
        self.ai_modifier = None
        if ai_config and ai_config.get("enabled", False):
            try:
                from src.core.ai_modifier import AIModifier
                self.ai_modifier = AIModifier(ai_config, rag_service=rag_service)
                if not self.ai_modifier.is_available():
                    logger.warning("AI Modifier初始化失败（API Key未配置？），将跳过AI调节")
                    self.ai_modifier = None
            except Exception as e:
                logger.warning(f"AI Modifier初始化异常: {e}，将跳过AI调节")
                self.ai_modifier = None

        # 初始化事件驱动层（Event Layer, v0.8.0 Phase 3新增, v0.8.1增加RAG）
        self.event_layer = None
        if event_config and event_config.get("enabled", False):
            try:
                from src.core.event_layer import EventLayer
                self.event_layer = EventLayer(event_config, ai_config=ai_config, rag_service=rag_service)
                if not self.event_layer.is_available():
                    logger.warning("EventLayer初始化失败，将跳过事件检测")
                    self.event_layer = None
            except Exception as e:
                logger.warning(f"EventLayer初始化异常: {e}，将跳过事件检测")
                self.event_layer = None

        # 初始化策略层（Strategy Layer）
        self.strategy_layer = StrategyLayer(pyramid_config=pyramid_config if pyramid_config is not None else None)

        # 初始化执行层（Execution Layer）
        self.execution_layer = ExecutionLayer(execution_constraint)

        # v0.8.3 Phase C: 买卖点计算器
        self.entry_exit_calc = None
        if entry_exit_config and entry_exit_config.get("enabled", False):
            try:
                from src.core.entry_exit import EntryExitCalculator
                self.entry_exit_calc = EntryExitCalculator(entry_exit_config)
                logger.info("EntryExitCalculator initialized")
            except Exception as e:
                logger.warning(f"EntryExitCalculator init error: {e}")

        ai_status = "enabled" if self.ai_modifier else "disabled"
        event_status = "enabled" if self.event_layer else "disabled"
        rag_status = "enabled" if self.rag_service else "disabled"
        logger.info(f"Orchestrator initialized (v0.8.1: Signal→Decision→Event({event_status})→AI Modifier({ai_status})→Strategy→Execution, RAG={rag_status})")

    def analyze(
        self,
        data: StockData,
        force_state: Optional[str] = None,
        current_position_ratio: float = 0.0,
        strategy_state: Optional[StrategyState] = None,
        ai_enabled: bool = True,
        has_position: bool = False,
        entry_price: Optional[float] = None,
        position_tier: str = "pilot",
        high_since_entry: Optional[float] = None,
        trade_plan=None,  # v0.8.5 阶段 1.3: TradePlan 实例（来自 PortfolioManager）
        today: Optional[str] = None,  # v0.8.5: 用于 PlanGuard 的时间止损判定
    ) -> tuple[DecisionResult, StrategyDecision, ExecutionEvaluation, Optional[AIModifierResult]]:
        """执行完整分析流程（v0.8.0 六层架构）

        Args:
            data: 股票数据
            force_state: 强制市场状态
            current_position_ratio: 当前仓位比例（0-1）
            strategy_state: 上一交易日的策略层状态（None=从FLAT开始）
            ai_enabled: 是否启用AI调节层（默认True）

        Returns:
            (信号聚合结果, 策略层决策, 执行层评估, AI调节结果)
        """
        logger.info(f"Analyzing {data.stock_code} {data.stock_name}")

        # Layer 1: 确定市场状态
        if force_state:
            from src.data.models import MarketState
            state = MarketState[force_state.upper()]
        else:
            state = StateMachine.determine_state(data)

        logger.info(f"Market state: {state}")

        # Layer 2: 执行所有技能（Signal Layer）
        signals = self.skill_engine.execute_all(data)
        logger.info(f"Generated {len(signals)} signals")

        for sig in signals:
            logger.info(f"  - {sig.skill_alias}: {sig.signal} (conf: {sig.confidence})")

        # Layer 3: 信号聚合（Decision Layer）
        decision_result = self.decision_engine.make_decision(
            data, signals, state, current_position_ratio=current_position_ratio
        )
        logger.info(f"Decision aggregate: {decision_result.decision} (score: {decision_result.score})")

        # Layer 3.25: 事件驱动层（Event Layer, v0.8.0 Phase 3新增）
        event_ai_result = None
        if self.event_layer and self.event_layer.enabled and self.event_layer.auto_scan:
            active_events = self.event_layer.check_events()
            if active_events:
                # 取impact_level最高的事件
                top_event = max(active_events, key=lambda e: e.impact_level)
                if top_event.impact_level >= 3:
                    event_ai_result = self.event_layer.to_ai_modifier_result(top_event)
                    logger.info(
                        f"Event Layer detected: {top_event.event_type} "
                        f"impact={top_event.impact_level} sentiment={top_event.sentiment}"
                    )
                    # 记录事件到决策理由
                    event_type_cn = {
                        "policy": "政策", "war": "地缘冲突", "earnings": "财报",
                        "macro": "宏观", "black_swan": "黑天鹅", "market_crash": "暴跌"
                    }
                    sentiment_cn = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}
                    decision_result.reason.append(
                        f"⚡事件预警: [{event_type_cn.get(top_event.event_type, top_event.event_type)}] "
                        f"{sentiment_cn.get(top_event.sentiment, top_event.sentiment)} "
                        f"(等级{top_event.impact_level}), {top_event.summary}"
                    )

        # Layer 3.5: AI调节层（v0.8.0新增）
        ai_result = None
        if ai_enabled and self.ai_modifier and self.ai_modifier.is_available():
            ai_result = self.ai_modifier.analyze(data)
            if ai_result.adjusted:
                # 应用信号调节
                original_score = decision_result.score
                decision_result.score = max(0.0, min(1.0, decision_result.score + ai_result.score_adjustment))
                logger.info(
                    f"AI Modifier adjusted score: {original_score:.3f} → {decision_result.score:.3f} "
                    f"(adjustment={ai_result.score_adjustment:.3f})"
                )

                # 应用仓位调节（传递给Strategy Layer通过decision_result）
                if ai_result.position_cap < 1.0:
                    decision_result.position_ratio = min(
                        decision_result.position_ratio, ai_result.position_cap
                    )
                    logger.info(f"AI Modifier capped position: max {ai_result.position_cap:.0%}")

                # 应用状态干预
                if ai_result.force_state:
                    force_state = ai_result.force_state
                    from src.data.models import MarketState
                    state = MarketState[force_state.upper()]
                    decision_result.state = state
                    logger.warning(f"AI Modifier forced state: {state.value}")

                # 记录AI调节到决策理由
                sentiment_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
                decision_result.reason.append(
                    f"AI情绪: {sentiment_cn.get(ai_result.sentiment, ai_result.sentiment)}"
                    f"({ai_result.confidence:.0%}), {ai_result.summary}"
                )

        # 合并事件层调节到AI调节结果
        if event_ai_result:
            # 如果AI调节也存在，叠加事件效果
            if ai_result and ai_result.adjusted:
                ai_result.score_adjustment += event_ai_result.score_adjustment
                ai_result.position_cap = min(ai_result.position_cap, event_ai_result.position_cap)
                if event_ai_result.force_state:
                    ai_result.force_state = event_ai_result.force_state
            else:
                # 事件层独立调节
                ai_result = event_ai_result

                # 应用信号调节
                original_score = decision_result.score
                decision_result.score = max(0.0, min(1.0, decision_result.score + ai_result.score_adjustment))
                logger.info(
                    f"Event Layer adjusted score: {original_score:.3f} → {decision_result.score:.3f} "
                    f"(adjustment={ai_result.score_adjustment:.3f})"
                )

                # 应用仓位调节
                if ai_result.position_cap < 1.0:
                    decision_result.position_ratio = min(
                        decision_result.position_ratio, ai_result.position_cap
                    )
                    logger.info(f"Event Layer capped position: max {ai_result.position_cap:.0%}")

                # 应用状态干预
                if ai_result.force_state:
                    force_state = ai_result.force_state
                    from src.data.models import MarketState
                    state = MarketState[force_state.upper()]
                    decision_result.state = state
                    logger.warning(f"Event Layer forced state: {state.value}")

        # Layer 3.75: 买卖点精确触发（Entry/Exit Calculator, v0.8.3 Phase C）
        entry_exit_result = None
        if self.entry_exit_calc and self.entry_exit_calc.enabled:
            entry_exit_result = self.entry_exit_calc.calculate(
                data, has_position, entry_price, position_tier, high_since_entry
            )
            if entry_exit_result.override_decision:
                decision_result.overridden_by = "entry_exit"
                decision_result.overridden_reason = (
                    entry_exit_result.entry_reason or entry_exit_result.exit_reason
                )
                # v0.8.3 Phase C: 买点仅精化BUY，卖点可覆盖HOLD（Chandelier/趋势破坏是安全网）
                current_decision = decision_result.decision
                action = entry_exit_result.override_action
                exit_type = entry_exit_result.exit_type
                if action in ("ENTRY", "ADD") and current_decision == SignalType.BUY:
                    decision_result.position_action = PositionAction.OPEN if action == "ENTRY" else PositionAction.ADD
                    decision_result.reason.append(f"[EntryExit] {decision_result.overridden_reason}")
                    logger.info(f"[EntryExit] Refine entry: {decision_result.overridden_reason}")
                elif action in ("EXIT", "STOP", "TRIM"):
                    # 卖点覆盖条件：决策已SELL，或Chandelier/趋势破坏安全网触发（覆盖HOLD）
                    force_exit = exit_type in ("chandelier_stop", "trend_break")
                    if current_decision == SignalType.SELL or (has_position and force_exit):
                        decision_result.decision = SignalType.SELL
                        decision_result.position_action = PositionAction.CLOSE_ALL if action in ("EXIT", "STOP") else PositionAction.REDUCE
                        decision_result.reason.append(f"[EntryExit] {decision_result.overridden_reason}")
                        logger.info(f"[EntryExit] Force exit: {decision_result.overridden_reason}")
                    else:
                        logger.debug(f"[EntryExit] Exit blocked: decision={current_decision.value}")
                elif action in ("ENTRY", "ADD"):
                    logger.debug(f"[EntryExit] Entry blocked: decision={current_decision.value} != BUY")
                elif action in ("EXIT", "STOP", "TRIM"):
                    logger.debug(f"[EntryExit] Exit blocked: decision={current_decision.value} != SELL")

        # Layer 3.84: 基本面恶化硬退出（ISS-053）--仅持仓检查，独立 fundamental_alert 通道
        # 被 ST / 业绩预告预亏预减 -> 强制 SELL+CLOSE_ALL。fail-open（异常跳过不假退出）但记 WARNING。
        falert = None
        if has_position:
            try:
                from src.core.exit_signals.fundamental import check_fundamental_alert
                falert = check_fundamental_alert(
                    data.stock_code,
                    entry_date=trade_plan.opened_at if trade_plan else None,
                )
            except Exception as e:
                logger.warning(f"[FundamentalAlert] 检查异常(安全网当日可能有洞): {e}")
            if falert:
                decision_result.decision = SignalType.SELL
                decision_result.position_action = PositionAction.CLOSE_ALL
                decision_result.reason.append(f"[FundamentalAlert] 重大利空: {falert}")
                logger.info(f"[FundamentalAlert] 强制离场: {falert}")

        # Layer 3.85: 高位止盈3维度大顶信号检查（跳法A 阶段2 / v0.8.6.4）
        # 仅持仓时检查；触发即作为 P1 信号强制 SELL（PlanGuard 不可压制，仅次于致命止损）。
        # 全客观硬规则（成交额/换手/缩量/减持），不依赖 AI 实时判断。
        top_signal = None
        if has_position:
            try:
                from src.core.exit_signals import check_top_signals
                top_signal = check_top_signals(
                    data, data.stock_code,
                    turnover_pct=getattr(data, "turnover_pct", None),
                    announcements=getattr(data, "recent_announcements", None),
                    market_turnover_trillion=getattr(data, "market_turnover_trillion", None),
                )
            except Exception as e:
                logger.debug(f"高位止盈信号检查失败: {e}")
            if top_signal:
                decision_result.decision = SignalType.SELL
                decision_result.position_action = PositionAction.CLOSE_ALL
                decision_result.reason.append(f"[TopSignal] 高位止盈: {top_signal}")
                logger.info(f"[TopSignal] 大顶信号强制离场: {top_signal}")

        # Layer 4: 策略层过滤（Strategy Layer）
        if strategy_state is None:
            strategy_state = StrategyState(
                current_position_ratio=current_position_ratio,
                lifecycle=TradeLifecycle.FLAT if current_position_ratio <= 0 else TradeLifecycle.HOLD,
            )

        # 跳法A（MarketState 降级）：把笨总 mode 透传给策略层，气宗走固定长持参数
        self.strategy_layer._active_mode = trade_plan.mode if trade_plan is not None else None
        strategy_decision = self.strategy_layer.process(
            decision_result, strategy_state, data
        )

        # v0.8.3 Phase C: 将买卖点结果附加到策略决策
        if entry_exit_result:
            strategy_decision.entry_exit = entry_exit_result.model_dump()

        # 跳法A 阶段2: 把大顶信号标到策略决策上，让 PlanGuard 按 P1 不可压处理
        if top_signal:
            strategy_decision.top_signal = top_signal
            if strategy_decision.decision == SignalType.SELL and not strategy_decision.sell_path:
                strategy_decision.sell_path = "top_signal"

        # ISS-053: fundamental_alert 标到策略决策，PlanGuard 规则4.5 按不可压处理
        if falert:
            strategy_decision.fundamental_alert = falert
            if strategy_decision.decision == SignalType.SELL and not strategy_decision.sell_path:
                strategy_decision.sell_path = "fundamental_alert"

        # v0.8.6.3 (ISS-033): Chandelier/trend_break force_exit 经 strategy_layer 后 sell_path
        # 可能落空（_calculate_position 重算 position_action 致 _infer_sell_path 推断不到）。
        # force_exit 本质是趋势退出，明确标 trend_exit，让 PlanGuard 气宗能匹配压制。
        if (entry_exit_result
                and strategy_decision.decision == SignalType.SELL
                and entry_exit_result.override_action in ("EXIT", "STOP", "TRIM")
                and entry_exit_result.exit_type in ("chandelier_stop", "trend_break")
                and not strategy_decision.sell_path):
            strategy_decision.sell_path = "trend_exit"

        # v0.8.3 收尾 ISS-030: 买卖点与AI情绪分歧检测
        if ai_result and entry_exit_result:
            ai_sentiment = ai_result.sentiment
            ai_bearish = ai_sentiment in ("看跌", "bearish")
            ai_bullish = ai_sentiment in ("看涨", "bullish")
            tech_buy = entry_exit_result.entry_triggered
            tech_sell = entry_exit_result.exit_triggered

            if tech_buy and ai_bearish:
                strategy_decision.divergence = {
                    "type": "entry_vs_bearish_ai",
                    "technical_signal": "买点触发",
                    "ai_sentiment": ai_sentiment,
                    "warning": f"技术面买点已触发，但AI情绪{ai_sentiment}。买点规则优先，AI仅作风险提示。"
                }
            elif tech_sell and ai_bullish:
                strategy_decision.divergence = {
                    "type": "exit_vs_bullish_ai",
                    "technical_signal": "卖点触发",
                    "ai_sentiment": ai_sentiment,
                    "warning": f"技术面卖点已触发，但AI情绪{ai_sentiment}。卖点规则优先，AI仅作风险提示。"
                }

        # AI仓位上限影响Strategy Layer的仓位建议
        if ai_result and ai_result.position_cap < 1.0:
            original_ratio = strategy_decision.position_ratio
            strategy_decision.position_ratio = min(
                strategy_decision.position_ratio, ai_result.position_cap
            )
            if strategy_decision.position_ratio != original_ratio:
                logger.info(
                    f"AI Modifier adjusted strategy position: {original_ratio:.0%} → {strategy_decision.position_ratio:.0%}"
                )

        logger.info(f"Strategy decision: {strategy_decision.decision} "
                     f"(lifecycle: {strategy_decision.lifecycle_before.value}→{strategy_decision.lifecycle_after.value})")

        # v0.8.5 阶段 1.3: PlanGuard — 根据 TradePlan 调整 strategy_decision
        # 只压制 weak_sell（计划未失效时不卖）；致命止损/趋势退出/止盈分批不动
        if trade_plan is not None:
            from src.core.plan_guard import PlanGuard
            guard = PlanGuard()
            strategy_decision = guard.evaluate(strategy_decision, trade_plan, data, today)
            logger.debug(f"PlanGuard evaluated; final decision={strategy_decision.decision}")

        # Layer 5: 执行层评估（Execution Layer）
        volume_ratio = None
        if data.avg_volume_20 and data.avg_volume_20 > 0:
            volume_ratio = data.volume / data.avg_volume_20

        execution_eval = self.execution_layer.evaluate(
            data, strategy_decision.position_action, volume_ratio
        )
        if execution_eval.blocked:
            logger.info(f"Execution blocked: {execution_eval.block_reason}")

        return decision_result, strategy_decision, execution_eval, ai_result

    def analyze_legacy(
        self,
        data: StockData,
        force_state: Optional[str] = None,
        current_position_ratio: float = 0.0,
        ai_enabled: bool = False,
    ) -> DecisionResult:
        """旧版分析接口（兼容回测引擎过渡期）

        与旧版Orchestrator.analyze()签名一致，返回DecisionResult。
        v0.8.0 新代码应使用 analyze() 方法。
        回测默认 ai_enabled=False（回测不应使用实时新闻）。
        """
        decision_result, _, _, _ = self.analyze(
            data, force_state, current_position_ratio,
            ai_enabled=ai_enabled,
        )
        return decision_result

    def get_available_skills(self) -> list[str]:
        """获取已加载的技能列表"""
        return list(self.skill_engine.skills.keys())
