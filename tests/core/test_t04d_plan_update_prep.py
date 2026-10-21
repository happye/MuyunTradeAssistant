"""T04-D 服务单测：prepare_generate_or_update_plan / accept_plan_update。

无打印、无 input、不写盘；更新预览合并止损且不改原 plan。
"""
import dataclasses
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.trade_plan.plan_update_prep import (
    PlanGenUpdateOutcome, prepare_generate_or_update_plan, accept_plan_update,
    PU_NO_POSITION, PU_DATA_UNAVAILABLE, PU_GENERATE_FAILED,
    PU_READY_NEW, PU_READY_UPDATE,
)
from src.data.models import TradePlan

CODE = "600000"


def _plan(**kw):
    base = dict(
        plan_id="p1", opened_at="2020-01-01", why_buy="w", when_buy="t",
        how_much=0.2, when_sell_targets=[10.0], when_sell_invalidate=["x"],
        locked_initial_stop=8.0, current_stop=9.0, max_hold_days=300,
        fundamental_outlook="neutral", mode="qizong",
    )
    base.update(kw)
    return TradePlan(**base)


def _sd(**kw):
    base = dict(atr_14=1.5, ma20=10.0, ma60=9.0, price=11.0, change_pct=1.0,
                macd=None, low_60d=8.0)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def mgr():
    def make(pos=None):
        return SimpleNamespace(
            get_position=lambda code: pos,
            attach_plan=lambda code, p: (_ for _ in ()).throw(AssertionError("no write")),
        )
    return make


def test_outcome_frozen():
    oc = PlanGenUpdateOutcome(status=PU_NO_POSITION, stock_code=CODE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        oc.status = "X"


def test_no_position(mgr):
    oc = prepare_generate_or_update_plan(mgr(None), CODE, update=True)
    assert oc.status == PU_NO_POSITION and oc.plan is None


def test_data_unavailable_none(mgr):
    pos = SimpleNamespace(trade_plan=None, entry_price=10, current_ratio=0.2, stock_name="x")
    oc = prepare_generate_or_update_plan(
        mgr(pos), CODE, update=False,
        get_stock_data=lambda c: None,
        auto_score_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")),
    )
    assert oc.status == PU_DATA_UNAVAILABLE
    assert oc.action_label == "生成"


def test_data_unavailable_exception(mgr):
    pos = SimpleNamespace(trade_plan=None, entry_price=10, current_ratio=0.2, stock_name="x")

    def boom(c):
        raise ConnectionError("down")

    oc = prepare_generate_or_update_plan(mgr(pos), CODE, get_stock_data=boom)
    assert oc.status == PU_DATA_UNAVAILABLE
    assert "ConnectionError" in oc.data_error


def test_ready_new_runs_score(mgr):
    pos = SimpleNamespace(trade_plan=None, entry_price=10, current_ratio=0.2, stock_name="服装")
    plan = _plan(mode=None, current_stop=8.0)

    class FakeScore:
        industry_prosperity = 70.0
        dimensions_meta = {"industry_prosperity": {"sources": ["行业: 纺服"]}}
        def effective_grade(self):
            return "A"
        def normalized_score(self):
            return 80.0

    class FakeBz:
        score = FakeScore()
        is_self_reliance = False
        flagbearer_code = None
        penetration_stage = None
        overall_confidence = 0.8

    class FakeGen:
        def generate(self, **kw):
            assert kw["benzong_grade"] == "A"
            return plan, {"fallback_reasons": []}

    scored = {"n": 0}

    def score(*a, **k):
        scored["n"] += 1
        return FakeBz()

    oc = prepare_generate_or_update_plan(
        mgr(pos), CODE, update=False,
        get_stock_data=lambda c: _sd(),
        auto_score_fn=score,
        get_rag_service=lambda: None,
        generator_factory=lambda rag: FakeGen(),
    )
    assert oc.status == PU_READY_NEW
    assert oc.plan is plan
    assert scored["n"] == 1
    assert oc.is_update_path is False
    assert oc.score_normalized == 80.0
    assert oc.industry_sources == ("行业: 纺服",)


def test_ready_update_skips_score_and_merges_stop(mgr):
    original = _plan(mode="qizong", max_hold_days=300, current_stop=9.0, locked_initial_stop=8.0)
    pos = SimpleNamespace(
        trade_plan=original, entry_price=10, current_ratio=0.2, stock_name="服装代工")
    gen_plan = _plan(mode="jianzong", max_hold_days=60, current_stop=7.0)  # lower stop ignored

    class FakeGen:
        def generate(self, **kw):
            assert kw["benzong_grade"] == "A"  # from qizong
            assert kw["flagbearer_code"] is None
            return gen_plan, {}

    scored = {"n": 0}

    def score(*a, **k):
        scored["n"] += 1
        raise AssertionError("update must skip score")

    oc = prepare_generate_or_update_plan(
        mgr(pos), CODE, update=True,
        get_stock_data=lambda c: _sd(price=7, ma20=8, ma60=9),
        auto_score_fn=score,
        get_rag_service=lambda: None,
        generator_factory=lambda rag: FakeGen(),
        bz_cache_get=lambda *a, **k: None,
    )
    assert oc.status == PU_READY_UPDATE
    assert scored["n"] == 0
    assert oc.is_update_path is True
    assert oc.reused_mode == "qizong"
    assert oc.prosperity_note == "unknown"
    # 原 plan 未被改写
    assert original.current_stop == 9.0 and original.mode == "qizong"
    assert original.max_hold_days == 300
    # 提案：止损单向上移（max(9, 7)=9），mode/期限保持旧
    assert oc.plan is not original
    assert oc.plan.mode == "qizong" and oc.plan.max_hold_days == 300
    assert oc.plan.locked_initial_stop == 8.0
    assert oc.old_stop == 9.0 and oc.proposed_stop == 9.0
    assert oc.plan.current_stop == 9.0
    assert oc.plan.adjustments == original.adjustments or oc.plan.adjustments == []


def test_ready_update_raises_stop_when_new_higher(mgr):
    original = _plan(current_stop=9.0)
    pos = SimpleNamespace(
        trade_plan=original, entry_price=10, current_ratio=0.2, stock_name="x")
    gen_plan = _plan(current_stop=12.5)

    class FakeGen:
        def generate(self, **kw):
            return gen_plan, {}

    oc = prepare_generate_or_update_plan(
        mgr(pos), CODE, update=True,
        get_stock_data=lambda c: _sd(),
        auto_score_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")),
        get_rag_service=lambda: None,
        generator_factory=lambda rag: FakeGen(),
        bz_cache_get=lambda *a, **k: {"score": 55.0},
    )
    assert oc.status == PU_READY_UPDATE
    assert oc.proposed_stop == 12.5
    assert oc.plan.current_stop == 12.5
    assert original.current_stop == 9.0
    assert oc.prosperity_from_cache is True
    assert oc.prosperity_note == "cache"
    assert oc.industry_prosperity == 55.0


def test_accept_plan_update_appends_adjustment():
    plan = _plan(current_stop=12.5)
    out = accept_plan_update(plan, old_stop=9.0, proposed_stop=12.5,
                             now=datetime(2026, 10, 21))
    assert out is plan
    assert plan.adjustments[-1]["source"] == "user"
    assert plan.adjustments[-1]["old"] == 9.0
    assert plan.adjustments[-1]["new"] == 12.5
    assert plan.adjustments[-1]["date"] == "2026-10-21"
    assert "明确接受" in plan.adjustments[-1]["reason"]


def test_generate_failed(mgr):
    pos = SimpleNamespace(trade_plan=None, entry_price=10, current_ratio=0.2, stock_name="x")

    class BoomGen:
        def generate(self, **kw):
            raise RuntimeError("gen boom")

    oc = prepare_generate_or_update_plan(
        mgr(pos), CODE,
        get_stock_data=lambda c: _sd(),
        auto_score_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("score")),
        get_rag_service=lambda: None,
        generator_factory=lambda rag: BoomGen(),
    )
    assert oc.status == PU_GENERATE_FAILED
    assert "gen boom" in oc.generate_error
    assert oc.score_failed is True


def test_update_false_with_existing_plan_is_new_generate(mgr):
    """update=False 即使有旧计划也走生成路径（调用方 normally 只查看不进本函数）。"""
    original = _plan()
    pos = SimpleNamespace(
        trade_plan=original, entry_price=10, current_ratio=0.2, stock_name="x")
    new_plan = _plan(plan_id="p2", current_stop=5.0)

    class FakeScore:
        industry_prosperity = 40.0
        dimensions_meta = {}
        def effective_grade(self):
            return "B"
        def normalized_score(self):
            return 50.0

    class FakeBz:
        score = FakeScore()
        is_self_reliance = False
        flagbearer_code = None
        penetration_stage = None
        overall_confidence = 0.5

    class FakeGen:
        def generate(self, **kw):
            return new_plan, {}

    oc = prepare_generate_or_update_plan(
        mgr(pos), CODE, update=False,
        get_stock_data=lambda c: _sd(),
        auto_score_fn=lambda *a, **k: FakeBz(),
        get_rag_service=lambda: None,
        generator_factory=lambda rag: FakeGen(),
    )
    assert oc.status == PU_READY_NEW
    assert oc.plan is new_plan
    assert oc.is_update_path is False
