"""高位止盈信号模块（跳法A 阶段2 / v0.8.6.4 + 报告2.1 板块层补全）

笨总"何时该止盈离场"体系的工程化--三层大顶信号（笨总教学八，各层内部"或"关系）：
- 宏观层（macro.py）：成交额破 10 万亿 + assess_liquidity_state 流动性温度（报告1.1）
- 个股层（stock.py）：换手率超 40% / 缩量加速上涨 / 实控人减持（+ 报告2.2 三倍定律等）
- 板块层（sector.py）：渗透率 30% 魔咒 / 旗手滞涨 / 新赛道虹吸（报告2.1）

设计原则（绕开 AI 天花板）：全部是**客观硬规则**，不依赖 AI 实时判断"该不该卖"。
触发即作为 P1 信号（仅次于致命止损），PlanGuard 不可压制--即使气宗持有期内
出现大顶信号也强制离场。
"""

from src.core.exit_signals.macro import check_macro_top_signal, assess_liquidity_state, assess_market_breadth
from src.core.exit_signals.stock import check_stock_top_signal
from src.core.exit_signals.sector import check_sector_top_signal

__all__ = [
    "check_macro_top_signal", "assess_liquidity_state", "assess_market_breadth",
    "check_stock_top_signal", "check_sector_top_signal", "check_top_signals",
]


def check_top_signals(stock_data, code, *, turnover_pct=None, announcements=None,
                      market_turnover_trillion=None, trade_plan=None, live: bool = False):
    """汇总宏观+板块+个股高位止盈信号，返回首个触发的信号描述（无则 None）。

    检查顺序：宏观(全市场) -> 板块(赛道) -> 个股(操作层)。笨总原话各层内部"或"关系。

    Args:
        stock_data: StockData（个股缩量加速/近20日涨幅判定用）
        code: 股票代码
        turnover_pct: 当日换手率(%)，live 路径可从 MarketCache 快照传入；
                      回测路径通常无（历史换手率需流通股本数据），传 None 跳过该子信号
        announcements: 近期公告列表 [{title,...}]，live 路径可传；回测历史公告获取受限，传 None 跳过
        market_turnover_trillion: 全市场成交额(万亿)，live 传 None 时 macro 层自行拉取；
                    回测(live=False)传 None 则跳过宏观层（不实时拉取，防未来信息+网络拖慢）
        trade_plan: TradePlan（板块层取 flagbearer_code / penetration_stage，报告2.1）；
                    None 时板块层跳过（向后兼容）
        live: True 时启用股东户数/融资余额信号（akshare 网络+日缓存）。回测传 False。

    Returns:
        Optional[str]: 触发的信号描述，如 "个股:换手率超40%(45.2%)"；无触发返回 None
    """
    macro = None
    # 宏观层门控（与个股层 live 门控同款纪律）：live=False（回测）且未显式传入成交额时
    # 跳过，绝不在回测里实时拉取全市场成交额——拉取失败会拖垮回测速度，
    # 拉取成功则是把"今天"的成交额注入历史bar（未来信息）。回测无该字段，恒为 None。
    if live or market_turnover_trillion is not None:
        macro = check_macro_top_signal(market_turnover_trillion=market_turnover_trillion)
    if macro:
        return macro

    sector = check_sector_top_signal(trade_plan=trade_plan, stock_data=stock_data, code=code)
    if sector:
        return sector

    return check_stock_top_signal(
        stock_data, code, turnover_pct=turnover_pct, announcements=announcements,
        live=live, mode=trade_plan.mode if trade_plan else None
    )
