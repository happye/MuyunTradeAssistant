"""PlanGuard — 交易计划守卫（v0.8.5 阶段 1.3）

核心价值：让前景良好的股票"持得住"，不被日常波动牵着走。

工作位置：在 Orchestrator 中插在 Strategy Layer 之后、Execution Layer 之前。
读 strategy_decision.sell_path + 当前 trade_plan，决定是否压制 SELL。

压制规则（按用户决策"只压 weak_sell"）：

1. weak_sell + plan.outlook != bearish + 失效条件未触发
   → 压制 SELL → HOLD（写入 strategy_reasons "PlanGuard: 计划未失效，压制弱卖出"）
2. take_profit_trim / stop_loss_* / trend_exit
   → 不动（安全网保留）
3. 时间止损：当前日期 - opened_at >= max_hold_days
   → 强制 EXIT（覆盖 HOLD/BUY，但 SELL 已经在卖就不动）
4. 价格穿透 current_stop（致命止损）
   → 强制 STOP（不可压制）

设计原则：
- 不削弱现有安全网：致命止损/趋势退出/止盈分档不动
- 失效条件触发优先：plan.when_sell_invalidate 任一触发就放过 SELL
- 审计透明：每次压制写 reason 到 strategy_reasons，CLI 自然显示
"""

import logging
from datetime import datetime
from typing import Optional

from src.data.models import (
    SignalType, StrategyDecision, StockData, TradePlan
)

logger = logging.getLogger(__name__)


def _days_since(start_date: str, today: Optional[str] = None) -> int:
    """计算从 start_date 到 today 的自然日数（容错：日期解析失败返回 0）"""
    if not start_date:
        return 0
    try:
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(today, "%Y-%m-%d") if today else datetime.now()
        return (d2 - d1).days
    except (ValueError, TypeError):
        return 0


def _check_invalidation_triggered(plan: TradePlan, data: StockData) -> tuple[bool, Optional[str]]:
    """检查计划失效条件是否触发（基于规则的轻量判定）。

    AI 辅助判定留到阶段 1.4 与 adjuster 一起做。本阶段先做技术面规则判定：
    - "跌破 MA60" 类条件 → 看 data.ma60 与 data.price
    - "MA20 死叉 MA60" → 看 ma20 < ma60
    - "高点回撤 >X%" → 暂不实现（需要 high_since_entry 历史）
    - "重大利空 / 财务造假" → 暂不实现（需要新闻面，留到 1.4）

    Returns:
        (是否触发, 触发的具体条件文本)
    """
    if not plan.when_sell_invalidate:
        return False, None

    for cond in plan.when_sell_invalidate:
        # 跌破 MA60 类
        if "MA60" in cond and "跌破" in cond:
            if data.ma60 is not None and data.price < data.ma60:
                return True, cond
        # MA20 死叉 MA60
        if "MA20" in cond and ("死叉" in cond or "下穿" in cond):
            if data.ma20 is not None and data.ma60 is not None and data.ma20 < data.ma60:
                return True, cond
        # 跌破止损（致命）单独由 _check_stop_breach 处理，不在这里
        # 其他条件（重大利空/财务造假）需要新闻面，本阶段不判定

    return False, None


def _check_stop_breach(plan: TradePlan, data: StockData) -> bool:
    """检查价格是否穿透 current_stop（致命止损）"""
    if plan.current_stop is None or plan.current_stop <= 0:
        return False
    return data.price <= plan.current_stop


def _check_max_hold_expired(plan: TradePlan, today: Optional[str] = None) -> bool:
    """检查是否超过最长持有天数"""
    if not plan.opened_at or plan.max_hold_days <= 0:
        return False
    days = _days_since(plan.opened_at, today)
    return days >= plan.max_hold_days


class PlanGuard:
    """计划守卫 — 在 Strategy Layer 之后调用，根据 TradePlan 调整 strategy_decision

    使用：
        guard = PlanGuard()
        adjusted = guard.evaluate(strategy_decision, plan, data, today)

    plan 为 None 时不做任何动作（向后兼容旧 portfolio.yaml）。
    """

    def evaluate(
        self,
        strategy_decision: StrategyDecision,
        trade_plan: Optional[TradePlan],
        data: StockData,
        today: Optional[str] = None,
    ) -> StrategyDecision:
        """根据 plan 评估并可能调整 strategy_decision

        返回一个新的 StrategyDecision（不修改入参 — 防止下游误用旧值）。
        """
        if trade_plan is None:
            return strategy_decision  # 无计划，直通

        # 用浅拷贝避免修改原对象 — Pydantic v2 推荐 model_copy
        adjusted = strategy_decision.model_copy(deep=True)
        reasons: list[str] = list(adjusted.strategy_reasons or [])

        # 规则 4：致命止损（最高优先级，不可压制）
        if _check_stop_breach(trade_plan, data):
            if adjusted.decision != SignalType.SELL:
                reasons.insert(0, f"PlanGuard 致命止损: 价格 ¥{data.price:.2f} ≤ current_stop ¥{trade_plan.current_stop:.2f}")
                adjusted.decision = SignalType.SELL
                # 不改 sell_path / position_action（让 execution_layer 按 STOP 处理）
                logger.warning(f"PlanGuard force STOP: price={data.price} <= stop={trade_plan.current_stop}")
            adjusted.strategy_reasons = reasons
            return adjusted

        # 规则 3：时间止损
        if _check_max_hold_expired(trade_plan, today):
            if adjusted.decision != SignalType.SELL:
                days = _days_since(trade_plan.opened_at, today)
                reasons.insert(0, f"PlanGuard 时间止损: 已持有 {days} 天 ≥ max_hold_days {trade_plan.max_hold_days}")
                adjusted.decision = SignalType.SELL
                logger.info(f"PlanGuard force EXIT: held {days} days >= max {trade_plan.max_hold_days}")
            adjusted.strategy_reasons = reasons
            return adjusted

        # 规则 1：weak_sell 压制（核心功能）
        if adjusted.decision == SignalType.SELL and adjusted.sell_path == "weak_sell":
            if trade_plan.fundamental_outlook == "bearish":
                # 前景已转空 → 不压制，让卖出执行
                reasons.insert(0, f"PlanGuard 不压制弱卖出: fundamental_outlook=bearish")
                adjusted.strategy_reasons = reasons
                return adjusted

            invalidated, invalidate_cond = _check_invalidation_triggered(trade_plan, data)
            if invalidated:
                reasons.insert(0, f"PlanGuard 不压制弱卖出: 失效条件已触发（{invalidate_cond}）")
                adjusted.strategy_reasons = reasons
                return adjusted

            # 计划未失效 + outlook 非 bearish → 压制
            outlook_label = trade_plan.fundamental_outlook
            thesis_short = (trade_plan.why_buy or "")[:40]
            reasons.insert(0,
                f"PlanGuard: 计划未失效，压制弱卖出 "
                f"(outlook={outlook_label}, thesis: {thesis_short}{'...' if len(trade_plan.why_buy or '') > 40 else ''})"
            )
            adjusted.decision = SignalType.HOLD
            adjusted.sell_path = None  # 不再是卖出，清空 sell_path
            logger.info(f"PlanGuard suppress weak_sell: outlook={outlook_label}")
            adjusted.strategy_reasons = reasons
            return adjusted

        # 规则 2：take_profit_trim / stop_loss_* / trend_exit / 其他 → 不动（安全网保留）
        return adjusted
