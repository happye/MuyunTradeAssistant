"""PlanGuard — 交易计划守卫（v0.8.5 阶段 1.3）

核心价值：让前景良好的股票"持得住"，不被日常波动牵着走。

工作位置：在 Orchestrator 中插在 Strategy Layer 之后、Execution Layer 之前。
读 strategy_decision.sell_path + 当前 trade_plan，决定是否压制 SELL。

压制规则（v0.8.5: 只压 weak_sell；v0.8.6.3 气宗模式额外压 trend_exit）：

1. weak_sell（+气宗时 trend_exit）+ plan.outlook != bearish + 失效条件未触发
   → 压制 SELL → HOLD（写入 strategy_reasons）
   · mode=None/jianzong：仅压 weak_sell（原行为，破趋势线即走）
   · mode=qizong（气宗）：压 weak_sell + trend_exit（让牛股拿住主升浪，ISS-033）
2. stop_loss_* -> 不动（止损是安全网）；take_profit_trim 气宗在规则1压（ISS-033：止盈分档不减仓，让牛股拿住整波主升浪）
3. 时间止损：当前日期 - opened_at >= max_hold_days → 强制 EXIT
4. 价格穿透 current_stop（致命止损）→ 强制 STOP（不可压制）

设计原则：
- 不削弱现有安全网：致命止损不动（趋势退出/止盈分档 take_profit_trim 气宗在规则1压，ISS-033 让牛股拿住主升浪）
- 失效条件触发优先：plan.when_sell_invalidate 任一触发就放过 SELL
- 审计透明：每次压制写 reason 到 strategy_reasons，CLI 自然显示
"""

import logging
from datetime import datetime
from typing import Optional

from src.data.models import (
    SignalType, StrategyDecision, StockData, TradePlan, PositionAction
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

        # 规则 4：致命止损（最高优先级，不可压制）--穿 current_stop 强制全清
        # 审查修复 P0：execution_layer 只读 position_action（不读 decision/sell_path，无 STOP 概念），
        # 故必须设 position_action=CLOSE_ALL，否则 strategy_layer 输出 HOLD 时穿止损不卖（安全网静默失效）。
        if _check_stop_breach(trade_plan, data):
            if adjusted.decision != SignalType.SELL:
                reasons.insert(0, f"PlanGuard 致命止损: 价格 ¥{data.price:.2f} ≤ current_stop ¥{trade_plan.current_stop:.2f}")
                adjusted.decision = SignalType.SELL
            adjusted.position_action = PositionAction.CLOSE_ALL
            # v0.8.7.5 审计修复 A06：CLOSE_ALL 必须同步清零 position_ratio，
            # 否则 portfolio 取 decision.position_ratio(>0) 记成幽灵残留仓位
            adjusted.position_ratio = 0.0
            adjusted.sell_path = "stop_loss_exit"
            # ISS-068 小盘诊断修复：PlanGuard 致命止损的强制清仓此前不碰 strategy_state
            # 生命周期 -> 冷却从未启动 -> 4-6天后信号恢复直接重新买入 -> 高波动小盘股
            # '止损-回头-再被打' 循环6轮（柯力2024实测亏约40%份额，小盘组Δ-16.71pp
            # 的主病根）。strategy_layer 自发的 CLOSE_ALL 会走 _update_lifecycle 进
            # COOLDOWN(5天禁买)，PlanGuard 强制路径必须对齐同款防护。
            # 冷却长度用 strategy_layer 同款 COOLDOWN_AFTER_CLOSE_DAYS(=5)，且
            # sell_path=stop_loss_exit 已由本函数写入 state，_is_in_cooldown 的
            # 止损守卫（不吃极端缩短）自然生效。
            try:
                # ISS-068 A/B 口子：MUYUN_GUARD_STOP_COOLDOWN=0 时跳过冷却写入（对照实验用），
                # 默认（未设/任意其他值）写入。实盘与默认回测行为=修复后。
                import os as _os
                if _os.environ.get("MUYUN_GUARD_STOP_COOLDOWN") == "0":
                    raise RuntimeError("ab-off")
                ns = getattr(adjusted, "new_state", None)
                if ns is not None:
                    from src.core.strategy_layer import StrategyLayer
                    from src.data.models import TradeLifecycle
                    ns.lifecycle = TradeLifecycle.COOLDOWN
                    ns.cooldown_remaining = StrategyLayer.COOLDOWN_AFTER_CLOSE_DAYS
                    ns.cooldown_reason = "close_all"
                    ns.last_close_sell_path = "stop_loss_exit"
                    ns.current_position_ratio = 0.0
                    ns.min_hold_remaining = 0
                    ns.add_protection_remaining = 0
                    ns.reduce_protection_remaining = 0
            except Exception as e:
                logger.debug(f"PlanGuard 止损冷却写入失败(不阻断清仓): {e}")
            logger.warning(f"PlanGuard force STOP(CLOSE_ALL): price={data.price} <= stop={trade_plan.current_stop}")
            adjusted.strategy_reasons = reasons
            return adjusted

        # 规则 4.5：基本面恶化硬退出（ISS-053）--不可压制，仅次于致命止损，先于高位止盈
        # 持有期被 ST / 业绩预告预亏预减 -> 强制 SELL+CLOSE_ALL（暴雷比技术顶更急）
        # 独立 fundamental_alert 通道（非 top_signal），避暴雷被日志记成高位止盈污染复盘
        if adjusted.fundamental_alert:
            if adjusted.decision != SignalType.SELL:
                reasons.insert(0, f"PlanGuard 重大利空(不可压): {adjusted.fundamental_alert}")
                adjusted.decision = SignalType.SELL
            adjusted.position_action = PositionAction.CLOSE_ALL
            adjusted.position_ratio = 0.0  # A06 同步清零，防幽灵仓位
            adjusted.sell_path = "fundamental_alert"
            logger.warning(f"PlanGuard force EXIT by fundamental_alert: {adjusted.fundamental_alert}")
            adjusted.strategy_reasons = reasons
            return adjusted

        # 规则 P1：高位止盈3维度大顶信号（跳法A 阶段2）——仅次于致命止损，不可压制。
        # 即使气宗持有期内，宏观/个股大顶信号触发也强制离场（绕开"该不该卖"的 AI 判断）。
        if adjusted.top_signal:
            if adjusted.decision != SignalType.SELL:
                adjusted.decision = SignalType.SELL
            adjusted.position_action = PositionAction.CLOSE_ALL
            adjusted.position_ratio = 0.0  # A06 同步清零，防幽灵仓位
            adjusted.sell_path = "top_signal"
            reasons.insert(0, f"PlanGuard 高位止盈(不可压): {adjusted.top_signal}")
            logger.info(f"PlanGuard force EXIT by top_signal: {adjusted.top_signal}")
            adjusted.strategy_reasons = reasons
            return adjusted

        # 规则 3：时间止损 --到期强制全清（审查修复 P0：同规则4，必须设 position_action=CLOSE_ALL）
        if _check_max_hold_expired(trade_plan, today):
            days = _days_since(trade_plan.opened_at, today)
            if adjusted.decision != SignalType.SELL:
                reasons.insert(0, f"PlanGuard 时间止损: 已持有 {days} 天 ≥ max_hold_days {trade_plan.max_hold_days}")
                adjusted.decision = SignalType.SELL
            adjusted.position_action = PositionAction.CLOSE_ALL
            adjusted.position_ratio = 0.0  # A06 同步清零，防幽灵仓位
            adjusted.sell_path = "time_stop"
            logger.info(f"PlanGuard force EXIT(CLOSE_ALL): held {days} days >= max {trade_plan.max_hold_days}")
            adjusted.strategy_reasons = reasons
            return adjusted

        # 规则 1：weak_sell 压制（核心功能）
        # v0.8.6.3 气宗模式（ISS-033）：气宗额外压 trend_exit + take_profit_trim（让牛股拿住整波主升浪）
        #   剑宗/未设定：仅压 weak_sell（原行为）
        #   注：take_profit_trim 是减仓非清仓，气宗压制它=不减仓继续持有，符合"长期格局"
        is_qizong = (trade_plan.mode == "qizong")
        suppressible_paths = ("weak_sell",)
        if is_qizong:
            suppressible_paths = ("weak_sell", "trend_exit", "take_profit_trim")

        if adjusted.decision == SignalType.SELL and adjusted.sell_path in suppressible_paths:
            if trade_plan.fundamental_outlook == "bearish":
                # 前景已转空 → 不压制，让卖出执行
                reasons.insert(0, f"PlanGuard 不压制弱卖出: fundamental_outlook=bearish")
                adjusted.strategy_reasons = reasons
                return adjusted

            # v0.8.6.3 气宗：跳过 invalidation（回调时 MA60 跌破正是该拿住的时刻，
            # 仅致命止损+时间止损作安全网，已在规则4/3处理）
            if not is_qizong:
                invalidated, invalidate_cond = _check_invalidation_triggered(trade_plan, data)
                if invalidated:
                    reasons.insert(0, f"PlanGuard 不压制弱卖出: 失效条件已触发（{invalidate_cond}）")
                    adjusted.strategy_reasons = reasons
                    return adjusted

            # 计划未失效 + outlook 非 bearish → 压制
            outlook_label = trade_plan.fundamental_outlook
            thesis_short = (trade_plan.why_buy or "")[:40]
            mode_label = "气宗" if is_qizong else None
            mode_tag = f"[{mode_label}压制 {adjusted.sell_path}] " if mode_label else ""
            reasons.insert(0,
                f"PlanGuard: {mode_tag}计划未失效，压制弱卖出 "
                f"(outlook={outlook_label}, thesis: {thesis_short}{'...' if len(trade_plan.why_buy or '') > 40 else ''})"
            )
            adjusted.decision = SignalType.HOLD
            suppressed_path = adjusted.sell_path
            adjusted.sell_path = None  # 不再是卖出，清空 sell_path
            adjusted.position_action = PositionAction.HOLD_POSITION  # v0.8.6.3: 同步重置仓位动作，否则执行层仍按 REDUCE/CLOSE_ALL 卖出
            adjusted.position_ratio = 0.0  # HOLD 不增减仓
            logger.info(f"PlanGuard suppress {suppressed_path} (qizong={is_qizong}): outlook={outlook_label}")
            adjusted.strategy_reasons = reasons
            return adjusted

        # 规则 2：stop_loss_* / 其他 -> 不动（安全网保留）；take_profit_trim 气宗在规则1压（ISS-033）
        return adjusted
