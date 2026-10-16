"""T05 read-only count: concept memory timestamp 0 still yields to fresh disk.

Concept memory holds 30 codes and the timestamp key stores 0, not a
missing-key default. The clock is BASE, so that age is far above the
1800s TTL and memory is not a hit. Disk holds a different list of 30
codes whose ts is BASE, so its age is 0 and still inside the TTL. The
caller receives the disk list, not the memory list. The fetch, join, and
fallback are not called. Concept memory is replaced by the disk list and
the timestamp becomes the disk ts rather than 0. Disk bytes stay
unchanged. Industry memory is left alone. Production code is not changed,
and the real ~/.muyun/market_cache is not used. This is not the industry
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
NAME = "t05czdsk"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_concept_zero_ts_fresh_disk_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.CONCEPT_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_concept_stocks)
    saved_ts = dict(MarketCache._shared_concept_stocks_ts)
    saved_industry = dict(MarketCache._shared_industry_stocks)
    saved_industry_ts = dict(MarketCache._shared_industry_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    disk_loads = {"n": 0, "keys": [], "value": "unset"}
    fallback_calls = {"n": 0}
    joins = {"n": 0}
    stale = _codes(220000)
    disk_codes = _codes(420000)
    real_join = threading.Thread.join
    real_load = market_cache._disk_load
    path = cache_dir / "concept_stocks_t05czdsk.pkl"
    pd.to_pickle({"ts": BASE, "data": disk_codes}, path)
    old_bytes = path.read_bytes()

    def board(self, name, kind):
        fetches["n"] += 1
        raise AssertionError("fresh disk must not fetch")

    def counting_load(key):
        disk_loads["n"] += 1
        disk_loads["keys"].append(key)
        disk_loads["value"] = real_load(key)
        return disk_loads["value"]

    def counting_fallback(self, kind, name):
        fallback_calls["n"] += 1
        raise AssertionError("fresh disk must not use fallback")

    def recording_join(self, timeout=None):
        joins["n"] += 1
        raise AssertionError("fresh disk must not join a worker")

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
        memory_age = clock.now - stored_ts
        memory_inside = memory_age < TTL
        disk_age = clock.now - BASE
        disk_inside = disk_age < TTL
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_concept(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        memory_after = MarketCache._shared_concept_stocks.get(NAME)
        ts_after = MarketCache._shared_concept_stocks_ts.get(NAME)
        kept = path.read_bytes()
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
    loaded = disk_loads["value"]
    assert ts_key_present and stored_ts == 0
    assert memory_age == BASE and memory_age > TTL and not memory_inside
    assert disk_age == 0 and disk_inside
    assert len(stale) == N and len(disk_codes) == N and stale != disk_codes
    assert loaded["ts"] == BASE and loaded["data"] == disk_codes
    assert returned is loaded["data"] and returned is not stale and returned != stale
    assert memory_after is returned and memory_after is not stale
    assert ts_after == BASE and ts_after != 0
    assert stored["ts"] == BASE and stored["data"] == disk_codes
    assert stored["data"] != stale
    assert kept == old_bytes and len(old_bytes) > 0
    assert disk_loads["n"] == 1 and disk_loads["keys"] == [f"concept_stocks_{NAME}"]
    assert fetches["n"] == 0 and joins["n"] == 0 and fallback_calls["n"] == 0
    assert industry_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during == threads_before
    assert threads_after == threads_before
    print(
        f"T05_CONCEPT_ZERO_TS_FRESH_DISK rows={N} memory_rows={len(stale)} "
        f"memory_hits=0 ts_key_present=1 stored_ts=0 "
        f"memory_age_seconds={int(memory_age)} memory_inside_ttl=0 "
        f"disk_rows={len(disk_codes)} disk_age_seconds=0 disk_inside_ttl=1 "
        f"disk_loads=1 disk_hit=1 same_memory_returned=0 "
        f"fetches=0 joins=0 fallback_calls=0 memory_replaced=1 "
        f"timestamp_written=1 timestamp_is_zero=0 bytes_unchanged=1 "
        f"old_bytes={len(old_bytes)} industry_memory_unchanged=1 "
        f"call_seconds={elapsed:.4f} files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_ZERO_TS_FRESH_DISK" in captured.out
    sys.stderr.write(captured.out)
