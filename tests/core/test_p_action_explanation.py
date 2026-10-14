"""Public action views must describe the same qualified packet as persisted evidence."""
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from src.cli import main as cli
from src.cli.action_view import render_action_card, terminal_verdict
from src.chat.formatter import format_analysis_result
from src.core.analysis_service import build_decision_packet
from src.core.decision_contract import DesiredAction, ExecutionStatus, ResearchStatus
from src.core.execution_layer import ExecutionEvaluation
from src.data.models import (DecisionResult, MarketState, PositionAction, SignalType,
                             StockData, StrategyDecision, StrategyState, TradeLifecycle)


def case(action="OPEN", status="UNKNOWN", current=0.0, target=.3):
    stock = StockData(stock_code="600519", stock_name="fixture", price=10, volume=100000,
                      change_pct=0, ma5=9, ma20=10, ma60=11)
    sell = action in ("REDUCE", "EXIT")
    dr = DecisionResult(stock=stock, state=MarketState.TRANSITION,
                        decision=SignalType.SELL if sell else SignalType.BUY,
                        score=.6, data_gaps=[], action_strength={"BUY": .6, "SELL": .1},
                        coverage_ratio=.75)
    sd = StrategyDecision(decision=dr.decision,
        position_action=PositionAction.CLOSE_ALL if action == "EXIT" else PositionAction(action),
        position_ratio=0 if action == "EXIT" else target,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        new_state=StrategyState(current_position_ratio=current),
        strategy_reasons=["参数档[fixture]", "实际采用的资格"],
        decision_basis=["实际采用的资格"], strategy_environment=["参数档[fixture]"])
    ev = ExecutionEvaluation(original_action=sd.position_action, effective_action=sd.position_action,
        blocked=status == "BLOCKED", block_reason="fixture execution block" if status == "BLOCKED" else "",
        execution_status=ExecutionStatus(status), slippage_pct=0, impact_cost_pct=0, total_cost_pct=0)
    packet = build_decision_packet(dr, sd, ev, confirmed_ratio=current,
                                  position_state="HELD" if current else "NONE")
    return stock, dr, sd, ev, packet


def summary(monkeypatch, stock, dr, sd, ev, packet):
    out = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=240, color_system=None))
    monkeypatch.setattr(cli, "_cached_benzong_brief", lambda code: ("full", 62, "C", "C"))
    cli._print_plain_summary(dr, sd, stock, execution_eval=ev, packet=packet)
    return out.getvalue()


@pytest.mark.parametrize("action,current,target", [("OPEN", 0, .3), ("ADD", .1, .3),
                                                   ("REDUCE", .3, .1), ("EXIT", .3, 0)])
@pytest.mark.parametrize("status", ["UNKNOWN", "BLOCKED", "ELIGIBLE"])
def test_all_public_views_follow_packet_qualification(monkeypatch, action, current, target, status):
    stock, dr, sd, ev, packet = case(action, status, current, target)
    verdict = terminal_verdict(sd, ev, packet=packet)
    assert verdict["bucket"] == action
    assert verdict["execution_status"] == status
    card = render_action_card(verdict)
    text = summary(monkeypatch, stock, dr, sd, ev, packet)
    chat = format_analysis_result(stock, dr, sd, ev, None, packet=packet)
    expected = {"OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓", "EXIT": "退出"}[action]
    for rendered in (card, text, chat):
        assert expected in rendered
        assert "明天开盘买" not in rendered and "条件凑齐了" not in rendered
        if status == "UNKNOWN":
            assert "成交资格待确认" in rendered
        elif status == "BLOCKED":
            assert "受阻" in rendered or "无法成交" in rendered
        else:
            assert "成交资格已确认" in rendered
    assert packet.executable_action == (packet.desired_action if status == "ELIGIBLE" else None)


def test_adapter_clamped_hold_is_the_visible_and_saved_action(monkeypatch):
    stock, dr, sd, ev, packet = case("ADD", "ELIGIBLE", .3, .3)
    assert packet.desired_action is DesiredAction.HOLD
    assert terminal_verdict(sd, ev, packet=packet)["bucket"] == "HOLD"
    for text in (summary(monkeypatch, stock, dr, sd, ev, packet),
                 format_analysis_result(stock, dr, sd, ev, None, packet=packet)):
        assert "继续持有" in text
        assert "当前建议：加仓" not in text and "仓位动作: 加仓" not in text
    assert packet.model_dump(mode="json")["desired_action"] == "HOLD"


def test_risk_and_structured_gaps_have_distinct_labels_and_status(monkeypatch):
    stock, dr, sd, ev, _ = case()
    dr.warnings = ["均线空头排列"] + [f"风险提醒{i}" for i in range(15)]
    dr.data_gaps = []
    clean = build_decision_packet(dr, sd, ev, confirmed_ratio=0)
    assert clean.research_status is ResearchStatus.COMPLETE
    assert clean.research_scope == "TECHNICAL_LEGACY"
    assert clean.thesis_status.value == "UNESTABLISHED"
    dr.data_gaps = ["行情时点无法核对"]
    missing = build_decision_packet(dr, sd, ev, confirmed_ratio=0)
    assert missing.research_status is ResearchStatus.INCOMPLETE
    text = summary(monkeypatch, stock, dr, sd, ev, missing)
    assert "风险提醒：均线空头排列" in text
    assert "待核事项：行情时点无法核对" in text
    assert "风险提醒14" in text
    assert "辅助风险/缺口" not in text
    assert "策略环境：参数档[fixture]" in text
    assert "采用依据：实际采用的资格" in text
    assert "决定性依据：参数档" not in text


def test_unrecorded_qualification_is_not_complete_company_research():
    stock, dr, sd, ev, _ = case()
    dr.data_gaps = None
    p = build_decision_packet(dr, sd, ev, confirmed_ratio=0)
    assert p.research_status is ResearchStatus.INCOMPLETE
    assert p.thesis_status.value == "UNESTABLISHED"


def test_exit_without_packet_remains_a_diagnostic_intent():
    _, _, sd, ev, _ = case("EXIT", "UNKNOWN", .3, 0)
    text = render_action_card(terminal_verdict(sd, ev, has_position=True))
    assert "退出" in text and "成交资格待确认" in text
    assert "未提供统一决策包" in text


def test_buy_pending_checks_do_not_request_sellable_shares():
    _, _, sd, ev, p = case("OPEN")
    text = render_action_card(terminal_verdict(sd, ev, packet=p))
    assert "可卖" not in text
    assert "订单" in text and "可成交量" in text


def test_post_strategy_plan_stop_is_the_final_adopted_basis(monkeypatch):
    from src.core.plan_guard import PlanGuard
    from src.data.models import TradePlan
    stock, dr, sd, ev, _ = case()
    sd.decision_basis = ["独立止盈抑制新增"]
    sd.analysis_as_of = "2026-10-13"
    plan = TradePlan(plan_id="fixture", opened_at="2026-10-01", why_buy="fixture",
                     when_buy="fixture", how_much=.3, locked_initial_stop=11, current_stop=11)
    final = PlanGuard().evaluate(sd, plan, stock, today="2026-10-13")
    p = build_decision_packet(dr, final, ev, confirmed_ratio=.3, position_state="HELD")
    assert p.desired_action.value == "EXIT"
    assert "exit.plan.price_stop" in "；".join(p.decision_basis)
    assert "抑制新增" not in "；".join(p.decision_basis)
    assert "exit.plan.price_stop" in summary(monkeypatch, stock, dr, final, ev, p)
    assert "exit.plan.price_stop" in format_analysis_result(stock, dr, final, ev, None, packet=p)


def test_current_and_old_shadow_protocols_do_not_share_denominator(tmp_path):
    import json
    from datetime import datetime
    from src.core.shadow_diff import build_shadow_report, SHADOW_DERIVATION_VERSION
    assert SHADOW_DERIVATION_VERSION == "shadow_v15"
    now = datetime.now().isoformat(timespec="seconds")
    old = {"derivation_version": "shadow_v13", "observation_kind": "effective",
           "mid_effective": True, "long_effective": True, "legacy_desired": "HOLD",
           "fusion_mid_action": "HOLD", "fusion_long_action": "HOLD", "as_of": now}
    frozen = {"derivation_version": "shadow_v14", "observation_kind": "effective",
              "mid_effective": True, "long_effective": True, "legacy_desired": "HOLD",
              "fusion_mid_action": "HOLD", "fusion_long_action": "HOLD", "as_of": now}
    path = tmp_path / "shadow.jsonl"
    path.write_text(json.dumps(old) + "\n" + json.dumps(frozen) + "\n", encoding="utf-8")
    report = build_shadow_report(path)
    assert report["protocol_version"] == "shadow_v15"
    assert report["cur_mid_effective"] == 0 and report["cur_long_effective"] == 0
    assert report["older_versions"]["shadow_v13"]["records"] == 1
    assert report["older_versions"]["shadow_v14"]["records"] == 1


def test_packet_with_old_unrecorded_fields_roundtrips():
    from src.core.decision_contract import DecisionPacket
    *_, p = case()
    data = p.model_dump(mode="json")
    for name in ("decision_basis", "strategy_environment", "risk_reminders", "data_gaps", "research_scope"):
        data.pop(name)
    old = DecisionPacket.model_validate(data)
    assert old.decision_basis is None and old.data_gaps is None
    assert old.research_scope == "UNRECORDED"
    assert old.desired_action == p.desired_action and old.executable_action is None


def test_evidence_card_uses_clamped_action_and_all_structured_reminders(tmp_path, monkeypatch):
    from src.cli import evidence
    stock, dr, sd, ev, p = case("ADD", "ELIGIBLE", .3, .3)
    dr.warnings = [f"reminder {i}" for i in range(15)]
    p = build_decision_packet(dr, sd, ev, confirmed_ratio=.3, position_state="HELD")
    monkeypatch.setattr(evidence, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(evidence, "_EVIDENCE_FILE", tmp_path / "evidence.jsonl")
    monkeypatch.setattr(evidence, "_REPORT_DIR", tmp_path / "cards")
    saved = evidence.record_evidence(dr, sd, source="fixture", packet=p)
    assert saved["desired_action"] == "HOLD" and saved["decision_basis"] == p.decision_basis
    text = next((tmp_path / "cards").glob("*.md")).read_text(encoding="utf-8")
    assert "继续持有" in text and "当前建议：加仓" not in text
    assert "reminder 14" in text and "## 采用依据" in text
