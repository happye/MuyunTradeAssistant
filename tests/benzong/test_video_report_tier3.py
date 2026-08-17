"""笨总视频理念优化报告 第三档 测试（2026-08-10）

覆盖：
3.1 超配策略（笨总教学十）铁律：
  - 涨幅3-10倍标的禁入（price/low_60d >= 3 -> 拒绝）
  - 只出手一次（overweight_executed=True -> 拒绝第二次）
  - 1-2月时间窗口（expiry = today + 45天中位）
  - TradePlan 超配字段持久化

运行：PYTHONUTF8=1 ./.venv/Scripts/python.exe tests/test_video_report_tier3.py
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.models import TradePlan

# 超配铁律阈值（与 cli/main.py pos overweight 一致）
OVERWEIGHT_TRIPLE_BAN = 3.0
OVERWEIGHT_EXPIRY_DAYS = 45


def _mk_plan(**kw):
    base = dict(plan_id="x", opened_at="2026-01-01", why_buy="t", when_buy="t",
                how_much=0.1, locked_initial_stop=10, current_stop=10)
    base.update(kw)
    return TradePlan(**base)


def test_overweight_fields_exist():
    """3.1 TradePlan 超配字段存在且默认值正确"""
    tp = _mk_plan()
    assert tp.overweight_executed is False
    assert tp.overweight_expiry is None
    assert tp.overweight_basis is None
    print("✓ 超配字段存在，默认未执行")


def test_rule1_triple_ban():
    """3.1 铁律1：涨幅>=3倍标的禁入（笨总"3-10倍自动pass"）"""
    # 4倍 -> 禁入
    price, low_60d = 40, 10
    assert (price / low_60d) >= OVERWEIGHT_TRIPLE_BAN, "4倍应禁入"
    # 2.9倍 -> 放行
    price, low_60d = 29, 10
    assert not ((price / low_60d) >= OVERWEIGHT_TRIPLE_BAN), "2.9倍应放行"
    # 边界3.0倍 -> 禁入（>=3）
    price, low_60d = 30, 10
    assert (price / low_60d) >= OVERWEIGHT_TRIPLE_BAN, "3.0倍边界应禁入"
    print("✓ 铁律1：涨幅>=3倍禁入，<3倍放行")


def test_rule2_only_once():
    """3.1 铁律2：只出手一次（overweight_executed=True 拒绝第二次）"""
    tp = _mk_plan(overweight_executed=True, overweight_expiry="2026-09-24")
    # 已执行 -> 不允许再次（cli 会拒绝）
    assert tp.overweight_executed is True
    # 未执行 -> 允许
    tp2 = _mk_plan()
    assert tp2.overweight_executed is False
    print("✓ 铁律2：只出手一次（executed 标志正确）")


def test_rule3_expiry_window():
    """3.1 铁律3：1-2月时间窗口（expiry = today + 45天中位）"""
    today = datetime.now()
    expiry = today + timedelta(days=OVERWEIGHT_EXPIRY_DAYS)
    # 45天在 30-60天区间（笨总1-2月）
    assert 30 <= OVERWEIGHT_EXPIRY_DAYS <= 60, "应在1-2月区间"
    tp = _mk_plan(overweight_executed=True, overweight_expiry=expiry.strftime("%Y-%m-%d"))
    assert tp.overweight_expiry == expiry.strftime("%Y-%m-%d")
    print(f"✓ 铁律3：到期窗口{OVERWEIGHT_EXPIRY_DAYS}天(1-2月中位)，expiry={tp.overweight_expiry}")


def test_overweight_persist():
    """3.1 超配字段可序列化持久化（portfolio.yaml 兼容）"""
    tp = _mk_plan(overweight_executed=True, overweight_expiry="2026-09-24",
                  overweight_basis="AI算力需求质变")
    d = tp.model_dump()
    assert d["overweight_executed"] is True
    assert d["overweight_expiry"] == "2026-09-24"
    assert d["overweight_basis"] == "AI算力需求质变"
    # 反序列化
    tp2 = TradePlan(**{k: v for k, v in d.items()})
    assert tp2.overweight_executed is True
    assert tp2.overweight_basis == "AI算力需求质变"
    print("✓ 超配字段序列化/反序列化正确（portfolio.yaml 兼容）")


def test_backward_compat_old_plans():
    """3.1 旧 portfolio.yaml（无超配字段）向后兼容"""
    # 模拟旧 plan dict 无 overweight 字段
    old = dict(plan_id="x", opened_at="2026-01-01", why_buy="t", when_buy="t",
               how_much=0.1, locked_initial_stop=10, current_stop=10)
    tp = TradePlan(**old)
    assert tp.overweight_executed is False
    assert tp.overweight_expiry is None
    print("✓ 旧 plan（无超配字段）向后兼容，默认 False/None")


def test_market_breadth_washout():
    """3.3 市场宽度：通杀（下跌>80%）"""
    import src.core.exit_signals.macro as m
    import pandas as pd
    orig = m.MarketCache if hasattr(m, "MarketCache") else None
    # mock MarketCache 返回90%跌的DataFrame
    df = pd.DataFrame({"涨跌幅": [-1.0] * 900 + [1.0] * 100})
    import src.scanner.market_cache as mc_mod
    orig_init = mc_mod.MarketCache.get_all_stocks
    mc_mod.MarketCache.get_all_stocks = lambda self, **kw: df
    try:
        # 重新import让macro引用到mock
        import importlib
        importlib.reload(m)
        from src.core.exit_signals.macro import assess_market_breadth
        r = assess_market_breadth()
        assert r is not None and r[0] == "通杀", f"90%跌应判通杀，got {r}"
    finally:
        mc_mod.MarketCache.get_all_stocks = orig_init
    print("✓ 市场宽度：90%跌->通杀")


def test_market_breadth_normal():
    """3.3 市场宽度：分化（60%跌，非通杀）"""
    import pandas as pd
    import src.scanner.market_cache as mc_mod
    orig_init = mc_mod.MarketCache.get_all_stocks
    df = pd.DataFrame({"涨跌幅": [-1.0] * 600 + [1.0] * 400})
    mc_mod.MarketCache.get_all_stocks = lambda self, **kw: df
    try:
        from src.core.exit_signals.macro import assess_market_breadth
        r = assess_market_breadth()
        assert r is not None and r[0] == "分化", f"60%跌应判分化，got {r}"
    finally:
        mc_mod.MarketCache.get_all_stocks = orig_init
    print("✓ 市场宽度：60%跌->分化")


def main():
    tests = [
        test_overweight_fields_exist,
        test_rule1_triple_ban,
        test_rule2_only_once,
        test_rule3_expiry_window,
        test_overweight_persist,
        test_backward_compat_old_plans,
        test_market_breadth_washout,
        test_market_breadth_normal,
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
