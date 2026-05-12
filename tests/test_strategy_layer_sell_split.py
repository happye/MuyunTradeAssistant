# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.strategy_layer import StrategyLayer
from src.data.models import (
    DecisionResult,
    DecisionTrace,
    MarketState,
    PositionAction,
    SignalType,
    SkillSignal,
    StockData,
    StrategyState,
    TradeLifecycle,
)


def _make_stock(price: float = 10.0) -> StockData:
    return StockData(
        stock_code="000001",
        stock_name="test",
        price=price,
        volume=1000000,
        ma20=10.5,
        ma60=11.0,
    )


def _make_signal(skill_name: str, confidence: float) -> SkillSignal:
    return SkillSignal(
        skill_name=skill_name,
        skill_alias=skill_name,
        signal=SignalType.SELL,
        confidence=confidence,
        reason=[skill_name],
        skill_type="action",
    )


def _make_decision_result(
    *,
    decision: SignalType = SignalType.SELL,
    buy_score: float = 0.1,
    sell_score: float = 0.4,
    price: float = 10.0,
    ma20: float = 10.5,
    ma60: float = 11.0,
    signals: list[SkillSignal] | None = None,
) -> DecisionResult:
    return DecisionResult(
        stock=StockData(
            stock_code="000001",
            stock_name="test",
            price=price,
            volume=1000000,
            ma20=ma20,
            ma60=ma60,
        ),
        state=MarketState.TRANSITION,
        decision=decision,
        score=max(buy_score, sell_score),
        signals=signals or [],
        trace=[
            DecisionTrace(
                step="市场状态影响",
                description="test scores",
                data={"final_scores": {"BUY": buy_score, "SELL": sell_score}},
            )
        ],
    )


def _make_state(
    position_ratio: float,
    lifecycle: TradeLifecycle = TradeLifecycle.HOLD,
    entry_price: float | None = 10.0,
) -> StrategyState:
    return StrategyState(
        current_position_ratio=position_ratio,
        lifecycle=lifecycle,
        entry_price=entry_price,
        last_decision=SignalType.HOLD if position_ratio > 0 else SignalType.WATCH,
    )


def test_sell_signal_stays_out_when_flat():
    layer = StrategyLayer()
    decision_result = _make_decision_result(sell_score=0.8)

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.0, TradeLifecycle.FLAT),
    )

    assert action == PositionAction.STAY_OUT
    assert ratio == 0.0


def test_stop_loss_sell_closes_all_when_strong():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        sell_score=0.8,
        price=9.0,
        signals=[_make_signal("stop_loss", 0.9)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.CLOSE_ALL
    assert ratio == 0.0


def test_stop_loss_sell_reduces_when_not_deep():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        sell_score=0.45,
        price=9.7,
        signals=[_make_signal("stop_loss", 0.78)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.4 * layer.NORMAL_REDUCE_KEEP


def test_stop_loss_signal_with_fresh_trend_break_exits_even_when_profitable():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=10.8,
        ma20=11.0,
        ma60=11.2,
        signals=[_make_signal("stop_loss", 0.78)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.CLOSE_ALL
    assert ratio == 0.0


def test_stop_loss_signal_without_actual_loss_falls_back_to_weak_sell():
    layer = StrategyLayer()
    state = _make_state(0.4, entry_price=10.0)
    decision_result = _make_decision_result(
        sell_score=0.45,
        price=10.3,
        signals=[_make_signal("stop_loss", 0.78)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        state,
    )
    sell_path = layer._infer_sell_path(
        SignalType.SELL,
        action,
        decision_result,
        state,
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.4 * layer.NORMAL_REDUCE_KEEP
    assert sell_path == "weak_sell"


def test_take_profit_sell_reduces_position():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        sell_score=0.45,
        price=11.0,
        signals=[_make_signal("take_profit", 0.8)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.4 * layer.TAKE_PROFIT_KEEP


def test_take_profit_with_fresh_trend_break_exits_instead_of_trimming():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=10.8,
        ma20=11.0,
        ma60=11.2,
        signals=[_make_signal("take_profit", 0.8)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.CLOSE_ALL
    assert ratio == 0.0


def test_strong_sell_dominance_with_trend_break_closes_all():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=9.8,
        ma20=10.2,
        ma60=10.5,
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.CLOSE_ALL
    assert ratio == 0.0


def test_profitable_ma60_break_without_trend_rollover_only_reduces():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=10.8,
        ma20=11.3,
        ma60=11.0,
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.4 * layer.NORMAL_REDUCE_KEEP


def test_strong_sell_without_trend_break_only_reduces():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=10.8,
        ma20=10.5,
        ma60=10.2,
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.4 * layer.NORMAL_REDUCE_KEEP


def test_exit_lifecycle_without_fresh_trend_break_does_not_force_close_all():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=10.8,
        ma20=10.5,
        ma60=10.2,
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, lifecycle=TradeLifecycle.EXIT, entry_price=10.0),
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.4 * layer.NORMAL_REDUCE_KEEP


def test_trend_exit_reason_mentions_trend_break_fact():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=9.8,
        ma20=10.2,
        ma60=10.5,
    )

    reason = layer._describe_position_action(
        SignalType.SELL,
        PositionAction.CLOSE_ALL,
        "trend_exit",
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert reason is not None
    assert "跌破MA60" in reason


def test_weak_sell_reason_mentions_trend_not_broken():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=10.8,
        ma20=10.5,
        ma60=10.2,
    )

    reason = layer._describe_position_action(
        SignalType.SELL,
        PositionAction.REDUCE,
        "weak_sell",
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert reason == "弱卖出: 卖压存在但趋势未破坏，先减仓观察"


def test_weak_sell_only_reduces_position():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        buy_score=0.25,
        sell_score=0.4,
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.4 * layer.NORMAL_REDUCE_KEEP


def test_take_profit_trim_sets_reason_specific_reduce_protection():
    layer = StrategyLayer()
    new_state = layer._update_lifecycle(
        _make_state(0.4, entry_price=10.0),
        SignalType.SELL,
        PositionAction.REDUCE,
        _make_stock(11.0),
        0.4 * layer.TAKE_PROFIT_KEEP,
        "take_profit_trim",
    )

    assert new_state.lifecycle == TradeLifecycle.HOLD
    assert new_state.last_reduce_reason == "take_profit_trim"
    assert new_state.reduce_protection_remaining == 10


def test_weak_sell_sets_longer_reduce_protection():
    layer = StrategyLayer()
    new_state = layer._update_lifecycle(
        _make_state(0.4, entry_price=10.0),
        SignalType.SELL,
        PositionAction.REDUCE,
        _make_stock(10.0),
        0.4 * layer.NORMAL_REDUCE_KEEP,
        "weak_sell",
    )

    assert new_state.last_reduce_reason == "weak_sell"
    assert new_state.reduce_protection_remaining == 15


def test_add_sets_add_protection_window():
    layer = StrategyLayer()
    new_state = layer._update_lifecycle(
        _make_state(0.2, entry_price=10.0),
        SignalType.BUY,
        PositionAction.ADD,
        _make_stock(10.5),
        0.4,
    )

    assert new_state.add_protection_remaining == layer.ADD_PROTECTION_DAYS


def test_open_sets_min_hold_window():
    layer = StrategyLayer()
    new_state = layer._update_lifecycle(
        _make_state(0.0, lifecycle=TradeLifecycle.FLAT, entry_price=None),
        SignalType.BUY,
        PositionAction.OPEN,
        _make_stock(10.0),
        0.2,
    )

    assert new_state.min_hold_remaining == layer.MIN_HOLD_DAYS


def test_stop_loss_trim_sets_reason_specific_reduce_protection():
    layer = StrategyLayer()
    new_state = layer._update_lifecycle(
        _make_state(0.4, entry_price=10.0),
        SignalType.SELL,
        PositionAction.REDUCE,
        _make_stock(9.7),
        0.4 * layer.NORMAL_REDUCE_KEEP,
        "stop_loss_trim",
    )

    assert new_state.lifecycle == TradeLifecycle.HOLD
    assert new_state.last_reduce_reason == "stop_loss_trim"
    assert new_state.reduce_protection_remaining == 15


def test_exit_lifecycle_reduce_normalizes_back_to_hold():
    layer = StrategyLayer()
    new_state = layer._update_lifecycle(
        _make_state(0.4, lifecycle=TradeLifecycle.EXIT, entry_price=10.0),
        SignalType.SELL,
        PositionAction.REDUCE,
        _make_stock(10.0),
        0.4 * layer.NORMAL_REDUCE_KEEP,
        "weak_sell",
    )

    assert new_state.lifecycle == TradeLifecycle.HOLD
    assert new_state.last_reduce_reason == "weak_sell"
    assert new_state.reduce_protection_remaining == 15


def test_same_reduce_reason_is_blocked_during_protection_window():
    layer = StrategyLayer()
    protected_state = _make_state(0.4, entry_price=10.0)
    protected_state.last_decision = SignalType.SELL
    protected_state.inertia_counter = layer.INERTIA_MIN_DAYS
    protected_state.recent_signals = [SignalType.SELL.value, SignalType.SELL.value]
    protected_state.last_reduce_reason = "weak_sell"
    protected_state.reduce_protection_remaining = 5

    decision_result = _make_decision_result(
        buy_score=0.25,
        sell_score=0.4,
    )

    result = layer.process(
        decision_result,
        protected_state,
        decision_result.stock,
    )

    assert result.position_action == PositionAction.HOLD_POSITION
    assert result.sell_path is None
    assert "减仓保护期: 同一减仓原因未重复执行" in result.strategy_reasons


def test_different_reduce_reason_can_still_execute_during_protection_window():
    layer = StrategyLayer()
    protected_state = _make_state(0.4, entry_price=10.0)
    protected_state.last_decision = SignalType.SELL
    protected_state.inertia_counter = layer.INERTIA_MIN_DAYS
    protected_state.recent_signals = [SignalType.SELL.value, SignalType.SELL.value]
    protected_state.last_reduce_reason = "weak_sell"
    protected_state.reduce_protection_remaining = 5

    decision_result = _make_decision_result(
        sell_score=0.45,
        price=11.0,
        signals=[_make_signal("take_profit", 0.8)],
    )

    result = layer.process(
        decision_result,
        protected_state,
        decision_result.stock,
    )

    assert result.position_action == PositionAction.REDUCE
    assert result.position_ratio == 0.4 * layer.TAKE_PROFIT_KEEP
    assert result.sell_path == "take_profit_trim"


def test_add_protection_blocks_weak_sell_trim_after_recent_add():
    layer = StrategyLayer()
    protected_state = _make_state(0.4, entry_price=10.0)
    protected_state.last_decision = SignalType.SELL
    protected_state.inertia_counter = layer.INERTIA_MIN_DAYS
    protected_state.recent_signals = [SignalType.SELL.value, SignalType.SELL.value]
    protected_state.add_protection_remaining = 3

    decision_result = _make_decision_result(
        buy_score=0.25,
        sell_score=0.4,
    )

    result = layer.process(
        decision_result,
        protected_state,
        decision_result.stock,
    )

    assert result.position_action == PositionAction.HOLD_POSITION
    assert result.sell_path is None
    assert "加仓保护窗口: 刚加仓后不执行战术性减仓" in result.strategy_reasons


def test_add_protection_does_not_block_trend_exit():
    layer = StrategyLayer()
    protected_state = _make_state(0.4, entry_price=10.0)
    protected_state.last_decision = SignalType.SELL
    protected_state.inertia_counter = layer.INERTIA_MIN_DAYS
    protected_state.recent_signals = [SignalType.SELL.value, SignalType.SELL.value]
    protected_state.add_protection_remaining = 3

    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=9.8,
        ma20=10.2,
        ma60=10.5,
    )

    result = layer.process(
        decision_result,
        protected_state,
        decision_result.stock,
    )

    assert result.position_action == PositionAction.CLOSE_ALL
    assert result.sell_path == "trend_exit"


def test_take_profit_signal_without_enough_profit_falls_back_to_weak_sell():
    layer = StrategyLayer()
    decision_result = _make_decision_result(
        sell_score=0.45,
        price=10.2,
        signals=[_make_signal("take_profit", 0.8)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        _make_state(0.4, entry_price=10.0),
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.4 * layer.NORMAL_REDUCE_KEEP


def test_open_lifecycle_blocks_weak_sell_trim_during_min_hold_window():
    layer = StrategyLayer()
    open_state = _make_state(0.4, lifecycle=TradeLifecycle.HOLD, entry_price=10.0)
    open_state.last_decision = SignalType.SELL
    open_state.inertia_counter = layer.INERTIA_MIN_DAYS
    open_state.recent_signals = [SignalType.SELL.value, SignalType.SELL.value]
    open_state.min_hold_remaining = 3

    decision_result = _make_decision_result(
        buy_score=0.25,
        sell_score=0.4,
    )

    result = layer.process(
        decision_result,
        open_state,
        decision_result.stock,
    )

    assert result.position_action == PositionAction.HOLD_POSITION
    assert result.sell_path is None
    assert "最短持有窗口: 新开仓阶段不执行战术性减仓" in result.strategy_reasons


def test_open_lifecycle_still_allows_trend_exit():
    layer = StrategyLayer()
    open_state = _make_state(0.4, lifecycle=TradeLifecycle.HOLD, entry_price=10.0)
    open_state.last_decision = SignalType.SELL
    open_state.inertia_counter = layer.INERTIA_MIN_DAYS
    open_state.recent_signals = [SignalType.SELL.value, SignalType.SELL.value]
    open_state.min_hold_remaining = 3

    decision_result = _make_decision_result(
        buy_score=0.1,
        sell_score=0.7,
        price=9.8,
        ma20=10.2,
        ma60=10.5,
    )

    result = layer.process(
        decision_result,
        open_state,
        decision_result.stock,
    )

    assert result.position_action == PositionAction.CLOSE_ALL
    assert result.sell_path == "trend_exit"


def test_small_take_profit_position_stays_take_profit_trim_instead_of_close_all():
    layer = StrategyLayer()
    state = _make_state(0.04, entry_price=10.0)
    decision_result = _make_decision_result(
        sell_score=0.45,
        price=11.0,
        signals=[_make_signal("take_profit", 0.8)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        state,
    )
    sell_path = layer._infer_sell_path(
        SignalType.SELL,
        action,
        decision_result,
        state,
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.04 * layer.TAKE_PROFIT_KEEP
    assert sell_path == "take_profit_trim"


def test_small_weak_sell_position_stays_weak_sell_instead_of_close_all():
    layer = StrategyLayer()
    state = _make_state(0.04, entry_price=10.0)
    decision_result = _make_decision_result(
        buy_score=0.25,
        sell_score=0.4,
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        state,
    )
    sell_path = layer._infer_sell_path(
        SignalType.SELL,
        action,
        decision_result,
        state,
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.04 * layer.NORMAL_REDUCE_KEEP
    assert sell_path == "weak_sell"


def test_small_stop_loss_trim_position_stays_stop_loss_trim_instead_of_close_all():
    layer = StrategyLayer()
    state = _make_state(0.04, entry_price=10.0)
    decision_result = _make_decision_result(
        sell_score=0.45,
        price=9.7,
        signals=[_make_signal("stop_loss", 0.78)],
    )

    action, ratio = layer._calculate_position(
        SignalType.SELL,
        MarketState.TRANSITION,
        decision_result,
        state,
    )
    sell_path = layer._infer_sell_path(
        SignalType.SELL,
        action,
        decision_result,
        state,
    )

    assert action == PositionAction.REDUCE
    assert ratio == 0.04 * layer.NORMAL_REDUCE_KEEP
    assert sell_path == "stop_loss_trim"


if __name__ == "__main__":
    test_sell_signal_stays_out_when_flat()
    test_stop_loss_sell_closes_all_when_strong()
    test_stop_loss_sell_reduces_when_not_deep()
    test_stop_loss_signal_with_fresh_trend_break_exits_even_when_profitable()
    test_stop_loss_signal_without_actual_loss_falls_back_to_weak_sell()
    test_take_profit_sell_reduces_position()
    test_take_profit_with_fresh_trend_break_exits_instead_of_trimming()
    test_strong_sell_dominance_with_trend_break_closes_all()
    test_profitable_ma60_break_without_trend_rollover_only_reduces()
    test_strong_sell_without_trend_break_only_reduces()
    test_exit_lifecycle_without_fresh_trend_break_does_not_force_close_all()
    test_trend_exit_reason_mentions_trend_break_fact()
    test_weak_sell_reason_mentions_trend_not_broken()
    test_weak_sell_only_reduces_position()
    test_take_profit_trim_sets_reason_specific_reduce_protection()
    test_weak_sell_sets_longer_reduce_protection()
    test_add_sets_add_protection_window()
    test_open_sets_min_hold_window()
    test_stop_loss_trim_sets_reason_specific_reduce_protection()
    test_exit_lifecycle_reduce_normalizes_back_to_hold()
    test_same_reduce_reason_is_blocked_during_protection_window()
    test_different_reduce_reason_can_still_execute_during_protection_window()
    test_add_protection_blocks_weak_sell_trim_after_recent_add()
    test_add_protection_does_not_block_trend_exit()
    test_take_profit_signal_without_enough_profit_falls_back_to_weak_sell()
    test_open_lifecycle_blocks_weak_sell_trim_during_min_hold_window()
    test_open_lifecycle_still_allows_trend_exit()
    test_small_take_profit_position_stays_take_profit_trim_instead_of_close_all()
    test_small_weak_sell_position_stays_weak_sell_instead_of_close_all()
    test_small_stop_loss_trim_position_stays_stop_loss_trim_instead_of_close_all()
    print("strategy_layer sell split tests passed")