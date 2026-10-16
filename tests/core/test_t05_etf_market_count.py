"""T05 read-only in-memory ETF market cache hit count.

The ETF source is a local stand-in. get_all_etfs keeps only the class-level
frame and does not read or write ~/.muyun/market_cache. Production code is
not changed.
"""
import sys
import threading
import tracemalloc
import types

import pandas as pd

from src.scanner.market_cache import MarketCache

N = 30
REPEATS = 10


def _frame():
    return pd.DataFrame({
        "代码": [f"{510000 + i:06d}" for i in range(N)],
        "最新价": [1.0] * N,
    })


def test_t05_etf_market_memory_hits(capsys):
    saved = (
        MarketCache._shared_etf_df,
        MarketCache._shared_etf_ts,
        MarketCache._shared_etf_count,
    )
    saved_module = sys.modules.get("akshare")
    fetches = {"n": 0}
    stub = types.ModuleType("akshare")

    def fund_etf_spot_em():
        fetches["n"] += 1
        return _frame()

    stub.fund_etf_spot_em = fund_etf_spot_em
    sys.modules["akshare"] = stub
    MarketCache._shared_etf_df = None
    MarketCache._shared_etf_ts = 0.0
    MarketCache._shared_etf_count = 0
    threads_before = threading.active_count()
    tracemalloc.start()
    cached_count = None
    try:
        cache = MarketCache()
        first = cache.get_all_etfs()
        first_fetches = fetches["n"]
        hits = 0
        for _ in range(REPEATS):
            again = cache.get_all_etfs()
            assert again is first
            hits += 1
        repeat_fetches = fetches["n"] - first_fetches
        refreshed = cache.get_all_etfs(force_refresh=True)
        force_fetches = fetches["n"] - first_fetches - repeat_fetches
        cached_count = MarketCache._shared_etf_count
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
    threads_after = threading.active_count()
    assert len(first) == N and len(refreshed) == N
    assert first_fetches == 1
    assert repeat_fetches == 0
    assert hits == REPEATS
    assert force_fetches == 1
    assert cached_count == N
    assert threads_after <= threads_before + 1
    print(
        f"T05_ETF_MARKET_HITS rows={N} first_fetches={first_fetches} "
        f"repeat_hits={hits} repeat_fetches={repeat_fetches} "
        f"force_refresh_fetches={force_fetches} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_ETF_MARKET_HITS" in captured.out
    sys.stderr.write(captured.out)
