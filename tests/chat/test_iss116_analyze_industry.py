"""ISS-116 analyze_industry 共享持仓定位。隔离账户，行业外源全部 mock。"""
import json
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace

import pytest
import pandas as pd

import src.chat.tools as chat_tools
from src.chat.agent import ChatAgent
import src.data.account_service as account_module
import src.data.industry_data as industry_data
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
from src.data.account_service import AccountEventLog, AccountService
from src.data.account_snapshot import AccountEvent, EventType
from src.data.portfolio import PortfolioManager

CODE = "002466"
ALIAS = "sh002466"
REQUEST_FACTS = PortfolioManager.request_account_facts


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
    calls = {"facts": 0, "chain": 0, "board": 0, "market": 0}
    seen = []
    real_chain = industry_data.format_chain_graph
    real_board = industry_data._board_section
    real_report = industry_data.build_industry_report

    def facts(self):
        calls["facts"] += 1
        return REQUEST_FACTS(self)

    def chain(*args, **kwargs):
        calls["chain"] += 1
        rendered = real_chain(*args, **kwargs)
        calls["chain_text"] = rendered
        return rendered

    def board(*args, **kwargs):
        calls["board"] += 1
        rendered = real_board(*args, **kwargs)
        calls["board_text"] = rendered[0]
        return rendered

    def market():
        calls["market"] += 1
        return pd.DataFrame({"代码": [CODE, "000001"]})

    def report(query, positions=None, scanner_engine=None):
        seen.append([dict(p) for p in (positions or [])])
        return real_report(query, positions, scanner_engine)

    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm, raising=False)
    monkeypatch.setattr(chat_tools, "_scanner_engine", SimpleNamespace(
        market_cache=SimpleNamespace(get_all_stocks=market)))
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
    assert calls["market"] == 1
    assert "产业链结构" in calls["chain_text"]
    assert "行业板块解析" in calls["board_text"]
    assert "产业链结构" in text and "行业板块解析" in text
    for position in seen[0]:
        if position["stock_code"] == CODE:
            assert f"{CODE} 天齐锂业" in calls["chain_text"]
            assert f"用户持仓在该板块内: {CODE}" in calls["board_text"]
            if position["current_ratio"] is None:
                assert "仓位" not in calls["chain_text"]
                assert "仓位" not in calls["board_text"]
            else:
                weight = f"仓位{position['current_ratio'] * 100:.0f}%"
                assert weight in calls["chain_text"] and weight in calls["board_text"]
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
    pm.add_position(CODE, stock_name="天齐锂业", entry_price=10, ratio=0.4)
    text2, seen2, calls2 = _run(monkeypatch, pm)
    assert calls2["facts"] == 1
    assert seen2[0] == [{"stock_code": CODE, "current_ratio": 0.4}]
    assert "仓位40%" in text2 and "仓位20%" not in text2


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
    holding = text.split("产业链结构", 1)[-1]
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


def test_long_report_survives_real_agent_with_account_summary(monkeypatch):
    calls = []
    reason = "snapshot missing; cannot prove account holding or weight"
    facts = SimpleNamespace(
        _read_failed=False,
        holding_entries=lambda: ([SimpleNamespace(stock_code=f"{i:06d}")
                                  for i in range(100)], True),
        context_for=lambda code: SimpleNamespace(position_state="UNKNOWN", weight_reason=reason))

    def request():
        calls.append(1)
        return facts

    monkeypatch.setattr(chat_tools, "_portfolio_manager", SimpleNamespace(request_account_facts=request))
    monkeypatch.setattr(industry_data, "build_industry_report", lambda *a, **k: "INDUSTRY" * 800)
    agent = ChatAgent.__new__(ChatAgent)
    agent.max_result_length = 4000
    delivered = agent._execute_tool("analyze_industry", {"industry": "synthetic"})
    assert len(calls) == 1
    assert len(delivered) <= 4000
    assert "清单不完整" in delivered
    assert "UNKNOWN 100只" in delivered
    assert "000000" in delivered and "000099" in delivered
    assert "明细省略94只" in delivered
    assert delivered.count(reason) == 1
    assert "原因1" in delivered and "100只" in delivered
    assert "行业正文已截短" in delivered
    assert "get_portfolio" in delivered


def test_summary_detail_entry_is_registered_and_readonly(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="天齐锂业", entry_price=10, ratio=0.2)
    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm)
    before = (tmp_path / "portfolio.yaml").read_bytes()
    agent = ChatAgent.__new__(ChatAgent)
    agent.max_result_length = 4000
    result = agent._execute_tool("get_portfolio", {})
    assert CODE in result and "20%" in result
    assert (tmp_path / "portfolio.yaml").read_bytes() == before


def test_mixed_long_reasons_have_exact_counts_and_bounded_output(monkeypatch):
    report_positions = []
    calls = []

    def context(code):
        i = int(code)
        return SimpleNamespace(position_state="UNKNOWN" if i < 50 else "HELD",
                               confirmed_weight=None, weight_reason=f"issue-{i}:" + "x" * 500)

    def request():
        calls.append(1)
        return SimpleNamespace(_read_failed=False,
            holding_entries=lambda: ([SimpleNamespace(stock_code=f"{i:06d}") for i in range(100)], True),
            context_for=context)

    def report(query, positions, **kwargs):
        report_positions.extend(positions)
        return "行业正文" * 1400

    monkeypatch.setattr(chat_tools, "_portfolio_manager", SimpleNamespace(request_account_facts=request))
    monkeypatch.setattr(industry_data, "build_industry_report", report)
    agent = ChatAgent.__new__(ChatAgent)
    agent.max_result_length = 4000
    result = agent._execute_tool("analyze_industry", {"industry": "synthetic"})
    assert len(calls) == 1 and len(result) <= 4000
    assert "UNKNOWN 50只" in result and "权重待核 50只" in result
    assert "明细省略94只" in result and "不同原因共100类，原因省略96类" in result
    assert "原因已截短" in result and "行业正文已截短" in result
    assert "000000 UNKNOWN" in result and "000099 权重待核" in result
    assert [p["stock_code"] for p in report_positions] == [f"{i:06d}" for i in range(50, 100)]
    assert all(p["current_ratio"] is None for p in report_positions)


@pytest.mark.parametrize("report_failure", [False, True])
def test_assembly_failure_keeps_qualification_and_tool_failure_marker(monkeypatch, report_failure):
    def request():
        raise OSError("account fixture " + "x" * 5000)

    def report(*args, **kwargs):
        if report_failure:
            raise OSError("industry fixture")
        return "行业正文" * 1400

    monkeypatch.setattr(chat_tools, "_portfolio_manager", SimpleNamespace(request_account_facts=request))
    monkeypatch.setattr(industry_data, "build_industry_report", report)
    agent = ChatAgent.__new__(ChatAgent)
    agent.max_result_length = 4000
    result = agent._execute_tool("analyze_industry", {"industry": "synthetic"})
    assert len(result) <= 4000
    assert "清单不完整" in result and "遗漏数量无法核定" in result
    assert "不能据此称为空仓" in result and "原因已截短" in result
    assert result.startswith(chat_tools.TOOL_ERROR_MARK) == report_failure


def test_healthy_industry_text_retains_existing_length_limit(monkeypatch):
    monkeypatch.setattr(chat_tools, "_portfolio_manager", SimpleNamespace(
        request_account_facts=lambda: SimpleNamespace(_read_failed=False,
            holding_entries=lambda: ([], False))))
    monkeypatch.setattr(industry_data, "build_industry_report", lambda *a, **k: "x" * 5600)
    agent = ChatAgent.__new__(ChatAgent)
    agent.max_result_length = 4000
    result = agent._execute_tool("analyze_industry", {"industry": "synthetic"})
    assert len(result) == 3500 and "行业正文已截短" in result
    assert "账户待核" not in result
