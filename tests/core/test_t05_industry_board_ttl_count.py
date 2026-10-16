"""T05 read-only industry-board list 10-minute TTL miss count.

The board list is a local stand-in. get_industry_boards keeps only the
class-level frame. Disk cache is enabled and pointed at the test tmp path
to show this path writes no file. Age 599s still hits, age 600s misses.
Production code is not changed, and the real ~/.muyun/market_cache is not used.
"""
import sys
import threading
import tracemalloc
import types

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
BASE = 1_700_000_000.0
TTL = 600


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _frame():
    return pd.DataFrame({
        "name": [f"board{i:02d}" for i in range(N)],
        "code": [f"{880000 + i:06d}" for i in range(N)],
    })


def test_t05_industry_board_ttl_window(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_industry_df,
        MarketCache._shared_industry_ts,
    )
    saved_module = sys.modules.get("akshare")
    clock = _Clock()
    fetches = {"ths": 0, "em": 0}
    stub = types.ModuleType("akshare")

    def stock_board_industry_name_ths():
        fetches["ths"] += 1
        return _frame()

    def stock_board_industry_name_em():
        fetches["em"] += 1
        raise AssertionError("industry board list path must not call em")

    stub.stock_board_industry_name_ths = stock_board_industry_name_ths
    stub.stock_board_industry_name_em = stock_board_industry_name_em
    sys.modules["akshare"] = stub
    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    MarketCache._shared_industry_df = None
    MarketCache._shared_industry_ts = 0.0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = cache.get_industry_boards()
        fresh_fetches = fetches["ths"]
        clock.now = BASE + TTL - 1
        memory_hit = cache.get_industry_boards()
        after_fresh = fetches["ths"]
        clock.now = BASE + TTL
        expired = cache.get_industry_boards()
        after_expired = fetches["ths"]
        clock.now = BASE + TTL + (TTL - 1)
        renewed = cache.get_industry_boards()
        after_renewed = fetches["ths"]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        if saved_module is None:
            sys.modules.pop("akshare", None)
        else:
            sys.modules["akshare"] = saved_module
        (
            MarketCache._shared_industry_df,
            MarketCache._shared_industry_ts,
        ) = saved
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    fresh_hits = 1 if after_fresh == fresh_fetches else 0
    expired_fetches = after_expired - fresh_fetches
    renewed_hits = 1 if after_renewed == after_expired else 0
    assert list(first["板块名称"]) == [f"board{i:02d}" for i in range(N)]
    assert "name" not in first.columns
    assert memory_hit is first
    assert list(expired["板块名称"]) == list(first["板块名称"])
    assert expired is not first
    assert renewed is expired
    assert fresh_fetches == 1
    assert fresh_hits == 1
    assert expired_fetches == 1
    assert renewed_hits == 1
    assert fetches["em"] == 0
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_BOARD_TTL rows={N} ttl={TTL} "
        f"fresh_fetches={fresh_fetches} fresh_hits={fresh_hits} "
        f"expired_fetches={expired_fetches} renewed_hits={renewed_hits} "
        f"source=ths em_calls={fetches['em']} disk_files={len(files)} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_BOARD_TTL" in captured.out
    sys.stderr.write(captured.out)
