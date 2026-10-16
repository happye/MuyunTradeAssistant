"""T05 read-only in-memory concept-board list cache hit count.

The board list is a local stand-in. get_concept_boards keeps only the
class-level frame and does not read or write ~/.muyun/market_cache.
This is not the concept-member count. Production code is not changed.
"""
import sys
import threading
import tracemalloc
import types

import pandas as pd

from src.scanner.market_cache import MarketCache

N = 30
REPEATS = 10


def _frame():
    return pd.DataFrame({
        "name": [f"concept{i:02d}" for i in range(N)],
        "code": [f"{885000 + i:06d}" for i in range(N)],
    })


def test_t05_concept_board_memory_hits(capsys):
    saved = (
        MarketCache._shared_concept_df,
        MarketCache._shared_concept_ts,
    )
    saved_module = sys.modules.get("akshare")
    fetches = {"ths": 0, "em": 0}
    stub = types.ModuleType("akshare")

    def stock_board_concept_name_ths():
        fetches["ths"] += 1
        return _frame()

    def stock_board_concept_name_em():
        fetches["em"] += 1
        raise AssertionError("concept board list path must not call em")

    stub.stock_board_concept_name_ths = stock_board_concept_name_ths
    stub.stock_board_concept_name_em = stock_board_concept_name_em
    sys.modules["akshare"] = stub
    MarketCache._shared_concept_df = None
    MarketCache._shared_concept_ts = 0.0
    threads_before = threading.active_count()
    tracemalloc.start()
    cached_rows = None
    try:
        cache = MarketCache()
        first = cache.get_concept_boards()
        first_fetches = fetches["ths"]
        hits = 0
        for _ in range(REPEATS):
            again = cache.get_concept_boards()
            assert again is first
            hits += 1
        repeat_fetches = fetches["ths"] - first_fetches
        refreshed = cache.get_concept_boards(force_refresh=True)
        force_fetches = fetches["ths"] - first_fetches - repeat_fetches
        cached_rows = 0 if MarketCache._shared_concept_df is None else len(MarketCache._shared_concept_df)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        if saved_module is None:
            sys.modules.pop("akshare", None)
        else:
            sys.modules["akshare"] = saved_module
        (
            MarketCache._shared_concept_df,
            MarketCache._shared_concept_ts,
        ) = saved
    threads_after = threading.active_count()
    assert list(first["板块名称"]) == [f"concept{i:02d}" for i in range(N)]
    assert "name" not in first.columns
    assert len(refreshed) == N and "板块名称" in refreshed.columns
    assert first_fetches == 1
    assert repeat_fetches == 0
    assert hits == REPEATS
    assert force_fetches == 1
    assert fetches["em"] == 0
    assert cached_rows == N
    assert threads_after <= threads_before + 1
    print(
        f"T05_CONCEPT_BOARD_HITS rows={N} first_fetches={first_fetches} "
        f"repeat_hits={hits} repeat_fetches={repeat_fetches} "
        f"force_refresh_fetches={force_fetches} source=ths em_calls={fetches['em']} "
        f"peak_bytes={peak} threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_CONCEPT_BOARD_HITS" in captured.out
    sys.stderr.write(captured.out)
