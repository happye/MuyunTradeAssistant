"""ISS-116 analyze_industry 共享持仓定位。隔离账户，行业外源全部 mock。"""
import json
from datetime import datetime
from unittest.mock import patch

import pytest

import src.chat.tools as chat_tools
import src.data.account_service as account_module
import src.data.industry_data as industry_data
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
from src.data.account_service import AccountEventLog, AccountService
from src.data.account_snapshot import AccountEvent, EventType
from src.data.portfolio import PortfolioManager

CODE = "002466"
ALIAS = "sh002466"


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")


def _open(code=CODE, quantity=100):
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=100000.0, trade_date="2026-09-20",
        lots=[{"security_id": code, "quantity": quantity, "cost_price": 10.0,
               "acquired_at": "2026-09-20"}])


def _ghost_sell():
    AccountEventLog(account_module.DEFAULT_LEDGER_PATH).append(AccountEvent(
        event_id="ghost", event_type=EventType.SELL, security_id="600000",
        trade_date="2026-09-27", quantity=100, price=10, cash_delta=1000))


def _run(monkeypatch, pm):
    calls = {"facts": 0, "chain": 0, "board": 0}
    seen = []
    real_facts = PortfolioManager.request_account_facts
    real_chain = industry_data.format_chain_graph
    real_board = industry_data._board_section
    real_report = industry_data.build_industry_report

    def facts(self):
        calls["facts"] += 1
        return real_facts(self)

    def chain(*args, **kwargs):
        calls["chain"] += 1
        return real_chain(*args, **kwargs)

    def board(*args, **kwargs):
        calls["board"] += 1
        return real_board(*args, **kwargs)

    def report(query, positions=None, scanner_engine=None):
        seen.append([dict(p) for p in (positions or [])])
        return real_report(query, positions, scanner_engine)

    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm, raising=False)
    monkeypatch.setattr(PortfolioManager, "request_account_facts", facts)
    monkeypatch.setattr(industry_data, "format_chain_graph", chain)
    monkeypatch.setattr(industry_data, "graph_source", lambda name: "auto")
    monkeypatch.setattr(industry_data, "_resolve_board",
                        lambda query, engine: ("锂", "概念", [CODE, "000001"]))
    monkeypatch.setattr(industry_data, "_board_section", board)
    monkeypatch.setattr(industry_data, "get_commodity_section", lambda cfg: "商品停用")
    monkeypatch.setattr(industry_data, "get_generic_commodity_section", lambda q: "商品停用")
    monkeypatch.setattr(industry_data, "get_demand_section", lambda cfg, **k: "需求停用")
    monkeypatch.setattr(industry_data, "generic_demand_section", lambda q: "需求停用")
    monkeypatch.setattr(industry_data, "format_macro", lambda: "宏观停用")
    monkeypatch.setattr(industry_data, "build_industry_report", report)
    text = chat_tools.analyze_industry("锂电")
    return text, seen, calls


def test_ratio_only_keeps_original_weight(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="天齐锂业", entry_price=10, ratio=0.2)
    before = (tmp_path / "portfolio.yaml").read_bytes()
    text, seen, calls = _run(monkeypatch, pm)
    assert calls["facts"] == 1 and calls["chain"] == 1 and calls["board"] == 1
    assert seen[0] == [{"stock_code": CODE, "current_ratio": 0.2}]
    assert "仓位20%" in text and "账户待核" not in text
    assert (tmp_path / "portfolio.yaml").read_bytes() == before
    text2, seen2, calls2 = _run(monkeypatch, pm)
    assert calls2["facts"] == 1


def test_ledger_alias_located_once_without_old_percent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(ALIAS, stock_name="别名", entry_price=10, ratio=0.35)
    _open(quantity=100)
    text, seen, calls = _run(monkeypatch, pm)
    assert calls["chain"] == 1 and calls["board"] == 1
    assert len(seen[0]) == 1 and seen[0][0]["stock_code"] == CODE
    assert seen[0][0]["current_ratio"] is None
    assert text.count(CODE) >= 1 and "35%" not in text
    assert "权重待核" in text


def test_partial_surviving_lot_keeps_holding_and_incomplete(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _open(quantity=80)
    _ghost_sell()
    text, seen, calls = _run(monkeypatch, pm)
    assert calls["facts"] == 1
    assert seen[0][0]["stock_code"] == CODE
    assert seen[0][0]["current_ratio"] is None
    assert "用户持仓" in text and "权重待核" in text and "不完整" in text


def test_snapshot_failure_does_not_invent_weight_or_empty(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="天齐锂业", entry_price=10, ratio=0.2)
    pm.add_position("000001", stock_name="零投影", entry_price=1, ratio=0.0)
    _open(quantity=1)
    with patch.object(AccountService, "snapshot", side_effect=OSError("fixture")):
        text, seen, calls = _run(monkeypatch, pm)
    assert calls["facts"] == 1
    assert seen[0] == [{"stock_code": CODE, "current_ratio": None}]
    assert "仓位20%" not in text and "当前无持仓" not in text
    assert "000001" in text and "账户待核" in text


def test_bad_metadata_keeps_lot_and_empty_states_differ(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _open(quantity=50)
    (tmp_path / "portfolio.yaml").write_text(
        "positions:\n  '000001': 42\n  '600000':\n    stock_code: '600000'\n"
        "    stock_name: 零仓\n    entry_price: 1\n    current_ratio: 0\n",
        encoding="utf-8")
    pm = PortfolioManager()
    text, seen, _ = _run(monkeypatch, pm)
    assert [p["stock_code"] for p in seen[0]] == [CODE]
    holding = text.split("【账户待核】")[0]
    assert "600000" not in holding
    assert "账户待核" in text and "不完整" in text
    (tmp_path / "portfolio.yaml").write_text("positions: {}\n", encoding="utf-8")
    account_module.DEFAULT_LEDGER_PATH.unlink()
    healthy, _, _ = _run(monkeypatch, PortfolioManager())
    assert "账户待核" not in healthy
    (tmp_path / "portfolio.yaml").write_text("positions: {}\n", encoding="utf-8")
    _ghost_sell()
    broken, _, _ = _run(monkeypatch, PortfolioManager())
    assert "账户待核" in broken and "不能据此称为空仓" in broken
    assert healthy != broken
