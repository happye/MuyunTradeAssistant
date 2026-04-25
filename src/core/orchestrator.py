"""编排层 - 协调数据、技能、决策、策略、执行流程

v0.7.2 架构：
  Data Layer
     ↓
  Signal Layer（Skill Engine）
     ↓
  Decision Layer（信号聚合器）
     ↓
  Strategy Layer（新增核心：约束行为、延迟决策、抑制噪声）
     ↓
  Execution Layer（现实约束建模）

Orchestrator 协调完整的五层流程。
"""

import logging
from typing import Optional

from src.data.models import (
    StockData, DecisionResult, StrategyState, StrategyDecision,
    ExecutionConstraint, TradeLifecycle
)
from src.core.skill_engine import SkillEngine
from src.core.decision_engine import DecisionEngine, StateMachine
from src.core.strategy_layer import StrategyLayer
from src.core.execution_layer import ExecutionLayer, ExecutionEvaluation

logger = logging.getLogger(__name__)


class Orchestrator:
    """编排器 - 协调五层分析流程"""

    def __init__(
        self,
        skills_dir: str = "./src/skills",
        enabled_skills: Optional[list[str]] = None,
        signal_weights: Optional[dict[str, float]] = None,
        skill_types: Optional[dict[str, str]] = None,
        execution_constraint: Optional[ExecutionConstraint] = None,
    ):
        # 初始化技能引擎（Signal Layer）
        self.skill_engine = SkillEngine(skills_dir, skill_types)
        self.skill_engine.load_skills(enabled_skills)

        # 初始化信号聚合器（Decision Layer）
        self.decision_engine = DecisionEngine(signal_weights)

        # 初始化策略层（Strategy Layer）
        self.strategy_layer = StrategyLayer()

        # 初始化执行层（Execution Layer）
        self.execution_layer = ExecutionLayer(execution_constraint)

        logger.info("Orchestrator initialized (v0.7.2: Signal→Decision→Strategy→Execution)")

    def analyze(
        self,
        data: StockData,
        force_state: Optional[str] = None,
        current_position_ratio: float = 0.0,
        strategy_state: Optional[StrategyState] = None,
    ) -> tuple[DecisionResult, StrategyDecision, ExecutionEvaluation]:
        """执行完整分析流程（v0.7.2 五层架构）

        Args:
            data: 股票数据
            force_state: 强制市场状态
            current_position_ratio: 当前仓位比例（0-1）
            strategy_state: 上一交易日的策略层状态（None=从FLAT开始）

        Returns:
            (信号聚合结果, 策略层决策, 执行层评估)
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

        # Layer 4: 策略层过滤（Strategy Layer）
        if strategy_state is None:
            strategy_state = StrategyState(
                current_position_ratio=current_position_ratio,
                lifecycle=TradeLifecycle.FLAT if current_position_ratio <= 0 else TradeLifecycle.HOLD,
            )

        strategy_decision = self.strategy_layer.process(
            decision_result, strategy_state, data
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

        return decision_result, strategy_decision, execution_eval

    def analyze_legacy(
        self,
        data: StockData,
        force_state: Optional[str] = None,
        current_position_ratio: float = 0.0,
    ) -> DecisionResult:
        """旧版分析接口（兼容回测引擎过渡期）

        与旧版Orchestrator.analyze()签名一致，返回DecisionResult。
        v0.7.2 新代码应使用 analyze() 方法。
        """
        decision_result, _, _ = self.analyze(
            data, force_state, current_position_ratio
        )
        return decision_result

    def get_available_skills(self) -> list[str]:
        """获取已加载的技能列表"""
        return list(self.skill_engine.skills.keys())
