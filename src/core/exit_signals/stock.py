"""个股层高位止盈信号（跳法A 阶段2 / v0.8.6.4）

笨总教学：个股见顶三信号 = 换手率>40% + 缩量加速上涨 + 实控人减持。
全部客观硬规则，不依赖 AI 判断。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 个股换手率见顶阈值(%)
TURNOVER_TOP_PCT = 40.0
# 缩量加速：近期量比阈值（当前量 / 均量 < 此值视为缩量）
SHRINK_VOLUME_RATIO = 0.7
# 缩量加速：涨幅阈值(%)，缩量+大涨=加速赶顶
SHRINK_GAIN_PCT = 15.0

# 实控人减持关键词（标题需同时命中减持词 + 主体词）
_REDUCE_KEYWORDS = ("减持", "拟减持")
_HOLDER_KEYWORDS = ("控股股东", "实际控制人", "实控人", "大股东")


def check_stock_top_signal(stock_data, code: str, *, turnover_pct: Optional[float] = None,
                           announcements: Optional[list] = None) -> Optional[str]:
    """检查个股层大顶信号，返回首个触发的描述（无则 None）。

    Args:
        stock_data: StockData（缩量加速判定用 volume/avg_volume/change_pct）
        code: 股票代码
        turnover_pct: 当日换手率(%)。live 路径可从 MarketCache 快照传入；
            回测路径通常 None（历史换手率需流通股本，data_provider 未提供）→ 跳过该子信号
        announcements: 近期公告列表 [{title,...}]。None 时不检查实控人减持
            （回测历史公告获取受限，诚实声明）

    Returns:
        Optional[str]: 信号描述，如 "个股:换手率超40%(45.2%)"
    """
    # 信号1：换手率 > 40%
    if turnover_pct is not None and turnover_pct >= TURNOVER_TOP_PCT:
        return f"个股:换手率超40%({turnover_pct:.1f}%)"

    # 信号2：缩量加速上涨（量比 < 0.7 且 涨幅 > 15%）
    accel = _check_shrink_acceleration(stock_data)
    if accel:
        return accel

    # 信号3：实控人减持公告
    if announcements:
        reduce_sig = _check_holder_reduction(announcements)
        if reduce_sig:
            return reduce_sig

    return None


def _check_shrink_acceleration(stock_data) -> Optional[str]:
    """缩量加速上涨：成交量萎缩但价格大涨，典型赶顶背离。"""
    if stock_data is None:
        return None
    try:
        vol = getattr(stock_data, "volume", None)
        avg_vol = getattr(stock_data, "avg_volume_5", None) or getattr(stock_data, "avg_volume_20", None)
        change_pct = getattr(stock_data, "change_pct", None)
        if not vol or not avg_vol or avg_vol <= 0 or change_pct is None:
            return None
        ratio = vol / avg_vol
        if ratio < SHRINK_VOLUME_RATIO and change_pct > SHRINK_GAIN_PCT:
            return f"个股:缩量加速(量比{ratio:.2f},涨{change_pct:.1f}%)"
    except Exception as e:
        logger.debug(f"缩量加速判定异常: {e}")
    return None


def _check_holder_reduction(announcements: list) -> Optional[str]:
    """扫公告标题：同时命中减持词 + 控股股东/实控人主体词。"""
    for ann in announcements:
        title = (ann.get("title") if isinstance(ann, dict) else str(ann)) or ""
        if any(k in title for k in _REDUCE_KEYWORDS) and any(k in title for k in _HOLDER_KEYWORDS):
            return f"个股:实控人减持({title[:20]})"
    return None
