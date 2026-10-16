"""T05 read-only force-refresh count for the market snapshot.

Memory and disk are both fresh, and disk L2 is the temp directory.
force_refresh still calls Sina once, skips the backup, and overwrites.
The next ordinary call hits that new snapshot and does not fetch.
Inside the 60s failure cooldown a non-forced call fetches nothing, but
force_refresh still fetches. The 1.5s gap is not waited. Production
code is not changed, and the real ~/.muyun/market_cache is not used.
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
    MarketCache._shared_fail_ts = 0.0


def _price(frame):
    return float(frame["最新价"].iloc[0])


def test_t05_market_force_refresh_count(tmp_path, monkeypatch, capsys):
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
    backup_queue = []
    path = cache_dir / "stocks_df.pkl"

    def sina(self):
        fetches["sina"] += 1
        if not sina_queue:
            raise AssertionError("Sina called more times than scripted")
        return sina_queue.pop(0)

    def backup(self):
        fetches["backup"] += 1
        if not backup_queue:
            raise AssertionError("backup called more times than scripted")
        return backup_queue.pop(0)

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
    threads_before = threading.active_count()
    tracemalloc.start()
    fresh = _good(10.0)
    refreshed = _good(11.0)
    cooled = _good(12.0)
    try:
        cache = MarketCache()
        assert cache.is_trading_hours()
        sina_queue.append(fresh)
        first = cache.get_all_stocks()
        memory_hit = cache.get_all_stocks()
        fresh_sina = fetches["sina"]
        fresh_backup = fetches["backup"]
        _clear_memory()
        disk_hit = cache.get_all_stocks()
        fresh_disk_sina = fetches["sina"]
        sina_queue.append(refreshed)
        forced = cache.get_all_stocks(force_refresh=True)
        refresh_sina = fetches["sina"] - fresh_disk_sina
        refresh_backup = fetches["backup"] - fresh_backup
        held = cache.get_all_stocks()
        after_hold_sina = fetches["sina"]
        _clear_memory()
        held_disk = cache.get_all_stocks()
        after_disk_sina = fetches["sina"]
        stored = pd.read_pickle(path)
        _clear_memory()
        path.unlink()
        sina_queue.extend([pd.DataFrame(), pd.DataFrame(), pd.DataFrame()])
        backup_queue.append(pd.DataFrame())
        failed = cache.get_all_stocks()
        fail_mark = MarketCache._shared_fail_ts
        entry_sina = fetches["sina"] - after_disk_sina
        entry_backup = fetches["backup"] - refresh_backup
        clock.now += 1
        assert MarketCache._shared_stock_df is None
        assert not path.exists()
        skipped = cache.get_all_stocks()
        skip_sina = fetches["sina"] - after_disk_sina - entry_sina
        skip_backup = fetches["backup"] - refresh_backup - entry_backup
        sina_queue.append(cooled)
        cooled_got = cache.get_all_stocks(force_refresh=True)
        cool_sina = fetches["sina"] - after_disk_sina - entry_sina - skip_sina
        cool_backup = fetches["backup"] - refresh_backup - entry_backup - skip_backup
        cleared_fail = MarketCache._shared_fail_ts
        cooled_hold = cache.get_all_stocks()
        _clear_memory()
        cooled_disk = cache.get_all_stocks()
        final_sina = fetches["sina"]
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
    assert first is fresh and memory_hit is first and disk_hit is not first
    assert _price(disk_hit) == 10.0
    assert fresh_sina == 1 and fresh_backup == 0 and fresh_disk_sina == 1
    assert forced is refreshed and forced is not first
    assert refresh_sina == 1 and refresh_backup == 0
    assert held is forced and after_hold_sina == fresh_disk_sina + 1
    assert _price(held_disk) == 11.0 and after_disk_sina == fresh_disk_sina + 1
    assert _price(stored["data"]) == 11.0
    assert failed.empty and skipped.empty
    assert entry_sina == 3 and entry_backup == 1
    assert fail_mark == BASE
    assert clock.now - fail_mark == 1
    assert skip_sina == 0 and skip_backup == 0
    assert cooled_got is cooled and cool_sina == 1 and cool_backup == 0
    assert cleared_fail == 0.0
    assert cooled_hold is cooled and _price(cooled_disk) == 12.0
    assert final_sina == 6
    assert fetches["backup"] == 1 and slept["n"] == 2
    assert sina_queue == [] and backup_queue == []
    assert len(files) == 1 and files[0].name == "stocks_df.pkl"
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_FORCE_REFRESH rows={N} fresh_sina={fresh_sina} "
        f"fresh_backup={fresh_backup} fresh_disk_hits=1 "
        f"refresh_sina={refresh_sina} refresh_backup={refresh_backup} "
        f"after_refresh_fetches=0 cooldown_entry_sina={entry_sina} "
        f"cooldown_entry_backup={entry_backup} cooldown_skip_sina={skip_sina} "
        f"cooldown_skip_backup={skip_backup} cooldown_refresh_sina={cool_sina} "
        f"cooldown_refresh_backup={cool_backup} sleeps={slept['n']} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_FORCE_REFRESH" in captured.out
    sys.stderr.write(captured.out)
