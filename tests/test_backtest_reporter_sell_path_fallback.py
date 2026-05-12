# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.backtest_reporter import _serialize_trade_with_semantic, build_analysis_payload
from src.data.models import BacktestResult, DailySnapshot, TradeRecord


def test_trend_exit_reason_preserves_trend_exit_path():
    trade = {
        "action": "SELL",
        "position_action": "CLOSE_ALL",
        "reason": "趋势退出: 跌破MA60、浮亏扩大，执行清仓(transition)",
    }

    payload = _serialize_trade_with_semantic(trade)

    assert payload["action_semantic"] == "EXIT"
    assert payload["sell_path"] == "trend_exit"


def test_weak_sell_reason_preserves_weak_sell_path():
    trade = {
        "action": "SELL",
        "position_action": "REDUCE",
        "reason": "弱卖出: 卖压存在但趋势未破坏，先减仓观察",
    }

    payload = _serialize_trade_with_semantic(trade)

    assert payload["action_semantic"] == "TRIM"
    assert payload["sell_path"] == "weak_sell"


def test_take_profit_reason_preserves_take_profit_path():
    trade = {
        "action": "SELL",
        "position_action": "REDUCE",
        "reason": "止盈触发: 分批落袋",
    }

    payload = _serialize_trade_with_semantic(trade)

    assert payload["action_semantic"] == "TRIM"
    assert payload["sell_path"] == "take_profit_trim"


def test_stop_loss_reason_preserves_stop_loss_path():
    trade = {
        "action": "SELL",
        "position_action": "CLOSE_ALL",
        "reason": "止损触发: 清仓保护本金",
    }

    payload = _serialize_trade_with_semantic(trade)

    assert payload["action_semantic"] == "STOP"
    assert payload["sell_path"] == "stop_loss_exit"


def test_analysis_summary_includes_semantic_breakdown():
    result = BacktestResult(
        stock_code="000001",
        stock_name="test",
        start_date="2025-01-01",
        end_date="2025-01-10",
        initial_capital=100000.0,
        final_value=101000.0,
        total_return_pct=1.0,
        annualized_return_pct=10.0,
        max_drawdown_pct=2.0,
        win_rate=50.0,
        profit_loss_ratio=1.2,
        sharpe_ratio=1.1,
        total_trades=3,
        buy_count=2,
        sell_count=1,
        benchmark_return_pct=0.5,
        invested_return_pct=1.5,
        trades=[
            TradeRecord(date="2025-01-02", action="BUY", price=10.0, shares=100, amount=1000.0, position_action="OPEN"),
            TradeRecord(date="2025-01-03", action="BUY", price=10.2, shares=100, amount=1020.0, position_action="ADD"),
            TradeRecord(date="2025-01-06", action="SELL", price=10.5, shares=200, amount=2100.0, position_action="REDUCE", reason="止盈触发: 分批落袋"),
        ],
        daily_snapshots=[
            DailySnapshot(date="2025-01-02", price=10.0, cash=99000.0, position=100, market_value=1000.0, total_value=100000.0, return_pct=0.0),
            DailySnapshot(date="2025-01-03", price=10.2, cash=97980.0, position=200, market_value=2040.0, total_value=100020.0, return_pct=0.02),
            DailySnapshot(date="2025-01-06", price=10.5, cash=100080.0, position=0, market_value=0.0, total_value=100080.0, return_pct=0.08),
        ],
    )

    payload = build_analysis_payload(result, "framework_strict")
    summary = payload["summary"]

    assert summary["action_semantic_breakdown"] == {"ADD": 1, "ENTRY": 1, "TRIM": 1}
    assert summary["sell_path_breakdown"] == {"take_profit_trim": 1}


def test_explicit_trade_semantic_is_preserved_even_when_position_action_is_close_all():
    trade = TradeRecord(
        date="2025-01-06",
        action="SELL",
        price=10.5,
        shares=200,
        amount=2100.0,
        position_action="CLOSE_ALL",
        action_semantic="TRIM",
        sell_path="take_profit_trim",
        reason="止盈触发: 分批落袋",
    )

    payload = _serialize_trade_with_semantic(trade)

    assert payload["action_semantic"] == "TRIM"
    assert payload["sell_path"] == "take_profit_trim"


def test_execution_log_tables_preserve_trade_semantic_and_sell_path():
    trade = TradeRecord(
        date="2025-01-06",
        action="SELL",
        price=10.5,
        shares=200,
        amount=2100.0,
        position_action="CLOSE_ALL",
        action_semantic="TRIM",
        sell_path="take_profit_trim",
        reason="止盈触发: 分批落袋",
        position_ratio_after=0.0,
    )
    result = BacktestResult(
        stock_code="000001",
        stock_name="test",
        start_date="2025-01-01",
        end_date="2025-01-10",
        initial_capital=100000.0,
        final_value=101000.0,
        total_return_pct=1.0,
        annualized_return_pct=10.0,
        max_drawdown_pct=2.0,
        win_rate=50.0,
        profit_loss_ratio=1.2,
        sharpe_ratio=1.1,
        total_trades=1,
        buy_count=0,
        sell_count=1,
        benchmark_return_pct=0.5,
        invested_return_pct=1.5,
        trades=[trade],
        daily_snapshots=[
            DailySnapshot(date="2025-01-02", price=10.0, cash=100000.0, position=200, market_value=2000.0, total_value=102000.0, return_pct=2.0),
            DailySnapshot(date="2025-01-06", price=10.5, cash=102100.0, position=0, market_value=0.0, total_value=102100.0, return_pct=2.1),
        ],
        diagnostics={
            "execution_logs": [
                {
                    "execution_date": "2025-01-06",
                    "signal_date": "2025-01-05",
                    "position_ratio_before": 0.2,
                    "position_ratio_after": 0.0,
                    "decision": {"decision": "SELL", "score": 0.45},
                    "strategy": {
                        "decision": "SELL",
                        "position_action": "REDUCE",
                        "action_semantic": "TRIM",
                        "sell_path": "take_profit_trim",
                    },
                    "execution": {"blocked": False, "effective_action": "CLOSE_ALL", "block_reason": ""},
                    "trade": trade.model_dump(),
                }
            ]
        },
    )

    payload = build_analysis_payload(result, "framework_strict")

    assert payload["action_source_table"][0]["action_semantic"] == "TRIM"
    assert payload["action_source_table"][0]["sell_path"] == "take_profit_trim"
    assert payload["hold_break_table"][0]["action_semantic"] == "TRIM"
    assert payload["hold_break_table"][0]["sell_path"] == "take_profit_trim"


if __name__ == "__main__":
    test_trend_exit_reason_preserves_trend_exit_path()
    test_weak_sell_reason_preserves_weak_sell_path()
    test_take_profit_reason_preserves_take_profit_path()
    test_stop_loss_reason_preserves_stop_loss_path()
    test_analysis_summary_includes_semantic_breakdown()
    test_explicit_trade_semantic_is_preserved_even_when_position_action_is_close_all()
    test_execution_log_tables_preserve_trade_semantic_and_sell_path()
    print("backtest reporter sell_path fallback tests passed")