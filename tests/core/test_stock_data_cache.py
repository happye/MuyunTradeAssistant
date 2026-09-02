"""个股数据短TTL缓存测试（v0.8.8.1，ISS-077）。

背景：chat 对话里 AI 反复分析同一只股票（追问/失败重试轮）此前每次都现爬
1次实时行情+3次K线，高频易被反爬限流。calculate_indicators 加类级 120s
缓存后须锁死的语义：

- 120s 内同 code 第二次调用直接命中（底层不重爬）
- 不同 code 互不干扰
- 完全失败（None）不缓存：AI 失败重试轮仍有恢复机会
- 降级结果（无技术指标，ma5=None）不缓存："重跑一次就好"保持原样
- TTL 过期后重新拉取
- 缓存返回的是同一对象（复用非拷贝，调用方回写字段可见）

纯 mock（patch _calculate_indicators_uncached 计数），无网络。
每个测试经 _clean_cache() 前后清类级缓存——铁律3：类级缓存是全局状态，
泄漏会污染同进程其他测试（实证：全量跑时本文件的 '600519' 缓存条目
让公告测试命中缓存不走真实路径而挂掉）。
"""

import contextlib
import os
import sys
import time

# 确保项目根在 path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.akshare_client import AKShareClient
from src.data.models import StockData


def _full_stock_data(code="600519", ma5=1500.0):
    """完整结果：有技术指标（ma5 非空）。"""
    return StockData(stock_code=code, stock_name="测试股", price=1510.0,
                     volume=1000000, ma5=ma5)


def _degraded_stock_data(code="600519"):
    """降级结果：仅实时行情，无技术指标（calculate_indicators 数据失败时构造）。"""
    return StockData(stock_code=code, stock_name="测试股", price=1510.0,
                     volume=1000000)


def _with_counter(fake_fn):
    """包装成可计数的 classmethod 替身。"""
    calls = {"n": 0}

    def _inner(cls, code, require_historical=False):
        calls["n"] += 1
        return fake_fn(code, require_historical)

    return classmethod(_inner), calls


@contextlib.contextmanager
def _clean_cache():
    """前后都清类级缓存（全局状态卫生，见模块 docstring）。"""
    AKShareClient._stock_data_cache.clear()
    try:
        yield
    finally:
        AKShareClient._stock_data_cache.clear()


@contextlib.contextmanager
def _patched_uncached(fake_fn):
    """临时替换 _calculate_indicators_uncached 为计数替身，退出恢复。"""
    patched, calls = _with_counter(fake_fn)
    orig = AKShareClient._calculate_indicators_uncached
    AKShareClient._calculate_indicators_uncached = patched
    try:
        yield calls
    finally:
        AKShareClient._calculate_indicators_uncached = orig


def test_cache_hit_within_ttl():
    with _clean_cache(), _patched_uncached(lambda c, rh: _full_stock_data()) as calls:
        r1 = AKShareClient.calculate_indicators("600519")
        r2 = AKShareClient.calculate_indicators("600519")
        r3 = AKShareClient.calculate_indicators("600519")
        assert calls["n"] == 1, f"120s内同股应只爬一次，实际 {calls['n']} 次"
        assert r1 is r2 is r3, "缓存命中应返回同一对象（复用非拷贝）"


def test_different_codes_no_cross_hit():
    with _clean_cache(), _patched_uncached(lambda c, rh: _full_stock_data(c)) as calls:
        AKShareClient.calculate_indicators("600519")
        AKShareClient.calculate_indicators("000001")
        AKShareClient.calculate_indicators("600519")  # 已缓存
        assert calls["n"] == 2, f"两只股票各爬一次，实际 {calls['n']} 次"


def test_failure_not_cached():
    """完全失败（None）不缓存——AI 失败重试轮仍有恢复机会。"""
    state = {"fail": True}

    def _fake(code, rh):
        if state["fail"]:
            return None
        return _full_stock_data()

    with _clean_cache(), _patched_uncached(_fake) as calls:
        assert AKShareClient.calculate_indicators("600519") is None
        assert AKShareClient.calculate_indicators("600519") is None  # 未缓存，又打了一次
        state["fail"] = False
        r = AKShareClient.calculate_indicators("600519")
        assert r is not None and r.ma5 == 1500.0, "第三次应恢复出完整数据"
        assert calls["n"] == 3, f"失败不缓存应每次都打接口，实际 {calls['n']} 次"


def test_degraded_result_not_cached():
    """降级结果（无技术指标）不缓存——'重跑一次就好'的恢复机会保持原样。"""
    state = {"degraded": True}

    def _fake(code, rh):
        if state["degraded"]:
            return _degraded_stock_data()
        return _full_stock_data()

    with _clean_cache(), _patched_uncached(_fake) as calls:
        r1 = AKShareClient.calculate_indicators("600519")
        assert r1.ma5 is None, "首次应为降级结果"
        state["degraded"] = False
        r2 = AKShareClient.calculate_indicators("600519")
        assert r2.ma5 == 1500.0, "降级未被缓存，重跑应拿到完整数据"
        assert calls["n"] == 2, f"降级不缓存应重打接口，实际 {calls['n']} 次"


def test_ttl_expiry():
    """超时后重新拉取。"""
    with _clean_cache(), _patched_uncached(lambda c, rh: _full_stock_data()) as calls:
        AKShareClient.calculate_indicators("600519")
        # 把缓存条目时间戳改到 TTL 之前
        stale_ts = time.time() - AKShareClient._STOCK_DATA_TTL - 10
        for k, (_, v) in list(AKShareClient._stock_data_cache.items()):
            AKShareClient._stock_data_cache[k] = (stale_ts, v)
        AKShareClient.calculate_indicators("600519")
        assert calls["n"] == 2, f"过期后应重新拉取，实际 {calls['n']} 次"


def test_code_normalization_for_cache_key():
    """带空格/前缀变体的 code 命中同一缓存键（与 chat _normalize_code 口径对齐）。"""
    with _clean_cache(), _patched_uncached(lambda c, rh: _full_stock_data()) as calls:
        AKShareClient.calculate_indicators("600519")
        AKShareClient.calculate_indicators(" 600519 ")  # 空格 strip 后命中
        assert calls["n"] == 1, f"strip 后同键应命中，实际 {calls['n']} 次"


if __name__ == "__main__":
    import traceback

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
        except Exception as e:
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
