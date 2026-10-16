"""T05 read-only market snapshot disk-L2 hit count.

The source is a local stand-in. Disk cache is pointed at the test tmp
path and enabled only for this count. Production code is not changed,
and the real ~/.muyun/market_cache is not used.
"""
import sys
import threading
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30


def _frame():
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [10.0] * N,
    })


def test_t05_market_disk_l2_hit_count(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    fetches = {"n": 0}

    def sina(self):
        fetches["n"] += 1
        return _frame()

    def backup(self):
        raise AssertionError("disk hit must not call the backup source")

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
        first = cache.get_all_stocks()
        first_fetches = fetches["n"]
        MarketCache._shared_stock_df = None
        MarketCache._shared_stock_ts = 0.0
        MarketCache._shared_stock_count = 0
        second = cache.get_all_stocks()
        disk_fetches = fetches["n"] - first_fetches
        third = cache.get_all_stocks()
        memory_fetches = fetches["n"] - first_fetches - disk_fetches
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
    assert len(first) == N and len(second) == N
    assert third is second
    assert first_fetches == 1
    assert disk_fetches == 0
    assert memory_fetches == 0
    assert len(files) == 1
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_DISK_L2 rows={N} first_fetches={first_fetches} "
        f"disk_hits=1 disk_fetches={disk_fetches} memory_hits_after_disk=1 "
        f"memory_fetches={memory_fetches} files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_DISK_L2" in captured.out
    sys.stderr.write(captured.out)
