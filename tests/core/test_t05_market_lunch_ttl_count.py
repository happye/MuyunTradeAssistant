"""T05 read-only full-market snapshot lunch-break TTL count.

The source is a local stand-in. time.time counts seconds from Friday
2026-10-16 00:00 so fromtimestamp stays on the same clock. 12:00 is not
a trading session, but it is not after the close, before the open, or a
weekend, so the long-cache rule does not apply. Expiry stays age > 300:
11:55 viewed at 12:00 still hits memory and disk, one more second misses
both. 11:55 itself is already inside the break. 11:30 and 13:00 are
trading sessions, and 11:31 is not. Disk uses the test tmp path.
Production code is not changed.
"""
import sys
import threading
import tracemalloc
from datetime import datetime, timedelta

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
TTL = 300
FRIDAY = datetime(2026, 10, 16)


class _Wall(datetime):
    stamp = datetime(2026, 10, 16, 11, 55, 0)

    @classmethod
    def now(cls, tz=None):
        s = cls.stamp
        return cls(s.year, s.month, s.day, s.hour, s.minute, s.second)

    @classmethod
    def fromtimestamp(cls, ts, tz=None):
        base = FRIDAY + timedelta(seconds=int(ts))
        return cls(base.year, base.month, base.day, base.hour, base.minute, base.second)


class _Clock:
    def time(self):
        return (_Wall.stamp - FRIDAY).total_seconds()


def _move(hour, minute, second=0):
    _Wall.stamp = FRIDAY + timedelta(hours=hour, minutes=minute, seconds=second)


def _stamp(hour, minute, second=0):
    return hour * 3600 + minute * 60 + second


def _frame():
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [10.0] * N,
    })


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0


def test_t05_market_lunch_ttl_window(tmp_path, monkeypatch, capsys):
    assert FRIDAY.weekday() == 4
    assert MarketCache.DEFAULT_TRADING_TTL == TTL
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
        raise AssertionError("lunch TTL path must not call the backup source")

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
        assert cache._ttl == TTL
        _move(11, 30, 0)
        assert cache.is_trading_hours() is True
        _move(11, 31, 0)
        assert cache.is_trading_hours() is False
        _move(12, 0, 0)
        assert cache.is_trading_hours() is False
        assert cache._is_expired(_stamp(11, 55, 0)) is False
        assert cache._is_expired(_stamp(11, 54, 59)) is True
        assert cache._is_expired(_stamp(10, 0, 0)) is True
        _move(13, 0, 0)
        assert cache.is_trading_hours() is True
        assert cache._is_expired(_stamp(12, 55, 0)) is False
        assert cache._is_expired(_stamp(12, 54, 59)) is True

        _move(11, 55, 0)
        assert cache.is_trading_hours() is False
        first = cache.get_all_stocks()
        fresh_fetches = fetches["sina"]
        assert int(MarketCache._shared_stock_ts) == _stamp(11, 55, 0)
        _move(12, 0, 0)
        assert cache.is_trading_hours() is False
        assert cache._is_expired(_stamp(11, 55, 0)) is False
        memory_hit = cache.get_all_stocks()
        after_memory = fetches["sina"]
        _clear_memory()
        disk_hit = cache.get_all_stocks()
        after_disk = fetches["sina"]
        _move(12, 0, 1)
        assert cache._is_expired(_stamp(11, 55, 0)) is True
        expired = cache.get_all_stocks()
        after_expired = fetches["sina"]
        renewed_ts = MarketCache._shared_stock_ts
        assert int(renewed_ts) == _stamp(12, 0, 1)
        _move(12, 5, 1)
        assert cache._is_expired(renewed_ts) is False
        renewed = cache.get_all_stocks()
        after_renewed = fetches["sina"]
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
    age300_memory_hits = 1 if after_memory == fresh_fetches else 0
    age300_disk_hits = 1 if after_disk == fresh_fetches else 0
    age301_fetches = after_expired - fresh_fetches
    renewed_memory_hits = 1 if after_renewed == after_expired else 0
    assert len(first) == N and len(expired) == N
    assert memory_hit is first
    assert disk_hit.equals(first) and disk_hit is not first
    assert expired is not first
    assert renewed is expired
    assert fresh_fetches == 1
    assert age300_memory_hits == 1
    assert age300_disk_hits == 1
    assert age301_fetches == 1
    assert renewed_memory_hits == 1
    assert fetches["backup"] == 0
    assert len(files) == 1 and files[0].name == "stocks_df.pkl"
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_LUNCH_TTL rows={N} ttl={TTL} cmp=gt "
        f"fresh_fetches={fresh_fetches} age300_memory_hits={age300_memory_hits} "
        f"age300_disk_hits={age300_disk_hits} age301_fetches={age301_fetches} "
        f"renewed_memory_hits={renewed_memory_hits} backup_fetches={fetches['backup']} "
        f"files={len(files)} peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_LUNCH_TTL" in captured.out
    sys.stderr.write(captured.out)
