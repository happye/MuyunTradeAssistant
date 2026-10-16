"""T05 read-only count: industry timestamp 0 and an empty disk list miss.

Memory holds 30 codes and the timestamp key stores 0. The clock is BASE,
so the memory age is far above the TTL and memory is not a hit. Disk data
is an empty list whose ts is also BASE, so its age is 0 and still inside
the TTL. An empty list is falsy, so the fresh disk path does not hit and
does not copy that ts into memory. Neither the memory list nor the empty
list is returned. The disk load runs once. The worker then finishes and
returns a different list of 30 codes. It does not raise. join uses the
production 15s argument but returns at once because the worker is already
dead, so this is not the timeout path and the test does not wait 15s.
Memory is replaced by the new 30 codes and the timestamp becomes the
current clock rather than 0. That clock number equals the disk ts only
because both are BASE; the empty payload itself is not installed. The
disk file is overwritten with the new list. The fallback is not called.
Concept memory is left alone. Production code is not changed, and the
real ~/.muyun/market_cache is not used.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05izemp"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_industry_zero_ts_empty_disk_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.INDUSTRY_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_industry_stocks)
    saved_ts = dict(MarketCache._shared_industry_stocks_ts)
    saved_concept = dict(MarketCache._shared_concept_stocks)
    saved_concept_ts = dict(MarketCache._shared_concept_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    raised = {"n": 0}
    disk_loads = {"n": 0, "keys": [], "value": "unset"}
    fallback_calls = {"n": 0}
    kinds = []
    joins = {"n": 0, "timeout": None, "alive": None}
    stale = _codes(140000)
    empty = []
    fresh = _codes(340000)
    real_join = threading.Thread.join
    real_load = market_cache._disk_load
    real_fallback = MarketCache._board_stocks_fallback
    path = cache_dir / "industry_stocks_t05izemp.pkl"
    pd.to_pickle({"ts": BASE, "data": empty}, path)
    old_bytes = path.read_bytes()

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        return fresh

    def counting_load(key):
        disk_loads["n"] += 1
        disk_loads["keys"].append(key)
        disk_loads["value"] = real_load(key)
        return disk_loads["value"]

    def counting_fallback(self, kind, name):
        fallback_calls["n"] += 1
        return real_fallback(self, kind, name)

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
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    monkeypatch.setattr(MarketCache, "_board_stocks_fallback", counting_fallback)
    MarketCache._shared_industry_stocks.clear()
    MarketCache._shared_industry_stocks_ts.clear()
    MarketCache._shared_industry_stocks[NAME] = stale
    MarketCache._shared_industry_stocks_ts[NAME] = 0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        ts_key_present = NAME in MarketCache._shared_industry_stocks_ts
        stored_ts = MarketCache._shared_industry_stocks_ts[NAME]
        memory_age = clock.now - stored_ts
        memory_inside = memory_age < TTL
        disk_age = clock.now - BASE
        disk_inside = disk_age < TTL
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_industry(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        memory_after = MarketCache._shared_industry_stocks.get(NAME)
        ts_after = MarketCache._shared_industry_stocks_ts.get(NAME)
        kept = path.read_bytes()
        concept_same = (
            MarketCache._shared_concept_stocks == saved_concept
            and MarketCache._shared_concept_stocks_ts == saved_concept_ts
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        tracemalloc.stop()
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks.update(saved_rows)
        MarketCache._shared_industry_stocks_ts.clear()
        MarketCache._shared_industry_stocks_ts.update(saved_ts)
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    stored = pd.read_pickle(path)
    loaded = disk_loads["value"]
    assert ts_key_present and stored_ts == 0
    assert memory_age == BASE and memory_age > TTL and not memory_inside
    assert disk_age == 0 and disk_inside
    assert empty == [] and not empty
    assert len(stale) == N and len(fresh) == N and stale != fresh
    assert loaded["ts"] == BASE and loaded["data"] == empty
    assert returned is fresh and returned is not stale and returned != stale
    assert returned != empty and returned is not empty
    assert memory_after is fresh and memory_after is not stale
    assert memory_after != empty and memory_after is not empty
    assert ts_after == clock.now and ts_after == BASE and ts_after != 0
    assert stored["ts"] == clock.now and stored["data"] == fresh
    assert stored["data"] != empty and stored["data"] != stale
    assert kept != old_bytes and len(old_bytes) > 0 and len(kept) > len(old_bytes)
    assert disk_loads["n"] == 1 and disk_loads["keys"] == [f"industry_stocks_{NAME}"]
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
        f"T05_INDUSTRY_ZERO_TS_EMPTY_DISK rows={N} memory_rows={len(stale)} "
        f"memory_hits=0 ts_key_present=1 stored_ts=0 "
        f"memory_age_seconds={int(memory_age)} memory_inside_ttl=0 "
        f"disk_rows=0 disk_age_seconds=0 disk_inside_ttl=1 "
        f"empty_disk_falsy=1 disk_loads=1 disk_hit=0 "
        f"same_memory_returned=0 empty_returned=0 disk_ts_copied=0 "
        f"success_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"raised=0 fallback_calls=0 fetches=1 memory_replaced=1 "
        f"timestamp_written=1 timestamp_is_zero=0 "
        f"timestamp_equals_clock=1 disk_overwritten=1 "
        f"old_bytes={len(old_bytes)} new_bytes={len(kept)} "
        f"concept_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_ZERO_TS_EMPTY_DISK" in captured.out
    sys.stderr.write(captured.out)
