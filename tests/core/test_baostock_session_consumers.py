"""Shared Baostock session behavior through real consumers (no network)."""

import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from src.data import akshare_client as ac
from src.data import baostock_session as session
from src.data.timeout_call import call_with_timeout


class Result:
    def __init__(self, rows=None, fields=None):
        self.rows = rows or []
        self._fields = fields or ["date", "code", "open", "high", "low", "close", "preclose", "volume", "amount"]
        self.error_code = "0"
        self.error_msg = ""
        self._i = -1
        self.next_calls = 0

    @property
    def fields(self):
        return list(self._fields)

    def next(self):
        self.next_calls += 1
        self._i += 1
        return self._i < len(self.rows)

    def get_row_data(self):
        return self.rows[self._i]


def test_kline_query_holds_lease_and_second_consumer_does_not_query(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def query(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3), "test barrier was not released"
        return Result([["2026-10-23", "sh.600519", "10", "12", "9", "11", "10", "100", "1100"]])

    monkeypatch.setattr(ac.bs, "query_history_k_data_plus", query)
    monkeypatch.setattr(ac.bs, "query_stock_basic", lambda **kwargs: SimpleNamespace(error_code="0"))
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)

    results = []
    second_returned = threading.Event()

    def first_call():
        results.append(ac.AKShareClient._fetch_baostock_kline(
            "600519", start_date="2026-10-01", end_date="2026-10-25"))

    def second_call():
        results.append(ac.AKShareClient._fetch_baostock_kline(
            "600519", start_date="2026-10-01", end_date="2026-10-25"))
        second_returned.set()

    first = threading.Thread(target=first_call)
    first.start()
    assert entered.wait(2)
    second = threading.Thread(target=second_call)
    second.start()
    try:
        assert second_returned.wait(0.5), "second consumer did not return while lease was held"
        assert len(calls) == 1, "second consumer entered the supplier while the first lease was held"
        assert results[-1] is None
    finally:
        release.set()
        first.join(3)
        second.join(3)
    assert not first.is_alive() and not second.is_alive()


def test_datafeeder_timeout_does_not_publish_or_logout(monkeypatch):
    from src.data.data_feeder import DataFeeder
    import src.data.data_feeder as feeder_module

    entered, release = threading.Event(), threading.Event()
    logouts = []

    def blocked_stock(self, start, end):
        entered.set()
        assert release.wait(3)
        return None

    monkeypatch.setattr(feeder_module.DataFeeder, "_fetch_stock_kline", blocked_stock)
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(ac, "_baostock_logout", lambda: logouts.append(True))
    feeder = DataFeeder("600519", "2026-01-01", "2026-03-31")
    result = []

    def run_load():
        result.append(feeder.load(timeout=0.2))

    thread = threading.Thread(target=run_load)
    thread.start()
    try:
        assert entered.wait(2)
        thread.join(2)
        assert not thread.is_alive()
        assert result == [False]
        assert feeder._stock_df is None and feeder._index_df is None
        assert logouts == []
        assert session._LEASE.locked()
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()


def _stock_frame():
    return pd.DataFrame({
        "date": ["2026-01-01", "2026-01-02"],
        "open": [10.0, 10.0], "high": [11.0, 11.0], "low": [9.0, 9.0],
        "close": [10.0, 10.5], "volume": [100.0, 100.0], "amount": [1000.0, 1050.0],
    })


def test_datafeeder_empty_stock_closes_immediately_without_index(monkeypatch):
    from src.data.data_feeder import DataFeeder
    import src.data.data_feeder as feeder_module

    calls = []
    monkeypatch.setattr(feeder_module.DataFeeder, "_fetch_stock_kline", lambda self, s, e: pd.DataFrame())
    monkeypatch.setattr(feeder_module.DataFeeder, "_fetch_index_kline",
                        lambda self, s, e: calls.append("index") or None)
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(ac, "_baostock_logout",
                        lambda: calls.append(("close", threading.current_thread())))
    feeder = DataFeeder("600519", "2026-01-01", "2026-03-31")
    assert feeder.load(timeout=2) is False
    assert feeder._stock_df is None and feeder._index_df is None
    assert [c for c in calls if c == "index"] == []
    assert [c for c in calls if isinstance(c, tuple) and c[0] == "close"]
    assert all(c[1] is not threading.main_thread() for c in calls if isinstance(c, tuple))


def test_datafeeder_index_failure_keeps_stock_and_closes_same_worker(monkeypatch):
    from src.data.data_feeder import DataFeeder
    import src.data.data_feeder as feeder_module

    threads = []
    monkeypatch.setattr(feeder_module.DataFeeder, "_fetch_stock_kline",
                        lambda self, s, e: threads.append(threading.current_thread()) or _stock_frame())
    monkeypatch.setattr(feeder_module.DataFeeder, "_fetch_index_kline", lambda self, s, e: None)
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(ac, "_baostock_logout",
                        lambda: threads.append(threading.current_thread()))
    feeder = DataFeeder("600519", "2026-01-01", "2026-01-02")
    assert feeder.load(timeout=2) is True
    assert feeder._stock_df is not None and feeder._index_df is None
    assert len(threads) == 2 and threads[0] is threads[1]
    assert threads[0] is not threading.main_thread()


def test_datafeeder_slow_close_does_not_publish(monkeypatch):
    from src.data.data_feeder import DataFeeder
    import src.data.data_feeder as feeder_module

    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(feeder_module.DataFeeder, "_fetch_stock_kline", lambda self, s, e: _stock_frame())
    monkeypatch.setattr(feeder_module.DataFeeder, "_fetch_index_kline", lambda self, s, e: None)
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)

    def slow_close():
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(ac, "_baostock_logout", slow_close)
    feeder = DataFeeder("600519", "2026-01-01", "2026-01-02")
    result = []
    thread = threading.Thread(target=lambda: result.append(feeder.load(timeout=0.2)))
    thread.start()
    try:
        assert entered.wait(2)
        thread.join(2)
        assert not thread.is_alive()
        assert result == [False]
        assert feeder._stock_df is None and feeder._index_df is None
        assert session._LEASE.locked()
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()


def test_forecast_reads_fields_once(monkeypatch):
    class ForecastRS(Result):
        def __init__(self):
            super().__init__(rows=[["预增", "摘要", "2026-01-01", "2025-12-31"]],
                             fields=["profitForcastType", "profitForcastAbstract",
                                     "profitForcastExpPubDate", "profitForcastExpStatDate"])
            self.field_reads = 0

        @property
        def fields(self):
            self.field_reads += 1
            return list(self._fields)

    rs = ForecastRS()
    monkeypatch.setattr(ac.bs, "query_forecast_report", lambda **kwargs: rs)
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)
    parsed = ac.AKShareClient.get_latest_forecast("600519")
    assert parsed and parsed["pub_date"] == "2026-01-01"
    assert rs.field_reads == 1, "forecast fields property was read repeatedly"


def test_basic_socket_reset_stays_in_same_worker(monkeypatch):
    from src.data import financial_data  # noqa: F401 - preload consumer package

    reset_threads = []

    def query(**kwargs):
        raise ConnectionResetError("socket reset fixture")

    monkeypatch.setattr(ac.bs, "query_stock_basic", query)
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(session, "_LOGGED_IN", True, raising=False)
    monkeypatch.setattr(ac.bs, "logout", lambda: reset_threads.append(threading.current_thread()))
    assert ac.AKShareClient.get_stock_basic_name("600519") is None
    assert reset_threads and reset_threads[0] is not threading.main_thread()


def test_late_kline_error_does_not_call_next(monkeypatch):
    calls = {"next": 0, "error": 0}

    class ErrorRS(Result):
        @property
        def error_code(self):
            calls["error"] += 1
            return "0" if calls["error"] == 1 else "1"

        def next(self):
            calls["next"] += 1
            return True

    monkeypatch.setattr(ac.bs, "query_history_k_data_plus", lambda *a, **k: ErrorRS())
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)
    assert ac.AKShareClient._fetch_baostock_kline(
        "600519", start_date="2026-10-01", end_date="2026-10-25") is None
    assert calls["next"] == 0, "error_code was read and next was still called"


def test_financial_query_exhausts_rows_but_keeps_first(monkeypatch):
    from src.data import financial_data

    rows = [
        ["sh.600519", "2025-03-31", "2025-04-30", "1.0"],
        ["sh.600519", "2025-03-31", "2025-04-30", "2.0"],
        ["sh.600519", "2025-03-31", "2025-04-30", "3.0"],
    ]
    result = Result(rows=rows, fields=["code", "statDate", "pubDate", "value"])

    def query(**kwargs):
        return result

    monkeypatch.setattr(ac.bs, "query_profit_data", query)
    monkeypatch.setattr(ac, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(financial_data, "_ensure_baostock_login", lambda: True)
    row = financial_data._query_one("query_profit_data", "sh.600519", 2025, 1, 1)
    assert row["value"] == "1.0"
    assert result.next_calls == 4, "financial query must exhaust all rows"
    assert session._LEASE.locked() is False


def test_login_timeout_keeps_lease_and_does_not_logout(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    logouts = []

    def login():
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(error_code="0", error_msg="")

    monkeypatch.setattr(ac.bs, "login", login)
    monkeypatch.setattr(ac.bs, "logout", lambda: logouts.append(True))
    monkeypatch.setattr(session, "_LOGGED_IN", False, raising=False)
    monkeypatch.setattr(session, "LOGIN_TIMEOUT", 0.1, raising=False)

    first = []
    thread = threading.Thread(target=lambda: first.append(ac._ensure_baostock_login()))
    thread.start()
    assert entered.wait(2)
    try:
        thread.join(1)
        assert not thread.is_alive()
        assert first == [False]
        assert session._LEASE.locked(), "caller timeout must not release the supplier lease"
        assert ac._ensure_baostock_login() is False
        assert logouts == []
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()
