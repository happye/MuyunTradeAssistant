"""价格计算模块 — ATR止损价 / 突破价位 / 回调价位 / Chandelier Exit 价格"""

from typing import Optional
from src.data.models import StockData


def calc_atr_stop_price(
    data: StockData,
    atr_period: int = 14,
    n_multiplier: float = 3.0,
    use_high_since_entry: bool = False,
    high_since_entry: Optional[float] = None,
    highest_since_entry: Optional[float] = None,
) -> Optional[float]:
    """计算 ATR 移动止损价格（Chandelier Exit）

    公式: stop_price = highest_since_entry - N * ATR
    无持仓时基于近期高点计算。

    Args:
        data: 股票数据（含 atr_14）
        atr_period: ATR 周期
        n_multiplier: N 倍乘数（试探=4/基础=3/重仓=2）
        use_high_since_entry: 是否使用持仓期间最高价
        high_since_entry: 持仓期间最高价（若有持仓）
        highest_since_entry: 持仓期间最高价（别名，兼容旧代码）
    """
    atr = data.atr_14
    if atr is None:
        return None

    highest = (high_since_entry or highest_since_entry or data.high or data.price)
    stop = highest - n_multiplier * atr
    return round(stop, 2)


def calc_breakout_price(data: StockData, high_window: int = 20) -> Optional[float]:
    """计算突破买入价位

    突破买入触发价 = 近N日最高点

    Args:
        data: 股票数据
        high_window: N日窗口
    """
    if high_window == 20:
        return data.high_60d  # high_60d 实际存储的是20日高点
    if high_window == 60:
        return data.high_60d
    if high_window == 120:
        return data.high_120d
    return data.high_60d


def calc_pullback_price(data: StockData, ma_near_pct: float = 0.03) -> Optional[float]:
    """计算回调买入价位

    回调买入区：MA20 附近的区间 [MA20, MA20*(1+ma_near_pct)]

    Args:
        data: 股票数据
        ma_near_pct: MA20 附近容忍度
    """
    ma20 = data.ma20
    if ma20 is None:
        return None
    return round(ma20 * (1 + ma_near_pct / 2), 2)


def calc_trailing_stop_price(
    high_since_entry: float,
    trail_pct: float = 0.3,
    profit_pct: float = 0.0,
) -> Optional[float]:
    """计算移动止盈价格（从最高点回撤触发）

    stop_price = high_since_entry * (1 - trail_pct * profit_pct/100)
    简化版: 从持仓期间最高点回撤 trail_pct 比例
    """
    if high_since_entry is None or high_since_entry <= 0:
        return None
    return round(high_since_entry * (1 - trail_pct), 2)


def calc_stop_loss_atr(data: StockData, entry_price: float, atr_mult: float = 2.0) -> Optional[float]:
    """基于 ATR 的初始止损价

    stop_price = entry_price - atr_mult * ATR
    """
    atr = data.atr_14
    if atr is None:
        return None
    return round(entry_price - atr_mult * atr, 2)