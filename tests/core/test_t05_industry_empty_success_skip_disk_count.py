"""T05 read-only count: an empty industry success does not use expired disk.

Industry memory has no key. Disk holds 30 codes whose timestamp age is
exactly 1800s, so the fresh disk path misses and does not copy that list
into memory. The worker finishes and returns an empty list. It does not
raise. join uses the production 15s argument but returns at once because
the worker is already dead, so this is not the timeout path and the test
does not wait 15s. A successful empty list returns immediately. It does
not call the expired-disk fallback, does not create the memory key or the
timestamp key, and returns a new empty list rather than the 30 disk codes.
Disk bytes stay unchanged. Concept memory is left alone. Production code
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
NAME = "t05ieskip"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE + TTL

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_industry_empty_success_skip_disk_count(tmp_path, monkeypatch, capsys):
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
    fallback_calls = {"n": 0}
    kinds = []
    joins = {"n": 0, "timeout": None, "alive": None}
    disk_codes = _codes(600000)
    real_join = threading.Thread.join
    real_fallback = MarketCache._board_stocks_fallback
    path = cache_dir / "industry_stocks_t05ieskip.pkl"

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        return []

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
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    monkeypatch.setattr(MarketCache, "_board_stocks_fallback", counting_fallback)
    MarketCache._shared_industry_stocks.clear()
    MarketCache._shared_industry_stocks_ts.clear()
    pd.to_pickle({"ts": BASE, "data": disk_codes}, path)
    old_bytes = path.read_bytes()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        disk_payload = pd.read_pickle(path)
        age = clock.now - disk_payload["ts"]
        inside_ttl = age < TTL
        memory_before = NAME in MarketCache._shared_industry_stocks
        ts_before = NAME in MarketCache._shared_industry_stocks_ts
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_industry(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        memory_after = NAME in MarketCache._shared_industry_stocks
        ts_after = NAME in MarketCache._shared_industry_stocks_ts
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
    assert age == TTL and not inside_ttl
    assert disk_payload["data"] == disk_codes and disk_codes != []
    assert not memory_before and not ts_before
    assert returned == [] and returned is not disk_payload["data"]
    assert returned != disk_codes
    assert not memory_after and not ts_after
    assert stored["ts"] == BASE and stored["data"] == disk_codes
    assert kept == old_bytes and len(old_bytes) > 0
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
        f"T05_INDUSTRY_EMPTY_SUCCESS_SKIP_DISK rows={N} "
        f"success_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"fallback_fetches=1 raised=0 fallback_calls=0 fresh_disk_hit=0 "
        f"returned_empty=1 disk_list_returned=0 memory_written=0 "
        f"timestamp_written=0 bytes_unchanged=1 old_bytes={len(old_bytes)} "
        f"age_seconds={TTL} inside_ttl=0 concept_memory_unchanged=1 "
        f"call_seconds={elapsed:.4f} files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_EMPTY_SUCCESS_SKIP_DISK" in captured.out
    sys.stderr.write(captured.out)
