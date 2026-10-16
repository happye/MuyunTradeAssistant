"""T05 read-only count for an industry timeout falling back to expired disk.

The worker enters the fetch and then stops. join returns at once with
the production 15s argument, so the test does not wait 15s, and the
worker is still alive. Memory is empty and the disk age is exactly
1800s, so the fresh TTL path misses. The fallback still returns the
old list, leaves the file bytes unchanged, and does not copy the old
timestamp back into memory. The list released later by that worker
does not enter memory and does not replace the file. Production code
is not changed, and the real ~/.muyun/market_cache is not used.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05tout"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_industry_timeout_fallback_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.INDUSTRY_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_industry_stocks)
    saved_ts = dict(MarketCache._shared_industry_stocks_ts)
    saved_concept = dict(MarketCache._shared_concept_stocks)
    saved_concept_ts = dict(MarketCache._shared_concept_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    kinds = []
    joins = {"n": 0, "timeout": None}
    phase = {"block": False}
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    old = _codes(600000)
    fresh = _codes(300000)
    real_join = threading.Thread.join

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        if not phase["block"]:
            return old
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
    MarketCache._shared_industry_stocks.clear()
    MarketCache._shared_industry_stocks_ts.clear()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = cache.get_stocks_by_industry(NAME)
        path = cache_dir / "industry_stocks_t05tout.pkl"
        old_bytes = path.read_bytes()
        stored_ts = pd.read_pickle(path)["ts"]
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks_ts.clear()
        clock.now = stored_ts + TTL
        phase["block"] = True
        threading.Thread.join = fast_join
        started = time.perf_counter()
        fallback = cache.get_stocks_by_industry(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        memory_after = NAME in MarketCache._shared_industry_stocks
        ts_after = NAME in MarketCache._shared_industry_stocks_ts
        threading.Thread.join = real_join
        release.set()
        assert finished.wait(2)
        late_memory = NAME in MarketCache._shared_industry_stocks
        late_ts = NAME in MarketCache._shared_industry_stocks_ts
        kept = path.read_bytes()
        concept_same = (
            MarketCache._shared_concept_stocks == saved_concept
            and MarketCache._shared_concept_stocks_ts == saved_concept_ts
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        release.set()
        if entered.is_set():
            finished.wait(2)
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
    assert first is old
    assert fallback == old and fallback is not old and fallback != fresh
    assert not memory_after and not ts_after
    assert not late_memory and not late_ts
    assert stored["ts"] == BASE and stored["data"] == old
    assert kept == old_bytes and len(old_bytes) > 0
    assert fetches["n"] == 2
    assert kinds == [(NAME, "industry"), (NAME, "industry")]
    assert joins["n"] == 1 and joins["timeout"] == 15
    assert finished.is_set()
    assert concept_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during >= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_TIMEOUT_FALLBACK rows={N} save_fetches=1 "
        f"timeout_joins=1 join_timeout_arg=15 fallback_fetches=1 "
        f"memory_written_back=0 late_memory_written=0 bytes_unchanged=1 "
        f"old_bytes={len(old_bytes)} age_seconds={TTL} "
        f"concept_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_TIMEOUT_FALLBACK" in captured.out
    sys.stderr.write(captured.out)
