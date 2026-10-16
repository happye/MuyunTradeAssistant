"""T05 read-only count: an industry fetch error skips an empty memory list.

The industry key is present, but its value is an empty list aged exactly
1800s. Disk holds a different non-empty list of the same age, so the fresh
TTL path misses both. The worker finishes and stores the raised error.
join uses the production 15s argument but returns at once because the
worker is already dead, so this is not the timeout path and the test does
not wait 15s. An empty list is false, so the fallback returns the disk
list, not the empty list. It does not copy that disk list back into memory
and does not refresh the memory timestamp. Disk bytes stay unchanged. The
error does not escape the caller. Concept memory is left alone. Production
code is not changed, and the real ~/.muyun/market_cache is not used. This
does not treat the empty list as a preferred hit, and it is not a timeout.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05ierrem"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_industry_error_empty_memory_count(tmp_path, monkeypatch, capsys):
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
    kinds = []
    joins = {"n": 0, "timeout": None, "alive": None}
    phase = {"raise": False}
    disk_codes = _codes(600000)
    empty = []
    real_join = threading.Thread.join

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        if not phase["raise"]:
            return disk_codes
        raised["n"] += 1
        raise RuntimeError("stand-in industry fetch failed")

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
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    MarketCache._shared_industry_stocks.clear()
    MarketCache._shared_industry_stocks_ts.clear()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = cache.get_stocks_by_industry(NAME)
        path = cache_dir / "industry_stocks_t05ierrem.pkl"
        old_bytes = path.read_bytes()
        stored_ts = pd.read_pickle(path)["ts"]
        MarketCache._shared_industry_stocks[NAME] = empty
        MarketCache._shared_industry_stocks_ts[NAME] = stored_ts
        clock.now = stored_ts + TTL
        fresh_memory = (clock.now - stored_ts) < TTL
        disk_payload = pd.read_pickle(path)
        fresh_disk = (clock.now - disk_payload["ts"]) < TTL
        phase["raise"] = True
        threading.Thread.join = recording_join
        started = time.perf_counter()
        fallback = cache.get_stocks_by_industry(NAME)
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
    assert stored_ts == BASE
    assert clock.now - stored_ts == TTL
    assert not fresh_memory and not fresh_disk
    assert empty == [] and disk_codes != []
    assert first is disk_codes
    assert fallback == disk_codes and fallback is not disk_codes
    assert fallback != empty
    assert memory_after is empty and memory_after == []
    assert ts_after == BASE and ts_after != clock.now
    assert stored["ts"] == BASE and stored["data"] == disk_codes
    assert kept == old_bytes and len(old_bytes) > 0
    assert fetches["n"] == 2 and raised["n"] == 1
    assert kinds == [(NAME, "industry"), (NAME, "industry")]
    assert joins["n"] == 1 and joins["timeout"] == 15 and joins["alive"] is False
    assert concept_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during <= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_ERROR_EMPTY_MEMORY rows={N} save_fetches=1 "
        f"error_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"fallback_fetches=1 raised=1 empty_memory_preferred=0 "
        f"disk_list_returned=1 memory_written_back=0 timestamp_refreshed=0 "
        f"bytes_unchanged=1 old_bytes={len(old_bytes)} age_seconds={TTL} "
        f"concept_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_ERROR_EMPTY_MEMORY" in captured.out
    sys.stderr.write(captured.out)
