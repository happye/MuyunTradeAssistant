"""编排层 - 协调数据、技能、决策流程"""

import logging
from typing import Optional

from src.data.models import StockData, DecisionResult
from src.core.skill_engine import SkillEngine
from src.core.decision_engine import DecisionEngine, StateMachine

logger = logging.getLogger(__name__)


class Orchestrator:
    """编排器 - 协调整个分析流程"""

    def __init__(
        self,
        skills_dir: str = "./src/skills",
        enabled_skills: Optional[list[str]] = None,
        signal_weights: Optional[dict[str, float]] = None,
        skill_types: Optional[dict[str, str]] = None
    ):
        # 初始化技能引擎
        self.skill_engine = SkillEngine(skills_dir, skill_types)
        self.skill_engine.load_skills(enabled_skills)

        # 初始化决策引擎
        self.decision_engine = DecisionEngine(signal_weights)

        logger.info("Orchestrator initialized")

    def analyze(self, data: StockData, force_state: Optional[str] = None, current_position_ratio: float = 0.0) -> DecisionResult:
        """执行完整分析流程

        Args:
            data: 股票数据
            force_state: 强制市场状态
            current_position_ratio: 当前仓位比例（0-1），用于仓位管理计算
        """

        logger.info(f"Analyzing {data.stock_code} {data.stock_name}")

        # Step 1: 确定市场状态
        if force_state:
            from src.data.models import MarketState
            state = MarketState[force_state.upper()]
        else:
            state = StateMachine.determine_state(data)

        logger.info(f"Market state: {state}")

        # Step 2: 执行所有技能
        signals = self.skill_engine.execute_all(data)
        logger.info(f"Generated {len(signals)} signals")

        for sig in signals:
            logger.info(f"  - {sig.skill_alias}: {sig.signal} (conf: {sig.confidence})")

        # Step 3: 生成决策
        result = self.decision_engine.make_decision(data, signals, state, current_position_ratio=current_position_ratio)

        logger.info(f"Final decision: {result.decision} (score: {result.score})")

        return result

    def get_available_skills(self) -> list[str]:
        """获取已加载的技能列表"""
        return list(self.skill_engine.skills.keys())
