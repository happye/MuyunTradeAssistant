"""买卖点精确触发模块（v0.8.3 Phase C）

EntryExitCalculator 在 AI Modifier 之后、Strategy 之前运行，
协调买点规则和卖点规则，输出 EntryExitResult。
买卖点触发后覆盖 DecisionResult 信号。

参考方法论:
- Chandelier Exit (Le Beau)
- ATR 止损 (Wilder)
- 海龟交易法则 (Dennis/Eckhardt)
- CANSLIM (O'Neil) / SEPA (Minervini)
"""

from src.core.entry_exit.calculator import EntryExitCalculator, EntryExitResult
from src.core.entry_exit.entry_rules import EntrySignal, check_breakout, check_pullback, check_northbound
from src.core.entry_exit.exit_rules import ExitSignal, check_chandelier, check_trend_break, check_take_profit
from src.core.entry_exit.price_level import (
    calc_atr_stop_price,
    calc_breakout_price,
    calc_pullback_price,
    calc_trailing_stop_price,
    calc_stop_loss_atr,
)

__all__ = [
    "EntryExitCalculator",
    "EntryExitResult",
    "EntrySignal",
    "check_breakout",
    "check_pullback",
    "check_northbound",
    "ExitSignal",
    "check_chandelier",
    "check_trend_break",
    "check_take_profit",
    "calc_atr_stop_price",
    "calc_breakout_price",
    "calc_pullback_price",
    "calc_trailing_stop_price",
    "calc_stop_loss_atr",
]