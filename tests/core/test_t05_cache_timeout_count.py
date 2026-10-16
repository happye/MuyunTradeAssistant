"""T05 read-only cache and timeout counts.

Counts existing calculate_indicators TTL hits and _call_with_timeout failures.
The uncached fetch is a local stand-in, so this is not a live HTTP count.
Production semantics stay unchanged.
"""
import sys
import threading
import time
import tracemalloc

from src.data.akshare_client import AKShareClient, _call_with_timeout
from src.data.models import StockData

N = 30


def _full(code):
    return StockData(
        stock_code=code,
        stock_name="test",
        price=10.0,
        volume=1000,
        ma5=10.0,
        weekly={"date": ["2026-09-26"], "close": [10.0]},
        monthly={"date": ["2026-09"], "close": [10.0]},
    )


def _slow():
    time.sleep(0.25)
    return "late"


def test_t05_cache_hits_and_timeout_counts(capsys):
    codes = [f"{600000 + i:06d}" for i in range(N)]
    calls = {"n": 0}

    def uncached(cls, code, require_historical=False):
        calls["n"] += 1
        return _full(code)

    original = AKShareClient._calculate_indicators_uncached
    AKShareClient._stock_data_cache.clear()
    AKShareClient._calculate_indicators_uncached = classmethod(uncached)
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        for code in codes:
            AKShareClient.calculate_indicators(code)
        first_uncached = calls["n"]
        for code in codes:
            again = AKShareClient.calculate_indicators(code)
            assert again is not None and again.ma5 == 10.0
        second_uncached = calls["n"] - first_uncached
        _, peak = tracemalloc.get_traced_memory()
        timeouts = 0
        try:
            _call_with_timeout(_slow, timeout=0.05)
        except TimeoutError:
            timeouts += 1
        threads_during = threading.active_count()
        recovered = _call_with_timeout(lambda: "ok", timeout=0.05)
        retry_none = AKShareClient._retry_with_backoff(
            _slow, max_retries=1, base_delay=0, timeout=0.05
        )
        retry_ok = AKShareClient._retry_with_backoff(
            lambda: "ok", max_retries=1, base_delay=0, timeout=0.05
        )
    finally:
        tracemalloc.stop()
        AKShareClient._calculate_indicators_uncached = original
        AKShareClient._stock_data_cache.clear()
    time.sleep(0.35)
    threads_after = threading.active_count()
    assert first_uncached == N
    assert second_uncached == 0
    assert timeouts == 1
    assert recovered == "ok"
    assert retry_none is None
    assert retry_ok == "ok"
    print(
        f"T05_CACHE_TIMEOUT_COUNT codes={N} first_uncached={first_uncached} "
        f"second_uncached={second_uncached} cache_hits={N - second_uncached} "
        f"timeout_errors={timeouts} retry_timeout_none={1 if retry_none is None else 0} "
        f"retry_recovery_ok={1 if retry_ok == 'ok' else 0} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    assert threads_after <= threads_before + 1
    captured = capsys.readouterr()
    assert "T05_CACHE_TIMEOUT_COUNT" in captured.out
    sys.stderr.write(captured.out)
