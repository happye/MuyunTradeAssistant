"""T05 read-only ETF lunch-break TTL count.

The source is a local stand-in. time.time counts seconds from Friday
2026-10-16 00:00 so fromtimestamp stays on the same clock. 12:00 is not
a trading session and does not use the after-close rule. Expiry stays
age > 300: 11:55 viewed at 12:00 still hits, one more second misses.
Clearing memory refetches, because ETF writes no disk file. 11:55 is
already inside the break. 11:30 and 13:00 are trading sessions, and
11:31 is not. Production code is not changed.
"""
import sys
import threading
import tracemalloc
import types
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
        "代码": [f"{510000 + i:06d}" for i in range(N)],
        "最新价": [1.0] * N,
    })


def _clear_memory():
    MarketCache._shared_etf_df = None
    MarketCache._shared_etf_ts = 0.0
    MarketCache._shared_etf_count = 0


def test_t05_etf_lunch_ttl_window(tmp_path, monkeypatch, capsys):
    assert FRIDAY.weekday() == 4
    assert MarketCache.DEFAULT_TRADING_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
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
    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", _Clock())
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    _clear_memory()
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
        first = cache.get_all_etfs()
        fresh_fetches = fetches["n"]
        assert int(MarketCache._shared_etf_ts) == _stamp(11, 55, 0)
        _move(12, 0, 0)
        assert cache._is_expired(_stamp(11, 55, 0)) is False
        memory_hit = cache.get_all_etfs()
        after_memory = fetches["n"]
        _move(12, 0, 1)
        assert cache._is_expired(_stamp(11, 55, 0)) is True
        expired = cache.get_all_etfs()
        after_expired = fetches["n"]
        renewed_ts = MarketCache._shared_etf_ts
        assert int(renewed_ts) == _stamp(12, 0, 1)
        _move(12, 5, 1)
        assert cache._is_expired(renewed_ts) is False
        renewed = cache.get_all_etfs()
        after_renewed = fetches["n"]

        _move(11, 55, 0)
        fresh_again = cache.get_all_etfs(force_refresh=True)
        after_again = fetches["n"]
        _move(12, 0, 0)
        kept = cache.get_all_etfs()
        after_kept = fetches["n"]
        _clear_memory()
        no_disk = cache.get_all_etfs()
        after_clear = fetches["n"]
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
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    codes = [f"{510000 + i:06d}" for i in range(N)]
    age300_memory_hits = 1 if after_memory == fresh_fetches else 0
    age301_fetches = after_expired - fresh_fetches
    renewed_memory_hits = 1 if after_renewed == after_expired else 0
    cleared_refetches = after_clear - after_kept
    kept_hits = 1 if after_kept == after_again else 0
    assert list(first["代码"]) == codes
    assert len(expired) == N and len(no_disk) == N and len(fresh_again) == N
    assert memory_hit is first
    assert expired is not first
    assert renewed is expired
    assert kept is fresh_again
    assert no_disk is not fresh_again
    assert fresh_fetches == 1
    assert age300_memory_hits == 1
    assert age301_fetches == 1
    assert renewed_memory_hits == 1
    assert kept_hits == 1
    assert cleared_refetches == 1
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_ETF_LUNCH_TTL rows={N} ttl={TTL} cmp=gt "
        f"fresh_fetches={fresh_fetches} age300_memory_hits={age300_memory_hits} "
        f"age301_fetches={age301_fetches} renewed_memory_hits={renewed_memory_hits} "
        f"age300_memory_hits_again={kept_hits} "
        f"memory_cleared_refetches={cleared_refetches} "
        f"disk_files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_ETF_LUNCH_TTL" in captured.out
    sys.stderr.write(captured.out)
