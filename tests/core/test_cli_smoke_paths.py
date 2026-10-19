"""CLI 冒烟回归：l / la / today / pipeline 真实入口（零网络、零 AI）。

背景：G3 重构（9d5651b）让 main.py 调用了 RequestAccountFacts.projection_for /
storage_key_for、AccountContext.trade_plan，today 传 account_facts=，但实现未落地，
l/la/today 一跑即 AttributeError/TypeError。本文件锁住这些入口能跑通：
- 真实 parse_input → run_cli（行情替身；socket 连接一律拒绝，保证不联网）
- 持久化路径全量重定向到 tmp_path（同 M0 _isolate 纪律）
跑法：pytest tests/core/test_cli_smoke_paths.py -q
"""
import importlib.util
import io
import socket
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from rich.console import Console

import src.cli.main as cli_main
import src.core.shadow_diff as shadow_module
import src.data.account_service as account_module
import src.data.akshare_client as akshare_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.data.models import StockData
from src.data.portfolio import PortfolioManager

ROOT = Path(__file__).resolve().parents[2]
CODE = "600519"
PLAN = {"plan_id": f"{CODE}_2026-10-01", "opened_at": "2026-10-01", "why_buy": "冒烟测试",
        "when_buy": "冒烟测试", "how_much": 0.1, "when_sell_targets": [13.0],
        "when_sell_invalidate": ["跌破初始止损价"], "locked_initial_stop": 9.0,
        "current_stop": 9.0, "max_hold_days": 30}
BROKEN = ("AttributeError", "unexpected keyword", "TypeError", "NameError", "Traceback")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    monkeypatch.setattr(shadow_module, "SHADOW_STORE_PATH", tmp_path / "shadow_diff.jsonl")
    import src.cli.evidence as evidence_module
    import src.data.horizon_plans as horizon_plans_module
    import src.data.research_store as research_store_module
    monkeypatch.setattr(evidence_module, "_EVIDENCE_FILE", tmp_path / "analysis_evidence.jsonl")
    monkeypatch.setattr(evidence_module, "_REPORT_DIR", tmp_path / "cards")
    monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", tmp_path / "horizon_plans.json")
    monkeypatch.setattr(research_store_module, "RESEARCH_DIR", tmp_path / "research")
    for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "MUYUN_DATA_SHIM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MUYUN_CLI_RAG", "0")   # RAG 嵌入模型加载在断网下会长时间重试
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    def _no_net(*a, **k):
        raise OSError("network disabled in smoke test")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    monkeypatch.setattr(socket, "create_connection", _no_net)
    # 投影键用历史形态 <代码>.SH：验证 storage_key_for 回写原记录而非新建重复键
    (tmp_path / "portfolio.yaml").write_text(yaml.safe_dump({"positions": {
        f"{CODE}.SH": {"stock_name": "测试股", "lifecycle": "HOLD", "current_ratio": 0.1,
                       "entry_price": 10.0, "high_since_entry": 11.0, "trade_plan": PLAN},
    }}, allow_unicode=True), encoding="utf-8")
    return tmp_path


def _stub(code):
    return StockData(stock_code=code, stock_name="测试股", price=11.0, volume=100000,
                     change_pct=0.5, avg_volume_20=100000.0, ma5=51.0, ma20=50.0, ma60=48.0,
                     quote_as_of="2026-10-19")


def _repl(cmd):
    parsed = start_module.parse_input(cmd)
    assert parsed is not None, cmd
    buf = io.StringIO()
    with patch.object(cli_main, "console", Console(file=buf, width=200)), redirect_stdout(buf), \
            patch.object(akshare_module, "get_stock_data", _stub):
        start_module.run_cli(parsed[0], parsed[1])
    return buf.getvalue()


def test_request_facts_projection_and_storage_key(env):
    facts = PortfolioManager().request_account_facts()
    assert facts.storage_key_for(CODE) == f"{CODE}.SH"
    assert facts.storage_key_for("000001") is None
    pos = facts.projection_for(CODE)
    assert pos is not None and pos.raw_storage_key == f"{CODE}.SH"
    ctx = facts.context_for(CODE, price=11.0, price_as_of="2026-10-19")
    assert ctx.position_state == "HELD"
    assert ctx.trade_plan is not None, "AccountContext 必须带独立核定计划"
    assert facts.context_for("000001").trade_plan is None


def test_l_runs(env):
    out = _repl(f"l {CODE}")
    assert not any(b in out for b in BROKEN), out[-1500:]
    assert "持仓记录" in out


def test_la_runs_and_writes_back_to_raw_key(env):
    out = _repl("la")
    assert not any(b in out for b in BROKEN), out[-1500:]
    keys = list((yaml.safe_load((env / "portfolio.yaml").read_text(encoding="utf-8"))
                 .get("positions") or {}).keys())
    assert len(keys) == 1, f"回写不得生成重复记录: {keys}"


def test_today_runs(env):
    out = _repl("today")
    assert not any(b in out for b in BROKEN), out[-1500:]
    assert CODE in out


def test_pipeline_runs(env, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("muyun_pipeline", ROOT / "scripts" / "pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(akshare_module.AKShareClient, "calculate_indicators",
                        staticmethod(lambda code, *a, **k: _stub(code)))
    monkeypatch.setattr(mod, "stage_market", lambda: {"stub": True})
    monkeypatch.setattr(mod, "stage_candidates", lambda *a, **k: ([], {"rule": "stub"}))
    monkeypatch.setattr(mod, "stage_backtest", lambda *a, **k: {})
    out_dir = tmp_path / "pipe"
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "--no-ai", "--nav", "69500", "--out", str(out_dir)])
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.main()
    md = (out_dir / "latest.md").read_text(encoding="utf-8")
    assert CODE in md, md[:1500]
    assert "错误" not in md.split("## 3")[0], md[:1500]


# ── NAV 估值接线：数量账户 + 假行情 → l / today 显示已算权重，不再"权重待核" ──

@pytest.fixture
def qty_env(env, monkeypatch):
    from src.data.account_service import AccountService
    import src.data.account_valuation as val
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-10-16")
    svc.opening_import(lots=[{"security_id": CODE, "quantity": 100, "cost_price": 10.0,
                              "acquired_at": "2026-10-16"}], trade_date="2026-10-16")
    monkeypatch.setenv("MUYUN_NAV_QUOTES", "1")
    monkeypatch.setattr(val, "default_quote_fn",
                        lambda codes: {c: {"price": 11.0, "effective_at": "2026-10-19T14:10:00"} for c in codes})
    return env


def test_l_shows_computed_weight_with_nav(qty_env):
    out = _repl(f"l {CODE}")
    assert not any(b in out for b in BROKEN), out[-1500:]
    assert "权重待核" not in out, out[-2500:]
    # 100×11 / (10000+1100) ≈ 9.9% → 显示 10%
    assert "当前仓位: 10%" in out, out[-2500:]


def test_today_with_nav_runs(qty_env):
    out = _repl("today")
    assert not any(b in out for b in BROKEN), out[-1500:]
    assert CODE in out
