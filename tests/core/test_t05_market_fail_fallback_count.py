"""T05 read-only full-market snapshot failure-fallback count.

Both sources are local stand-ins. A fresh snapshot is stored, then the
clock moves past the 300s TTL. Empty sources still run Sina three times
and the backup once, but an expired memory object is returned with its
timestamp unchanged and cooldown is not armed. After the memory is
cleared, the expired disk snapshot is restored instead, again without
cooldown. Cooldown starts only when memory and disk are both gone.
The 1.5s retry gap is not waited. Production code is not changed.
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
TTL = 300
COOLDOWN_CALLS = 5


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


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0


def test_t05_market_fail_fallback_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
    assert MarketCache.DEFAULT_TRADING_TTL == TTL
    assert MarketCache.FAIL_COOLDOWN_SECONDS == 60
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
    mode = {"value": "ok"}

    def sina(self):
        fetches["sina"] += 1
        if mode["value"] == "ok":
            return _frame()
        return pd.DataFrame()

    def backup(self):
        fetches["backup"] += 1
        return pd.DataFrame()

    def no_sleep(seconds):
        slept["n"] += 1

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
    try:
        cache = MarketCache()
        assert cache.is_trading_hours()
        first = cache.get_all_stocks()
        after_ok_sina = fetches["sina"]
        after_ok_backup = fetches["backup"]
        after_ok_sleep = slept["n"]
        stored_ts = MarketCache._shared_stock_ts
        assert int(stored_ts) == int(BASE)
        clock.now = BASE + TTL + 1
        assert cache._is_expired(stored_ts) is True
        mode["value"] = "empty"
        memory_kept = cache.get_all_stocks()
        after_memory_sina = fetches["sina"]
        after_memory_backup = fetches["backup"]
        after_memory_sleep = slept["n"]
        memory_again = cache.get_all_stocks()
        after_memory_again_sina = fetches["sina"]
        after_memory_again_backup = fetches["backup"]
        after_memory_again_sleep = slept["n"]
        ts_after_memory = MarketCache._shared_stock_ts
        fail_ts_after_memory = MarketCache._shared_fail_ts
        disk_ts_after_memory = pd.read_pickle(cache_dir / "stocks_df.pkl")["ts"]
        _clear_memory()
        disk_kept = cache.get_all_stocks()
        after_disk_sina = fetches["sina"]
        after_disk_backup = fetches["backup"]
        after_disk_sleep = slept["n"]
        restored_ts = MarketCache._shared_stock_ts
        fail_ts_after_disk = MarketCache._shared_fail_ts
        _clear_memory()
        (cache_dir / "stocks_df.pkl").unlink()
        bare = cache.get_all_stocks()
        after_bare_sina = fetches["sina"]
        after_bare_backup = fetches["backup"]
        after_bare_sleep = slept["n"]
        fail_ts_after_bare = MarketCache._shared_fail_ts
        cooled = [cache.get_all_stocks() for _ in range(COOLDOWN_CALLS)]
        after_cool_sina = fetches["sina"]
        after_cool_backup = fetches["backup"]
        after_cool_sleep = slept["n"]
        fail_ts_after_cool = MarketCache._shared_fail_ts
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
    memory_sina = after_memory_sina - after_ok_sina
    memory_backup = after_memory_backup - after_ok_backup
    memory_again_sina = after_memory_again_sina - after_memory_sina
    memory_again_backup = after_memory_again_backup - after_memory_backup
    disk_sina = after_disk_sina - after_memory_again_sina
    disk_backup = after_disk_backup - after_memory_again_backup
    bare_sina = after_bare_sina - after_disk_sina
    bare_backup = after_bare_backup - after_disk_backup
    cool_sina = after_cool_sina - after_bare_sina
    cool_backup = after_cool_backup - after_bare_backup
    assert len(first) == N and list(first["代码"]) == [f"{600000 + i:06d}" for i in range(N)]
    assert memory_kept is first and memory_again is first
    assert ts_after_memory == stored_ts
    assert disk_kept.equals(first) and disk_kept is not first
    assert bare.empty and all(frame.empty for frame in cooled)
    assert after_ok_sina == 1 and after_ok_backup == 0 and after_ok_sleep == 0
    assert memory_sina == 3 and memory_backup == 1
    assert memory_again_sina == 3 and memory_again_backup == 1
    assert after_memory_sleep - after_ok_sleep == 2
    assert after_memory_again_sleep - after_memory_sleep == 2
    assert fail_ts_after_memory == 0.0
    assert int(disk_ts_after_memory) == int(BASE)
    assert disk_sina == 3 and disk_backup == 1
    assert after_disk_sleep - after_memory_again_sleep == 2
    assert int(restored_ts) == int(BASE)
    assert fail_ts_after_disk == 0.0
    assert bare_sina == 3 and bare_backup == 1
    assert after_bare_sleep - after_disk_sleep == 2
    assert fail_ts_after_bare == BASE + TTL + 1
    assert cool_sina == 0 and cool_backup == 0
    assert after_cool_sleep == after_bare_sleep
    assert fail_ts_after_cool == fail_ts_after_bare
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_FAIL_FALLBACK rows={N} ok_sina={after_ok_sina} "
        f"ok_backup={after_ok_backup} expired_memory_sina={memory_sina} "
        f"expired_memory_backup={memory_backup} "
        f"expired_memory_again_sina={memory_again_sina} "
        f"expired_memory_again_backup={memory_again_backup} "
        f"expired_disk_sina={disk_sina} expired_disk_backup={disk_backup} "
        f"bare_sina={bare_sina} bare_backup={bare_backup} "
        f"cooldown_calls={COOLDOWN_CALLS} cooldown_fetches={cool_sina + cool_backup} "
        f"retry_gaps_skipped={slept['n']} files_left={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_FAIL_FALLBACK" in captured.out
    sys.stderr.write(captured.out)
