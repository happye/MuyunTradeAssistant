# -*- coding: utf-8 -*-
"""
ISS-086 回归测试：strategy_layer 日频推进的日期去重。

漏洞背景：process() 此前每次调用无条件递减冷却/减仓保护/最短持有/加仓保护
四计数器，并无条件 push_signal——回测每 bar 调一次恰好正确；但 live 同一天
重复分析（REPL 连跑 l / chat 多问 / la 批量）会把 5 天禁买烧成 N 次分析，
且同日信号重复入史污染稳定性评分（2026-09-06 探针实证）。

修复：process 按 current_date 去重——同日重入跳过推进；日期变化推进一次；
current_date=None 保持旧语义（无条件推进，兼容 tests 等调用方）。

跑法：
  .venv/Scripts/python.exe -m pytest tests/core/test_strategy_layer_daily_tick.py -q
  .venv/Scripts/python.exe tests/core/test_strategy_layer_daily_tick.py   # 直跑
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.core.strategy_layer import StrategyLayer  # noqa: E402
from src.data.models import (  # noqa: E402
    DecisionResult, DecisionTrace, MarketState, SignalType, StockData,
    StrategyState, TradeLifecycle,
)


def _make_decision_result(decision: SignalType = SignalType.HOLD) -> DecisionResult:
    return DecisionResult(
        stock=StockData(
            stock_code="000001",
            stock_name="test",
            price=10.0,
            volume=1000000,
            ma20=10.5,
            ma60=11.0,
        ),
        state=MarketState.TRANSITION,
        decision=decision,
        score=0.3,
        signals=[],
        trace=[
            DecisionTrace(
                step="市场状态影响",
                description="test scores",
                data={"final_scores": {"BUY": 0.3, "SELL": 0.1}},
            )
        ],
    )


def _make_state(**kwargs) -> StrategyState:
    return StrategyState(
        current_position_ratio=0.2,
        lifecycle=TradeLifecycle.COOLDOWN,
        entry_date="2026-09-01",
        entry_price=10.0,
        cooldown_remaining=5,
        cooldown_reason="close_all",
        reduce_protection_remaining=10,
        min_hold_remaining=3,
        **kwargs,
    )


def test_same_day_repeated_process_ticks_once():
    """同一天重复 process：四计数器只递减一次（修复前会递减两次，红灯项）。"""
    layer = StrategyLayer()
    state = _make_state()
    dr = _make_decision_result()

    first = layer.process(dr, state, dr.stock, current_date="2026-09-06")
    assert first.new_state.cooldown_remaining == 4
    assert first.new_state.reduce_protection_remaining == 9
    assert first.new_state.min_hold_remaining == 2

    second = layer.process(dr, first.new_state, dr.stock, current_date="2026-09-06")
    assert second.new_state.cooldown_remaining == 4, "同日重入不得再烧冷却"
    assert second.new_state.reduce_protection_remaining == 9
    assert second.new_state.min_hold_remaining == 2
    assert second.new_state.last_tick_date == "2026-09-06"


def test_next_day_ticks_again():
    """跨日调用：各递减一次（回测每 bar 语义保持）。"""
    layer = StrategyLayer()
    state = _make_state()
    dr = _make_decision_result()

    day1 = layer.process(dr, state, dr.stock, current_date="2026-09-06")
    day2 = layer.process(dr, day1.new_state, dr.stock, current_date="2026-09-07")
    assert day1.new_state.cooldown_remaining == 4
    assert day2.new_state.cooldown_remaining == 3
    assert day2.new_state.last_tick_date == "2026-09-07"


def test_none_date_keeps_legacy_unconditional_tick():
    """current_date=None（tests 等调用方）：保持旧语义无条件推进，行为零变化。"""
    layer = StrategyLayer()
    state = _make_state()
    dr = _make_decision_result()

    once = layer.process(dr, state, dr.stock)
    twice = layer.process(dr, once.new_state, dr.stock)
    assert once.new_state.cooldown_remaining == 4
    assert twice.new_state.cooldown_remaining == 3


def test_same_day_signal_history_not_polluted():
    """同日重入不重复 push_signal——recent_signals 与稳定性不被同日信号污染。"""
    layer = StrategyLayer()
    state = _make_state()
    dr = _make_decision_result(decision=SignalType.SELL)

    first = layer.process(dr, state, dr.stock, current_date="2026-09-06")
    history_after_first = list(first.new_state.recent_signals)
    second = layer.process(dr, first.new_state, dr.stock, current_date="2026-09-06")
    assert second.new_state.recent_signals == history_after_first
    assert second.new_state.signal_stability_score == first.new_state.signal_stability_score


def test_old_state_without_last_tick_date_migrates():
    """旧 portfolio.yaml 的 strategy_state 无 last_tick_date 字段：首次分析正常推进并落字段。"""
    layer = StrategyLayer()
    state = _make_state()  # last_tick_date 默认 None
    assert state.last_tick_date is None
    dr = _make_decision_result()

    out = layer.process(dr, state, dr.stock, current_date="2026-09-06")
    assert out.new_state.cooldown_remaining == 4
    assert out.new_state.last_tick_date == "2026-09-06"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
