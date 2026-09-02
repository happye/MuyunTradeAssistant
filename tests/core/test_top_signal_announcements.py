"""ISS-052: 激活 top_signal 数据源 -- 实控人减持公告 单测

两层：
1. 信号逻辑（无网络）：_check_holder_reduction / check_stock_top_signal 双关键词 AND
   - 命中：减持词 + 主体词 同时出现
   - 不误报：单关键词（仅减持词 / 仅主体词）不触发
   - announcements=[]/None -> None（回测语义）
2. 接线（mock）：calculate_indicators 把 get_recent_announcements 返回值赋给
   stock_data.recent_announcements；fetch 异常时降 None（fail-open，不崩主流程）

跑法:
    PYTHONUTF8=1 PYTHONPATH=. uv run python tests/test_top_signal_announcements.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pandas as pd

from src.core.exit_signals.stock import (
    check_stock_top_signal, _check_holder_reduction,
)
from src.data.akshare_client import AKShareClient


# ========== 第1层：信号逻辑 ==========

def _ann(title):
    return [{"title": title, "date": "2026-07-01", "content": "", "source": "em"}]


def test_holder_reduction_hit_controlling_shareholder():
    """控股股东 + 拟减持 -> 命中。"""
    assert _check_holder_reduction(_ann("控股股东拟减持公司股份")) is not None


def test_holder_reduction_hit_actual_controller():
    """实际控制人 + 减持 -> 命中。"""
    assert _check_holder_reduction(_ann("实际控制人减持计划")) is not None


def test_holder_reduction_hit_big_shareholder():
    """大股东 + 减持 -> 命中。"""
    assert _check_holder_reduction(_ann("大股东减持")) is not None


def test_holder_reduction_no_holder_keyword_not_fire():
    """仅减持词、无主体词 -> 不误报（审视点：单关键词不触发）。"""
    assert _check_holder_reduction(_ann("普通减持公告")) is None


def test_holder_reduction_no_reduce_keyword_not_fire():
    """仅主体词、无减持词（如增持）-> 不误报。"""
    assert _check_holder_reduction(_ann("控股股东增持")) is None


def test_holder_reduction_empty_or_none():
    """announcements=[]/None -> None（回测语义：跳过减持子信号）。"""
    assert _check_holder_reduction([]) is None


def test_check_stock_top_signal_with_holder_reduction():
    """check_stock_top_signal 透传减持子信号。"""
    sig = check_stock_top_signal(None, "600519", announcements=_ann("控股股东拟减持"))
    assert sig is not None and "实控人减持" in sig


def test_check_stock_top_signal_no_announcements():
    """announcements=None -> 减持子信号跳过（无缩量加速时整体 None）。"""
    # 给一个无缩量加速的 stock_data（volume 充足避免误触缩量加速）
    class _SD:
        volume = 1000.0
        avg_volume_5 = 1000.0
        avg_volume_20 = 1000.0
        change_pct = 1.0
    assert check_stock_top_signal(_SD(), "600519",
                                  announcements=None) is None


# ========== 第2层：接线（calculate_indicators 填充 recent_announcements） ==========


def _clear_stock_data_cache():
    """v0.8.8.1 calculate_indicators 加了类级短TTL缓存；本文件两个接线测试
    同用 code 600519 走生产入口，前一个成功会写缓存让后一个命中旧数据。
    前后各清一次，保证每个测试都走真实路径。"""
    AKShareClient._stock_data_cache.clear()


def _fake_quote(code="600519"):
    return {
        "stock_code": code, "stock_name": "贵州茅台", "price": 1500.0,
        "open": 1490.0, "high": 1510.0, "low": 1485.0,
        "change_pct": 0.5, "volume": 10000,
    }


def _fake_kline_df(rows=70):
    """构造足够算 MA60 的日线 DataFrame（中文列名，对齐 calculate_indicators）。"""
    dates = pd.date_range(end="2026-07-15", periods=rows, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "日期": dates,
        "开盘": [1500.0] * rows,
        "最高": [1510.0] * rows,
        "最低": [1490.0] * rows,
        "收盘": [1505.0] * rows,
        "成交量": [10000] * rows,
    })


def test_calculate_indicators_populates_recent_announcements():
    """calculate_indicators 把 get_recent_announcements 返回值赋给 recent_announcements。"""
    fake_ann = [{"title": "控股股东拟减持", "date": "2026-07-01", "content": "", "source": "em"}]
    _clear_stock_data_cache()
    try:
        with patch.object(AKShareClient, "get_realtime_quote", return_value=_fake_quote()), \
             patch.object(AKShareClient, "get_historical_kline", return_value=_fake_kline_df()), \
             patch.object(AKShareClient, "_get_index_trend", return_value=None), \
             patch.object(AKShareClient, "_build_timeframe_snapshot", return_value=None), \
             patch("src.core.benzong.data_provider.get_recent_announcements", return_value=fake_ann):
            sd = AKShareClient.calculate_indicators("600519")
    finally:
        _clear_stock_data_cache()
    assert sd is not None
    assert sd.recent_announcements == fake_ann


def test_calculate_indicators_fail_open_to_none():
    """get_recent_announcements 抛异常 -> recent_announcements 降 None，主流程不崩。"""
    _clear_stock_data_cache()
    try:
        with patch.object(AKShareClient, "get_realtime_quote", return_value=_fake_quote()), \
             patch.object(AKShareClient, "get_historical_kline", return_value=_fake_kline_df()), \
             patch.object(AKShareClient, "_get_index_trend", return_value=None), \
             patch.object(AKShareClient, "_build_timeframe_snapshot", return_value=None), \
             patch("src.core.benzong.data_provider.get_recent_announcements",
                   side_effect=RuntimeError("network down")):
            sd = AKShareClient.calculate_indicators("600519")
    finally:
        _clear_stock_data_cache()
    assert sd is not None
    assert sd.recent_announcements is None  # fail-open 降级，不崩


def test_stockdata_field_default_none():
    """StockData recent_announcements 缺省 None（回测 DataFeeder 不填 -> 跳过减持子信号）。"""
    from src.data.models import StockData
    sd = StockData(stock_code="600519", stock_name="贵州茅台", price=1500.0, volume=10000)
    assert sd.recent_announcements is None


if __name__ == "__main__":
    # 手动 runner（项目 venv 无 pytest，对齐 test_fundamental_alert.py 风格）
    tests = [
        test_holder_reduction_hit_controlling_shareholder,
        test_holder_reduction_hit_actual_controller,
        test_holder_reduction_hit_big_shareholder,
        test_holder_reduction_no_holder_keyword_not_fire,
        test_holder_reduction_no_reduce_keyword_not_fire,
        test_holder_reduction_empty_or_none,
        test_check_stock_top_signal_with_holder_reduction,
        test_check_stock_top_signal_no_announcements,
        test_calculate_indicators_populates_recent_announcements,
        test_calculate_indicators_fail_open_to_none,
        test_stockdata_field_default_none,
    ]
    print("\n=== ISS-052 top_signal 实控人减持公告 单测 ===\n")
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n=== 全部 {len(tests)} 项 PASS ===")
