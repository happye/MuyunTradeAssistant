"""T05 read-only count: market snapshot disk cache stays off under pytest.

_disk_enabled is the real function and is not replaced. PYTEST_CURRENT_TEST
is set and MUYUN_DISABLE_DISK_CACHE is not 1, so the switch returns False
before Path.exists. A valid fresh stocks_df is already on disk. Its frame
passes the quality gate and its ts is inside the 300s trading TTL, so it
would have been a hit. The load returns None, does not call exists or
read_pickle, and does not raise. None is not a hit. The Sina stub then
returns 30 different priced rows. The quality gate accepts them and
memory stores that frame at the clock. The real save returns False and
leaves the original bytes unchanged. The backup source is not called and
the 1.5s retry gap is not waited. ETF, industry, and concept memory are
left alone. Production code is not changed, and the real
~/.muyun/market_cache is not used. This is not the missing-file count.
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
WALL = datetime(2026, 10, 16, 10, 0, 0)
BASE = WALL.timestamp()


class _Clock:
    now = BASE

    def time(self):
        return self.now


class _Wall(datetime):
    @classmethod
    def now(cls, tz=None):
        return WALL


def _frame(price):
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(N)],
        "最新价": [price] * N,
    })


def test_t05_market_disk_off_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
    assert MarketCache.DEFAULT_TRADING_TTL == 300
    assert MarketCache.FAIL_COOLDOWN_SECONDS == 60
    enabled = market_cache._disk_enabled
    assert enabled.__name__ == "_disk_enabled"
    assert enabled.__module__ == "src.scanner.market_cache"
    assert "PYTEST_CURRENT_TEST" in os.environ
    assert os.environ.get("MUYUN_DISABLE_DISK_CACHE", "") != "1"
    assert enabled() is False
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
    disk_saves = {"n": 0, "ok": None, "same": None}
    exists_seen = {"calls": []}
    pickle_reads = {"n": 0}
    debugs = []
    old = _frame(8.0)
    fresh = _frame(10.0)
    real_load = market_cache._disk_load
    real_save = market_cache._disk_save
    real_exists = Path.exists
    real_read = pd.read_pickle
    real_debug = market_cache.logger.debug
    real_sleep = time.sleep
    path = cache_dir / "stocks_df.pkl"
    pd.to_pickle({"ts": BASE, "data": old}, path)
    old_bytes = path.read_bytes()
    stored_before = real_read(path)
    target_name = path.name
    target_parent = os.path.normcase(str(path.parent))

    def sina(self):
        fetches["sina"] += 1
        return fresh

    def backup(self):
        fetches["backup"] += 1
        raise AssertionError("disabled market disk must not use the backup source")

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
        disk_loads["exists"] = list(exists_seen["calls"][start:])
        disk_loads["reads"] = pickle_reads["n"] - read_start
        disk_loads["debugs"] = debugs[debug_start:]
        return value

    def counting_save(key, data):
        disk_saves["n"] += 1
        disk_saves["same"] = data is fresh
        disk_saves["ok"] = real_save(key, data)
        return disk_saves["ok"]

    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_load", counting_load)
    monkeypatch.setattr(market_cache, "_disk_save", counting_save)
    monkeypatch.setattr(market_cache.pd, "read_pickle", counting_read)
    monkeypatch.setattr(market_cache.logger, "debug", counting_debug)
    monkeypatch.setattr(Path, "exists", counting_exists)
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    monkeypatch.setattr(time, "sleep", no_sleep)
    assert market_cache._disk_enabled is enabled
    assert enabled() is False
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0
    assert real_exists(path)
    assert isinstance(stored_before["data"], pd.DataFrame)
    assert stored_before["ts"] == BASE and stored_before["data"].equals(old)
    assert bool(MarketCache._snapshot_quality_ok(stored_before["data"])) is True
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
        would_be_fresh = trading and cache._ttl == 300 and cache._is_expired(BASE) is False
        memory_absent = MarketCache._shared_stock_df is None
        started = time.perf_counter()
        returned = cache.get_all_stocks()
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        kept = path.read_bytes()
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
        memory_ts = MarketCache._shared_stock_ts
        memory_count = MarketCache._shared_stock_count
        fail_ts = MarketCache._shared_fail_ts
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
    assert market_cache._disk_enabled is enabled
    assert trading is True and would_be_fresh is True and memory_absent
    assert probe_value is None and probe_exists == [] and probe_read_n == 0
    assert not failure_logs
    assert not old.equals(fresh) and len(fresh) == N and priced == N
    assert loaded is None
    assert disk_loads["exists"] == [] and disk_loads["reads"] == 0
    assert returned is fresh
    assert memory_ts == clock.now == BASE and memory_count == N and fail_ts == 0.0
    assert kept == old_bytes
    assert stored["ts"] == BASE and stored["data"].equals(old)
    assert disk_loads["n"] == 1 and disk_loads["keys"] == ["stocks_df"]
    assert disk_saves["n"] == 1 and disk_saves["ok"] is False and disk_saves["same"] is True
    assert fetches["sina"] == 1 and fetches["backup"] == 0 and slept["n"] == 0
    assert etf_same and boards_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during <= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_DISK_OFF rows={N} memory_absent=1 "
        f"file_existed_before=1 file_would_be_fresh_hit=1 "
        f"disk_enabled=0 pytest_current_test=1 disable_env=0 "
        f"exists_calls=0 read_pickle_on_load=0 "
        f"direct_load_is_none=1 direct_load_raised=0 disk_loads=1 "
        f"disk_load_is_none=1 disk_hit=0 trading_hours=1 "
        f"sina=1 backup=0 sleeps=0 disk_saves=1 save_ok=0 "
        f"quality_positive_prices={priced} timestamp_is_clock=1 "
        f"disk_bytes_unchanged=1 files=1 etf_industry_concept_unchanged=1 "
        f"call_seconds={elapsed:.4f} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_DISK_OFF" in captured.out
    sys.stderr.write(captured.out)
    assert cache is not None
