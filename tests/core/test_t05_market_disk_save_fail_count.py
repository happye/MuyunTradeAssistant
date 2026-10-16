"""T05 read-only count when the market-snapshot disk write fails.

to_pickle raises before it writes any byte. The caller still receives
the in-memory frame, and the exception does not escape. The backup
source is not called. After memory is cleared the missing file is not
a hit, so Sina runs once more. A failed write is not counted as a
saved snapshot. Production code is not changed, and the real
~/.muyun/market_cache is not used.
"""
import sys
import threading
import time
import tracemalloc
from datetime import datetime

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30


class _Wall(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 16, 10, 0, 0)


def _good(price):
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [price] * N,
    })


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0


def test_t05_market_disk_save_fail_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    fetches = {"sina": 0, "backup": 0}
    writes = {"n": 0}
    slept = {"n": 0}
    made = []

    def sina(self):
        fetches["sina"] += 1
        frame = _good(10.0 + fetches["sina"])
        made.append(frame)
        return frame

    def backup(self):
        fetches["backup"] += 1
        return _good(99.0)

    def boom(*args, **kwargs):
        writes["n"] += 1
        raise OSError("stand-in disk write failed before any byte")

    def no_sleep(seconds):
        slept["n"] += 1
        assert seconds == 1.5

    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(market_cache.pd, "to_pickle", boom)
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    monkeypatch.setattr(time, "sleep", no_sleep)
    _clear_memory()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        assert cache.is_trading_hours()
        first = cache.get_all_stocks()
        after_first_sina = fetches["sina"]
        after_first_writes = writes["n"]
        held = cache.get_all_stocks()
        after_hold_sina = fetches["sina"]
        after_hold_writes = writes["n"]
        _clear_memory()
        second = cache.get_all_stocks()
        after_second_sina = fetches["sina"]
        after_second_writes = writes["n"]
        _, peak = tracemalloc.get_traced_memory()
        assert MarketCache._shared_fail_ts == 0.0
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
    assert first is made[0] and held is first
    assert second is made[1] and second is not first
    assert int((first["最新价"] == 11.0).sum()) == N
    assert int((second["最新价"] == 12.0).sum()) == N
    assert int((second["最新价"] == 99.0).sum()) == 0
    assert after_first_sina == 1 and after_hold_sina == 1 and after_second_sina == 2
    assert fetches["backup"] == 0
    assert after_first_writes == 1 and after_hold_writes == 1 and after_second_writes == 2
    assert slept["n"] == 0
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_DISK_SAVE_FAIL rows={N} first_sina={after_first_sina} "
        f"memory_hits_after_fail=1 memory_fetches={after_hold_sina - after_first_sina} "
        f"after_clear_sina={after_second_sina - after_hold_sina} "
        f"backup={fetches['backup']} write_errors={writes['n']} sleeps={slept['n']} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_DISK_SAVE_FAIL" in captured.out
    sys.stderr.write(captured.out)
