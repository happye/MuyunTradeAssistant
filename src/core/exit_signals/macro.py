"""宏观层高位止盈信号（跳法A 阶段2 / v0.8.6.4）

笨总教学：宏观见顶三信号 = 成交额破10万亿 + 储蓄搬家>30% + 官方发金牌。
当前仅成交额可获取数据源，其余两项无现成源（诚实声明，见 TODO）。

v0.8.6.8（笨总视频理念优化报告 1.1）：新增 assess_liquidity_state()，用笨总实操阈值
（1万亿基准/0.8枯竭/1.3风险线/1.5充沛）给出"当下流动性温度"，让永不触发的10万亿
极端顶之外也有可感知的宏观层。advisory 展示用，不触发强制清仓。
"""

import logging
from datetime import datetime
from typing import Optional

from src.data.models import SignalFinding

logger = logging.getLogger(__name__)

# 全市场单日成交额阈值（万亿）-- 笨总教学的"成交额破10万亿"系统性见顶信号
# 注：A股历史峰值~3.5万亿，此阈值面向未来极端牛市（ISS-052 已知不可达，不擅改笨总原话）
MARKET_TURNOVER_TOP_TRILLION = 10.0

# 笨总实操流动性阈值（教学二/五，三处一致）--笨总日常用的成交额分档，非10万亿极端顶
# 1万亿=基准(↔3050点)；0.8万亿=枯竭(打8折，风险)；1.3万亿=风险线(不连续破=无风险)；1.5万亿=充沛(×1.2加成)
# 用于 advisory 环境温度计（展示用），不触发强制清仓（force-exit 仍只归 10万亿极端顶）
LIQUIDITY_EXHAUSTED = 0.8
LIQUIDITY_TIGHT = 1.3
LIQUIDITY_ABUNDANT = 1.5


def check_macro_top_signal(market_turnover_trillion: Optional[float] = None) -> Optional[SignalFinding]:
    """检查宏观层大顶信号。

    Args:
        market_turnover_trillion: 全市场成交额(万亿)。None 时自动调 data_provider 拉取
            （live 路径可用；回测无历史全市场成交额，应由调用方传 None 并接受跳过）

    Returns:
        Optional[SignalFinding]：research 资格的系统性风险提醒（v0.8.29 降级后
        宏观层不再存在 force-exit 路径）；无触发返回 None

    数据源现状（诚实声明）：
    - 成交额：data_provider.get_market_turnover()（新浪全市场快照），可用
    - 储蓄搬家>30%：需居民存款月度数据（央行），无实时源 -> TODO 未实现
    - 官方发金牌：需政策新闻语义识别，靠 AI 不作硬规则 -> TODO 未实现
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
        # ISS-117 A12（S0 降级）：单一绝对阈值未经市场规模归一化/策略实证，只作
        # 系统性风险研究提醒，不对全部持仓强制清仓
        return SignalFinding(
            signal_id="research.macro.turnover_10t",
            source="data_provider.get_market_turnover()",
            securities=[], as_of=datetime.now().strftime("%Y-%m-%d"),
            data_quality="UNKNOWN", action_scope="research", verified=False,
            strategy_binding="iss117-s0/ruling-2026-10-09",
            detail=f"宏观:成交额破10万亿({turnover:.1f}万亿)",
            reason="单一绝对阈值（ISS-117 A12）：未经市场规模归一化与策略实证，"
                   "降级为系统性风险研究提醒，不自动获得全持仓清仓权限")

    # TODO 储蓄搬家>30%：无数据源
    # TODO 官方发金牌：无数据源
    return None


def assess_liquidity_state(market_turnover_trillion: Optional[float] = None) -> Optional[tuple]:
    """笨总实操流动性状态评估（教学二/五，advisory 环境温度计）。

    笨总日常用的是成交额分档（1万亿基准/0.8枯竭/1.3风险线/1.5充沛），
    而非 10万亿极端顶。10万亿面向未来极端牛市、当前永不触发，导致宏观层基本是死的；
    此函数用笨总实操阈值给出"当下流动性温度"，让宏观层活起来。

    advisory 展示用，**不触发强制清仓**（force-exit 仍只归 check_macro_top_signal 的 10万亿极端顶）。
    笨总：流动性枯竭=选股难度激增/普跌概率大（防守）；充沛=可格局（进攻）。
    （v0.8.28.1 注：10万亿极端顶也已降级 research——宏观层现无 force-exit 路径。）

    Args:
        market_turnover_trillion: 全市场成交额(万亿)。None 时自动拉取（live 可用）

    Returns:
        (state, description) 或 None（成交额获取失败）
        state: "枯竭"/"偏紧"/"正常"/"充沛"
    """
    turnover = market_turnover_trillion
    if turnover is None:
        try:
            from src.core.benzong import data_provider
            turnover = data_provider.get_market_turnover()
        except Exception as e:
            logger.debug(f"流动性状态: 成交额获取失败: {e}")
            return None

    if turnover is None:
        return None

    if turnover < LIQUIDITY_EXHAUSTED:
        return ("枯竭", f"成交额{turnover:.2f}万亿<0.8万亿枯竭线，流动性风险（笨总：打8折，普跌概率大）")
    if turnover < LIQUIDITY_TIGHT:
        return ("偏紧", f"成交额{turnover:.2f}万亿<1.3万亿风险线，流动性偏紧（选股难度升）")
    if turnover < LIQUIDITY_ABUNDANT:
        return ("正常", f"成交额{turnover:.2f}万亿，流动性正常")
    return ("充沛", f"成交额{turnover:.2f}万亿>1.5万亿充沛线（笨总：×1.2加成，可格局）")


# 报告3.3 市场宽度阈值（笨总直播：通杀/分化/普涨）
BREADTH_WASHOUT_RATIO = 0.80   # 下跌家数占比>80% = 通杀（情绪杀，非个股逻辑问题）
BREADTH_BROAD_RALLY_RATIO = 0.80  # 上涨家数占比>80% = 普涨


def assess_market_breadth() -> Optional[tuple]:
    """笨总市场宽度评估（报告3.3，advisory 环境温度计）。

    用全市场涨跌家数比判断市场环境状态（笨总直播多视频共识）：
    - 通杀：下跌>80%（"好公司也跌，市场情绪杀非个股逻辑"，别误杀）
    - 普涨：上涨>80%（普涨行情，选股难度低）
    - 分化：多空分歧（结构性行情，选对赛道比判断涨跌重要）

    advisory 展示用，不触发强制清仓。数据源 MarketCache 全市场快照（新浪）。
    fail-open（获取失败返回 None）。
    """
    try:
        from src.scanner.market_cache import MarketCache
        df = MarketCache().get_all_stocks()
        if df is None or df.empty or "涨跌幅" not in df.columns:
            return None
        import pandas as pd
        chg = pd.to_numeric(df["涨跌幅"], errors="coerce").dropna()
        n = len(chg)
        if n < 100:  # 样本不足不判断
            return None
        up = int((chg > 0).sum())
        down = int((chg < 0).sum())
        flat = n - up - down
        down_ratio = down / n
        up_ratio = up / n
        if down_ratio > BREADTH_WASHOUT_RATIO:
            return ("通杀", f"下跌{down}/{n}({down_ratio:.0%})>80%，市场情绪杀（好公司也跌，别误杀个股）")
        if up_ratio > BREADTH_BROAD_RALLY_RATIO:
            return ("普涨", f"上涨{up}/{n}({up_ratio:.0%})>80%，普涨行情（选股难度低）")
        return ("分化", f"涨{up}/跌{down}/平{flat}（结构性行情，选对赛道比判断涨跌重要）")
    except Exception as e:
        logger.debug(f"市场宽度评估失败: {e}")
        return None
