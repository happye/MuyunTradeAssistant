"""T04-C 服务单测：prepare_trade_plan_draft / activate_overweight_checked。"""
import dataclasses
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.trade_plan.draft_prep import (
    TradePlanDraftOutcome, prepare_trade_plan_draft,
    TP_DATA_UNAVAILABLE, TP_GENERATE_FAILED, TP_READY,
)
from src.data.models import TradePlan
from src.data.portfolio import (
    PortfolioManager, activate_overweight_checked,
    OW_MISSING_CODE, OW_NOT_HELD, OW_NO_PLAN, OW_ALREADY_EXECUTED,
    OW_RISE_REJECTED, OW_SAVE_FAILED, OW_ACTIVATED, SaveResult,
)

CODE = "600519"


def _plan(**kw):
    base = dict(
        plan_id="p1", opened_at="2026-10-21", why_buy="w", when_buy="t",
        how_much=0.2, when_sell_targets=[10.0], when_sell_invalidate=["x"],
        locked_initial_stop=9.0, current_stop=9.0, max_hold_days=60,
        fundamental_outlook="neutral",
    )
    base.update(kw)
    return TradePlan(**base)


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(str(tmp_path / "portfolio.yaml"), str(tmp_path / "proposals.json"))


def test_draft_outcome_frozen():
    oc = TradePlanDraftOutcome(status=TP_DATA_UNAVAILABLE, stock_code=CODE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        oc.status = "X"


def test_prepare_data_unavailable_on_none():
    oc = prepare_trade_plan_draft(
        CODE, "茅台", 100.0, 0.2,
        get_stock_data=lambda c: None,
        auto_score_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not score")),
    )
    assert oc.status == TP_DATA_UNAVAILABLE and oc.plan is None


def test_prepare_data_unavailable_on_exception():
    def boom(c):
        raise ConnectionError("down")
    oc = prepare_trade_plan_draft(CODE, "茅台", 100.0, 0.2, get_stock_data=boom)
    assert oc.status == TP_DATA_UNAVAILABLE
    assert "ConnectionError" in oc.data_error


def test_prepare_ready_with_stubs():
    sd = SimpleNamespace(atr_14=1.5, ma20=10.0, ma60=9.0, price=11.0, change_pct=1.0,
                         macd=None, low_60d=8.0)

    class FakeScore:
        industry_prosperity = 70.0
        dimensions_meta = {"industry_prosperity": {"sources": ["行业: 白酒"]}}
        def effective_grade(self):
            return "A"
        def normalized_score(self):
            return 88.0

    class FakeBz:
        score = FakeScore()
        is_self_reliance = False
        flagbearer_code = None
        penetration_stage = None
        overall_confidence = 0.9

    plan = _plan()

    class FakeGen:
        def generate(self, **kw):
            return plan, {"fallback_reasons": ["AI Modifier 未配置 — thesis 走规则模板"]}

    oc = prepare_trade_plan_draft(
        CODE, "茅台", 100.0, 0.2,
        get_stock_data=lambda c: sd,
        auto_score_fn=lambda *a, **k: FakeBz(),
        get_rag_service=lambda: None,
        generator_factory=lambda rag: FakeGen(),
    )
    assert oc.status == TP_READY
    assert oc.plan is plan
    assert oc.benzong_grade == "A"
    assert oc.atr_str == "1.50" and oc.ma20_str == "10.00"
    assert oc.industry_sources == ("行业: 白酒",)
    assert oc.score_normalized == 88.0


def test_prepare_generate_failed():
    sd = SimpleNamespace(atr_14=1.0, ma20=1.0, ma60=1.0, price=1.0, change_pct=0,
                         macd=None, low_60d=1.0)

    class BoomGen:
        def generate(self, **kw):
            raise RuntimeError("gen boom")

    oc = prepare_trade_plan_draft(
        CODE, "x", 10.0, 0.2,
        get_stock_data=lambda c: sd,
        auto_score_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("score fail")),
        get_rag_service=lambda: None,
        generator_factory=lambda rag: BoomGen(),
    )
    assert oc.status == TP_GENERATE_FAILED
    assert oc.score_failed is True
    assert "gen boom" in oc.generate_error


def test_prepare_skip_benzong():
    sd = SimpleNamespace(atr_14=1.0, ma20=1.0, ma60=1.0, price=1.0, change_pct=0,
                         macd=None, low_60d=1.0)
    plan = _plan()

    class FakeGen:
        def generate(self, **kw):
            assert kw["benzong_grade"] == "B"
            return plan, {}

    called = {"score": False}

    def score(*a, **k):
        called["score"] = True
        raise AssertionError("should skip")

    oc = prepare_trade_plan_draft(
        CODE, "x", 10.0, 0.2,
        get_stock_data=lambda c: sd,
        auto_score_fn=score,
        get_rag_service=lambda: None,
        generator_factory=lambda rag: FakeGen(),
        skip_benzong=True,
        benzong_grade="B",
        industry_prosperity=50.0,
    )
    assert oc.status == TP_READY and called["score"] is False


# ── overweight ────────────────────────────────────────────

def test_ow_missing_and_not_held(pm):
    assert activate_overweight_checked(pm, "").status == OW_MISSING_CODE
    assert activate_overweight_checked(pm, CODE).status == OW_NOT_HELD


def test_ow_no_plan(pm):
    assert pm.add_position(CODE, stock_name="茅台", entry_price=100, ratio=0.2)
    assert activate_overweight_checked(pm, CODE).status == OW_NO_PLAN


def test_ow_already_executed(pm):
    assert pm.add_position(CODE, stock_name="茅台", entry_price=100, ratio=0.2)
    plan = _plan(overweight_executed=True, overweight_expiry="2026-12-01", overweight_basis="旧")
    assert pm.attach_plan(CODE, plan)
    oc = activate_overweight_checked(pm, CODE)
    assert oc.status == OW_ALREADY_EXECUTED and oc.expiry == "2026-12-01"


def test_ow_rise_rejected(pm):
    assert pm.add_position(CODE, stock_name="茅台", entry_price=100, ratio=0.2)
    assert pm.attach_plan(CODE, _plan())
    sd = SimpleNamespace(price=40.0, low_60d=10.0)
    oc = activate_overweight_checked(pm, CODE, get_stock_data=lambda c: sd)
    assert oc.status == OW_RISE_REJECTED and oc.multiple == pytest.approx(4.0)
    assert pm.get_position(CODE).trade_plan.overweight_executed is not True


def test_ow_activated(pm):
    assert pm.add_position(CODE, stock_name="茅台", entry_price=100, ratio=0.2)
    assert pm.attach_plan(CODE, _plan())
    sd = SimpleNamespace(price=15.0, low_60d=10.0)
    now = datetime(2026, 10, 21)
    oc = activate_overweight_checked(
        pm, CODE, basis="事件A", get_stock_data=lambda c: sd, now=now)
    assert oc.status == OW_ACTIVATED
    assert oc.multiple == pytest.approx(1.5)
    assert oc.basis == "事件A"
    assert oc.expiry == "2026-12-05"  # +45d
    reloaded = PortfolioManager(pm.portfolio_path, str(pm._proposals.path))
    assert reloaded.get_position(CODE).trade_plan.overweight_executed is True


def test_ow_save_failed(pm, monkeypatch):
    assert pm.add_position(CODE, stock_name="茅台", entry_price=100, ratio=0.2)
    assert pm.attach_plan(CODE, _plan())
    monkeypatch.setattr(PortfolioManager, "_save", lambda self: SaveResult(False))
    sd = SimpleNamespace(price=15.0, low_60d=10.0)
    oc = activate_overweight_checked(pm, CODE, get_stock_data=lambda c: sd)
    assert oc.status == OW_SAVE_FAILED
