"""T05 read-only count: a concept timestamp stored as 0 is not a hit.

The concept list key is present and holds 30 codes. The timestamp key is
also present and its stored value is 0, which is not the missing-key
default. The clock is BASE, so the age is BASE minus 0 and far above the
1800s TTL. Those 30 codes are not returned. No disk file exists. The disk
load runs once and misses. The worker then finishes and returns a
different list of 30 codes. It does not raise. join uses the production
15s argument but returns at once because the worker is already dead, so
this is not the timeout path and the test does not wait 15s. Concept
memory is replaced by the new 30 codes, the timestamp becomes the current
clock rather than 0, and that new list is written to disk. The fallback
is not called. Industry memory is left alone. Production code is not
changed, and the real ~/.muyun/market_cache is not used. This is not the
missing-key count and not the industry count.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05czts"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_concept_zero_ts_memory_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.CONCEPT_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_concept_stocks)
    saved_ts = dict(MarketCache._shared_concept_stocks_ts)
    saved_industry = dict(MarketCache._shared_industry_stocks)
    saved_industry_ts = dict(MarketCache._shared_industry_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    raised = {"n": 0}
    disk_loads = {"n": 0, "keys": [], "value": "unset"}
    fallback_calls = {"n": 0}
    kinds = []
    joins = {"n": 0, "timeout": None, "alive": None}
    stale = _codes(210000)
    fresh = _codes(410000)
    real_join = threading.Thread.join
    real_load = market_cache._disk_load
    real_fallback = MarketCache._board_stocks_fallback
    path = cache_dir / "concept_stocks_t05czts.pkl"

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
    MarketCache._shared_concept_stocks.clear()
    MarketCache._shared_concept_stocks_ts.clear()
    MarketCache._shared_concept_stocks[NAME] = stale
    MarketCache._shared_concept_stocks_ts[NAME] = 0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        ts_key_present = NAME in MarketCache._shared_concept_stocks_ts
        stored_ts = MarketCache._shared_concept_stocks_ts[NAME]
        got_without_default = MarketCache._shared_concept_stocks_ts.get(NAME)
        got_with_default = MarketCache._shared_concept_stocks_ts.get(NAME, 0)
        age = clock.now - stored_ts
        inside_ttl = age < TTL
        assert not path.exists()
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_concept(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        memory_after = MarketCache._shared_concept_stocks.get(NAME)
        ts_after = MarketCache._shared_concept_stocks_ts.get(NAME)
        industry_same = (
            MarketCache._shared_industry_stocks == saved_industry
            and MarketCache._shared_industry_stocks_ts == saved_industry_ts
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        tracemalloc.stop()
        MarketCache._shared_concept_stocks.clear()
        MarketCache._shared_concept_stocks.update(saved_rows)
        MarketCache._shared_concept_stocks_ts.clear()
        MarketCache._shared_concept_stocks_ts.update(saved_ts)
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    stored = pd.read_pickle(path)
    assert ts_key_present
    assert stored_ts == 0 and got_without_default == 0 and got_with_default == 0
    assert age == BASE and age > TTL and not inside_ttl
    assert len(stale) == N and len(fresh) == N and stale != fresh
    assert returned is fresh and returned is not stale and returned != stale
    assert memory_after is fresh and memory_after is not stale
    assert ts_after == clock.now and ts_after == BASE and ts_after != 0
    assert disk_loads["n"] == 1 and disk_loads["value"] is None
    assert disk_loads["keys"] == [f"concept_stocks_{NAME}"]
    assert stored["ts"] == clock.now and stored["data"] == fresh
    assert stored["data"] != stale
    assert fetches["n"] == 1 and raised["n"] == 0
    assert fallback_calls["n"] == 0
    assert kinds == [(NAME, "concept")]
    assert joins["n"] == 1 and joins["timeout"] == 15 and joins["alive"] is False
    assert industry_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert path.read_bytes()
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during <= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_CONCEPT_ZERO_TS_MEMORY rows={N} memory_rows={len(stale)} "
        f"memory_hits=0 ts_key_present=1 stored_ts=0 "
        f"get_without_default=0 age_seconds={int(age)} inside_ttl=0 "
        f"same_stale_returned=0 disk_loads=1 disk_hit=0 "
        f"success_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"raised=0 fallback_calls=0 memory_replaced=1 timestamp_written=1 "
        f"timestamp_is_zero=0 disk_written=1 industry_memory_unchanged=1 "
        f"call_seconds={elapsed:.4f} files={len(files)} "
        f"bytes={path.stat().st_size} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_ZERO_TS_MEMORY" in captured.out
    sys.stderr.write(captured.out)
