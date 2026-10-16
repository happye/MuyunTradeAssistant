"""T05 read-only count: a missing market-snapshot disk file loads as None.

Memory has no snapshot. stocks_df.pkl is not created up front. Real
_disk_load reaches Path.exists, sees False, and returns None there. It
does not call read_pickle and does not raise, so the except branch is
not the source of None. None is not a hit. The Sina stub then returns
30 priced rows. The quality gate accepts them, memory stores that frame,
and the save creates one new dict with ts equal to the clock. The backup
source is not called and the 1.5s retry gap is not waited. ETF, industry,
and concept memory are left alone. Production code is not changed, and
the real ~/.muyun/market_cache is not used. This is not the corrupt
market-disk count.
"""
import os
import sys
import threading
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
BASE = 1_700_000_000.0


class _Clock:
    now = BASE

    def time(self):
        return self.now


class _Wall(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 16, 10, 0, 0)


def _good():
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [10.0] * N,
    })


def test_t05_market_missing_disk_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
    assert MarketCache.DEFAULT_TRADING_TTL == 300
    assert MarketCache.FAIL_COOLDOWN_SECONDS == 60
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    saved_etf = (
        MarketCache._shared_etf_df,
        MarketCache._shared_etf_ts,
        MarketCache._shared_etf_count,
    )
    saved_industry = dict(MarketCache._shared_industry_stocks)
    saved_industry_ts = dict(MarketCache._shared_industry_stocks_ts)
    saved_concept = dict(MarketCache._shared_concept_stocks)
    saved_concept_ts = dict(MarketCache._shared_concept_stocks_ts)
    clock = _Clock()
    fetches = {"sina": 0, "backup": 0}
    slept = {"n": 0}
    disk_loads = {"n": 0, "keys": [], "value": "unset", "exists": None, "reads": None, "debugs": None}
    disk_saves = {"n": 0, "ok": None}
    exists_seen = {"calls": []}
    pickle_reads = {"n": 0}
    debugs = []
    fresh = _good()
    real_load = market_cache._disk_load
    real_save = market_cache._disk_save
    real_exists = Path.exists
    real_read = pd.read_pickle
    real_debug = market_cache.logger.debug
    real_sleep = time.sleep
    path = cache_dir / "stocks_df.pkl"
    target_name = path.name
    target_parent = os.path.normcase(str(path.parent))

    def sina(self):
        fetches["sina"] += 1
        return fresh

    def backup(self):
        fetches["backup"] += 1
        raise AssertionError("missing market disk must not use the backup source")

    def no_sleep(seconds):
        slept["n"] += 1
        return real_sleep(0)

    def counting_exists(self):
        result = real_exists(self)
        parent = os.path.normcase(str(getattr(self, "parent", "")))
        if getattr(self, "name", None) == target_name and parent == target_parent:
            exists_seen["calls"].append(bool(result))
        return result

    def counting_read(target, *args, **kwargs):
        pickle_reads["n"] += 1
        return real_read(target, *args, **kwargs)

    def counting_debug(msg, *args, **kwargs):
        text = msg % args if args and isinstance(msg, str) else str(msg)
        debugs.append(text)
        return real_debug(msg, *args, **kwargs)

    def counting_load(key):
        disk_loads["n"] += 1
        disk_loads["keys"].append(key)
        start = len(exists_seen["calls"])
        read_start = pickle_reads["n"]
        debug_start = len(debugs)
        value = real_load(key)
        disk_loads["value"] = value
        disk_loads["exists"] = exists_seen["calls"][start:]
        disk_loads["reads"] = pickle_reads["n"] - read_start
        disk_loads["debugs"] = debugs[debug_start:]
        return value

    def counting_save(key, data):
        disk_saves["n"] += 1
        disk_saves["ok"] = real_save(key, data)
        disk_saves["same"] = data is fresh
        return disk_saves["ok"]

    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(market_cache, "_disk_load", counting_load)
    monkeypatch.setattr(market_cache, "_disk_save", counting_save)
    monkeypatch.setattr(market_cache.pd, "read_pickle", counting_read)
    monkeypatch.setattr(market_cache.logger, "debug", counting_debug)
    monkeypatch.setattr(Path, "exists", counting_exists)
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    monkeypatch.setattr(time, "sleep", no_sleep)
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0
    assert not real_exists(path)
    assert clock.now - MarketCache._shared_fail_ts >= MarketCache.FAIL_COOLDOWN_SECONDS
    probe_start = len(exists_seen["calls"])
    probe_reads = pickle_reads["n"]
    probe_debugs = len(debugs)
    probe_value = real_load("stocks_df")
    probe_exists = exists_seen["calls"][probe_start:]
    probe_read_n = pickle_reads["n"] - probe_reads
    probe_debug_text = debugs[probe_debugs:]
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        trading = cache.is_trading_hours()
        memory_absent = MarketCache._shared_stock_df is None
        still_absent = not real_exists(path)
        started = time.perf_counter()
        returned = cache.get_all_stocks()
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        created = real_exists(path)
        etf_same = (
            MarketCache._shared_etf_df is saved_etf[0]
            and MarketCache._shared_etf_ts == saved_etf[1]
            and MarketCache._shared_etf_count == saved_etf[2]
        )
        boards_same = (
            MarketCache._shared_industry_stocks == saved_industry
            and MarketCache._shared_industry_stocks_ts == saved_industry_ts
            and MarketCache._shared_concept_stocks == saved_concept
            and MarketCache._shared_concept_stocks_ts == saved_concept_ts
        )
        priced = int((returned["最新价"] > 0).sum()) if returned is fresh else 0
        _, peak = tracemalloc.get_traced_memory()
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
    stored = real_read(path)
    loaded = disk_loads["value"]
    failure_logs = [text for text in (probe_debug_text + (disk_loads["debugs"] or [])) if "磁盘缓存读取失败" in text]
    assert trading is True and memory_absent and still_absent
    assert probe_value is None and probe_exists == [False] and probe_read_n == 0
    assert not failure_logs
    assert len(fresh) == N and priced == N
    assert loaded is None
    assert disk_loads["exists"] == [False] and disk_loads["reads"] == 0
    assert returned is fresh
    assert created is True
    assert isinstance(stored, dict) and set(stored) == {"ts", "data"}
    assert stored["ts"] == clock.now == BASE
    assert isinstance(stored["data"], pd.DataFrame) and stored["data"].equals(fresh)
    assert disk_loads["n"] == 1 and disk_loads["keys"] == ["stocks_df"]
    assert disk_saves["n"] == 1 and disk_saves["ok"] is True and disk_saves["same"] is True
    assert fetches["sina"] == 1 and fetches["backup"] == 0 and slept["n"] == 0
    assert etf_same and boards_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during <= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_MISSING_DISK rows={N} memory_absent=1 "
        f"file_existed_before=0 exists_returned_false=1 read_pickle_on_miss=0 "
        f"direct_load_is_none=1 direct_load_raised=0 disk_loads=1 "
        f"disk_load_is_none=1 disk_hit=0 trading_hours=1 "
        f"sina=1 backup=0 sleeps=0 disk_saves=1 save_ok=1 "
        f"quality_positive_prices={priced} timestamp_is_clock=1 "
        f"disk_created=1 files=1 etf_industry_concept_unchanged=1 "
        f"call_seconds={elapsed:.4f} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_MISSING_DISK" in captured.out
    sys.stderr.write(captured.out)
    assert cache is not None
