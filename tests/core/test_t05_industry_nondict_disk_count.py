"""T05 read-only count: an industry disk file that is a bare list loads as None.

The industry memory key is absent. The disk pickle is a list of 30 codes,
not a dict. Real _disk_load requires a dict that contains ts, so it returns
None. None is not a hit, and those 30 codes are not copied into memory.
The disk load runs once. The worker then finishes and returns a different
list of 30 codes. It does not raise. join uses the production 15s argument
but returns at once because the worker is already dead, so this is not the
timeout path and the test does not wait 15s. Memory is written with the
new 30 codes and the timestamp becomes the current clock BASE. The disk
file is overwritten with a dict that has that list and a ts. The fallback
is not called. Concept memory is left alone. Production code is not
changed, and the real ~/.muyun/market_cache is not used. This is not the
missing-ts count and not the missing-data count.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05inolst"
BASE = 1_700_000_000.0
TTL = 1800


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


def test_t05_industry_nondict_disk_count(tmp_path, monkeypatch, capsys):
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
    parked = _codes(180000)
    fresh = _codes(680000)
    rows = _Spy()
    ts = _Spy()
    real_join = threading.Thread.join
    real_load = market_cache._disk_load
    real_save = market_cache._disk_save
    path = cache_dir / "industry_stocks_t05inolst.pkl"
    pd.to_pickle(parked, path)
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
        raise AssertionError("industry bare-list disk must not use fallback")

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
    assert isinstance(raw, list) and not isinstance(raw, dict) and raw == parked
    assert len(parked) == N and len(fresh) == N and parked != fresh
    assert loaded is None
    assert returned is fresh and returned != parked and returned is not parked
    assert rows[NAME] is fresh and rows[NAME] != parked
    assert ts[NAME] == clock.now == BASE
    assert rows.writes == [(NAME, fresh)]
    assert ts.writes == [(NAME, BASE)]
    assert all(value is not parked and value != parked for _, value in rows.writes)
    assert isinstance(stored, dict) and stored["ts"] == clock.now == BASE
    assert stored["data"] == fresh and stored["data"] != parked
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
        f"T05_INDUSTRY_NONDICT_DISK rows={N} memory_key_absent=1 "
        f"disk_rows={N} disk_is_dict=0 disk_loads=1 disk_load_is_none=1 "
        f"disk_hit=0 parked_copied_to_memory=0 "
        f"success_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"raised=0 fallback_calls=0 fetches=1 disk_saves=1 "
        f"memory_writes=1 timestamp_writes=1 timestamp_is_clock=1 "
        f"disk_overwritten=1 old_bytes={len(old_bytes)} new_bytes={len(kept)} "
        f"files=1 concept_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_during={threads_during} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_NONDICT_DISK" in captured.out
    sys.stderr.write(captured.out)
