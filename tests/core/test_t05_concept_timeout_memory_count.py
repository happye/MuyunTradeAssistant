"""T05 read-only count: concept timeout prefers expired memory over disk.

Concept memory holds a list that differs from the on-disk list. Both
ages are exactly 1800s, so the fresh TTL path misses memory and disk.
The worker enters the fetch and then stops. join returns at once with
the production 15s argument, so the test does not wait 15s, and the
worker is still alive. The fallback returns that concept memory list,
not the disk list, and does not refresh the concept memory timestamp.
Disk bytes stay unchanged. The list released later by the worker does
not replace concept memory or the file. Industry memory is left alone.
Production code is not changed, and the real ~/.muyun/market_cache is
not used. This is not the industry timeout-memory count.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05cmem"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_concept_timeout_memory_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.CONCEPT_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_concept_stocks)
    saved_ts = dict(MarketCache._shared_concept_stocks_ts)
    saved_industry = dict(MarketCache._shared_industry_stocks)
    saved_industry_ts = dict(MarketCache._shared_industry_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    kinds = []
    joins = {"n": 0, "timeout": None}
    phase = {"block": False}
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    disk_codes = _codes(600000)
    mem = _codes(100000)
    fresh = _codes(300000)
    real_join = threading.Thread.join

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        if not phase["block"]:
            return disk_codes
        entered.set()
        release.wait()
        finished.set()
        return fresh

    def fast_join(self, timeout=None):
        joins["n"] += 1
        joins["timeout"] = timeout
        assert timeout == 15
        assert entered.wait(2)
        assert self.is_alive()
        return None

    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    MarketCache._shared_concept_stocks.clear()
    MarketCache._shared_concept_stocks_ts.clear()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = cache.get_stocks_by_concept(NAME)
        path = cache_dir / "concept_stocks_t05cmem.pkl"
        old_bytes = path.read_bytes()
        stored_ts = pd.read_pickle(path)["ts"]
        MarketCache._shared_concept_stocks[NAME] = mem
        MarketCache._shared_concept_stocks_ts[NAME] = stored_ts
        clock.now = stored_ts + TTL
        fresh_memory = (clock.now - stored_ts) < TTL
        disk_payload = pd.read_pickle(path)
        fresh_disk = (clock.now - disk_payload["ts"]) < TTL
        phase["block"] = True
        threading.Thread.join = fast_join
        started = time.perf_counter()
        fallback = cache.get_stocks_by_concept(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        memory_after = MarketCache._shared_concept_stocks.get(NAME)
        ts_after = MarketCache._shared_concept_stocks_ts.get(NAME)
        threading.Thread.join = real_join
        release.set()
        assert finished.wait(2)
        late_memory = MarketCache._shared_concept_stocks.get(NAME)
        late_ts = MarketCache._shared_concept_stocks_ts.get(NAME)
        kept = path.read_bytes()
        industry_same = (
            MarketCache._shared_industry_stocks == saved_industry
            and MarketCache._shared_industry_stocks_ts == saved_industry_ts
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        release.set()
        if entered.is_set():
            finished.wait(2)
        tracemalloc.stop()
        MarketCache._shared_concept_stocks.clear()
        MarketCache._shared_concept_stocks.update(saved_rows)
        MarketCache._shared_concept_stocks_ts.clear()
        MarketCache._shared_concept_stocks_ts.update(saved_ts)
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    stored = pd.read_pickle(path)
    assert stored_ts == BASE
    assert clock.now - stored_ts == TTL
    assert not fresh_memory and not fresh_disk
    assert mem != disk_codes and mem != fresh and disk_codes != fresh
    assert first is disk_codes
    assert fallback is mem
    assert fallback != disk_codes and fallback != fresh
    assert memory_after is mem and ts_after == BASE and ts_after != clock.now
    assert late_memory is mem and late_ts == BASE
    assert stored["ts"] == BASE and stored["data"] == disk_codes
    assert stored["data"] != mem
    assert kept == old_bytes and len(old_bytes) > 0
    assert fetches["n"] == 2
    assert kinds == [(NAME, "concept"), (NAME, "concept")]
    assert joins["n"] == 1 and joins["timeout"] == 15
    assert finished.is_set()
    assert industry_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during >= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_CONCEPT_TIMEOUT_MEMORY rows={N} save_fetches=1 "
        f"timeout_joins=1 join_timeout_arg=15 fallback_fetches=1 "
        f"memory_preferred=1 timestamp_refreshed=0 late_memory_replaced=0 "
        f"disk_list_returned=0 bytes_unchanged=1 old_bytes={len(old_bytes)} "
        f"age_seconds={TTL} industry_memory_unchanged=1 "
        f"call_seconds={elapsed:.4f} files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_TIMEOUT_MEMORY" in captured.out
    sys.stderr.write(captured.out)
