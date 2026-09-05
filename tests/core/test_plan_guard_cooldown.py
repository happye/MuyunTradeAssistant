"""ISS-068 PlanGuard 止损冷却写入回归测试（2026-08-29 补欠账）

锁死语义：
1. 规则4（穿 current_stop）触发强制清仓时，new_state 必须进入 COOLDOWN 且
   cooldown_reason="close_all"、last_close_sell_path="stop_loss_exit"
   （修复前：完全不碰生命周期，4-6 天后无阻拦重新买入--小盘循环接刀病根）
2. 止损类清仓的冷却不吃极端缩短（strategy_layer._is_in_cooldown 守卫）
3. A/B 开关 MUYUN_GUARD_STOP_COOLDOWN=0 关闭写入（对照实验口子，默认开启）

跑法：pytest tests/core/test_plan_guard_cooldown.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import unittest.mock as mock

from src.core.plan_guard import PlanGuard
from src.data.models import StrategyDecision, StrategyState, StockData, SignalType, PositionAction, TradeLifecycle, TradePlan
from src.core.strategy_layer import StrategyLayer


def _plan(stop: float = 10.0) -> TradePlan:
    return TradePlan(
        plan_id="600000_2024-01-01", stock_code="600000", stock_name="x",
        opened_at="2024-01-01", mode=None, current_stop=stop,
        why_buy="t", when_buy="t", how_much=0.2,
        when_sell_targets=[11.0], when_sell_invalidate=["t"],
        locked_initial_stop=stop,
    )


def _sd(price: float = 9.0) -> StockData:
    return StockData(stock_code="600000", stock_name="x", price=price, volume=1000)


def _dec() -> StrategyDecision:
    st = StrategyState()
    st.lifecycle = TradeLifecycle.HOLD
    from src.data.models import MarketState
    return StrategyDecision(
        decision=SignalType.HOLD, state=MarketState.TRANSITION, new_state=st,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
    )


def test_rule4_writes_cooldown():
    g = PlanGuard()
    d = _dec()
    out = g.evaluate(d, _plan(stop=10.0), _sd(price=9.0), today="2024-06-01")
    assert out.decision == SignalType.SELL
    assert out.position_action == PositionAction.CLOSE_ALL
    ns = out.new_state
    assert ns.lifecycle == TradeLifecycle.COOLDOWN, "止损清仓必须进入冷却（ISS-068 病根）"
    assert ns.cooldown_reason == "close_all"
    assert ns.cooldown_remaining == StrategyLayer.COOLDOWN_AFTER_CLOSE_DAYS
    assert (ns.last_close_sell_path or "").startswith("stop_loss")
    print("PASS 规则4止损清仓写入冷却")


def test_rule4_ab_switch_off():
    os.environ["MUYUN_GUARD_STOP_COOLDOWN"] = "0"
    try:
        g = PlanGuard()
        out = g.evaluate(_dec(), _plan(stop=10.0), _sd(price=9.0), today="2024-06-01")
        # 开关关闭：行为回到修复前（不写冷却），但清仓决策本身不变
        assert out.decision == SignalType.SELL and out.position_action == PositionAction.CLOSE_ALL
        assert out.new_state.lifecycle == TradeLifecycle.HOLD, "AB关闭时不写冷却"
    finally:
        os.environ.pop("MUYUN_GUARD_STOP_COOLDOWN", None)
    print("PASS A/B开关关闭=修复前行为")


def test_stop_close_cooldown_ignores_extreme():
    sl = StrategyLayer()
    st = StrategyState()
    st.cooldown_remaining = 2
    st.cooldown_reason = "close_all"
    st.last_close_sell_path = "stop_loss_exit"
    assert sl._is_in_cooldown(st, SignalType.BUY, is_extreme=True) is True, "止损冷却极端模式也拦"
    st2 = StrategyState()
    st2.cooldown_remaining = 2
    st2.cooldown_reason = "close_all"
    st2.last_close_sell_path = "trend_exit"
    assert sl._is_in_cooldown(st2, SignalType.BUY, is_extreme=True) is False, "非止损冷却吃极端缩短"
    print("PASS 止损冷却不吃极端缩短")


def test_no_breach_no_cooldown_write():
    # v0.8.8.6 契约更新：time_stop（规则3）现在也写冷却——原场景 today=2024-06-01
    # 同时触发时间止损（152天≥90），会误撞新冷却写入。改到 90 天内的日期，
    # 保证四条强制清仓规则（4/4.5/P1/3）全不触发，才是本测试要锁的"无规则命中"。
    g = PlanGuard()
    out = g.evaluate(_dec(), _plan(stop=10.0), _sd(price=12.0), today="2024-03-01")
    assert out.new_state.lifecycle == TradeLifecycle.HOLD, "四条强制规则均未触发时不改生命周期"
    print("PASS 未穿止损不写冷却")


if __name__ == "__main__":
    test_rule4_writes_cooldown()
    test_rule4_ab_switch_off()
    test_stop_close_cooldown_ignores_extreme()
    test_no_breach_no_cooldown_write()
    print("\n4/4 全部通过")
