"""T05 read-only same-day kline disk-cache hit count.

The fetch is a local stand-in and the cache directory is the test tmp path.
Production code is not changed, and the real ~/.muyun cache is not used.
"""
import sys
import threading
import tracemalloc
from datetime import datetime

import pandas as pd

import src.data.akshare_client as akshare_client
from src.data.akshare_client import AKShareClient

N = 30
START = "20260101"
END = "20261016"


class _Morning(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 16, 10, 0, 0)


def test_t05_kline_disk_cache_hit_count(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "kline_cache"

    def cache_dir_fn(cls):
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    calls = {"n": 0}

    def uncached(cls, stock_code, period="daily", adjust="qfq",
                 start_date=None, end_date=None, retry=3):
        calls["n"] += 1
        return pd.DataFrame({"date": ["2026-10-15"], "close": [10.0]})

    monkeypatch.setattr(akshare_client, "datetime", _Morning)
    monkeypatch.setattr(AKShareClient, "_kline_cache_dir", classmethod(cache_dir_fn))
    monkeypatch.setattr(
        AKShareClient, "_get_historical_kline_uncached", classmethod(uncached)
    )
    codes = [f"{600000 + i:06d}" for i in range(N)]
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        for code in codes:
            first = AKShareClient.get_historical_kline(
                code, start_date=START, end_date=END
            )
            assert first is not None and len(first) == 1
        first_fetches = calls["n"]
        for code in codes:
            second = AKShareClient.get_historical_kline(
                code, start_date=START, end_date=END
            )
            assert second is not None and float(second["close"].iloc[-1]) == 10.0
        second_fetches = calls["n"] - first_fetches
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    files = list(cache_dir.glob("*.json"))
    threads_after = threading.active_count()
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert len(files) == N
    assert first_fetches == N
    assert second_fetches == 0
    assert threads_after <= threads_before + 1
    print(
        f"T05_KLINE_DISK_HITS codes={N} first_fetches={first_fetches} "
        f"second_fetches={second_fetches} disk_hits={N - second_fetches} "
        f"files={len(files)} peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_KLINE_DISK_HITS" in captured.out
    sys.stderr.write(captured.out)
