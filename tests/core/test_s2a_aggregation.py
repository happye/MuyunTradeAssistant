# -*- coding: utf-8 -*-
"""ISS-117 S2a 验收测试（v0.8.29.7）：弱票不放大，仓位消费同一动作强度。

冻结公式 V1（S2_DESIGN）落到产品聚合（DecisionEngine.make_decision 带 E）→
真实 StrategyLayer（analyze_packet 全链）→ 最终 packet：

- 数值矩阵 14 行：A/P/C 与方向判定逐行断言（等权/3:1、同槽合一/冲突、
  未注册/坏值、零置信度计覆盖、原阈值边界）。
- 固定分母：加载失败/执行失败槽占 W；删除其他返回票不增强固定 BUY；
  顺序置换不变。
- regulator：0 置信度加入/移除不变；无基础方向不能凭调节器造买入。
- 门收口：HOLD 不绕 ADD；缺 E 拒新增（降级协议）；C=0 时合法止损仍
  EXIT/CLOSE_ALL；AI 加分不绕基础资格。
- 坏值在模型构造期拒绝（confidence 只接受有限 [0,1]，不 clamp）。

零网络、零 AI、不触碰真实 ~/.muyun。
跑法：pytest tests/core/test_s2a_aggregation.py
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.models import (
    SkillSignal, SignalType, MarketState, StockData, TradePlan,
    AIModifierResult,
)
from src.core.decision_engine import DecisionEngine
from src.core.orchestrator import Orchestrator
from src.core.decision_contract import DesiredAction

B, S, H, W = SignalType.BUY, SignalType.SELL, SignalType.HOLD, SignalType.WATCH
FOUR = {f"slot{i}": 1.0 for i in range(4)}


def _sd(**kw):
    return StockData(**(dict(stock_code="600000", stock_name="synthetic", price=10,
                             volume=1000, avg_volume_20=1000, change_pct=0,
                             quote_as_of="2026-10-09") | kw))


def _plan(**kw):
    return TradePlan(**(dict(plan_id="s2a", opened_at="2026-10-01",
                             why_buy="synthetic", when_buy="synthetic", how_much=.2,
                             locked_initial_stop=8, current_stop=8, max_hold_days=180) | kw))


def _vote_signals(votes):
    """votes: [(slot_name, direction, confidence)] → base SkillSignal 列表。"""
    return [SkillSignal(skill_name=slot, skill_alias=slot, signal=d, confidence=c,
                        skill_type="base")
            for slot, d, c in votes]


def _decide(monkeypatch, roster, votes, state=None, **kw):
    """产品聚合：make_decision 带固定分母 E（注册表由 skill_engine 提供）。"""
    engine = Orchestrator(enabled_skills=[])
    rv = {k: "rv0" for k in roster}
    monkeypatch.setattr(engine.skill_engine, "base_roster",
                        lambda weights: (dict(roster), dict(rv)))
    dr = engine.decision_engine.make_decision(
        _sd(), _vote_signals(votes), state=state,
        expected_base=dict(roster), rule_versions=dict(rv),
        evaluation_as_of="2026-10-09", **kw)
    return dr


# ── 冻结数值矩阵（S2_DESIGN 固定数值验收矩阵） ──────────────

@pytest.mark.parametrize("roster,votes,buy,sell,coverage,share,on,transition", [
    ({"slot0": 1.0}, [("slot0", B, .01)], .01, 0, 1, 1, H, W),
    (FOUR, [("slot0", B, .8)], .2, 0, .25, 1, H, W),
    (FOUR, [(f"slot{i}", B, .8) for i in range(4)], .8, 0, 1, 1, B, B),
    (FOUR, [("slot0", B, .8), ("slot1", B, .8), ("slot2", S, .4), ("slot3", S, .4)],
        .4, .2, 1, 2 / 3, B, B),
    (FOUR, [], 0, 0, 0, 0, H, W),
    (FOUR, [("slot0", B, 0)], 0, 0, .25, 0, H, W),
    (FOUR, [("slot0", B, .8)] * 5, .2, 0, .25, 1, H, W),
    (FOUR, [("slot0", B, .8), ("slot0", S, .8)], 0, 0, 0, 0, H, W),
    (FOUR, [("unknown_slot", B, 1)], 0, 0, 0, 0, H, W),
    ({"slot0": 3.0, "slot1": 1.0}, [("slot0", B, .8)], .6, 0, .75, 1, B, B),
    (FOUR, [("slot0", B, 1)], .25, 0, .25, 1, B, W),
    (FOUR, [(f"slot{i}", S, .8) for i in range(4)], 0, .8, 1, 0, S, S),
], ids=[
    "one_weak_buy_not_amplified", "one_of_four_to_.2_hold", "four_buys_.8_buy",
    "two_buy_two_sell", "all_missing_hold", "zero_confidence_covers_no_contribute",
    "same_slot_duplicate_merged", "same_slot_conflict_unknown",
    "unregistered_slot_ignored", "weighted_3to1", "original_threshold_boundary",
    "all_sell",
])
def test_s2a_frozen_matrix(monkeypatch, roster, votes, buy, sell, coverage, share,
                           on, transition):
    dr = _decide(monkeypatch, roster, votes)
    A = dr.action_strength
    P = dr.opinion_share
    assert A is not None and dr.aggregation_protocol == "s2a-v1"
    assert A["BUY"] == pytest.approx(buy, abs=1e-6)
    assert A["SELL"] == pytest.approx(sell, abs=1e-6)
    assert dr.coverage_ratio == pytest.approx(coverage, abs=1e-6)
    assert P["BUY"] == pytest.approx(share, abs=5e-4)  # 产品 P 展示值 round(4)
    # RISK_ON / TRANSITION 两状态分别断言（矩阵最后两列；首段 dr 不固定 state，
    # 方向以显式状态的两次断言为准）
    dr_on = _decide(monkeypatch, roster, votes, state=MarketState.RISK_ON)
    dr_tr = _decide(monkeypatch, roster, votes, state=MarketState.TRANSITION)
    assert dr_on.decision == on
    assert dr_tr.decision == transition


# ── 坏值在模型构造期拒绝（不 clamp） ────────────────────────

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1.1, -0.1])
def test_s2a_invalid_confidence_rejected_at_model(bad):
    """confidence 只接受有限 [0,1]——NaN/Inf/越界在构造期即拒，不进引擎。"""
    with pytest.raises(Exception):
        SkillSignal(skill_name="slot0", skill_alias="x", signal=B, confidence=bad,
                    skill_type="base")


# ── 固定分母：失败槽占 W；删除其他票不增强；顺序置换不变 ─────

def test_s2a_failed_slot_still_occupies_denominator(monkeypatch):
    """E 四槽但只有一槽返回票（其余加载/执行失败无票）：A=.2 而非 1。"""
    dr = _decide(monkeypatch, FOUR, [("slot0", B, .8)], state=MarketState.RISK_ON)
    assert dr.action_strength["BUY"] == pytest.approx(.2)
    assert dr.coverage_ratio == pytest.approx(.25)
    assert dr.decision == H  # 弱票不得放大成 BUY


def test_s2a_removing_other_votes_does_not_strengthen_fixed_buy(monkeypatch):
    full = [("slot0", B, .8), ("slot1", B, .4), ("slot2", H, .8)]
    dr_full = _decide(monkeypatch, FOUR, full, state=MarketState.RISK_ON)
    dr_less = _decide(monkeypatch, FOUR, full[:-1], state=MarketState.RISK_ON)
    assert dr_less.action_strength["BUY"] == pytest.approx(
        dr_full.action_strength["BUY"])


def test_s2a_permutation_invariant(monkeypatch):
    full = [("slot0", B, .8), ("slot1", B, .4), ("slot2", H, .8)]
    a = _decide(monkeypatch, FOUR, full, state=MarketState.RISK_ON)
    b = _decide(monkeypatch, FOUR, list(reversed(full)), state=MarketState.RISK_ON)
    assert a.action_strength == b.action_strength
    assert a.opinion_share == b.opinion_share
    assert a.coverage_ratio == b.coverage_ratio
    assert a.decision == b.decision


# ── regulator：0 置信度不参与；无基础方向不能凭调节器造买入 ──

def _with_regulator(votes, regs):
    sigs = _vote_signals(votes)
    sigs += [SkillSignal(skill_name=n, skill_alias=n, signal=d, confidence=c,
                         skill_type="regulator") for n, d, c in regs]
    return sigs


def test_s2a_zero_confidence_regulator_no_effect(monkeypatch):
    roster = {"slot0": 1.0}
    votes = [("slot0", B, .01)]
    base = _decide(monkeypatch, roster, votes, state=MarketState.RISK_ON)
    with_zero = _decide(
        monkeypatch, roster, votes, state=MarketState.RISK_ON)
    # 直接构造带零置信 regulator 的调用（绕过 _decide 的 votes-only 封装）
    engine = Orchestrator(enabled_skills=[])
    monkeypatch.setattr(engine.skill_engine, "base_roster",
                        lambda w: (dict(roster), {"slot0": "rv0"}))
    sigs = _with_regulator(votes, [("reg0", S, 0.0)])
    dr = engine.decision_engine.make_decision(
        _sd(), sigs, state=MarketState.RISK_ON,
        expected_base=dict(roster), rule_versions={"slot0": "rv0"},
        evaluation_as_of="2026-10-09")
    assert dr.action_strength["BUY"] == pytest.approx(base.action_strength["BUY"])
    assert dr.decision == base.decision


def test_s2a_regulator_cannot_create_direction_from_nothing(monkeypatch):
    """全缺失 + 强 BUY regulator：U=0 时乘积仍 0——调节器不能造方向。"""
    engine = Orchestrator(enabled_skills=[])
    roster = FOUR
    monkeypatch.setattr(engine.skill_engine, "base_roster",
                        lambda w: (dict(roster), {k: "rv0" for k in roster}))
    sigs = _with_regulator([], [("reg0", B, 1.0)])
    dr = engine.decision_engine.make_decision(
        _sd(), sigs, state=MarketState.RISK_ON,
        expected_base=dict(roster), rule_versions={k: "rv0" for k in roster},
        evaluation_as_of="2026-10-09")
    assert dr.action_strength["BUY"] == 0
    assert dr.decision == H


def test_s2a_confident_regulator_scales_not_creates(monkeypatch):
    """满置信 regulator 放大已有票（1 票 BUY .01 ×1.5 → .015），但不改变资格。"""
    engine = Orchestrator(enabled_skills=[])
    roster = {"slot0": 1.0}
    monkeypatch.setattr(engine.skill_engine, "base_roster",
                        lambda w: (dict(roster), {"slot0": "rv0"}))
    sigs = _with_regulator([("slot0", B, .01)], [("reg0", B, 1.0)])
    dr = engine.decision_engine.make_decision(
        _sd(), sigs, state=MarketState.RISK_ON,
        expected_base=dict(roster), rule_versions={"slot0": "rv0"},
        evaluation_as_of="2026-10-09")
    assert dr.action_strength["BUY"] == pytest.approx(.015)
    assert dr.decision == H


# ── 降级协议：缺 E 显式降级，不按成功票缩分母 ────────────────

def test_s2a_missing_roster_degrades_and_rejects_new_positions(monkeypatch):
    engine = Orchestrator(enabled_skills=[])
    dr = engine.decision_engine.make_decision(
        _sd(), _vote_signals([("slot0", B, 1.0)]), state=MarketState.RISK_ON,
        expected_base=None, evaluation_as_of="2026-10-09")
    assert dr.aggregation_protocol == "legacy-no-roster"
    assert dr.action_strength is None and dr.coverage_ratio is None
    # 普通票无证据：决策=门槛函数对全 0 的自然输出（与冻结矩阵「全缺失」一致），
    # 绝不按成功票（1 票满置信 BUY）放大出方向
    assert dr.decision == H
    assert "不按成功票缩分母" in dr.aggregation_degraded_reason
    assert dr.position_action.value in ("STAY_OUT", "HOLD_POSITION")


def test_s2a_empty_roster_degrades_unknown(monkeypatch):
    engine = Orchestrator(enabled_skills=[])
    dr = engine.decision_engine.make_decision(
        _sd(), _vote_signals([("slot0", B, 1.0)]), state=MarketState.RISK_ON,
        expected_base={}, evaluation_as_of="2026-10-09")
    assert dr.aggregation_protocol == "v1-unknown-roster"
    assert dr.decision == H


# ── 真实链：产品聚合 → 真实 Strategy → 最终 packet ───────────

def _offline_engine(monkeypatch, roster, votes, regs=()):
    """合成技能执行：execute_all 返回固定票；注册表 E 固定。"""
    engine = Orchestrator(enabled_skills=[])
    monkeypatch.setattr(engine.skill_engine, "execute_all",
                        lambda data: _with_regulator(votes, regs))
    monkeypatch.setattr(engine.skill_engine, "base_roster",
                        lambda w: (dict(roster), {k: "rv0" for k in roster}))
    monkeypatch.setattr(engine, "_compute_fundamental_findings", lambda *a: (None, []))
    from src.core.benzong import data_provider
    from src.core.exit_signals import stock as stock_mod
    monkeypatch.setattr(data_provider, "get_market_turnover", lambda *a, **k: 1.0)
    monkeypatch.setattr(stock_mod, "_check_holder_count_surge", lambda code: None)
    monkeypatch.setattr(stock_mod, "_check_margin_surge", lambda code: None)
    return engine


def _risk_on_sd():
    """RISK_ON 市场（沪深300 > 年线且回撤小）。"""
    return _sd(index_close=4000.0, index_ma250=3500.0, index_high_250d=4100.0)


def test_s2a_packet_one_weak_vote_no_new_position(monkeypatch):
    """配置4槽只到1票 BUY .8：A=.20 → HOLD；真实链下不得 OPEN/ADD（旧版会绕门）。"""
    engine = _offline_engine(monkeypatch, FOUR, [("slot0", B, .8)])
    packet, dr, out, _, _ = engine.analyze_packet(
        _risk_on_sd(), confirmed_ratio=.2, position_state="HELD",
        current_position_ratio=.2, has_position=True, trade_plan=_plan(),
        today="2026-10-09", ai_enabled=False)
    assert dr.decision == H
    assert dr.action_strength["BUY"] == pytest.approx(.2)
    assert dr.coverage_ratio == pytest.approx(.25)
    # 旧版：HOLD + buy(.2) > 1.5*sell(0) → ADD。新版：A 不过 BUY 门 → HOLD_POSITION
    assert out.position_action.value != "ADD"
    assert packet.desired_action != DesiredAction.OPEN


def test_s2a_packet_four_buys_open(monkeypatch):
    """4 槽全 BUY .8：A=.8 → BUY；确认通过后空仓 → OPEN（合法多数票仍工作）。

    （既有确认机制要求同向信号连续出现才放行新增——与本批无关，测试内把
    确认次数置 1 以聚焦 S2a 聚合语义。）
    """
    from src.core.strategy_layer import StrategyLayer
    monkeypatch.setattr(StrategyLayer, "CONFIRMATION_MIN_CONSECUTIVE", 1)
    engine = _offline_engine(
        monkeypatch, FOUR, [(f"slot{i}", B, .8) for i in range(4)])
    packet, dr, out, _, _ = engine.analyze_packet(
        _risk_on_sd(), confirmed_ratio=0.0, position_state="FLAT",
        current_position_ratio=0.0, has_position=False, trade_plan=None,
        today="2026-10-09", ai_enabled=False)
    assert dr.decision == B
    assert dr.action_strength["BUY"] == pytest.approx(.8)
    assert dr.coverage_ratio == pytest.approx(1.0)
    assert packet.desired_action == DesiredAction.OPEN


def test_s2a_packet_c0_valid_stop_still_exits(monkeypatch):
    """全缺失（C=0）+ 合格止损：普通票无方向，但独立硬纪律仍 EXIT/CLOSE_ALL。

    场景二走**策略层止损路径**（trade_plan=None → 无 PlanGuard 价格止损救回）：
    guard P1 实证的回归点——is_extreme 强止损条件若只读 A[SELL]（止损覆盖场景
    为 0），首日确认门会把止损 SELL 降 HOLD 延迟一天；修复后 _gate_score 口径
    （override 置信度 0.9 ≥ 0.8）跳过确认当日退出。
    """
    engine = _offline_engine(monkeypatch, FOUR, [])
    packet, dr, out, _, _ = engine.analyze_packet(
        _risk_on_sd(), confirmed_ratio=.2, position_state="HELD",
        current_position_ratio=.2, has_position=True, trade_plan=_plan(),
        today="2026-10-09", ai_enabled=False)
    assert dr.coverage_ratio == pytest.approx(0.0)
    assert dr.decision in (H, W)  # 普通票无方向
    # 场景二：止损一票否决（conf 0.9）+ 持仓亏损事实（entry 12 → price 10 ≈ -16.7%）
    # + 无 trade_plan（排除 PlanGuard 价格止损救回，锁 strategy 止损路径本身）
    engine2 = _offline_engine(monkeypatch, FOUR, [])
    engine2.skill_engine.execute_all = lambda data: [
        SkillSignal(skill_name="stop_loss", skill_alias="止损", signal=S,
                    confidence=0.9, skill_type="action",
                    reason=["跌破止损价"])]
    from src.data.models import StrategyState, TradeLifecycle
    packet2, dr2, out2, _, _ = engine2.analyze_packet(
        _risk_on_sd(), confirmed_ratio=.2, position_state="HELD",
        current_position_ratio=.2, has_position=True, trade_plan=None,
        strategy_state=StrategyState(
            lifecycle=TradeLifecycle.HOLD, entry_price=20.0,
            current_position_ratio=0.2),  # price 10 / entry 20 = -50%：达止损退出档
        today="2026-10-09", ai_enabled=False)
    assert dr2.coverage_ratio == pytest.approx(0.0)  # 覆盖 0 不吞独立退出
    assert dr2.decision == S and dr2.action_override_confidence == pytest.approx(0.9)
    assert packet2.desired_action == DesiredAction.EXIT
    assert out2.position_action.value == "CLOSE_ALL"


def test_s2a_packet_ai_bonus_cannot_bypass_gate(monkeypatch):
    """AI 大幅加分：score（调节后综合分）上升，但普通票资格门读 A——
    HOLD 不被抬成 OPEN/ADD。"""
    engine = _offline_engine(monkeypatch, FOUR, [("slot0", B, .8)])
    engine.ai_modifier = SimpleNamespace(
        is_available=lambda: True,
        analyze=lambda d: AIModifierResult(confidence=1.0, score_adjustment=0.5,
                                           adjusted=True))
    packet, dr, out, ai_result, _ = engine.analyze_packet(
        _risk_on_sd(), confirmed_ratio=.2, position_state="HELD",
        current_position_ratio=.2, has_position=True, trade_plan=_plan(),
        today="2026-10-09", ai_enabled=True)  # AI 层开着（S2a：加分不得绕基础资格）
    assert dr.decision == H
    assert dr.action_strength["BUY"] == pytest.approx(.2)  # A 不含 AI 调节
    assert dr.score > 0.2  # score（调节后综合分）被 AI 抬高
    assert out.position_action.value != "ADD"
    assert packet.desired_action != DesiredAction.OPEN


def test_s2a_packet_trace_final_scores_is_a_with_protocol(monkeypatch):
    """trace 的 final_scores 兼容值 = A 且带协议版本标记。"""
    engine = _offline_engine(monkeypatch, FOUR, [(f"slot{i}", B, .8) for i in range(4)])
    packet, dr, out, _, _ = engine.analyze_packet(
        _risk_on_sd(), confirmed_ratio=.2, position_state="FLAT",
        current_position_ratio=0.0, has_position=False, trade_plan=None,
        today="2026-10-09", ai_enabled=False)
    step = next(t for t in dr.trace if t.step == "市场状态影响")
    assert step.data.get("final_scores_protocol") == "s2a-v1"
    assert step.data["final_scores"]["BUY"] == pytest.approx(.8, abs=1e-6)


def test_s2a_same_slot_duplicate_conflict_packet_uses_abstain(monkeypatch):
    """同槽冲突（同 skill BUY+SELL）：槽弃权保留 W——packet 无方向不新增。"""
    engine = _offline_engine(
        monkeypatch, FOUR, [("slot0", B, .8), ("slot0", S, .8)])
    packet, dr, out, _, _ = engine.analyze_packet(
        _risk_on_sd(), confirmed_ratio=.2, position_state="HELD",
        current_position_ratio=.2, has_position=True, trade_plan=_plan(),
        today="2026-10-09", ai_enabled=False)
    assert dr.action_strength["BUY"] == 0 and dr.action_strength["SELL"] == 0
    assert dr.coverage_ratio == pytest.approx(0.0)
    assert packet.desired_action != DesiredAction.OPEN


# ── 双版本对照（旧归一化语义 vs 新动作强度，逐项解释差异） ────

def test_s2a_old_vs_new_divergence_documented(monkeypatch):
    """同一输入（1/4 槽 BUY .8）：
    旧语义——按方向和归一化，BUY 占比 = 0.8/0.8 = 1.0 → 决策 BUY → 放行新增；
    新语义——动作强度 A = 0.2（3 个空槽占分母）→ 低于 .25 门 → HOLD、不新增。
    差异解释：旧口径把「1/4 覆盖率的一票」放大成「100% 的方向占比」，策略层
    再据此放行仓位——正是 S2a 要修的「1% 把握放大成 100% 强动作」同族缺陷。
    """
    legacy_normalized = {B: 1.0, S: 0.0, H: 0.0, W: 0.0}
    legacy_decision = DecisionEngine()._determine_final_signal(
        legacy_normalized, MarketState.RISK_ON)
    assert legacy_decision == B  # 旧口径：放大成满票 BUY
    dr = _decide(monkeypatch, FOUR, [("slot0", B, .8)], state=MarketState.RISK_ON)
    assert dr.decision == H  # 新口径：A=.2 不过门
    assert dr.action_strength["BUY"] == pytest.approx(.2)
    assert dr.coverage_ratio == pytest.approx(.25)
    # 4 槽全 BUY .8 时全部成功响应：决策同为 BUY（新 A=.80≥.4 强买门；
    # 旧归一化 1.0——方向一致，仅强度值不同，合法多数不受影响）
    dr_full = _decide(monkeypatch, FOUR, [(f"slot{i}", B, .8) for i in range(4)],
                      state=MarketState.RISK_ON)
    assert dr_full.decision == B
    assert dr_full.action_strength["BUY"] == pytest.approx(.8)


def test_s2a_evidence_reads_same_fields(monkeypatch, tmp_path):
    """evidence 落盘字段与 decision_result 同源（protocol/A/C 一致）。"""
    from src.cli import evidence as ev
    monkeypatch.setattr(ev, "_EVIDENCE_FILE", tmp_path / "evidence.jsonl")
    monkeypatch.setattr(ev, "_STATE_DIR", tmp_path)  # 人话卡片也落 tmp
    engine = _offline_engine(monkeypatch, FOUR, [(f"slot{i}", B, .8) for i in range(4)])
    packet, dr, out, _, _ = engine.analyze_packet(
        _risk_on_sd(), confirmed_ratio=0.0, position_state="FLAT",
        current_position_ratio=0.0, has_position=False, trade_plan=None,
        today="2026-10-09", ai_enabled=False)
    rec = ev.record_evidence(dr, out, source="s2a-test")
    assert rec is not None
    assert rec["aggregation_protocol"] == dr.aggregation_protocol == "s2a-v1"
    assert rec["action_strength"]["BUY"] == pytest.approx(
        dr.action_strength["BUY"])
    assert rec["coverage_ratio"] == pytest.approx(dr.coverage_ratio)
