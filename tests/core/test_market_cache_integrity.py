"""Public cache queries must reject invalid clocks and preserve committed files."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import src.scanner.market_cache as module
from src.scanner.market_cache import MarketCache

NOW = datetime(2026, 10, 16, 10).timestamp()
BAD = [NOW + 1, NOW + 1800, True, "1700000000", float("nan"),
       float("inf"), -1, 0, None, 10**1000]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    for name, value in vars(MarketCache).copy().items():
        if name.startswith("_shared_"):
            monkeypatch.setattr(MarketCache, name, {} if isinstance(value, dict)
                                else None if name.endswith("_df") else 0)
    monkeypatch.setattr(module, "_DISK_CACHE_DIR", tmp_path)
    monkeypatch.setattr(module, "_disk_enabled", lambda: True)
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(MarketCache, "is_trading_hours", lambda self: True)
    return MarketCache()


@pytest.mark.parametrize("kind", ["industry", "concept"])
@pytest.mark.parametrize("storage", ["memory", "disk", "empty_bad_disk", "expired_memory_bad_disk"])
@pytest.mark.parametrize("timestamp", BAD)
@pytest.mark.parametrize("source_fails", [False, True])
def test_constituent_clock_queries(cache, monkeypatch, kind, storage, timestamp, source_fails):
    name, old, fresh = "integrity", ["600001"], ["600002"]
    rows = getattr(MarketCache, f"_shared_{kind}_stocks")
    clocks = getattr(MarketCache, f"_shared_{kind}_stocks_ts")
    if storage == "memory":
        rows[name], clocks[name] = old, timestamp
    else:
        pd.to_pickle({"ts": timestamp, "data": [] if storage == "empty_bad_disk" else old},
                     module._DISK_CACHE_DIR / f"{kind}_stocks_{name}.pkl")
        if storage == "expired_memory_bad_disk":
            rows[name], clocks[name] = ["600003"], NOW - 1800
    calls = []
    def fetch(self, requested, requested_kind):
        calls.append((requested, requested_kind))
        if source_fails:
            raise OSError("offline fixture")
        return fresh
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", fetch)
    result = getattr(cache, f"get_stocks_by_{kind}")(name)
    expected = (["600003"] if storage == "expired_memory_bad_disk" else []) if source_fails else fresh
    assert result == expected
    assert calls == [(name, kind)]


@pytest.mark.parametrize("kind", ["industry", "concept"])
@pytest.mark.parametrize("storage", ["memory", "disk"])
@pytest.mark.parametrize("age,refresh", [(1799, False), (1800, True), (1801, True)])
def test_constituent_ttl_boundary(cache, monkeypatch, kind, storage, age, refresh):
    name, old, fresh = "boundary", ["600001"], ["600002"]
    if storage == "memory":
        getattr(MarketCache, f"_shared_{kind}_stocks")[name] = old
        getattr(MarketCache, f"_shared_{kind}_stocks_ts")[name] = NOW - age
    else:
        assert module._disk_save(f"{kind}_stocks_{name}", old)
        pd.to_pickle({"ts": NOW-age, "data": old}, module._DISK_CACHE_DIR / f"{kind}_stocks_{name}.pkl")
    calls = []
    monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name",
                        lambda *args: calls.append(1) or fresh)
    assert getattr(cache, f"get_stocks_by_{kind}")(name) == (fresh if refresh else old)
    assert len(calls) == int(refresh)


@pytest.mark.parametrize("timestamp", BAD)
@pytest.mark.parametrize("kind", ["stock", "etf", "industry", "concept"])
def test_frame_invalid_clock_cannot_fallback(cache, monkeypatch, timestamp, kind):
    old = pd.DataFrame({"代码": ["600001"]})
    monkeypatch.setattr(MarketCache, f"_shared_{kind}_df", old)
    monkeypatch.setattr(MarketCache, f"_shared_{kind}_ts", timestamp)
    def fail(*args, **kwargs):
        raise OSError("offline fixture")
    if kind == "stock":
        monkeypatch.setattr(MarketCache, "_fetch_sina_market", lambda self: None)
        monkeypatch.setattr(MarketCache, "_fetch_efinance_market", lambda self: None)
        monkeypatch.setattr("time.sleep", lambda *args: None)
        result = cache.get_all_stocks()
    else:
        import akshare as ak
        method = {"etf": "fund_etf_spot_em", "industry": "stock_board_industry_name_ths",
                  "concept": "stock_board_concept_name_ths"}[kind]
        monkeypatch.setattr(ak, method, fail)
        result = getattr(cache, "get_all_etfs" if kind == "etf" else f"get_{kind}_boards")()
    assert result.empty


@pytest.mark.parametrize("kind", ["stock", "etf"])
@pytest.mark.parametrize("timestamp", BAD)
def test_status_invalid_clock(cache, monkeypatch, kind, timestamp):
    monkeypatch.setattr(MarketCache, f"_shared_{kind}_df", pd.DataFrame({"代码": ["600001"]}))
    monkeypatch.setattr(MarketCache, f"_shared_{kind}_ts", timestamp)
    status = cache.get_cache_status()["stocks" if kind == "stock" else "etfs"]
    assert status["expired"] is True
    assert status["age_seconds"] is None


@pytest.mark.parametrize("kind", ["stocks_df", "industry_stocks_integrity", "concept_stocks_integrity"])
@pytest.mark.parametrize("failure", ["before", "partial", "replace"])
def test_atomic_save_preserves_old_file(cache, monkeypatch, kind, failure):
    assert module._disk_save(kind, ["old"])
    path = module._DISK_CACHE_DIR / f"{kind}.pkl"
    before = path.read_bytes()
    if failure in ("before", "partial"):
        def partial(obj, target, *args, **kwargs):
            target = Path(target)
            assert target.parent == path.parent and target != path
            if failure == "partial":
                target.write_bytes(b"partial pickle")
            raise OSError("partial write")
        monkeypatch.setattr(module.pd, "to_pickle", partial)
    else:
        def failed_replace(*args):
            raise OSError("replace blocked")
        monkeypatch.setattr(module.os, "replace", failed_replace)
    assert module._disk_save(kind, ["new"]) is False
    assert path.read_bytes() == before
    assert module._disk_load(kind)["data"] == ["old"]
    assert list(module._DISK_CACHE_DIR.iterdir()) == [path]


@pytest.mark.parametrize("kind", ["stock", "industry", "concept"])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["before", "partial"])
def test_successful_query_survives_partial_write(cache, monkeypatch, kind, existing, failure):
    key = "stocks_df" if kind == "stock" else f"{kind}_stocks_integrity"
    path = module._DISK_CACHE_DIR / f"{key}.pkl"
    if existing:
        assert module._disk_save(key, ["old"])
    before = path.read_bytes() if existing else None
    def partial(obj, target, *args, **kwargs):
        if failure == "partial":
            Path(target).write_bytes(b"partial pickle")
        raise OSError("partial write")
    monkeypatch.setattr(module.pd, "to_pickle", partial)
    fresh = pd.DataFrame({"代码": ["600002"]}) if kind == "stock" else ["600002"]
    if kind == "stock":
        monkeypatch.setattr(MarketCache, "_fetch_sina_market", lambda self: fresh)
        result = cache.get_all_stocks(force_refresh=True)
        held = cache.get_all_stocks()
    else:
        # Force the old disk entry past its normal TTL.
        monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: NOW + 1800))
        monkeypatch.setattr(MarketCache, "_get_ths_board_stocks_by_name", lambda *args: fresh)
        result = getattr(cache, f"get_stocks_by_{kind}")("integrity")
        held = getattr(cache, f"get_stocks_by_{kind}")("integrity")
    assert result is fresh and held is fresh
    assert (path.read_bytes() if path.exists() else None) == before
    assert list(module._DISK_CACHE_DIR.iterdir()) == ([path] if existing else [])


@pytest.mark.parametrize("timestamp", BAD)
def test_bad_cooldown_cannot_suppress_recovery(cache, monkeypatch, timestamp):
    monkeypatch.setattr(MarketCache, "_shared_fail_ts", timestamp)
    fresh = pd.DataFrame({"代码": ["600002"]})
    calls = []
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", lambda self: calls.append(1) or fresh)
    assert cache.get_all_stocks() is fresh
    assert calls == [1]


@pytest.mark.parametrize("timestamp", BAD)
def test_bad_clock_off_hours_does_not_crash(cache, monkeypatch, timestamp):
    monkeypatch.setattr(MarketCache, "is_trading_hours", lambda self: False)
    assert cache._is_expired(timestamp) is True


@pytest.mark.parametrize("timestamp", BAD)
@pytest.mark.parametrize("source_fails", [False, True])
def test_stock_disk_clock_cannot_hit_or_fallback(cache, monkeypatch, timestamp, source_fails):
    old = pd.DataFrame({"代码": ["600001"]})
    fresh = pd.DataFrame({"代码": ["600002"]})
    pd.to_pickle({"ts": timestamp, "data": old}, module._DISK_CACHE_DIR / "stocks_df.pkl")
    calls = []
    monkeypatch.setattr(MarketCache, "_fetch_sina_market",
                        lambda self: calls.append(1) or (None if source_fails else fresh))
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", lambda self: None)
    monkeypatch.setattr("time.sleep", lambda *args: None)
    result = cache.get_all_stocks()
    assert result.empty if source_fails else result is fresh
    assert len(calls) == (3 if source_fails else 1)


def test_normal_expiry_has_no_clock_warning(cache, caplog):
    assert cache._is_expired(NOW - 301) is True
    assert "缓存时间异常" not in caplog.text
