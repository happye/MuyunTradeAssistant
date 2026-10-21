"""T04-C：chat manage_portfolio add/plan/overweight 不再调用 CLI manage_positions。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.chat.tools as chat_tools
import src.cli.main as cli_main
import src.data.portfolio as portfolio_mod
from src.data.models import TradePlan
from src.data.portfolio import PortfolioManager


CODE = "600519"


class _Env:
    def __init__(self, tmp_path, monkeypatch):
        self.portfolio = tmp_path / "portfolio.yaml"
        self.proposals = tmp_path / "proposals.json"
        self.mp = monkeypatch
        self.cli_calls = []
        monkeypatch.setattr(portfolio_mod, "PortfolioManager", self.new_pm)
        monkeypatch.setattr(cli_main, "manage_positions", self._cli)
        chat_tools._portfolio_manager = None

    def new_pm(self):
        return PortfolioManager(str(self.portfolio), str(self.proposals))

    def _cli(self, *a, **k):
        self.cli_calls.append((a, k))
        raise AssertionError("manage_positions should not be called")


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _Env(tmp_path, monkeypatch)


def test_chat_add_no_price_skips_cli(env):
    r = chat_tools.manage_portfolio(
        action="add", stock_code=CODE, stock_name="测试股",
        price=None, ratio=0.2, confirm=True)
    assert "已添加持仓" in r
    assert "跳过 TradePlan" in r
    assert env.cli_calls == []
    assert env.new_pm().has_position(CODE)


def test_chat_plan_view_existing_no_cli(env):
    pm = env.new_pm()
    assert pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.2)
    plan = TradePlan(
        plan_id="p", opened_at="2026-10-21", why_buy="w", when_buy="t",
        how_much=0.2, when_sell_targets=[12.0], when_sell_invalidate=["x"],
        locked_initial_stop=9.0, current_stop=9.0, max_hold_days=30,
        fundamental_outlook="neutral")
    assert pm.attach_plan(CODE, plan)
    r = chat_tools.manage_portfolio(action="plan", stock_code=CODE, confirm=True)
    assert "交易计划" in r and "why_buy: w" in r
    assert env.cli_calls == []


def test_chat_overweight_no_plan_no_cli(env):
    assert env.new_pm().add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.2)
    r = chat_tools.manage_portfolio(action="overweight", stock_code=CODE, basis="事件", confirm=True)
    assert "无 TradePlan" in r
    assert env.cli_calls == []
