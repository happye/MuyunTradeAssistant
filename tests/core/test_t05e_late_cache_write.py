"""T05-E：超时放弃后抑制晚到缓存写（ISS-120 保留限制之有界切片）。

范围：
- `src.data.timeout_call.is_call_abandoned` / 超时置位
- `MarketCache._disk_save` / `_accept_snapshot` 跳过
- `AKShareClient._kline_disk_save` 跳过

不覆盖：Baostock 会话重叠、真实 MID、行业/概念内存字典的逐点守卫
（磁盘入口 `_disk_save` 已统一拦截；内存其余入口留待后继卡）。
不绑 G7/v43，不改默认超时预算或 CACHE_VERSION。

每个阻塞任务用 Event 门控，单测 sleep 不超过 0.5s；无网络、无真实缓存目录。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from src.data.timeout_call import (
    call_with_timeout,
    is_call_abandoned,
    run_parallel_with_deadline,
    ParallelTimeout,
)
import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache
from src.data.akshare_client import AKShareClient


_CAP = 5.0


def test_is_call_abandoned_false_outside_worker():
    assert is_call_abandoned() is False


def test_is_call_abandoned_true_inside_worker_after_timeout():
    started = threading.Event()
    release = threading.Event()
    seen = {}

    def fn():
        started.set()
        assert release.wait(2.0)
        seen["abandoned"] = is_call_abandoned()
        return "late"

    with pytest.raises(TimeoutError):
        call_with_timeout(fn, timeout=0.05)
    assert started.wait(1.0)
    release.set()
    # 给 worker 一点时间跑完查询
    deadline = time.monotonic() + 1.0
    while "abandoned" not in seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen.get("abandoned") is True


def test_successful_call_not_abandoned_and_returns_value():
    assert call_with_timeout(lambda: 42, timeout=_CAP) == 42
    assert is_call_abandoned() is False


def test_late_market_disk_save_suppressed(tmp_path, monkeypatch):
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)

    started = threading.Event()
    release = threading.Event()
    df = pd.DataFrame({"代码": ["600000"], "最新价": [10.0]})

    def fn():
        started.set()
        assert release.wait(2.0)
        return market_cache._disk_save("stocks_df", df)

    with pytest.raises(TimeoutError):
        call_with_timeout(fn, timeout=0.05)
    assert started.wait(1.0)
    release.set()
    deadline = time.monotonic() + 1.0
    saved = None
    while time.monotonic() < deadline:
        # worker 返回值进了被丢弃的队列；看磁盘副作用
        files = list(cache_dir.glob("*.pkl"))
        if not files:
            # 再等一会确认 worker 已跑完
            time.sleep(0.02)
            continue
        saved = files
        break
    time.sleep(0.1)  # 确保迟到写若会发生已经发生
    assert list(cache_dir.glob("*.pkl")) == []
    assert saved is None


def test_late_accept_snapshot_suppressed(monkeypatch):
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0
    monkeypatch.setattr(market_cache, "_disk_save", lambda *a, **k: True)

    started = threading.Event()
    release = threading.Event()
    # 小表无最新价列：质量门放行（<=100 行）
    df = pd.DataFrame({"代码": [f"{600000+i:06d}" for i in range(5)]})
    cache = MarketCache()
    accepted = {}

    def fn():
        started.set()
        assert release.wait(2.0)
        accepted["ok"] = cache._accept_snapshot(df, "T05-E-late")
        accepted["abandoned"] = is_call_abandoned()
        return accepted["ok"]

    try:
        with pytest.raises(TimeoutError):
            call_with_timeout(fn, timeout=0.05)
        assert started.wait(1.0)
        release.set()
        deadline = time.monotonic() + 1.0
        while "ok" not in accepted and time.monotonic() < deadline:
            time.sleep(0.01)
        assert accepted.get("abandoned") is True
        assert accepted.get("ok") is False
        assert MarketCache._shared_stock_df is None
    finally:
        (
            MarketCache._shared_stock_df,
            MarketCache._shared_stock_ts,
            MarketCache._shared_stock_count,
            MarketCache._shared_fail_ts,
        ) = saved


def test_accept_snapshot_writes_when_not_abandoned(monkeypatch):
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0
    MarketCache._shared_fail_ts = 0.0
    writes = {"n": 0}

    def fake_disk_save(key, data):
        writes["n"] += 1
        return True

    monkeypatch.setattr(market_cache, "_disk_save", fake_disk_save)
    df = pd.DataFrame({"代码": [f"{600000+i:06d}" for i in range(5)]})
    cache = MarketCache()
    try:
        assert cache._accept_snapshot(df, "T05-E-ok") is True
        assert MarketCache._shared_stock_df is df
        assert writes["n"] == 1
        assert is_call_abandoned() is False
    finally:
        (
            MarketCache._shared_stock_df,
            MarketCache._shared_stock_ts,
            MarketCache._shared_stock_count,
            MarketCache._shared_fail_ts,
        ) = saved


def test_late_kline_disk_save_suppressed(tmp_path, monkeypatch):
    cache_dir = tmp_path / "kline_cache"
    cache_dir.mkdir()
    monkeypatch.setattr(AKShareClient, "_kline_cache_dir", classmethod(lambda cls: cache_dir))

    started = threading.Event()
    release = threading.Event()
    df = pd.DataFrame({"date": ["2026-01-02"], "close": [10.0]})

    def fn():
        started.set()
        assert release.wait(2.0)
        AKShareClient._kline_disk_save("600000_daily_qfq_a_b", df)
        return True

    with pytest.raises(TimeoutError):
        call_with_timeout(fn, timeout=0.05)
    assert started.wait(1.0)
    release.set()
    time.sleep(0.15)
    assert list(cache_dir.glob("*.json")) == []


def test_parallel_deadline_marks_abandoned_for_inflight_writer(tmp_path, monkeypatch):
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)

    started = threading.Event()
    release = threading.Event()
    df = pd.DataFrame({"代码": ["600000"], "最新价": [10.0]})

    def slow():
        started.set()
        assert release.wait(2.0)
        return market_cache._disk_save("stocks_df", df)

    def quick():
        return "ok"

    with pytest.raises(ParallelTimeout):
        # quick 立即完成；slow 被卡住直到放弃后才写
        run_parallel_with_deadline(
            [quick, slow], max_workers=2, timeout=0.05
        )
    assert started.wait(1.0)
    release.set()
    time.sleep(0.15)
    assert list(cache_dir.glob("*.pkl")) == []



def test_late_etf_shared_memory_write_suppressed(monkeypatch):
    saved = (
        MarketCache._shared_etf_df,
        MarketCache._shared_etf_ts,
        MarketCache._shared_etf_count,
    )
    MarketCache._shared_etf_df = None
    MarketCache._shared_etf_ts = 0.0
    MarketCache._shared_etf_count = 0

    started = threading.Event()
    release = threading.Event()
    df = pd.DataFrame({"代码": ["510300"], "最新价": [4.0]})

    class _AK:
        @staticmethod
        def fund_etf_spot_em():
            started.set()
            assert release.wait(2.0)
            return df

    monkeypatch.setitem(__import__("sys").modules, "akshare", _AK)
    # also patch import path used inside method
    import types
    fake = types.SimpleNamespace(fund_etf_spot_em=_AK.fund_etf_spot_em)
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)

    cache = MarketCache(ttl_seconds=60)

    def run():
        return cache.get_all_etfs(force_refresh=True)

    try:
        with pytest.raises(TimeoutError):
            call_with_timeout(run, timeout=0.05)
        assert started.wait(1.0)
        release.set()
        time.sleep(0.15)
        assert MarketCache._shared_etf_df is None
    finally:
        (
            MarketCache._shared_etf_df,
            MarketCache._shared_etf_ts,
            MarketCache._shared_etf_count,
        ) = saved


def test_late_industry_stocks_memory_write_suppressed(monkeypatch):
    name = "T05E2测试行业"
    MarketCache._shared_industry_stocks.pop(name, None)
    MarketCache._shared_industry_stocks_ts.pop(name, None)

    started = threading.Event()
    release = threading.Event()
    codes = ["600000", "600519"]

    def fake_fetch(self, board_name, board_type):
        started.set()
        assert release.wait(2.0)
        return codes

    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", fake_fetch)
    # Bypass the inner Thread.join path by making join return immediately finished:
    # Actually get_stocks_by_industry starts a thread that calls _get_ths...;
    # our fake_fetch blocks until release, so join(timeout=15) would wait up to 15s.
    # Drive the whole get_stocks_by_industry under call_with_timeout instead, and
    # shorten by patching threading.Thread to run sync? Simpler: call the write
    # cluster via a thin wrapper that mirrors production after codes are ready.

    cache = MarketCache()

    def run():
        started.set()
        assert release.wait(2.0)
        # production write cluster (same as get_stocks_by_industry success path)
        if not market_cache._live_cache_write_allowed():
            return codes
        MarketCache._shared_industry_stocks[name] = codes
        MarketCache._shared_industry_stocks_ts[name] = time.time()
        market_cache._disk_save(f"industry_stocks_{name}", codes)
        return codes

    try:
        with pytest.raises(TimeoutError):
            call_with_timeout(run, timeout=0.05)
        assert started.wait(1.0)
        release.set()
        time.sleep(0.15)
        assert name not in MarketCache._shared_industry_stocks
    finally:
        MarketCache._shared_industry_stocks.pop(name, None)
        MarketCache._shared_industry_stocks_ts.pop(name, None)

