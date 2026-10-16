"""T05 read-only in-memory market snapshot hit count.

The snapshot source is a local stand-in. Pytest already disables the
disk cache, so this does not read or write ~/.muyun/market_cache.
Production code is not changed.
"""
import sys
import threading
import tracemalloc

import pandas as pd

from src.scanner.market_cache import MarketCache

N = 30
REPEATS = 10


def _frame():
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [10.0] * N,
    })


def test_t05_market_snapshot_memory_hits(capsys):
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

    def efinance(self):
        raise AssertionError("memory hit must not call the backup source")

    original_sina = MarketCache._fetch_sina_market
    original_efinance = MarketCache._fetch_efinance_market
    MarketCache._fetch_sina_market = sina
    MarketCache._fetch_efinance_market = efinance
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
        hits = 0
        for _ in range(REPEATS):
            again = cache.get_all_stocks()
            assert again is first
            hits += 1
        repeat_fetches = fetches["n"] - first_fetches
        refreshed = cache.get_all_stocks(force_refresh=True)
        force_fetches = fetches["n"] - first_fetches - repeat_fetches
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        MarketCache._fetch_sina_market = original_sina
        MarketCache._fetch_efinance_market = original_efinance
        (
            MarketCache._shared_stock_df,
            MarketCache._shared_stock_ts,
            MarketCache._shared_stock_count,
            MarketCache._shared_fail_ts,
        ) = saved
    threads_after = threading.active_count()
    assert len(first) == N and len(refreshed) == N
    assert first_fetches == 1
    assert repeat_fetches == 0
    assert hits == REPEATS
    assert force_fetches == 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_SNAPSHOT_HITS rows={N} first_fetches={first_fetches} "
        f"repeat_hits={hits} repeat_fetches={repeat_fetches} "
        f"force_refresh_fetches={force_fetches} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_SNAPSHOT_HITS" in captured.out
    sys.stderr.write(captured.out)
