# -*- coding: utf-8 -*-
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.portfolio import PortfolioManager


def test_manual_open_position_seeds_min_hold_window():
    with tempfile.TemporaryDirectory() as temp_dir:
        portfolio_path = os.path.join(temp_dir, "portfolio.yaml")
        manager = PortfolioManager(portfolio_path=portfolio_path)

        manager.add_position(
            stock_code="000001",
            stock_name="test",
            entry_price=10.0,
            ratio=0.2,
            lifecycle="OPEN",
        )

        state = manager.to_strategy_state("000001")

        assert state.min_hold_remaining == manager.DEFAULT_MIN_HOLD_DAYS


if __name__ == "__main__":
    test_manual_open_position_seeds_min_hold_window()
    print("portfolio phase5 window tests passed")