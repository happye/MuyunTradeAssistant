"""笨总视频理念优化报告 第二档 测试（2026-08-10）

覆盖：
2.1 板块层 sector.py：渗透率30%魔咒 + 旗手滞涨（mock 网络）+ 虹吸 TODO
2.2 个股层 stock.py：三倍定律+5日线破位
2.3 check_top_signals 集成（trade_plan 透传触发板块层）

运行：PYTHONUTF8=1 ./.venv/Scripts/python.exe tests/test_video_report_tier2.py
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.exit_signals.sector import (
    check_sector_top_signal, _check_flagbearer_lag, FLAGBEARER_LAG_RATIO, FLAGBEARER_MIN_STOCK_GAIN
)
from src.core.exit_signals.stock import _check_triple_up_rule, TRIPLE_UP_MULTIPLE
from src.core.exit_signals import check_top_signals
from src.data.models import TradePlan, StockData


def _mk_plan(**kw):
    """造一个最小 TradePlan"""
    base = dict(plan_id="x", opened_at="2026-01-01", why_buy="t", when_buy="t",
                how_much=0.1, locked_initial_stop=10, current_stop=10)
    base.update(kw)
    return TradePlan(**base)


def _mk_sd(**kw):
    base = dict(stock_code="x", stock_name="x", price=10, volume=1000)
    base.update(kw)
    return StockData(**base)


def test_penetration_30_plus_no_longer_force_exit():
    """2.1 渗透率30+已降级为研究提醒（v0.8.28.1 架构师裁决）——不再单独触发强制清仓。

    原断言「30+ 应触发」是误触发事故的根因之一（银行/基建被 AI 标 30+ → 新开仓
    当天即被反复 CLOSE_ALL），见 docs/2026-10-08_决策简报_渗透率30+_误触发强制清仓.md。
    """
    from src.core.exit_signals.sector import penetration_research_reminder
    p = _mk_plan(penetration_stage="30+")
    assert check_sector_top_signal(trade_plan=p) is None, "30+ 已降级，不得再触发强制清仓"
    reminder = penetration_research_reminder(p)
    assert reminder is not None and "缺少可核实依据" in reminder
    print("✓ 渗透率30+已降级为研究提醒（不再强制清仓）")


def test_penetration_below_30_no_trigger():
    """2.1 渗透率<30%不触发"""
    for stage in ("0-1", "1-10", "10-30", None):
        p = _mk_plan(penetration_stage=stage)
        assert check_sector_top_signal(trade_plan=p) is None, f"{stage} 不应触发"
    print("✓ 渗透率0-1/1-10/10-30/None 不触发")


def test_no_trade_plan_skip():
    """2.1 无 trade_plan 板块层跳过"""
    assert check_sector_top_signal(trade_plan=None) is None
    print("✓ 无 plan 板块层跳过")


def test_flagbearer_lag_triggers(monkeypatch_fetch=None):
    """2.1 旗手滞涨触发（mock 网络）"""
    p = _mk_plan(flagbearer_code="600519")
    # 标的涨50%，旗手只涨5%（< 50*0.33=16.5）-> 滞涨
    sd = _mk_sd(price=15, change_20d=50.0)
    import src.core.exit_signals.sector as sec
    orig = sec._fetch_20d_change_pct
    sec._fetch_20d_change_pct = lambda c: 5.0
    try:
        sig = _check_flagbearer_lag("600519", sd, "x")
        assert sig is not None and "旗手" in sig, f"应触发滞涨，got {sig}"
    finally:
        sec._fetch_20d_change_pct = orig
    print("✓ 旗手滞涨触发（旗手5% vs 标的50%）")


def test_flagbearer_no_lag_when_stock_low_gain():
    """2.1 标的涨幅不足(<10%)不判旗手滞涨（低位不算见顶）"""
    p = _mk_plan(flagbearer_code="600519")
    sd = _mk_sd(price=11, change_20d=5.0)  # 标的只涨5%
    assert _check_flagbearer_lag("600519", sd, "x") is None
    print("✓ 标的涨幅<10% 不判旗手滞涨（低位保护）")


def test_flagbearer_no_lag_when_leader_rising():
    """2.1 旗手同步上涨不判滞涨"""
    sd = _mk_sd(price=15, change_20d=50.0)
    import src.core.exit_signals.sector as sec
    orig = sec._fetch_20d_change_pct
    sec._fetch_20d_change_pct = lambda c: 40.0  # 旗手涨40%，> 50*0.33
    try:
        assert _check_flagbearer_lag("600519", sd, "x") is None
    finally:
        sec._fetch_20d_change_pct = orig
    print("✓ 旗手同步上涨不判滞涨")


def test_triple_up_triggers():
    """2.2 三倍定律触发（4倍+破MA5）"""
    sd = _mk_sd(price=40, ma5=42, low_60d=10)  # 4倍 + price<MA5
    assert _check_triple_up_rule(sd) is not None
    assert "三倍定律" in _check_triple_up_rule(sd)
    print("✓ 三倍定律触发（4倍涨+破5日线）")


def test_triple_up_no_trigger_below_multiple():
    """2.2 涨幅不足3倍不触发"""
    sd = _mk_sd(price=25, ma5=26, low_60d=10)  # 2.5倍
    assert _check_triple_up_rule(sd) is None
    print("✓ 涨幅<3倍不触发")


def test_triple_up_no_trigger_above_ma5():
    """2.2 涨3倍但未破MA5不触发"""
    sd = _mk_sd(price=35, ma5=30, low_60d=10)  # 3.5倍但 price>MA5
    assert _check_triple_up_rule(sd) is None
    print("✓ 3倍+但未破MA5不触发")


def test_top_signals_integration_sector(monkeypatch):
    """2.3 集成：trade_plan 透传到板块层——30+ 降级后不再产生强制信号（v0.8.28.1）。

    原断言「渗透率 30+ 触发」即误触发事故根因本身（银行/基建被标 30+ → 新开仓
    当天反复 CLOSE_ALL），已按架构师裁决改为验证降级。提醒路径由
    penetration_research_reminder 单测覆盖（test_sector_penetration_demote.py）。
    """
    from src.core.benzong import data_provider
    monkeypatch.setattr(data_provider, "get_market_turnover", lambda *a, **k: 1.0)
    p = _mk_plan(penetration_stage="30+")
    sd = _mk_sd(price=10)
    sig = check_top_signals(sd, "x", trade_plan=p, live=True)
    assert sig is None, "30+ 标签不得再单独触发强制清仓信号"
    print("✓ check_top_signals 集成：渗透率30+不再触发（降级为研究提醒）")


def test_top_signals_integration_triple():
    """2.3 集成：无 plan 时个股三倍定律触发"""
    sd = _mk_sd(price=40, ma5=42, low_60d=10)
    sig = check_top_signals(sd, "x", trade_plan=None)
    assert sig is not None and "三倍定律" in sig
    print("✓ check_top_signals 无 plan 时个股三倍定律触发")


def main():
    tests = [
        test_penetration_30_plus_triggers,
        test_penetration_below_30_no_trigger,
        test_no_trade_plan_skip,
        test_flagbearer_lag_triggers,
        test_flagbearer_no_lag_when_stock_low_gain,
        test_flagbearer_no_lag_when_leader_rising,
        test_triple_up_triggers,
        test_triple_up_no_trigger_below_multiple,
        test_triple_up_no_trigger_above_ma5,
        test_top_signals_integration_sector,
        test_top_signals_integration_triple,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            import traceback
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print()
    if failed:
        print(f"=== 失败 {failed} / {len(tests)} ===")
        sys.exit(1)
    print(f"=== 全部 {len(tests)} 项 PASS ===")


if __name__ == "__main__":
    main()
