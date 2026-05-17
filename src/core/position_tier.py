"""金字塔仓位管理（v0.8.3 Phase D）

仓位档位：FLAT → PILOT → BASE → FULL
升级需盈利保护，降级触发减仓，止损按档位分层。
"""

import logging
from typing import Optional
from src.data.models import StockData, StrategyState, PositionTier

logger = logging.getLogger(__name__)


class PyramidPositionManager:
    """金字塔仓位管理器

    管理仓位档位升级/降级，计算各档位的仓位比例和止损线。
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._tiers = self.config.get("position_tiers", {})

    def get_tier_ratio(self, tier: PositionTier) -> float:
        """获取档位对应的仓位比例"""
        tier_cfg = self._tiers.get(tier.value.lower(), {})
        return tier_cfg.get("ratio", 0.15 if tier == PositionTier.PILOT else 0.30)

    def get_tier_stop_loss(self, tier: PositionTier) -> float:
        """获取档位对应的止损百分比"""
        tier_cfg = self._tiers.get(tier.value.lower(), {})
        return tier_cfg.get("stop_loss_pct", 0.10)

    def get_tier_atr_n(self, tier: PositionTier) -> int:
        """获取档位对应的 Chandelier Exit N 值"""
        tier_cfg = self._tiers.get(tier.value.lower(), {})
        return tier_cfg.get("atr_n", 4)

    def check_upgrade(
        self,
        current_tier: PositionTier,
        data: StockData,
        strategy_state: StrategyState,
    ) -> PositionTier:
        """检查是否满足升级条件

        PILOT → BASE: 盈利>3% + MA多头 + 持有>5天
        BASE → FULL: 盈利>5% + MACD零轴上 + 持有>10天
        """
        upgrade_cfg = self._tiers.get("upgrade", {})
        profit_pct = strategy_state.unrealized_profit_pct
        days_held = strategy_state.days_held

        if current_tier == PositionTier.PILOT:
            cfg = upgrade_cfg.get("pilot_to_base", {})
            min_profit = cfg.get("min_profit_pct", 3.0)
            min_days = cfg.get("min_hold_days", 5)
            require_ma = cfg.get("require_ma_bullish", True)

            if profit_pct >= min_profit and days_held >= min_days:
                if not require_ma or self._is_ma_bullish(data):
                    logger.info(f"仓位升级: PILOT → BASE (盈利{profit_pct:.1f}%, 持有{days_held}天)")
                    return PositionTier.BASE

        elif current_tier == PositionTier.BASE:
            cfg = upgrade_cfg.get("base_to_full", {})
            min_profit = cfg.get("min_profit_pct", 5.0)
            min_days = cfg.get("min_hold_days", 10)
            require_macd = cfg.get("require_macd_above_zero", True)

            if profit_pct >= min_profit and days_held >= min_days:
                if not require_macd or self._is_macd_above_zero(data):
                    logger.info(f"仓位升级: BASE → FULL (盈利{profit_pct:.1f}%, 持有{days_held}天)")
                    return PositionTier.FULL

        return current_tier

    def check_downgrade(
        self,
        current_tier: PositionTier,
        data: StockData,
        strategy_state: StrategyState,
    ) -> PositionTier:
        """检查是否触发降级条件

        FULL → BASE: MA5<MA10 或 回撤>5%
        BASE → PILOT: MA10<MA20 或 回撤>8%
        """
        downgrade_cfg = self._tiers.get("downgrade", {})

        if current_tier == PositionTier.FULL:
            cfg = downgrade_cfg.get("full_to_base", {})
            if cfg.get("ma5_below_ma10") and self._is_ma5_below_ma10(data):
                logger.info(f"仓位降级: FULL → BASE (MA5<MA10)")
                return PositionTier.BASE
            max_dd = cfg.get("max_drawdown_pct", 5.0)
            if strategy_state.unrealized_profit_pct < -max_dd:
                logger.info(f"仓位降级: FULL → BASE (回撤>{max_dd}%)")
                return PositionTier.BASE

        elif current_tier == PositionTier.BASE:
            cfg = downgrade_cfg.get("base_to_pilot", {})
            if cfg.get("ma10_below_ma20") and self._is_ma10_below_ma20(data):
                logger.info(f"仓位降级: BASE → PILOT (MA10<MA20)")
                return PositionTier.PILOT
            max_dd = cfg.get("max_drawdown_pct", 8.0)
            if strategy_state.unrealized_profit_pct < -max_dd:
                logger.info(f"仓位降级: BASE → PILOT (回撤>{max_dd}%)")
                return PositionTier.PILOT

        return current_tier

    def get_take_profit_reduce_ratio(self, profit_pct: float) -> tuple[float, float]:
        """倒金字塔减仓：返回 (减仓比例, 保留比例)

        Returns:
            (reduce_ratio, keep_ratio)
        """
        tp_cfg = self._tiers.get("take_profit", {})
        tier1 = tp_cfg.get("tier1_profit_pct", 10)
        tier2 = tp_cfg.get("tier2_profit_pct", 20)

        if profit_pct >= tier2:
            reduce = tp_cfg.get("tier2_reduce_ratio", 0.30)
            keep = tp_cfg.get("trail_keep_ratio", 0.30)
            return reduce, keep
        elif profit_pct >= tier1:
            reduce = tp_cfg.get("tier1_reduce_ratio", 0.40)
            keep = 1.0 - reduce
            return reduce, keep
        return 0.0, 1.0

    # ===== 条件判断 =====

    @staticmethod
    def _is_ma_bullish(data: StockData) -> bool:
        return (
            data.ma5 is not None and data.ma20 is not None and data.ma60 is not None
            and data.ma5 > data.ma20 > data.ma60
        )

    @staticmethod
    def _is_macd_above_zero(data: StockData) -> bool:
        return data.macd_dif is not None and data.macd_dif > 0

    @staticmethod
    def _is_ma5_below_ma10(data: StockData) -> bool:
        return data.ma5 is not None and data.ma10 is not None and data.ma5 < data.ma10

    @staticmethod
    def _is_ma10_below_ma20(data: StockData) -> bool:
        return data.ma10 is not None and data.ma20 is not None and data.ma10 < data.ma20