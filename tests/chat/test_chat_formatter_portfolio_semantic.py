# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.chat.formatter import format_portfolio
from src.data.portfolio import PositionRecord


def test_format_portfolio_includes_action_semantic_and_sell_path():
    positions = [
        PositionRecord(
            stock_code="000001",
            stock_name="test",
            current_ratio=0.2,
            entry_price=10.0,
            last_action="SELL",
            last_action_semantic="TRIM",
            last_sell_path="take_profit_trim",
            last_action_date="2025-01-06",
            lifecycle="HOLD",
        )
    ]

    text = format_portfolio(positions)

    assert "动作语义:TRIM" in text
    assert "卖出路径:take_profit_trim" in text


if __name__ == "__main__":
    test_format_portfolio_includes_action_semantic_and_sell_path()
    print("chat formatter portfolio semantic tests passed")