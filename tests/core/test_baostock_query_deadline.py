"""T05-F2: complete fallback queries share their existing 30-second wait."""
import logging
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

# Pre-import real modules before any substitute is installed.
from src.data import akshare_client as module
from src.data.timeout_call import call_with_timeout


class FixedDate(datetime):
    @classmethod
    def now(cls):
        return cls(2026, 10, 25)


@pytest.fixture(params=["realtime", "kline", "index"])
def env(request, monkeypatch):
    workers, budgets, logouts, conversions, payloads = [], [], [], [], []

    def bounded(fn, *args, timeout, **kwargs):
        budgets.append(timeout)

        def owned():
            workers.append(threading.current_thread())
            result = fn(*args, **kwargs)
            payloads.append(result)
            return result

        return call_with_timeout(owned, timeout=0.05)

    monkeypatch.setattr(module, "datetime", FixedDate)
    monkeypatch.setattr(module, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(module, "_baostock_logout", lambda: logouts.append(True))
    monkeypatch.setattr(module, "_call_with_timeout", bounded)
    quote = module.AKShareClient._baostock_quote_from_row
    numeric = module.pd.to_numeric

    def convert_quote(*args, **kwargs):
        conversions.append(threading.current_thread())
        return quote(*args, **kwargs)

    def convert_numeric(*args, **kwargs):
        conversions.append(threading.current_thread())
        return numeric(*args, **kwargs)

    monkeypatch.setattr(module.AKShareClient, "_baostock_quote_from_row", staticmethod(convert_quote))
    monkeypatch.setattr(module.pd, "to_numeric", convert_numeric)
    kind = request.param

    def invoke(**kwargs):
        if kind == "realtime":
            return module.AKShareClient._fetch_baostock_realtime("600519")
        if kind == "kline":
            return module.AKShareClient._fetch_baostock_kline(
                "600519", start_date="2026-10-01", end_date="2026-10-25", **kwargs
            )
        return module.AKShareClient._get_index_trend_uncached("sh.000300")

    state = SimpleNamespace(kind=kind, invoke=invoke, workers=workers, budgets=budgets,
                            logouts=logouts, conversions=conversions, payloads=payloads)
    yield state
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive(), "owned query worker survived mock teardown"
    assert all(budget == 30 for budget in budgets)


class Result:
    def __init__(self, kind, *, empty=False, error="0", hook=lambda stage: None):
        self.hook, self.error = hook, error
        self.index, self.next_calls, self.field_reads = -1, 0, 0
        if kind == "index":
            self._fields = ["date", "close", "high", "preclose"]
            self.rows = [["2026-10-01", str(100 + i), str(101 + i), str(99 + i)] for i in range(25)]
        else:
            self._fields = "date,code,open,high,low,close,preclose,volume,amount".split(",")
            self.rows = [["2026-10-23", "sh.600519", "10", "12", "9", "11", "10", "100", "1100"]]
        if empty:
            self.rows = []

    @property
    def error_code(self):
        self.hook("error")
        return self.error

    @property
    def error_msg(self):
        return "controlled rejection"

    @property
    def fields(self):
        self.hook("fields")
        self.field_reads += 1
        assert self.error != "0" or self.index >= len(self.rows), "fields consumed before iteration ended"
        return self._fields

    def next(self):
        self.hook("next")
        self.next_calls += 1
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        self.hook("row")
        return self.rows[self.index]


def install(monkeypatch, result, query_hook=lambda: None):
    calls = []

    def query(*args, **kwargs):
        calls.append((args, kwargs, threading.current_thread()))
        query_hook()
        return result

    monkeypatch.setattr(module.bs, "query_history_k_data_plus", query)
    return calls


@pytest.mark.parametrize("stage", ["query", "next", "fields"])
def test_blocked_query_returns_before_release_and_late_rows_do_not_convert(monkeypatch, env, stage):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    returned = []

    def hook(current):
        if stage == current:
            entered.set()
            assert release.wait(3), "test barrier was not released"

    result = Result(env.kind, hook=hook)
    install(monkeypatch, result, lambda: hook("query"))

    def invoke():
        try:
            returned.append(env.invoke())
        finally:
            finished.set()

    caller = threading.Thread(target=invoke, daemon=True)
    caller.start()
    try:
        assert entered.wait(2)
        assert finished.wait(0.7), f"{env.kind}/{stage} escaped wait budget"
        assert returned == [None]
    finally:
        release.set()
        caller.join(3)
        assert not caller.is_alive()
    for worker in env.workers:
        worker.join(3)
        assert not worker.is_alive()
    assert result.next_calls == len(result.rows) + 1
    assert env.conversions == []
    assert returned == [None]
    assert env.logouts == []
    assert env.budgets == [30]


def test_normal_query_parameters_and_caller_conversion(monkeypatch, env):
    operations = []
    result = Result(env.kind, hook=lambda stage: operations.append(threading.current_thread()))
    calls = install(monkeypatch, result)
    value = env.invoke(adjust="qfq")
    args, kwargs, query_thread = calls[0]
    if env.kind == "index":
        assert args == ("sh.000300", "date,close,high,preclose")
        assert kwargs == {"start_date": "2025-09-20", "end_date": "2026-10-25", "frequency": "d"}
        assert value == {"trend": "BULLISH", "ma20": 114.5, "ma60": None,
                         "ma250": None, "close": 124.0, "change_pct": 0.81,
                         "high_250d": None, "change_20d": 19.23}
    else:
        assert args == ("sh.600519", ",".join(result._fields))
        expected = {"start_date": "2026-10-01" if env.kind == "kline" else "2026-10-15",
                    "end_date": "2026-10-25", "frequency": "d"}
        if env.kind == "kline":
            expected["adjustflag"] = "2"
            assert value.iloc[0].to_dict() == {
                "日期": "2026-10-23", "代码": "sh.600519", "开盘": 10,
                "最高": 12, "最低": 9, "收盘": 11, "preclose": 10,
                "成交量": 100, "成交额": 1100,
            }
        else:
            assert value["price"] == 11 and value["change_pct"] == 10
            assert value["effective_at"] == "2026-10-23"
            assert value["volume"] == 100 and value["source"] == "baostock"
        assert kwargs == expected
    assert query_thread is not threading.current_thread()
    assert all(thread is query_thread for thread in operations)
    assert env.conversions and all(thread is threading.current_thread() for thread in env.conversions)
    assert env.budgets == [30]
    assert env.payloads[0]["fields"] == result._fields
    assert env.payloads[0]["fields"] is not result._fields
    assert env.payloads[0]["data"] == result.rows


@pytest.mark.parametrize("error", [False, True], ids=["empty", "query-error"])
def test_empty_and_error_preserve_logging_and_skip_fields(monkeypatch, env, caplog, error):
    result = Result(env.kind, empty=not error, error="1" if error else "0")
    install(monkeypatch, result)
    with caplog.at_level(logging.DEBUG, logger=module.__name__):
        assert env.invoke() is None
    assert result.field_reads == 0 and env.conversions == []
    if env.kind == "index":
        assert "大盘数据不足（0行），无法判断趋势" in caplog.text
        assert result.next_calls == 0 if error else result.next_calls == 1
    elif error:
        assert "controlled rejection" in caplog.text
        assert result.next_calls == 0
        assert caplog.records[-1].levelno == (logging.ERROR if env.kind == "kline" else logging.WARNING)
    else:
        assert not caplog.records
    assert env.logouts == []


@pytest.mark.parametrize("stage", ["query", "error", "fields", "next", "row"])
@pytest.mark.parametrize("socket_timeout", [False, True])
def test_exception_paths_keep_original_reset_and_timeout_behavior(
    monkeypatch, env, caplog, stage, socket_timeout
):
    failure = TimeoutError("socket controlled") if socket_timeout else ValueError("controlled failure")

    def hook(current):
        if stage == current:
            raise failure

    install(monkeypatch, Result(env.kind, hook=hook), lambda: hook("query"))
    with caplog.at_level(logging.DEBUG, logger=module.__name__):
        assert env.invoke() is None
    iteration_timeout = socket_timeout and (
        stage in {"next", "row"} or (stage == "error" and env.kind == "index")
    )
    assert len(env.logouts) == int(socket_timeout and not iteration_timeout)
    assert ("走降级" in caplog.text) == iteration_timeout
    if not iteration_timeout:
        assert str(failure) in caplog.text and "异常" in caplog.text


def test_login_failure_does_not_query(monkeypatch, env):
    monkeypatch.setattr(module, "_ensure_baostock_login", lambda: False)
    calls = install(monkeypatch, Result(env.kind))
    assert env.invoke() is None
    assert calls == [] and env.budgets == []


def test_index_short_rows_and_changed_error_keep_read_semantics(monkeypatch, env):
    result = Result(env.kind)
    result.rows = result.rows[:3] if env.kind == "index" else result.rows * 2

    def hook(stage):
        if stage == "row":
            result.error = "1"

    result.hook = hook
    install(monkeypatch, result)
    value = env.invoke()
    if env.kind == "kline":
        # Bitwise &: next() still runs when the changed error code is nonzero.
        assert result.next_calls == 2 and len(value) == 1
    elif env.kind == "index":
        assert result.next_calls == 1 and value is None and result.field_reads == 0
    else:
        assert result.next_calls == 3 and value["price"] == 11
