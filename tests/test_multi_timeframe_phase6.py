import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.skill_engine import SkillEngine
from src.data.models import SignalType, StockData


def _build_stock_data(**overrides) -> StockData:
    base = dict(
        stock_code="600519",
        stock_name="贵州茅台",
        price=105.0,
        open=104.0,
        high=106.0,
        low=103.0,
        ma5=102.0,
        ma10=101.0,
        ma20=100.0,
        ma60=98.0,
        volume=1000000,
        avg_volume_20=800000,
        change_pct=1.2,
        monthly={
            "close": 100.0,
            "ma5": 95.0,
            "ma10": 90.0,
            "ma20": 85.0,
            "trend": "BULLISH",
        },
        weekly={
            "close": 102.0,
            "ma5": 101.0,
            "ma10": 100.0,
            "ma20": 99.0,
            "trend": "BULLISH",
        },
    )
    base.update(overrides)
    return StockData(**base)


def test_multi_timeframe_uses_real_monthly_and_weekly_context_for_buy():
    engine = SkillEngine(skills_dir="./src/skills")
    engine.load_skills(["multi_timeframe"])

    signal = engine.get_skill("multi_timeframe").execute(_build_stock_data())

    assert signal.signal == SignalType.BUY
    assert any("月线" in reason for reason in signal.reason)
    assert any("周线" in reason for reason in signal.reason)


def test_multi_timeframe_returns_watch_when_month_up_but_week_down():
    engine = SkillEngine(skills_dir="./src/skills")
    engine.load_skills(["multi_timeframe"])

    signal = engine.get_skill("multi_timeframe").execute(
        _build_stock_data(
            weekly={
                "close": 97.0,
                "ma5": 98.0,
                "ma10": 99.0,
                "ma20": 100.0,
                "trend": "BEARISH",
            }
        )
    )

    assert signal.signal == SignalType.HOLD or signal.signal == SignalType.WATCH
    assert any("周线" in reason for reason in signal.reason)


def test_multi_timeframe_returns_sell_when_month_and_week_turn_down():
    engine = SkillEngine(skills_dir="./src/skills")
    engine.load_skills(["multi_timeframe"])

    signal = engine.get_skill("multi_timeframe").execute(
        _build_stock_data(
            price=95.0,
            ma5=97.0,
            monthly={
                "close": 90.0,
                "ma5": 92.0,
                "ma10": 95.0,
                "ma20": 98.0,
                "trend": "BEARISH",
            },
            weekly={
                "close": 94.0,
                "ma5": 95.0,
                "ma10": 97.0,
                "ma20": 99.0,
                "trend": "BEARISH",
            },
        )
    )

    assert signal.signal == SignalType.SELL
    assert any("月线" in reason for reason in signal.reason)


if __name__ == "__main__":
    test_multi_timeframe_uses_real_monthly_and_weekly_context_for_buy()
    test_multi_timeframe_returns_watch_when_month_up_but_week_down()
    test_multi_timeframe_returns_sell_when_month_and_week_turn_down()
    print("ALL PHASE 6 MULTI-TIMEFRAME TESTS PASSED")