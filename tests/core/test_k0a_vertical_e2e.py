"""K0a 纵向端到端验收（plan/fusion iteration4，DELIVERY_PLAN K0a-6）

一次 quantity 部分卖出贯穿：真实 REPL 解析（start.parse_input）→ run_cli →
CLI 确认（manage_positions）→ 账户事件账本（事实源）→ 重启（新实例读盘）→
today / legacy 持仓读取。

隔离纪律（J2 事故教训）：DEFAULT_PORTFOLIO_PATH 是模块位置相对路径、proposals
与账户账本默认在 HOME——**HOME 重定向本身不算隔离**；本测试在构造任何管理器
之前显式重定向三条持久化路径并断言全部落在临时根内。

跑法：pytest tests/core/test_k0a_vertical_e2e.py -q
"""
import io
import os
import sys
from unittest.mock import patch

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.data.account_service as account_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.cli.today_service import build_today_view
from src.data.account_service import AccountService
from src.data.portfolio import PortfolioManager
from src.data.proposals import Proposal


def test_k0a_partial_sell_vertical_repl_to_today(tmp_path, monkeypatch):
    """部分卖出（1000 股卖 100）：记录保留、账本 900 股、重启不丢、today 如实显示。"""
    # ── 隔离重定向（必须先于一切构造）──
    portfolio_path = tmp_path / "portfolio.yaml"
    proposals_path = tmp_path / "proposals.json"
    ledger_path = tmp_path / "account_events.jsonl"
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(portfolio_path))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(proposals_path))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", ledger_path)
    root = str(tmp_path.resolve())
    for p in (str(portfolio_path), str(proposals_path), str(ledger_path)):
        assert os.path.abspath(p).startswith(root), f"持久化路径越出临时根: {p}"
    pm = PortfolioManager()
    assert os.path.abspath(str(pm.portfolio_path)).startswith(root), \
        f"PortfolioManager 必须解析到临时根: {pm.portfolio_path}"
    assert os.path.abspath(str(pm._proposals.path)).startswith(root)

    # ── 场景：比例视图 10% + CLOSE_ALL 待确认建议；账本期初 1000 股@10 + 现金 10000 ──
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    pm._proposals.add_proposal(Proposal(stock_code="600519", stock_name="测试股",
                                        position_action="CLOSE_ALL", target_ratio=0,
                                        current_ratio=0.1))
    assert pm._proposals._save()
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20",
                       lots=[{"security_id": "600519", "quantity": 1000,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])

    # ── 真实 REPL 解析 + CLI 执行 ──
    parsed = start_module.parse_input(
        "pos confirm 600519 --qty 100 --price 10 --date 2026-09-21")
    assert parsed is not None and parsed[0] == "pos_confirm", f"REPL 解析失败: {parsed}"
    output = io.StringIO()
    with patch.object(cli_main, "console", Console(file=output, width=200)):
        start_module.run_cli(parsed[0], parsed[1])

    # ── 账本事实（数量是事实）──
    snap = svc.snapshot()
    remaining = sum(h.quantity for h in snap.holdings)
    assert remaining == 900, f"账本事实：剩 900 股，实际 {remaining}"
    assert snap.holdings[0].avg_cost == pytest.approx(10.0)
    assert not snap.isolated_events
    out = output.getvalue()
    assert "已成交 100" in out and "剩余 900" in out, f"输出须如实显示股数事实:\n{out}"
    assert "清仓完成" not in out, "未清仓不得显示清仓完成"

    # ── 重启（新实例从磁盘加载——崩溃恢复读路径）──
    pm2 = PortfolioManager()
    view = pm2.get_position("600519")
    assert view is not None, "部分卖出后重启：持仓记录必须还在（legacy 持仓读取不得当无仓）"
    assert view.ratio_stale, "NAV 未知：比例视图标记待重估"
    assert view.quantity_fact and view.quantity_fact.get("quantity") == 900
    assert view.last_action_date == "2026-09-21", "真实成交日贯穿"
    state = pm2.to_strategy_state("600519")  # legacy 策略层读取不崩
    assert state is not None

    # ── today 消费同一账户状态 ──
    today_view = build_today_view(
        pm2, account=AccountService(account_module.DEFAULT_LEDGER_PATH).snapshot())
    assert any("持有 900 股" in line for line in today_view.account), \
        f"today 须显示账本事实: {today_view.account}"
    assert any("11,000" in line for line in today_view.account), \
        f"today 现金区（期初10000+卖出1000=11000）: {today_view.account}"
    assert not any("待核对事件" in n for n in today_view.notices), \
        "干净账本不得出现待核对告警"
