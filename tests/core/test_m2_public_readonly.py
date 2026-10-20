"""Actual REPL l/la consume the local view without mutating its records."""
import contextlib
import json
from io import StringIO
from time import perf_counter

import pytest
from rich.console import Console

import start
import test_m2_readonly_view as m2
import test_o0_exceptional_account as o0
import test_m0_account_terminal as m0
from src.cli import main as cli
from src.core.research_application import ResearchApplicationService
from src.data.models import DecisionResult, MarketState, SignalType
import src.data.horizon_plans as plan_module


@pytest.mark.parametrize("command", ["l 600519", "la"])
@pytest.mark.parametrize("kind", ["accepted", "candidate", "corrupt", "null"])
def test_repl_local_research_preserves_main_and_exit(tmp_path, monkeypatch, command, kind):
    o0._isolate(tmp_path, monkeypatch)
    svc = m2.service(tmp_path)
    plan = m2.seed(svc)
    if kind == "candidate":
        candidate = plan.model_copy(update={"plan_id": "p2_candidate", "intent": "新材料", "accepted_at": None})
        assert svc.plans_store.save_candidate(candidate)
    elif kind == "corrupt":
        svc.plans_store.path.write_text("{bad", encoding="utf-8")
    elif kind == "null":
        data = json.loads(svc.plans_store.path.read_text(encoding="utf-8"))
        data["plans"][f"{o0.CODE}:MID"] = None
        svc.plans_store.path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(plan_module, "PLANS_FILE", svc.plans_store.path)
    before = {p: data for p, data in m2.tree(tmp_path).items()
              if p == "plans.json" or p.startswith("research/")}
    pm = o0.PortfolioManager()
    assert pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    monkeypatch.setattr(cli, "PortfolioManager", lambda: pm)
    stock = o0._stub_market(o0.CODE)
    calls, timings = [], []
    original = ResearchApplicationService.get_daily_view

    def query_spy(self, security_id, *, as_of):
        assert as_of.tzinfo is not None
        then = perf_counter()
        view = original(self, security_id, as_of=as_of)
        timings.append(perf_counter() - then)
        calls.append(view)
        return view

    monkeypatch.setattr(ResearchApplicationService, "get_daily_view", query_spy)
    monkeypatch.setattr(ResearchApplicationService, "run", lambda *a, **kw: pytest.fail("view ran research"))
    import openai
    monkeypatch.setattr(openai.OpenAI, "__init__", lambda *a, **kw: pytest.fail("local view called AI"))

    class Engine:
        def analyze(self, data, **kwargs):
            dr = DecisionResult(stock=data, state=MarketState.TRANSITION,
                                decision=SignalType.SELL, score=.8, data_gaps=[])
            return dr, m0._sd("CLOSE_ALL", 0, "stop_loss_hard"), o0._eval(), None

    monkeypatch.setattr(cli, "build_live_orchestrator", lambda *a, **kw: Engine())
    monkeypatch.setattr(cli, "_cli_rag", lambda: None)
    monkeypatch.setattr(cli, "_print_benzong_summary", lambda *a: None)
    monkeypatch.setattr(cli, "_cached_benzong_brief", lambda *a: None)
    monkeypatch.setattr(cli, "_watch_pool_touch", lambda *a: None)
    monkeypatch.setattr(o0.akshare_module, "get_stock_data", lambda c: stock)
    out = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=240, color_system=None))
    # Two commands reconstruct the query service, rather than reuse an in-memory store.
    for _ in range(2):
        with contextlib.redirect_stdout(out):
            start.run_cli(*start.parse_input(command))
    text = out.getvalue()
    assert len(calls) == 2
    assert "研究状态（本地只读）" in text
    assert "退出" in text and "成交资格待确认" in text
    assert "不新增交易授权" in text
    if kind in ("corrupt", "null"):
        assert all(view["store_status"] == "CORRUPT" for view in calls)
        assert "记录损坏，待复核" in text and "尚无记录" not in text
        if kind == "null":
            assert "中期：记录待核" in text and "中期：尚未建立研究" not in text
    else:
        for view in calls:
            mid = view["horizons"]["MID"]
            assert mid["accepted"] and mid["plan_id"] == plan.plan_id
            assert mid["content_hash"] == plan.content_hash()
        assert "主版本：" in text and plan.content_hash() in text
        if kind == "candidate":
            assert "候选草稿" in text and "不替换主版本" in text
    after = {p: data for p, data in m2.tree(tmp_path).items()
             if p == "plans.json" or p.startswith("research/")}
    assert after == before
    evidence = [json.loads(line) for line in (tmp_path / "analysis_evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(evidence) == 2 and all(row["desired_action"] == "EXIT" for row in evidence)
    print("M2_LOCAL_QUERY_MS", kind, command, [round(t * 1000, 3) for t in timings])


def test_query_exception_is_visible_and_not_propagated(monkeypatch):
    class Unavailable:
        def get_daily_view(self, *a, **kw):
            raise OSError("isolated read failure")
    out = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, color_system=None))
    assert cli._print_daily_research_view(o0.CODE, app=Unavailable()) is None
    assert "读取失败，待复核" in out.getvalue()
    assert "独立保护保留" in out.getvalue()
