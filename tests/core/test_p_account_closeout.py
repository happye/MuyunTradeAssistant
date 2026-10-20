"""P0 request facts matrix: isolated snapshots, projections, state and packet."""
from pathlib import Path
from unittest.mock import patch
import json

import pytest

import test_o0_exceptional_account as o0
import test_m0_account_terminal as m0
from src.core.analysis_service import build_decision_packet
from src.data.models import TradeLifecycle


@pytest.mark.parametrize("health", ["healthy", "partial", "failed"])
@pytest.mark.parametrize("projection", ["none", "qty0", "qty+", "ratio0", "ratio+", "bad"])
@pytest.mark.parametrize("lot", [False, True])
def test_request_matrix(tmp_path, monkeypatch, health, projection, lot):
    o0._isolate(tmp_path, monkeypatch)
    if lot:
        o0._seed_ledger_only(quantity=100)
    else:
        o0.AccountService(o0.account_module.DEFAULT_LEDGER_PATH).opening_import(
            opening_cash=10000, trade_date="2026-09-20", lots=[])
    if health == "partial":
        with o0.account_module.DEFAULT_LEDGER_PATH.open("a", encoding="utf-8") as stream:
            stream.write('{"truncated_event"\n')
    pm = o0.PortfolioManager()
    if projection == "bad":
        pm._data["positions"][o0.CODE] = 42
    elif projection != "none":
        pm.add_position(o0.CODE, entry_price=10, ratio=.1 if projection == "ratio+" else 0)
        if projection.startswith("qty"):
            pm._data["positions"][o0.CODE]["quantity_fact"] = {
                "quantity": 100 if projection == "qty+" else 0,
                "as_of": "2026-09-20", "avg_cost": 10}
    source_bytes = o0.account_module.DEFAULT_LEDGER_PATH.read_bytes()
    projection_path = Path(o0.portfolio_module.DEFAULT_PORTFOLIO_PATH)
    projection_bytes = projection_path.read_bytes() if projection_path.exists() else None
    original = o0.AccountService.snapshot
    def snapshot(service):
        if health == "failed":
            raise OSError("isolated fixture")
        return original(service)
    with patch.object(o0.AccountService, "snapshot", autospec=True, side_effect=snapshot) as read:
        facts = pm.request_account_facts()
        assert read.call_count == 1
        assert facts._read_failed is (health == "failed")
        assert facts._ledger_partial is (health == "partial")
        assert facts._ledger_quantity(o0.CODE) == (100 if lot and health != "failed" else 0)
        ctx = facts.context_for(o0.CODE)
        entries, incomplete = facts.holding_entries()
        state = pm.to_strategy_state(o0.CODE, account_context=ctx)
        if health != "failed" and lot:
            expected_state, expected_qty, weight = "HELD", 100, None
        elif projection == "ratio+":
            expected_state, expected_qty = "HELD", None
            weight = .1 if health == "healthy" else None
        elif projection == "qty+":
            expected_state = "HELD" if health == "healthy" else "UNKNOWN"
            expected_qty, weight = 100, None
        elif health != "healthy" or projection == "bad":
            expected_state, expected_qty, weight = "UNKNOWN", None, None
        else:
            expected_state, expected_qty, weight = "NONE", 0, 0.0
        assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == (
            expected_state, expected_qty, weight), ctx.weight_reason
        assert state.current_position_ratio == weight
        if expected_state == "HELD":
            assert state.lifecycle != TradeLifecycle.FLAT
        assert incomplete is (health != "healthy" or projection == "bad")
        expected_codes = [o0.CODE] if projection not in ("none", "bad") or (
            lot and health != "failed") else []
        assert [entry.stock_code for entry in entries] == expected_codes
        packet = build_decision_packet(o0._dr("SELL"), o0._sd("CLOSE_ALL", 0), o0._eval(),
            confirmed_ratio=ctx.confirmed_weight, position_state=ctx.position_state,
            account_version=ctx.account_version)
        assert packet.portfolio_revision == (ctx.account_version or None)
        if expected_state != "NONE":
            assert packet.desired_action.value == "EXIT"
        else:
            assert packet.desired_action.value == "WAIT"
        buy_packet = build_decision_packet(o0._dr("BUY"), o0._sd("ADD", .3), o0._eval(),
            confirmed_ratio=ctx.confirmed_weight, position_state=ctx.position_state,
            account_version=ctx.account_version)
        if weight is None:
            assert buy_packet.target_weight is None
            if expected_state == "HELD":
                assert buy_packet.desired_action.value == "HOLD"
        assert read.call_count == 1
        assert o0.account_module.DEFAULT_LEDGER_PATH.read_bytes() == source_bytes
        assert (projection_path.read_bytes() if projection_path.exists() else None) == projection_bytes


@pytest.mark.parametrize("ctx_version", [None, "", "request-version"])
def test_failed_request_shadow_never_retries_version(tmp_path, monkeypatch, ctx_version):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    with patch.object(o0.AccountService, "snapshot", side_effect=OSError("fixture")):
        facts = pm.request_account_facts()
    ctx = facts.context_for(o0.CODE)
    from dataclasses import replace
    ctx = replace(ctx, account_version=ctx_version)
    plans, assessments = m0._seed_accepted_mid_plan(tmp_path)
    sd, ee, dr = m0._sd("HOLD_POSITION", .1), m0._eval(), m0._dr()
    packet = build_decision_packet(dr, sd, ee, confirmed_ratio=ctx.confirmed_weight,
        position_state=ctx.position_state, account_version=ctx.account_version)
    with patch.object(o0.AccountService, "account_version", return_value="retry_success") as retry:
        rec = o0.shadow_module.capture_shadow(dr, sd, ee, pm.get_position(o0.CODE),
            packet=packet, account_context=ctx, account_version="unrelated_version",
            plans_store=plans, assessment_store=assessments, quote_as_of=m0.PRICE_DAY,
            store_path=tmp_path / "shadow.jsonl",
            config={"fusion": {"mode": "capture_only", "shadow_capture": True}})
    assert retry.call_count == 0
    assert packet.portfolio_revision == (ctx_version or None)
    assert rec.account_version == (ctx_version or "")
    assert rec.mid_binding["account_version"] == (ctx_version or "")
    if not ctx_version:
        assert not rec.mid_effective
    stored = json.loads((tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert stored["account_version"] == (ctx_version or "")
    assert stored["mid_binding"]["account_version"] == (ctx_version or "")
    if not ctx_version:
        assert stored["mid_effective"] is False
    report = o0.shadow_module.build_shadow_report(store_path=tmp_path / "shadow.jsonl", days=7)
    if not ctx_version:
        assert report["cur_mid_effective"] == 0


def test_request_projection_and_collection_are_frozen(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    facts = pm.request_account_facts()
    pm._data["positions"][o0.CODE]["current_ratio"] = .8
    pm._data["positions"][o0.CODE]["entry_price"] = 99
    pm._data["positions"]["sh600519"] = 42
    ctx = facts.context_for("sh600519")
    assert ctx.confirmed_weight == .1
    assert facts.projection_for("sh600519").entry_price == 10
    assert [entry.stock_code for entry in facts.holding_entries()[0]] == [o0.CODE]
    assert pm.to_strategy_state(o0.CODE, account_context=ctx).entry_price == 10
    refreshed = pm.request_account_facts()
    assert refreshed.context_for(o0.CODE).confirmed_weight == .8
    assert refreshed.holding_entries()[1] is True


@pytest.mark.parametrize("projection", ["none", "qty0", "qty+", "ratio0", "ratio+", "bad"])
def test_explicit_zero_ledger_is_distinct_from_missing_quantity(tmp_path, monkeypatch, projection):
    import test_p0_account_exception_matrix as old
    o0._isolate(tmp_path, monkeypatch)
    old._zero_qty_ledger()
    pm = o0.PortfolioManager()
    if projection == "bad":
        pm._data["positions"][o0.CODE] = 42
    elif projection != "none":
        pm.add_position(o0.CODE, entry_price=10, ratio=.1 if projection == "ratio+" else 0)
        if projection.startswith("qty"):
            pm._data["positions"][o0.CODE]["quantity_fact"] = {
                "quantity": 100 if projection == "qty+" else 0, "as_of": "2026-09-21"}
    facts = pm.request_account_facts()
    assert o0.CODE in facts._quantities and facts._quantities[o0.CODE] == 0
    ctx = facts.context_for(o0.CODE)
    expected = "UNKNOWN" if projection in ("qty+", "ratio+", "bad") else "NONE"
    assert ctx.position_state == expected
    assert ctx.confirmed_weight == (0.0 if expected == "NONE" else None)
    assert pm.to_strategy_state(o0.CODE, account_context=ctx).current_position_ratio == ctx.confirmed_weight
    assert facts.holding_entries()[1] is (projection == "bad")


def test_mixed_projection_bad_record_and_alias_keep_good_entries(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    pm = o0.PortfolioManager()
    pm.add_position("000001", entry_price=5, ratio=.2)
    pm._data["positions"][o0.CODE] = 42
    pm._data["positions"]["sz000001"] = dict(pm._data["positions"]["000001"])
    facts = pm.request_account_facts()
    entries, incomplete = facts.holding_entries()
    assert [entry.stock_code for entry in entries] == ["000001", o0.CODE]
    assert incomplete and o0.CODE in facts.projection_gaps
    assert facts.projection_for("sz000001").current_ratio == .2
    ctx = facts.context_for(o0.CODE)
    assert ctx.position_state == "HELD" and ctx.quantity == 100
    assert ctx.projection_loaded and ctx.projection is None
    assert pm.to_strategy_state(o0.CODE, account_context=ctx).entry_price is None


def test_bad_projection_independent_exit_capture_storage_same_request(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    pm = o0.PortfolioManager()
    pm._data["positions"][o0.CODE] = 42
    original = o0.AccountService.snapshot
    with patch.object(o0.AccountService, "snapshot", autospec=True, side_effect=original) as reads:
        facts = pm.request_account_facts()
        ctx = facts.context_for(o0.CODE)
        state = pm.to_strategy_state(o0.CODE, account_context=ctx)
        assert state.lifecycle == TradeLifecycle.OPEN and state.entry_price is None
        sd, dr, ee = m0._sd("CLOSE_ALL", 0, "fundamental_alert"), o0._dr("SELL"), o0._eval()
        packet = build_decision_packet(dr, sd, ee, confirmed_ratio=ctx.confirmed_weight,
            position_state=ctx.position_state, account_version=ctx.account_version)
        rec = o0.shadow_module.capture_shadow(dr, sd, ee, facts.projection_for(o0.CODE),
            packet=packet, account_context=ctx, quote_as_of=m0.PRICE_DAY,
            store_path=tmp_path / "shadow.jsonl",
            config={"fusion": {"mode": "capture_only", "shadow_capture": True}})
        assert reads.call_count == 1
    assert packet.desired_action.value == "EXIT"
    assert rec.legacy_desired == rec.fusion_mid_action == rec.fusion_long_action == "EXIT"
    stored = json.loads((tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert stored["account_version"] == packet.portfolio_revision == ctx.account_version
    assert stored["legacy_desired"] == stored["fusion_mid_action"] == stored["fusion_long_action"] == "EXIT"


def test_failed_request_next_request_can_refresh(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    pm = o0.PortfolioManager()
    original = o0.AccountService.snapshot
    first = True
    def snapshot(service):
        nonlocal first
        if first:
            first = False
            raise OSError("fixture")
        return original(service)
    with patch.object(o0.AccountService, "snapshot", autospec=True, side_effect=snapshot) as reads:
        failed = pm.request_account_facts()
        assert failed.context_for(o0.CODE).position_state == "UNKNOWN"
        fresh = pm.request_account_facts()
        assert fresh.context_for(o0.CODE).position_state == "HELD"
        assert fresh.account_version and failed.account_version == ""
        assert failed.context_for(o0.CODE).position_state == "UNKNOWN"
        assert reads.call_count == 2


@pytest.mark.parametrize("ledger", ["positive", "zero", "absent"])
def test_whole_projection_corruption_cannot_hide_independent_holding(tmp_path, monkeypatch, ledger):
    import test_p0_account_exception_matrix as old
    o0._isolate(tmp_path, monkeypatch)
    if ledger == "positive":
        o0._seed_ledger_only(quantity=100)
    elif ledger == "zero":
        old._zero_qty_ledger()
    projection_path = Path(o0.portfolio_module.DEFAULT_PORTFOLIO_PATH)
    projection_path.write_text("{bad", encoding="utf-8")
    before = projection_path.read_bytes()
    pm = o0.PortfolioManager()
    assert pm._corrupted
    facts = pm.request_account_facts()
    ctx = facts.context_for(o0.CODE)
    entries, incomplete = facts.holding_entries()
    assert incomplete is True
    if ledger == "positive":
        assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == ("HELD", 100, None)
        assert [entry.stock_code for entry in entries] == [o0.CODE]
        state = pm.to_strategy_state(o0.CODE, account_context=ctx)
        assert state.lifecycle == TradeLifecycle.OPEN and state.entry_price is None
    else:
        assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == ("UNKNOWN", None, None)
        assert entries == []
    assert projection_path.read_bytes() == before


@pytest.mark.parametrize("damage", ["entry_price", "inertia_counter"])
def test_inner_bad_metadata_cannot_block_independent_state(tmp_path, monkeypatch, damage):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    data = pm._data["positions"][o0.CODE]
    if damage == "entry_price":
        data["entry_price"] = "bad"
    else:
        data["strategy_state"]["inertia_counter"] = "bad"
    facts = pm.request_account_facts()
    ctx = facts.context_for(o0.CODE)
    assert ctx.position_state == "HELD" and ctx.quantity == 100
    state = pm.to_strategy_state(o0.CODE, account_context=ctx)
    assert state.entry_price is None
    assert state.lifecycle == TradeLifecycle.OPEN
    assert facts.projection_for(o0.CODE) is None and o0.CODE in facts.projection_gaps
    assert facts.holding_entries()[1] is True


@pytest.mark.parametrize("ledger_lot", [False, True])
@pytest.mark.parametrize("section,key,value", [
    (None, "stock_name", 42),
    (None, "entry_price", float("nan")),
    (None, "entry_price", 0),
    (None, "high_since_entry", "bad"),
    (None, "entry_date", "2026-02-30"),
    ("strategy_state", "last_tick_date", "bad"),
    ("strategy_state", "cooldown_remaining", "bad"),
    ("strategy_state", "recent_signals", "bad"),
    ("strategy_state", "signal_stability_score", float("nan")),
    ("strategy_state", "total_commission_paid", float("inf")),
    ("quantity_fact", "quantity", "bad"),
    ("quantity_fact", "quantity", -100),
    ("quantity_fact", "avg_cost", "bad"),
    ("quantity_fact", "as_of", "bad"),
    ("trade_plan", "current_stop", "bad"),
    ("trade_plan", "current_stop", float("nan")),
    ("trade_plan", "opened_at", "bad"),
])
def test_inner_metadata_family_isolated_without_fabricating_empty(
        tmp_path, monkeypatch, ledger_lot, section, key, value):
    o0._isolate(tmp_path, monkeypatch)
    if ledger_lot:
        o0._seed_ledger_only(quantity=100)
    else:
        o0.AccountService(o0.account_module.DEFAULT_LEDGER_PATH).opening_import(
            opening_cash=10000, trade_date="2026-09-20", lots=[])
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    data = pm._data["positions"][o0.CODE]
    if section == "quantity_fact":
        data[section] = {"quantity": 100, "avg_cost": 10, "as_of": "2026-09-20"}
    if section == "trade_plan":
        data[section] = _valid_plan()
    (data if section is None else data[section])[key] = value
    before = repr(data)
    facts = pm.request_account_facts()
    ctx = facts.context_for(o0.CODE)
    assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == (
        ("HELD", 100, None) if ledger_lot else ("UNKNOWN", None, None))
    assert facts.projection_for(o0.CODE) is None and o0.CODE in facts.projection_gaps
    entries, incomplete = facts.holding_entries()
    assert incomplete and [entry.stock_code for entry in entries] == ([o0.CODE] if ledger_lot else [])
    state = pm.to_strategy_state(o0.CODE, account_context=ctx)
    assert state.entry_price is None
    assert state.current_position_ratio is None
    packet = build_decision_packet(o0._dr("SELL"), o0._sd("CLOSE_ALL", 0), o0._eval(),
        confirmed_ratio=ctx.confirmed_weight, position_state=ctx.position_state,
        account_version=ctx.account_version)
    assert packet.desired_action.value == "EXIT"
    assert repr(pm._data["positions"][o0.CODE]) == before


def _valid_plan():
    return {"plan_id": "legal-legacy", "opened_at": "2026-09-20", "why_buy": "accepted",
            "when_buy": "condition", "how_much": .1, "locked_initial_stop": 8,
            "current_stop": 9, "when_sell_targets": [12], "entry_price": 10}


def test_legal_inner_metadata_keeps_values_and_protection(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    data = pm._data["positions"][o0.CODE]
    data["entry_date"] = "2026-09-20"
    data["high_since_entry"] = 12
    data["trade_plan"] = _valid_plan()
    data["strategy_state"].update(inertia_counter=2, cooldown_remaining=1,
                                 total_commission_paid=3.5, last_tick_date="2026-09-20")
    data["quantity_fact"] = {"quantity": 100, "avg_cost": 10, "as_of": "2026-09-20"}
    before = repr(data)
    facts = pm.request_account_facts()
    ctx = facts.context_for(o0.CODE)
    assert not facts.projection_gaps and not facts.holding_entries()[1]
    pos = facts.projection_for(o0.CODE)
    assert pos.entry_price == 10 and pos.high_since_entry == 12
    assert pos.trade_plan.current_stop == 9 and pos.trade_plan.when_sell_targets == [12]
    state = pm.to_strategy_state(o0.CODE, account_context=ctx)
    assert state.inertia_counter == 2 and state.cooldown_remaining == 1
    assert state.entry_date == "2026-09-20" and state.total_commission_paid == 3.5
    assert repr(pm._data["positions"][o0.CODE]) == before


def test_alias_projection_keeps_raw_storage_identity_without_migration(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position("sh600519", stock_name="legacy", entry_price=10, ratio=.1)
    path = Path(o0.portfolio_module.DEFAULT_PORTFOLIO_PATH)
    before = path.read_bytes()
    facts = pm.request_account_facts()
    pos = facts.projection_for(o0.CODE)
    assert pos.stock_code == o0.CODE and pos.raw_storage_key == "sh600519"
    assert facts.storage_key_for(o0.CODE) == "sh600519"
    assert facts.storage_key_for("sh600519") == "sh600519"
    assert "raw_storage_key" not in pos.to_dict()
    assert pm.get_position("sh600519").stock_code == "sh600519"
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["entry_price", "inertia_counter"])
@pytest.mark.parametrize("valid_plan", [True, False])
def test_valid_plan_protection_survives_unrelated_metadata_failure(
        tmp_path, monkeypatch, damage, valid_plan):
    from src.core.plan_guard import PlanGuard
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    raw = pm._data["positions"][o0.CODE]
    raw["trade_plan"] = _valid_plan()
    raw["trade_plan"]["current_stop"] = 11 if valid_plan else "bad"
    if damage == "entry_price":
        raw["entry_price"] = "bad"
    else:
        raw["strategy_state"]["inertia_counter"] = "bad"
    before = repr(raw)
    facts = pm.request_account_facts()
    ctx = facts.context_for(o0.CODE)
    assert (ctx.position_state, ctx.quantity, ctx.confirmed_weight) == ("HELD", 100, None)
    assert facts.projection_for(o0.CODE) is None and facts.holding_entries()[1]
    state = pm.to_strategy_state(o0.CODE, account_context=ctx)
    assert state.entry_price is None
    plan = getattr(ctx, "trade_plan", None)
    assert plan is facts.trade_plan_for(o0.CODE)
    assert facts.holding_entries()[0][0].trade_plan is plan
    guarded = PlanGuard().evaluate(o0._sd("HOLD_POSITION", None), plan,
                                  o0._stub_market(o0.CODE, price=10), today=o0.PRICE_DAY)
    packet = build_decision_packet(o0._dr("HOLD"), guarded, o0._eval(),
        confirmed_ratio=ctx.confirmed_weight, position_state=ctx.position_state,
        account_version=ctx.account_version)
    if valid_plan:
        assert plan.current_stop == 11
        assert guarded.position_action.value == "CLOSE_ALL"
        assert packet.desired_action.value == "EXIT"
        assert any(f.signal_id == "exit.plan.price_stop" and f.verified for f in guarded.signal_findings)
    else:
        assert plan is None
        assert guarded.position_action.value == "HOLD_POSITION"
        assert packet.desired_action.value == "HOLD"
    assert repr(pm._data["positions"][o0.CODE]) == before
