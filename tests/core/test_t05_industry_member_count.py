"""T05 read-only in-memory industry-member cache hit count.

The board fetch is a local stand-in. Pytest already disables the disk
cache, so this does not read or write ~/.muyun/market_cache.
Production code is not changed.
"""
import sys
import threading
import tracemalloc

from src.scanner.market_cache import MarketCache

N = 30
REPEATS = 10


def _codes():
    return [f"{600000 + i:06d}" for i in range(N)]


def test_t05_industry_member_memory_hits(capsys):
    saved_rows = dict(MarketCache._shared_industry_stocks)
    saved_ts = dict(MarketCache._shared_industry_stocks_ts)
    fetches = {"n": 0}

    def board(self, name, kind):
        fetches["n"] += 1
        return _codes()

    MarketCache._shared_industry_stocks.clear()
    MarketCache._shared_industry_stocks_ts.clear()
    original = MarketCache._get_ths_board_stocks_by_name
    MarketCache._get_ths_board_stocks_by_name = board
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        first = cache.get_stocks_by_industry("semi")
        first_fetches = fetches["n"]
        hits = 0
        for _ in range(REPEATS):
            again = cache.get_stocks_by_industry("semi")
            assert again is first
            hits += 1
        other = cache.get_stocks_by_industry("bank")
        repeat_fetches = fetches["n"] - first_fetches
        again = cache.get_stocks_by_industry("semi")
        assert again is first
        hits += 1
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        MarketCache._get_ths_board_stocks_by_name = original
        MarketCache._shared_industry_stocks.clear()
        MarketCache._shared_industry_stocks_ts.clear()
        MarketCache._shared_industry_stocks.update(saved_rows)
        MarketCache._shared_industry_stocks_ts.update(saved_ts)
    threads_after = threading.active_count()
    assert first == _codes() and other == _codes()
    assert first_fetches == 1
    assert repeat_fetches == 1
    assert hits == REPEATS + 1
    assert fetches["n"] == 2
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_MEMBER_HITS names=2 members={N} "
        f"fetches={fetches['n']} repeat_hits={hits} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_MEMBER_HITS" in captured.out
    sys.stderr.write(captured.out)
