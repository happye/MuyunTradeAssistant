"""BUG-FEE（iteration10）：pos confirm 入口数值校验接线测试

反例：`pos confirm X --qty 100 --price 12 --fee -1`、`--fee nan`、`--qty nan`、`--price inf`、
位置参数成交价 `inf` 在入口层被放行，直到服务层 FillInput(fee ge=0) 才抛 pydantic 原始异常。
锁死：走真实 start.parse_input -> start.run_cli 全链（LRN-L005），入口拒绝后
  - parse_input 返回 None（不进入账本层）
  - stdout 含「  [!]」开头的人话提示且点名具体 flag
  - 账本事件数 / 投影不变
  - 无 Traceback / ValidationError 字样
正例：--fee 0 / --fee 5 正常入账（行为不变）。

隔离：持久化路径全部指向 tmp_path；零网络零 AI。
跑法：pytest tests/core/test_bug_fee_confirm_input.py -q
"""
import io
import os
import sys
from contextlib import redirect_stdout

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.data.account_service as account_module
import src.data.horizon_plans as horizon_plans_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.data.account_service import AccountService
from src.data.portfolio import PortfolioManager
from src.data.proposals import Proposal

CODE = "600519"
TRADE_DATE = "2026-09-21"   # 期初批次 2026-09-20，T+1 可卖


@pytest.fixture
def acct(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", tmp_path / "horizon_plans.json")
    root = str(tmp_path.resolve())
    for p in (tmp_path / "portfolio.yaml", tmp_path / "account_events.jsonl",
              tmp_path / "horizon_plans.json", tmp_path / "proposals.json"):
        assert os.path.abspath(str(p)).startswith(root), f"持久化路径越出临时根: {p}"
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10.0, ratio=0.1)
    pm._proposals.add_proposal(Proposal(
        proposal_id="bf", stock_code=CODE, stock_name="测试股",
        position_action="ADD", target_ratio=0.15, current_ratio=0.1))
    assert pm._proposals._save()
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=100000.0, trade_date="2026-09-20",
        lots=[{"security_id": CODE, "quantity": 300, "cost_price": 10.0,
               "acquired_at": "2026-09-20"}])
    return tmp_path


def _state():
    """磁盘事实快照（新实例读盘）：账本事件数 + 投影关键字段。"""
    events = AccountService(account_module.DEFAULT_LEDGER_PATH).log.events()
    pos = PortfolioManager().get_position(CODE)
    return len(events), (pos.current_ratio, pos.last_action, pos.quantity_fact)


def _repl(cmd):
    """真实 REPL 全链：parse_input -> （非 None 时）run_cli。返回 (parsed, stdout, 异常或 None)。"""
    buf = io.StringIO()
    exc = None
    parsed = None
    from unittest.mock import patch
    with patch.object(cli_main, "console", Console(file=buf, width=200)):
        with redirect_stdout(buf):
            try:
                parsed = start_module.parse_input(cmd)
                if parsed is not None:
                    start_module.run_cli(parsed[0], parsed[1])
            except Exception as e:  # noqa: BLE001 —— 记录原始呈现，断言处判红
                exc = e
    return parsed, buf.getvalue(), exc


@pytest.mark.parametrize("cmd, flag", [
    (f"pos confirm {CODE} --qty 100 --price 12 --fee -1", "--fee"),
    (f"pos confirm {CODE} --qty 100 --price 12 --fee nan", "--fee"),
    (f"pos confirm {CODE} --qty nan --price 12", "--qty"),
    (f"pos confirm {CODE} --qty 100 --price inf", "--price"),
    (f"pos confirm {CODE} 0.1 inf", "inf"),
])
def test_bad_numeric_rejected_at_entry(acct, cmd, flag):
    before = _state()
    parsed, out, exc = _repl(cmd)
    assert exc is None, f"入口不得抛原始异常: {type(exc).__name__}: {exc}"
    assert parsed is None, f"应在入口被拒（不进入 run_cli）: {parsed}"
    hints = [ln for ln in out.splitlines() if ln.startswith("  [!]")]
    assert hints, f"须有「  [!]」人话提示: {out!r}"
    assert any(flag in ln for ln in hints), f"提示须点名 {flag}: {hints}"
    assert "Traceback" not in out and "ValidationError" not in out
    assert _state() == before, "账本/投影不得改动"


@pytest.mark.parametrize("fee", ["0", "5"])
def test_valid_fee_still_books(acct, fee):
    n0, _ = _state()
    parsed, out, exc = _repl(
        f"pos confirm {CODE} --qty 100 --price 12 --fee {fee} --date {TRADE_DATE}")
    assert exc is None, f"{type(exc).__name__}: {exc}"
    assert parsed is not None and parsed[0] == "pos_confirm"
    assert "事件账本入账" in out, out
    n1, _ = _state()
    assert n1 == n0 + 1
    last = AccountService(account_module.DEFAULT_LEDGER_PATH).log.events()[-1]
    assert last.fee == float(fee)
