"""笨总「超景气价值投机」量化体系（v0.8.6 起）

来源：用户提供的笨总 B 站 up 主教学知识库
- 投资策略（持续更新）/笨总教学 bilibili-笨笨的韭菜/

模块：
- scorer：6 维打分体系（基于 xlsx 课件）
- auto_scorer：6 维 AI 自动评分调度器（v0.8.6.2）
- data_provider：财务/公告等数据接入（v0.8.6.2）
- cache：评分结果文件缓存（v0.8.6.2）
- dimensions/：6 个维度独立评分器（v0.8.6.2）
"""

from src.core.benzhong.scorer import (
    BenzhongScore,
    score_one,
    liquidity_coefficient,
)
from src.core.benzhong.auto_scorer import auto_score, AutoScoredResult

__all__ = [
    "BenzhongScore",
    "score_one",
    "liquidity_coefficient",
    "auto_score",
    "AutoScoredResult",
]
