"""T05 read-only concept-member disk-L2 hit count.

The board fetch is a local stand-in. Disk cache is pointed at the test
tmp path and enabled only for this count. Production code is not changed,
and the real ~/.muyun/market_cache is not used. This is not the earlier
in-memory concept-member count or the industry disk count.
"""
import sys
import threading
import tracemalloc

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05theme"


def _codes():
    return [f"{300000 + i:06d}" for i in range(N)]


def test_t05_concept_member_disk_l2_hits(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_concept_stocks)
    saved_ts = dict(MarketCache._shared_concept_stocks_ts)
    fetches = {"n": 0}
    kinds = []

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        return _codes()

    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    original = MarketCache._get_ths_board_stocks_by_name
    MarketCache._get_ths_board_stocks_by_name = board
    MarketCache._shared_concept_stocks.clear()
    MarketCache._shared_concept_stocks_ts.clear()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = cache.get_stocks_by_concept(NAME)
        first_fetches = fetches["n"]
        MarketCache._shared_concept_stocks.clear()
        MarketCache._shared_concept_stocks_ts.clear()
        second = cache.get_stocks_by_concept(NAME)
        disk_fetches = fetches["n"] - first_fetches
        third = cache.get_stocks_by_concept(NAME)
        memory_fetches = fetches["n"] - first_fetches - disk_fetches
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        MarketCache._get_ths_board_stocks_by_name = original
        MarketCache._shared_concept_stocks.clear()
        MarketCache._shared_concept_stocks.update(saved_rows)
        MarketCache._shared_concept_stocks_ts.clear()
        MarketCache._shared_concept_stocks_ts.update(saved_ts)
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    assert first == _codes()
    assert second == first
    assert third is second
    assert kinds == [(NAME, "concept")]
    assert first_fetches == 1
    assert disk_fetches == 0
    assert memory_fetches == 0
    assert len(files) == 1
    assert files[0].name == "concept_stocks_t05theme.pkl"
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_CONCEPT_DISK_L2 rows={N} first_fetches={first_fetches} "
        f"disk_hits=1 disk_fetches={disk_fetches} memory_hits_after_disk=1 "
        f"memory_fetches={memory_fetches} files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_DISK_L2" in captured.out
    sys.stderr.write(captured.out)
