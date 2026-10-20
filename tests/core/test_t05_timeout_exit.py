"""T05-A/T05-B finite process-exit counterexamples for the three timeout helpers.

Scope:
- src/core/benzong/data_provider.py::_safe_call
- src/data/akshare_client.py::_call_with_timeout
- src/data/source_check.py::_probe_t

T05-B applies the minimal production fix (shared daemon worker + one-slot
queue); this file stays as the regression. The exit case checks three things:
the helper call itself returns fast, the child process exits within one second
after that return, and the parent's own wall-clock observation plus the exit
wait still ends before the worker's natural 8s completion (no false green from
reading a stale marker late).

T05-C adds the budget edge cases: negative or NaN budgets must raise
TimeoutError immediately (old Future.result did), while None and normal budgets
keep their old wait semantics. The exit-window rule is also covered by a small
pure function so a late parent observation can be rejected without another
subprocess case.

The explicit subprocess case is skipped by default and only runs with
MUYUN_T05_EXIT_PROBE=1. It is deliberately not executed through
plan/fusion/iteration9/review_test_runner.py: that runner blocks subprocess and
network by design, while this case needs one real child process. The child
blocks sockets and process creation itself and runs with isolated HOME/TEMP.
"""

import gc
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from inspect import signature
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
PROBE_ENV = "MUYUN_T05_EXIT_PROBE"
HELPER_BUDGET_S = 0.05
WORKER_SLEEP_S = 8.0
STARTUP_DEADLINE_S = 25.0
CALL_ELAPSED_LIMIT_S = 1.0
EXIT_DEADLINE_S = 1.0
LATE_OBSERVATION_MARGIN_S = 0.25
KILL_WAIT_S = 10.0

_CHILD_SOURCE = r'''
import json
import os
import platform
import socket
import subprocess
import sys
import time

CASE = sys.argv[1]
EVENTS = sys.argv[2]
SLEEP_S = float(sys.argv[3])
T0 = time.monotonic()


class Blocked(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    raise Blocked("T05 child forbids network and extra processes")


# Keep the Popen class intact: asyncio.windows_utils subclasses it at import.
# Only instance creation is blocked, together with shell helpers and sockets.
subprocess.Popen.__init__ = _blocked
os.system = _blocked
os.popen = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.socket.sendto = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

# pandas/platform may run one `cmd /c ver` on Windows while importing. Fix the
# read-only stdlib result before the guards so no child process is created.
platform.win32_ver = lambda *args, **kwargs: ("10", "10.0.26100", "SP0", "Multiprocessor Free")
platform._uname_cache = None


def emit(event, **fields):
    mono_s = time.monotonic()
    record = {
        "event": event,
        "at_s": round(mono_s - T0, 4),
        "mono_s": mono_s,
        "wall_s": time.time(),
    }
    record.update(fields)
    with open(EVENTS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def slow_worker():
    emit("worker_started", where="worker-thread")
    time.sleep(SLEEP_S)
    return "slow-done"


emit("import_begin")
if CASE == "safe_call":
    from src.core.benzong import data_provider
elif CASE == "call_with_timeout":
    from src.data import akshare_client
elif CASE == "probe_t":
    from src.data import source_check
elif CASE == "robust_call_timeout":
    from src.data import robust_fetch
else:
    raise SystemExit("unknown case: " + CASE)
emit("import_done")
emit("call_started")
call_t0 = time.monotonic()

if CASE == "safe_call":
    value = data_provider._safe_call("t05_safe_call", slow_worker, timeout=0.05)
    outcome = {"value_is_none": value is None, "value_repr": repr(value)}
elif CASE == "call_with_timeout":
    try:
        value = akshare_client._call_with_timeout(slow_worker, timeout=0.05)
    except TimeoutError as error:
        outcome = {"timeout_error": True, "error_type": type(error).__name__}
    else:
        outcome = {"timeout_error": False, "value_repr": repr(value)}
elif CASE == "robust_call_timeout":
    try:
        value = robust_fetch._call_timeout(slow_worker, 0.05)
    except TimeoutError as error:
        outcome = {"timeout_error": True, "error_type": type(error).__name__}
    else:
        outcome = {"timeout_error": False, "value_repr": repr(value)}
else:
    result = source_check._probe_t("t05_probe", slow_worker, timeout=0.05)
    outcome = {"ok": result.get("ok"), "error": str(result.get("error"))}

call_elapsed_s = round(time.monotonic() - call_t0, 4)
emit("returned", call_elapsed_s=call_elapsed_s, **outcome)
'''


def _read_events(path):
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _wait_for(proc, events_path, required, deadline_s):
    deadline = time.monotonic() + deadline_s
    seen = {}
    while time.monotonic() < deadline:
        for record in _read_events(events_path):
            seen[record.get("event")] = record
        if all(name in seen for name in required):
            return seen
        if proc.poll() is not None:
            break
        time.sleep(0.01)
    for record in _read_events(events_path):
        seen[record.get("event")] = record
    return seen


def _tail(path, limit=1200):
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


def _exit_window_is_safe(
    worker_started_wall_s,
    observed_wall_s,
    exit_deadline_s=EXIT_DEADLINE_S,
    margin_s=LATE_OBSERVATION_MARGIN_S,
    worker_sleep_s=WORKER_SLEEP_S,
):
    """True only if the parent's exit wait ends before the worker's natural 8s finish.

    ``observed_wall_s`` is measured by the parent when it sees the returned
    marker. A parent that reads an old marker 8s late must fail this check; two
    child-relative markers alone cannot detect that.
    """
    return observed_wall_s + exit_deadline_s + margin_s < worker_started_wall_s + worker_sleep_s


def _assert_timeout_semantics(case, record):
    if case == "safe_call":
        assert record.get("value_is_none") is True, (
            "safe_call semantic precondition failed, not the exit assertion: %r" % (record,)
        )
    elif case in ("call_with_timeout", "robust_call_timeout"):
        assert record.get("timeout_error") is True, (
            "%s semantic precondition failed, not the exit assertion: %r" % (case, record)
        )
    else:
        error = str(record.get("error") or "")
        assert record.get("ok") is False, (
            "probe_t semantic precondition failed, not the exit assertion: %r" % (record,)
        )
        assert error.startswith("超时"), (
            "probe_t timeout text precondition failed, not the exit assertion: %r" % (record,)
        )


def _stop_child(proc):
    if proc.poll() is not None:
        return "child exited before cleanup rc=%r" % (proc.returncode,)
    proc.kill()
    try:
        proc.wait(timeout=KILL_WAIT_S)
    except subprocess.TimeoutExpired:
        return "child still alive after kill and %.0fs wait" % KILL_WAIT_S
    return "child killed after marker, rc=%r" % (proc.returncode,)


def test_safe_call_preserves_normal_result():
    from src.core.benzong.data_provider import _safe_call

    for value in (7, None):
        assert _safe_call("t05_ok", lambda value=value: value, timeout=5.0) == value
    assert _safe_call("t05_args", lambda a, b=0: a + b, 2, b=3, timeout=5.0) == 5


def test_safe_call_swallows_original_exception():
    from src.core.benzong.data_provider import _safe_call

    def boom():
        raise ValueError("t05-original-error")

    assert _safe_call("t05_boom", boom, timeout=5.0) is None


def test_safe_call_timeout_returns_none(caplog):
    from src.core.benzong.data_provider import _safe_call

    with caplog.at_level(logging.WARNING, logger="src.core.benzong.data_provider"):
        assert _safe_call("t05_slow", lambda: time.sleep(0.3) or "late", timeout=0.05) is None
    assert any(
        "已停止等待，底层调用可能仍在运行" in record.getMessage() for record in caplog.records
    )


def test_call_with_timeout_preserves_normal_result():
    from src.data.akshare_client import _call_with_timeout

    for value in (7, None):
        assert _call_with_timeout(lambda value=value: value, timeout=5.0) == value
    assert _call_with_timeout(lambda a, b, *, c: (a, b, c), 1, 2, c=3, timeout=5.0) == (1, 2, 3)


def test_call_with_timeout_propagates_original_exception():
    from src.data.akshare_client import _call_with_timeout

    def boom():
        raise ValueError("t05-original-error")

    with pytest.raises(ValueError, match="t05-original-error"):
        _call_with_timeout(boom, timeout=5.0)


def test_call_with_timeout_raises_timeout_error():
    from src.data.akshare_client import _call_with_timeout

    with pytest.raises(TimeoutError):
        _call_with_timeout(lambda: time.sleep(0.3) or "late", timeout=0.05)


def test_probe_t_ok_payload_and_legal_none():
    from src.data.source_check import _probe_t

    payload = _probe_t("t05_ok", lambda: {"v": 1}, timeout=5.0)
    assert payload["ok"] is True and payload["detail"] == {"v": 1} and payload["error"] is None
    blank = _probe_t("t05_none", lambda: None, timeout=5.0)
    assert blank["ok"] is True and blank["detail"] is None and blank["error"] is None


def test_probe_t_original_failure_dict():
    from src.data.source_check import _probe_t

    def boom():
        raise ValueError("boom")

    payload = _probe_t("t05_boom", boom, timeout=5.0)
    assert payload["ok"] is False
    assert payload["error"].startswith("ValueError: boom")
    assert payload["hint"]


def test_probe_t_timeout_returns_not_ok():
    from src.data.source_check import _probe_t

    payload = _probe_t("t05_slow", lambda: time.sleep(0.3) or "late", timeout=0.05)
    assert payload["ok"] is False and payload["detail"] is None
    assert payload["error"].startswith("超时")
    assert "已停止等待，底层调用可能仍在运行" in payload["error"]


class _BaseBoom(BaseException):
    pass


def test_shared_timeout_call_passes_arguments_and_legal_none():
    from src.data.timeout_call import call_with_timeout

    def combine(a, b, *, c):
        return (a, b, c)

    assert call_with_timeout(combine, 1, 2, c=3, timeout=5.0) == (1, 2, 3)
    assert call_with_timeout(lambda: None, timeout=5.0) is None


def test_shared_timeout_call_rethrows_same_base_exception_instance():
    from src.data.timeout_call import call_with_timeout

    boom = _BaseBoom("t05-base")

    def raiser():
        raise boom

    with pytest.raises(_BaseBoom) as info:
        call_with_timeout(raiser, timeout=5.0)
    assert info.value is boom


@pytest.mark.parametrize(
    "budget",
    [-1.0, -0.001, float("nan"), 0.0],
    ids=["negative-int", "negative-fraction", "nan", "zero"],
)
def test_nonpositive_or_nan_budget_times_out_immediately(budget):
    from src.data.timeout_call import call_with_timeout

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        call_with_timeout(lambda: time.sleep(0.25) or "late", timeout=budget)
    elapsed = time.monotonic() - started
    assert elapsed < 0.2, "budget %r did not fail immediately (%.3fs)" % (budget, elapsed)


def test_none_budget_returns_completed_value():
    from src.data.timeout_call import call_with_timeout

    assert call_with_timeout(lambda: "ok", timeout=None) == "ok"


def test_exit_window_rejects_late_parent_observation():
    assert _exit_window_is_safe(1000.0, 1001.0)
    assert _exit_window_is_safe(1000.0, 1006.0)
    assert _exit_window_is_safe(1005.0, 1005.5)  # import time does not consume the worker window
    assert not _exit_window_is_safe(1000.0, 1006.8)
    assert not _exit_window_is_safe(1000.0, 1008.0)


@pytest.mark.parametrize("case", ["safe_call", "call_with_timeout", "probe_t"])
def test_wrappers_keep_base_exception_identity(case):
    boom = _BaseBoom("t05-base")

    def raiser():
        raise boom

    with pytest.raises(_BaseBoom) as info:
        if case == "safe_call":
            from src.core.benzong.data_provider import _safe_call

            _safe_call("t05_base", raiser, timeout=5.0)
        elif case == "call_with_timeout":
            from src.data.akshare_client import _call_with_timeout

            _call_with_timeout(raiser, timeout=5.0)
        else:
            from src.data.source_check import _probe_t

            _probe_t("t05_base", raiser, timeout=5.0)
    assert info.value is boom


def test_late_result_does_not_leak_into_next_call():
    from src.data.timeout_call import call_with_timeout

    done = threading.Event()

    def slow_late():
        time.sleep(0.25)
        done.set()
        return "late"

    with pytest.raises(TimeoutError):
        call_with_timeout(slow_late, timeout=0.05)
    assert done.wait(2.0), "late worker did not finish"
    assert call_with_timeout(lambda: "fresh", timeout=1.0) == "fresh"


def test_completed_late_payload_is_not_globally_retained():
    from src.data.timeout_call import call_with_timeout

    refs = {}

    class Payload:
        pass

    def slow_payload():
        payload = Payload()
        refs["ref"] = weakref.ref(payload)
        time.sleep(0.2)
        return payload

    with pytest.raises(TimeoutError):
        call_with_timeout(slow_payload, timeout=0.05)

    deadline = time.monotonic() + 2.0
    while "ref" not in refs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "ref" in refs, "late worker never started"

    while time.monotonic() < deadline and refs["ref"]() is not None:
        gc.collect()
        time.sleep(0.02)
    assert refs["ref"]() is None, "late payload still referenced after worker completion"


@pytest.mark.parametrize("case", ["safe_call", "call_with_timeout", "probe_t"])
def test_wrappers_invoke_fn_once_on_timeout(case):
    calls = {"n": 0}

    def slow():
        calls["n"] += 1
        time.sleep(0.25)
        return "late"

    if case == "safe_call":
        from src.core.benzong.data_provider import _safe_call

        assert _safe_call("t05_once", slow, timeout=0.05) is None
    elif case == "call_with_timeout":
        from src.data.akshare_client import _call_with_timeout

        with pytest.raises(TimeoutError):
            _call_with_timeout(slow, timeout=0.05)
    else:
        from src.data.source_check import _probe_t

        assert _probe_t("t05_once", slow, timeout=0.05)["ok"] is False
    time.sleep(0.3)
    assert calls["n"] == 1


def test_retry_backoff_attempt_count_unchanged(monkeypatch):
    from src.data import akshare_client

    attempts = {"n": 0}

    def slow():
        attempts["n"] += 1
        time.sleep(0.2)
        return "late"

    monkeypatch.setattr(akshare_client.random, "uniform", lambda a, b: 0.0)
    result = akshare_client.AKShareClient._retry_with_backoff(
        slow, max_retries=2, base_delay=0, timeout=0.01
    )
    assert result is None
    assert attempts["n"] == 2
    time.sleep(0.25)


def test_safe_call_fast_fail_breaker_after_timeout(monkeypatch):
    from src.core.benzong import data_provider

    monkeypatch.setenv("MUYUN_FAST_FAIL", "1")
    monkeypatch.delenv("MUYUN_SKIP_SOURCES", raising=False)
    calls = {"n": 0}

    def slow():
        calls["n"] += 1
        time.sleep(0.25)
        return "late"

    name = "t05_breaker_999999"
    key = data_provider._breaker_key(name)
    data_provider._BROKEN_SOURCES.discard(key)
    try:
        assert data_provider._safe_call(name, slow, timeout=0.05) is None
        assert data_provider._safe_call(name, lambda: "fresh", timeout=5.0) is None
        assert calls["n"] == 1
        assert key in data_provider._BROKEN_SOURCES
    finally:
        data_provider._BROKEN_SOURCES.discard(key)
        time.sleep(0.25)


def test_default_timeout_budgets_unchanged():
    from src.core.benzong import data_provider
    from src.data import akshare_client, source_check

    assert akshare_client.DEFAULT_TIMEOUT == 30.0
    assert data_provider.DATA_CALL_TIMEOUT == 30.0
    assert signature(akshare_client._call_with_timeout).parameters["timeout"].default == 30.0
    assert signature(data_provider._safe_call).parameters["timeout"].default is None
    assert signature(source_check._probe_t).parameters["timeout"].default == 20.0


@pytest.mark.skipif(
    os.environ.get(PROBE_ENV) != "1",
    reason="requires explicit MUYUN_T05_EXIT_PROBE=1 isolated child-process run",
)
@pytest.mark.parametrize("case", ["safe_call", "call_with_timeout", "probe_t", "robust_call_timeout"])
def test_helper_returns_before_process_exit_deadline(case):
    """Helper returns fast, then the interpreter must exit within EXIT_DEADLINE_S."""
    if not VENV_PYTHON.is_file():
        pytest.fail("project venv interpreter missing: %s" % VENV_PYTHON)

    sandbox = Path(tempfile.mkdtemp(prefix="muyun-t05-exit-%s-" % case))
    home = sandbox / "home"
    temp = sandbox / "temp"
    appdata = sandbox / "appdata"
    localappdata = sandbox / "localappdata"
    for directory in (home, temp, appdata, localappdata):
        directory.mkdir()
    events_path = sandbox / "events.jsonl"
    stdout_path = sandbox / "child_stdout.txt"
    stderr_path = sandbox / "child_stderr.txt"
    child_script = sandbox / "child_probe.py"
    child_script.write_text(_CHILD_SOURCE, encoding="utf-8")

    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONSTARTUP", "MUYUN_FAST_FAIL",
                "MUYUN_SKIP_SOURCES", "MUYUN_DATA_CALL_TIMEOUT", "MUYUN_TESTS_REAL_HOME"):
        env.pop(key, None)
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TEMP": str(temp),
        "TMP": str(temp),
        "TMPDIR": str(temp),
        "APPDATA": str(appdata),
        "LOCALAPPDATA": str(localappdata),
        "XDG_CACHE_HOME": str(temp),
        "MPLCONFIGDIR": str(temp),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })

    stdout_handle = open(stdout_path, "wb")
    stderr_handle = open(stderr_path, "wb")
    command = [str(VENV_PYTHON), "-B", str(child_script), case, str(events_path), str(WORKER_SLEEP_S)]
    spawn_wall = time.time()
    proc = subprocess.Popen(
        command,
        cwd=str(sandbox),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=stderr_handle,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    exit_after = None
    try:
        seen = _wait_for(
            proc, events_path, ("call_started", "worker_started", "returned"), STARTUP_DEADLINE_S
        )
        returned_observed_wall = time.time()
        missing = [
            name
            for name in ("import_done", "call_started", "worker_started", "returned")
            if name not in seen
        ]
        assert not missing, (
            "fixture/startup failed before exit assertion, missing=%r rc=%r stdout=%r stderr=%r"
            % (missing, proc.poll(), _tail(stdout_path), _tail(stderr_path))
        )
        assert seen["call_started"]["at_s"] >= seen["import_done"]["at_s"]
        assert seen["worker_started"]["at_s"] <= seen["returned"]["at_s"]
        call_elapsed = seen["returned"].get("call_elapsed_s")
        assert call_elapsed is not None and call_elapsed <= CALL_ELAPSED_LIMIT_S, (
            "CALL DURATION ASSERTION: helper %r blocked %.3fs (>%.1fs), so the exit check would "
            "not prove non-blocking; events=%r"
            % (
                case,
                -1.0 if call_elapsed is None else call_elapsed,
                CALL_ELAPSED_LIMIT_S,
                _read_events(events_path),
            )
        )
        _assert_timeout_semantics(case, seen["returned"])
        worker_started_wall = seen["worker_started"].get("wall_s")
        returned_wall = seen["returned"].get("wall_s")
        assert worker_started_wall is not None and returned_wall is not None, (
            "fixture missing child wall-clock timestamps: %r" % (_read_events(events_path),)
        )
        assert worker_started_wall >= spawn_wall - 1.0, (
            "fixture wall clock moved backwards before worker start: spawn=%r worker=%r"
            % (spawn_wall, worker_started_wall)
        )
        assert returned_observed_wall >= returned_wall - 0.5, (
            "fixture parent observation predates child marker: observed=%r returned=%r"
            % (returned_observed_wall, returned_wall)
        )
        assert _exit_window_is_safe(worker_started_wall, returned_observed_wall), (
            "LATE OBSERVATION: worker started at wall %.3f, parent observed returned at wall %.3f; "
            "the exit window (%.1fs + %.2fs margin) must end before the worker's natural finish at "
            "wall %.3f; events=%r"
            % (
                worker_started_wall,
                returned_observed_wall,
                EXIT_DEADLINE_S,
                LATE_OBSERVATION_MARGIN_S,
                worker_started_wall + WORKER_SLEEP_S,
                _read_events(events_path),
            )
        )

        returned_observed_at = time.monotonic()
        try:
            proc.wait(timeout=EXIT_DEADLINE_S)
            exit_after = time.monotonic() - returned_observed_at
        except subprocess.TimeoutExpired:
            exit_after = None
    finally:
        cleanup_note = _stop_child(proc)
        stdout_handle.close()
        stderr_handle.close()
        stdout_text = _tail(stdout_path)
        stderr_text = _tail(stderr_path)

    if exit_after is None:
        pytest.fail(
            "EXIT ASSERTION: helper %r returned but the child process was still alive %.1fs later. "
            "events=%r cleanup=%s rc=%r stdout=%r stderr=%r"
            % (case, EXIT_DEADLINE_S, _read_events(events_path), cleanup_note,
               proc.returncode, stdout_text, stderr_text)
        )

    assert proc.returncode == 0, (
        "fixture/child exited nonzero after helpers; rc=%r stderr=%r cleanup=%s"
        % (proc.returncode, stderr_text, cleanup_note)
    )
    assert exit_after <= EXIT_DEADLINE_S
    print(
        "T05_EXIT_OK case=%s import_done=%.3fs call_started=%.3fs returned=%.3fs "
        "call_elapsed=%.3fs exit_after=%.3fs worker_wall=%.3f observed_wall=%.3f "
        "window_margin=%.3fs cleanup=%s"
        % (
            case,
            seen["import_done"]["at_s"],
            seen["call_started"]["at_s"],
            seen["returned"]["at_s"],
            call_elapsed,
            exit_after,
            worker_started_wall,
            returned_observed_wall,
            (worker_started_wall + WORKER_SLEEP_S)
            - (returned_observed_wall + EXIT_DEADLINE_S),
            cleanup_note,
        )
    )
