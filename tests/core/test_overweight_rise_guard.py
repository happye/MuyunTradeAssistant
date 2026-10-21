"""BUG-OW 超配 3 倍禁入铁律回归测试（笨总教学十：涨幅3-10倍标的自动 pass）

缺陷：manage_positions overweight 分支拒绝文案里有未配对的 [/red]，rich 抛
MarkupError，被外层 except Exception 吞成「涨幅检查数据获取失败，继续」，
随后超配被激活——3 倍禁入铁律被静默绕过（fail-open）。

锁死语义：
1. 涨幅 >= 3 倍 → 输出「拒绝超配」、overweight_executed 不落盘为 True
2. 涨幅 < 3 倍 → 检查通过并激活超配、落盘
3. 数据获取失败（get_stock_data 抛错）→ 既有产品裁决不变：继续但建议人工核实，并激活

隔离纪律：PortfolioManager 全部路径在 tmp_path；get_stock_data 被 monkeypatch，不联网。
跑法：pytest tests/core/test_overweight_rise_guard.py -q
"""
import io
import os
import sys

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 类测试顶部先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.data.akshare_client as akshare_client
from src.data.models import StockData, TradePlan
from src.data.portfolio import PortfolioManager


def _setup(tmp_path):
    folder = tmp_path / "ow"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    root = str(tmp_path.resolve())
    for p in (str(pm.portfolio_path), str(pm._proposals.path)):
        assert os.path.abspath(p).startswith(root), f"持久化路径越出临时根: {p}"
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    plan = TradePlan(plan_id="ow-test", opened_at="2026-10-01", why_buy="t", when_buy="t",
                     stock_code="600519", stock_name="测试股", entry_price=10.0,
                     how_much=0.1, locked_initial_stop=9.0, current_stop=9.0)
    assert pm.attach_plan("600519", plan)
    return folder, pm


def _run_overweight(monkeypatch, folder, pm, get_stock_data):
    monkeypatch.setattr(akshare_client, "get_stock_data", get_stock_data)
    output = io.StringIO()
    monkeypatch.setattr(cli_main, "PortfolioManager", lambda *a, **k: pm)
    monkeypatch.setattr(cli_main, "console", Console(file=output, width=200))
    cli_main.manage_positions("overweight", stock_code="600519", name="质变事件")
    fresh = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    return output.getvalue(), fresh.get_position("600519").trade_plan


def _sd(price, low_60d):
    return lambda code: StockData(stock_code=code, stock_name="测试股", price=price,
                                  volume=1000, low_60d=low_60d)


def test_rise_3x_or_more_rejects_overweight(tmp_path, monkeypatch):
    """倍数 3.5 → 拒绝超配；标志不得落盘为 True（旧实现 MarkupError 被吞后照常激活）。"""
    folder, pm = _setup(tmp_path)
    out, tp = _run_overweight(monkeypatch, folder, pm, _sd(35.0, 10.0))
    assert "拒绝超配" in out, f"3 倍以上必须拒绝超配:\n{out}"
    assert "数据获取失败" not in out, f"拒绝文案不得触发数据失败兜底:\n{out}"
    assert tp.overweight_executed is False, "3 倍禁入：overweight_executed 不得落盘为 True"
    assert tp.overweight_expiry is None


def test_rise_below_3x_activates_overweight(tmp_path, monkeypatch):
    """倍数 2.0 → 涨幅检查通过并激活超配、落盘。"""
    folder, pm = _setup(tmp_path)
    out, tp = _run_overweight(monkeypatch, folder, pm, _sd(20.0, 10.0))
    assert "涨幅检查通过" in out and "超配已激活" in out, out
    assert tp.overweight_executed is True
    assert tp.overweight_expiry


def test_data_fetch_error_keeps_continue_semantics(tmp_path, monkeypatch):
    """get_stock_data 抛错 → 既有产品裁决：继续但建议人工核实，并激活（语义不改）。"""
    folder, pm = _setup(tmp_path)

    def _boom(code):
        raise RuntimeError("模拟行情获取失败")

    out, tp = _run_overweight(monkeypatch, folder, pm, _boom)
    assert "数据获取失败，继续但建议人工核实" in out, out
    assert "超配已激活" in out
    assert tp.overweight_executed is True
