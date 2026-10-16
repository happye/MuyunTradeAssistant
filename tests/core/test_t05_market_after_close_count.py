"""T05 read-only full-market snapshot after-close long-cache count.

The source is a local stand-in. A synthetic clock keeps fromtimestamp
aligned with time.time. After 15:00 the cache is stale only when its
timestamp is strictly before today's 15:00. Exactly 15:00 still hits.
This is not the intraday 301-second rule. Disk cache is the test tmp
path. Production code is not changed.
"""
import sys
import threading
import tracemalloc
from datetime import datetime, timedelta

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
DAY = datetime(2026, 10, 16)


class _Wall(datetime):
    stamp = datetime(2026, 10, 16, 10, 0, 0)

    @classmethod
    def now(cls, tz=None):
        s = cls.stamp
        return cls(s.year, s.month, s.day, s.hour, s.minute, s.second)

    @classmethod
    def fromtimestamp(cls, ts, tz=None):
        base = DAY + timedelta(seconds=int(ts))
        return cls(base.year, base.month, base.day, base.hour, base.minute, base.second)


class _Clock:
    def time(self):
        s = _Wall.stamp
        return s.hour * 3600 + s.minute * 60 + s.second


def _move(hour, minute):
    _Wall.stamp = datetime(2026, 10, 16, hour, minute, 0)


def _frame():
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [10.0] * N,
    })


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0


def test_t05_market_after_close_long_cache(tmp_path, monkeypatch, capsys):
    assert DAY.weekday() < 5
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    fetches = {"sina": 0, "backup": 0}

    def sina(self):
        fetches["sina"] += 1
        return _frame()

    def backup(self):
        fetches["backup"] += 1
        raise AssertionError("after-close path must not call the backup source")

    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", _Clock())
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    _clear_memory()
    MarketCache._shared_fail_ts = 0.0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        _move(10, 0)
        assert cache.is_trading_hours()
        morning = cache.get_all_stocks()
        morning_fetches = fetches["sina"]
        _move(18, 0)
        assert cache.is_trading_hours() is False
        assert cache._is_expired(10 * 3600) is True
        rejected = cache.get_all_stocks()
        after_reject = fetches["sina"]
        repeated = cache.get_all_stocks()
        after_repeat = fetches["sina"]
        _clear_memory()
        disk_hit = cache.get_all_stocks()
        after_disk = fetches["sina"]
        _move(15, 0)
        assert cache.is_trading_hours()
        at_close = cache.get_all_stocks(force_refresh=True)
        after_1500 = fetches["sina"]
        _move(18, 0)
        assert cache._is_expired(15 * 3600) is False
        kept = cache.get_all_stocks()
        after_kept = fetches["sina"]
        _move(14, 59)
        before_close = cache.get_all_stocks(force_refresh=True)
        after_1459 = fetches["sina"]
        _clear_memory()
        _move(18, 0)
        assert cache._is_expired(14 * 3600 + 59 * 60) is True
        missed = cache.get_all_stocks()
        after_miss = fetches["sina"]
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
    reject_fetches = after_reject - morning_fetches
    repeat_hits = 1 if after_repeat == after_reject else 0
    disk_hits = 1 if after_disk == after_reject else 0
    exact_1500_hits = 1 if after_kept == after_1500 else 0
    before_1459_fetches = after_miss - after_1459
    assert len(morning) == N and len(rejected) == N and len(missed) == N
    assert repeated is rejected
    assert disk_hit.equals(rejected) and disk_hit is not rejected
    assert kept is at_close
    assert len(before_close) == N
    assert morning_fetches == 1
    assert reject_fetches == 1
    assert repeat_hits == 1
    assert disk_hits == 1
    assert exact_1500_hits == 1
    assert before_1459_fetches == 1
    assert fetches["backup"] == 0
    assert len(files) == 1 and files[0].name == "stocks_df.pkl"
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_AFTER_CLOSE rows={N} morning_fetches={morning_fetches} "
        f"close_rejects_morning={reject_fetches} after_close_memory_hits={repeat_hits} "
        f"after_close_disk_hits={disk_hits} exact_1500_hits={exact_1500_hits} "
        f"before_1459_fetches={before_1459_fetches} backup_fetches={fetches['backup']} "
        f"files={len(files)} peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_AFTER_CLOSE" in captured.out
    sys.stderr.write(captured.out)
