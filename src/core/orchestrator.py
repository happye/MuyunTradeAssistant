"""编排层 - 协调数据、技能、决策、事件、AI调节、策略、执行流程

v0.8.0 架构：
  Data Layer
     ↓
  Signal Layer（Skill Engine）
     ↓
  Decision Layer（信号聚合器）
     ↓
  Event Layer（v0.8.0 Phase 3：事件检测+预警）
     ↓
  AI Modifier Layer（新闻分析+情绪调节）
     ↓
  Strategy Layer（约束行为、延迟决策、抑制噪声）
     ↓
  Execution Layer（现实约束建模）

Orchestrator 协调完整的七层流程。
"""

import logging
from typing import Optional

from src.data.models import (
    StockData, DecisionResult, StrategyState, StrategyDecision,
    ExecutionConstraint, TradeLifecycle, AIModifierResult, MarketEvent
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
    ):
        # 初始化技能引擎（Signal Layer）
        self.skill_engine = SkillEngine(skills_dir, skill_types)
        self.skill_engine.load_skills(enabled_skills)

        # 初始化信号聚合器（Decision Layer）
        self.decision_engine = DecisionEngine(signal_weights)

        # 初始化AI调节层（AI Modifier Layer, v0.8.0新增）
        self.ai_modifier = None
        if ai_config and ai_config.get("enabled", False):
            try:
                from src.core.ai_modifier import AIModifier
                self.ai_modifier = AIModifier(ai_config)
                if not self.ai_modifier.is_available():
                    logger.warning("AI Modifier初始化失败（API Key未配置？），将跳过AI调节")
                    self.ai_modifier = None
            except Exception as e:
                logger.warning(f"AI Modifier初始化异常: {e}，将跳过AI调节")
                self.ai_modifier = None

        # 初始化事件驱动层（Event Layer, v0.8.0 Phase 3新增）
        self.event_layer = None
        if event_config and event_config.get("enabled", False):
            try:
                from src.core.event_layer import EventLayer
                self.event_layer = EventLayer(event_config, ai_config=ai_config)
                if not self.event_layer.is_available():
                    logger.warning("EventLayer初始化失败，将跳过事件检测")
                    self.event_layer = None
            except Exception as e:
                logger.warning(f"EventLayer初始化异常: {e}，将跳过事件检测")
                self.event_layer = None

        # 初始化策略层（Strategy Layer）
        self.strategy_layer = StrategyLayer()

        # 初始化执行层（Execution Layer）
        self.execution_layer = ExecutionLayer(execution_constraint)

        ai_status = "enabled" if self.ai_modifier else "disabled"
        event_status = "enabled" if self.event_layer else "disabled"
        logger.info(f"Orchestrator initialized (v0.8.0: Signal→Decision→Event({event_status})→AI Modifier({ai_status})→Strategy→Execution)")

    def analyze(
        self,
        data: StockData,
        force_state: Optional[str] = None,
        current_position_ratio: float = 0.0,
        strategy_state: Optional[StrategyState] = None,
        ai_enabled: bool = True,
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

        # Layer 4: 策略层过滤（Strategy Layer）
        if strategy_state is None:
            strategy_state = StrategyState(
                current_position_ratio=current_position_ratio,
                lifecycle=TradeLifecycle.FLAT if current_position_ratio <= 0 else TradeLifecycle.HOLD,
            )

        strategy_decision = self.strategy_layer.process(
            decision_result, strategy_state, data
        )

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
