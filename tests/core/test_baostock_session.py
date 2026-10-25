"""Process-wide non-blocking Baostock lease (offline, finite)."""

import os
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from src.data import baostock_session as session
from src.data.timeout_call import call_with_timeout, run_parallel_with_deadline, ParallelTimeout


def _wait_release(entered, release, timeout=3):
    entered.set()
    assert release.wait(timeout)


@pytest.mark.skipif(os.environ.get("MUYUN_SESSION_EXIT_PROBE") != "1",
                    reason="dedicated child-process exit probe; run exact nodeid with MUYUN_SESSION_EXIT_PROBE=1")
def test_daemon_worker_does_not_block_exit():
    child = r'''
import threading, time
from src.data import baostock_session as session
blocked = threading.Event()
def body():
    blocked.wait(10)
try:
    session.run(body, timeout=0.1, label="exit-probe")
except session.BaostockTimeout:
    pass
print("child-returned")
'''
    with tempfile.TemporaryDirectory(prefix="muyun-session-exit-") as home:
        env = os.environ.copy()
        env.update({"HOME": home, "USERPROFILE": home, "TEMP": home, "TMP": home,
                    "PYTHONPATH": os.getcwd(), "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONIOENCODING": "utf-8"})
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", child],
                              cwd=os.getcwd(), env=env, capture_output=True,
                              text=True, timeout=4)
        elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr
    assert "child-returned" in proc.stdout
    assert elapsed < 3


def test_busy_request_is_immediate_and_has_no_second_supplier_call():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def owner():
        calls.append("owner")
        _wait_release(entered, release)
        return "ok"

    result = []
    worker = threading.Thread(target=lambda: result.append(session.run(owner, timeout=2, label="owner")))
    worker.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        with pytest.raises(session.BaostockBusy):
            session.run(lambda: calls.append("second"), timeout=1, label="second")
        assert time.monotonic() - started < 0.2
        assert calls == ["owner"]
    finally:
        release.set()
        worker.join(3)
    assert result == ["ok"]
    assert not session._LEASE.locked()


def test_caller_timeout_keeps_lease_until_worker_finally_releases():
    entered, release = threading.Event(), threading.Event()

    def owner():
        _wait_release(entered, release)
        return "late"

    with pytest.raises(session.BaostockTimeout):
        session.run(owner, timeout=0.1, label="timeout-owner")
    assert entered.wait(2)
    assert session._LEASE.locked(), "caller timeout must not release the supplier lease"
    with pytest.raises(session.BaostockBusy):
        session.run(lambda: None, timeout=0.1, label="busy")
    release.set()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()
    assert session.run(lambda: "recovered", timeout=1, label="recovered") == "recovered"


def test_heartbeat_ping_timeout_keeps_lease_and_does_not_relogin(monkeypatch):
    import baostock as bs

    entered, release = threading.Event(), threading.Event()
    logins = []
    monkeypatch.setattr(session, "_LOGGED_IN", True, raising=False)
    monkeypatch.setattr(session, "LOGIN_TIMEOUT", 0.1, raising=False)

    def ping(**kwargs):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(error_code="0")

    monkeypatch.setattr(bs, "query_stock_basic", ping)
    monkeypatch.setattr(bs, "login", lambda: logins.append(True))
    thread = threading.Thread(target=lambda: session.ensure_login())
    thread.start()
    try:
        assert entered.wait(2)
        thread.join(1)
        assert not thread.is_alive()
        assert session._LEASE.locked()
        assert logins == []
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()
    assert logins == []


def test_logout_timeout_keeps_lease_and_does_not_repeat(monkeypatch):
    import baostock as bs

    entered, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(session, "_LOGGED_IN", True, raising=False)

    def logout():
        calls.append("logout")
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(error_code="0")

    monkeypatch.setattr(bs, "logout", logout)
    first = []
    thread = threading.Thread(target=lambda: first.append(session.logout(timeout=0.1)))
    thread.start()
    try:
        assert entered.wait(2)
        thread.join(1)
        assert not thread.is_alive()
        assert first == [False]
        assert session._LEASE.locked()
        assert session.logout(timeout=0.1) is False
        assert calls == ["logout"]
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()


def test_loginout_print_is_local_and_other_threads_keep_printing(monkeypatch, capsys):
    import baostock as bs
    import importlib
    loginout = importlib.import_module("baostock.login.loginout")

    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(session, "_LOGGED_IN", False, raising=False)

    def login():
        entered.set()
        assert release.wait(3)
        loginout.print("supplier-noise")
        return SimpleNamespace(error_code="0", error_msg="")

    monkeypatch.setattr(bs, "login", login)
    worker = threading.Thread(target=lambda: session.run(
        session._login_impl, timeout=2, label="login-print"))
    worker.start()
    try:
        assert entered.wait(2)
        other = threading.Thread(target=lambda: print("other-thread-visible"))
        other.start()
        other.join(2)
        assert not other.is_alive()
    finally:
        release.set()
        worker.join(3)
    captured = capsys.readouterr()
    assert "other-thread-visible" in captured.out
    assert "supplier-noise" not in captured.out


def test_heartbeat_login_drains_next_and_row_before_success(monkeypatch):
    import baostock as bs

    events = []

    class PingRS:
        error_code = "0"
        error_msg = ""

        @property
        def fields(self):
            events.append("fields")
            return ["code"]

        def next(self):
            events.append("next")
            return len([e for e in events if e == "next"]) < 2

        def get_row_data(self):
            events.append("row")
            return ["sh.600000"]

    monkeypatch.setattr(session, "_LOGGED_IN", True, raising=False)
    monkeypatch.setattr(bs, "query_stock_basic", lambda **kwargs: PingRS())
    assert session.run(session._login_impl, timeout=1, label="heartbeat") is True
    assert events.count("next") == 2 and events.count("row") == 1


def test_heartbeat_pagination_error_is_unhealthy_and_reconnects(monkeypatch):
    import baostock as bs

    events = []

    class PaginationErrorRS:
        error_code = "0"
        error_msg = ""
        fields = ["code"]

        def next(self):
            self.error_code = "1"
            return False

        def get_row_data(self):
            return ["sh.600000"]

    assert session.result_is_healthy(PaginationErrorRS()) is False

    monkeypatch.setattr(session, "_LOGGED_IN", True, raising=False)
    monkeypatch.setattr(bs, "query_stock_basic", lambda **kwargs: PaginationErrorRS())
    monkeypatch.setattr(bs, "logout", lambda: events.append("logout"))
    monkeypatch.setattr(bs, "login", lambda: events.append("login") or SimpleNamespace(error_code="0", error_msg=""))
    monkeypatch.setattr(bs, "query_history_k_data_plus", lambda *a, **k: events.append("business"))
    assert session.run(session._login_impl, timeout=2, label="pagination-heartbeat") is True
    session.run(lambda: bs.query_history_k_data_plus("sh.600000"), timeout=2, label="business")
    assert "logout" in events and "login" in events
    assert events.index("logout") < events.index("login") < events.index("business")


def test_receiver_rechecks_ancestor_after_worker_result(monkeypatch):
    real_run = session.call_with_timeout
    completed, release = threading.Event(), threading.Event()

    def delayed_return(fn, *, timeout):
        result = real_run(fn, timeout=timeout)
        completed.set()
        assert release.wait(2)
        return result

    monkeypatch.setattr(session, "call_with_timeout", delayed_return)
    returned = []
    parent_done = threading.Event()

    def parent():
        try:
            returned.append(session.run(lambda: "late-success", timeout=1, label="delayed-receiver"))
        finally:
            parent_done.set()

    with pytest.raises(TimeoutError):
        call_with_timeout(parent, timeout=0.05)
    try:
        assert completed.wait(1)
        release.set()
        assert parent_done.wait(2), "parent did not return after ancestor abandonment"
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()
    assert returned == [], "receiver returned success after ancestor abandoned"


def test_parallel_ancestor_abandonment_prevents_late_child_io():
    entered, release = threading.Event(), threading.Event()
    io = []

    def child():
        def body():
            entered.set()
            assert release.wait(3)
            session.check()
            io.append("parallel-late")

        session.run(body, timeout=1, label="parallel-child")

    with pytest.raises(ParallelTimeout):
        run_parallel_with_deadline([child], max_workers=1, timeout=0.03)
    assert entered.wait(2)
    release.set()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()
    assert io == [], "parallel child IO ran after ancestor abandoned"


def test_ancestor_abandonment_prevents_late_child_io():
    entered, release = threading.Event(), threading.Event()
    io = []

    def child_body():
        entered.set()
        assert release.wait(3)
        session.check()
        io.append("late-io")
        return "late"

    def parent():
        return session.run(child_body, timeout=1, label="child-under-parent")

    with pytest.raises(TimeoutError):
        call_with_timeout(parent, timeout=0.03)
    assert entered.wait(2)
    release.set()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()
    assert io == [], "child IO ran after ancestor abandoned"


def test_expired_entry_does_not_execute_callback():
    io = []
    with pytest.raises(session.BaostockTimeout):
        session.run(lambda: io.append("expired-io"), timeout=0, label="expired-entry")
    assert io == []
    assert not session._LEASE.locked()


def test_zero_nested_budget_does_not_execute_callback():
    io = []

    def outer():
        with pytest.raises(session.BaostockTimeout):
            session.run(lambda: io.append("zero-io"), timeout=0, label="zero-child")
        return "outer-ok"

    assert session.run(outer, timeout=1, label="zero-parent") == "outer-ok"
    assert io == []
    assert not session._LEASE.locked()


def test_late_login_result_does_not_write_shared_state(monkeypatch):
    import baostock as bs

    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(session, "_LOGGED_IN", False, raising=False)

    def login():
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(error_code="0", error_msg="")

    monkeypatch.setattr(bs, "login", login)
    with pytest.raises(session.BaostockTimeout):
        session.run(session._login_impl, timeout=0.05, label="late-login")
    assert entered.wait(2)
    release.set()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()
    assert session._LOGGED_IN is False, "late login result rewrote shared login state"


def test_late_logout_result_does_not_write_shared_state(monkeypatch):
    import baostock as bs

    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(session, "_LOGGED_IN", True, raising=False)

    def logout():
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(error_code="0")

    monkeypatch.setattr(bs, "logout", logout)
    with pytest.raises(session.BaostockTimeout):
        session.run(session._logout_impl, timeout=0.05, label="late-logout")
    assert entered.wait(2)
    release.set()
    deadline = time.monotonic() + 2
    while session._LEASE.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session._LEASE.locked()
    assert session._LOGGED_IN is True, "late logout result rewrote shared login state"


def test_nested_same_thread_is_inline_and_uses_earlier_deadline():
    outer = []

    def body():
        outer.append(threading.current_thread())
        inner = session.run(lambda: "inner", timeout=1, label="inner")
        # A nested call with an expired child budget fails without releasing the
        # outer lease; the outer caller still owns it.
        def expired_after_check():
            time.sleep(0.02)
            session.check()

        with pytest.raises(session.BaostockTimeout):
            session.run(expired_after_check, timeout=0.01, label="expired-inner")
        return inner

    assert session.run(body, timeout=2, label="outer") == "inner"
    assert len(outer) == 1


def test_other_thread_cannot_reenter_and_worker_release_is_idempotent():
    entered, release = threading.Event(), threading.Event()

    def owner():
        _wait_release(entered, release)
        return None

    worker = threading.Thread(target=lambda: session.run(owner, timeout=2, label="owner"))
    worker.start()
    try:
        assert entered.wait(2)
        result = []

        def other():
            try:
                session.run(lambda: result.append("bad"), timeout=0.2, label="other")
            except session.BaostockBusy:
                result.append("busy")

        thread = threading.Thread(target=other)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        assert result == ["busy"]
    finally:
        release.set()
        worker.join(3)
    assert not session._LEASE.locked()
