"""高位止盈信号模块（跳法A 阶段2 / v0.8.6.4）

笨总"何时该止盈离场"体系的工程化——三层大顶信号：
- 宏观层（macro.py）：全市场成交额破 10 万亿 等系统性见顶信号
- 个股层（stock.py）：换手率超 40% / 缩量加速上涨 / 实控人减持
- 板块层（sector.py）：渗透率 30% 魔咒 / 旗手滞涨（阶段2 未实现，数据源缺失）

设计原则（绕开 AI 天花板）：全部是**客观硬规则**，不依赖 AI 实时判断"该不该卖"。
触发即作为 P1 信号（仅次于致命止损），PlanGuard 不可压制——即使气宗持有期内
出现大顶信号也强制离场。
"""

from src.core.exit_signals.macro import check_macro_top_signal
from src.core.exit_signals.stock import check_stock_top_signal

__all__ = ["check_macro_top_signal", "check_stock_top_signal", "check_top_signals"]


def check_top_signals(stock_data, code, *, turnover_pct=None, announcements=None,
                      market_turnover_trillion=None):
    """汇总宏观+个股高位止盈信号，返回首个触发的信号描述（无则 None）。

    Args:
        stock_data: StockData（个股缩量加速判定用）
        code: 股票代码
        turnover_pct: 当日换手率(%)，live 路径可从 MarketCache 快照传入；
                      回测路径通常无（历史换手率需流通股本数据），传 None 跳过该子信号
        announcements: 近期公告列表 [{title,...}]，live 路径可传；回测历史公告获取受限，传 None 跳过
        market_turnover_trillion: 全市场成交额(万亿)，传 None 时 macro 层自行拉取

    Returns:
        Optional[str]: 触发的信号描述，如 "个股:换手率超40%(45.2%)"；无触发返回 None
    """
    macro = check_macro_top_signal(market_turnover_trillion=market_turnover_trillion)
    if macro:
        return macro
    return check_stock_top_signal(
        stock_data, code, turnover_pct=turnover_pct, announcements=announcements
    )
