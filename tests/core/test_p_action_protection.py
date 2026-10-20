"""P2 adopted protection persists through public strategy; hints do not authorize exits."""
import pytest

import test_s2a_aggregation as s2a
from src.core.decision_engine import DecisionEngine
from src.core.strategy_layer import StrategyLayer
from src.data.models import MarketState, PositionAction, SignalType, SkillSignal, StrategyState, TradeLifecycle


def _decision(protection=None, ratio=.1):
    votes = s2a._vote_signals([(f"slot{i}", SignalType.BUY, .6) for i in range(4)])
    if protection:
        votes.append(protection)
    return DecisionEngine().make_decision(s2a._sd(), votes, state=MarketState.TRANSITION,
        current_position_ratio=ratio, expected_base=dict(s2a.FOUR),
        rule_versions={name: "rv0" for name in s2a.FOUR}, evaluation_as_of="2026-10-09")


def _signal(name, confidence=.8, kind="action"):
    return SkillSignal(skill_name=name, skill_alias=name, signal=SignalType.SELL,
                       confidence=confidence, skill_type=kind, reason=["synthetic protection"])


@pytest.mark.parametrize("name", ["stop_loss", "take_profit"])
@pytest.mark.parametrize("kind,confidence", [("base", .1), ("base", .9), ("action", .1)])
def test_unadopted_protection_is_only_a_hint(name, kind, confidence):
    warnings = DecisionEngine()._generate_warnings(s2a._sd(), [_signal(name, confidence, kind)], MarketState.TRANSITION)
    assert any("技能线索" in warning for warning in warnings)
    assert not any("触发止损条件" in warning or "触发止盈条件" in warning for warning in warnings)


@pytest.mark.parametrize("ratio", [0., .1, None])
def test_adopted_take_profit_cannot_reopen_at_any_position_boundary(ratio):
    dr = _decision(_signal("take_profit"), ratio)
    assert dr.decision == SignalType.HOLD
    assert dr.action_override_signal_id == "take_profit"
    assert dr.position_action not in (PositionAction.OPEN, PositionAction.ADD)
    state = StrategyState(current_position_ratio=ratio, lifecycle=TradeLifecycle.HOLD,
                          entry_price=dr.stock.price, entry_date="2026-10-01")
    layer = StrategyLayer()
    action, _ = layer._calculate_position(dr.decision, dr.state, dr, state)
    assert action not in (PositionAction.OPEN, PositionAction.ADD)
    result = layer.process(dr, state, dr.stock, current_date="2026-10-09")
    assert result.position_action not in (PositionAction.OPEN, PositionAction.ADD)
    assert any("抑制新增" in warning for warning in dr.warnings)


def test_ordinary_hold_keeps_existing_buy_gate():
    dr = _decision()
    dr.decision = SignalType.HOLD
    action, target = StrategyLayer()._calculate_position(dr.decision, dr.state, dr, StrategyState(current_position_ratio=.1))
    assert action == PositionAction.ADD and target == pytest.approx(.3)


def test_selected_stop_is_reported_and_preserves_public_exit():
    dr = _decision(_signal("stop_loss", .95))
    assert dr.decision == SignalType.SELL
    assert any("已采用止损" in warning for warning in dr.warnings)
    assert dr.position_action == PositionAction.CLOSE_ALL
    state = StrategyState(current_position_ratio=.1, lifecycle=TradeLifecycle.HOLD,
                          entry_price=20, entry_date="2026-10-01")
    result = StrategyLayer().process(dr, state, dr.stock, current_date="2026-10-09")
    assert result.position_action == PositionAction.CLOSE_ALL


def test_flat_stop_is_suppression_not_exit():
    dr = _decision(_signal("stop_loss", .95), 0.)
    assert dr.decision == SignalType.WATCH
    assert any("抑制新增" in warning for warning in dr.warnings)
    assert not any("退出保护" in warning for warning in dr.warnings)


def test_all_protection_hints_are_retained():
    hints = [_signal("take_profit", .1, "base") for _ in range(16)]
    warnings = DecisionEngine()._generate_warnings(s2a._sd(), hints, MarketState.TRANSITION)
    assert sum("技能线索" in warning for warning in warnings) == 16


def test_market_risk_is_not_a_data_gap_and_environment_is_separate():
    dr = _decision()
    assert dr.warnings and dr.data_gaps == []
    state = StrategyState(current_position_ratio=.1, lifecycle=TradeLifecycle.HOLD,
                          entry_price=dr.stock.price, entry_date="2026-10-01")
    result = StrategyLayer().process(dr, state, dr.stock, current_date="2026-10-09")
    assert result.strategy_environment and result.decision_basis
    assert all("参数档" not in text and "极端行情模式" not in text for text in result.decision_basis)
    assert any("参数档" in text for text in result.strategy_environment)


@pytest.mark.parametrize("kind", ["missing_response", "wrong_security", "no_roster"])
def test_slot_qualification_gaps_are_recorded_at_source(kind):
    votes = s2a._vote_signals([(f"slot{i}", SignalType.BUY, .6) for i in range(4)])
    if kind == "missing_response":
        votes.pop()
    elif kind == "wrong_security":
        votes[0].security = "000001"
    dr = DecisionEngine().make_decision(s2a._sd(), votes, state=MarketState.TRANSITION,
        current_position_ratio=.1, expected_base=None if kind == "no_roster" else dict(s2a.FOUR),
        rule_versions={name: "rv0" for name in s2a.FOUR}, evaluation_as_of="2026-10-09")
    assert dr.data_gaps


def test_qualified_zero_confidence_is_not_a_missing_response():
    votes = s2a._vote_signals([(f"slot{i}", SignalType.BUY, 0.) for i in range(4)])
    dr = DecisionEngine().make_decision(s2a._sd(), votes, state=MarketState.TRANSITION,
        expected_base=dict(s2a.FOUR), rule_versions={name: "rv0" for name in s2a.FOUR},
        evaluation_as_of="2026-10-09")
    assert dr.coverage_ratio == 1.
    assert dr.data_gaps == []


@pytest.mark.parametrize("protection", ["plan_price_stop", "take_profit_suppression"])
@pytest.mark.parametrize("is_backtest", [False, True], ids=["live_unknown_execution", "simulated_bar_execution"])
def test_real_protection_pipeline_records_and_displays_same_terminal(tmp_path, monkeypatch, protection, is_backtest):
    """Real engine/guard/execution; fixed skills and unavailable external feeds, no broker fills."""
    import json
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from src.cli import evidence
    from src.cli.action_view import render_action_card, terminal_verdict
    from src.core.orchestrator import Orchestrator
    from src.core.plan_guard import PlanGuard
    from src.core.shadow_diff import capture_shadow
    from src.core.benzong import data_provider
    from src.core.exit_signals import fundamental, stock as stock_signals
    from src.core.fear_index import history
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore

    as_of = datetime.now().strftime("%Y-%m-%d")
    opened = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    data = s2a._sd(quote_as_of=as_of)
    votes = s2a._vote_signals([(f"slot{i}", SignalType.BUY, .6) for i in range(4)])
    if protection == "take_profit_suppression":
        votes.append(_signal("take_profit", .8))
    engine = Orchestrator(enabled_skills=[], ai_config=None, event_config=None, entry_exit_config=None)
    calls = []
    def fixed_skills(stock):
        calls.append(("skills", stock.stock_code))
        return votes
    monkeypatch.setattr(engine.skill_engine, "execute_all", fixed_skills)
    monkeypatch.setattr(engine.skill_engine, "base_roster", lambda weights: (dict(s2a.FOUR), {name: "rv0" for name in s2a.FOUR}))
    # Only unrelated external feeds are unavailable. Tested price/skill protection stays real.
    monkeypatch.setattr(fundamental, "check_fundamental_findings", lambda *args, **kwargs: (None, []))
    monkeypatch.setattr(data_provider, "get_market_turnover", lambda *args, **kwargs: None)
    monkeypatch.setattr(stock_signals, "_check_holder_count_surge", lambda *args, **kwargs: None)
    monkeypatch.setattr(stock_signals, "_check_margin_surge", lambda *args, **kwargs: None)
    monkeypatch.setattr(history, "trading_days", lambda *args, **kwargs: [])

    # Wrap, rather than replace, each production boundary to prove this path was exercised.
    for owner, method in [(engine.decision_engine, "make_decision"), (engine.strategy_layer, "process"),
                          (engine.execution_layer, "evaluate")]:
        original = getattr(owner, method)
        def observed(*args, _original=original, _method=method, **kwargs):
            calls.append((_method, data.stock_code))
            return _original(*args, **kwargs)
        monkeypatch.setattr(owner, method, observed)
    original_guard = PlanGuard.evaluate
    def observed_guard(self, *args, **kwargs):
        calls.append(("plan_guard", data.stock_code))
        return original_guard(self, *args, **kwargs)
    monkeypatch.setattr(PlanGuard, "evaluate", observed_guard)
    plan = s2a._plan(plan_id="accepted_synthetic_plan", stock_code=data.stock_code, opened_at=opened,
                    current_stop=11. if protection == "plan_price_stop" else 8., locked_initial_stop=8.)
    state = StrategyState(current_position_ratio=.1, lifecycle=TradeLifecycle.HOLD,
                          entry_price=data.price, entry_date=opened)
    packet, dr, sd, execution, ai = engine.analyze_packet(data, force_state="TRANSITION",
        confirmed_ratio=.1, position_state="HELD", account_version="synthetic_account_v1",
        current_position_ratio=.1, strategy_state=state, has_position=True, trade_plan=plan,
        today=as_of, ai_enabled=False, is_backtest=is_backtest, source="p2_protection_e2e")
    assert [call[0] for call in calls] == ["skills", "make_decision", "process", "plan_guard", "evaluate"]
    assert ai is None and execution.simulated is is_backtest
    expected = "EXIT" if protection == "plan_price_stop" else "HOLD"
    assert packet.desired_action.value == expected
    if protection == "plan_price_stop":
        assert dr.decision == SignalType.BUY  # PlanGuard protection must override real ordinary BUY.
        assert sd.position_action == PositionAction.CLOSE_ALL and sd.position_ratio == 0.
        assert any(f.signal_id == "exit.plan.price_stop" and f.strategy_binding == "plan:" + plan.plan_id for f in sd.signal_findings)
        assert any("exit.plan.price_stop" in reason for reason in packet.decision_basis)
        assert packet.execution_status.value == ("ELIGIBLE" if is_backtest else "UNKNOWN")
    else:
        assert dr.decision == SignalType.HOLD and dr.action_override_signal_id == "take_profit"
        assert sd.position_action == PositionAction.HOLD_POSITION and sd.position_ratio == pytest.approx(.1)
        assert any("take_profit" in reason for reason in packet.decision_basis)

    for attr, value in [("_STATE_DIR", tmp_path), ("_EVIDENCE_FILE", tmp_path / "evidence.jsonl"), ("_REPORT_DIR", tmp_path / "cards")]:
        monkeypatch.setattr(evidence, attr, value)
    saved = evidence.record_evidence(dr, sd, source="p2_protection_e2e", packet=packet,
                                    position_state="HELD", account_version="synthetic_account_v1")
    assert saved is not None
    row = json.loads((tmp_path / "evidence.jsonl").read_text(encoding="utf-8"))
    assert row["desired_action"] == expected and row["position_action"] == sd.position_action.value
    assert row["decision_basis"] == packet.decision_basis
    assert row["execution_status"] == packet.execution_status.value
    assert row["execution_simulated"] is is_backtest
    if protection == "plan_price_stop":
        assert any(f["signal_id"] == "exit.plan.price_stop" for f in row["signal_findings"])
    else:
        assert row["action_override_signal_id"] == "take_profit"

    context = SimpleNamespace(confirmed_weight=.1, position_state="HELD", account_version="synthetic_account_v1")
    pos = SimpleNamespace(stock_code=data.stock_code, current_ratio=.1, trade_plan=plan)
    shadow = capture_shadow(dr, sd, execution, pos, packet=packet, source="p2_protection_e2e",
        config={"fusion": {"mode": "capture_only"}}, store_path=tmp_path / "shadow.jsonl",
        plans_store=HorizonPlanStore(tmp_path / "plans.json"), assessment_store=AssessmentStore(tmp_path / "research"),
        account_context=context, quote_as_of=as_of)
    assert shadow is not None and shadow.append_status == "saved"
    shadow_row = json.loads((tmp_path / "shadow.jsonl").read_text(encoding="utf-8"))
    assert shadow_row["legacy_desired"] == expected and shadow_row["legacy_action"] == sd.position_action.value
    assert shadow_row["execution_status"] == packet.execution_status.value
    assert shadow_row["hard_exit"] is (protection == "plan_price_stop")
    if protection == "plan_price_stop":
        assert shadow_row["sell_path"] == "stop_loss_exit"
        assert shadow_row["fusion_mid_action"] == shadow_row["fusion_long_action"] == "EXIT"
    verdict = terminal_verdict(sd, execution, has_position=True, packet=packet)
    card = render_action_card(verdict, reasons=packet.decision_basis, as_of=as_of)
    assert verdict["bucket"] == expected
    assert all(reason in card for reason in packet.decision_basis)
    assert ("退出/卖出" if protection == "plan_price_stop" else "暂持") in card
    if protection == "take_profit_suppression":
        assert "抑制新增风险" in card and "当前建议：加仓" not in card
    if protection == "plan_price_stop" and not is_backtest:
        assert "资格待确认" in card and packet.executable_action is None
