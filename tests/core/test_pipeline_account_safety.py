"""The read-only pipeline must not turn ratio estimates into executable orders."""
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def pipeline(monkeypatch):
    # Import the real script while containing its legacy environment/cwd setup.
    monkeypatch.chdir(Path.cwd())
    monkeypatch.setattr(sys, "path", list(sys.path))
    script = Path(__file__).resolve().parents[2] / "scripts" / "pipeline.py"
    spec = importlib.util.spec_from_file_location("pipeline_account_safety", script)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ):
        spec.loader.exec_module(module)
        yield module


def candidate(code="600001", action="OPEN"):
    return {"code": code, "name": "测试股票", "price": 10, "atr": 1,
            "position_action": action, "target_ratio": .1,
            "decision": "BUY", "score": .7}


def assert_pending(sizing):
    assert sizing["cash_ratio"] is None
    assert sizing["exposure"] is None
    assert sizing["status"] == "UNKNOWN"
    assert "股数/可用现金待核，不提供下单数量" in sizing["note"]
    for plan in sizing["plans"]:
        for key in ("shares", "cost", "stop_2atr", "risk_at_stop", "risk_pct_nav"):
            assert plan[key] is None


def test_unknown_weight_cannot_become_full_cash_and_1000_share_order(pipeline):
    sizing = pipeline.stage_sizing([candidate()], [{"ratio": None}], 100000, {})
    assert_pending(sizing)
    assert sizing["plans"][0]["code"] == "600001"


def test_two_candidates_cannot_reuse_the_same_assumed_cash(pipeline):
    sizing = pipeline.stage_sizing([candidate(), candidate("600002")],
                                   [{"ratio": .9}], 100000, {})
    assert_pending(sizing)
    assert len(sizing["plans"]) == 2


@pytest.mark.parametrize("holdings", [[], [{"error": "failed", "code": "600003"}],
                                       [{"ratio": 0}]])
def test_incomplete_or_ratio_only_roster_does_not_prove_cash(pipeline, holdings):
    assert_pending(pipeline.stage_sizing([candidate()], holdings, 100000, {}))


@pytest.mark.parametrize("ratio,stale,action", [(None, False, "CLOSE_ALL"),
                                               (.6, False, "REDUCE"),
                                               (.6, True, "CLOSE_ALL")])
def test_holdings_do_not_infer_shares_or_sales_from_nav(pipeline, monkeypatch,
                                                     ratio, stale, action):
    position = SimpleNamespace(stock_code="600001", stock_name="测试股票",
                               current_ratio=ratio, ratio_stale=stale,
                               entry_price=8, trade_plan=None)
    pm = SimpleNamespace(list_positions=lambda: [position])
    monkeypatch.setattr(pipeline, "analyze_one", lambda *a, **kw: candidate(action=action))
    row = pipeline.stage_holdings(None, pm, 100000, {}, False)[0]
    assert row["ratio"] == (None if stale else ratio)
    assert row["est_shares"] is None
    assert row.get("suggest_shares_delta") is None
    assert row["quantity_status"] == "UNKNOWN"
    if row["ratio"] is None:
        assert row["over_cap"] is None


@pytest.mark.parametrize("nav_args,target", [([], .1), (["--nav", "69500"], None)])
def test_public_main_writes_pending_json_and_markdown_without_account_claims(
        pipeline, monkeypatch, tmp_path, capsys, nav_args, target):
    from src.chat import tools
    from src.data import portfolio

    position = SimpleNamespace(stock_code="600003", stock_name="原有持仓",
                               current_ratio=None, entry_price=8, trade_plan=None)
    pm = SimpleNamespace(list_positions=lambda: [position])
    monkeypatch.setattr(portfolio, "PortfolioManager", lambda: pm)
    monkeypatch.setattr(tools, "init_engines", lambda cfg: None)
    monkeypatch.setattr(pipeline, "load_config", lambda: {})
    monkeypatch.setattr(pipeline, "stage_market", lambda: {})
    monkeypatch.setattr(pipeline, "analyze_one", lambda *a, **kw: candidate("600003", "CLOSE_ALL"))
    selected = candidate()
    selected["target_ratio"] = target
    monkeypatch.setattr(pipeline, "stage_candidates", lambda *a: ([selected], {}))
    monkeypatch.setattr(pipeline, "stage_backtest", lambda *a: {})
    monkeypatch.setattr(pipeline, "ai_summary", lambda *a: pytest.fail("AI disabled"))
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "--no-ai", "--out", str(tmp_path)] + nav_args)
    pipeline.main()
    report = json.loads(next(tmp_path.glob("pipeline_*.json")).read_text(encoding="utf-8"))
    assert_pending(report["sizing"])
    assert report["account_nav"] is None
    assert report["nav_source"] == "USER_ESTIMATE_OR_DEFAULT"
    assert report["nav"] == (69500 if nav_args else 100000)
    assert report["sizing"]["plans"][0]["target_ratio"] == target
    assert report["holdings"][0]["ratio"] is None
    assert report["holdings"][0]["est_shares"] is None
    md = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "股数/可用现金待核，不提供下单数量" in md
    assert "用户估计/默认" in md
    assert "UNKNOWN" in md
    assert "600001" in md and "测试股票" in md
    assert "600003" in md
    assert "1000 股" not in md and "现金 100.0%" not in md
    assert "整手，T+1" not in md
    assert "已保存" in capsys.readouterr().out


def test_holdings_error_remains_visible_and_does_not_authorize_sizing(pipeline, monkeypatch):
    pm = SimpleNamespace(list_positions=lambda: [SimpleNamespace(stock_code="600003", stock_name="原有持仓")])
    def fail(*args, **kwargs):
        raise RuntimeError("行情/指标获取失败")
    monkeypatch.setattr(pipeline, "analyze_one", fail)
    rows = pipeline.stage_holdings(None, pm, 100000, {}, False)
    assert rows[0]["error"] == "行情/指标获取失败"
    assert_pending(pipeline.stage_sizing([candidate()], rows, 100000, {}))
