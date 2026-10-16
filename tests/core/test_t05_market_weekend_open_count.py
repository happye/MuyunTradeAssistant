"""T05 read-only full-market snapshot weekend and before-open cache count.

The source is a local stand-in. time.time counts seconds from Friday
2026-10-16 00:00 so fromtimestamp stays on the same clock. Saturday,
Sunday, and Monday 09:00 accept a cache only when it is at or after the
previous weekday 15:00. Exactly Friday 15:00 still hits. Friday 14:59
expires. Monday 09:00 is before the open, not a trading session.
Monday 09:30 is trading again. Disk uses the test tmp path. Statutory
holidays are not modeled. Production code is not changed.
"""
import sys
import threading
import tracemalloc
from datetime import datetime, timedelta

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
FRIDAY = datetime(2026, 10, 16)


class _Wall(datetime):
    stamp = datetime(2026, 10, 16, 10, 0, 0)

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


def _move(days, hour, minute):
    _Wall.stamp = FRIDAY + timedelta(days=days, hours=hour, minutes=minute)


def _stamp(days, hour, minute):
    return days * 86400 + hour * 3600 + minute * 60


def _frame():
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [10.0] * N,
    })


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0


def test_t05_market_weekend_before_open_long_cache(tmp_path, monkeypatch, capsys):
    assert FRIDAY.weekday() == 4
    assert (FRIDAY + timedelta(days=1)).weekday() == 5
    assert (FRIDAY + timedelta(days=2)).weekday() == 6
    assert (FRIDAY + timedelta(days=3)).weekday() == 0
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
        raise AssertionError("weekend path must not call the backup source")

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
        _move(1, 10, 0)
        assert cache.is_trading_hours() is False
        assert cache._is_expired(_stamp(0, 10, 0)) is True
        assert cache._is_expired(_stamp(0, 15, 0)) is False
        assert cache._is_expired(_stamp(0, 14, 59)) is True
        _move(1, 18, 0)
        assert cache._is_expired(_stamp(0, 15, 0)) is False
        _move(2, 12, 0)
        assert cache.is_trading_hours() is False
        assert cache._is_expired(_stamp(0, 15, 0)) is False
        assert cache._is_expired(_stamp(0, 14, 59)) is True
        _move(3, 9, 0)
        assert cache.is_trading_hours() is False
        assert cache._is_expired(_stamp(0, 15, 0)) is False
        assert cache._is_expired(_stamp(0, 14, 59)) is True
        _move(3, 9, 30)
        assert cache.is_trading_hours() is True

        _move(0, 10, 0)
        assert cache.is_trading_hours()
        morning = cache.get_all_stocks()
        morning_fetches = fetches["sina"]
        _move(1, 10, 0)
        sat_rejected = cache.get_all_stocks()
        after_sat_reject = fetches["sina"]
        sat_repeated = cache.get_all_stocks()
        after_sat_repeat = fetches["sina"]
        _clear_memory()
        sat_disk = cache.get_all_stocks()
        after_sat_disk = fetches["sina"]

        _move(0, 15, 0)
        assert cache.is_trading_hours()
        friday_close = cache.get_all_stocks(force_refresh=True)
        after_friday_close = fetches["sina"]
        assert int(MarketCache._shared_stock_ts) == _stamp(0, 15, 0)
        _move(1, 10, 0)
        sat_kept = cache.get_all_stocks()
        after_sat_kept = fetches["sina"]
        _clear_memory()
        sat_disk_kept = cache.get_all_stocks()
        after_sat_disk_kept = fetches["sina"]

        _move(0, 14, 59)
        friday_before = cache.get_all_stocks(force_refresh=True)
        after_1459 = fetches["sina"]
        assert int(MarketCache._shared_stock_ts) == _stamp(0, 14, 59)
        _move(1, 10, 0)
        sat_missed = cache.get_all_stocks()
        after_sat_miss = fetches["sina"]

        _move(0, 15, 0)
        sunday_source = cache.get_all_stocks(force_refresh=True)
        after_sunday_source = fetches["sina"]
        _move(2, 12, 0)
        sunday_kept = cache.get_all_stocks()
        after_sunday_kept = fetches["sina"]
        _clear_memory()
        sunday_disk = cache.get_all_stocks()
        after_sunday_disk = fetches["sina"]
        _move(0, 14, 59)
        cache.get_all_stocks(force_refresh=True)
        after_sunday_1459 = fetches["sina"]
        _move(2, 12, 0)
        sunday_missed = cache.get_all_stocks()
        after_sunday_miss = fetches["sina"]

        _move(0, 15, 0)
        monday_source = cache.get_all_stocks(force_refresh=True)
        after_monday_source = fetches["sina"]
        _move(3, 9, 0)
        assert cache.is_trading_hours() is False
        monday_kept = cache.get_all_stocks()
        after_monday_kept = fetches["sina"]
        _clear_memory()
        monday_disk = cache.get_all_stocks()
        after_monday_disk = fetches["sina"]
        _move(0, 14, 59)
        cache.get_all_stocks(force_refresh=True)
        after_monday_1459 = fetches["sina"]
        _move(3, 9, 0)
        monday_missed = cache.get_all_stocks()
        after_monday_miss = fetches["sina"]
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
    sat_reject_fetches = after_sat_reject - morning_fetches
    sat_memory_hits = 1 if after_sat_repeat == after_sat_reject else 0
    sat_disk_hits = 1 if after_sat_disk == after_sat_reject else 0
    sat_exact_memory_hits = 1 if after_sat_kept == after_friday_close else 0
    sat_exact_disk_hits = 1 if after_sat_disk_kept == after_friday_close else 0
    sat_1459_fetches = after_sat_miss - after_1459
    sunday_exact_memory_hits = 1 if after_sunday_kept == after_sunday_source else 0
    sunday_exact_disk_hits = 1 if after_sunday_disk == after_sunday_source else 0
    sunday_1459_fetches = after_sunday_miss - after_sunday_1459
    monday_exact_memory_hits = 1 if after_monday_kept == after_monday_source else 0
    monday_exact_disk_hits = 1 if after_monday_disk == after_monday_source else 0
    monday_1459_fetches = after_monday_miss - after_monday_1459
    assert len(morning) == N and len(sat_rejected) == N and len(sat_missed) == N
    assert len(sunday_missed) == N and len(monday_missed) == N and len(friday_before) == N
    assert sat_repeated is sat_rejected
    assert sat_disk.equals(sat_rejected) and sat_disk is not sat_rejected
    assert sat_kept is friday_close
    assert sat_disk_kept.equals(friday_close) and sat_disk_kept is not friday_close
    assert sunday_kept is sunday_source
    assert sunday_disk.equals(sunday_source) and sunday_disk is not sunday_source
    assert monday_kept is monday_source
    assert monday_disk.equals(monday_source) and monday_disk is not monday_source
    assert morning_fetches == 1
    assert sat_reject_fetches == 1
    assert sat_memory_hits == 1 and sat_disk_hits == 1
    assert sat_exact_memory_hits == 1 and sat_exact_disk_hits == 1
    assert sat_1459_fetches == 1
    assert sunday_exact_memory_hits == 1 and sunday_exact_disk_hits == 1
    assert sunday_1459_fetches == 1
    assert monday_exact_memory_hits == 1 and monday_exact_disk_hits == 1
    assert monday_1459_fetches == 1
    assert fetches["backup"] == 0
    assert len(files) == 1 and files[0].name == "stocks_df.pkl"
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_WEEKEND_OPEN rows={N} morning_fetches={morning_fetches} "
        f"saturday_rejects_friday_1000={sat_reject_fetches} "
        f"saturday_memory_hits={sat_memory_hits} saturday_disk_hits={sat_disk_hits} "
        f"saturday_exact_1500_memory_hits={sat_exact_memory_hits} "
        f"saturday_exact_1500_disk_hits={sat_exact_disk_hits} "
        f"saturday_1459_fetches={sat_1459_fetches} "
        f"sunday_exact_1500_memory_hits={sunday_exact_memory_hits} "
        f"sunday_exact_1500_disk_hits={sunday_exact_disk_hits} "
        f"sunday_1459_fetches={sunday_1459_fetches} "
        f"monday_0900_exact_1500_memory_hits={monday_exact_memory_hits} "
        f"monday_0900_exact_1500_disk_hits={monday_exact_disk_hits} "
        f"monday_0900_1459_fetches={monday_1459_fetches} "
        f"backup_fetches={fetches['backup']} files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_WEEKEND_OPEN" in captured.out
    sys.stderr.write(captured.out)
