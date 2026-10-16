"""T05 read-only count: industry disk cache stays off under pytest.

_disk_enabled is the real function and is not replaced. PYTEST_CURRENT_TEST
is set and MUYUN_DISABLE_DISK_CACHE is not 1, so the switch returns False
before Path.exists. A valid fresh disk dict is already on disk and would
have been a hit. The load returns None, does not call exists or
read_pickle, and does not raise. None is not a hit. The worker then
returns 30 different codes. join uses the production 15s argument and
returns at once. Memory stores those codes at the clock. The real save
returns False and leaves the original bytes unchanged. The fallback is
not called. Concept memory is left alone. Production code is not changed,
and the real ~/.muyun/market_cache is not used. This is not the missing
file count.
"""
import os
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

N = 30
NAME = "t05inoff"
BASE = 1_700_000_000.0
TTL = 1800


class _Clock:
    now = BASE

    def time(self):
        return self.now


class _Spy(dict):
    def __init__(self):
        super().__init__()
        self.writes = []

    def __setitem__(self, key, value):
        self.writes.append((key, value))
        super().__setitem__(key, value)


def _codes(start):
    return [f"{start + i:06d}" for i in range(N)]


def test_t05_industry_disk_off_count(tmp_path, monkeypatch, capsys):
    assert MarketCache.INDUSTRY_STOCKS_TTL == TTL
    enabled = market_cache._disk_enabled
    assert enabled.__name__ == "_disk_enabled"
    assert enabled.__module__ == "src.scanner.market_cache"
    assert "PYTEST_CURRENT_TEST" in os.environ
    assert os.environ.get("MUYUN_DISABLE_DISK_CACHE", "") != "1"
    assert enabled() is False
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved_rows = MarketCache._shared_industry_stocks
    saved_ts = MarketCache._shared_industry_stocks_ts
    saved_concept = dict(MarketCache._shared_concept_stocks)
    saved_concept_ts = dict(MarketCache._shared_concept_stocks_ts)
    clock = _Clock()
    fetches = {"n": 0}
    disk_loads = {"n": 0, "keys": [], "value": "unset", "exists": None, "reads": None, "debugs": None}
    disk_saves = {"n": 0, "ok": None, "data": None}
    fallback_calls = {"n": 0}
    kinds = []
    joins = {"n": 0, "timeout": None, "alive": None}
    exists_seen = {"calls": []}
    pickle_reads = {"n": 0}
    debugs = []
    old = _codes(760000)
    fresh = _codes(761000)
    rows = _Spy()
    ts = _Spy()
    real_join = threading.Thread.join
    real_load = market_cache._disk_load
    real_save = market_cache._disk_save
    real_exists = Path.exists
    real_read = pd.read_pickle
    real_debug = market_cache.logger.debug
    path = cache_dir / "industry_stocks_t05inoff.pkl"
    pd.to_pickle({"ts": BASE, "data": old}, path)
    old_bytes = path.read_bytes()
    stored_before = real_read(path)
    target_name = path.name
    target_parent = os.path.normcase(str(path.parent))

    def board(self, name, kind):
        fetches["n"] += 1
        kinds.append((name, kind))
        return fresh

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
        disk_saves["data"] = data
        disk_saves["ok"] = real_save(key, data)
        return disk_saves["ok"]

    def counting_fallback(self, kind, name):
        fallback_calls["n"] += 1
        raise AssertionError("disabled industry disk must not use fallback")

    def recording_join(self, timeout=None):
        joins["n"] += 1
        joins["timeout"] = timeout
        assert timeout == 15
        real_join(self, timeout)
        joins["alive"] = self.is_alive()
        assert joins["alive"] is False
        return None

    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_load", counting_load)
    monkeypatch.setattr(market_cache, "_disk_save", counting_save)
    monkeypatch.setattr(market_cache.pd, "read_pickle", counting_read)
    monkeypatch.setattr(market_cache.logger, "debug", counting_debug)
    monkeypatch.setattr(Path, "exists", counting_exists)
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", board)
    monkeypatch.setattr(MarketCache, "_board_stocks_fallback", counting_fallback)
    assert market_cache._disk_enabled is enabled
    assert enabled() is False
    MarketCache._shared_industry_stocks = rows
    MarketCache._shared_industry_stocks_ts = ts
    assert real_exists(path)
    assert stored_before["ts"] == BASE and stored_before["data"] == old
    assert 0 <= clock.now - stored_before["ts"] < TTL
    probe_start = len(exists_seen["calls"])
    probe_reads = pickle_reads["n"]
    probe_debugs = len(debugs)
    probe_value = real_load(f"industry_stocks_{NAME}")
    probe_exists = exists_seen["calls"][probe_start:]
    probe_read_n = pickle_reads["n"] - probe_reads
    probe_debug_text = debugs[probe_debugs:]
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        memory_absent = NAME not in rows and NAME not in ts
        threading.Thread.join = recording_join
        started = time.perf_counter()
        returned = cache.get_stocks_by_industry(NAME)
        elapsed = time.perf_counter() - started
        threads_during = threading.active_count()
        concept_same = (
            MarketCache._shared_concept_stocks == saved_concept
            and MarketCache._shared_concept_stocks_ts == saved_concept_ts
        )
        kept = path.read_bytes()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        threading.Thread.join = real_join
        tracemalloc.stop()
        MarketCache._shared_industry_stocks = saved_rows
        MarketCache._shared_industry_stocks_ts = saved_ts
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    stored = real_read(path)
    loaded = disk_loads["value"]
    failure_logs = [text for text in (probe_debug_text + (disk_loads["debugs"] or [])) if "磁盘缓存读取失败" in text]
    assert market_cache._disk_enabled is enabled
    assert memory_absent
    assert probe_value is None and probe_exists == [] and probe_read_n == 0
    assert not failure_logs
    assert old != fresh and len(fresh) == N and len(old) == N
    assert loaded is None
    assert disk_loads["exists"] == [] and disk_loads["reads"] == 0
    assert returned is fresh
    assert rows[NAME] is fresh
    assert ts[NAME] == clock.now == BASE
    assert rows.writes == [(NAME, fresh)]
    assert ts.writes == [(NAME, BASE)]
    assert kept == old_bytes
    assert stored["ts"] == BASE and stored["data"] == old
    assert disk_loads["n"] == 1 and disk_loads["keys"] == [f"industry_stocks_{NAME}"]
    assert disk_saves["n"] == 1 and disk_saves["ok"] is False and disk_saves["data"] is fresh
    assert fetches["n"] == 1
    assert fallback_calls["n"] == 0
    assert kinds == [(NAME, "industry")]
    assert joins["n"] == 1 and joins["timeout"] == 15 and joins["alive"] is False
    assert concept_same
    assert elapsed < 5
    assert len(files) == 1 and files[0].name == path.name
    assert all(item.resolve().is_relative_to(tmp_path.resolve()) for item in files)
    assert threads_during <= threads_before + 1
    assert threads_after <= threads_before + 1
    print(
        f"T05_INDUSTRY_DISK_OFF rows={N} memory_key_absent=1 "
        f"file_existed_before=1 file_would_be_fresh_hit=1 "
        f"disk_enabled=0 pytest_current_test=1 disable_env=0 "
        f"exists_calls=0 read_pickle_on_load=0 "
        f"direct_load_is_none=1 direct_load_raised=0 disk_loads=1 "
        f"disk_load_is_none=1 disk_hit=0 kind=industry "
        f"success_joins=1 join_timeout_arg=15 worker_alive_after_join=0 "
        f"fallback_calls=0 fetches=1 disk_saves=1 save_ok=0 "
        f"disk_bytes_unchanged=1 memory_writes=1 timestamp_writes=1 "
        f"timestamp_is_clock=1 files=1 concept_memory_unchanged=1 "
        f"call_seconds={elapsed:.4f} peak_bytes={peak} "
        f"threads_before={threads_before} threads_during={threads_during} "
        f"threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_INDUSTRY_DISK_OFF" in captured.out
    sys.stderr.write(captured.out)
    assert cache is not None
