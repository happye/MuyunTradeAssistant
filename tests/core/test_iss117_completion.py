"""Product regressions for the remaining frozen ISS-117 scope. No live services."""
import json
from types import SimpleNamespace

import pytest

from src.data.models import (StockData, TradePlan, StrategyState, StrategyDecision,
    SignalType, PositionAction, TradeLifecycle, SkillSignal, MarketState, DecisionResult, SignalFinding)
from src.core.trade_plan.conditions import evaluate_conditions
from src.core.trade_plan.adjuster import TradePlanAdjuster
from src.core.plan_guard import PlanGuard
from src.core.execution_layer import ExecutionLayer
from src.core.analysis_service import build_decision_packet
from src.core.decision_engine import DecisionEngine

DAY = "2026-10-12"


def stock(**kw):
    return StockData(**dict(dict(stock_code="600000", stock_name="测试", price=10,
        volume=100, avg_volume_20=100, ma20=11, ma60=12, quote_as_of=DAY), **kw))


def plan(**kw):
    return TradePlan(**dict(dict(plan_id="accepted", opened_at=DAY, why_buy="已接受逻辑",
        when_buy="明确选择", how_much=.2, current_stop=8, locked_initial_stop=8,
        when_sell_invalidate=[], max_hold_days=90), **kw))


def decision(action=PositionAction.REDUCE, **kw):
    return StrategyDecision(**dict(dict(decision=SignalType.SELL,
        position_action=action, position_ratio=.1, lifecycle_before=TradeLifecycle.HOLD,
        lifecycle_after=TradeLifecycle.HOLD, new_state=StrategyState(current_position_ratio=.2),
        sell_path="weak_sell", analysis_as_of=DAY), **kw))


def rule(predicate, **kw):
    return dict(version="plan-condition-v1", predicate=predicate, **kw)


@pytest.mark.parametrize("rules,data,expected", [
    ([rule("below_ma60")], {}, "TRUE"),
    ([rule("below_ma60")], {"price": 13}, "FALSE"),
    ([rule("below_ma60")], {"ma60": None}, "UNKNOWN"),
    ([rule("below_ma60"), {}], {"price": 13}, "UNKNOWN"),
    ([rule("below_ma60"), {}], {}, "TRUE"),
    ([dict(version="plan-condition-v1", op="AND", children=[rule("below_ma60"), {}])], {}, "UNKNOWN"),
    ([dict(version="plan-condition-v1", op="AND", children=[rule("below_ma60"), {}])], {"price": 13}, "FALSE"),
    ([dict(version="plan-condition-v1", op="AND", children=[rule("below_ma60"), rule("volume_ratio_above", threshold=.5)])], {}, "TRUE"),
    ([], {}, "UNKNOWN"),
    ([rule("below_ma60")], {"price": float("nan")}, "UNKNOWN"),
    ([rule("below_ma60")], {"ma60": float("inf")}, "UNKNOWN"),
    ([rule("below_ma60")], {"quote_as_of": "2000-01-01"}, "UNKNOWN"),
    ([rule("below_ma60")], {"quote_as_of": "2099-01-01"}, "UNKNOWN"),
    ([rule("below_ma60")], {"quote_as_of": "bad"}, "UNKNOWN"),
    ([rule("below_ma60")], {"quote_as_of": None}, "UNKNOWN"),
])
def test_conditions_shared_between_actual_guard_and_adjuster(rules, data, expected):
    p, sd = plan(invalidation_rules=rules), stock(**data)
    result = evaluate_conditions(p, sd, DAY)
    out = PlanGuard().evaluate(decision(), p, sd, DAY)
    suggestions = TradePlanAdjuster().suggest(p, sd, today=DAY)
    assert result["value"] == out.condition_evaluation["value"] == expected
    if expected == "TRUE":
        assert out.position_action == PositionAction.REDUCE  # Releases the accepted action, no blanket clear.
        assert any(s["trigger"] == "invalidate_triggered" for s in suggestions)
    else:
        assert out.position_action == PositionAction.HOLD_POSITION
        if expected == "UNKNOWN":
            assert "待复核" in out.strategy_reasons[0]
            assert any(s["trigger"] == "invalidate_unknown" for s in suggestions)


@pytest.mark.parametrize("text", ["跌破 MA60 + 成交量异常放大", "AI认为业绩变差", "跌破MA60但无须退出"])
def test_complete_legacy_condition_is_never_truncated(text):
    assert evaluate_conditions(plan(when_sell_invalidate=[text]), stock(), DAY)["value"] == "UNKNOWN"


@pytest.mark.parametrize("kind", ["price", "time"])
def test_unknown_conditions_cannot_cancel_accepted_exit(kind):
    p = plan(current_stop=11) if kind == "price" else plan(opened_at="2020-01-01")
    out = PlanGuard().evaluate(decision(), p, stock(), DAY)
    assert out.position_action == PositionAction.CLOSE_ALL
    assert out.signal_findings[-1].signal_id == "exit.plan." + ("price_stop" if kind == "price" else "time_stop")


@pytest.mark.parametrize("label", ["服装代工", "芯片", "未知"])
def test_keyword_has_no_mode_authority(label):
    from src.core.benzong.self_reliance import detect_self_reliance
    from src.core.trade_plan.generator import TradePlanGenerator
    data = stock(price=14, ma20=12, ma60=11, macd_dif=2, macd_dea=1)
    p, meta = TradePlanGenerator().generate("600000", label, 14, .2, stock_data=data,
        benzong_grade="A", industry_prosperity=80, is_self_reliance=detect_self_reliance(label))
    assert p.mode == meta["mode"] == "qizong"


@pytest.mark.parametrize("accept", [False, True])
def test_real_plan_update_keeps_old_policies_until_acceptance(monkeypatch, accept):
    from src.cli import main
    from src.data import akshare_client
    from src.rag import service
    original = plan(mode="qizong", max_hold_days=300, current_stop=9, locked_initial_stop=8)
    position = SimpleNamespace(trade_plan=original, entry_price=10, current_ratio=.2, stock_name="服装代工")
    saved = []
    manager = SimpleNamespace(get_position=lambda code: position, attach_plan=lambda code, p: saved.append(p) or True)
    monkeypatch.setattr(akshare_client, "get_stock_data", lambda code: stock(price=7, ma20=8, ma60=9))
    monkeypatch.setattr(service, "get_rag_service", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y" if accept else "n")
    assert main._generate_or_update_plan(manager, "600000", update=True) is accept
    assert original.mode == "qizong" and original.max_hold_days == 300 and original.current_stop == 9
    if accept:
        p = saved[0]
        assert p.mode == "qizong" and p.max_hold_days == 300 and p.locked_initial_stop == 8
        assert p.current_stop >= 9 and p.adjustments[-1]["source"] == "user"
    else:
        assert not saved


@pytest.mark.parametrize("data", [{"volume": 1}, {"change_pct": -9.9}, {"avg_volume_20": None}, {"quote_as_of": None}])
def test_live_estimates_do_not_cancel_exit_or_claim_eligibility(data):
    sd = stock(**data)
    ev = ExecutionLayer().evaluate(sd, PositionAction.CLOSE_ALL, analysis_as_of=DAY)
    raw = DecisionResult(stock=sd, state=MarketState.RISK_ON, decision=SignalType.SELL, score=.9)
    packet = build_decision_packet(raw, decision(PositionAction.CLOSE_ALL), ev, confirmed_ratio=.2)
    assert not ev.blocked and ev.effective_action == PositionAction.CLOSE_ALL
    assert packet.desired_action.value == "EXIT" and packet.execution_status.value == "UNKNOWN"
    assert packet.executable_action is None and packet.target_weight == 0
    assert ev.diagnostics


@pytest.mark.parametrize("facts,expected", [
    ({"suspended": True}, "BLOCKED"),
    ({"sellable_qty": 0}, "BLOCKED"),
    ({"suspended": False, "order_qty": 100, "sellable_qty": 100, "executable_qty": 100}, "ELIGIBLE"),
    ({"suspended": True, "verified": False}, "UNKNOWN"),
    ({"suspended": True, "as_of": "2099-01-01"}, "UNKNOWN"),
    ({"suspended": True, "security": "000001"}, "UNKNOWN"),
    ({"suspended": True, "source": "AI"}, "UNKNOWN"),
])
def test_execution_requires_qualified_facts(facts, expected):
    evidence = dict(dict(source="broker_snapshot", verified=True, security="600000", as_of=DAY), **facts)
    ev = ExecutionLayer().evaluate(stock(execution_facts=evidence), PositionAction.CLOSE_ALL, analysis_as_of=DAY)
    assert ev.execution_status.value == expected and ev.effective_action == PositionAction.CLOSE_ALL


def test_simulated_limit_model_is_explicitly_separate():
    sd = stock(change_pct=-9.9)
    layer = ExecutionLayer()
    live = layer.evaluate(sd, PositionAction.CLOSE_ALL, analysis_as_of=DAY)
    sim = layer.evaluate(sd, PositionAction.CLOSE_ALL, simulated=True)
    assert not live.blocked and live.effective_action == PositionAction.CLOSE_ALL
    assert sim.blocked and sim.effective_action == PositionAction.HOLD_POSITION and sim.simulated
    assert "模拟成交假设" in sim.block_reason


@pytest.mark.parametrize("weights,directions,expected", [([1, 1], ["BUY", "BUY"], .5),
    ([3, 1], ["BUY", "BUY"], .75), ([1, 1], ["BUY", "SELL"], 0)])
def test_cross_slot_same_claim_uses_one_weight_budget(weights, directions, expected):
    binding = dict(claim_id="claim", evidence_id="raw-hash", revision="v1", security="600000", as_of=DAY)
    roster = dict(zip(["a", "b"], weights))
    signals = [SkillSignal(skill_name=n, skill_alias=n, signal=SignalType(d), confidence=1,
        source_kind="external", evidence_binding=binding) for n, d in zip(roster, directions)]
    dr = DecisionEngine().make_decision(stock(), signals, MarketState.RISK_ON,
        expected_base=roster, rule_versions={n: "v1" for n in roster}, evaluation_as_of=DAY,
        evidence_resolver=lambda eid: dict(binding, verified=True, data_quality="OK"))
    assert dr.action_strength["BUY"] == pytest.approx(expected)


def test_unresolved_external_fact_is_not_a_vote_but_distinct_technicals_are():
    signals = [SkillSignal(skill_name=n, skill_alias=n, signal=SignalType.BUY, confidence=1,
        source_kind="external") for n in ("a", "b")]
    args = dict(expected_base={"a": 1, "b": 1}, rule_versions={"a": "v1", "b": "v1"}, evaluation_as_of=DAY)
    dr = DecisionEngine().make_decision(stock(), signals, MarketState.RISK_ON, **args)
    assert dr.action_strength["BUY"] == 0 and dr.coverage_ratio == 0
    for s in signals:
        s.source_kind = "technical"
    dr = DecisionEngine().make_decision(stock(), signals, MarketState.RISK_ON, **args)
    assert dr.action_strength["BUY"] == 1 and dr.coverage_ratio == 1


def test_shadow_path_name_is_not_authority_and_qualified_plan_exit_is():
    from src.core.shadow_diff import _derive_facts
    packet = SimpleNamespace(security_id="600000", research_status=None)
    sd = decision(PositionAction.CLOSE_ALL, sell_path="top_signal")
    assert not _derive_facts(sd, packet)["hard_exit_triggered"]
    sd = PlanGuard().evaluate(sd, plan(current_stop=11), stock(), DAY)
    facts = _derive_facts(sd, packet)
    assert facts["hard_exit_triggered"] and facts["qualified_exit_ids"] == ["exit.plan.price_stop"]
    sd.signal_findings[0].as_of = "2099-01-01"
    assert not _derive_facts(sd, packet)["hard_exit_triggered"]


def test_all_reminders_and_qualified_sources_survive_real_evidence(monkeypatch, tmp_path):
    from src.cli import evidence
    dr = DecisionResult(stock=stock(), state=MarketState.RISK_ON, decision=SignalType.HOLD,
        score=0, warnings=[f"reminder-{i}" for i in range(14)])
    sd = PlanGuard().evaluate(decision(), plan(), stock(), DAY)
    packet = build_decision_packet(dr, sd, None, confirmed_ratio=.2)
    monkeypatch.setattr(evidence, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(evidence, "_EVIDENCE_FILE", tmp_path / "records.jsonl")
    monkeypatch.setattr(evidence, "_REPORT_DIR", tmp_path / "cards")
    assert evidence.record_evidence(dr, sd, packet=packet, source="test")
    row = json.loads((tmp_path / "records.jsonl").read_text(encoding="utf-8"))
    card = next((tmp_path / "cards").glob("*.md")).read_text(encoding="utf-8")
    assert len(row["warnings"]) == 14 and "reminder-13" in card
    assert row["condition_evaluation"]["value"] == "UNKNOWN" and "待复核" in card


def test_new_hold_expires_old_suggestion_without_changing_holdings(tmp_path):
    from src.data.portfolio import PortfolioManager
    manager = PortfolioManager(str(tmp_path / "portfolio.yaml"), str(tmp_path / "proposals.json"))
    manager.add_position("600000", "测试", 10, .2)
    sell = PlanGuard().evaluate(decision(), plan(current_stop=11), stock(), DAY)
    before = manager.get_position("600000").to_dict()
    proposal = manager.record_proposal("600000", "测试", sell, source="test")
    assert proposal.authority_protocol == "iss117-v1"
    assert manager.get_position("600000").to_dict() == before
    hold = decision(PositionAction.HOLD_POSITION, decision=SignalType.HOLD, sell_path=None)
    manager.record_proposal("600000", "测试", hold, source="test")
    assert not manager.pending_proposals("600000")
    assert manager.get_position("600000").to_dict() == before


def test_shared_card_and_chat_keep_unknown_exit_and_condition_gap():
    from src.cli.action_view import terminal_verdict, render_action_card
    from src.chat.formatter import format_analysis_result
    sd = stock(change_pct=-9.9)
    ev = ExecutionLayer().evaluate(sd, PositionAction.CLOSE_ALL, analysis_as_of=DAY)
    out = decision(PositionAction.CLOSE_ALL)
    card = render_action_card(terminal_verdict(out, ev, True))
    assert "退出" in card and "成交资格待确认" in card
    raw = DecisionResult(stock=sd, state=MarketState.RISK_ON, decision=SignalType.SELL, score=.9)
    chat = format_analysis_result(sd, raw, out, ev, None)
    assert "待确认" in chat and "封板，无法卖出" not in chat and "不代表已封板" in chat
    held = PlanGuard().evaluate(decision(), plan(), sd, DAY)
    card = render_action_card(terminal_verdict(held, ev, True))
    assert "暂持" in card and "待复核" in card and "不用操作" not in card


@pytest.mark.parametrize("bad_binding", [{"revision": []}, {"evidence_id": "missing"}, {"as_of": "2099-01-01"}])
def test_bad_external_binding_abstains_with_fixed_denominator(bad_binding):
    binding = dict(dict(claim_id="claim", evidence_id="raw", revision="v1", security="600000", as_of=DAY), **bad_binding)
    sig = SkillSignal(skill_name="a", skill_alias="a", signal=SignalType.BUY, confidence=1,
                      source_kind="external", evidence_binding=binding)
    dr = DecisionEngine().make_decision(stock(), [sig], MarketState.RISK_ON,
        expected_base={"a": 1}, rule_versions={"a": "v1"}, evaluation_as_of=DAY,
        evidence_resolver=lambda eid: dict(claim_id="claim", evidence_id="raw", revision="v1",
            security="600000", as_of=DAY, verified=True, data_quality="OK"))
    assert dr.action_strength["BUY"] == 0 and dr.coverage_ratio == 0
    assert any("UNKNOWN" in w for w in dr.warnings)


def test_same_slot_different_evidence_never_selects_by_order():
    bindings = [dict(claim_id="claim", evidence_id=eid, revision="v1", security="600000", as_of=DAY)
                for eid in ("raw1", "raw2")]
    signals = [SkillSignal(skill_name="a", skill_alias="a", signal=SignalType.BUY, confidence=1,
        source_kind="external", evidence_binding=b) for b in bindings]
    for ordered in (signals, signals[::-1]):
        dr = DecisionEngine().make_decision(stock(), ordered, MarketState.RISK_ON,
            expected_base={"a": 1}, rule_versions={"a": "v1"}, evaluation_as_of=DAY,
            evidence_resolver=lambda eid: dict(bindings[eid == "raw2"], verified=True, data_quality="OK"))
        assert dr.action_strength["BUY"] == 0 and dr.coverage_ratio == 0


def test_old_authority_budget_record_is_not_numerically_comparable(monkeypatch, tmp_path):
    from src.cli import evidence
    path = tmp_path / "e.jsonl"
    rows = [dict(code="600000", ts="1", price=10, score=.8, aggregation_protocol="s2a-v1"),
            dict(code="600000", ts="2", price=11, score=.4, aggregation_protocol="s2a-v1",
                 signal_authority_protocol="iss117-s3-v1")]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(evidence, "_EVIDENCE_FILE", path)
    result = evidence.diff_evidence("600000")
    assert result["changes"]["score"][2] == "口径不同，数值不可比"
    assert result["changes"]["price"][2] == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0])
def test_invalid_values_cannot_create_stop_or_target_adjustments(value):
    p = plan(when_sell_targets=[11])
    suggestions = TradePlanAdjuster().suggest(p, stock(price=value, atr_14=.2), high_since_entry=value, today=DAY)
    assert not any(s["trigger"] in {"target_reached", "auto_trailing"} for s in suggestions)
    with pytest.raises(ValueError):
        TradePlanAdjuster().apply(p, {"field": "current_stop", "new": value}, today=DAY)
    assert p.current_stop == 8


def test_unknown_compound_does_not_override_qizong_plan():
    p = plan(mode="qizong", when_sell_invalidate=["跌破 MA60 + 成交量异常放大"])
    out = PlanGuard().evaluate(decision(), p, stock(), DAY)
    assert out.position_action == PositionAction.HOLD_POSITION
    assert out.condition_evaluation["value"] == "UNKNOWN" and "待复核" in out.strategy_reasons[0]


def test_chat_confirmation_bridge_handles_plan_preview_without_looping():
    from src.chat.tools import _InputPatcher
    with _InputPatcher(False):
        assert input("接受这次计划更新？[y/N]: ") == "n"
    with _InputPatcher(True):
        assert input("接受这次计划更新？[y/N]: ") == "y"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0])
def test_invalid_quote_price_cannot_obtain_ordinary_vote_permission(value):
    sig = SkillSignal(skill_name="a", skill_alias="a", signal=SignalType.BUY, confidence=1)
    dr = DecisionEngine().make_decision(stock(price=value), [sig], MarketState.RISK_ON,
        expected_base={"a": 1}, rule_versions={"a": "v1"}, evaluation_as_of=DAY)
    assert dr.action_strength["BUY"] == 0 and dr.coverage_ratio == 0
    assert dr.position_action not in (PositionAction.OPEN, PositionAction.ADD)
    assert any("价格" in w and "UNKNOWN" in w for w in dr.warnings)



def test_soft_effect_has_identity_scope_and_actual_before_after():
    from src.core.orchestrator import Orchestrator
    raw = DecisionResult(stock=stock(), state=MarketState.RISK_ON, decision=SignalType.BUY, score=.4,
                         action_strength={"BUY": .4, "SELL": 0, "HOLD": 0, "WATCH": 0})
    engine = object.__new__(Orchestrator)
    engine.direction_aware_ai = True
    engine._apply_sentiment_to_decision(raw, .2, "AI Modifier")
    out = decision(PositionAction.ADD, decision=SignalType.BUY, position_ratio=.3)
    packet = build_decision_packet(raw, out, None, confirmed_ratio=.2)
    effect = packet.signal_effects[0]
    assert effect["signal_id"] == "modifier.ai.score" and effect["action_scope"] == "display_score_only"
    assert effect["before"] == .4 and effect["after"] == pytest.approx(.6)
    assert packet.legacy_trace.voting_action_strength["BUY"] == .4
    assert effect["evidence_as_of"] is None and effect["data_quality"] == "UNKNOWN"



def test_real_cli_version_is_synchronized(monkeypatch, capsys):
    from src.cli import main
    monkeypatch.setattr("sys.argv", ["muyun", "--version"])
    with pytest.raises(SystemExit) as exited:
        main.main()
    import start
    assert exited.value.code == 0 and start.VERSION in capsys.readouterr().out



@pytest.mark.parametrize("quote", [None, "bad", "2099-01-01", "2000-01-01"])
def test_order_facts_do_not_launder_missing_or_bad_market_quote(quote):
    facts = dict(source="broker_snapshot", verified=True, security="600000", as_of=DAY,
                 suspended=False, order_qty=100, sellable_qty=100, executable_qty=100)
    ev = ExecutionLayer().evaluate(stock(quote_as_of=quote, execution_facts=facts),
                                   PositionAction.CLOSE_ALL, analysis_as_of=DAY)
    assert ev.execution_status.value == "UNKNOWN" and ev.effective_action == PositionAction.CLOSE_ALL
