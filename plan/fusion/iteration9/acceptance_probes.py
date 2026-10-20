"""R15 branch-intersection probes; run only through guarded review_test_runner."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests/core"))
import test_o0_exceptional_account as o0
import src.chat.tools as chat


def emit(case, **values):
    print("R15_RESULT " + json.dumps(dict(case=case, **values), ensure_ascii=False, default=str))


def test_partial_ledger_zero_quantity_projection_is_not_flat(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=0.0)
    pm._data["positions"][o0.CODE]["quantity_fact"] = {
        "quantity": 0, "as_of": "2026-09-19", "avg_cost": 0.0}
    o0.account_module.DEFAULT_LEDGER_PATH.write_text('{"truncated_event"\n', encoding="utf-8")
    before = o0.account_module.DEFAULT_LEDGER_PATH.read_bytes()
    facts = pm.request_account_facts()
    assert facts._ledger_partial
    ctx = facts.context_for(o0.CODE)
    emit("Z1_partial_zero_projection", state=ctx.position_state,
         weight=ctx.confirmed_weight, reason=ctx.weight_reason)
    assert o0.account_module.DEFAULT_LEDGER_PATH.read_bytes() == before
    assert ctx.position_state == "UNKNOWN" and ctx.confirmed_weight is None


def test_projection_list_failure_sets_incomplete(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    # Valid YAML with an invalid record shape: the file-level syntax guard does
    # not mark it corrupted, while the real PositionRecord decoder fails.
    Path(o0.portfolio_module.DEFAULT_PORTFOLIO_PATH).write_text(
        "positions:\n  '600519': 42\n", encoding="utf-8")
    pm = o0.PortfolioManager()
    assert not pm._corrupted
    facts = pm.request_account_facts()
    entries, incomplete = facts.holding_entries()
    output = o0._repl("la")
    emit("Z2_projection_list_failure", count=len(entries), incomplete=incomplete, output=output)
    assert incomplete is True and "当前无持仓记录" not in output


def test_chat_mixed_holdings_not_truncated_to_projection(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position("000001", stock_name="projection only", entry_price=5, ratio=.1)
    o0._seed_ledger_only(quantity=100)
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    output = chat.get_portfolio()
    emit("Z3_chat_mixed_collection", output=output)
    assert "000001" in output and o0.CODE in output


def test_ledger_holding_output_does_not_claim_flat(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    monkeypatch.setattr(o0.akshare_module, "get_stock_data", o0._stub_market)
    output = o0._repl("la")
    emit("Z3_ledger_holding_output", says_flat="将从FLAT状态开始分析" in output,
         says_ledger_held="账本有仓 100 股" in output)
    assert "将从FLAT状态开始分析" not in output
    assert "账本有仓 100 股" in output


def test_complete_empty_and_legacy_ratio_stay_distinct(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    empty = pm.request_account_facts().context_for(o0.CODE)
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    legacy = pm.request_account_facts().context_for(o0.CODE)
    emit("compat_no_ledger", empty=empty.position_state,
         legacy=legacy.position_state, weight=legacy.confirmed_weight)
    assert empty.position_state == "NONE"
    assert legacy.position_state == "HELD" and legacy.confirmed_weight == .1


@pytest.mark.parametrize("future", [False, True])
def test_time_gate_capture_storage_report_same_call(tmp_path, monkeypatch, future):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "iteration8"))
    import r13_contract_replay as replay
    sh = ZoneInfo("Asia/Shanghai")
    fixed = (datetime.now(sh) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)
    monkeypatch.setattr(replay.shadow, "datetime", FrozenDatetime)
    quote = fixed.replace(hour=23 if future else 9).replace(tzinfo=None).isoformat()
    monkeypatch.setattr(replay.m0, "PRICE_DAY", quote)
    _, rec = replay.capture(tmp_path, monkeypatch)
    report = replay.shadow.build_shadow_report(store_path=tmp_path / "capture.jsonl", days=7)
    stored = json.loads((tmp_path / "capture.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    emit("O1_capture_report", future=future, quote=quote,
         effective=rec.mid_effective, stored=stored["mid_effective"],
         denominator=report["cur_mid_effective"], reasons=rec.mid_binding["drop_reasons"])
    assert rec.mid_effective is (not future)
    assert stored["mid_effective"] is (not future)
    assert report["cur_mid_effective"] == int(not future)
    if future:
        assert "quote_cutoff_future_mid" in rec.mid_binding["drop_reasons"]


# Current-worktree boundary checks. The original R15 probes above remain intact.
def test_partial_positive_ratio_does_not_confirm_weight(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    o0.account_module.DEFAULT_LEDGER_PATH.write_text('{"truncated_event"\n', encoding="utf-8")
    ctx = pm.request_account_facts().context_for(o0.CODE)
    emit("P_partial_positive_ratio", state=ctx.position_state, weight=ctx.confirmed_weight)
    assert ctx.position_state == "HELD"
    assert ctx.confirmed_weight is None, "PARTIAL must freeze precise ratio too"


def test_failed_snapshot_zero_ratio_is_not_proven_empty(tmp_path, monkeypatch):
    from unittest.mock import patch
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=0.0)
    o0._seed_ledger_only(quantity=100)
    with patch.object(o0.account_module.AccountService, "snapshot", side_effect=OSError("fixture")):
        ctx = pm.request_account_facts().context_for(o0.CODE)
    emit("P_failed_snapshot_zero_ratio", state=ctx.position_state, weight=ctx.confirmed_weight)
    assert ctx.position_state == "UNKNOWN" and ctx.confirmed_weight is None


def test_bad_projection_keeps_independent_ledger_state_reachable(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    Path(o0.portfolio_module.DEFAULT_PORTFOLIO_PATH).write_text(
        "positions:\n  '600519': 42\n", encoding="utf-8")
    pm = o0.PortfolioManager()
    ctx = pm.request_account_facts().context_for(o0.CODE)
    try:
        state = pm.to_strategy_state(o0.CODE, account_context=ctx)
    except Exception as exc:
        emit("P_bad_projection_state", context=ctx.position_state, error=type(exc).__name__)
        pytest.fail("Independent ledger holding cannot reach strategy state: " + type(exc).__name__)
    emit("P_bad_projection_state", context=ctx.position_state, lifecycle=state.lifecycle)
    assert ctx.position_state == "HELD" and ctx.quantity == 100


def test_failed_request_shadow_does_not_retry_account_version(tmp_path, monkeypatch):
    from unittest.mock import patch
    from src.core import analysis_service as analysis
    import test_m0_account_terminal as m0
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    o0._seed_ledger_only(quantity=100)
    with patch.object(o0.account_module.AccountService, "snapshot", side_effect=OSError("fixture")):
        ctx = pm.request_account_facts().context_for(o0.CODE)
    assert not ctx.account_version
    plans, assessments = m0._seed_accepted_mid_plan(tmp_path)
    sd = m0._sd("HOLD_POSITION", .1)
    ee = m0._eval()
    dr = m0._dr()
    packet = analysis.build_decision_packet(dr, sd, ee, confirmed_ratio=ctx.confirmed_weight,
        position_state=ctx.position_state, account_version=ctx.account_version)
    with patch.object(o0.account_module.AccountService, "account_version", return_value="retry_success") as retry:
        rec = o0.shadow_module.capture_shadow(dr, sd, ee, pm.get_position(o0.CODE), packet=packet,
            account_context=ctx, plans_store=plans, assessment_store=assessments,
            quote_as_of=m0.PRICE_DAY, store_path=tmp_path / "shadow.jsonl",
            config={"fusion": {"mode": "capture_only", "shadow_capture": True}})
    assert rec is not None
    emit("P_shadow_retry", reads=retry.call_count, packet_version=packet.portfolio_revision,
         shadow_version=rec.mid_binding.get("account_version"), effective=rec.mid_effective)
    assert retry.call_count == 0, "One failed request cannot obtain a second account version"
    assert rec.mid_effective is False and not rec.mid_binding.get("account_version")


def test_chat_uses_normalized_shared_collection(tmp_path, monkeypatch):
    from copy import deepcopy
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    pm._data["positions"]["sh600519"] = deepcopy(pm._data["positions"][o0.CODE])
    entries, _ = pm.request_account_facts().holding_entries()
    assert [entry.stock_code for entry in entries] == [o0.CODE]
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    output = chat.get_portfolio()
    emit("P_chat_normalized_collection", output=output)
    assert output.count(o0.CODE) == 1


def test_chat_does_not_present_unknown_weight_as_confirmed(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    o0._seed_ledger_only(quantity=100)
    ctx = pm.request_account_facts().context_for(o0.CODE)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight is None
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    output = chat.get_portfolio()
    emit("P_chat_unknown_weight", output=output)
    assert "权重" in output and any(x in output for x in ("未知", "待重估", "待对账"))


@pytest.mark.parametrize("status", ["UNKNOWN", "BLOCKED"])
def test_open_summary_does_not_promise_execution_without_eligibility(tmp_path, monkeypatch, status):
    import io
    from rich.console import Console
    import test_m0_account_terminal as m0
    from src.core import analysis_service as analysis
    from src.data.models import DecisionResult, MarketState, SignalType
    from src.core.decision_contract import ExecutionStatus
    o0._isolate(tmp_path, monkeypatch)
    stream = io.StringIO()
    monkeypatch.setattr(o0.cli_main, "console", Console(file=stream, width=180, force_terminal=False))
    monkeypatch.setattr(o0.cli_main, "_cached_benzong_brief", lambda code: None)
    stock = o0._stub_market(o0.CODE)
    dr = DecisionResult(stock=stock, state=MarketState.TRANSITION, decision=SignalType.BUY, score=.6)
    sd = m0._sd("OPEN", .2, decision="BUY")
    ee = m0._eval(blocked=status == "BLOCKED", reason="fixture blocked" if status == "BLOCKED" else "")
    ee.execution_status = ExecutionStatus(status)
    ee.effective_action = sd.position_action
    ctx = m0._ctx(state="NONE", weight=0.0, version="fixture")
    packet = analysis.build_decision_packet(dr, sd, ee, confirmed_ratio=ctx.confirmed_weight,
        position_state=ctx.position_state, account_version=ctx.account_version)
    assert packet.executable_action is None
    o0.cli_main._print_plain_summary(dr, sd, stock, account_context=ctx, execution_eval=ee)
    output = stream.getvalue()
    emit("L_open_qualification", status=status, desired=packet.desired_action.value, output=output)
    assert "明天开盘买" not in output and "条件凑齐了" not in output


def test_take_profit_suppression_is_not_reopened_by_position_calculation():
    import test_s2a_aggregation as s2a
    from src.core.decision_engine import DecisionEngine
    from src.core.strategy_layer import StrategyLayer
    from src.data.models import MarketState, SignalType, SkillSignal, StrategyState
    stock = s2a._sd()
    votes = s2a._vote_signals([(f"slot{i}", SignalType.BUY, .6) for i in range(4)])
    votes.append(SkillSignal(skill_name="take_profit", skill_alias="take_profit", signal=SignalType.SELL,
                            confidence=.8, skill_type="action", reason=["fixture protection"]))
    dr = DecisionEngine().make_decision(stock, votes, state=MarketState.TRANSITION,
        current_position_ratio=.1, expected_base=dict(s2a.FOUR),
        rule_versions={name: "rv0" for name in s2a.FOUR}, evaluation_as_of="2026-10-09")
    assert dr.decision == SignalType.HOLD, "Fixture must reach accepted take-profit suppression"
    action, target = StrategyLayer()._calculate_position(dr.decision, dr.state, dr,
        StrategyState(current_position_ratio=.1))
    emit("L_take_profit_suppression", decision=dr.decision, strength=dr.action_strength,
         override=dr.action_override_signal_id, action=action, target=target)
    assert action.value not in ("OPEN", "ADD"), "Suppressed risk increase cannot reopen downstream"


def test_weak_method_stop_and_profit_do_not_claim_authorized_trigger():
    from src.core.decision_engine import DecisionEngine
    from src.data.models import MarketState, SkillSignal, SignalType
    stock = o0._stub_market(o0.CODE)
    hints = [SkillSignal(skill_name=name, skill_alias=name, signal=SignalType.SELL, confidence=.1,
                        skill_type="base") for name in ("stop_loss", "take_profit")]
    warnings = DecisionEngine()._generate_warnings(stock, hints, MarketState.TRANSITION)
    emit("L_unaccepted_exit_hints", warnings=warnings)
    assert not any("触发止损条件" in x or "触发止盈条件" in x for x in warnings)


def test_market_risk_warning_alone_does_not_mean_missing_research():
    import test_m0_account_terminal as m0
    from src.core import analysis_service as analysis
    from src.data.models import DecisionResult, MarketState, SignalType
    stock = o0._stub_market(o0.CODE)
    sd = m0._sd("HOLD_POSITION", .1)
    dr = DecisionResult(stock=stock, state=MarketState.TRANSITION, decision=SignalType.HOLD, score=.5)
    kwargs = dict(confirmed_ratio=.1, position_state="HELD", account_version="fixture")
    clean = analysis.build_decision_packet(dr, sd, m0._eval(), **kwargs)
    dr.warnings = ["均线空头排列，中期趋势向下，突破信号不可靠"]
    with_risk = analysis.build_decision_packet(dr, sd, m0._eval(), **kwargs)
    emit("L_risk_status", clean=clean.research_status, with_risk=with_risk.research_status)
    assert with_risk.research_status == clean.research_status


def test_take_profit_suppression_public_strategy_stays_nonincreasing():
    import test_s2a_aggregation as s2a
    from src.core.decision_engine import DecisionEngine
    from src.core.strategy_layer import StrategyLayer
    from src.data.models import MarketState, SignalType, SkillSignal, StrategyState, TradeLifecycle
    stock = s2a._sd()
    votes = s2a._vote_signals([(f"slot{i}", SignalType.BUY, .6) for i in range(4)])
    votes.append(SkillSignal(skill_name="take_profit", skill_alias="take_profit", signal=SignalType.SELL,
                            confidence=.8, skill_type="action", reason=["fixture protection"]))
    dr = DecisionEngine().make_decision(stock, votes, state=MarketState.TRANSITION,
        current_position_ratio=.1, expected_base=dict(s2a.FOUR),
        rule_versions={name: "rv0" for name in s2a.FOUR}, evaluation_as_of="2026-10-09")
    assert dr.decision == SignalType.HOLD
    state = StrategyState(current_position_ratio=.1, lifecycle=TradeLifecycle.HOLD,
                          entry_price=stock.price, entry_date="2026-10-01")
    result = StrategyLayer().process(dr, state, stock, current_date="2026-10-09")
    emit("L_take_profit_public_strategy", decision=dr.decision,
         action=result.position_action, target=result.position_ratio, reasons=result.strategy_reasons)
    assert result.position_action.value not in ("OPEN", "ADD")
