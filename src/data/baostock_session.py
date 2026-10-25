"""Process-wide non-blocking coordinator for the Baostock shared session.

The supplier keeps one socket in module globals.  All login, heartbeat, query
creation, pagination and logout work for one process must therefore run on one
leased daemon worker.  A new caller either gets that lease immediately or
degrades with ``BaostockBusy``; it never starts a second session.

The only object that releases the lease is the worker's ``finally`` block.  A
caller timeout (or an abandoned ancestor) marks the call abandoned and returns,
but the worker keeps the lease until the supplier operation actually returns.
No timeout path calls logout, resets the shared status, or starts a new login.

Nested calls on the lease-owning thread run inline and inherit the earliest
monotonic deadline.  Another thread cannot re-enter by passing the same label
or session id: it sees the lease as busy.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import math
import threading
import time
from typing import Any, Callable

from src.data.timeout_call import call_with_timeout, is_call_abandoned

logger = logging.getLogger(__name__)

LOGIN_TIMEOUT = 8.0
LOGOUT_TIMEOUT = 8.0

_LEASE = threading.Lock()
_LOCAL = threading.local()
_LOGGED_IN = False


class BaostockBusy(RuntimeError):
    """Another worker already owns the process-wide Baostock lease."""


class BaostockTimeout(TimeoutError):
    """The absolute caller budget expired; the worker may still be running."""


class BaostockAbandoned(BaostockTimeout):
    """An ancestor caller abandoned this worker; no new supplier I/O is allowed."""


class SessionCall:
    """One active lease-scoped call (or a nested inline child)."""

    __slots__ = ("deadline", "label", "parent")

    def __init__(self, deadline: float | None, label: str,
                 parent: "SessionCall | None" = None):
        self.deadline = deadline
        self.label = label
        self.parent = parent

    def check(self) -> None:
        # timeout_call owns the cross-thread ancestor stack.  Checking it here
        # is what lets an abandoned outer call stop this leased worker.
        if is_call_abandoned() or (self.parent is not None and self.parent.is_abandoned()):
            raise BaostockAbandoned(f"{self.label}: caller abandoned")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise BaostockTimeout(f"{self.label}: absolute deadline expired")

    def is_abandoned(self) -> bool:
        return is_call_abandoned() or (self.parent is not None and self.parent.is_abandoned())


def current() -> SessionCall | None:
    """Return the lease-scoped call for the current thread, if any."""
    return getattr(_LOCAL, "call", None)


def check() -> None:
    """Check deadline/abandonment before a supplier operation or publish."""
    call = current()
    if call is not None:
        call.check()


def is_logged_in() -> bool:
    return _LOGGED_IN


@contextlib.contextmanager
def _quiet_loginout():
    """Silence only the supplier loginout module's own prints.

    Do not redirect sys.stdout or builtins.print: unrelated threads must keep
    printing normally.  The swap is restored in ``finally`` and only the leased
    worker can enter login/logout.
    """
    module = None
    had_override = False
    original = None
    try:
        module = importlib.import_module("baostock.login.loginout")
        had_override = hasattr(module, "print")
        original = getattr(module, "print", None)
        module.print = lambda *args, **kwargs: None
    except Exception:
        module = None
    try:
        yield
    finally:
        if module is not None:
            if had_override:
                module.print = original
            else:
                try:
                    del module.print
                except AttributeError:
                    pass


def _login_impl() -> bool:
    global _LOGGED_IN
    import baostock as bs

    check()
    if _LOGGED_IN:
        # Synchronous heartbeat on the same worker/deadline.  Never spawn the
        # old 8-second ping worker.
        try:
            check()
            rs = bs.query_stock_basic(code="sh.600000")
            check()
            if rs is not None and result_is_healthy(rs):
                return True
        except BaostockTimeout:
            raise
        except Exception:
            pass
        # A dead connection may be replaced only while the deadline is live.
        check()
        _logout_impl()
        check()
    with _quiet_loginout():
        lg = bs.login()
    # A blocked login may return after the caller/ancestor abandoned this
    # worker.  Re-check before reading its result or writing shared login state.
    check()
    _LOGGED_IN = bool(getattr(lg, "error_code", None) == "0")
    if _LOGGED_IN:
        logger.info("Baostock登录成功（共享会话）")
    else:
        logger.warning(f"Baostock登录失败: {getattr(lg, 'error_msg', '')}")
    return _LOGGED_IN


def _logout_impl() -> bool:
    global _LOGGED_IN
    import baostock as bs

    check()
    if not _LOGGED_IN:
        return False
    with _quiet_loginout():
        bs.logout()
    # A late logout result must not rewrite shared state after abandonment.
    check()
    _LOGGED_IN = False
    return True


def result_is_healthy(rs) -> bool:
    """Drain one supplier ResultData under the current worker/deadline.

    Missing ResultData members are not healthy; a terminated error code is not
    healthy either.  Timeout/abandonment propagates to the caller.
    """
    check()
    try:
        error = rs.error_code
    except AttributeError as exc:
        raise RuntimeError("Baostock ResultData missing error_code") from exc
    check()
    if str(error) != "0":
        return False
    try:
        fields = rs.fields
        rs.next
        rs.get_row_data
    except AttributeError as exc:
        raise RuntimeError("Baostock ResultData missing iteration interface") from exc
    check()
    list(fields)
    check()
    while True:
        check()
        if not rs.next():
            break
        check()
        rs.get_row_data()
        check()
    # Baostock's ResultData can set error_code while next() returns False;
    # a pagination failure is not a healthy heartbeat.
    check()
    try:
        final_error = rs.error_code
    except AttributeError:
        return False
    check()
    return str(final_error) == "0"


def reset_connection() -> bool:
    """Reset the supplier socket on the current lease worker only."""
    if current() is None:
        raise RuntimeError("Baostock connection reset requires an active lease")
    check()
    return _logout_impl()


def ensure_login() -> bool:
    """Ensure login under the current lease, or under a short standalone lease."""
    if current() is not None:
        return _login_impl()
    try:
        return bool(run(_login_impl, timeout=LOGIN_TIMEOUT, label="baostock-login"))
    except (BaostockBusy, BaostockTimeout):
        return False


def logout(*, timeout: float = LOGOUT_TIMEOUT) -> bool:
    """Explicit close; busy requests do not touch the supplier."""
    if current() is not None:
        return _logout_impl()
    if not _LOGGED_IN:
        return False
    try:
        return bool(run(_logout_impl, timeout=timeout, label="baostock-logout"))
    except (BaostockBusy, BaostockTimeout):
        return False


def run(fn: Callable[[], Any], *, timeout: float, label: str = "baostock") -> Any:
    """Run ``fn`` on the lease-owning daemon worker and return its plain payload.

    ``BaostockBusy`` is raised before any supplier call when another worker
    holds the lease.  On caller timeout the worker is left running and keeps
    the lease; its result is discarded.  The worker's finally releases it.
    """
    budget = None if timeout is None else float(timeout)
    if budget is not None and (budget < 0 or math.isnan(budget)):
        budget = 0.0

    outer = current()
    if outer is not None:
        # Same-thread composition: no second lease and no fresh budget.
        deadline = outer.deadline
        if budget is not None:
            candidate = time.monotonic() + budget
            deadline = candidate if deadline is None else min(deadline, candidate)
        child = SessionCall(deadline, label, parent=outer)
        _LOCAL.call = child
        try:
            child.check()
            result = fn()
            child.check()
            return result
        finally:
            _LOCAL.call = outer

    if _LEASE.locked():
        logger.warning("Baostock共享会话忙，本次请求立即降级")
        raise BaostockBusy(f"{label}: shared Baostock session is busy")

    started = time.monotonic()
    deadline = None if budget is None else started + budget
    # Zero/expired budgets must not execute the callback even if a worker
    # could start before the caller notices the expired wait.
    if deadline is not None and time.monotonic() >= deadline:
        raise BaostockTimeout(f"{label}: absolute deadline expired")

    def _worker():
        if not _LEASE.acquire(blocking=False):
            return ("busy", None)
        call = SessionCall(deadline, label)
        _LOCAL.call = call
        try:
            call.check()
            result = fn()
            call.check()
            return ("ok", result)
        except BaseException as error:  # returned and re-raised as the same instance
            return ("error", error)
        finally:
            _LOCAL.call = None
            _LEASE.release()

    remaining = None if budget is None else max(0.0, deadline - time.monotonic())
    try:
        status, payload = call_with_timeout(
            _worker, timeout=remaining,  # remaining of the anchored deadline, not a fresh budget
        )
    except TimeoutError:
        logger.warning(f"Baostock共享会话调用超时: {label}")
        raise BaostockTimeout(f"{label}: absolute deadline expired") from None
    if is_call_abandoned():
        raise BaostockAbandoned(f"{label}: caller abandoned before receive")
    # A completed worker result still cannot be published after the deadline.
    if deadline is not None and time.monotonic() >= deadline:
        logger.warning(f"Baostock共享会话调用超时: {label}")
        raise BaostockTimeout(f"{label}: absolute deadline expired")
    if status == "busy":
        logger.warning("Baostock共享会话忙，本次请求立即降级")
        raise BaostockBusy(f"{label}: shared Baostock session is busy")
    if status == "error":
        raise payload
    return payload
