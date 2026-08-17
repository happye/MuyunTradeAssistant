"""ISS-053 审查修复：回测禁用 fundamental_alert 守卫单测

深度审查发现：fundamental_alert forecast 路径用当前 baostock 数据 + 无上界过滤，
回测里未来预亏预告会在第一根持仓 bar 即触发退出（前瞻偏差）。修法：回测整体禁用
fundamental_alert（is_backtest=True 守卫），live 不变。本测直接验 Orchestrator._compute_fundamental_alert。

跑法: PYTHONUTF8=1 PYTHONPATH=. uv run python tests/test_fundamental_alert_backtest_guard.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.orchestrator import Orchestrator
from src.data.models import StockData, TradePlan


def _sd():
    return StockData(stock_code="600519", stock_name="贵州茅台", price=1500.0, volume=10000)


def _plan():
    return TradePlan(
        plan_id="X_2026-01-01", opened_at="2026-01-01", why_buy="t", when_buy="t",
        how_much=0.2, when_sell_targets=[11.0], when_sell_invalidate=["x"],
        locked_initial_stop=9.2, current_stop=9.2, max_hold_days=180,
        fundamental_outlook="bullish", thesis_sources=[], adjustments=[],
    )


def test_backtest_skips_fundamental_alert():
    """is_backtest=True + 持仓 -> 不调 check_fundamental_alert，返回 None（回测不前瞻）。"""
    with patch("src.core.exit_signals.fundamental.check_fundamental_alert") as m:
        m.return_value = "基本面恶化:被ST(ST星源)"
        r = Orchestrator._compute_fundamental_alert(_sd(), True, True, _plan())
    assert r is None, "回测应跳过 fundamental_alert"
    assert not m.called, "回测不应调 check_fundamental_alert"
    print("✓ is_backtest=True 跳过 fundamental_alert（不调 check，回测不前瞻）")


def test_live_runs_fundamental_alert():
    """is_backtest=False + 持仓 -> 调 check_fundamental_alert，返回其结果（live 仍生效）。"""
    with patch("src.core.exit_signals.fundamental.check_fundamental_alert") as m:
        m.return_value = "基本面恶化:被ST(ST星源)"
        r = Orchestrator._compute_fundamental_alert(_sd(), True, False, _plan())
    assert r == "基本面恶化:被ST(ST星源)"
    assert m.called, "live 应调 check_fundamental_alert"
    print("✓ is_backtest=False 启用 fundamental_alert（live 不变）")


def test_no_position_skips():
    """has_position=False -> None（无论 is_backtest）。"""
    with patch("src.core.exit_signals.fundamental.check_fundamental_alert") as m:
        r1 = Orchestrator._compute_fundamental_alert(_sd(), False, False, _plan())
        r2 = Orchestrator._compute_fundamental_alert(_sd(), False, True, _plan())
    assert r1 is None and r2 is None
    assert not m.called, "无持仓不应调 check"
    print("✓ has_position=False 跳过")


def test_exception_fail_open():
    """check_fundamental_alert 抛异常 -> 返回 None（fail-open，不假退出/不崩）。"""
    with patch("src.core.exit_signals.fundamental.check_fundamental_alert",
               side_effect=RuntimeError("net down")):
        r = Orchestrator._compute_fundamental_alert(_sd(), True, False, _plan())
    assert r is None
    print("✓ 异常 fail-open 返回 None")


if __name__ == "__main__":
    print("\n=== ISS-053 回测禁用 fundamental_alert 守卫单测 ===\n")
    test_backtest_skips_fundamental_alert()
    test_live_runs_fundamental_alert()
    test_no_position_skips()
    test_exception_fail_open()
    print("\n=== 全部 4 项 PASS ===")
