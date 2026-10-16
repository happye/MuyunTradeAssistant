"""T05 read-only ETF fetch-failure count.

The source is a local stand-in. The wall clock stays at Friday 10:00 so
expiry is the intraday TTL. An exception returns the previous memory
object and does not move its timestamp. An empty frame or None is not
stored and is not replaced by that old object. With no cache, both
failures return an empty frame. Repeating the failure calls the source
again: this path has no 60-second cooldown. ETF writes no disk file.
Production code is not changed.
"""
import sys
import threading
import tracemalloc
import types
from datetime import datetime

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
BASE = 1_700_000_000.0
TTL = 300


class _Wall(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 16, 10, 0, 0)


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _frame():
    return pd.DataFrame({
        "代码": [f"{510000 + i:06d}" for i in range(N)],
        "最新价": [1.0] * N,
    })


def _clear_memory():
    MarketCache._shared_etf_df = None
    MarketCache._shared_etf_ts = 0.0
    MarketCache._shared_etf_count = 0


def test_t05_etf_fetch_failure_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
    assert MarketCache.DEFAULT_TRADING_TTL == TTL
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_etf_df,
        MarketCache._shared_etf_ts,
        MarketCache._shared_etf_count,
        MarketCache._shared_fail_ts,
    )
    saved_module = sys.modules.get("akshare")
    clock = _Clock()
    fetches = {"n": 0}
    mode = {"value": "boom"}
    stub = types.ModuleType("akshare")

    def fund_etf_spot_em():
        fetches["n"] += 1
        value = mode["value"]
        if value == "ok":
            return _frame()
        if value == "empty":
            return pd.DataFrame()
        if value == "none":
            return None
        raise RuntimeError("etf source down")

    stub.fund_etf_spot_em = fund_etf_spot_em
    sys.modules["akshare"] = stub
    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    _clear_memory()
    MarketCache._shared_fail_ts = 0.0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        assert cache.is_trading_hours()
        mode["value"] = "boom"
        bare_exc = cache.get_all_etfs()
        after_bare_exc = fetches["n"]
        mode["value"] = "empty"
        bare_empty = cache.get_all_etfs()
        after_bare_empty = fetches["n"]
        mode["value"] = "none"
        bare_none = cache.get_all_etfs()
        after_bare_none = fetches["n"]
        assert MarketCache._shared_etf_df is None
        assert MarketCache._shared_etf_ts == 0.0
        mode["value"] = "boom"
        repeated = [cache.get_all_etfs() for _ in range(3)]
        after_repeat = fetches["n"]

        mode["value"] = "ok"
        first = cache.get_all_etfs()
        after_ok = fetches["n"]
        stored_ts = MarketCache._shared_etf_ts
        assert int(stored_ts) == int(BASE)
        mode["value"] = "empty"
        forced_empty = cache.get_all_etfs(force_refresh=True)
        after_forced_empty = fetches["n"]
        mode["value"] = "none"
        forced_none = cache.get_all_etfs(force_refresh=True)
        after_forced_none = fetches["n"]
        assert MarketCache._shared_etf_df is first
        assert MarketCache._shared_etf_ts == stored_ts
        clock.now = BASE + TTL + 1
        assert cache._is_expired(stored_ts) is True
        mode["value"] = "boom"
        expired_exc = cache.get_all_etfs()
        after_expired_exc = fetches["n"]
        assert MarketCache._shared_etf_ts == stored_ts
        clock.now = stored_ts + TTL
        assert cache._is_expired(stored_ts) is False
        hit = cache.get_all_etfs()
        after_hit = fetches["n"]
        fail_ts_after = MarketCache._shared_fail_ts
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        if saved_module is None:
            sys.modules.pop("akshare", None)
        else:
            sys.modules["akshare"] = saved_module
        (
            MarketCache._shared_etf_df,
            MarketCache._shared_etf_ts,
            MarketCache._shared_etf_count,
            MarketCache._shared_fail_ts,
        ) = saved
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    bare_exc_fetches = after_bare_exc
    bare_empty_fetches = after_bare_empty - after_bare_exc
    bare_none_fetches = after_bare_none - after_bare_empty
    repeat_fetches = after_repeat - after_bare_none
    ok_fetches = after_ok - after_repeat
    forced_empty_fetches = after_forced_empty - after_ok
    forced_none_fetches = after_forced_none - after_forced_empty
    expired_exc_fetches = after_expired_exc - after_forced_none
    fresh_hits = 1 if after_hit == after_expired_exc else 0
    assert bare_exc.empty and bare_empty.empty and bare_none.empty
    assert all(item.empty for item in repeated)
    assert list(first["代码"]) == [f"{510000 + i:06d}" for i in range(N)]
    assert forced_empty.empty and forced_empty is not first
    assert forced_none.empty and forced_none is not first
    assert expired_exc is first
    assert hit is first
    assert bare_exc_fetches == 1
    assert bare_empty_fetches == 1 and bare_none_fetches == 1
    assert repeat_fetches == 3
    assert ok_fetches == 1
    assert forced_empty_fetches == 1 and forced_none_fetches == 1
    assert expired_exc_fetches == 1
    assert fresh_hits == 1
    assert fail_ts_after == 0.0
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_ETF_FETCH_FAIL rows={N} bare_exc_fetches={bare_exc_fetches} "
        f"bare_empty_fetches={bare_empty_fetches} bare_none_fetches={bare_none_fetches} "
        f"repeat_exc_fetches={repeat_fetches} ok_fetches={ok_fetches} "
        f"forced_empty_fetches={forced_empty_fetches} "
        f"forced_none_fetches={forced_none_fetches} "
        f"expired_exc_fetches={expired_exc_fetches} fresh_hits_after_restore={fresh_hits} "
        f"fail_ts_unchanged=1 disk_files={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_ETF_FETCH_FAIL" in captured.out
    sys.stderr.write(captured.out)
