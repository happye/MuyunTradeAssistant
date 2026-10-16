"""T05 read-only count for a corrupt market-snapshot disk cache.

A stocks_df.pkl that is not a dict carrying ts, whose data is not a
DataFrame, or whose read raises, is a miss after memory is cleared.
Each case fetches Sina once, overwrites the file, and does not call
the backup. The exception does not escape. A DataFrame stored without
ts is not treated as a hit. The 1.5s gap is not waited. Production
code is not changed, and the real ~/.muyun/market_cache is not used.
"""
import sys
import threading
import time
import tracemalloc
from datetime import datetime

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30


class _Wall(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 16, 10, 0, 0)


def _good(price):
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [price] * N,
    })


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0


def test_t05_market_disk_corrupt_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    fetches = {"sina": 0, "backup": 0}
    slept = {"n": 0}
    made = []
    path = cache_dir / "stocks_df.pkl"

    def sina(self):
        fetches["sina"] += 1
        frame = _good(10.0 + fetches["sina"])
        made.append(frame)
        return frame

    def backup(self):
        fetches["backup"] += 1
        return _good(99.0)

    def no_sleep(seconds):
        slept["n"] += 1
        assert seconds == 1.5

    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    monkeypatch.setattr(time, "sleep", no_sleep)
    _clear_memory()
    threads_before = threading.active_count()
    tracemalloc.start()
    trap = _good(99.0)
    cases = (
        ("not_dict", ["not", "a", "dict"]),
        ("no_ts", {"data": trap}),
        ("data_not_frame", {"ts": time.time(), "data": "nope"}),
        ("read_error", b"not-a-pickle"),
    )
    try:
        cache = MarketCache()
        assert cache.is_trading_hours()
        for name, payload in cases:
            if isinstance(payload, (bytes, bytearray)):
                path.write_bytes(payload)
            else:
                pd.to_pickle(payload, path)
            _clear_memory()
            before = fetches["sina"]
            got = cache.get_all_stocks()
            assert fetches["sina"] == before + 1, name
            assert fetches["backup"] == 0, name
            assert got is made[-1], name
            assert int((got["最新价"] == 99.0).sum()) == 0
            stored = pd.read_pickle(path)
            assert isinstance(stored, dict) and "ts" in stored, name
            assert isinstance(stored["data"], pd.DataFrame), name
            assert int((stored["data"]["最新价"] == got["最新价"].iloc[0]).sum()) == N
            _clear_memory()
            hit = cache.get_all_stocks()
            assert fetches["sina"] == before + 1, name
            assert list(hit["最新价"]) == list(got["最新价"])
            assert hit is not trap
        _, peak = tracemalloc.get_traced_memory()
        assert MarketCache._shared_fail_ts == 0.0
    finally:
        tracemalloc.stop()
        (
            MarketCache._shared_stock_df,
            MarketCache._shared_stock_ts,
            MarketCache._shared_stock_count,
            MarketCache._shared_fail_ts,
        ) = saved
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    assert fetches["sina"] == len(cases)
    assert fetches["backup"] == 0
    assert slept["n"] == 0
    assert len(files) == 1 and files[0].name == "stocks_df.pkl"
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_DISK_CORRUPT rows={N} cases={len(cases)} "
        f"sina={fetches['sina']} backup={fetches['backup']} sleeps={slept['n']} "
        f"disk_hits_after_overwrite={len(cases)} files={len(files)} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_DISK_CORRUPT" in captured.out
    sys.stderr.write(captured.out)
