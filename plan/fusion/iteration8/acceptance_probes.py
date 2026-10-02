"""R14 incremental contract probes. Run only through review_test_runner.py.

Synthetic state, temporary persistence; red assertions identify delivery gaps.
"""
import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import r13_contract_replay as replay


def emit(case, **values):
    print("R14_RESULT " + json.dumps(dict(case=case, **values), ensure_ascii=False, default=str))


def test_read_failure_zero_projection_cannot_prove_flat(tmp_path, monkeypatch):
    pm, _ = replay.seed(tmp_path, monkeypatch)
    pm._data["positions"][replay.m0.CODE]["quantity_fact"] = {
        "quantity": 0, "as_of": "2026-09-19", "avg_cost": 0.0}
    with patch.object(replay.accounts.AccountService, "snapshot", side_effect=OSError("fixture read failure")):
        ctx = pm.request_account_facts().context_for(replay.m0.CODE)
    emit("Y1_read_failure_zero_projection", state=ctx.position_state,
         weight=ctx.confirmed_weight, reason=ctx.weight_reason)
    assert ctx.position_state == "UNKNOWN" and ctx.confirmed_weight is None


def test_partial_ledger_cannot_prove_flat(tmp_path, monkeypatch):
    pm, svc = replay.seed(tmp_path, monkeypatch, projection=False)
    # A real truncated ledger whose opening event cannot be replayed.
    replay.accounts.DEFAULT_LEDGER_PATH.write_text('{"truncated_opening_event"\n', encoding="utf-8")
    snap = svc.snapshot()
    assert snap.isolated_events and snap.data_completeness == "PARTIAL"
    ctx = pm.request_account_facts().context_for(replay.m0.CODE)
    emit("Y1_partial_no_remaining_lot", state=ctx.position_state,
         weight=ctx.confirmed_weight, reason=ctx.weight_reason,
         isolated=len(snap.isolated_events))
    assert ctx.position_state == "UNKNOWN" and ctx.confirmed_weight is None


def test_la_does_not_omit_ledger_only_holding(tmp_path, monkeypatch):
    replay.seed(tmp_path, monkeypatch, projection=False)
    output = replay.m0._repl("la")
    emit("Y2_la_ledger_only", output=output)
    assert "当前无持仓记录" not in output
    assert replay.m0.CODE in output


def test_naive_future_quote_not_effective_in_current_binding(tmp_path, monkeypatch):
    _, rec = replay.capture(tmp_path, monkeypatch)
    binding = dict(rec.mid_binding)
    now = datetime(2026, 10, 2, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    binding["quote_cutoff"] = "2026-10-02T23:59:59"
    binding["evidence_cutoff"] = "2026-10-01"
    effective, reasons = replay.shadow._binding_eligibility(binding, as_of=now)
    emit("Y3_naive_future_quote", quote=binding["quote_cutoff"],
         as_of=now, effective=effective, reasons=reasons)
    assert not effective and any("quote_cutoff_future" in r for r in reasons)


def test_long_unknown_weight_does_not_add():
    from src.core.decision_policy import HorizonPlan, HorizonFacts, evaluate_horizon
    plan = HorizonPlan(plan_id="r14_long", intent="synthetic LONG contract",
                       horizon="LONG", accepted_at="2026-10-01", policy_id="fusion_long_v1")
    facts = HorizonFacts(research_status="COMPLETE", thesis_status="VALID",
                         quality_valuation_ok=True, price_in_buy_zone=True,
                         acute_risk=False, budget_available=True)
    pkt = evaluate_horizon(plan, facts, security_id="600519",
                           position_state="HELD", confirmed_ratio=None)
    emit("Y4_long_unknown_weight", action=pkt.desired_action.value,
         target=pkt.target_weight, execution=pkt.execution_status.value)
    assert pkt.desired_action.value == "HOLD" and pkt.target_weight is None


def test_ratio_only_without_ledger_remains_supported(tmp_path, monkeypatch):
    replay.m0._isolate(tmp_path, monkeypatch)
    pm = replay.portfolio.PortfolioManager()
    pm.add_position(replay.m0.CODE, entry_price=10, ratio=.1)
    assert not replay.accounts.DEFAULT_LEDGER_PATH.exists()
    ctx = pm.request_account_facts().context_for(replay.m0.CODE)
    emit("compat_ratio_only_missing_ledger", state=ctx.position_state, weight=ctx.confirmed_weight)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight == .1
