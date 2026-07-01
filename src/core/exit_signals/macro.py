"""宏观层高位止盈信号（跳法A 阶段2 / v0.8.6.4）

笨总教学：宏观见顶三信号 = 成交额破10万亿 + 储蓄搬家>30% + 官方发金牌。
当前仅成交额可获取数据源，其余两项无现成源（诚实声明，见 TODO）。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 全市场单日成交额阈值（万亿）—— 笨总教学的"成交额破10万亿"系统性见顶信号
MARKET_TURNOVER_TOP_TRILLION = 10.0


def check_macro_top_signal(market_turnover_trillion: Optional[float] = None) -> Optional[str]:
    """检查宏观层大顶信号。

    Args:
        market_turnover_trillion: 全市场成交额(万亿)。None 时自动调 data_provider 拉取
            （live 路径可用；回测无历史全市场成交额，应由调用方传 None 并接受跳过）

    Returns:
        Optional[str]: 触发信号描述，无则 None

    数据源现状（诚实声明）：
    - ✅ 成交额：data_provider.get_market_turnover()（新浪全市场快照）
    - ❌ 储蓄搬家>30%：需居民存款月度数据（央行），无实时源 → TODO 未实现
    - ❌ 官方发金牌：需政策新闻语义识别，靠 AI 不作硬规则 → TODO 未实现
    """
    turnover = market_turnover_trillion
    if turnover is None:
        try:
            from src.core.benzong import data_provider
            turnover = data_provider.get_market_turnover()
        except Exception as e:
            logger.debug(f"宏观成交额获取失败: {e}")
            return None

    if turnover is not None and turnover > MARKET_TURNOVER_TOP_TRILLION:  # 严格大于，"破10万亿"语义是超过非等于
        return f"宏观:成交额破10万亿({turnover:.1f}万亿)"

    # TODO 储蓄搬家>30%：无数据源
    # TODO 官方发金牌：无数据源
    return None
