"""T05 read-only same-day kline 17:30 miss-window count.

The fetch is a local stand-in and the cache directory is the test tmp path.
A fixed clock walks 10:00, 17:30, then 18:00. Production code is not changed.
"""
import sys
import threading
import tracemalloc
from datetime import datetime

import pandas as pd

import src.data.akshare_client as akshare_client
from src.data.akshare_client import AKShareClient

CODE = "600519"
START = "20260101"
END = "20261016"


class _Clock(datetime):
    stamp = datetime(2026, 10, 16, 10, 0, 0)

    @classmethod
    def now(cls, tz=None):
        value = cls.stamp
        return cls(
            value.year, value.month, value.day,
            value.hour, value.minute, value.second,
        )


def _set(hour, minute):
    _Clock.stamp = datetime(2026, 10, 16, hour, minute, 0)


def test_t05_kline_miss_window_count(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "kline_cache"

    def cache_dir_fn(cls):
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    calls = {"n": 0}

    def uncached(cls, stock_code, period="daily", adjust="qfq",
                 start_date=None, end_date=None, retry=3):
        calls["n"] += 1
        return pd.DataFrame({"date": ["2026-10-15"], "close": [10.0]})

    monkeypatch.setattr(akshare_client, "datetime", _Clock)
    monkeypatch.setattr(AKShareClient, "_kline_cache_dir", classmethod(cache_dir_fn))
    monkeypatch.setattr(
        AKShareClient, "_get_historical_kline_uncached", classmethod(uncached)
    )
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        _set(10, 0)
        morning = AKShareClient.get_historical_kline(
            CODE, start_date=START, end_date=END
        )
        morning_again = AKShareClient.get_historical_kline(
            CODE, start_date=START, end_date=END
        )
        morning_fetches = calls["n"]
        _set(17, 30)
        window_a = AKShareClient.get_historical_kline(
            CODE, start_date=START, end_date=END
        )
        window_b = AKShareClient.get_historical_kline(
            CODE, start_date=START, end_date=END
        )
        window_fetches = calls["n"] - morning_fetches
        _set(18, 0)
        evening = AKShareClient.get_historical_kline(
            CODE, start_date=START, end_date=END
        )
        evening_again = AKShareClient.get_historical_kline(
            CODE, start_date=START, end_date=END
        )
        evening_fetches = calls["n"] - morning_fetches - window_fetches
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    files = list(cache_dir.glob("*.json"))
    threads_after = threading.active_count()
    for frame in (morning, morning_again, window_a, window_b, evening, evening_again):
        assert frame is not None and len(frame) == 1
    assert morning_fetches == 1
    assert window_fetches == 2
    assert evening_fetches == 1
    assert len(files) == 1
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)
    assert threads_after <= threads_before + 1
    print(
        f"T05_KLINE_MISS_WINDOW morning_fetches={morning_fetches} "
        f"morning_hits=1 window_fetches={window_fetches} window_hits=0 "
        f"evening_fetches={evening_fetches} evening_hits=1 files={len(files)} "
        f"peak_bytes={peak} threads_before={threads_before} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_KLINE_MISS_WINDOW" in captured.out
    sys.stderr.write(captured.out)
