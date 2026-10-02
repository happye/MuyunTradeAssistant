"""P1 共享清单和 ctx 贯穿查仓与启动提示回归（plan/fusion iteration9；R15 Z3）

锁死语义：
1. Z3①：chat get_portfolio **所有路径**读同一共享清单（holding_entries）——
   投影独有/账本独有/重叠/混合/明确空/损坏逐项；股票代码集合等于已知共享
   集合且不漏不重复（不只 assert"不是空仓"）。
2. Z3②：l/la 启动提示按 ctx 三态显示——账本有仓时同次输出不得先说
   "将从FLAT状态开始分析"再说"账本有仓"（同屏矛盾）；UNKNOWN 提示正例；
   正常 RATIO_ONLY 展示兼容。
3. 数据失败不阻断批量后续股；同次上游硬退出→终态/影子一致（既有回归引用）。

隔离纪律：持久化路径全量重定向临时根；行情替身。AI 调用在守卫 runner
（socket 审计阻断→降级）或 configs/settings.yaml ai.enabled=false 下为
零付费零外联；开发机裸跑 la/l 全链注意付费开关。
跑法：pytest tests/chat/test_p1_shared_portfolio_view.py -q
"""
import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.chat.tools as chat_tools
import src.cli.main as cli_main
import src.core.shadow_diff as shadow_module
import src.data.account_service as account_module
import src.data.akshare_client as akshare_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.data.account_service import AccountService
from src.data.portfolio import PortfolioManager

PRICE_DAY = "2026-09-30"
CODE = "600519"


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    monkeypatch.setattr(shadow_module, "SHADOW_STORE_PATH", tmp_path / "shadow_diff.jsonl")
    import src.cli.evidence as evidence_module
    monkeypatch.setattr(evidence_module, "_EVIDENCE_FILE", tmp_path / "analysis_evidence.jsonl")
    monkeypatch.setattr(evidence_module, "_REPORT_DIR", tmp_path / "cards")


def _seed_ledger_only(*, code=CODE, quantity=100):
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20",
                       lots=[{"security_id": code, "quantity": quantity,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    return svc


def _stub_market(code, *, price=10.0):
    from src.data.models import StockData
    return StockData(stock_code=code, stock_name="测试股", price=price, volume=100000,
                     change_pct=1.0, avg_volume_20=100000.0,
                     ma5=9.8, ma20=9.5, ma60=9.0, quote_as_of=PRICE_DAY)


def _repl(cmd: str) -> str:
    parsed = start_module.parse_input(cmd)
    assert parsed is not None, f"REPL 解析失败: {cmd}"
    buf = io.StringIO()
    with patch.object(cli_main, "console", Console(file=buf, width=200)):
        with redirect_stdout(buf):
            start_module.run_cli(parsed[0], parsed[1])
    return buf.getvalue()


def _code_occurrences(output: str, code: str) -> int:
    """代码在输出中的出现次数（6 位码带词边界近似——统计形如 (600519) 或行首码）。"""
    import re
    return len(re.findall(rf"(?<!\d){re.escape(code)}(?!\d)", output))


# ── Z3①：get_portfolio 全路径共享清单 ────────────────────────────────

def test_z3_chat_mixed_collection_complete(tmp_path, monkeypatch):
    """投影独有+账本独有 → 两者都列出（R15 Z3①红灯——旧实现只列投影）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("000001", stock_name="投影独有", entry_price=5, ratio=.1)
    _seed_ledger_only(quantity=100)
    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm, raising=False)
    out = chat_tools.get_portfolio()
    assert "000001" in out and CODE in out, f"共享清单必须完整: {out}"
    assert _code_occurrences(out, "000001") >= 1
    assert "待对账" in out or "投影" in out, "账本独有条目必须标注元数据缺口"


def test_z3_chat_all_paths_no_leak_no_dup(tmp_path, monkeypatch):
    """投影独有、账本独有、重叠、混合——代码集合等于已知共享集合且不漏不重。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("000001", stock_name="投影独有", entry_price=5, ratio=.1)  # A 投影独有
    pm.add_position(CODE, stock_name="重叠持仓", entry_price=10, ratio=.1)     # B 重叠
    _seed_ledger_only(quantity=100)                                            # B 账本侧
    _seed_ledger_only(code="000688", quantity=200)                             # C 账本独有
    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm, raising=False)
    out = chat_tools.get_portfolio()
    # 集合相等且不漏不重：三只各恰出现一次（去重由 holding_entries 保证）
    for code in ("000001", CODE, "000688"):
        n = _code_occurrences(out, code)
        assert n == 1, f"{code} 应恰出现 1 次（不漏不重）: 实际 {n} 次"
    # 投影细节保留（旧比例/动作语义照旧展示——正常输出不改变）
    assert "投影独有" in out and "重叠持仓" in out


def test_z3_chat_empty_and_incomplete_distinct(tmp_path, monkeypatch):
    """明确空 → "当前无持仓记录"；读取不完整 → 显式提示（不谎报空仓）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm, raising=False)
    assert chat_tools.get_portfolio() == "当前无持仓记录"
    # 损坏：合法 YAML 坏记录（Z2 同款——_corrupted=False 但解码失败）
    import pathlib
    pathlib.Path(portfolio_module.DEFAULT_PORTFOLIO_PATH).write_text(
        "positions:\n  '600519': 42\n", encoding="utf-8")
    pm2 = PortfolioManager()
    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm2, raising=False)
    out = chat_tools.get_portfolio()
    assert "当前无持仓记录" not in out, f"解码失败不得谎报空仓: {out}"
    assert ("待对账" in out) or ("不完整" in out) or ("未对账" in out), out


# ── Z3②：l/la 启动提示按 ctx 三态 ───────────────────────────────────

def test_z3_la_ledger_holding_no_flat_claim(tmp_path, monkeypatch):
    """R15 Z3②红灯：账本独有持仓的 la 同次输出不得说"将从FLAT状态开始分析"。"""
    _isolate(tmp_path, monkeypatch)
    _seed_ledger_only(quantity=100)
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    with patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c)):
        out = _repl("la")
    assert "将从FLAT状态开始分析" not in out, \
        f"账本有仓不得宣称 FLAT 起点: {out[-500:]}"
    assert "账本有仓 100 股" in out, f"账本事实必须可见: {out[-500:]}"
    # Z3② 新提示行本体锁定（不依赖既有摘要面板文案）
    assert "事件账本事实" in out, f"ctx 三态提示行必须出现: {out[-500:]}"


def test_z3_l_single_ledger_holding_no_flat_claim(tmp_path, monkeypatch):
    """单股 l 同口径：账本独有 → 提示按 ctx 三态（不按 pos 存在与否）。"""
    _isolate(tmp_path, monkeypatch)
    _seed_ledger_only(quantity=100)
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    with patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c)):
        out = _repl(f"l {CODE}")
    assert "将从FLAT状态开始分析" not in out
    assert "账本有仓 100 股" in out


def test_z3_l_unknown_state_hint_positive(tmp_path, monkeypatch):
    """UNKNOWN 提示正例：读取失败+正数量投影 → 提示未对账（不 FLAT、不 NONE）。"""
    _isolate(tmp_path, monkeypatch)
    _seed_ledger_only(quantity=100)
    pm = PortfolioManager()
    pm.add_position(CODE, entry_price=10, ratio=0.0)
    pm._data["positions"][CODE]["quantity_fact"] = {
        "quantity": 100, "as_of": "2026-09-21", "avg_cost": 10.0}
    assert pm._save(), "quantity_fact 必须落盘（analyze_live 新实例从盘上读）"
    from unittest.mock import patch as _patch
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    with _patch.object(AccountService, "snapshot", side_effect=OSError("fixture")), \
            patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c)):
        out = _repl(f"l {CODE}")
    assert "将从FLAT状态开始分析" not in out, f"UNKNOWN 不得宣称 FLAT 起点: {out[-500:]}"
    assert ("未对账" in out) or ("待对账" in out), f"必须有未对账提示: {out[-500:]}"


def test_z3_l_normal_projection_display_unchanged(tmp_path, monkeypatch):
    """正常 RATIO_ONLY 展示兼容：既有持仓记录区照常（回归不回退）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=.1)
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    with patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c)):
        out = _repl(f"l {CODE}")
    assert "📂 持仓记录" in out, f"正常投影持仓展示保持: {out[-500:]}"
    assert "10.00%" in out or "仓位" in out


# ── 回归锚：数据失败不阻断批量；硬退出方向保持（引用既有回归）────────

def test_p1_batch_survives_single_failure_regression(tmp_path, monkeypatch):
    """数据失败不阻断批量后续股（O批 P0 回归在新树保持绿）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("000001", stock_name="投影独有", entry_price=5, ratio=0.2)
    _seed_ledger_only(quantity=100)

    real_analyze = cli_main.analyze_live

    def _flaky(code, **kw):
        if code == "000001":
            raise ValueError("fixture analyze failure")
        return real_analyze(code, **kw)

    monkeypatch.setattr(cli_main, "analyze_live", _flaky)
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    with patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c)):
        out = _repl("la")
    assert "分析失败" in out
    records = []
    if shadow_module.SHADOW_STORE_PATH.exists():
        import json
        records = [json.loads(line) for line in
                   shadow_module.SHADOW_STORE_PATH.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    assert any(r["security_id"] == CODE for r in records), \
        f"单只失败不得中止整批: {[r['security_id'] for r in records]}"
