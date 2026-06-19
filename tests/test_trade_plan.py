"""TradePlan 子系统单元测试（v0.8.5 阶段 1.5）

覆盖：
- TradePlan Pydantic 序列化往返
- PositionRecord.trade_plan 字段持久化
- TradePlanGenerator 5 场景（兜底/S2/S4/ATR缺失/高层）
- PlanGuard 6 条规则（4 主规则 + 无 plan + 入参不变）
- TradePlanAdjuster 4 触发器 + apply

跑法：
    .\.venv\Scripts\python.exe tests/test_trade_plan.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.models import (
    StockData, StrategyState, StrategyDecision, SignalType, PositionAction,
    TradeLifecycle, TradePlan
)
from src.data.portfolio import PositionRecord
from src.core.trade_plan import TradePlanGenerator, generate_plan_draft, TradePlanAdjuster
from src.core.plan_guard import PlanGuard


def _sd(**kw):
    """构造 StockData 帮手"""
    defaults = dict(stock_code="X", stock_name="Y", price=10.0, change_pct=0.0,
                    open=10.0, high=10.0, low=10.0, volume=100000)
    defaults.update(kw)
    return StockData(**defaults)


def _make_plan(**kw):
    defaults = dict(
        plan_id="X_2026-06-19", opened_at="2026-06-19",
        why_buy="测试 thesis", when_buy="测试入场", how_much=0.20,
        when_sell_targets=[11.0, 12.0],
        when_sell_invalidate=["跌破 MA60 + 成交量异常放大"],
        locked_initial_stop=9.2, current_stop=9.2,
        max_hold_days=90, fundamental_outlook="bullish",
        thesis_sources=["test"], adjustments=[]
    )
    defaults.update(kw)
    return TradePlan(**defaults)


def _make_strategy_decision(decision, sell_path=None, reasons=None):
    state = StrategyState(current_position_ratio=0.20)
    return StrategyDecision(
        decision=decision,
        position_action=PositionAction.HOLD_POSITION if decision == SignalType.HOLD else PositionAction.REDUCE,
        action_semantic=None, sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        position_ratio=0.20, new_state=state, strategy_reasons=reasons or [],
    )


# ========== 1. TradePlan 模型 ==========

def test_tradeplan_pydantic_creation():
    plan = _make_plan()
    assert plan.plan_id == "X_2026-06-19"
    assert plan.fundamental_outlook == "bullish"
    assert plan.locked_initial_stop == 9.2


def test_tradeplan_yaml_roundtrip():
    """YAML 序列化往返"""
    import yaml
    plan = _make_plan()
    pos = PositionRecord(
        stock_code="600519", stock_name="贵州茅台",
        entry_date="2026-06-19", entry_price=1500.0,
        current_ratio=0.20, trade_plan=plan,
    )
    d = pos.to_dict()
    yaml_str = yaml.safe_dump({"600519": d}, allow_unicode=True)
    loaded = yaml.safe_load(yaml_str)
    pos2 = PositionRecord.from_dict("600519", loaded["600519"])
    assert pos2.trade_plan is not None
    assert pos2.trade_plan.fundamental_outlook == "bullish"
    assert pos2.trade_plan.when_sell_targets == [11.0, 12.0]


def test_tradeplan_backward_compat():
    """旧 portfolio.yaml 无 trade_plan 字段时兼容"""
    old_data = {"stock_name": "X", "entry_price": 10.0, "current_ratio": 0.2}
    pos = PositionRecord.from_dict("000001", old_data)
    assert pos.trade_plan is None


# ========== 2. Generator ==========

def test_generator_fallback_no_data():
    """无 stock_data 时走 8% 兜底"""
    plan = generate_plan_draft("600519", "贵州茅台", 1500.0, 0.20)
    assert plan.locked_initial_stop == 1380.0  # 1500 * 0.92
    assert plan.when_sell_targets == [1650.0, 1800.0]


def test_generator_s2_bullish():
    """S2 牛市完整路径"""
    sd = _sd(price=1500.0, change_pct=1.5, ma20=1480.0, ma60=1450.0,
             atr_14=35.0, macd_dif=2.5, macd_dea=1.8)
    plan = generate_plan_draft("600519", "贵州茅台", 1500.0, 0.20, stock_data=sd)
    assert plan.locked_initial_stop == 1430.0  # 1500 - 2*35
    assert plan.fundamental_outlook == "bullish"
    assert plan.max_hold_days == 135  # 90 * 1.5
    assert "Weinstein S2" in plan.why_buy


def test_generator_s4_bearish():
    """S4 下跌"""
    sd = _sd(price=10.0, change_pct=-2.0, ma20=10.5, ma60=11.0, atr_14=0.3)
    plan = generate_plan_draft("000001", "X", 10.0, 0.10, stock_data=sd)
    assert plan.fundamental_outlook == "bearish"
    assert plan.max_hold_days == 8  # round(15 * 0.5)


def test_generator_atr_missing_fallback():
    sd = _sd(price=20.0, ma20=19.0, ma60=18.0)  # 无 atr_14
    plan = generate_plan_draft("X", "Y", 20.0, 0.20, stock_data=sd)
    assert plan.locked_initial_stop == 18.40  # 20 * 0.92


def test_generator_high_level():
    gen = TradePlanGenerator()
    sd = _sd(price=1500.0, ma20=1480.0, ma60=1450.0, atr_14=35.0)
    plan, meta = gen.generate("600519", "贵州茅台", 1500.0, 0.20, stock_data=sd)
    assert meta["rag_used"] is False
    assert "AI Modifier 未配置" in str(meta["fallback_reasons"])


# ========== 3. PlanGuard ==========

def test_planguard_suppress_weak_sell():
    """规则 1: weak_sell + bullish + 失效未触发 → 压制为 HOLD"""
    sd = _sd(price=10.5, ma20=10.2, ma60=10.0)
    plan = _make_plan()
    strategy = _make_strategy_decision(SignalType.SELL, sell_path="weak_sell")
    result = PlanGuard().evaluate(strategy, plan, sd, today="2026-06-25")
    assert result.decision == SignalType.HOLD
    assert result.sell_path is None
    assert "PlanGuard" in result.strategy_reasons[0]


def test_planguard_bearish_no_suppress():
    """规则 1b: bearish 时不压制"""
    sd = _sd(price=10.5, ma20=10.2, ma60=10.0)
    plan = _make_plan(fundamental_outlook="bearish")
    strategy = _make_strategy_decision(SignalType.SELL, sell_path="weak_sell")
    result = PlanGuard().evaluate(strategy, plan, sd)
    assert result.decision == SignalType.SELL


def test_planguard_invalidate_no_suppress():
    """规则 1c: 失效条件触发不压制"""
    sd = _sd(price=9.5, ma20=10.0, ma60=10.0)  # 跌破 MA60
    plan = _make_plan()
    strategy = _make_strategy_decision(SignalType.SELL, sell_path="weak_sell")
    result = PlanGuard().evaluate(strategy, plan, sd)
    assert result.decision == SignalType.SELL


def test_planguard_take_profit_passthrough():
    """规则 2: take_profit_trim 不动"""
    sd = _sd(price=10.5)
    plan = _make_plan()
    strategy = _make_strategy_decision(SignalType.SELL, sell_path="take_profit_trim")
    result = PlanGuard().evaluate(strategy, plan, sd)
    assert result.decision == SignalType.SELL
    assert result.sell_path == "take_profit_trim"


def test_planguard_max_hold_force_exit():
    """规则 3: 时间止损"""
    sd = _sd(price=10.0)
    plan = _make_plan(opened_at="2025-01-01", max_hold_days=30)  # 远超
    strategy = _make_strategy_decision(SignalType.HOLD)
    result = PlanGuard().evaluate(strategy, plan, sd, today="2026-06-25")
    assert result.decision == SignalType.SELL
    assert "时间止损" in result.strategy_reasons[0]


def test_planguard_fatal_stop():
    """规则 4: 致命止损（HOLD 也覆盖）"""
    sd = _sd(price=8.5)  # 跌破 stop=9.2
    plan = _make_plan()
    strategy = _make_strategy_decision(SignalType.HOLD)
    result = PlanGuard().evaluate(strategy, plan, sd)
    assert result.decision == SignalType.SELL
    assert "致命止损" in result.strategy_reasons[0]


def test_planguard_no_plan_passthrough():
    """无 plan 时直通"""
    sd = _sd(price=10.0)
    strategy = _make_strategy_decision(SignalType.SELL, sell_path="weak_sell")
    result = PlanGuard().evaluate(strategy, None, sd)
    assert result.decision == SignalType.SELL


def test_planguard_input_immutable():
    """入参 strategy_decision 不被修改"""
    sd = _sd(price=10.5, ma60=10.0)
    plan = _make_plan()
    strategy = _make_strategy_decision(SignalType.SELL, sell_path="weak_sell")
    original = strategy.decision
    PlanGuard().evaluate(strategy, plan, sd)
    assert strategy.decision == original


# ========== 4. Adjuster ==========

def test_adjuster_trailing_stop():
    """trailing stop 上移建议"""
    plan = _make_plan()  # current_stop=9.2，targets=[11,12] → entry=10
    sd = _sd(price=11.0, atr_14=0.3)
    sugs = TradePlanAdjuster().suggest(plan, sd, high_since_entry=11.5)
    trail = next((s for s in sugs if s["trigger"] == "auto_trailing"), None)
    assert trail is not None
    assert trail["new"] == 10.9  # 11.5 - 2*0.3
    assert trail["new"] > plan.current_stop


def test_adjuster_no_trailing_when_low_gain():
    """浮盈不足时不触发 trailing"""
    plan = _make_plan()
    sd = _sd(price=10.2, atr_14=0.3)
    sugs = TradePlanAdjuster().suggest(plan, sd, high_since_entry=10.2)
    trail = next((s for s in sugs if s["trigger"] == "auto_trailing"), None)
    assert trail is None


def test_adjuster_target_reached():
    plan = _make_plan()
    sd = _sd(price=11.5, atr_14=0.2)
    sugs = TradePlanAdjuster().suggest(plan, sd, high_since_entry=11.5)
    target = next((s for s in sugs if s["trigger"] == "target_reached"), None)
    assert target is not None
    assert target["new"] == 11.0  # T1


def test_adjuster_max_hold_warning():
    plan = _make_plan(opened_at="2026-04-01", max_hold_days=85)
    sugs = TradePlanAdjuster().suggest(plan, _sd(price=10.0), today="2026-06-19")
    warn = next((s for s in sugs if s["trigger"] == "max_hold_warning"), None)
    assert warn is not None


def test_adjuster_invalidate():
    sd = _sd(price=9.5, ma60=10.0)
    plan = _make_plan()
    sugs = TradePlanAdjuster().suggest(plan, sd)
    inval = next((s for s in sugs if s["trigger"] == "invalidate_triggered"), None)
    assert inval is not None


def test_adjuster_apply_immutable():
    """apply 后原 plan 不变，新 plan 含 adjustments 审计"""
    plan = _make_plan()
    sd = _sd(price=11.0, atr_14=0.3)
    adj = TradePlanAdjuster()
    sugs = adj.suggest(plan, sd, high_since_entry=11.5)
    trail = next((s for s in sugs if s["trigger"] == "auto_trailing"), None)
    new_plan = adj.apply(plan, trail, today="2026-06-19")
    assert new_plan.current_stop == 10.9
    assert plan.current_stop == 9.2  # 原对象不变
    assert len(new_plan.adjustments) == 1
    assert new_plan.adjustments[0]["source"] == "user"


# ========== Test runner ==========

def main():
    """跑所有测试"""
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
    else:
        print(f"=== TradePlan 子系统全部测试 PASS ===")


if __name__ == "__main__":
    main()
