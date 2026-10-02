"""R14 replay of R13 contract probes, adapted to the explicit ctx API. Run ONLY through review_test_runner.py.

Assertions describe the required contract; failures are review findings, not
implementation fixes. Synthetic inputs; all persistent paths use pytest tmp_path.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tests/core"))
import test_m0_account_terminal as m0
import src.chat.tools as chat
import src.chat.formatter as formatter
import src.core.analysis_service as analysis
import src.data.account_service as accounts
import src.data.portfolio as portfolio
import src.core.shadow_diff as shadow
from src.data.models import DecisionResult, MarketState, SignalType


def emit(case, **result):
    print("R13_RESULT " + json.dumps({"case": case, **result}, ensure_ascii=False, default=str))


def seed(tmp_path, monkeypatch, projection=True, ratio=0.1):
    m0._isolate(tmp_path, monkeypatch)
    pm = portfolio.PortfolioManager()
    if projection:
        pm.add_position(m0.CODE, entry_price=10, ratio=ratio)
    svc = accounts.AccountService(accounts.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=10000, trade_date="2026-09-20",
                       lots=[{"security_id": m0.CODE, "quantity": 100,
                              "cost_price": 10, "acquired_at": "2026-09-20"}])
    return pm, svc


@pytest.mark.parametrize("projection", [True, False])
def test_account_strategy_consumes_same_context(tmp_path, monkeypatch, projection):
    pm, _ = seed(tmp_path, monkeypatch, projection)
    facts = pm.request_account_facts()
    ctx = facts.context_for(m0.CODE, price=10, price_as_of=m0.PRICE_DAY)
    state = pm.to_strategy_state(m0.CODE, price=10, price_as_of=m0.PRICE_DAY,
                                 nav=facts.nav, nav_as_of=facts.nav_day, account_context=ctx)
    emit("X1_strategy_context", projection=projection, state=ctx.position_state,
         quantity=ctx.quantity, context_weight=ctx.confirmed_weight,
         strategy_weight=state.current_position_ratio, lifecycle=state.lifecycle)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight is None
    assert state.current_position_ratio == ctx.confirmed_weight


def test_chat_ledger_only_holding_retains_exit(tmp_path, monkeypatch):
    pm, _ = seed(tmp_path, monkeypatch, False)
    stock = m0._stub_market(m0.CODE)
    dr = DecisionResult(stock=stock, state=MarketState.TRANSITION,
                        decision=SignalType.SELL, score=.8)
    seen = {}
    def analyze(*args, **kwargs):
        seen.update(kwargs)
        return dr, m0._sd("CLOSE_ALL", 0, sell_path="fundamental_alert"), m0._eval(), None
    packets = []
    original = analysis.build_decision_packet
    def build(*args, **kwargs):
        pkt = original(*args, **kwargs)
        packets.append(pkt)
        return pkt
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    monkeypatch.setattr(chat, "_orchestrator", SimpleNamespace(analyze=analyze))
    monkeypatch.setattr(chat, "_get_stock_data_with_timeout", lambda code: stock)
    monkeypatch.setattr(formatter, "format_analysis_result", lambda *args: "fixture")
    monkeypatch.setattr(m0.cli_main, "_watch_pool_touch", lambda *args: None)
    monkeypatch.setattr(analysis, "build_decision_packet", build)
    result = chat.analyze_stock(m0.CODE)
    assert packets, result
    emit("X1_chat_no_projection", has_position=seen["has_position"],
         strategy_weight=seen["current_position_ratio"],
         terminal=packets[-1].desired_action.value,
         account_version=packets[-1].portfolio_revision)
    assert seen["has_position"] is True
    assert packets[-1].desired_action.value == "EXIT"


def test_unreadable_ledger_is_unknown_not_flat(tmp_path, monkeypatch):
    pm, _ = seed(tmp_path, monkeypatch, False)
    with patch.object(accounts.AccountService, "snapshot", side_effect=OSError("fixture read error")):
        ctx = pm.request_account_facts().context_for(m0.CODE)
    emit("X1_ledger_read_failure", state=ctx.position_state,
         weight=ctx.confirmed_weight, reason=ctx.weight_reason)
    assert ctx.position_state == "UNKNOWN" and ctx.confirmed_weight is None


def capture(tmp_path, monkeypatch, action="HOLD_POSITION", weight=.1, ratio=.1,
            policy_version=None):
    pm, _ = seed(tmp_path, monkeypatch)
    plans, asm = m0._seed_accepted_mid_plan(tmp_path)
    if policy_version:
        plan = plans.get(m0.CODE, "MID").model_copy(update={"policy_version": policy_version})
        plans.save(plan)
        assert plans.accept(m0.CODE, "MID")[0]
    ctx = m0._ctx(weight=weight)
    sd = m0._sd(action, ratio, sell_path="weak_sell" if action == "REDUCE" else None)
    ee = m0._eval()
    ee.effective_action = sd.position_action
    pkt = analysis.build_decision_packet(m0._dr(), sd, ee,
        confirmed_ratio=ctx.confirmed_weight, position_state=ctx.position_state,
        account_version=ctx.account_version)
    rec = shadow.capture_shadow(m0._dr(), sd, ee, pm.get_position(m0.CODE),
        packet=pkt, account_context=ctx, plans_store=plans, assessment_store=asm,
        quote_as_of=m0.PRICE_DAY, store_path=tmp_path / "capture.jsonl",
        config={"fusion": {"mode": "capture_only", "shadow_capture": True}})
    assert rec is not None and rec.mid_binding is not None
    return pkt, rec


def test_shadow_legacy_hold_uses_normalized_target(tmp_path, monkeypatch):
    pkt, rec = capture(tmp_path, monkeypatch)
    arm = rec.mid_binding["arms"]["legacy"]
    emit("X2_hold_target", packet_target=pkt.target_weight, arm=arm,
         effective=rec.mid_effective)
    assert arm["target"] == pkt.target_weight


def test_shadow_execution_uses_same_enum(tmp_path, monkeypatch):
    pkt, rec = capture(tmp_path, monkeypatch, action="REDUCE", weight=.1, ratio=.05)
    arm = rec.mid_binding["arms"]["legacy"]
    emit("X2_execution", packet_execution=pkt.execution_status.value,
         legacy=arm["execution"], fusion=rec.mid_binding["arms"]["fusion"]["execution"])
    assert arm["execution"] == pkt.execution_status.value


def test_unknown_policy_version_not_effective(tmp_path, monkeypatch):
    _, rec = capture(tmp_path, monkeypatch, policy_version="UNSUPPORTED_FUTURE_VERSION")
    emit("X2_version_gate", version=rec.mid_binding["policy_version"],
         effective=rec.mid_effective, drops=rec.mid_binding["drop_reasons"])
    assert rec.mid_effective is False


def test_horizon_unknown_reduce_goes_review():
    pkt = m0.evaluate_horizon(m0._plan(), m0._hf(thesis_status="VALID", technical_exit_triggered=True),
                             m0.CODE, confirmed_ratio=None, position_state="UNKNOWN")
    emit("X2_horizon_unknown", action=pkt.desired_action.value)
    assert pkt.desired_action.value == "REVIEW"


def test_horizon_held_unknown_budget_true_still_freezes_add():
    pkt = m0.evaluate_horizon(m0._plan(), m0._hf(thesis_status="VALID", entry_condition_met=True,
                                               budget_available=True),
                             m0.CODE, confirmed_ratio=None, position_state="HELD")
    emit("X2_horizon_add", action=pkt.desired_action.value)
    assert pkt.desired_action.value == "HOLD"


@pytest.mark.parametrize("stamp", ["2026-02-30", "2026-09-30T99:99:99", "2026-09-30garbage"])
def test_bad_date_never_qualifies_weight(stamp):
    weight, reason = portfolio._qualified_weight(100, 10, stamp, 10000, stamp[:10])
    emit("X3_bad_date", stamp=stamp, weight=weight, reason=reason)
    assert weight is None


def test_same_day_future_time_is_rejected():
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    future = now.replace(hour=23, minute=59, second=59, microsecond=0)
    if future <= now:
        pytest.skip("End of trading-zone day")
    weight, reason = portfolio._qualified_weight(100, 10, future.isoformat(), 10000,
                                                 now.date().isoformat())
    emit("X3_future_time", now=now, quote=future, weight=weight, reason=reason)
    assert weight is None


def test_w2_source_date_repair_positive(tmp_path, monkeypatch):
    from src.data.akshare_client import AKShareClient
    fields = "date code open high low close preclose volume amount".split()
    old_day = (datetime.now().astimezone() - timedelta(days=2)).date().isoformat()
    quote = AKShareClient._baostock_quote_from_row(fields,
        [old_day, "sh.600519", "10", "11", "9", "10", "10", "100000", "1000000"], m0.CODE)
    with patch.object(AKShareClient, "get_realtime_quote", return_value=quote), \
         patch.object(AKShareClient, "get_historical_kline", return_value=None):
        stock = AKShareClient._calculate_indicators_uncached(m0.CODE)
    weight, _ = portfolio._qualified_weight(100, stock.price, stock.quote_as_of, 10000,
                                             datetime.now().astimezone().date().isoformat())
    emit("W2_positive", source_day=old_day, effective_at=stock.quote_as_of,
         fetched_at=stock.quote_fetched_at, weight=weight)
    assert stock.quote_as_of == old_day and weight is None


def test_old_protocol_counts_both_horizons(tmp_path, monkeypatch):
    _, rec = capture(tmp_path, monkeypatch)
    row = rec.model_dump(mode="json")
    row.update(derivation_version="shadow_v7", mid_effective=True, long_effective=True)
    path = tmp_path / "older.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = shadow.build_shadow_report(store_path=path, days=7)
    bucket = report["older_versions"]["shadow_v7"]
    emit("X2_old_bucket", bucket=bucket, current_mid=report["cur_mid_effective"])
    assert report["cur_mid_effective"] == 0
    assert bucket["mid_effective"] == 1 and bucket["long_effective"] == 1


def prototype():
    sys.path.insert(0, str(REPO / "plan/fusion/iteration6"))
    import m2_readonly_view_prototype
    return m2_readonly_view_prototype


def test_m2_repeated_read_zero_mutation(tmp_path, monkeypatch):
    m0._isolate(tmp_path, monkeypatch)
    plans, asm = m0._seed_accepted_mid_plan(tmp_path)
    proto = prototype()
    before = proto._tree_hash([tmp_path])
    for seconds in (0, 1, 60):
        view = proto.daily_research_view(m0.CODE, plans_store=plans, assessment_store=asm,
                                         as_of=datetime.now().astimezone() + timedelta(seconds=seconds))
        assert view["horizons"]["MID"]["accepted"]
    after = proto._tree_hash([tmp_path])
    emit("M2_readonly_positive", repeated_reads=3, hashes_equal=before == after)
    assert before == after


def test_m2_corrupt_store_not_reported_as_empty(tmp_path, monkeypatch):
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    m0._isolate(tmp_path, monkeypatch)
    path = tmp_path / "bad_plans.json"
    path.write_text("{broken", encoding="utf-8")
    plans = HorizonPlanStore(path)
    proto = prototype()
    before = proto._tree_hash([tmp_path])
    view = proto.daily_research_view(m0.CODE, plans_store=plans,
        assessment_store=AssessmentStore(tmp_path / "research"), as_of=datetime.now().astimezone())
    assert proto._tree_hash([tmp_path]) == before
    emit("M2_corrupt_store", corrupted=plans.corrupted, view=view)
    assert plans.corrupted
    assert view["store_status"] == "corrupted"
