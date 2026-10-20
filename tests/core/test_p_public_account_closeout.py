"""Normalized holdings and independent ledger exits reach public l/la/chat consumers."""
import contextlib
import json
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

import test_o0_exceptional_account as o0
import test_m0_account_terminal as m0
from src.chat import tools as chat
from src.cli import main as cli
from src.data.models import DecisionResult, MarketState, SignalType
import start


def test_chat_normalized_aliases_are_rendered_once(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    pm._data["positions"]["sh600519"] = dict(pm._data["positions"][o0.CODE])
    facts = pm.request_account_facts()
    assert len(facts.holding_entries()[0]) == 1
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    text = chat.get_portfolio()
    assert text.count(o0.CODE) == 1


def test_chat_unknown_weight_is_not_old_projection_percentage(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    o0._seed_ledger_only(quantity=100)
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    text = chat.get_portfolio()
    assert "权重待对账" in text and "100股" in text
    assert "10%" not in text


def test_chat_overlapping_flat_projection_cannot_claim_flat(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=0)
    pm._data["positions"][o0.CODE]["lifecycle"] = "FLAT"
    o0._seed_ledger_only(quantity=100)
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    text = chat.get_portfolio()
    assert "100股" in text and "生命周期:FLAT" not in text


@pytest.mark.parametrize("command", ["l", "la", "chat"])
def test_bad_projection_ledger_exit_reaches_public_consumer(tmp_path, monkeypatch, command):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    path = tmp_path / "portfolio.yaml"
    path.write_text("positions:\n  '600519': 42\n", encoding="utf-8")
    pm = o0.PortfolioManager()
    monkeypatch.setattr(cli, "PortfolioManager", lambda: pm)
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    stock = o0._stub_market(o0.CODE)
    calls = []

    class Engine:
        def analyze(self, data, **kwargs):
            calls.append(kwargs)
            assert kwargs["has_position"] is True
            assert kwargs["strategy_state"].current_position_ratio is None
            dr = DecisionResult(stock=data, state=MarketState.TRANSITION, decision=SignalType.SELL,
                                score=.8, data_gaps=[])
            return dr, m0._sd("CLOSE_ALL", 0, "fundamental_alert"), o0._eval(), None

    monkeypatch.setattr(cli, "build_live_orchestrator", lambda *a, **kw: Engine())
    monkeypatch.setattr(cli, "_cli_rag", lambda: None)
    monkeypatch.setattr(cli, "_print_benzong_summary", lambda *a: None)
    monkeypatch.setattr(cli, "_cached_benzong_brief", lambda *a: None)
    monkeypatch.setattr(cli, "_watch_pool_touch", lambda *a: None)
    monkeypatch.setattr(o0.akshare_module, "get_stock_data", lambda c: stock)
    out = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=240, color_system=None))
    if command == "chat":
        monkeypatch.setattr(chat, "_orchestrator", Engine())
        monkeypatch.setattr(chat, "_get_stock_data_with_timeout", lambda c: stock)
        text = chat.analyze_stock(o0.CODE)
    elif command == "la":
        parsed = start.parse_input("la")
        with contextlib.redirect_stdout(out):
            start.run_cli(*parsed)
        text = out.getvalue()
    else:
        parsed = start.parse_input("l " + o0.CODE)
        with contextlib.redirect_stdout(out):
            start.run_cli(*parsed)
        text = out.getvalue()
    assert len(calls) == 1
    assert "退出" in text or "清仓" in text
    assert "将从FLAT状态开始分析" not in text
    assert path.read_text(encoding="utf-8") == "positions:\n  '600519': 42\n"
    evidence = [json.loads(line) for line in (tmp_path / "analysis_evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert evidence[-1]["desired_action"] == "EXIT"
    shadow = [json.loads(line) for line in (tmp_path / "shadow_diff.jsonl").read_text(encoding="utf-8").splitlines()]
    assert shadow[-1]["legacy_desired"] == "EXIT"


@pytest.mark.parametrize("command", ["l", "la", "chat"])
def test_alias_only_projection_keeps_observation_storage_identity(tmp_path, monkeypatch, command):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position("sh600519", entry_price=10, ratio=.1)
    o0._seed_ledger_only(quantity=100)
    stock = o0._stub_market(o0.CODE)
    calls = []

    class Engine:
        def analyze(self, data, **kwargs):
            assert kwargs["has_position"]
            dr = DecisionResult(stock=data, state=MarketState.TRANSITION,
                                decision=SignalType.HOLD, score=.5, data_gaps=[])
            final = m0._sd("HOLD_POSITION", .1)
            final.new_state.recent_signals = ["HOLD"]
            return dr, final, o0._eval(), None

    original = o0.PortfolioManager.record_analysis_observation
    def observe(manager, key, *args, **kwargs):
        calls.append(key)
        return original(manager, key, *args, **kwargs)
    monkeypatch.setattr(o0.PortfolioManager, "record_analysis_observation", observe)
    monkeypatch.setattr(cli, "PortfolioManager", lambda: pm)
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    monkeypatch.setattr(cli, "build_live_orchestrator", lambda *a, **kw: Engine())
    monkeypatch.setattr(cli, "_cli_rag", lambda: None)
    monkeypatch.setattr(cli, "_cached_benzong_brief", lambda *a: None)
    monkeypatch.setattr(cli, "_print_benzong_summary", lambda *a: None)
    monkeypatch.setattr(cli, "_watch_pool_touch", lambda *a: None)
    monkeypatch.setattr(o0.akshare_module, "get_stock_data", lambda c: stock)
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=240))
    if command == "chat":
        monkeypatch.setattr(chat, "_orchestrator", Engine())
        monkeypatch.setattr(chat, "_get_stock_data_with_timeout", lambda c: stock)
        text = chat.analyze_stock(o0.CODE)
        assert chat.TOOL_ERROR_MARK not in text
    else:
        with contextlib.redirect_stdout(output):
            start.run_cli(*start.parse_input("la" if command == "la" else "l " + o0.CODE))
    assert calls == ["sh600519"], "Observation must use the real storage key exactly once"
    pm._load()
    assert set(pm._data["positions"]) == {"sh600519"}
    assert pm.get_position("sh600519").current_ratio == .1
    assert pm.get_position("sh600519").strategy_state["recent_signals"] == ["HOLD"]
