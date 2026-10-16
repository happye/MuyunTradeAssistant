"""T05 read-only Sina retry-success count for the market snapshot.

The sources are local stand-ins. An empty frame or None is a miss.
Success on the second call uses Sina twice, sleeps once, and does not
call the backup. Success on the third call uses Sina three times and
sleeps twice. The backup runs only after three misses, and that frame
is the one stored. The 1.5s gap is not waited. Production code is not
changed.
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
BASE = 1_700_000_000.0


class _Wall(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 16, 10, 0, 0)


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _good(price):
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [price] * N,
    })


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0


def test_t05_market_sina_retry_success_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
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
    slept = {"n": 0}
    sina_queue = []
    made = {}

    def sina(self):
        fetches["sina"] += 1
        if not sina_queue:
            raise AssertionError("Sina called more times than scripted")
        return sina_queue.pop(0)

    def backup(self):
        fetches["backup"] += 1
        frame = _good(12.0)
        made["backup"] = frame
        return frame

    def no_sleep(seconds):
        slept["n"] += 1
        assert seconds == 1.5

    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    monkeypatch.setattr(time, "sleep", no_sleep)
    _clear_memory()
    MarketCache._shared_fail_ts = 0.0
    threads_before = threading.active_count()
    tracemalloc.start()
    good_second = _good(10.0)
    good_third = _good(11.0)
    try:
        cache = MarketCache()
        assert cache.is_trading_hours()
        sina_queue.extend([pd.DataFrame(), good_second])
        second = cache.get_all_stocks()
        after_second_sina = fetches["sina"]
        after_second_backup = fetches["backup"]
        after_second_sleep = slept["n"]
        disk_second = pd.read_pickle(cache_dir / "stocks_df.pkl")
        _clear_memory()
        (cache_dir / "stocks_df.pkl").unlink()
        sina_queue.extend([pd.DataFrame(), None, good_third])
        third = cache.get_all_stocks()
        after_third_sina = fetches["sina"]
        after_third_backup = fetches["backup"]
        after_third_sleep = slept["n"]
        disk_third = pd.read_pickle(cache_dir / "stocks_df.pkl")
        _clear_memory()
        (cache_dir / "stocks_df.pkl").unlink()
        sina_queue.extend([pd.DataFrame(), pd.DataFrame(), pd.DataFrame()])
        from_backup = cache.get_all_stocks()
        after_backup_sina = fetches["sina"]
        after_backup_backup = fetches["backup"]
        after_backup_sleep = slept["n"]
        disk_backup = pd.read_pickle(cache_dir / "stocks_df.pkl")
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
    third_sina = after_third_sina - after_second_sina
    third_backup = after_third_backup - after_second_backup
    third_sleep = after_third_sleep - after_second_sleep
    backup_sina = after_backup_sina - after_third_sina
    backup_calls = after_backup_backup - after_third_backup
    backup_sleep = after_backup_sleep - after_third_sleep
    assert second is good_second and third is good_third
    assert from_backup is made["backup"]
    assert int((disk_second["data"]["最新价"] == 10.0).sum()) == N
    assert int((disk_third["data"]["最新价"] == 11.0).sum()) == N
    assert int((disk_backup["data"]["最新价"] == 12.0).sum()) == N
    assert after_second_sina == 2 and after_second_backup == 0 and after_second_sleep == 1
    assert third_sina == 3 and third_backup == 0 and third_sleep == 2
    assert backup_sina == 3 and backup_calls == 1 and backup_sleep == 2
    assert sina_queue == []
    assert len(files) == 1 and files[0].name == "stocks_df.pkl"
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_SINA_RETRY rows={N} "
        f"second_sina={after_second_sina} second_backup={after_second_backup} "
        f"second_sleeps={after_second_sleep} "
        f"third_sina={third_sina} third_backup={third_backup} third_sleeps={third_sleep} "
        f"all_empty_sina={backup_sina} all_empty_backup={backup_calls} "
        f"all_empty_sleeps={backup_sleep} retry_gaps_skipped={slept['n']} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_SINA_RETRY" in captured.out
    sys.stderr.write(captured.out)
