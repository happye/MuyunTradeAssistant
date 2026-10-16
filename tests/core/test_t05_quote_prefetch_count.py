"""T05 read-only batch quote prefetch hit count.

The batch source is a local stand-in. This does not count live HTTP
and does not change production semantics.
"""
import sys
import threading
import tracemalloc

from src.data.akshare_client import AKShareClient

N = 30


def test_t05_quote_prefetch_hit_count(capsys):
    codes = [f"{600000 + i:06d}" for i in range(N)]
    fetches = {"n": 0}
    original = AKShareClient._fetch_sina_batch
    saved = dict(AKShareClient._QUOTE_PREFETCH)
    AKShareClient._QUOTE_PREFETCH.clear()

    def fake_batch(cls, batch):
        fetches["n"] += 1
        return {
            code: {"stock_code": code, "price": 10.0, "source": "stand_in"}
            for code in batch
        }

    AKShareClient._fetch_sina_batch = classmethod(fake_batch)
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        filled = AKShareClient.prefetch_realtime_quotes(codes + [codes[0] + ".SZ"])
        prefetch_fetches = fetches["n"]
        hits = 0
        for code in codes:
            plain = AKShareClient.get_realtime_quote(code)
            aliased = AKShareClient.get_realtime_quote(code + ".SZ")
            assert plain is not None and plain["price"] == 10.0
            assert aliased is plain
            hits += 2
        lookup_fetches = fetches["n"] - prefetch_fetches
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        AKShareClient._fetch_sina_batch = original
        AKShareClient._QUOTE_PREFETCH.clear()
        AKShareClient._QUOTE_PREFETCH.update(saved)
    threads_after = threading.active_count()
    assert filled == N
    assert prefetch_fetches == 1
    assert lookup_fetches == 0
    assert hits == N * 2
    assert threads_after <= threads_before + 1
    print(
        f"T05_PREFETCH_HITS codes={N} prefetch_fetches={prefetch_fetches} "
        f"filled={filled} lookup_hits={hits} lookup_fetches={lookup_fetches} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_PREFETCH_HITS" in captured.out
    sys.stderr.write(captured.out)
