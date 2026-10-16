"""T05 read-only count: expired concept memory yields to a future disk list.

Concept memory holds one list of 30 codes. Its timestamp is BASE-1800.
The clock is BASE, so the memory age is exactly 1800. The production
comparison is strict less-than 1800, so that age is not a hit and the
stale list is not returned. Disk data is a different list of 30 codes and
its ts is BASE+1, so the disk age is -1 and is inside the TTL. The disk
list is returned. The disk load runs once. Memory is replaced by that same
disk list and the timestamp becomes the disk ts, not the current clock and
not the old BASE-1800. The fetch, the fallback, and join are not called.
The disk file is not rewritten. Industry memory is left alone. Production
code is not changed, and the real ~/.muyun/market_cache is not used. This
is not the industry count.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05cmiss"
BASE = 1_700_000_000.0
TTL = 1800
EXPIRED = BASE - TTL
FUTURE = BASE + 1


class _Clock:
    now = BASE

    def time(self):
        return self.now


class _Spy(dict):
    def __init__(self):
        super().__init__()
        self.writes = []

    def __setitem__(self, key, value):
        self.writes.append((key, value))
        super().__setitem__(key, value)


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_concept_expired_memory_future_disk_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.CONCEPT_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = MarketCache._shared_concept_stocks
    saved_ts = MarketCache._shared_concept_stocks_ts
    saved_industry = dict(MarketCache._shared_industry_stocks)
    saved_industry_ts = dict(MarketCache._shared_industry_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    disk_loads = {"n": 0, "keys": [], "value": "unset"}
    disk_saves = {"n": 0}
    fallback_calls = {"n": 0}
    joins = {"n": 0}
    rows = _Spy()
    ts = _Spy()
    stale = _codes(330000)
    fresh = _codes(340000)
    real_join = threading.Thread.join
    real_load = market_cache._disk_load
    path = cache_dir / "concept_stocks_t05cmiss.pkl"
    pd.to_pickle({"ts": FUTURE, "data": fresh}, path)
    old_bytes = path.read_bytes()
    rows[NAME] = stale
    ts[NAME] = EXPIRED
    rows.writes.clear()
    ts.writes.clear()

    def board(self, name, kind):
        fetches["n"] += 1
        raise AssertionError("expired concept memory must not fetch when disk is fresh")

    def counting_load(key):
        disk_loads["n"] += 1
        disk_loads["keys"].append(key)
        disk_loads["value"] = real_load(key)
        return disk_loads["value"]

    def counting_save(key, data):
        disk_saves["n"] += 1
        raise AssertionError("expired concept memory disk hit must not rewrite disk")

    def counting_fallback(self, kind, name):
        fallback_calls["n"] += 1
        raise AssertionError("expired concept memory disk hit must not use fallback")

    def recording_join(self, timeout=None):
        joins["n"] += 1
        raise AssertionError("expired concept memory disk hit must not join")

    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(market_cache, "_disk_load", counting_load)
    monkeypatch.setattr(market_cache, "_disk_save", counting_save)
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    monkeypatch.setattr(MarketCache, "_board_stocks_fallback", counting_fallback)
    MarketCache._shared_concept_stocks = rows
    MarketCache._shared_concept_stocks_ts = ts
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        memory_present = NAME in rows and rows[NAME] is stale and len(stale) == N
        stored_ts = ts[NAME]
        memory_age = clock.now - stored_ts
        memory_inside = memory_age < TTL
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_concept(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        industry_same = (
            MarketCache._shared_industry_stocks == saved_industry
            and MarketCache._shared_industry_stocks_ts == saved_industry_ts
        )
        kept = path.read_bytes()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        tracemalloc.stop()
        MarketCache._shared_concept_stocks = saved_rows
        MarketCache._shared_concept_stocks_ts = saved_ts
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    loaded = disk_loads["value"]
    disk_age = clock.now - FUTURE
    disk_inside = disk_age < TTL
    assert memory_present and stored_ts == EXPIRED
    assert memory_age == TTL and memory_age == 1800 and memory_inside is False
    assert disk_age == -1 and disk_inside
    assert stale != fresh and len(fresh) == N
    assert loaded["ts"] == FUTURE and loaded["data"] == fresh
    assert returned is loaded["data"] and returned is not stale and returned != stale
    assert rows[NAME] is loaded["data"] and rows[NAME] is not stale
    assert ts[NAME] == FUTURE and ts[NAME] != clock.now and ts[NAME] != EXPIRED
    assert rows.writes == [(NAME, loaded["data"])]
    assert ts.writes == [(NAME, FUTURE)]
    assert disk_loads["n"] == 1 and disk_loads["keys"] == [f"concept_stocks_{NAME}"]
    assert disk_saves["n"] == 0 and fetches["n"] == 0
    assert fallback_calls["n"] == 0 and joins["n"] == 0
    assert kept == old_bytes and len(old_bytes) > 0
    assert industry_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during == threads_before
    assert threads_after == threads_before
    print(
        f"T05_CONCEPT_EXPIRED_MEMORY_FUTURE_DISK rows={N} "
        f"memory_rows={N} memory_age_seconds={int(memory_age)} "
        f"memory_inside_ttl=0 memory_hits=0 "
        f"disk_rows={N} disk_loads=1 disk_hits=1 "
        f"disk_age_seconds={int(disk_age)} compare_lt_ttl=1 disk_inside_ttl=1 "
        f"same_disk_object=1 stale_returned=0 fetches=0 "
        f"fallback_calls=0 joins=0 disk_saves=0 "
        f"memory_writes=1 timestamp_writes=1 "
        f"timestamp_is_disk_ts=1 timestamp_rewritten_to_clock=0 "
        f"old_timestamp_kept=0 disk_bytes_unchanged=1 files=1 "
        f"industry_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_during={threads_during} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_EXPIRED_MEMORY_FUTURE_DISK" in captured.out
    sys.stderr.write(captured.out)
