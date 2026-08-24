"""MarketCache L1 快照类级共享回归测试（v0.8.7.2 修复验证）

背景：快照缓存曾是实例属性，macro/benzong 等调用方每次 MarketCache() 新建实例
→ TTL 永不命中 → 每次 l/la 都 73 页全量拉新浪，高频触发反爬限流。
本测试锁死：跨实例共享命中、force_refresh 绕过、失败冷却。

跑法：pytest tests/core/test_market_cache_shared.py 或直接 python 执行（自带 sys.path 修复）。
mock 说明：只 mock 网络拉取方法（_fetch_sina_market/_fetch_efinance_market），
被测对象是缓存共享语义本身，与数据正确性无关。
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pandas as pd
import unittest.mock as mock

from src.scanner.market_cache import MarketCache


def _fake_df(rows: int = 5) -> pd.DataFrame:
    return pd.DataFrame({"代码": [f"{600000 + i:06d}" for i in range(rows)], "成交额": [1e8] * rows})


def _reset_shared():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0


def test_cross_instance_cache_hit():
    """实例A成功拉取后，新实例B必须命中共享缓存，不再打网络。"""
    _reset_shared()
    calls = {"sina": 0}

    def fake_sina(self):
        calls["sina"] += 1
        return _fake_df()

    with mock.patch.object(MarketCache, "_fetch_sina_market", fake_sina), \
         mock.patch.object(MarketCache, "_fetch_efinance_market", return_value=None):
        a = MarketCache()
        df_a = a.get_all_stocks()
        assert not df_a.empty and calls["sina"] == 1, "首次应真实拉取"

        b = MarketCache()  # 关键：新实例（旧实现下这里会重新打网络）
        df_b = b.get_all_stocks()
        assert calls["sina"] == 1, f"跨实例应命中共享缓存，实际 sina 被调 {calls['sina']} 次"
        assert len(df_b) == len(df_a)

        c = MarketCache()
        c.get_all_stocks()
        assert calls["sina"] == 1, "第三个实例仍不应打网络"
    print("PASS 跨实例缓存命中")


def test_force_refresh_bypasses_shared():
    """force_refresh=True 应绕过共享缓存重新拉取并回写。"""
    _reset_shared()
    calls = {"sina": 0}

    def fake_sina(self):
        calls["sina"] += 1
        return _fake_df()

    with mock.patch.object(MarketCache, "_fetch_sina_market", fake_sina), \
         mock.patch.object(MarketCache, "_fetch_efinance_market", return_value=None):
        MarketCache().get_all_stocks()
        MarketCache().get_all_stocks(force_refresh=True)
        assert calls["sina"] == 2, "force_refresh 应绕过共享缓存"
    print("PASS force_refresh 绕过")


def test_failure_cooldown():
    """双源全失败且无旧缓存时：进入60秒冷却，冷却期内后续调用不再打网络。"""
    _reset_shared()
    MarketCache.FAIL_COOLDOWN_SECONDS = 60  # 显式固定，防将来改默认值影响断言
    calls = {"sina": 0}

    def fake_sina(self):
        calls["sina"] += 1
        return None  # 模拟全空

    def fake_efu(self):
        return None

    with mock.patch.object(MarketCache, "_fetch_sina_market", fake_sina), \
         mock.patch.object(MarketCache, "_fetch_efinance_market", fake_efu):
        df1 = MarketCache().get_all_stocks()
        assert df1.empty and calls["sina"] == 3, "首败应有3次重试"
        n_after_first = calls["sina"]

        df2 = MarketCache().get_all_stocks()
        assert df2.empty
        assert calls["sina"] == n_after_first, "冷却期内不应再打源"

        # force_refresh 不受冷却限制
        MarketCache().get_all_stocks(force_refresh=True)
        assert calls["sina"] > n_after_first, "force_refresh 应无视冷却"

    print("PASS 失败冷却")


def test_stale_fallback_prefers_old_cache():
    """有旧缓存时双源失败 → 返回过期缓存兜底（且不进入冷却）。"""
    _reset_shared()
    seq = {"n": 0}

    def fake_sina(self):
        seq["n"] += 1
        if seq["n"] == 1:
            return _fake_df(3)
        return None  # 之后全挂

    with mock.patch.object(MarketCache, "_fetch_sina_market", fake_sina), \
         mock.patch.object(MarketCache, "_fetch_efinance_market", return_value=None):
        MarketCache()._shared_stock_ts -= 10 * 3600  # 人为把共享缓存变过期
        df = MarketCache().get_all_stocks()
        assert not df.empty and len(df) == 3, "应返回过期缓存兜底"
        assert MarketCache._shared_fail_ts == 0.0, "有过期缓存兜底时不应记冷却"
    print("PASS 过期缓存兜底优先于冷却")


if __name__ == "__main__":
    test_cross_instance_cache_hit()
    test_force_refresh_bypasses_shared()
    test_failure_cooldown()
    test_stale_fallback_prefers_old_cache()
    print("\n4/4 全部通过")
