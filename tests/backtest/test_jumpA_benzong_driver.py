"""跳法A 阶段1+2 单元测试 — 笨总当主驾（mode 链路 + 高位止盈3维度）

覆盖：
- 阶段1.1 generator 按笨总 grade 定 mode（_mode_from_grade + generate_plan_draft）
- 阶段2.1 exit_signals 宏观/个股大顶信号
- 阶段2.4 PlanGuard P1 top_signal 不可压（气宗持有期内也强制离场）
- 技术层降级 strategy_layer 气宗走固定长持参数

跑法：
    PYTHONUTF8=1 PYTHONPATH=. uv run python tests/test_jumpA_benzong_driver.py
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.models import (
    StockData, StrategyState, StrategyDecision, SignalType, PositionAction,
    TradeLifecycle, TradePlan, MarketState,
)
from src.core.trade_plan.generator import (
    generate_plan_draft, _mode_from_grade,
    QIZONG_MIN_HOLD_DAYS, JIANZONG_MAX_HOLD_DAYS,
)
from src.core.exit_signals import check_macro_top_signal, check_stock_top_signal, check_top_signals
from src.core.plan_guard import PlanGuard
from src.core.strategy_layer import StrategyLayer
from src.core.benzong.scorer import score_one


# ========== 景气度大前提硬约束（effective_grade）==========

def test_effective_grade_prosperity_gate_30():
    """景气度≤30 → 实际等级最高 C（即使原始 A）"""
    s = score_one(30, 100, 100, 90, 100, 0, 1.6)
    assert s.grade() == "A"
    assert s.effective_grade() == "C"


def test_effective_grade_prosperity_zero_to_f():
    """景气度=0 → 模型失效 → F"""
    s = score_one(0, 100, 100, 100, 100, 0, 1.6)
    assert s.effective_grade() == "F"


def test_effective_grade_prosperity_50_cap_b():
    """景气度≤50 → 最高 B"""
    s = score_one(50, 100, 100, 100, 100, 0, 1.6)
    assert s.effective_grade() == "B"


def test_effective_grade_high_prosperity_no_downgrade():
    """景气度>50 → 不降级，取原始等级"""
    s = score_one(70, 100, 100, 90, 90, 0, 1.6)
    assert s.effective_grade() == s.grade()


def test_normalized_score_caps_100():
    """归一化分上限 100（原始 144 满分压到 100）"""
    s = score_one(100, 100, 100, 100, 100, 0, 1.6)  # 原始超100
    assert s.total_score > 100
    assert s.normalized_score() <= 100.0


def test_mode_uses_effective_grade_path():
    """景气度低的 A 级股，effective_grade=C，_mode_from_grade(C) 不应设气宗"""
    s = score_one(30, 100, 100, 90, 100, 0, 1.6)
    assert _mode_from_grade(s.effective_grade(), s.industry_prosperity) is None


# ========== 跳法A 阶段4：MarketState 闸门（已回退，保留参数但不用）==========
# 回测证明 MarketState 一刀切误杀"大盘熊个股牛"(2022煤炭)的气宗收益，故回退。
# market_state 参数保留向后兼容，但不参与判定。

def test_mode_qizong_regardless_of_market_state():
    """A级股气宗不受 market_state 影响（闸门已回退，防矫枉过正）"""
    assert _mode_from_grade("A", 70, market_state="RISK_ON") == "qizong"
    assert _mode_from_grade("A", 70, market_state="RISK_OFF") == "qizong"  # 回退后不降级
    assert _mode_from_grade("A", 70, market_state="TRANSITION") == "qizong"


def test_mode_no_market_state_backward_compat():
    """不传 market_state → A级气宗"""
    assert _mode_from_grade("A", 70, market_state=None) == "qizong"


def test_mode_jianzong_unaffected_by_market_state():
    """B级股本就剑宗，市况不影响"""
    assert _mode_from_grade("B", 70, market_state="RISK_OFF") == "jianzong"
    assert _mode_from_grade("B", 70, market_state="RISK_ON") == "jianzong"


def _sd(**kw):
    defaults = dict(stock_code="X", stock_name="Y", price=10.0, change_pct=0.0,
                    open=10.0, high=10.0, low=10.0, volume=100000)
    defaults.update(kw)
    return StockData(**defaults)


# ========== 阶段1.1 grade → mode ==========

def test_mode_from_grade_a_qizong():
    assert _mode_from_grade("A", 70) == "qizong"


def test_mode_from_grade_a_zero_prosperity_invalidates():
    """A 级但行业景气=0 → 大前提失效，不设气宗"""
    assert _mode_from_grade("A", 0) is None


def test_mode_from_grade_b_jianzong():
    assert _mode_from_grade("B", 70) == "jianzong"


def test_mode_from_grade_c_none():
    assert _mode_from_grade("C", 90) is None
    assert _mode_from_grade(None, None) is None


def test_generate_qizong_plan_long_hold():
    plan = generate_plan_draft("600989", "test", 100.0, 0.2,
                               benzong_grade="A", industry_prosperity=70, today="2026-06-25")
    assert plan.mode == "qizong"
    assert plan.max_hold_days >= QIZONG_MIN_HOLD_DAYS
    # 气宗失效条件只留致命止损 + 高位止盈，不含技术失效条件
    assert any("致命" in c for c in plan.when_sell_invalidate)
    assert not any("MA60" in c or "MA20" in c for c in plan.when_sell_invalidate)


def test_generate_jianzong_plan_short_hold():
    plan = generate_plan_draft("600989", "test", 100.0, 0.2,
                               benzong_grade="B", industry_prosperity=70, today="2026-06-25")
    assert plan.mode == "jianzong"
    assert plan.max_hold_days <= JIANZONG_MAX_HOLD_DAYS


def test_generate_no_grade_backward_compat():
    """不传 grade → mode=None，行为与现状等价"""
    plan = generate_plan_draft("600989", "test", 100.0, 0.2, today="2026-06-25")
    assert plan.mode is None


# ========== 阶段2.1 exit_signals ==========

def test_macro_turnover_top():
    assert check_macro_top_signal(market_turnover_trillion=11.0) is not None
    assert check_macro_top_signal(market_turnover_trillion=8.0) is None


def test_stock_shrink_acceleration():
    """缩量(量比<0.7) + 加速(涨幅>15%) → 触发"""
    sd = _sd(volume=500, avg_volume_5=1000, change_pct=18.0)
    r = check_stock_top_signal(sd, "X")
    assert r is not None and "缩量加速" in r


def test_stock_holder_reduction():
    anns = [{"title": "某公司控股股东拟减持不超过2%股份"}]
    r = check_stock_top_signal(_sd(volume=1, avg_volume_5=1, change_pct=0.0), "X", announcements=anns)
    assert r is not None and "实控人减持" in r


def test_stock_no_signal():
    sd = _sd(volume=1000, avg_volume_5=1000, change_pct=1.0)
    assert check_stock_top_signal(sd, "X") is None


def test_check_top_signals_macro_priority():
    """宏观信号优先于个股信号返回"""
    sd = _sd(volume=500, avg_volume_5=1000, change_pct=18.0)
    r = check_top_signals(sd, "X", market_turnover_trillion=11.0)
    assert r is not None and "宏观" in r


# ========== 阶段2.4 PlanGuard P1 top_signal 不可压 ==========

def _qizong_plan():
    return TradePlan(
        plan_id="X_2026", opened_at="2026-06-01", why_buy="t", when_buy="t",
        how_much=0.2, locked_initial_stop=80.0, current_stop=80.0,
        max_hold_days=180, mode="qizong",
    )


def _sell_decision(top_signal=None, sell_path=None):
    state = StrategyState(current_position_ratio=0.2, lifecycle=TradeLifecycle.HOLD)
    return StrategyDecision(
        decision=SignalType.SELL, position_action=PositionAction.CLOSE_ALL,
        sell_path=sell_path, top_signal=top_signal, position_ratio=0.0,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.EXIT,
        new_state=state,
    )


def test_planguard_top_signal_not_suppressed_in_qizong():
    """气宗持有期内大顶信号也强制 SELL，不被压制"""
    sd = _sd(price=100.0)  # price > stop(80), 不触发致命止损
    out = PlanGuard().evaluate(_sell_decision(top_signal="宏观:成交额破10万亿"),
                               _qizong_plan(), sd, today="2026-06-10")
    assert out.decision == SignalType.SELL
    assert out.sell_path == "top_signal"
    assert out.position_action == PositionAction.CLOSE_ALL


def test_planguard_weak_sell_still_suppressed_in_qizong():
    """对照：无 top_signal 的 weak_sell 在气宗仍被压制为 HOLD"""
    sd = _sd(price=100.0)
    state = StrategyState(current_position_ratio=0.2, lifecycle=TradeLifecycle.HOLD)
    dec = StrategyDecision(
        decision=SignalType.SELL, position_action=PositionAction.REDUCE,
        sell_path="weak_sell", position_ratio=0.2,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        new_state=state,
    )
    out = PlanGuard().evaluate(dec, _qizong_plan(), sd, today="2026-06-10")
    assert out.decision == SignalType.HOLD  # 被压制


# ========== 技术层降级 MarketState ==========

def test_strategy_qizong_fixed_params():
    """气宗 mode 下走固定长持参数，不随熊市分档"""
    sl = StrategyLayer()
    sl._active_mode = "qizong"
    p = sl._get_state_params(MarketState.RISK_OFF)
    assert p["take_profit_keep"] == 0.85  # 长持口径，非熊市 0.50


def test_strategy_no_mode_uses_market_state():
    """无 mode 时保留原市况分档行为"""
    sl = StrategyLayer()
    sl._active_mode = None
    p = sl._get_state_params(MarketState.RISK_OFF)
    assert p["take_profit_keep"] == 0.50  # 熊市档


# ========== Test runner ==========

def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed, failed = 0, []
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed.append((t.__name__, str(e)))

    total = len(tests)
    print(f"\n{'='*50}")
    print(f"通过: {passed} / {total}")
    if failed:
        print(f"失败: {len(failed)}")
        for name, err in failed:
            print(f"  - {name}: {err}")
        sys.exit(1)
    print("=== 跳法A 阶段1+2 全部测试 PASS ===")


if __name__ == "__main__":
    main()
