"""执行资格：实时核验事实与回测撮合假设分开。

实时量比/近涨跌幅仅作估计；缺盘口、订单或可卖数量为UNKNOWN，保留动作意图。
确认停牌/数量受限才BLOCKED。回测沿用保守模型并明确标模拟假设，不写成交事实。
"""

import logging
import math
from src.core.decision_contract import ExecutionStatus
from src.core.source_time import parse_source_time
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
        execution_status=None,
        diagnostics=None,
        simulated: bool = False,
    ):
        self.original_action = original_action
        self.effective_action = effective_action
        self.blocked = blocked
        self.block_reason = block_reason
        self.slippage_pct = slippage_pct
        self.impact_cost_pct = impact_cost_pct
        self.total_cost_pct = total_cost_pct
        self.execution_status = execution_status or (ExecutionStatus.BLOCKED if blocked else ExecutionStatus.UNKNOWN)
        self.diagnostics = list(diagnostics or [])
        self.simulated = simulated


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
        *, simulated: bool = False, analysis_as_of: Optional[str] = None,
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

        if not simulated:
            return self._evaluate_live(data, position_action, current_volume_ratio, analysis_as_of)

        # 1. 流动性过滤（回测保守撮合假设，不是实时市场事实）
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
                    block_reason = "模拟成交假设：接近涨停阈值，本轮不撮合买入"

            if is_sell and self.constraint.limit_down_blocked:
                if self._is_limit_down(data):
                    blocked = True
                    block_reason = "模拟成交假设：接近跌停阈值，本轮不撮合卖出"

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
            execution_status=ExecutionStatus.BLOCKED if blocked else ExecutionStatus.ELIGIBLE,
            diagnostics=["模拟成交假设：量比/幅度阈值保守撮合"], simulated=True,
        )

    def _evaluate_live(self, data, action, volume_ratio, analysis_as_of):
        status, reason = ExecutionStatus.UNKNOWN, "盘口/订单数量/可卖数量待确认"
        diagnostics = []
        is_buy = action in (PositionAction.OPEN, PositionAction.ADD)
        is_sell = action in (PositionAction.REDUCE, PositionAction.CLOSE_ALL)
        if not is_buy and not is_sell:
            status, reason = ExecutionStatus.NOT_NEEDED, ""
        if volume_ratio is None and data.avg_volume_20 and data.avg_volume_20 > 0:
            volume_ratio = data.volume / data.avg_volume_20
        if volume_ratio is not None and math.isfinite(volume_ratio) and volume_ratio < self.constraint.min_volume_ratio:
            diagnostics.append("量比偏低：流动性估计，需确认盘口；不代表无法成交")
        if (is_buy and self._is_limit_up(data)) or (is_sell and self._is_limit_down(data)):
            diagnostics.append("接近涨跌幅阈值：需确认盘口；不代表已封板")
        facts = data.execution_facts or {}
        quote, cutoff = parse_source_time(facts.get("as_of")), parse_source_time(analysis_as_of)
        qualified = (facts.get("verified") is True and facts.get("source") in {"broker_snapshot", "exchange_status"}
                     and facts.get("security") == data.stock_code and quote is not None and cutoff is not None
                     and quote[0].date() == cutoff[0].date()
                     and (not cutoff[1] or not quote[1] or quote[0] <= cutoff[0]))
        if qualified and (is_buy or is_sell):
            if facts.get("suspended") is True:
                status, reason = ExecutionStatus.BLOCKED, "已核验停牌快照，当前订单受阻"
            elif is_sell and facts.get("sellable_qty") == 0 and type(facts.get("sellable_qty")) is int:
                status, reason = ExecutionStatus.BLOCKED, "已核验账户可卖数量为0"
            else:
                qty, available = facts.get("order_qty"), facts.get("executable_qty")
                market_stamp = parse_source_time(data.quote_as_of)
                if (facts.get("source") == "broker_snapshot" and facts.get("suspended") is False
                        and type(qty) is int and qty > 0 and type(available) is int and available >= qty
                        and (not is_sell or (type(facts.get("sellable_qty")) is int and facts["sellable_qty"] >= qty))
                        and math.isfinite(data.price) and data.price > 0
                        and market_stamp is not None and market_stamp[0].date() == cutoff[0].date()
                        and (not cutoff[1] or not market_stamp[1] or market_stamp[0] <= cutoff[0])):
                    status, reason = ExecutionStatus.ELIGIBLE, "核验快照具备订单资格；建议不等于已成交"
        if status == ExecutionStatus.UNKNOWN:
            diagnostics.append(reason)
        return ExecutionEvaluation(action, action, status == ExecutionStatus.BLOCKED, reason,
                                   self.constraint.slippage_pct, 0.0, self.constraint.slippage_pct,
                                   execution_status=status, diagnostics=diagnostics, simulated=False)

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
