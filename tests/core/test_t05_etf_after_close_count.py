"""T05 read-only ETF market after-close long-cache count.

The source is a local stand-in. A synthetic clock keeps fromtimestamp
aligned with time.time. After 15:00 the cache is stale only when its
timestamp is strictly before today's 15:00. Exactly 15:00 still hits.
At 15:00 itself the session is still trading, so a 300s-old stamp uses
the intraday TTL and is not rejected by the after-close rule. ETF has
no disk cache. Production code is not changed.
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
DAY = datetime(2026, 10, 16)
TTL = 300


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


def _move(hour, minute, second=0):
    _Wall.stamp = datetime(2026, 10, 16, hour, minute, second)


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


def test_t05_etf_after_close_long_cache(tmp_path, monkeypatch, capsys):
    assert DAY.weekday() < 5
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
        _move(10, 0)
        assert cache.is_trading_hours()
        morning = cache.get_all_etfs()
        morning_fetches = fetches["n"]
        _move(18, 0)
        assert cache.is_trading_hours() is False
        assert cache._is_expired(_stamp(10, 0)) is True
        rejected = cache.get_all_etfs()
        after_reject = fetches["n"]
        repeated = cache.get_all_etfs()
        after_repeat = fetches["n"]
        _clear_memory()
        no_disk = cache.get_all_etfs()
        after_clear = fetches["n"]
        _move(14, 55)
        near_close = cache.get_all_etfs(force_refresh=True)
        after_1455 = fetches["n"]
        assert MarketCache._shared_etf_ts == _stamp(14, 55)
        _move(15, 0)
        assert cache.is_trading_hours()
        assert cache._is_expired(_stamp(14, 55)) is False
        trading_hit = cache.get_all_etfs()
        after_trading = fetches["n"]
        at_close = cache.get_all_etfs(force_refresh=True)
        after_1500 = fetches["n"]
        assert MarketCache._shared_etf_ts == _stamp(15, 0)
        _move(18, 0)
        assert cache._is_expired(_stamp(15, 0)) is False
        kept = cache.get_all_etfs()
        after_kept = fetches["n"]
        _move(14, 59)
        before_close = cache.get_all_etfs(force_refresh=True)
        after_1459 = fetches["n"]
        assert MarketCache._shared_etf_ts == _stamp(14, 59)
        _move(18, 0)
        assert cache._is_expired(_stamp(14, 59)) is True
        missed = cache.get_all_etfs()
        after_miss = fetches["n"]
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
    reject_fetches = after_reject - morning_fetches
    repeat_hits = 1 if after_repeat == after_reject else 0
    cleared_refetches = after_clear - after_reject
    trading_1500_ttl_hits = 1 if after_trading == after_1455 else 0
    exact_1500_hits = 1 if after_kept == after_1500 else 0
    before_1459_fetches = after_miss - after_1459
    assert len(morning) == N and list(morning["代码"]) == [f"{510000 + i:06d}" for i in range(N)]
    assert len(rejected) == N and len(no_disk) == N and len(missed) == N
    assert repeated is rejected
    assert no_disk is not rejected
    assert trading_hit is near_close
    assert kept is at_close
    assert len(before_close) == N
    assert morning_fetches == 1
    assert reject_fetches == 1
    assert repeat_hits == 1
    assert cleared_refetches == 1
    assert trading_1500_ttl_hits == 1
    assert exact_1500_hits == 1
    assert before_1459_fetches == 1
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_ETF_AFTER_CLOSE rows={N} morning_fetches={morning_fetches} "
        f"close_rejects_morning={reject_fetches} after_close_memory_hits={repeat_hits} "
        f"memory_cleared_refetches={cleared_refetches} "
        f"trading_1500_ttl_hits={trading_1500_ttl_hits} "
        f"exact_1500_hits={exact_1500_hits} "
        f"before_1459_fetches={before_1459_fetches} "
        f"disk_files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_ETF_AFTER_CLOSE" in captured.out
    sys.stderr.write(captured.out)
