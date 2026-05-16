"""EntryExitCalculator — 买卖点主计算器

在 AI Modifier 之后、Strategy 之前运行，协调买点和卖点规则，
输出 EntryExitResult。买卖点触发后覆盖 DecisionResult 信号。
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field

from src.data.models import StockData
from src.core.entry_exit.entry_rules import (
    EntrySignal,
    check_breakout,
    check_pullback,
    check_northbound,
)
from src.core.entry_exit.exit_rules import (
    ExitSignal,
    check_chandelier,
    check_trend_break,
    check_take_profit,
)

logger = logging.getLogger(__name__)


class EntryExitResult(BaseModel):
    """买卖点计算结果"""
    # 买点
    entry_triggered: bool = False
    entry_type: Optional[str] = None        # "breakout" / "pullback"
    entry_price: Optional[float] = None
    entry_action: Optional[str] = None      # "ENTRY" / "ADD"
    entry_ratio: float = 0.0               # 建议仓位比例
    entry_reason: str = ""

    # 卖点
    exit_triggered: bool = False
    exit_type: Optional[str] = None         # "chandelier_stop" / "trend_break" / "take_profit"
    exit_price: Optional[float] = None
    exit_action: Optional[str] = None       # "TRIM" / "EXIT" / "STOP"
    exit_ratio: float = 0.0                # 减仓比例
    exit_reason: str = ""

    # 辅助信息
    atr_value: Optional[float] = None
    highest_since_entry: Optional[float] = None
    chandelier_stop_price: Optional[float] = None

    # 最终动作
    override_decision: bool = False
    override_action: Optional[str] = None   # 覆盖后的交易动作


class EntryExitCalculator:
    """买点/卖点计算器

    插入点: AI Modifier → [EntryExitCalculator] → Strategy → Execution
    运行逻辑:
        - 空仓时只看买点
        - 持仓时只看卖点
        - 买卖点触发后设置 override_decision=True，覆盖下游 Strategy 决策
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._enabled = self.config.get("enabled", True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def calculate(
        self,
        data: StockData,
        has_position: bool = False,
        entry_price: Optional[float] = None,
        position_tier: str = "pilot",
        high_since_entry: Optional[float] = None,
        northbound_data: Optional[dict] = None,
    ) -> EntryExitResult:
        """计算买卖点

        Args:
            data: 股票数据
            has_position: 是否持仓
            entry_price: 开仓均价（持仓时必传）
            position_tier: 仓位档位（pilot/base/full）
            high_since_entry: 持仓期间最高价
            northbound_data: 北向资金数据（可选）

        Returns:
            EntryExitResult
        """
        if not self._enabled:
            return EntryExitResult()

        if has_position:
            return self._check_exits(
                data, entry_price, position_tier, high_since_entry
            )
        else:
            return self._check_entries(data, northbound_data)

    def _check_entries(
        self,
        data: StockData,
        northbound_data: Optional[dict] = None,
    ) -> EntryExitResult:
        """检查买点（空仓时）"""
        # 优先级: 突破买入 > 回调买入
        breakout = check_breakout(data, self.config)
        if breakout:
            logger.info(f"[EntryExit] 突破买入触发: {breakout.reason}")
            return EntryExitResult(
                entry_triggered=True,
                entry_type=breakout.entry_type,
                entry_price=breakout.entry_price,
                entry_action=breakout.entry_action,
                entry_ratio=breakout.entry_ratio,
                entry_reason=breakout.reason,
                atr_value=data.atr_14,
                override_decision=True,
                override_action="ENTRY",
            )

        pullback = check_pullback(data, self.config)
        if pullback:
            logger.info(f"[EntryExit] 回调买入触发: {pullback.reason}")
            return EntryExitResult(
                entry_triggered=True,
                entry_type=pullback.entry_type,
                entry_price=pullback.entry_price,
                entry_action=pullback.entry_action,
                entry_ratio=pullback.entry_ratio,
                entry_reason=pullback.reason,
                atr_value=data.atr_14,
                override_decision=True,
                override_action="ENTRY",
            )

        # 无买点
        return EntryExitResult(atr_value=data.atr_14)

    def _check_exits(
        self,
        data: StockData,
        entry_price: Optional[float] = None,
        position_tier: str = "pilot",
        high_since_entry: Optional[float] = None,
    ) -> EntryExitResult:
        """检查卖点（持仓时）

        优先级: Chandelier Exit > 趋势破坏 > 止盈
        """
        # 1. Chandelier Exit（最高优先级）
        chandelier = check_chandelier(
            data, self.config, position_tier, high_since_entry, entry_price
        )
        if chandelier:
            logger.info(f"[EntryExit] Chandelier Exit触发: {chandelier.reason}")
            return EntryExitResult(
                exit_triggered=True,
                exit_type=chandelier.exit_type,
                exit_price=chandelier.exit_price,
                exit_action=chandelier.exit_action,
                exit_ratio=chandelier.exit_ratio,
                exit_reason=chandelier.reason,
                atr_value=chandelier.atr_value,
                highest_since_entry=chandelier.highest_since_entry,
                chandelier_stop_price=chandelier.chandelier_stop_price,
                override_decision=True,
                override_action=chandelier.exit_action,
            )

        # 2. 趋势破坏
        trend_break = check_trend_break(data, self.config)
        if trend_break:
            logger.info(f"[EntryExit] 趋势破坏触发: {trend_break.reason}")
            return EntryExitResult(
                exit_triggered=True,
                exit_type=trend_break.exit_type,
                exit_price=trend_break.exit_price,
                exit_action=trend_break.exit_action,
                exit_ratio=trend_break.exit_ratio,
                exit_reason=trend_break.reason,
                atr_value=data.atr_14,
                override_decision=True,
                override_action=trend_break.exit_action,
            )

        # 3. 止盈
        take_profit = check_take_profit(
            data, self.config, entry_price, high_since_entry
        )
        if take_profit:
            logger.info(f"[EntryExit] 止盈触发: {take_profit.reason}")
            return EntryExitResult(
                exit_triggered=True,
                exit_type=take_profit.exit_type,
                exit_price=take_profit.exit_price,
                exit_action=take_profit.exit_action,
                exit_ratio=take_profit.exit_ratio,
                exit_reason=take_profit.reason,
                atr_value=data.atr_14,
                highest_since_entry=high_since_entry,
                override_decision=True,
                override_action=take_profit.exit_action,
            )

        # 无卖点
        return EntryExitResult(atr_value=data.atr_14)