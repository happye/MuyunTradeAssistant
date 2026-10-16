"""T05 read-only count when an industry write fails to replace an old file.

The first list is saved for real. Age 1799s still hits memory and, after
that memory is cleared, still hits disk. Neither hit writes. Age 1800s
misses both. to_pickle then raises before it writes any byte, so the
old file bytes stay the same and the caller still receives the new
list. After industry memory is cleared, the expired file is not a fresh
hit and the board fetch runs once more. The clock is moved directly.
Concept memory is not cleared. Production code is not changed, and the
real ~/.muyun/market_cache is not used. This is not the concept count.
"""
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05oldi"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_industry_disk_save_old_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.INDUSTRY_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_industry_stocks)
    saved_ts = dict(MarketCache._shared_industry_stocks_ts)
    saved_concept = dict(MarketCache._shared_concept_stocks)
    saved_concept_ts = dict(MarketCache._shared_concept_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    writes = {"n": 0}
    kinds = []
    elapsed = {"max": 0.0}
    old = _codes(600000)
    fresh_a = _codes(300000)
    fresh_b = _codes(300000)
    planned = [old, fresh_a, fresh_b]
    path = cache_dir / "industry_stocks_t05oldi.pkl"

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        if not planned:
            raise AssertionError("board called more times than scripted")
        return planned.pop(0)

    def boom(obj, filepath, *args, **kwargs):
        writes["n"] += 1
        target = Path(filepath)
        assert target.name == path.name
        assert target.resolve().is_relative_to(tmp_path.resolve())
        assert obj["data"] == fresh_a
        assert obj["data"] != old
        raise OSError("stand-in disk write failed before any byte")

    def timed(fn):
        started = time.perf_counter()
        try:
            return fn()
        finally:
            elapsed["max"] = max(elapsed["max"], time.perf_counter() - started)

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
        first = timed(lambda: cache.get_stocks_by_industry(NAME))
        old_bytes = path.read_bytes()
        stored_ts = pd.read_pickle(path)["ts"]
        clock.now = stored_ts + TTL - 1
        memory_hit = timed(lambda: cache.get_stocks_by_industry(NAME))
        after_memory_fetches = fetches["n"]
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks_ts.clear()
        disk_hit = timed(lambda: cache.get_stocks_by_industry(NAME))
        after_disk_fetches = fetches["n"]
        after_disk_bytes = path.read_bytes()
        monkeypatch.setattr(market_cache.pd, "to_pickle", boom)
        clock.now = stored_ts + TTL
        expired = timed(lambda: cache.get_stocks_by_industry(NAME))
        after_expire_fetches = fetches["n"]
        after_expire_writes = writes["n"]
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks_ts.clear()
        revived = timed(lambda: cache.get_stocks_by_industry(NAME))
        after_revive_fetches = fetches["n"]
        after_revive_writes = writes["n"]
        kept = path.read_bytes()
        concept_same = (
            MarketCache._shared_concept_stocks == saved_concept
            and MarketCache._shared_concept_stocks_ts == saved_concept_ts
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks.update(saved_rows)
        MarketCache._shared_industry_stocks_ts.clear()
        MarketCache._shared_industry_stocks_ts.update(saved_ts)
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    stored = pd.read_pickle(path)
    assert stored_ts == BASE
    assert first is old and memory_hit is first
    assert disk_hit == old and disk_hit is not first
    assert expired is fresh_a and revived is fresh_b
    assert revived == fresh_a and revived is not fresh_a and revived != old
    assert stored["ts"] == BASE and stored["data"] == old
    assert after_disk_bytes == old_bytes and kept == old_bytes and len(old_bytes) > 0
    assert after_memory_fetches == 1 and after_disk_fetches == 1
    assert after_expire_fetches == 2 and after_revive_fetches == 3
    assert after_expire_writes == 1 and after_revive_writes == 2
    assert kinds == [(NAME, "industry"), (NAME, "industry"), (NAME, "industry")]
    assert planned == []
    assert concept_same
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert elapsed["max"] < 5
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_DISK_SAVE_OLD rows={N} save_fetches=1 "
        f"hit_1799_memory=1 hit_1799_disk=1 hit_1799_fetches=0 "
        f"expire_1800_fetches={after_expire_fetches - after_disk_fetches} "
        f"expire_1800_write_errors={after_expire_writes} "
        f"after_clear_fetches={after_revive_fetches - after_expire_fetches} "
        f"after_clear_write_errors={after_revive_writes - after_expire_writes} "
        f"bytes_unchanged=1 old_bytes={len(old_bytes)} "
        f"concept_memory_unchanged=1 max_call_seconds={elapsed['max']:.4f} "
        f"files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_DISK_SAVE_OLD" in captured.out
    sys.stderr.write(captured.out)
