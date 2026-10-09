# ⚠ SUPERSEDED（v0.8.29 已实施 S0/S1）: 本探针针对受审版本 ac5aa8e 的旧契约编写
# （check_sector_top_signal 返回 str/_compute_fundamental_alert 等），重跑会红——
# 反例已落成正式回归 tests/core/test_signal_authority_iss117.py（33 项，全绿）。
# 本文件保留作诊断考古，不作为当前验收工具。S2/S3 卡片的验收探针另起。
"""Architect review probes, not product tests or an implementation.

Run only through iteration9/review_test_runner.py. Negative tests assert the
proposed safety contracts; failures are review findings, not repaired behavior.
Synthetic data only; external boundaries are mocked before use.
"""
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import baostock as bs

from src.data import akshare_client as quotes
from src.data.models import (StockData, TradePlan, StrategyDecision, StrategyState,
    SignalType, PositionAction, TradeLifecycle, MarketState, AIModifierResult,
    MarketEvent, SkillSignal, DecisionResult)
from src.core.exit_signals import stock, sector, fundamental
from src.core.benzong import data_provider
from src.core.benzong.dimensions import risk_deduction
from src.core.ai_modifier import AIModifier
from src.core.event_layer import EventLayer
from src.core.skill_engine import YAMLBasedSkill
from src.core.decision_engine import DecisionEngine
from src.core.plan_guard import PlanGuard
from src.core.execution_layer import ExecutionLayer
from src.core.entry_exit.exit_rules import check_trend_break
from src.core.shadow_diff import _derive_facts
from src.core.decision_policy import HorizonPlan, HorizonFacts, evaluate_horizon, POLICY_ID_LONG
from src.core.decision_contract import Horizon, DesiredAction, ExecutionStatus
from src.core.benzong.self_reliance import detect_self_reliance
from src.core.orchestrator import Orchestrator
from src.core.analysis_service import build_decision_packet
from src.core.skill_engine import SkillEngine
import yaml


def sd(**kw):
    return StockData(**(dict(stock_code="600000", stock_name="synthetic", price=10,
        volume=1000, avg_volume_20=1000, change_pct=0) | kw))


def plan(**kw):
    return TradePlan(**(dict(plan_id="review", opened_at="2026-10-01", why_buy="test",
        when_buy="test", how_much=.2, locked_initial_stop=8, current_stop=8,
        max_hold_days=180) | kw))


def strategy(**kw):
    return StrategyDecision(**(dict(decision=SignalType.HOLD,
        state=MarketState.TRANSITION, position_action=PositionAction.HOLD_POSITION,
        position_ratio=0, new_state=StrategyState(current_position_ratio=.2),
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD) | kw))


class RS:
    error_code = "0"
    def __init__(self, rows, fields=()):
        self.rows, self.fields, self.i = rows, list(fields), -1
    def next(self):
        self.i += 1
        return self.i < len(self.rows)
    def get_row_data(self):
        return self.rows[self.i]


@pytest.mark.parametrize("title", ["控股股东终止减持计划暨未减持股份的公告",
    "实际控制人承诺未来六个月不减持股份", "大股东澄清减持传闻不实"])
def test_negated_headline_cannot_be_confirmed_reduction(title):
    assert stock._check_holder_reduction([{"title": title}]) is None


def test_missing_continuity_must_not_strengthen_exit():
    base = dict(volume=60, avg_volume_5=100, change_pct=16)
    assert stock._check_shrink_acceleration(SimpleNamespace(**base, volume_series=[100,100,100,100,60])) is None
    assert stock._check_shrink_acceleration(SimpleNamespace(**base, volume_series=None)) is None


def test_holder_count_uses_latest_period(monkeypatch):
    monkeypatch.setattr(stock, "_daily_cache_get", lambda *a: stock._MISS)
    monkeypatch.setattr(stock, "_daily_cache_set", lambda *a: None)
    frame = pd.DataFrame([
        {"股东户数统计截止日": "2020-01-01", "股东户数-本次": 200, "股东户数-上次": 100},
        {"股东户数统计截止日": "2026-10-01", "股东户数-本次": 100, "股东户数-上次": 110},
    ])
    monkeypatch.setattr(data_provider, "_safe_call", lambda *a, **k: frame)
    assert stock._check_holder_count_surge("600000") is None


def test_flagbearer_requires_full_comparable_window(monkeypatch):
    monkeypatch.setattr(quotes, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(quotes, "_call_with_timeout", lambda fn, **kw: fn())
    monkeypatch.setattr(bs, "query_history_k_data_plus", lambda *a, **k:
        RS([["2026-10-07", "10"], ["2026-10-08", "10"]]))
    assert sector._fetch_20d_change_pct("600001") is None


def test_ai_selected_peer_alone_is_not_hard_exit(monkeypatch):
    monkeypatch.setattr(sector, "_fetch_20d_change_pct", lambda code: 0.0)
    result = sector.check_sector_top_signal(plan(flagbearer_code="600001"), sd(change_20d=20), "600000")
    assert result is None  # No evidence that the selected peer defines this thesis.


@pytest.mark.parametrize("pub", ["", "2099-01-01"])
def test_forecast_without_valid_publication_time_not_hard_exit(monkeypatch, pub):
    monkeypatch.setattr(quotes, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(quotes, "_call_with_timeout", lambda fn, **kw: fn())
    fields = ["profitForcastExpPubDate", "profitForcastExpStatDate", "profitForcastType", "profitForcastAbstract"]
    monkeypatch.setattr(bs, "query_forecast_report", lambda **kw:
        RS([[pub, "2026-12-31", "预亏", "synthetic"]], fields))
    assert fundamental._check_loss_forecast("600000", entry_date="2026-10-01") is None


def test_zero_confidence_ai_label_cannot_force_panic():
    ai = object.__new__(AIModifier)
    ai.sentiment_weight, ai.risk_position_cap = .3, .7
    result = AIModifierResult(event_type="black_swan", confidence=0,
        sentiment="neutral", risk_level="low")
    ai._apply_modification(result, sd())
    assert result.force_state is None


def test_event_missing_verification_cannot_force_panic():
    event = MarketEvent(event_type="black_swan", impact_level=5,
        sentiment="neutral", detection_method="ai", four_elements=None)
    result = EventLayer.to_ai_modifier_result(object.__new__(EventLayer), event)
    assert result.force_state is None


def test_unknown_required_skill_condition_cannot_trigger_stop():
    skill = YAMLBasedSkill("stop_loss", {"rules": [{"signal": "SELL",
        "confidence": .9, "require": "all", "condition": {
        "change_negative": {}, "nonexistent_confirmation": {}}}]}, Path("unused"), "action")
    signal = skill.execute(sd(change_pct=-.1))
    assert signal.signal != SignalType.SELL


def test_unstructured_words_cannot_veto_grade(monkeypatch):
    monkeypatch.setattr(risk_deduction, "_call_ai_for_score", lambda *a, **k: {
        "score": 10, "confidence": .1, "reasoning": "unverified",
        "raw_ai_response": '{"invalidate":null,"reasoning":"claim true is unverified"}'})
    result = risk_deduction.score("600000", "synthetic",
        data_summary={"announcements": [{"title": "synthetic"}]}, ai_client=object())
    assert result["invalidate"] is False


def test_tiny_single_vote_cannot_become_full_action_strength():
    # Diagnostic threshold only, not a proposed production tuning parameter.
    # If score is kept as a relative share, verify separate coverage/action
    # strength and terminal no-open contract instead when implementing S2.
    signal = SkillSignal(skill_name="synthetic", skill_alias="synthetic",
        skill_type="base", signal=SignalType.BUY, confidence=.01)
    result = DecisionEngine().make_decision(sd(), [signal], MarketState.RISK_ON)
    assert result.score < .3, (result.decision, result.score)


def test_keyword_unrelated_industry_cannot_set_short_horizon():
    assert detect_self_reliance(industry_name="服装代工") is False


def test_unassessed_legacy_plan_cannot_certify_invalidation_false():
    result = PlanGuard().evaluate(strategy(decision=SignalType.SELL,
        position_action=PositionAction.REDUCE, position_ratio=.1, sell_path="weak_sell"),
        plan(fundamental_outlook="neutral", when_sell_invalidate=["核心订单取消"]),
        sd(), today="2026-10-09")
    # A missing acceptance field in an old plan does not revoke its discipline.
    # This test rejects the false factual claim, not all protective HOLD actions.
    assert not any("计划未失效" in reason for reason in result.strategy_reasons)


def test_shadow_must_not_promote_path_name_to_verified_hard_exit():
    facts = _derive_facts(strategy(sell_path="top_signal"), SimpleNamespace(research_status=None))
    assert facts["hard_exit_triggered"] is False


def test_low_volume_alone_is_not_proof_exit_impossible():
    data = sd(volume=200, avg_volume_20=1000)
    result = ExecutionLayer().evaluate(data, PositionAction.CLOSE_ALL)
    raw = DecisionResult(stock=data, state=MarketState.TRANSITION, decision=SignalType.SELL, score=.9)
    packet = build_decision_packet(raw, strategy(decision=SignalType.SELL,
        position_action=PositionAction.CLOSE_ALL), result, confirmed_ratio=.2)
    assert packet.desired_action == DesiredAction.EXIT
    assert packet.execution_status == ExecutionStatus.UNKNOWN


def test_near_limit_change_alone_is_not_proof_sealed_board():
    data = sd(change_pct=-9.6)
    result = ExecutionLayer().evaluate(data, PositionAction.CLOSE_ALL)
    raw = DecisionResult(stock=data, state=MarketState.TRANSITION, decision=SignalType.SELL, score=.9)
    packet = build_decision_packet(raw, strategy(decision=SignalType.SELL,
        position_action=PositionAction.CLOSE_ALL), result, confirmed_ratio=.2)
    assert packet.desired_action == DesiredAction.EXIT
    assert packet.execution_status == ExecutionStatus.UNKNOWN


def test_old_penetration_label_no_longer_hard_exit():
    p = plan(penetration_stage="30+")
    assert sector.check_sector_top_signal(p, sd(), "600000") is None
    assert sector.penetration_research_reminder(p)


def test_confirmed_price_stop_remains_decisive():
    result = PlanGuard().evaluate(strategy(), plan(), sd(price=7.9), today="2026-10-09")
    assert result.position_action == PositionAction.CLOSE_ALL
    assert result.sell_path == "stop_loss_exit"


def test_explicit_existing_deadline_remains_decisive():
    result = PlanGuard().evaluate(strategy(), plan(max_hold_days=7), sd(), today="2026-10-09")
    assert result.position_action == PositionAction.CLOSE_ALL
    assert result.sell_path == "time_stop"


def test_backtest_skips_live_fundamental_source(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("live source must not be reached")
    monkeypatch.setattr(fundamental, "check_fundamental_alert", forbidden)
    assert Orchestrator._compute_fundamental_alert(sd(), True, True, plan()) is None


def test_missing_ma_does_not_invent_trend_exit():
    assert check_trend_break(sd(), {}) is None


def test_fusion_unknown_evidence_is_review_not_exit():
    p = HorizonPlan(plan_id="review", horizon=Horizon.LONG, policy_id=POLICY_ID_LONG,
        intent="synthetic", accepted_at="2026-10-01")
    out = evaluate_horizon(p, HorizonFacts(), "600000", confirmed_ratio=.2)
    assert out.desired_action == DesiredAction.REVIEW


def offline_orchestrator(monkeypatch):
    # Real aggregation, strategy, guard, execution and final packet; fake data IO.
    engine = Orchestrator(enabled_skills=[])
    monkeypatch.setattr(engine.skill_engine, "execute_all", lambda data: [])
    monkeypatch.setattr(engine, "_compute_fundamental_alert", lambda *a: None)
    monkeypatch.setattr(data_provider, "get_market_turnover", lambda *a, **k: 1.0)
    monkeypatch.setattr(stock, "_check_holder_count_surge", lambda code: None)
    monkeypatch.setattr(stock, "_check_margin_surge", lambda code: None)
    return engine


def test_negated_headline_must_not_reach_exit_packet(monkeypatch):
    engine = offline_orchestrator(monkeypatch)
    data = sd(recent_announcements=[{"title": "控股股东终止减持计划暨未减持股份的公告"}])
    packet, dr, out, execution, ai = engine.analyze_packet(data,
        confirmed_ratio=.2, position_state="HELD", current_position_ratio=.2,
        has_position=True, trade_plan=plan(), today="2026-10-09", ai_enabled=False)
    assert packet.desired_action != DesiredAction.EXIT, (out.top_signal, packet.reason_codes)


def test_unrelated_stock_event_cannot_force_current_stock_panic(monkeypatch):
    engine = offline_orchestrator(monkeypatch)
    event = MarketEvent(event_type="black_swan", impact_level=5, sentiment="bearish",
        scope="stock", affected_codes=["600999"], four_elements={"authenticity":100})
    engine.event_layer = SimpleNamespace(enabled=True, auto_scan=True,
        check_events=lambda: [event], to_ai_modifier_result=lambda e:
            EventLayer.to_ai_modifier_result(object.__new__(EventLayer), e))
    dr, out, execution, ai = engine.analyze(sd(), current_position_ratio=.2,
        has_position=True, trade_plan=plan(), today="2026-10-09", ai_enabled=False)
    assert dr.state != MarketState.PANIC


def test_ai_soft_cap_cannot_silently_amplify_sell_quantity(monkeypatch):
    engine = offline_orchestrator(monkeypatch)
    result = AIModifierResult(confidence=0, risk_level="high")
    modifier = object.__new__(AIModifier)
    modifier.sentiment_weight, modifier.risk_position_cap = .3, .3
    modifier._apply_modification(result, sd())
    engine.ai_modifier = SimpleNamespace(is_available=lambda: True, analyze=lambda d: result)
    engine.strategy_layer = SimpleNamespace(process=lambda *a, **k: strategy(
        decision=SignalType.SELL, position_action=PositionAction.REDUCE,
        position_ratio=.6, sell_path="weak_sell"))
    dr, out, execution, ai = engine.analyze(sd(), current_position_ratio=.8,
        has_position=True, today="2026-10-09")
    assert out.position_ratio == .6  # Existing trim .8->.6 must not become .8->.3 by unverified AI.


def test_bad_price_cannot_become_confirmed_stop_breach():
    result = PlanGuard().evaluate(strategy(), plan(), sd(price=0), today="2026-10-09")
    assert result.position_action != PositionAction.CLOSE_ALL


def test_known_rule_registry_inventory():
    YAMLBasedSkill._register_conditions()
    repo = Path(__file__).resolve().parents[3]
    unknown, rule_count, file_count = [], 0, 0
    special = {"price_position", "signal", "weight", "confidence", "require"}
    for path in sorted((repo / "src" / "skills").rglob("*.yaml")):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = cfg.get("rules", []) if isinstance(cfg, dict) else []
        if rules:
            file_count += 1
        for idx, rule in enumerate(rules):
            rule_count += 1
            for key in (rule.get("condition") or {}):
                if key not in special and key not in YAMLBasedSkill.CONDITION_REGISTRY:
                    unknown.append((str(path.relative_to(repo)), idx, key))
    print("REGISTRY_INVENTORY", {"files":file_count, "rules":rule_count, "unknown":unknown})
    assert not unknown
