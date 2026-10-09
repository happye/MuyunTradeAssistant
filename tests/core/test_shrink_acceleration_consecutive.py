"""缩量加速连续性增强单测（2026-08-23，未做事项评估§2.2）

规则：当日量比<0.7 且 涨幅>15% 后，若 volume_series 可用（live 路径）还要求
昨日也缩量（昨日量 / 其之前可得日均量 < 0.7）；序列缺失/过短退回单日判定
（回测路径行为不变）。

场景：
1. 单日缩量+大涨+无序列（回测路径）-> 触发（legacy 兜底保留）
2. 连续两日缩量+大涨 -> 触发
3. 今日缩量+大涨但昨日放量 -> 不触发（本次增强核心）
4. 序列长度不足5 -> 触发（legacy 兜底）
5. 非缩量/非大涨 -> 不触发（回归确认未破坏旧判定）

运行：PYTHONUTF8=1 PYTHONPATH=. ./.venv/Scripts/python.exe tests/core/test_shrink_acceleration_consecutive.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.exit_signals.stock import _check_shrink_acceleration
from src.data.models import StockData


def _sd(volume_series=None):
    return StockData(
        stock_code="600519", stock_name="茅台",
        price=100.0, volume=500_000,
        avg_volume_5=800_000,      # 今日量比 = 0.625 < 0.7
        change_pct=16.0,           # 涨幅 > 15%
        volume_series=volume_series,
    )


def test_no_series_falls_back_legacy():
    """E3（ISS-117 S1）：无 volume_series → 不再放宽为单日触发；
    如实标 UNKNOWN 研究线索（旧"legacy 单日判定保留"即缺数据降门缺陷，已按裁决移除）"""
    r = _check_shrink_acceleration(_sd(None))
    assert r is not None and r.data_quality == "UNKNOWN" and r.action_scope == "research"
    assert "连续性证据缺失" in r.detail
    print("✓ 无序列 → UNKNOWN 研究线索（不再放宽为单日触发）")


def test_two_day_shrink_triggers():
    """连续两日缩量：今日0.625、昨日也<0.7 -> 触发"""
    # vs=[d-4,d-3,d-2,d-1,d0]；昨日 d-1=400k，其前3日均量=(900k+850k+800k)/3=850k -> 0.47<0.7
    r = _check_shrink_acceleration(_sd([900_000, 850_000, 800_000, 400_000, 500_000]))
    # ISS-117 A12：缩量加速整体降级 research（不再 CLOSE_ALL）
    assert r is not None and r.action_scope == "research" and "连续2日缩量" in r.detail
    print("✓ 连续两日缩量 → research 发现")


def test_prev_day_not_shrunk_blocks():
    """昨日放量：今日单日缩量不再触发（增强核心）"""
    # 昨日 d-1=1_200_000，前3日均量=(300k+320k+310k)/3≈310k -> 比值~3.9 >= 0.7 -> 阻断
    r = _check_shrink_acceleration(_sd([300_000, 320_000, 310_000, 1_200_000, 500_000]))
    assert r is None, f"昨日放量应阻断, got {r}"
    print("✓ 昨日未缩量正确阻断（单日不构成连续加速）")


def test_short_series_falls_back():
    """E3：序列长度不足5 → UNKNOWN 研究线索（不放宽为单日触发）"""
    r = _check_shrink_acceleration(_sd([500_000, 500_000, 500_000]))
    assert r is not None and r.data_quality == "UNKNOWN" and "连续性证据缺失" in r.detail, f"短序列应为 UNKNOWN 研究线索, got {r}"
    print("✓ 短序列 legacy 兜底")


def test_non_trigger_regressions():
    """回归：量比不够低 / 涨幅不够高 都不触发"""
    sd_low_vol_ratio = _sd(None)
    sd_low_vol_ratio.volume = 750_000   # 量比0.94 >= 0.7
    assert _check_shrink_acceleration(sd_low_vol_ratio) is None
    sd_small_gain = _sd(None)
    sd_small_gain.change_pct = 10.0     # 涨幅 <= 15%
    assert _check_shrink_acceleration(sd_small_gain) is None
    print("✓ 量比/涨幅阈值回归不变")


def main():
    tests = [test_no_series_falls_back_legacy, test_two_day_shrink_triggers,
             test_prev_day_not_shrunk_blocks, test_short_series_falls_back,
             test_non_trigger_regressions]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"✗ {t.__name__}: {e}")
    print(f"\n{'FAIL ' + str(failed) if failed else 'ALL PASS'}: {len(tests) - failed}/{len(tests)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
