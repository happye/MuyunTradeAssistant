"""T05 read-only count when an industry-member disk write fails.

The directory starts empty. to_pickle raises before it writes any
byte, and the caller still receives the code list. The exception does
not escape. A second call hits memory and does not fetch. After the
industry memory is cleared, the missing file is not a hit, so the
board fetch runs once more. The stand-in returns inside the existing
15s thread wrapper and does not wait out the timeout. Production code
is not changed, and the real ~/.muyun/market_cache is not used. This
is not the market-snapshot write-failure count.
"""
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05semi"


def _codes():
    return [f"{600000 + i:06d}" for i in range(N)]


def test_t05_industry_disk_save_fail_count(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_industry_stocks)
    saved_ts = dict(MarketCache._shared_industry_stocks_ts)
    fetches = {"n": 0}
    writes = {"n": 0}
    kinds = []
    elapsed = {"max": 0.0}

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        return _codes()

    def boom(obj, filepath, *args, **kwargs):
        writes["n"] += 1
        path = Path(filepath)
        assert path.name == "industry_stocks_t05semi.pkl"
        assert path.resolve().is_relative_to(tmp_path.resolve())
        assert obj["data"] == _codes()
        raise OSError("stand-in disk write failed before any byte")

    def timed(fn):
        started = time.perf_counter()
        try:
            return fn()
        finally:
            elapsed["max"] = max(elapsed["max"], time.perf_counter() - started)

    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(market_cache.pd, "to_pickle", boom)
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    MarketCache._shared_industry_stocks.clear()
    MarketCache._shared_industry_stocks_ts.clear()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = timed(lambda: cache.get_stocks_by_industry(NAME))
        after_first_fetches = fetches["n"]
        after_first_writes = writes["n"]
        held = timed(lambda: cache.get_stocks_by_industry(NAME))
        after_hold_fetches = fetches["n"]
        after_hold_writes = writes["n"]
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks_ts.clear()
        second = timed(lambda: cache.get_stocks_by_industry(NAME))
        after_second_fetches = fetches["n"]
        after_second_writes = writes["n"]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks.update(saved_rows)
        MarketCache._shared_industry_stocks_ts.clear()
        MarketCache._shared_industry_stocks_ts.update(saved_ts)
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    assert first == _codes() and held is first
    assert second == first and second is not first
    assert kinds == [(NAME, "industry"), (NAME, "industry")]
    assert after_first_fetches == 1 and after_hold_fetches == 1 and after_second_fetches == 2
    assert after_first_writes == 1 and after_hold_writes == 1 and after_second_writes == 2
    assert files == []
    assert elapsed["max"] < 5
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_DISK_SAVE_FAIL rows={N} first_fetches={after_first_fetches} "
        f"memory_hits_after_fail=1 "
        f"memory_fetches={after_hold_fetches - after_first_fetches} "
        f"after_clear_fetches={after_second_fetches - after_hold_fetches} "
        f"write_errors={writes['n']} files={len(files)} "
        f"max_call_seconds={elapsed['max']:.4f} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_DISK_SAVE_FAIL" in captured.out
    sys.stderr.write(captured.out)
