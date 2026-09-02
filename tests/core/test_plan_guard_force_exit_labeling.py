"""ISS-078 PlanGuard 规则1 与 force_exit 标注组合的契约测试（2026-09-03）

锁死语义：
1. CLOSE_ALL + weak_sell 是 force_exit 残留的错误标注组合（Chandelier/趋势破坏
   强制清仓经 strategy_layer REDUCE 分支后 sell_path 残留 weak_sell）——
   规则1 不得把它压成 HOLD，否则技术安全网在有 TradePlan 时静默失效（剑宗也中招）。
2. REDUCE + weak_sell 是策略层正常弱卖出——规则1 照常压制（核心功能不回退）。

跑法：pytest tests/core/test_plan_guard_force_exit_labeling.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.plan_guard import PlanGuard
from src.data.models import (
    StrategyDecision, StrategyState, StockData, SignalType,
    PositionAction, TradeLifecycle, MarketState, TradePlan,
)


def _plan() -> TradePlan:
    return TradePlan(
        plan_id="600000_2024-01-01", stock_code="600000", stock_name="x",
        opened_at="2024-01-01", mode=None,  # None=剑宗口径（仅压 weak_sell）
        current_stop=10.0, why_buy="t", when_buy="t", how_much=0.2,
        when_sell_targets=[11.0], when_sell_invalidate=["t"],
        locked_initial_stop=10.0,
    )


def _data() -> StockData:
    return StockData(stock_code="600000", stock_name="x", price=10.5, volume=1000)


def _dec(position_action: PositionAction, sell_path: str) -> StrategyDecision:
    st = StrategyState(current_position_ratio=0.2)
    st.lifecycle = TradeLifecycle.HOLD
    return StrategyDecision(
        decision=SignalType.SELL, state=MarketState.TRANSITION, new_state=st,
        position_action=position_action, position_ratio=0.0, sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
    )


def test_rule1_never_suppresses_close_all_weak_sell():
    out = PlanGuard().evaluate(_dec(PositionAction.CLOSE_ALL, "weak_sell"),
                               _plan(), _data(), today="2024-02-01")
    assert out.decision == SignalType.SELL, \
        f"CLOSE_ALL+weak_sell 是强制清仓残留，不得压成 {out.decision.value}"
    assert out.position_action == PositionAction.CLOSE_ALL


def test_rule1_still_suppresses_reduce_weak_sell():
    out = PlanGuard().evaluate(_dec(PositionAction.REDUCE, "weak_sell"),
                               _plan(), _data(), today="2024-02-01")
    assert out.decision == SignalType.HOLD, "正常弱卖出压制（核心功能）不得回退"
    assert out.position_action == PositionAction.HOLD_POSITION
