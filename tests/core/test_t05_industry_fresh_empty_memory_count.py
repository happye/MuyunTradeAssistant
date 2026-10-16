"""T05 read-only count: a fresh empty industry memory list is a hit.

The industry key is present. Its value is an empty list and its timestamp
age is 0, still inside the 1800s TTL. Key presence is enough, so the empty
list is returned as the same object. The fetch, the disk load, the
fallback, and join are not called. The timestamp is not written again.
No disk file is created. Concept memory is left alone. Production code is
not changed, and the real ~/.muyun/market_cache is not used. This is the
opposite of an empty disk list, which is not a fresh hit.
"""
import sys
import threading
import time
import tracemalloc

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

NAME = "t05iemhit"
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
        self.writes.append(key)
        super().__setitem__(key, value)


def test_t05_industry_fresh_empty_memory_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.INDUSTRY_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = MarketCache._shared_industry_stocks
    saved_ts = MarketCache._shared_industry_stocks_ts
    saved_concept = dict(MarketCache._shared_concept_stocks)
    saved_concept_ts = dict(MarketCache._shared_concept_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    disk_loads = {"n": 0}
    fallback_calls = {"n": 0}
    joins = {"n": 0}
    empty = []
    rows = _Spy()
    ts = _Spy()
    real_join = threading.Thread.join
    rows[NAME] = empty
    ts[NAME] = BASE
    rows.writes.clear()
    ts.writes.clear()

    def board(self, name, kind):
        fetches["n"] += 1
        raise AssertionError("fresh empty memory must not fetch")

    def counting_load(key):
        disk_loads["n"] += 1
        raise AssertionError("fresh empty memory must not read disk")

    def counting_fallback(self, kind, name):
        fallback_calls["n"] += 1
        raise AssertionError("fresh empty memory must not use fallback")

    def recording_join(self, timeout=None):
        joins["n"] += 1
        raise AssertionError("fresh empty memory must not join a worker")

    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(market_cache, "_disk_load", counting_load)
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    monkeypatch.setattr(MarketCache, "_board_stocks_fallback", counting_fallback)
    MarketCache._shared_industry_stocks = rows
    MarketCache._shared_industry_stocks_ts = ts
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        age = clock.now - ts[NAME]
        inside_ttl = age < TTL
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_industry(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        concept_same = (
            MarketCache._shared_concept_stocks == saved_concept
            and MarketCache._shared_concept_stocks_ts == saved_concept_ts
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        tracemalloc.stop()
        MarketCache._shared_industry_stocks = saved_rows
        MarketCache._shared_industry_stocks_ts = saved_ts
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    assert age == 0 and inside_ttl
    assert empty == [] and returned is empty
    assert rows[NAME] is empty and ts[NAME] == BASE
    assert rows.writes == [] and ts.writes == []
    assert fetches["n"] == 0 and disk_loads["n"] == 0
    assert fallback_calls["n"] == 0 and joins["n"] == 0
    assert concept_same
    assert elapsed < 5
    assert files == []
    assert threads_during == threads_before
    assert threads_after == threads_before
    print(
        f"T05_INDUSTRY_FRESH_EMPTY_MEMORY empty_rows=0 "
        f"memory_hits=1 same_object=1 fetches=0 disk_loads=0 "
        f"fallback_calls=0 joins=0 timestamp_writes=0 memory_writes=0 "
        f"age_seconds=0 inside_ttl=1 files=0 "
        f"concept_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_during={threads_during} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_FRESH_EMPTY_MEMORY" in captured.out
    sys.stderr.write(captured.out)
