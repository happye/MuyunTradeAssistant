"""笨总「超景气价值投机」量化体系（v0.8.6 起）

来源：用户提供的笨总 B 站 up 主教学知识库
- 投资策略（持续更新）/笨总教学 bilibili-笨笨的韭菜/

模块：
- scorer：6 维打分体系（基于 xlsx 课件）
- （后续阶段补充：event_detector / mode_classifier / exit_signals）
"""

from src.core.benzhong.scorer import (
    BenzhongScore,
    score_one,
    liquidity_coefficient,
)

__all__ = ["BenzhongScore", "score_one", "liquidity_coefficient"]
