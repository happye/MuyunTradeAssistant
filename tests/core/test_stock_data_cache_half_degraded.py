"""ISS-078 补充：半降级结果（周线/月线缺失但 ma5 在）不得入类级缓存。

背景：v0.8.8.1 缓存写入条件只查 ma5，周/月线拉取失败的半降级 StockData
会被缓存 120s，与包装器 docstring「只缓存完整结果」的承诺不符。

跑法：pytest tests/core/test_stock_data_cache_half_degraded.py
"""
import contextlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.akshare_client import AKShareClient
from src.data.models import StockData


@contextlib.contextmanager
def _clean_cache():
    AKShareClient._stock_data_cache.clear()
    try:
        yield
    finally:
        AKShareClient._stock_data_cache.clear()


@contextlib.contextmanager
def _patched_uncached(fake_fn):
    calls = {"n": 0}

    def _inner(cls, code, require_historical=False):
        calls["n"] += 1
        return fake_fn(code, require_historical)

    orig = AKShareClient._calculate_indicators_uncached
    AKShareClient._calculate_indicators_uncached = classmethod(_inner)
    try:
        yield calls
    finally:
        AKShareClient._calculate_indicators_uncached = orig


def _half_degraded(code="600519"):
    """日线成功（ma5 在）但周线/月线拉取失败——缓存写入点可见的半降级形态。"""
    return StockData(stock_code=code, stock_name="测试股", price=10.0,
                     volume=1000, ma5=9.8, weekly=None, monthly=None)


def test_half_degraded_result_not_cached():
    with _clean_cache(), _patched_uncached(lambda c, rh: _half_degraded()) as calls:
        AKShareClient.calculate_indicators("600519")
        assert len(AKShareClient._stock_data_cache) == 0, "半降级结果不应入缓存"
        AKShareClient.calculate_indicators("600519")
        assert calls["n"] == 2, "半降级结果若入缓存，第二次调用会错误命中跳过重算"


def test_full_result_still_cached():
    with _clean_cache(), _patched_uncached(
            lambda c, rh: StockData(stock_code="600519", stock_name="x", price=10.0,
                                    volume=1000, ma5=9.8, weekly={"c": [1]}, monthly={"c": [1]})):
        AKShareClient.calculate_indicators("600519")
        AKShareClient.calculate_indicators("600519")
        assert len(AKShareClient._stock_data_cache) == 1, "完整结果缓存语义不得回退"
