"""T05 read-only count: a future concept timestamp is still a memory hit.

The concept list key is present and holds 30 codes. The timestamp key is
present and stores BASE+1. The clock is BASE, so the age is -1. The
production comparison is strict less-than 1800, and -1 is inside that
test, so the same 30-code list is returned. The fetch, the disk load, the
fallback, and join are not called. The timestamp stays BASE+1 and is not
rewritten to the current clock. No disk file is created. Industry memory
is left alone. Production code is not changed, and the real
~/.muyun/market_cache is not used. This is not the industry count.
"""
import sys
import threading
import time
import tracemalloc

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05cfut"
BASE = 1_700_000_000.0
TTL = 1800
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
        self.writes.append(key)
        super().__setitem__(key, value)


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_concept_future_ts_memory_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.CONCEPT_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = MarketCache._shared_concept_stocks
    saved_ts = MarketCache._shared_concept_stocks_ts
    saved_industry = dict(MarketCache._shared_industry_stocks)
    saved_industry_ts = dict(MarketCache._shared_industry_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    disk_loads = {"n": 0}
    fallback_calls = {"n": 0}
    joins = {"n": 0}
    rows = _Spy()
    ts = _Spy()
    codes = _codes(300000)
    real_join = threading.Thread.join
    rows[NAME] = codes
    ts[NAME] = FUTURE
    rows.writes.clear()
    ts.writes.clear()

    def board(self, name, kind):
        fetches["n"] += 1
        raise AssertionError("future concept timestamp must not fetch")

    def counting_load(key):
        disk_loads["n"] += 1
        raise AssertionError("future concept timestamp must not read disk")

    def counting_fallback(self, kind, name):
        fallback_calls["n"] += 1
        raise AssertionError("future concept timestamp must not use fallback")

    def recording_join(self, timeout=None):
        joins["n"] += 1
        raise AssertionError("future concept timestamp must not join a worker")

    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(market_cache, "_disk_load", counting_load)
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    monkeypatch.setattr(MarketCache, "_board_stocks_fallback", counting_fallback)
    MarketCache._shared_concept_stocks = rows
    MarketCache._shared_concept_stocks_ts = ts
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        list_present = NAME in rows and rows[NAME] is codes and len(rows[NAME]) == N
        ts_present = NAME in ts
        stored_ts = ts[NAME]
        memory_age = clock.now - stored_ts
        inside_ttl = memory_age < TTL
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_concept(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        industry_same = (
            MarketCache._shared_industry_stocks == saved_industry
            and MarketCache._shared_industry_stocks_ts == saved_industry_ts
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        tracemalloc.stop()
        MarketCache._shared_concept_stocks = saved_rows
        MarketCache._shared_concept_stocks_ts = saved_ts
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    assert list_present and ts_present
    assert stored_ts == FUTURE and stored_ts == BASE + 1
    assert memory_age == -1 and inside_ttl
    assert returned is codes and len(returned) == N
    assert rows[NAME] is codes and ts[NAME] == FUTURE and ts[NAME] != clock.now
    assert rows.writes == [] and ts.writes == []
    assert fetches["n"] == 0 and disk_loads["n"] == 0
    assert fallback_calls["n"] == 0 and joins["n"] == 0
    assert industry_same
    assert elapsed < 5
    assert files == []
    assert threads_during == threads_before
    assert threads_after == threads_before
    print(
        f"T05_CONCEPT_FUTURE_TS_MEMORY rows={N} memory_hits=1 "
        f"same_object=1 ts_key_present=1 stored_ts_offset=1 "
        f"age_seconds={int(memory_age)} compare_lt_ttl=1 inside_ttl=1 "
        f"fetches=0 disk_loads=0 fallback_calls=0 joins=0 "
        f"timestamp_writes=0 memory_writes=0 timestamp_kept_future=1 "
        f"timestamp_rewritten_to_clock=0 files=0 "
        f"industry_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_during={threads_during} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_FUTURE_TS_MEMORY" in captured.out
    sys.stderr.write(captured.out)
