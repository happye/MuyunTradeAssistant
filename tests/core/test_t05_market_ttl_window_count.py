"""T05 read-only full-market snapshot trading-hours TTL miss count.

The source is a local stand-in. The wall clock stays at a Friday 10:00
so the path is the intraday TTL, not the after-close long cache. Expiry
is age > ttl: 300s still hits, 301s misses memory and disk. Disk cache
is the test tmp path. Production code is not changed, and the real
~/.muyun/market_cache is not used.
"""
import sys
import threading
import tracemalloc
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
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [10.0] * N,
    })


def test_t05_market_snapshot_trading_ttl_window(tmp_path, monkeypatch, capsys):
    assert MarketCache.DEFAULT_TRADING_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    clock = _Clock()
    fetches = {"sina": 0, "backup": 0}

    def sina(self):
        fetches["sina"] += 1
        return _frame()

    def backup(self):
        fetches["backup"] += 1
        raise AssertionError("trading TTL path must not call the backup source")

    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        assert cache._ttl == TTL
        assert cache.is_trading_hours()
        first = cache.get_all_stocks()
        fresh_fetches = fetches["sina"]
        clock.now = BASE + TTL
        assert cache._is_expired(BASE) is False
        memory_hit = cache.get_all_stocks()
        after_memory = fetches["sina"]
        MarketCache._shared_stock_df = None
        MarketCache._shared_stock_ts = 0.0
        MarketCache._shared_stock_count = 0
        disk_hit = cache.get_all_stocks()
        after_disk = fetches["sina"]
        clock.now = BASE + TTL + 1
        assert cache._is_expired(BASE) is True
        expired = cache.get_all_stocks()
        after_expired = fetches["sina"]
        renewed_ts = MarketCache._shared_stock_ts
        clock.now = renewed_ts + TTL
        assert cache._is_expired(renewed_ts) is False
        renewed = cache.get_all_stocks()
        after_renewed = fetches["sina"]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        (
            MarketCache._shared_stock_df,
            MarketCache._shared_stock_ts,
            MarketCache._shared_stock_count,
            MarketCache._shared_fail_ts,
        ) = saved
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    age300_memory_hits = 1 if after_memory == fresh_fetches else 0
    age300_disk_hits = 1 if after_disk == fresh_fetches else 0
    age301_fetches = after_expired - fresh_fetches
    renewed_memory_hits = 1 if after_renewed == after_expired else 0
    assert len(first) == N and len(expired) == N
    assert memory_hit is first
    assert disk_hit.equals(first) and disk_hit is not first
    assert expired is not first
    assert renewed is expired
    assert fresh_fetches == 1
    assert age300_memory_hits == 1
    assert age300_disk_hits == 1
    assert age301_fetches == 1
    assert renewed_memory_hits == 1
    assert fetches["backup"] == 0
    assert len(files) == 1
    assert files[0].name == "stocks_df.pkl"
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_TTL_WINDOW rows={N} ttl={TTL} cmp=gt "
        f"fresh_fetches={fresh_fetches} age300_memory_hits={age300_memory_hits} "
        f"age300_disk_hits={age300_disk_hits} age301_fetches={age301_fetches} "
        f"renewed_memory_hits={renewed_memory_hits} backup_fetches={fetches['backup']} "
        f"files={len(files)} peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_TTL_WINDOW" in captured.out
    sys.stderr.write(captured.out)
