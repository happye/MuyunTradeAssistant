"""T05 read-only dual-source failure cooldown count.

Both snapshot sources are local empty stand-ins. The 1.5s retry gap is
not waited, and the count is the number of source calls. Production code
is not changed and the network is not called.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

from src.scanner.market_cache import MarketCache

COOLDOWN_CALLS = 5


def test_t05_market_fail_cooldown_count(monkeypatch, capsys):
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    fetches = {"sina": 0, "backup": 0}
    slept = {"n": 0}

    def sina(self):
        fetches["sina"] += 1
        return pd.DataFrame()

    def backup(self):
        fetches["backup"] += 1
        return pd.DataFrame()

    def no_sleep(seconds):
        slept["n"] += 1

    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    monkeypatch.setattr(time, "sleep", no_sleep)
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = cache.get_all_stocks()
        first_sina = fetches["sina"]
        first_backup = fetches["backup"]
        cooled = []
        for _ in range(COOLDOWN_CALLS):
            cooled.append(cache.get_all_stocks())
        cooldown_sina = fetches["sina"] - first_sina
        cooldown_backup = fetches["backup"] - first_backup
        forced = cache.get_all_stocks(force_refresh=True)
        force_sina = fetches["sina"] - first_sina - cooldown_sina
        force_backup = fetches["backup"] - first_backup - cooldown_backup
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        (
            MarketCache._shared_stock_df,
            MarketCache._shared_stock_ts,
            MarketCache._shared_stock_count,
            MarketCache._shared_fail_ts,
        ) = saved
    threads_after = threading.active_count()
    assert first.empty and forced.empty
    assert all(frame.empty for frame in cooled)
    assert first_sina == 3 and first_backup == 1
    assert cooldown_sina == 0 and cooldown_backup == 0
    assert force_sina == 3 and force_backup == 1
    assert slept["n"] == 4
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_FAIL_COOLDOWN first_sina={first_sina} "
        f"first_backup={first_backup} cooldown_calls={COOLDOWN_CALLS} "
        f"cooldown_fetches={cooldown_sina + cooldown_backup} "
        f"force_sina={force_sina} force_backup={force_backup} "
        f"retry_gaps_skipped={slept['n']} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_FAIL_COOLDOWN" in captured.out
    sys.stderr.write(captured.out)
