"""T05 read-only count: an industry disk file with ts but no data misses.

The industry memory key is absent. The disk pickle is a dict whose only
key is ts, set to BASE-1, and it has no data key. Real _disk_load returns
that dict because its timestamp is valid. The clock is
BASE, so the disk age is 1 and is inside the strict less-than 1800 test.
_d.get("data") is None, which is falsy, so this is not a hit and nothing
from the disk dict is copied into memory. The disk load runs once. The
worker then finishes and returns a list of 30 codes. It does not raise.
join uses the production 15s argument but returns at once because the
worker is already dead, so this is not the timeout path and the test does
not wait 15s. Memory is written with those 30 codes and the timestamp
becomes the current clock BASE, not the disk ts BASE-1. The disk file is
overwritten with that list and the clock ts. The fallback is not called.
Concept memory is left alone. Production code is not changed, and the real
~/.muyun/market_cache is not used. This is not the missing-ts count and
not the empty-list count.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05inoda"
BASE = 1_700_000_000.0
TTL = 1800
STORED_TS = BASE - 1


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


def test_t05_industry_missing_data_disk_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.INDUSTRY_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = MarketCache._shared_industry_stocks
    saved_ts = MarketCache._shared_industry_stocks_ts
    saved_concept = dict(MarketCache._shared_concept_stocks)
    saved_concept_ts = dict(MarketCache._shared_concept_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    raised = {"n": 0}
    disk_loads = {"n": 0, "keys": [], "value": "unset"}
    disk_saves = {"n": 0}
    fallback_calls = {"n": 0}
    kinds = []
    joins = {"n": 0, "timeout": None, "alive": None}
    fresh = _codes(760000)
    rows = _Spy()
    ts = _Spy()
    real_join = threading.Thread.join
    real_load = market_cache._disk_load
    real_save = market_cache._disk_save
    path = cache_dir / "industry_stocks_t05inoda.pkl"
    pd.to_pickle({"ts": STORED_TS}, path)
    old_bytes = path.read_bytes()
    raw = pd.read_pickle(path)

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        return fresh

    def counting_load(key):
        disk_loads["n"] += 1
        disk_loads["keys"].append(key)
        disk_loads["value"] = real_load(key)
        return disk_loads["value"]

    def counting_save(key, data):
        disk_saves["n"] += 1
        return real_save(key, data)

    def counting_fallback(self, kind, name):
        fallback_calls["n"] += 1
        raise AssertionError("industry disk without data must not use fallback")

    def recording_join(self, timeout=None):
        joins["n"] += 1
        joins["timeout"] = timeout
        assert timeout == 15
        real_join(self, timeout)
        joins["alive"] = self.is_alive()
        assert joins["alive"] is False
        return None

    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(market_cache, "_disk_load", counting_load)
    monkeypatch.setattr(market_cache, "_disk_save", counting_save)
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    monkeypatch.setattr(MarketCache, "_board_stocks_fallback", counting_fallback)
    MarketCache._shared_industry_stocks = rows
    MarketCache._shared_industry_stocks_ts = ts
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        memory_absent = NAME not in rows and NAME not in ts
        disk_age = clock.now - raw["ts"]
        disk_inside = disk_age < TTL
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_industry(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        concept_same = (
            MarketCache._shared_concept_stocks == saved_concept
            and MarketCache._shared_concept_stocks_ts == saved_concept_ts
        )
        kept = path.read_bytes()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        tracemalloc.stop()
        MarketCache._shared_industry_stocks = saved_rows
        MarketCache._shared_industry_stocks_ts = saved_ts
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    stored = pd.read_pickle(path)
    loaded = disk_loads["value"]
    assert memory_absent
    assert isinstance(raw, dict) and "data" not in raw and raw["ts"] == STORED_TS
    assert disk_age == 1 and disk_inside
    assert len(fresh) == N
    assert loaded is not None and loaded == raw and "data" not in loaded
    assert loaded.get("data") is None and not loaded.get("data")
    assert returned is fresh
    assert rows[NAME] is fresh
    assert ts[NAME] == clock.now == BASE and ts[NAME] != STORED_TS
    assert rows.writes == [(NAME, fresh)]
    assert ts.writes == [(NAME, BASE)]
    assert all(value is not None for _, value in rows.writes)
    assert stored["ts"] == clock.now == BASE and stored["ts"] != STORED_TS
    assert stored["data"] == fresh and "data" in stored
    assert kept != old_bytes and len(old_bytes) > 0 and len(kept) > len(old_bytes)
    assert disk_loads["n"] == 1 and disk_loads["keys"] == [f"industry_stocks_{NAME}"]
    assert disk_saves["n"] == 1
    assert fetches["n"] == 1 and raised["n"] == 0
    assert fallback_calls["n"] == 0
    assert kinds == [(NAME, "industry")]
    assert joins["n"] == 1 and joins["timeout"] == 15 and joins["alive"] is False
    assert concept_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during <= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_MISSING_DATA_DISK rows={N} memory_key_absent=1 "
        f"disk_load_is_none=0 disk_has_ts=1 disk_has_data=0 "
        f"age_seconds={int(disk_age)} compare_lt_ttl=1 inside_ttl=1 "
        f"disk_loads=1 disk_hit=0 disk_copied_to_memory=0 "
        f"success_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"raised=0 fallback_calls=0 fetches=1 disk_saves=1 "
        f"memory_writes=1 timestamp_writes=1 "
        f"timestamp_is_clock=1 timestamp_is_disk_ts=0 "
        f"disk_overwritten=1 old_bytes={len(old_bytes)} new_bytes={len(kept)} "
        f"files=1 concept_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_during={threads_during} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_MISSING_DATA_DISK" in captured.out
    sys.stderr.write(captured.out)
