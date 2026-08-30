"""执行层（Execution Layer） - v0.7.2 新增模块

将"理想交易"转为"可执行交易"。

核心约束：
1. 波动率滑点：不再使用固定值，滑点与波动率（ATR）正相关
2. 流动性过滤：低成交量禁止交易，避免"理论可买、实际买不到"
3. 涨跌停限制：涨停无法买入，跌停无法卖出（A股特性）
4. 冲击成本：大额交易影响价格，与成交量比例相关
"""

import logging
from typing import Optional

from src.data.models import (
    StockData, ExecutionConstraint, PositionAction, SignalType
)

logger = logging.getLogger(__name__)


class ExecutionEvaluation:
    """执行评估结果"""

    def __init__(
        self,
        original_action: PositionAction,
        effective_action: PositionAction,
        blocked: bool,
        block_reason: str,
        slippage_pct: float,
        impact_cost_pct: float,
        total_cost_pct: float,
    ):
        self.original_action = original_action
        self.effective_action = effective_action
        self.blocked = blocked
        self.block_reason = block_reason
        self.slippage_pct = slippage_pct
        self.impact_cost_pct = impact_cost_pct
        self.total_cost_pct = total_cost_pct


class ExecutionLayer:
    """执行层 - 现实约束建模

    输入：Strategy Layer的决策（理想交易）
    输出：经过现实约束过滤后的可执行交易
    """

    def __init__(self, constraint: Optional[ExecutionConstraint] = None):
        self.constraint = constraint or ExecutionConstraint()

    def evaluate(
        self,
        data: StockData,
        position_action: PositionAction,
        current_volume_ratio: Optional[float] = None,
    ) -> ExecutionEvaluation:
        """评估交易可执行性

        Args:
            data: 当前股票数据
            position_action: 仓位动作
            current_volume_ratio: 当日成交量/20日均量（预计算，避免重复计算）

        Returns:
            ExecutionEvaluation: 执行评估结果
        """
        is_buy = position_action in (PositionAction.OPEN, PositionAction.ADD)
        is_sell = position_action in (PositionAction.REDUCE, PositionAction.CLOSE_ALL)

        blocked = False
        block_reason = ""
        adjusted_slippage = self.constraint.slippage_pct
        impact_cost_pct = 0.0

        # 1. 流动性过滤
        if self.constraint.min_volume_ratio > 0:
            if current_volume_ratio is None:
                current_volume_ratio = self._calc_volume_ratio(data)
            if current_volume_ratio < self.constraint.min_volume_ratio:
                blocked = True
                block_reason = f"流动性不足(量比={current_volume_ratio:.2f} < {self.constraint.min_volume_ratio:.2f})"

        # 2. 涨跌停限制
        if not blocked:
            if is_buy and self.constraint.limit_up_blocked:
                if self._is_limit_up(data):
                    blocked = True
                    block_reason = "涨停封板，无法买入"

            if is_sell and self.constraint.limit_down_blocked:
                if self._is_limit_down(data):
                    blocked = True
                    block_reason = "跌停封板，无法卖出"

        # 3. 波动率滑点
        if not blocked and self.constraint.volatility_slippage:
            adjusted_slippage = self._calc_volatility_slippage(data)

        # 4. 冲击成本
        if not blocked and self.constraint.impact_cost_enabled:
            impact_cost_pct = self._calc_impact_cost(data, current_volume_ratio)

        # 如果被阻止，修正仓位动作
        effective_action = position_action
        if blocked:
            if is_buy:
                effective_action = PositionAction.STAY_OUT
            elif is_sell:
                # 卖出被阻止（跌停卖不出），维持当前仓位
                effective_action = PositionAction.HOLD_POSITION

        return ExecutionEvaluation(
            original_action=position_action,
            effective_action=effective_action,
            blocked=blocked,
            block_reason=block_reason,
            slippage_pct=adjusted_slippage,
            impact_cost_pct=impact_cost_pct,
            total_cost_pct=adjusted_slippage + impact_cost_pct,
        )

    @staticmethod
    def _calc_volume_ratio(data: StockData) -> float:
        """计算量比（当前成交量/20日均量）"""
        if data.avg_volume_20 is None or data.avg_volume_20 == 0:
            return 1.0  # 无数据时默认正常
        return data.volume / data.avg_volume_20

    @staticmethod
    def _limit_threshold(stock_code: str) -> float:
        """按板块取涨跌停判定阈值（v0.8.7.8 裁决修复 H01，H区块）。

        原实现 9.5% 一刀切：创业板/科创板 ±20% 空间内 +12%/-12% 的正常单日波动
        被误判封板（实测 300502 BUY 被封"涨停无法买入"、止损 SELL 被吞成 HOLD）。
        主板 10%、创业板/科创板 20%、北交所 30%，各留 0.5% 缓冲；
        ST ±5% 因无 ST 数据源判不到——维持原简化，在此声明。
        """
        code = str(stock_code or "").split(".")[-1]
        if code.startswith(("30", "68")):
            return 19.5   # 创业板/科创板 20cm
        if code.startswith(("43", "83", "87", "88", "92")):
            return 29.5   # 北交所 30cm
        return 9.5        # 主板（及未识别板块，保守）

    @classmethod
    def _is_limit_up(cls, data: StockData) -> bool:
        """判断是否涨停（H01：按板块分阈值）

        Args:
            data: 当前股票数据（含板块可判定的 stock_code）
        """
        if data.change_pct is None:
            return False
        return data.change_pct >= cls._limit_threshold(data.stock_code)

    @classmethod
    def _is_limit_down(cls, data: StockData) -> bool:
        """判断是否跌停（H01：按板块分阈值）"""
        if data.change_pct is None:
            return False
        return data.change_pct <= -cls._limit_threshold(data.stock_code)

    def _calc_volatility_slippage(self, data: StockData) -> float:
        """计算波动率相关滑点

        逻辑：
        - 基础滑点 = self.constraint.slippage_pct
        - 用60日区间的振幅近似波动率
        - 波动率越大，滑点越大（流动性越差）
        - 公式：slippage = base × (1 + volatility_factor)

        volatility_factor 估算：
        - 用 (high_60d - low_60d) / price 近似60日振幅
        - 振幅 < 10% → factor=0（低波动，基础滑点足够）
        - 振幅 10%-30% → factor=0.5（中等波动，滑点+50%）
        - 振幅 > 30% → factor=1.0（高波动，滑点×2）
        """
        if data.high_60d is None or data.low_60d is None or data.price <= 0:
            return self.constraint.slippage_pct

        amplitude = (data.high_60d - data.low_60d) / data.price

        if amplitude < 0.10:
            factor = 0.0
        elif amplitude < 0.30:
            factor = 0.5
        else:
            factor = 1.0

        return self.constraint.slippage_pct * (1 + factor)

    def _calc_impact_cost(
        self, data: StockData, volume_ratio: Optional[float] = None
    ) -> float:
        """计算冲击成本

        逻辑：
        - 大额交易占当日成交量比例越高，冲击成本越大
        - impact = base_rate × (trade_amount / daily_volume)
        - 简化：用量比反推，量比低=成交不活跃=冲击成本高
        """
        if volume_ratio is None:
            volume_ratio = self._calc_volume_ratio(data)

        if volume_ratio <= 0:
            return self.constraint.impact_cost_rate * 2  # 无量=高冲击

        # 量比越低，冲击成本越高
        if volume_ratio < 0.5:
            multiplier = 2.0
        elif volume_ratio < 1.0:
            multiplier = 1.5
        else:
            multiplier = 1.0

        return self.constraint.impact_cost_rate * multiplier
