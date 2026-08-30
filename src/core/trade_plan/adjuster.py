"""TradePlan 动态调整器（v0.8.5 阶段 1.4）

每天跑 scan 时调用，输出"建议调整止损/持有天数"。

设计：规则版必有，AI 增强可选。

规则版触发器：
1. **trailing stop 上移**（auto_trailing）：
   high_since_entry > entry × (1 + 5%) AND
   plan.current_stop < high_since_entry - 2×ATR
   → 建议把 current_stop 上移到 high_since_entry - 2×ATR
   依据：策略库 ch48「止损只上移不下移」+ Chandelier Exit
2. **达到止盈分档**（target_reached）：
   price >= when_sell_targets[i]
   → 提示用户当前已到止盈目标
3. **接近最长持有期**（max_hold_warning）：
   剩余 < 7 天 → 警告
4. **失效条件触发**（invalidate_triggered）：
   规则版能判定的失效条件（跌破 MA60 / MA20 死叉）已触发
   → 提示用户"thesis 已失效，建议立即评估卖出"

AI 增强（留接口，本阶段先用规则版兜底）：
- 读 7 天新闻 → 建议 outlook 升级/降级
- 读 7 天新闻 + 失效条件文本 → AI 判断是否触发"重大利空/财务造假"

Adjustment 数据结构：
{
  "trigger": "auto_trailing" / "target_reached" / "max_hold_warning" / "invalidate_triggered" / "ai_outlook_change",
  "field": "current_stop" / "max_hold_days" / "fundamental_outlook" / "_alert",
  "old": ...,
  "new": ...,
  "reason": "人话理由",
  "source": "auto_trailing" / "ai_suggested" / "rule_based",
  "confidence": 0-1,
  "actionable": True/False  # 用户是否需要 Y/n 确认
}
"""

import logging
from datetime import datetime
from typing import Optional

from src.data.models import StockData, TradePlan

logger = logging.getLogger(__name__)


# Trailing 触发的最低盈利门槛（avoid 噪音）
TRAILING_TRIGGER_MIN_GAIN_PCT = 0.05  # 浮盈 5% 才开始 trailing
TRAILING_ATR_MULTIPLIER = 2.0          # 与 generator.ATR_MULTIPLIER 对齐
MAX_HOLD_WARNING_DAYS = 7              # 剩 7 天给警告


def _days_since(start_date: str, today: Optional[str] = None) -> int:
    if not start_date:
        return 0
    try:
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(today, "%Y-%m-%d") if today else datetime.now()
        return (d2 - d1).days
    except (ValueError, TypeError):
        return 0


def _check_trailing(
    plan: TradePlan,
    data: StockData,
    high_since_entry: Optional[float],
) -> Optional[dict]:
    """检查是否需要上移止损（trailing stop）"""
    if high_since_entry is None or high_since_entry <= 0:
        # 兜底：用当前价当 high
        high_since_entry = data.high or data.price
    if not data.atr_14 or data.atr_14 <= 0:
        return None  # 无 ATR 不做建议

    # 浮盈门槛
    # v0.8.7.8 裁决修复 H02：优先用 plan.entry_price（真入场价）。
    # 原反推链（locked_initial_stop/0.92 或 when_sell_targets[0]/1.10）在
    # ATR 止损 / 用户改过止盈分档时严重失真 → 5% 浮盈门槛错位。
    if plan.entry_price and plan.entry_price > 0:
        entry = plan.entry_price
    elif plan.when_sell_targets:
        entry = plan.when_sell_targets[0] / 1.10
    else:
        entry = plan.locked_initial_stop / 0.92
    gain_pct = (high_since_entry - entry) / entry if entry > 0 else 0
    if gain_pct < TRAILING_TRIGGER_MIN_GAIN_PCT:
        return None

    # 计算建议的新止损
    new_stop = round(high_since_entry - TRAILING_ATR_MULTIPLIER * data.atr_14, 2)

    # 单向上移：只有更高时才建议
    if new_stop <= plan.current_stop:
        return None

    return {
        "trigger": "auto_trailing",
        "field": "current_stop",
        "old": plan.current_stop,
        "new": new_stop,
        "reason": (
            f"持仓最高 ¥{high_since_entry:.2f}（盈利 {gain_pct*100:.1f}%），"
            f"按 2×ATR(14)={data.atr_14:.2f} 上移止损至 ¥{new_stop:.2f}（原 ¥{plan.current_stop:.2f}）"
        ),
        "source": "auto_trailing",
        "confidence": 0.9,  # 数学公式高置信
        "actionable": True,
    }


def _check_target_reached(plan: TradePlan, data: StockData) -> Optional[dict]:
    """检查是否到了止盈分档目标价"""
    if not plan.when_sell_targets:
        return None
    for i, target in enumerate(plan.when_sell_targets):
        if data.price >= target:
            return {
                "trigger": "target_reached",
                "field": "_alert",
                "old": None,
                "new": target,
                "reason": f"价格 ¥{data.price:.2f} 已达止盈分档 T{i+1} (¥{target:.2f})，建议按计划分批落袋",
                "source": "rule_based",
                "confidence": 1.0,
                "actionable": False,  # 提示性质，不改字段
            }
    return None


def _check_max_hold_warning(plan: TradePlan, today: Optional[str] = None) -> Optional[dict]:
    """检查是否接近最长持有期"""
    if not plan.opened_at or plan.max_hold_days <= 0:
        return None
    days_held = _days_since(plan.opened_at, today)
    days_remaining = plan.max_hold_days - days_held
    if 0 < days_remaining <= MAX_HOLD_WARNING_DAYS:
        return {
            "trigger": "max_hold_warning",
            "field": "_alert",
            "old": days_held,
            "new": plan.max_hold_days,
            "reason": (
                f"已持有 {days_held} 天，距 max_hold_days {plan.max_hold_days} 仅剩 {days_remaining} 天。"
                f"若持仓表现良好可考虑延长 max_hold_days；否则按计划逐步退出"
            ),
            "source": "rule_based",
            "confidence": 1.0,
            "actionable": False,
        }
    return None


def _check_invalidate_triggered(plan: TradePlan, data: StockData) -> Optional[dict]:
    """检查规则版可判定的失效条件是否触发（与 plan_guard 对齐）"""
    if not plan.when_sell_invalidate:
        return None
    for cond in plan.when_sell_invalidate:
        if "MA60" in cond and "跌破" in cond:
            if data.ma60 is not None and data.price < data.ma60:
                return {
                    "trigger": "invalidate_triggered",
                    "field": "_alert",
                    "old": None,
                    "new": cond,
                    "reason": f"失效条件「{cond}」已触发：价格 ¥{data.price:.2f} 跌破 MA60 ¥{data.ma60:.2f}。建议立即评估卖出",
                    "source": "rule_based",
                    "confidence": 1.0,
                    "actionable": False,
                }
        if "MA20" in cond and ("死叉" in cond or "下穿" in cond):
            if data.ma20 is not None and data.ma60 is not None and data.ma20 < data.ma60:
                return {
                    "trigger": "invalidate_triggered",
                    "field": "_alert",
                    "old": None,
                    "new": cond,
                    "reason": f"失效条件「{cond}」已触发：MA20({data.ma20:.2f}) 死叉 MA60({data.ma60:.2f})",
                    "source": "rule_based",
                    "confidence": 1.0,
                    "actionable": False,
                }
    return None


class TradePlanAdjuster:
    """计划调整器（v0.8.5 阶段 1.4）

    使用：
        adj = TradePlanAdjuster()
        suggestions = adj.suggest(plan, data, high_since_entry, today)
        for s in suggestions:
            print(s["reason"])

    AI 增强：传入 ai_modifier 后会在新闻面变化时给出 outlook 升降建议。
    """

    def __init__(self, ai_modifier=None):
        self.ai = ai_modifier  # 留接口，本阶段先不用

    def suggest(
        self,
        plan: TradePlan,
        data: StockData,
        high_since_entry: Optional[float] = None,
        today: Optional[str] = None,
    ) -> list[dict]:
        """生成调整建议列表（按优先级排序）"""
        suggestions = []

        # 优先级 1：失效条件触发（最高警觉）
        sug = _check_invalidate_triggered(plan, data)
        if sug:
            suggestions.append(sug)

        # 优先级 2：达到止盈分档
        sug = _check_target_reached(plan, data)
        if sug:
            suggestions.append(sug)

        # 优先级 3：接近最长持有期
        sug = _check_max_hold_warning(plan, today)
        if sug:
            suggestions.append(sug)

        # 优先级 4：trailing stop 上移
        sug = _check_trailing(plan, data, high_since_entry)
        if sug:
            suggestions.append(sug)

        # AI 增强（留接口）
        if self.ai is not None:
            try:
                # TODO 阶段 1.4 增量：调 AI 判断 outlook 是否需要升降级
                pass
            except Exception as e:
                logger.warning(f"AI 调整建议生成失败: {e}")

        return suggestions

    def apply(self, plan: TradePlan, suggestion: dict, today: Optional[str] = None) -> TradePlan:
        """应用一条调整建议（用户 Y 确认后调用）

        返回更新后的 plan（不修改原对象）。
        """
        new_plan = plan.model_copy(deep=True)
        today = today or datetime.now().strftime("%Y-%m-%d")
        field = suggestion.get("field")

        if field == "current_stop":
            new_plan.current_stop = suggestion["new"]
        elif field == "max_hold_days":
            new_plan.max_hold_days = suggestion["new"]
        elif field == "fundamental_outlook":
            new_plan.fundamental_outlook = suggestion["new"]
        # _alert 类型不修改字段，仅作为提示

        # 写入 adjustments 审计
        new_plan.adjustments.append({
            "date": today,
            "field": field,
            "old": suggestion.get("old"),
            "new": suggestion.get("new"),
            "reason": suggestion.get("reason"),
            "source": "user",  # 用户确认后才 apply，所以 source=user
            "trigger": suggestion.get("trigger"),  # 保留原触发器
        })
        return new_plan
