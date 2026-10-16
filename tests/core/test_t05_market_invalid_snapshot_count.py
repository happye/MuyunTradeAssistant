"""T05 read-only rejection of an all-zero market snapshot.

Both sources are local stand-ins. A non-empty all-zero Sina frame is not
retried and is not stored. A valid backup is the frame that enters memory
and disk. After that cache expires, another all-zero pair returns the old
object and does not arm cooldown. Cooldown starts only when both sources
are all-zero and memory and disk are gone. The 1.5s gap is not used.
Production code is not changed.
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


def _good():
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [10.0] * N,
    })


def _zeros():
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [0.0] * N,
    })


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0


def test_t05_market_invalid_snapshot_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
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
    slept = {"n": 0}
    mode = {"sina": "zero", "backup": "good"}
    made = {}

    def sina(self):
        fetches["sina"] += 1
        frame = _zeros() if mode["sina"] == "zero" else _good()
        made["sina"] = frame
        return frame

    def backup(self):
        fetches["backup"] += 1
        frame = _good() if mode["backup"] == "good" else _zeros()
        made["backup"] = frame
        return frame

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
        assert not cache._snapshot_quality_ok(_zeros())
        assert cache._snapshot_quality_ok(_good())
        saved_backup = cache.get_all_stocks()
        sina_frame = made["sina"]
        backup_frame = made["backup"]
        after_save_sina = fetches["sina"]
        after_save_backup = fetches["backup"]
        after_save_sleep = slept["n"]
        stored_ts = MarketCache._shared_stock_ts
        assert int(stored_ts) == int(BASE)
        clock.now = BASE + TTL + 1
        assert cache._is_expired(stored_ts) is True
        mode["backup"] = "zero"
        kept = cache.get_all_stocks()
        after_keep_sina = fetches["sina"]
        after_keep_backup = fetches["backup"]
        ts_after_keep = MarketCache._shared_stock_ts
        fail_ts_after_keep = MarketCache._shared_fail_ts
        disk_after_keep = pd.read_pickle(cache_dir / "stocks_df.pkl")
        _clear_memory()
        (cache_dir / "stocks_df.pkl").unlink()
        bare = cache.get_all_stocks()
        after_bare_sina = fetches["sina"]
        after_bare_backup = fetches["backup"]
        fail_ts_after_bare = MarketCache._shared_fail_ts
        cooled = [cache.get_all_stocks() for _ in range(COOLDOWN_CALLS)]
        after_cool_sina = fetches["sina"]
        after_cool_backup = fetches["backup"]
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
    keep_sina = after_keep_sina - after_save_sina
    keep_backup = after_keep_backup - after_save_backup
    bare_sina = after_bare_sina - after_keep_sina
    bare_backup = after_bare_backup - after_keep_backup
    cool_fetches = (after_cool_sina - after_bare_sina) + (after_cool_backup - after_bare_backup)
    assert saved_backup is backup_frame
    assert saved_backup is not sina_frame
    assert list(saved_backup["代码"]) == [f"{600000 + i:06d}" for i in range(N)]
    assert int((saved_backup["最新价"] > 0).sum()) == N
    assert kept is saved_backup
    assert ts_after_keep == stored_ts
    assert fail_ts_after_keep == 0.0
    assert int(disk_after_keep["ts"]) == int(BASE)
    assert int((disk_after_keep["data"]["最新价"] > 0).sum()) == N
    assert bare.empty and all(frame.empty for frame in cooled)
    assert bare is not sina_frame and bare is not backup_frame
    assert after_save_sina == 1 and after_save_backup == 1 and after_save_sleep == 0
    assert keep_sina == 1 and keep_backup == 1
    assert bare_sina == 1 and bare_backup == 1
    assert cool_fetches == 0
    assert slept["n"] == 0
    assert fail_ts_after_bare == BASE + TTL + 1
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_INVALID_SNAPSHOT rows={N} "
        f"save_sina={after_save_sina} save_backup={after_save_backup} "
        f"expired_keep_sina={keep_sina} expired_keep_backup={keep_backup} "
        f"bare_sina={bare_sina} bare_backup={bare_backup} "
        f"cooldown_calls={COOLDOWN_CALLS} cooldown_fetches={cool_fetches} "
        f"retry_gaps_skipped={slept['n']} files_left={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_INVALID_SNAPSHOT" in captured.out
    sys.stderr.write(captured.out)
