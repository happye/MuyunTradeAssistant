"""T05 read-only industry-member 30-minute TTL miss count.

The board fetch is a local stand-in. Disk cache is pointed at the test
tmp path and enabled only for this count. The clock is fixed: age 1799s
still hits, age 1800s misses both memory and disk. Production code is
not changed, and the real ~/.muyun/market_cache is not used.
"""
import sys
import threading
import tracemalloc

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05ttl"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _codes():
    return [f"{600000 + i:06d}" for i in range(N)]


def test_t05_industry_member_ttl_window(tmp_path, monkeypatch, capsys):
    assert MarketCache.INDUSTRY_STOCKS_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = dict(MarketCache._shared_industry_stocks)
    saved_ts = dict(MarketCache._shared_industry_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    kinds = []

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        return _codes()

    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    original = MarketCache._get_ths_board_stocks_by_name
    MarketCache._get_ths_board_stocks_by_name = board
    MarketCache._shared_industry_stocks.clear()
    MarketCache._shared_industry_stocks_ts.clear()
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = cache.get_stocks_by_industry(NAME)
        fresh_fetches = fetches["n"]
        clock.now = BASE + TTL - 1
        memory_hit = cache.get_stocks_by_industry(NAME)
        after_memory = fetches["n"]
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks_ts.clear()
        disk_hit = cache.get_stocks_by_industry(NAME)
        after_disk = fetches["n"]
        clock.now = BASE + TTL
        expired = cache.get_stocks_by_industry(NAME)
        after_expired = fetches["n"]
        clock.now = BASE + TTL + (TTL - 1)
        renewed = cache.get_stocks_by_industry(NAME)
        after_renewed = fetches["n"]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        MarketCache._get_ths_board_stocks_by_name = original
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks.update(saved_rows)
        MarketCache._shared_industry_stocks_ts.clear()
        MarketCache._shared_industry_stocks_ts.update(saved_ts)
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    fresh_memory_hits = 1 if after_memory == fresh_fetches else 0
    fresh_disk_hits = 1 if after_disk == fresh_fetches else 0
    expired_fetches = after_expired - fresh_fetches
    renewed_memory_hits = 1 if after_renewed == after_expired else 0
    assert first == _codes()
    assert memory_hit is first
    assert disk_hit == first and disk_hit is not first
    assert expired == _codes()
    assert renewed is expired
    assert kinds == [(NAME, "industry"), (NAME, "industry")]
    assert fresh_fetches == 1
    assert fresh_memory_hits == 1
    assert fresh_disk_hits == 1
    assert expired_fetches == 1
    assert renewed_memory_hits == 1
    assert len(files) == 1
    assert files[0].name == "industry_stocks_t05ttl.pkl"
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_TTL_WINDOW rows={N} ttl={TTL} "
        f"fresh_fetches={fresh_fetches} fresh_memory_hits={fresh_memory_hits} "
        f"fresh_disk_hits={fresh_disk_hits} expired_fetches={expired_fetches} "
        f"renewed_memory_hits={renewed_memory_hits} files={len(files)} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_TTL_WINDOW" in captured.out
    sys.stderr.write(captured.out)
