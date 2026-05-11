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


if __name__ == "__main__":
    test_sell_signal_stays_out_when_flat()
    test_stop_loss_sell_closes_all_when_strong()
    test_stop_loss_sell_reduces_when_not_deep()
    test_take_profit_sell_reduces_position()
    test_strong_sell_dominance_with_trend_break_closes_all()
    test_strong_sell_without_trend_break_only_reduces()
    test_trend_exit_reason_mentions_trend_break_fact()
    test_weak_sell_reason_mentions_trend_not_broken()
    test_weak_sell_only_reduces_position()
    test_take_profit_signal_without_enough_profit_falls_back_to_weak_sell()
    print("strategy_layer sell split tests passed")