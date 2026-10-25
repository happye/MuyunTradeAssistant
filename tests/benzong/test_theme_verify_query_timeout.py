"""T05-F1: the complete post-login query shares the existing wait budget."""
import threading
from types import SimpleNamespace

import baostock as bs
import pytest

# Import before patches: a first import must not capture any test substitute.
from src.core.benzong import theme_locator
from src.data import akshare_client
from src.data import baostock_session


@pytest.fixture
def query_env(monkeypatch):
    workers = []
    budgets = []
    materialized = []

    real_run = baostock_session.run

    def bounded(fn, *args, timeout, **kwargs):
        budgets.append(timeout)

        def owned():
            workers.append(threading.current_thread())
            value = fn(*args)
            materialized.append(value)
            return value

        # Keep the production absolute runner/lease, but shorten only the test wait.
        return real_run(owned, timeout=0.5, label=kwargs.get("label", "bs-test"))

    monkeypatch.setattr(akshare_client, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(baostock_session, "run", bounded)
    yield SimpleNamespace(budgets=budgets, materialized=materialized, workers=workers)
    # Every test releases its barriers before fixture teardown restores mocks.
    for worker in workers:
        worker.join(timeout=3)
        assert not worker.is_alive(), "owned query worker did not terminate"
    assert all(budget == 15 for budget in budgets)


class Result:
    def __init__(self, rows=None, fields=None, error="0", hook=lambda stage: None):
        self._fields = fields if fields is not None else ["code_name", "status"]
        self.rows = [["贵州茅台", "1"]] if rows is None else rows
        self.error = error
        self.hook = hook
        self.index = -1
        self.next_calls = 0
        self.row_calls = 0
        self.field_reads = 0

    @property
    def error_code(self):
        self.hook("error")
        return self.error

    @property
    def fields(self):
        self.hook("fields")
        self.field_reads += 1
        return self._fields

    def next(self):
        self.hook("next")
        self.next_calls += 1
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        self.hook("row")
        self.row_calls += 1
        return self.rows[self.index]


@pytest.mark.parametrize("blocked_stage", ["query", "error", "fields", "next", "row"])
def test_query_and_consumption_return_before_blocked_worker_finishes(
    monkeypatch, query_env, blocked_stage
):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    returned = []

    def hook(stage):
        if stage == blocked_stage:
            entered.set()
            assert release.wait(3), "test barrier was not released"

    result = Result(hook=hook)

    def query(**kwargs):
        assert kwargs == {"code": "sh.600519"}
        hook("query")
        return result

    monkeypatch.setattr(bs, "query_stock_basic", query)

    def verify():
        try:
            returned.append(theme_locator._verify_code("600519", "贵州茅台"))
        finally:
            finished.set()

    caller = threading.Thread(target=verify, daemon=True)
    caller.start()
    try:
        assert entered.wait(2), "query did not reach the controlled barrier"
        assert finished.wait(0.7), f"{blocked_stage} escaped the query wait budget"
        assert returned == [None]
    finally:
        release.set()
        caller.join(timeout=3)
        assert not caller.is_alive(), "verification caller did not terminate"
    for worker in query_env.workers:
        worker.join(timeout=3)
        assert not worker.is_alive(), "late query worker did not terminate"
    if blocked_stage == "next":
        assert result.next_calls == 1
        assert result.row_calls == 0, "late next returned and then get_row_data ran"
    elif blocked_stage == "row":
        assert result.next_calls == 1
        assert result.row_calls == 1
    else:
        assert result.next_calls == 0
        assert result.row_calls == 0
    assert returned == [None], "late successful rows changed the expired return"


def test_late_error_does_not_read_fields(monkeypatch, query_env):
    entered, release = threading.Event(), threading.Event()

    def hook(stage):
        if stage == "error":
            entered.set()
            assert release.wait(3)

    result = Result(hook=hook)
    monkeypatch.setattr(bs, "query_stock_basic", lambda **kwargs: result)
    finished = threading.Event()
    caller = threading.Thread(target=lambda: (theme_locator._verify_code("600519", "贵州茅台"), finished.set()))
    caller.start()
    try:
        assert entered.wait(2)
        assert finished.wait(0.7)
    finally:
        release.set()
        caller.join(3)
    assert not caller.is_alive()
    assert result.field_reads == 0, "late error_code was followed by fields read"


def test_normal_query_materializes_fields_and_all_rows(monkeypatch, query_env):
    result = Result(
        fields=["status", "unused", "code_name"],
        rows=[["1", "a", "贵州茅台"], ["1", "b", "另一家公司"]],
    )
    operations = []
    result.hook = lambda stage: operations.append(threading.current_thread())

    def query(**kwargs):
        assert kwargs == {"code": "sh.600519"}
        operations.append(threading.current_thread())
        return result

    monkeypatch.setattr(bs, "query_stock_basic", query)
    assert theme_locator._verify_code("600519", "贵州茅台") == {
        "code": "600519", "name": "贵州茅台"
    }
    assert result.next_calls == 3
    assert result.row_calls == 2
    assert query_env.budgets == [15]
    fields, rows = query_env.materialized[0]
    assert fields == result._fields and fields is not result._fields
    assert rows == result.rows
    assert all(thread is not threading.current_thread() for thread in operations)
    assert len(set(operations)) == 1


@pytest.mark.parametrize(
    "result, ai_name",
    [
        (Result(error="1"), "贵州茅台"),
        (Result(rows=[]), "贵州茅台"),
        (Result(), "中国平安"),
        (Result(rows=[["贵州茅台", "0"]]), "贵州茅台"),
    ],
    ids=["query-error", "empty", "name-mismatch", "delisted"],
)
def test_rejected_results_stay_none(monkeypatch, query_env, result, ai_name):
    monkeypatch.setattr(bs, "query_stock_basic", lambda **kwargs: result)
    assert theme_locator._verify_code("600519", ai_name) is None
    assert query_env.budgets == [15]
    if result.error != "0":
        assert result.next_calls == 0


@pytest.mark.parametrize("stage", ["query", "next", "row"])
def test_query_and_read_exceptions_stay_none(monkeypatch, query_env, stage):
    def hook(current):
        if current == stage:
            raise RuntimeError("controlled query failure")

    def query(**kwargs):
        hook("query")
        return Result(hook=hook)

    monkeypatch.setattr(bs, "query_stock_basic", query)
    assert theme_locator._verify_code("600519", "贵州茅台") is None
    assert query_env.budgets == [15]


def test_absent_status_keeps_existing_default(monkeypatch, query_env):
    monkeypatch.setattr(
        bs, "query_stock_basic",
        lambda **kwargs: Result(fields=["code_name"], rows=[["贵州茅台"]]),
    )
    assert theme_locator._verify_code("600519", "贵州茅台") == {
        "code": "600519", "name": "贵州茅台"
    }


def test_failed_login_does_not_query(monkeypatch, query_env):
    monkeypatch.setattr(akshare_client, "_ensure_baostock_login", lambda: False)

    def forbidden(**kwargs):
        pytest.fail("failed login must not issue a query")

    monkeypatch.setattr(bs, "query_stock_basic", forbidden)
    assert theme_locator._verify_code("600519", "贵州茅台") is None
    assert query_env.budgets == [15]
