"""T05 read-only count: concept fetch error returns nothing for an empty disk list.

Concept memory has no key. The disk file exists, its data is an empty
list, and its timestamp age is 0, still inside the 1800s TTL. Empty data
is false, so the fresh disk path does not hit and does not copy that
timestamp into concept memory. The worker finishes and stores the raised
error. join uses the production 15s argument but returns at once because
the worker is already dead, so this is not the timeout path and the test
does not wait 15s. The fallback also skips the empty disk list and returns
a new empty list. It does not create the concept memory key or the
timestamp key. Disk bytes stay unchanged. The error does not escape the
caller. Industry memory is left alone. Production code is not changed, and
the real ~/.muyun/market_cache is not used. This is not the industry
empty-disk error count, and an empty disk list is not a hit.
"""
import sys
import threading
import time
import tracemalloc

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05cerred"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def test_t05_concept_error_empty_disk_count(tmp_path, monkeypatch, capsys):
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
    kinds = []
    joins = {"n": 0, "timeout": None, "alive": None}
    real_join = threading.Thread.join
    path = cache_dir / "concept_stocks_t05cerred.pkl"

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        raised["n"] += 1
        raise RuntimeError("stand-in concept fetch failed")

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
    MarketCache._shared_concept_stocks.clear()
    MarketCache._shared_concept_stocks_ts.clear()
    pd.to_pickle({"ts": BASE, "data": []}, path)
    old_bytes = path.read_bytes()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        disk_payload = pd.read_pickle(path)
        age = clock.now - disk_payload["ts"]
        inside_ttl = age < TTL
        memory_before = NAME in MarketCache._shared_concept_stocks
        ts_before = NAME in MarketCache._shared_concept_stocks_ts
        threading.Thread.join = recording_join
        started = time.perf_counter()
        fallback = cache.get_stocks_by_concept(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        memory_after = NAME in MarketCache._shared_concept_stocks
        ts_after = NAME in MarketCache._shared_concept_stocks_ts
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
    assert age == 0 and inside_ttl
    assert disk_payload["data"] == [] and not disk_payload["data"]
    assert not memory_before and not ts_before
    assert fallback == [] and fallback is not disk_payload["data"]
    assert not memory_after and not ts_after
    assert stored["ts"] == BASE and stored["data"] == []
    assert kept == old_bytes and len(old_bytes) > 0
    assert fetches["n"] == 1 and raised["n"] == 1
    assert kinds == [(NAME, "concept")]
    assert joins["n"] == 1 and joins["timeout"] == 15 and joins["alive"] is False
    assert industry_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during <= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_CONCEPT_ERROR_EMPTY_DISK rows={N} "
        f"error_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"fallback_fetches=1 raised=1 empty_disk_hit=0 returned_empty=1 "
        f"memory_written=0 timestamp_written=0 bytes_unchanged=1 "
        f"old_bytes={len(old_bytes)} age_seconds=0 inside_ttl=1 "
        f"industry_memory_unchanged=1 call_seconds={elapsed:.4f} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_ERROR_EMPTY_DISK" in captured.out
    sys.stderr.write(captured.out)
