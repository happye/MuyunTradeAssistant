"""T05 read-only ETF market trading-hours TTL miss count.

The source is a local stand-in. The wall clock stays at a Friday 10:00
so the path is the intraday TTL, not the after-close long cache. Expiry
is age > ttl: 300s still hits, 301s misses. Disk cache is enabled and
pointed at the test tmp path to show this path writes no file. Production
code is not changed, and the real ~/.muyun/market_cache is not used.
"""
import sys
import threading
import tracemalloc
import types
from datetime import datetime

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
BASE = 1_700_000_000.0
TTL = 300


class _Wall(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 16, 10, 0, 0)


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _frame():
    return pd.DataFrame({
        "代码": [f"{510000 + i:06d}" for i in range(N)],
        "最新价": [1.0] * N,
    })


def test_t05_etf_trading_ttl_window(tmp_path, monkeypatch, capsys):
    assert MarketCache.DEFAULT_TRADING_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_etf_df,
        MarketCache._shared_etf_ts,
        MarketCache._shared_etf_count,
    )
    saved_module = sys.modules.get("akshare")
    clock = _Clock()
    fetches = {"n": 0}
    stub = types.ModuleType("akshare")

    def fund_etf_spot_em():
        fetches["n"] += 1
        return _frame()

    stub.fund_etf_spot_em = fund_etf_spot_em
    sys.modules["akshare"] = stub
    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    MarketCache._shared_etf_df = None
    MarketCache._shared_etf_ts = 0.0
    MarketCache._shared_etf_count = 0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        assert cache._ttl == TTL
        assert cache.is_trading_hours()
        first = cache.get_all_etfs()
        fresh_fetches = fetches["n"]
        clock.now = BASE + TTL
        assert cache._is_expired(BASE) is False
        memory_hit = cache.get_all_etfs()
        after_memory = fetches["n"]
        clock.now = BASE + TTL + 1
        assert cache._is_expired(BASE) is True
        expired = cache.get_all_etfs()
        after_expired = fetches["n"]
        renewed_ts = MarketCache._shared_etf_ts
        clock.now = renewed_ts + TTL
        assert cache._is_expired(renewed_ts) is False
        renewed = cache.get_all_etfs()
        after_renewed = fetches["n"]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        if saved_module is None:
            sys.modules.pop("akshare", None)
        else:
            sys.modules["akshare"] = saved_module
        (
            MarketCache._shared_etf_df,
            MarketCache._shared_etf_ts,
            MarketCache._shared_etf_count,
        ) = saved
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    age300_memory_hits = 1 if after_memory == fresh_fetches else 0
    age301_fetches = after_expired - fresh_fetches
    renewed_memory_hits = 1 if after_renewed == after_expired else 0
    assert len(first) == N and list(first["代码"]) == [f"{510000 + i:06d}" for i in range(N)]
    assert memory_hit is first
    assert len(expired) == N and expired is not first
    assert renewed is expired
    assert fresh_fetches == 1
    assert age300_memory_hits == 1
    assert age301_fetches == 1
    assert renewed_memory_hits == 1
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_ETF_TTL_WINDOW rows={N} ttl={TTL} cmp=gt "
        f"fresh_fetches={fresh_fetches} age300_memory_hits={age300_memory_hits} "
        f"age301_fetches={age301_fetches} renewed_memory_hits={renewed_memory_hits} "
        f"disk_files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_ETF_TTL_WINDOW" in captured.out
    sys.stderr.write(captured.out)
