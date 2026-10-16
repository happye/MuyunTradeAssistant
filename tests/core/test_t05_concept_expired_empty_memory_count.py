"""T05 read-only count: a concept empty list aged exactly one TTL is a miss.

The concept key is present and its value is an empty list, but its
timestamp age is exactly 1800s. The fresh check is strict less-than, so
this is not a hit and the empty list is not returned. No disk file exists.
The disk load runs once and misses. The worker then finishes and returns
30 new codes. It does not raise. join uses the production 15s argument
but returns at once because the worker is already dead, so this is not
the timeout path and the test does not wait 15s. Concept memory is
replaced by those 30 codes, the timestamp becomes the current clock, and
the same list is written to disk. The fallback is not called. Industry
memory is left alone. Production code is not changed, and the real
~/.muyun/market_cache is not used. This is not the industry expired-empty
count.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05cexpem"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE + TTL

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_concept_expired_empty_memory_count(tmp_path, monkeypatch, capsys):
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
    empty = []
    fresh = _codes(300000)
    real_join = threading.Thread.join
    real_load = market_cache._disk_load
    real_fallback = MarketCache._board_stocks_fallback
    path = cache_dir / "concept_stocks_t05cexpem.pkl"

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
    MarketCache._shared_concept_stocks[NAME] = empty
    MarketCache._shared_concept_stocks_ts[NAME] = BASE
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        age = clock.now - BASE
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
    assert age == TTL and not inside_ttl
    assert empty == [] and returned is fresh and returned is not empty
    assert returned != empty
    assert memory_after is fresh and memory_after is not empty
    assert ts_after == clock.now and ts_after != BASE
    assert disk_loads["n"] == 1 and disk_loads["value"] is None
    assert disk_loads["keys"] == [f"concept_stocks_{NAME}"]
    assert stored["ts"] == clock.now and stored["data"] == fresh
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
        f"T05_CONCEPT_EXPIRED_EMPTY_MEMORY rows={N} "
        f"memory_hits=0 same_empty_returned=0 disk_loads=1 disk_hit=0 "
        f"success_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"fallback_fetches=1 raised=0 fallback_calls=0 memory_replaced=1 "
        f"timestamp_written=1 disk_written=1 age_seconds={TTL} inside_ttl=0 "
        f"industry_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"files={len(files)} bytes={path.stat().st_size} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_EXPIRED_EMPTY_MEMORY" in captured.out
    sys.stderr.write(captured.out)
