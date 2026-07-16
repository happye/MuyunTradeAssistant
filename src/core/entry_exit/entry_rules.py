"""买点规则 — 突破买入 / 回调买入 / 北向资金确认"""

from typing import Optional
from dataclasses import dataclass
from src.data.models import StockData


@dataclass
class EntrySignal:
    """买入信号"""
    triggered: bool
    entry_type: Optional[str] = None       # "breakout" / "pullback"
    entry_price: Optional[float] = None
    entry_action: Optional[str] = None     # "ENTRY" / "ADD"
    entry_ratio: float = 0.0              # 建议仓位比例
    reason: str = ""


def check_breakout(data: StockData, config: dict) -> Optional[EntrySignal]:
    """突破买入检查

    条件（AND）：
    1. price >= high_Nd（突破N日高点）
    2. volume > avg_volume_20 * 1.5（放量确认）
    3. ma5 > ma20 > ma60（多头排列）

    Args:
        data: 股票数据
        config: 配置参数（含 breakout.high_window, breakout.volume_ratio）
    """
    breakout_cfg = config.get("breakout", {})
    high_window = breakout_cfg.get("high_window", 20)
    volume_ratio_min = breakout_cfg.get("volume_ratio", 1.5)

    # 条件1: 价格突破N日高点。StockData 只有 high_60d / high_120d（无 high_20d），
    # 故 20/60 均用 60 日高点。审查 M-D：配置已改 60 对齐实际（市场动荡避免假突破）。
    if high_window == 120:
        high_nd = data.high_120d
    else:  # 60（或 20 兜底，均用 high_60d）
        high_nd = data.high_60d

    if high_nd is None or data.price < high_nd:
        return None

    # 条件2: 放量确认
    avg_vol = data.avg_volume_20
    if avg_vol is None or avg_vol <= 0:
        return None
    if data.volume < avg_vol * volume_ratio_min:
        return None

    # 条件3: 多头排列
    if data.ma5 is None or data.ma20 is None or data.ma60 is None:
        return None
    if not (data.ma5 > data.ma20 > data.ma60):
        return None

    return EntrySignal(
        triggered=True,
        entry_type="breakout",
        entry_price=data.price,
        entry_action="ENTRY",
        entry_ratio=0.5,
        reason=f"突破{high_window}日高点({high_nd:.2f})+放量(x{data.volume/avg_vol:.1f})+多头排列"
    )


def check_pullback(data: StockData, config: dict) -> Optional[EntrySignal]:
    """回调买入检查

    条件（AND）：
    1. 价格在 MA20 附近（|price-MA20|/MA20 < ma_near_pct）
    2. 缩量（volume < avg_volume_20 * volume_ratio_max）
    3. 此前有过明显上涨（change_20d > prior_gain_pct）
    4. 均线多头排列（ma5 > ma20 > ma60）

    Args:
        data: 股票数据
        config: 配置参数
    """
    pullback_cfg = config.get("pullback", {})
    ma_near_pct = pullback_cfg.get("ma_near_pct", 0.03)
    volume_ratio_max = pullback_cfg.get("volume_ratio_max", 0.8)
    prior_gain_pct = pullback_cfg.get("prior_gain_pct", 10)

    # 条件1: 价格在 MA20 附近
    if data.ma20 is None or data.ma20 <= 0:
        return None
    deviation = abs(data.price - data.ma20) / data.ma20
    if deviation > ma_near_pct:
        return None

    # 条件2: 缩量
    avg_vol = data.avg_volume_20
    if avg_vol is None or avg_vol <= 0:
        return None
    if data.volume > avg_vol * volume_ratio_max:
        return None

    # 条件3: 此前有过明显上涨（用均线斜率替代，change_20d 在 StockData 中可能不可用）
    # 用 ma5 > ma60 且 ma20 > ma60 作为趋势确认
    if data.ma5 is None or data.ma20 is None or data.ma60 is None:
        return None
    if not (data.ma5 > data.ma20 > data.ma60):
        return None

    # 条件4: 此前上涨幅度（用价格相对MA60的偏离度估算）
    gain_from_ma60 = (data.price - data.ma60) / data.ma60 * 100
    if gain_from_ma60 < prior_gain_pct:
        return None

    entry_price = round(data.ma20 * (1 + ma_near_pct / 2), 2)
    return EntrySignal(
        triggered=True,
        entry_type="pullback",
        entry_price=entry_price,
        entry_action="ENTRY",
        entry_ratio=0.3,
        reason=f"回调MA20附近(偏离{deviation*100:.1f}%)+缩量+多头排列+前期涨幅{gain_from_ma60:.0f}%"
    )


def check_northbound(data: StockData, northbound_data: Optional[dict] = None) -> Optional[EntrySignal]:
    """北向资金确认 — 辅助买点增强

    在主买点触发后，用北向资金数据确认。
    回测中若无可用的北向历史数据，自动降级为跳过。

    Args:
        data: 股票数据
        northbound_data: 北向资金数据（可选，回测中可能为None）
    """
    if northbound_data is None:
        return None  # 回测中降级跳过，不阻断主买点

    consecutive_days = northbound_data.get("consecutive_inflow_days", 0)
    if consecutive_days < 3:
        return None

    return EntrySignal(
        triggered=True,
        entry_type="northbound",
        entry_price=data.price,
        entry_action="ENTRY",
        entry_ratio=0.2,
        reason=f"北向资金连续{consecutive_days}日净流入确认"
    )