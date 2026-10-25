"""T05-D remote-integration boundaries: robust cache, K-line budget/parsing, theme scope.

Offline, finite tests only. All cache dirs are temporary; no real network, Baostock
session, account, or production cache is touched.
"""
import math
import pickle
import sys
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from src.data import board_members_alt as bma
from src.data import robust_fetch as rf
from src.scanner.scanner_engine import ScannerEngine


# ---------------- robust_fetch cache contract ----------------

def _write_record(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(payload))


def test_robust_load_rejects_bad_shape_and_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "cache"))
    path = rf._path("t", "shape")
    now = time.time()
    bad = [
        ["not", "a", "dict"],
        {"value": 1},
        {"ts": now, "value": 1, "extra": True},
    ]
    for payload in bad[:2]:
        _write_record(path, payload)
        assert rf._load("t", "shape") is None, payload
    for ts in (True, False, "123", float("inf"), float("nan"), 0, -1, now + 60):
        _write_record(path, {"ts": ts, "value": 1})
        assert rf._load("t", "shape") is None, ts
    _write_record(path, {"ts": now, "value": 1})
    assert rf._load("t", "shape")["value"] == 1
    _write_record(path, {"ts": now, "value": 1, "extra": True})
    assert rf._load("t", "shape")["value"] == 1


def test_robust_bad_cache_is_miss_and_refetches(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "cache"))
    path = rf._path("t", "bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a pickle")
    value = rf.robust_call("t", "bad", lambda: {"v": 2}, ttl=3600, retries=0)
    assert value == {"v": 2}
    assert rf.LAST_STATUS[("t", "bad")] == "live"


def test_robust_future_bool_and_string_cache_do_not_revive(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "cache"))
    now = time.time()
    for ts in (now + 60, True, "123"):
        path = rf._path("t", "gate")
        _write_record(path, {"ts": ts, "value": {"v": "cached"}})
        value = rf.robust_call("t", "gate", lambda: {"v": "live"}, ttl=3600, retries=0)
        assert value == {"v": "live"}, ts
        assert rf.LAST_STATUS[("t", "gate")] == "live"


def test_robust_ttl_zero_uses_valid_stale_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "cache"))
    path = rf._path("t", "ttl0")
    now = time.time()
    _write_record(path, {"ts": now - 10, "value": {"v": "old"}})
    value = rf.robust_call("t", "ttl0", lambda: 1 / 0, ttl=0, stale_max=3600, retries=0)
    assert value == {"v": "old"}
    assert rf.LAST_STATUS[("t", "ttl0")] == "stale_cache"


def test_robust_save_partial_failure_keeps_old_bytes_and_own_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "cache"))
    rf._save("t", "atomic", {"v": "old"})
    path = rf._path("t", "atomic")
    old_bytes = path.read_bytes()

    def broken_dump(obj, handle, protocol=None):
        handle.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(rf.pickle, "dump", broken_dump)
    rf._save("t", "atomic", {"v": "new"})
    assert path.read_bytes() == old_bytes
    leftovers = [item for item in path.parent.iterdir() if item.suffix == ".tmp"]
    assert leftovers == []


def test_robust_concurrent_writers_keep_valid_file_and_no_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "cache"))
    barrier = threading.Barrier(3)
    errors = []

    def writer(value):
        try:
            barrier.wait(timeout=2)
            rf._save("t", "race", {"v": value})
        except BaseException as error:  # noqa: BLE001 - thread must not die silently
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(value,)) for value in ("a", "b")]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)
    assert not errors
    loaded = rf._load("t", "race")
    assert loaded is not None and loaded.get("value") in ({"v": "a"}, {"v": "b"})
    leftovers = [item for item in rf._path("t", "race").parent.iterdir() if item.suffix == ".tmp"]
    assert leftovers == []


# ---------------- K-line parsing, window and budget ----------------

def _rows(days=61, close=10.0, volume=100.0, start=None):
    start = start or datetime(2026, 1, 1)
    return [((start + timedelta(days=i)).strftime("%Y-%m-%d"), close, volume) for i in range(days)]


def _metrics(rows, **kwargs):
    return ScannerEngine.kline_metrics_from_rows(rows, **kwargs)


def test_kline_metrics_real_dates_and_formula():
    rows = _rows(60) + [((datetime(2026, 1, 1) + timedelta(days=60)).strftime("%Y-%m-%d"), 12.0, 50.0)]
    result = _metrics(rows)
    assert result["量比"] == 0.5
    assert result["60日涨跌幅"] == 20.0
    assert _metrics(rows[:5]) == {"量比": None, "60日涨跌幅": None}


def test_kline_metrics_sorts_and_drops_future_observation():
    rows = _rows(61)
    near_future = [("2026-03-03", 999.0, 1.0)]
    as_of = rows[-1][0]
    expected = _metrics(rows)
    assert _metrics(list(reversed(rows)) + near_future, as_of=as_of) == expected
    assert _metrics(rows + near_future) != expected  # default today includes a near-past date
    assert _metrics(rows + [("2099-01-01", 999.0, 1.0)]) == expected  # default excludes true future


def test_kline_metrics_strict_dates_and_window():
    non_padded_day = _rows(60) + [("2026-10-9", 20.0, 100.0)]
    assert _metrics(non_padded_day)["60日涨跌幅"] is None
    old = _rows(61, start=datetime(2025, 11, 1))
    assert _metrics(old, as_of="2026-1-1") == {"量比": None, "60日涨跌幅": None}
    assert _metrics(_rows(61), as_of="2026-13-01") == {"量比": None, "60日涨跌幅": None}
    assert _metrics(_rows(61), start_date="2026-1-1") == {"量比": None, "60日涨跌幅": None}
    assert _metrics(_rows(61), start_date="2026-03-01", as_of="2026-02-01") == {
        "量比": None, "60日涨跌幅": None}
    window = _rows(5, start=datetime(2026, 2, 1))
    before = _rows(56, start=datetime(2025, 12, 1))
    assert _metrics(before + window, as_of="2026-02-05",
                    start_date="2026-02-01") == {"量比": None, "60日涨跌幅": None}


def test_kline_metrics_duplicate_days_do_not_fill_periods_and_conflict_rejects():
    rows = _rows(60)
    duplicate_same = rows + [rows[0]]
    assert _metrics(duplicate_same)["60日涨跌幅"] is None
    conflict = _rows(61)
    conflict[-1] = (conflict[-1][0], 11.0, conflict[-1][2])
    conflict.append((conflict[-1][0], 12.0, conflict[-1][2]))
    assert _metrics(conflict) == {"量比": None, "60日涨跌幅": None}


def test_kline_metrics_rejects_bool_nonfinite_and_negative_values():
    rows = _rows(61)
    bad_values = [(True, 100.0), (float("inf"), 100.0), (10.0, float("nan")),
                  (0.0, 100.0), (10.0, -1.0)]
    for index in range(56):
        rows[index] = (rows[index][0], *bad_values[index % len(bad_values)])
    result = _metrics(rows)
    assert result["60日涨跌幅"] is None
    assert result["量比"] is None


def _fake_baostock(monkeypatch, *, login_delay=0.0, next_delay=0.0):
    calls = {"login": 0, "query": 0}
    rows = [("2026-01-01", "10", "100")]

    def fake_login():
        calls["login"] += 1
        if login_delay:
            time.sleep(login_delay)
        return True

    class FakeRS:
        error_code = "0"

        def __init__(self, payload):
            self._payload = payload
            self._index = 0

        def next(self):
            if next_delay:
                time.sleep(next_delay)
            if self._index < len(self._payload):
                self._index += 1
                return True
            return False

        def get_row_data(self):
            return self._payload[self._index - 1]

    def fake_query(*args, **kwargs):
        calls["query"] += 1
        return FakeRS(rows)

    import src.data.akshare_client as ac
    monkeypatch.setattr(ac, "_ensure_baostock_login", fake_login)
    monkeypatch.setitem(sys.modules, "baostock", SimpleNamespace(query_history_k_data_plus=fake_query))
    monkeypatch.setattr(ScannerEngine, "KLINE_TOTAL_TIMEOUT_S", 0.2, raising=False)
    engine = ScannerEngine.__new__(ScannerEngine)
    return engine, calls


def _fake_kline(monkeypatch, rows, module_name=None):
    calls = {"login": 0, "query": 0, "fields": []}

    def fake_login():
        calls["login"] += 1
        return True

    class FakeRS:
        error_code = "0"

        def __init__(self, payload):
            self._payload = payload
            self._index = 0

        def next(self):
            if self._index < len(self._payload):
                self._index += 1
                return True
            return False

        def get_row_data(self):
            return self._payload[self._index - 1]

    def fake_query(symbol, fields, **kwargs):
        calls["query"] += 1
        calls["fields"].append(fields)
        return FakeRS(rows)

    if module_name is not None:
        fake_query.__module__ = module_name
    import src.data.akshare_client as ac
    monkeypatch.setattr(ac, "_ensure_baostock_login", fake_login)
    monkeypatch.setitem(sys.modules, "baostock", SimpleNamespace(query_history_k_data_plus=fake_query))
    monkeypatch.setattr(ScannerEngine, "KLINE_TOTAL_TIMEOUT_S", 5.0, raising=False)
    return ScannerEngine.__new__(ScannerEngine), calls


def _code_rows(code_field, days=6, with_code=True):
    start = datetime(2026, 9, 1)
    out = []
    for index in range(days):
        day = (start + timedelta(days=index)).strftime("%Y-%m-%d")
        out.append((day, code_field, 10.0, 100.0) if with_code else (day, 10.0, 100.0))
    return out


def test_kline_query_verifies_code_and_exchange(monkeypatch):
    engine, calls = _fake_kline(monkeypatch, _code_rows("sh.600519"))
    assert engine._kline_metrics(["600519"])["600519"]["量比"] == 1.0
    assert "code" in calls["fields"][0]
    for bad in ("sz.600519", "sh.000001", "", "600519",
                "sh6005190", "sh.600519garbage", "6005190.sh"):
        engine, _ = _fake_kline(monkeypatch, _code_rows(bad))
        assert engine._kline_metrics(["600519"]) == {}, bad
    engine, _ = _fake_kline(monkeypatch, _code_rows("", with_code=False))
    assert engine._kline_metrics(["600519"]) == {}


def test_kline_query_shim_requires_code(monkeypatch):
    engine, _ = _fake_kline(monkeypatch, _code_rows("sh.600519"), module_name="src.data.bs_shim")
    assert engine._kline_metrics(["600519"])["600519"]["量比"] == 1.0
    engine, _ = _fake_kline(monkeypatch, _code_rows("sh.000001"), module_name="src.data.bs_shim")
    assert engine._kline_metrics(["600519"]) == {}
    engine, _ = _fake_kline(monkeypatch, _code_rows("600519"), module_name="src.data.bs_shim")
    assert engine._kline_metrics(["600519"]) == {}
    engine, _ = _fake_kline(monkeypatch, _code_rows("", with_code=False), module_name="src.data.bs_shim")
    assert engine._kline_metrics(["600519"]) == {}


def test_kline_metrics_deadline_anchored_before_late_worker(monkeypatch):
    import src.scanner.scanner_engine as se
    engine, calls = _fake_baostock(monkeypatch)
    real = se.baostock_session.run

    def delayed(fn, *, timeout, label="bs-test"):
        time.sleep(0.3)
        return real(fn, timeout=timeout, label=label)

    monkeypatch.setattr(se.baostock_session, "run", delayed)
    started = time.monotonic()
    result = engine._kline_metrics(["000001"])
    elapsed = time.monotonic() - started
    assert result == {}
    assert elapsed < 0.8
    assert calls["login"] == 0 and calls["query"] == 0


def test_kline_metrics_budget_covers_login(monkeypatch):
    engine, calls = _fake_baostock(monkeypatch, login_delay=0.4)
    started = time.monotonic()
    result = engine._kline_metrics(["000001", "000002"])
    elapsed = time.monotonic() - started
    assert result == {}
    assert elapsed < 0.5
    time.sleep(0.35)
    assert calls["query"] == 0  # deadline checked before the first query


def test_kline_metrics_budget_covers_read_and_stops_next_security(monkeypatch):
    engine, calls = _fake_baostock(monkeypatch, next_delay=0.4)
    started = time.monotonic()
    result = engine._kline_metrics(["000001", "000002"])
    elapsed = time.monotonic() - started
    assert result == {}
    assert elapsed < 0.5
    time.sleep(0.45)
    assert calls["query"] == 1  # no second security after the deadline


def test_enrich_missing_metric_never_satisfies_neq():
    engine = ScannerEngine.__new__(ScannerEngine)
    engine._kline_metrics = lambda codes: {"000001": {"量比": 0.6, "60日涨跌幅": 1.0}}
    df = pd.DataFrame({"代码": ["000001", "000002"], "涨跌幅": [-1.0, -1.0]})
    out = engine._apply_filters_with_kline_enrich(
        df, [{"field": "volume_ratio", "op": "neq", "value": 1.0}]
    )
    assert out["代码"].tolist() == ["000001"]


def test_enrich_without_code_keeps_kline_condition_failing():
    engine = ScannerEngine.__new__(ScannerEngine)
    engine._kline_metrics = lambda codes: {}
    df = pd.DataFrame({"涨跌幅": [-1.0, -1.0]})
    out = engine._apply_filters_with_kline_enrich(
        df, [{"field": "change_pct", "op": "between", "value": [-3, -0.1]},
             {"field": "volume_ratio", "op": "lt", "value": 1.0}]
    )
    assert out.empty


def test_enrich_over_limit_keeps_kline_condition_failing(monkeypatch):
    engine = ScannerEngine.__new__(ScannerEngine)
    monkeypatch.setattr(ScannerEngine, "KLINE_ENRICH_MAX", 1, raising=False)
    engine._kline_metrics = lambda codes: {code: {"量比": 0.5} for code in codes}
    df = pd.DataFrame({"代码": ["000001", "000002"], "涨跌幅": [-1.0, -1.0]})
    out = engine._apply_filters_with_kline_enrich(
        df, [{"field": "change_pct", "op": "between", "value": [-3, -0.1]},
             {"field": "volume_ratio", "op": "lt", "value": 1.0}]
    )
    assert out.empty


# ---------------- theme scope ----------------

def test_theme_static_requires_exact_key_and_no_reverse_substring(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "cache"))
    nodes = lambda: [("sw2_410100", "电力"), ("sw2_630800", "电网设备")]
    assert bma.resolve_nodes(["电力设备"], node_fetcher=nodes) == []
    assert ("sw2_410100", "电力") not in bma.resolve_nodes(["电力设备"], node_fetcher=nodes)
    assert bma.resolve_nodes(["电力"], node_fetcher=nodes) == [("sw2_410100", "电力")]


def test_theme_dynamic_query_keeps_pv_recall(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "cache"))
    nodes = lambda: [("sw2_999900", "光伏设备"), ("sw3_999901", "光伏电池组件")]
    assert bma.resolve_nodes(["光伏"], node_fetcher=nodes)[0][0] == "sw2_999900"
