"""T05 read-only count when a new snapshot fails to replace an old file.

The first Sina frame is saved for real. to_pickle then raises before
it writes any byte. force_refresh still returns the new frame, and the
exception does not escape. The old file bytes stay the same. After
memory is cleared, the hit is that old file and Sina is not called.
A failed write is not an overwrite. Production code is not changed,
and the real ~/.muyun/market_cache is not used.
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


def test_t05_market_disk_save_old_count(tmp_path, monkeypatch, capsys):
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
    sina_queue = []
    path = cache_dir / "stocks_df.pkl"

    def sina(self):
        fetches["sina"] += 1
        if not sina_queue:
            raise AssertionError("Sina called more times than scripted")
        return sina_queue.pop(0)

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
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    monkeypatch.setattr(time, "sleep", no_sleep)
    _clear_memory()
    threads_before = threading.active_count()
    tracemalloc.start()
    old = _good(10.0)
    fresh = _good(11.0)
    try:
        cache = MarketCache()
        assert cache.is_trading_hours()
        sina_queue.append(old)
        first = cache.get_all_stocks()
        old_bytes = path.read_bytes()
        monkeypatch.setattr(market_cache.pd, "to_pickle", boom)
        sina_queue.append(fresh)
        forced = cache.get_all_stocks(force_refresh=True)
        after_force_sina = fetches["sina"]
        after_force_writes = writes["n"]
        held = cache.get_all_stocks()
        after_hold_sina = fetches["sina"]
        kept = path.read_bytes()
        fail_ts = MarketCache._shared_fail_ts
        _clear_memory()
        revived = cache.get_all_stocks()
        after_revive_sina = fetches["sina"]
        after_revive_writes = writes["n"]
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
    stored = pd.read_pickle(path)
    assert first is old and forced is fresh and held is fresh
    assert revived is not fresh
    assert int((revived["最新价"] == 10.0).sum()) == N
    assert int((stored["data"]["最新价"] == 10.0).sum()) == N
    assert int((stored["data"]["最新价"] == 11.0).sum()) == 0
    assert kept == old_bytes and len(old_bytes) > 0
    assert after_force_sina == 2 and after_hold_sina == 2 and after_revive_sina == 2
    assert fetches["backup"] == 0
    assert after_force_writes == 1 and after_revive_writes == 1
    assert fail_ts == 0.0
    assert slept["n"] == 0
    assert sina_queue == []
    assert len(files) == 1 and files[0].name == "stocks_df.pkl"
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_DISK_SAVE_OLD rows={N} save_sina=1 "
        f"refresh_sina={after_force_sina - 1} refresh_backup=0 "
        f"memory_hits_after_failed_write=1 "
        f"memory_fetches={after_hold_sina - after_force_sina} "
        f"old_disk_hits_after_clear=1 "
        f"revive_fetches={after_revive_sina - after_hold_sina} "
        f"backup={fetches['backup']} write_errors={writes['n']} "
        f"bytes_unchanged=1 old_bytes={len(old_bytes)} sleeps={slept['n']} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_DISK_SAVE_OLD" in captured.out
    sys.stderr.write(captured.out)
