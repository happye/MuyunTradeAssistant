"""卖点规则 — Chandelier Exit / 趋势破坏 / 止盈"""

from typing import Optional
from dataclasses import dataclass
from src.data.models import StockData


@dataclass
class ExitSignal:
    """卖出信号"""
    triggered: bool
    exit_type: Optional[str] = None        # "chandelier_stop" / "trend_break" / "take_profit"
    exit_price: Optional[float] = None
    exit_action: Optional[str] = None      # "TRIM" / "EXIT" / "STOP"
    exit_ratio: float = 0.0               # 减仓比例
    reason: str = ""

    # Chandelier Exit 辅助
    atr_value: Optional[float] = None
    highest_since_entry: Optional[float] = None
    chandelier_stop_price: Optional[float] = None


def check_chandelier(
    data: StockData,
    config: dict,
    position_tier: str = "pilot",
    high_since_entry: Optional[float] = None,
    entry_price: Optional[float] = None,
) -> Optional[ExitSignal]:
    """Chandelier Exit 移动止损检查

    规则: 若 price <= highest_since_entry - N * ATR，则触发卖出
    三重 N 值：试探=4 / 基础=3 / 重仓=2

    Args:
        data: 股票数据（含 atr_14）
        config: 配置参数
        position_tier: 仓位档位（pilot/base/full）
        high_since_entry: 持仓期间最高价
        entry_price: 开仓均价
    """
    chandelier_cfg = config.get("chandelier", {})
    n_map = {
        "pilot": chandelier_cfg.get("n_pilot", 4),
        "base": chandelier_cfg.get("n_base", 3),
        "full": chandelier_cfg.get("n_full", 2),
    }
    n_mult = n_map.get(position_tier, 4)

    atr = data.atr_14
    if atr is None:
        return None

    # 持仓期间最高价
    highest = high_since_entry or entry_price or data.high or data.price
    if highest is None:
        return None

    stop_price = highest - n_mult * atr

    # 触发检查
    if data.price > stop_price:
        return None  # 未触发

    exit_action = "EXIT" if position_tier == "pilot" else "TRIM"
    exit_ratio = 1.0 if exit_action == "EXIT" else 0.5

    return ExitSignal(
        triggered=True,
        exit_type="chandelier_stop",
        exit_price=data.price,
        exit_action=exit_action,
        exit_ratio=exit_ratio,
        reason=f"Chandelier Exit触发: 价格{data.price:.2f}<=止损价{stop_price:.2f} "
               f"(最高{highest:.2f}-{n_mult}×ATR{atr:.2f})",
        atr_value=atr,
        highest_since_entry=highest,
        chandelier_stop_price=round(stop_price, 2),
    )


def check_trend_break(data: StockData, config: dict) -> Optional[ExitSignal]:
    """趋势破坏检查

    条件（OR）：
    1. 短期均线死叉: ma5 < ma20（快速趋势破坏）
    2. 中期均线死叉: ma10 < ma60（中期趋势破坏）

    短期死叉 → TRIM（减仓40%）
    中期死叉 → EXIT（清仓）

    Args:
        data: 股票数据
        config: 配置参数
    """
    trend_cfg = config.get("trend_break", {})
    trim_ratio = trend_cfg.get("trim_ratio", 0.4)

    # 中期趋势破坏: EXIT
    if (data.ma10 is not None and data.ma60 is not None
            and data.ma10 < data.ma60):
        return ExitSignal(
            triggered=True,
            exit_type="trend_break",
            exit_price=data.price,
            exit_action="EXIT",
            exit_ratio=1.0,
            reason=f"中期趋势破坏: MA10({data.ma10:.2f})<MA60({data.ma60:.2f})"
        )

    # 短期趋势破坏: TRIM
    if (data.ma5 is not None and data.ma20 is not None
            and data.ma5 < data.ma20):
        return ExitSignal(
            triggered=True,
            exit_type="trend_break",
            exit_price=data.price,
            exit_action="TRIM",
            exit_ratio=trim_ratio,
            reason=f"短期趋势破坏: MA5({data.ma5:.2f})<MA20({data.ma20:.2f})"
        )

    return None


def check_take_profit(
    data: StockData,
    config: dict,
    entry_price: Optional[float] = None,
    high_since_entry: Optional[float] = None,
) -> Optional[ExitSignal]:
    """止盈检查

    三层止盈：
    1. 第一档: 盈利 >= tier1_pct(10%) → TRIM 30%
    2. 第二档: 盈利 >= tier2_pct(20%) → TRIM 40%
    3. 移动止盈: 从最高点回撤 trail_pct(30%) → EXIT 剩余

    Args:
        data: 股票数据
        config: 配置参数
        entry_price: 开仓均价
        high_since_entry: 持仓期间最高价
    """
    take_profit_cfg = config.get("take_profit", {})
    tier1_pct = take_profit_cfg.get("tier1_pct", 10)
    tier1_trim = take_profit_cfg.get("tier1_trim", 0.3)
    tier2_pct = take_profit_cfg.get("tier2_pct", 20)
    tier2_trim = take_profit_cfg.get("tier2_trim", 0.4)
    trail_pct = take_profit_cfg.get("trail_pct", 0.3)

    if entry_price is None or entry_price <= 0:
        return None

    profit_pct = (data.price - entry_price) / entry_price * 100

    # 移动止盈（最高优先级）: 从最高点回撤超过 trail_pct
    if high_since_entry and high_since_entry > entry_price:
        drawdown = (high_since_entry - data.price) / high_since_entry * 100
        if drawdown >= trail_pct * 100:  # trail_pct=0.3 → 30%回撤
            return ExitSignal(
                triggered=True,
                exit_type="take_profit",
                exit_price=data.price,
                exit_action="EXIT",
                exit_ratio=1.0,
                reason=f"移动止盈触发: 从最高{high_since_entry:.2f}回撤{drawdown:.1f}%"
            )

    # 第二档止盈
    if profit_pct >= tier2_pct:
        return ExitSignal(
            triggered=True,
            exit_type="take_profit",
            exit_price=data.price,
            exit_action="TRIM",
            exit_ratio=tier2_trim,
            reason=f"止盈T2: 盈利{profit_pct:.1f}%>={tier2_pct}% → 减仓{tier2_trim*100:.0f}%"
        )

    # 第一档止盈
    if profit_pct >= tier1_pct:
        return ExitSignal(
            triggered=True,
            exit_type="take_profit",
            exit_price=data.price,
            exit_action="TRIM",
            exit_ratio=tier1_trim,
            reason=f"止盈T1: 盈利{profit_pct:.1f}%>={tier1_pct}% → 减仓{tier1_trim*100:.0f}%"
        )

    return None