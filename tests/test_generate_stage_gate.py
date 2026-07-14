"""ISS-051 第2层验证: stage 闸门在 generate 实盘入口生效

通过 TradePlanGenerator().generate() (pos add / pos plan 的真实入口) 验证 stage 闸门
不是只落在 _mode_from_grade 孤立函数上, 而是穿透到用户实际触发的 generate 路径:
- A 级 + 个股 S3 顶部 / S4 下跌 -> plan.mode = jianzong (降剑宗, 不死扛)
- A 级 + 个股 S2 上升 -> plan.mode = qizong (不误杀, 气宗长持)
- A 级 + 景气<=0 + S3 -> plan.mode = None (大前提失效优先于 stage 闸门)

构造场景 (不依赖 baostock, 确定性):
- S3 顶部: ma20>ma60 且 price<ma20
- S4 下跌: ma20<ma60 且 price<ma20
- S2 上升: ma20>ma60 且 price>ma20

跑法:
    uv run python tests/test_generate_stage_gate.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.models import StockData
from src.core.trade_plan import TradePlanGenerator


def _sd(**kw):
    """构造 StockData 帮手 (与 test_trade_plan.py 同口径)"""
    defaults = dict(stock_code="X", stock_name="Y", price=10.0, change_pct=0.0,
                    open=10.0, high=10.0, low=10.0, volume=100000)
    defaults.update(kw)
    return StockData(**defaults)


def test_s3_top_a_grade_downgrades_jianzong():
    # S3 顶部: ma20>ma60 且 price<ma20
    sd = _sd(price=9.5, ma20=10.0, ma60=9.8, atr_14=0.2)
    plan, meta = TradePlanGenerator().generate(
        "X", "Y", 10.0, 0.20, stock_data=sd,
        benzong_grade="A", industry_prosperity=60,
    )
    assert meta["weinstein_stage"] == "S3", f"stage 应为 S3, 实际 {meta['weinstein_stage']}"
    assert plan.mode == "jianzong", f"S3 顶部 A 级应降剑宗, 实际 {plan.mode}"
    assert meta["mode"] == "jianzong"
    print(f"✓ S3 顶部 A 级 -> jianzong (stage={meta['weinstein_stage']}, max_hold={plan.max_hold_days}天紧窗口)")


def test_s4_decline_a_grade_downgrades_jianzong():
    # S4 下跌: ma20<ma60 且 price<ma20
    sd = _sd(price=9.0, ma20=9.5, ma60=10.0, atr_14=0.2)
    plan, meta = TradePlanGenerator().generate(
        "X", "Y", 10.0, 0.20, stock_data=sd,
        benzong_grade="A", industry_prosperity=60,
    )
    assert meta["weinstein_stage"] == "S4"
    assert plan.mode == "jianzong", f"S4 下跌 A 级应降剑宗, 实际 {plan.mode}"
    print(f"✓ S4 下跌 A 级 -> jianzong (stage={meta['weinstein_stage']}, 不死扛下跌)")


def test_s2_rise_a_grade_keeps_qizong():
    # S2 上升: ma20>ma60 且 price>ma20
    sd = _sd(price=1500.0, ma20=1480.0, ma60=1450.0, atr_14=35.0)
    plan, meta = TradePlanGenerator().generate(
        "600519", "贵州茅台", 1500.0, 0.20, stock_data=sd,
        benzong_grade="A", industry_prosperity=60,
    )
    assert meta["weinstein_stage"] == "S2"
    assert plan.mode == "qizong", f"S2 上升 A 级应气宗, 实际 {plan.mode}"
    assert plan.max_hold_days >= 180, f"气宗持有期应>=180, 实际 {plan.max_hold_days}"
    print(f"✓ S2 上升 A 级 -> qizong (max_hold={plan.max_hold_days}天, 宽止损长持)")


def test_prosperity_zero_disables_before_stage():
    # 景气<=0 大前提失效, 即使 S3 也不设 mode (大前提优先于 stage 闸门)
    sd = _sd(price=9.5, ma20=10.0, ma60=9.8, atr_14=0.2)
    plan, meta = TradePlanGenerator().generate(
        "X", "Y", 10.0, 0.20, stock_data=sd,
        benzong_grade="A", industry_prosperity=0,
    )
    assert meta["weinstein_stage"] == "S3"
    assert plan.mode is None, f"景气<=0 大前提失效应 None, 实际 {plan.mode}"
    print(f"✓ 景气<=0 + S3 -> None (大前提失效优先于 stage 闸门, 不强加气宗纪律)")


def test_2022_coal_s2_not_false_killed():
    # 2022 煤炭场景: 大盘熊但个股牛 = 个股级 S2 上升期, 闸门不误杀
    sd = _sd(price=20.0, ma20=18.0, ma60=15.0, atr_14=0.5)
    plan, meta = TradePlanGenerator().generate(
        "601088", "中国神华", 20.0, 0.20, stock_data=sd,
        benzong_grade="A", industry_prosperity=70,
    )
    assert meta["weinstein_stage"] == "S2"
    assert plan.mode == "qizong", f"2022煤炭个股级S2应气宗(不误杀), 实际 {plan.mode}"
    print(f"✓ 2022煤炭场景个股级 S2 -> qizong (不误杀, 个股级而非大盘级的价值)")


if __name__ == "__main__":
    print("\n=== ISS-051 第2层: generate 实盘入口 stage 闸门验证 ===\n")
    test_s2_rise_a_grade_keeps_qizong()
    test_s3_top_a_grade_downgrades_jianzong()
    test_s4_decline_a_grade_downgrades_jianzong()
    test_prosperity_zero_disables_before_stage()
    test_2022_coal_s2_not_false_killed()
    print("\n=== 全部 PASS (stage 闸门在 generate 路径生效) ===")
