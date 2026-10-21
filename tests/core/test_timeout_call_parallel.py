"""T05-D-2 unit tests for src/data/timeout_call.py::run_parallel_with_deadline.

Contract under test (see the function docstring and iteration10 DELIVERY_PLAN):
- results come back in input order, ``None`` is a legal result, empty input
  returns ``[]`` without starting threads;
- a task exception is re-raised as the same instance (BaseException included);
- the total budget expires with ``ParallelTimeout`` (a ``TimeoutError`` carrying
  ``completed``/``total``); negative or NaN budgets expire at once, ``None``
  waits for everything;
- at most ``max_workers`` tasks run at the same time, in daemon threads;
- after the caller gives up no further task is started, and late results never
  leak into a later call.

No network, no subprocess, no patching of the code under test. Every blocking
task waits on a ``threading.Event`` with an upper bound and is released in a
``finally`` so no thread outlives its test; no single sleep exceeds 0.5s.
"""
import math
import threading
import time

import pytest

from src.data import timeout_call
from src.data.timeout_call import ParallelTimeout, run_parallel_with_deadline

_CAP = 5.0


def test_results_follow_input_order_not_completion_order():
    delays = [0.3, 0.15, 0.0, 0.05]
    finish_order = []
    lock = threading.Lock()

    def make(index, delay):
        def task():
            time.sleep(delay)
            with lock:
                finish_order.append(index)
            return f"r{index}"

        return task

    fns = [make(i, d) for i, d in enumerate(delays)]
    result = run_parallel_with_deadline(fns, max_workers=4, timeout=_CAP)

    assert result == ["r0", "r1", "r2", "r3"]
    assert finish_order[0] != 0 and finish_order[-1] == 0  # really finished out of order


def test_none_return_value_is_legal():
    fns = [lambda: None, lambda: 1, lambda: None]
    assert run_parallel_with_deadline(fns, max_workers=2, timeout=_CAP) == [None, 1, None]


def test_accepts_any_iterable_of_callables():
    result = run_parallel_with_deadline((lambda i=i: i * i for i in range(4)), max_workers=2, timeout=_CAP)
    assert result == [0, 1, 4, 9]


def test_empty_input_returns_empty_list_without_starting_threads(monkeypatch):
    class _NoThreads:
        def __init__(self, *args, **kwargs):
            raise AssertionError("no thread may be created for empty input")

    monkeypatch.setattr(timeout_call.threading, "Thread", _NoThreads)

    assert run_parallel_with_deadline([], max_workers=3, timeout=_CAP) == []
    assert run_parallel_with_deadline(iter(()), max_workers=3, timeout=0.0) == []


def test_task_exception_is_reraised_as_same_instance():
    error = ValueError("t05-d-original")

    def boom():
        raise error

    with pytest.raises(ValueError) as info:
        run_parallel_with_deadline([lambda: 1, boom, lambda: 3], max_workers=3, timeout=_CAP)

    assert info.value is error


def test_base_exception_subclass_is_reraised_as_same_instance():
    class _Fatal(BaseException):
        pass

    error = _Fatal("t05-d-base")

    def boom():
        raise error

    with pytest.raises(_Fatal) as info:
        run_parallel_with_deadline([boom, lambda: 2], max_workers=2, timeout=_CAP)

    assert info.value is error


def test_total_budget_raises_parallel_timeout_with_counts():
    release = threading.Event()

    def blocker():
        release.wait(_CAP)
        return "late"

    fns = [lambda: 1, lambda: 2, lambda: 3, blocker]
    start = time.monotonic()
    try:
        with pytest.raises(ParallelTimeout) as info:
            run_parallel_with_deadline(fns, max_workers=4, timeout=0.3)
        elapsed = time.monotonic() - start
    finally:
        release.set()

    error = info.value
    assert isinstance(error, TimeoutError)
    assert (error.completed, error.total) == (3, 4)
    assert "3/4" in str(error)
    assert 0.25 <= elapsed < 2.0  # waited about the budget, not the blocker's _CAP


def test_budget_is_shared_across_tasks_not_per_task():
    # Two sequential 0.5s tasks on one worker need 1.0s; a 0.7s total budget
    # must expire even though each single task fits inside it.
    fns = [lambda: time.sleep(0.5), lambda: time.sleep(0.5)]
    start = time.monotonic()
    with pytest.raises(ParallelTimeout) as info:
        run_parallel_with_deadline(fns, max_workers=1, timeout=0.7)
    elapsed = time.monotonic() - start

    assert info.value.total == 2 and info.value.completed <= 1
    assert elapsed < 1.0  # running both sequentially would take at least 1.0s


@pytest.mark.parametrize("budget", [-1.0, -0.001, float("nan"), -math.inf])
def test_negative_or_nan_budget_times_out_immediately(budget):
    release = threading.Event()
    fns = [lambda: release.wait(_CAP), lambda: release.wait(_CAP)]
    start = time.monotonic()
    try:
        with pytest.raises(ParallelTimeout) as info:
            run_parallel_with_deadline(fns, max_workers=2, timeout=budget)
        elapsed = time.monotonic() - start
    finally:
        release.set()

    assert (info.value.completed, info.value.total) == (0, 2)
    assert elapsed < 0.3


def test_none_budget_waits_for_all_tasks():
    def slow(value):
        def task():
            time.sleep(0.2)
            return value

        return task

    start = time.monotonic()
    result = run_parallel_with_deadline([slow("a"), slow("b")], max_workers=2, timeout=None)
    elapsed = time.monotonic() - start

    assert result == ["a", "b"]
    assert elapsed >= 0.18


def test_concurrency_is_capped_by_max_workers():
    lock = threading.Lock()
    state = {"running": 0, "peak": 0}

    def task():
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.08)
        with lock:
            state["running"] -= 1
        return True

    result = run_parallel_with_deadline([task] * 6, max_workers=2, timeout=_CAP)

    assert result == [True] * 6
    assert 2 <= state["peak"] <= 2


def test_max_workers_larger_than_tasks_still_runs_all_in_parallel():
    barrier = threading.Barrier(3, timeout=_CAP)  # only passes if all 3 run concurrently
    result = run_parallel_with_deadline([barrier.wait] * 3, max_workers=16, timeout=_CAP)
    assert sorted(result) == [0, 1, 2]


def test_worker_threads_are_daemon():
    seen = []

    def task():
        thread = threading.current_thread()
        seen.append((thread.daemon, thread is not threading.main_thread()))
        return None

    run_parallel_with_deadline([task, task, task], max_workers=2, timeout=_CAP)

    assert seen == [(True, True)] * 3


def test_no_new_task_starts_after_caller_gives_up():
    first_started = threading.Event()
    release = threading.Event()
    later_runs = []

    def first():
        first_started.set()
        release.wait(_CAP)
        return "first"

    def later():
        later_runs.append(1)
        return "later"

    try:
        with pytest.raises(ParallelTimeout) as info:
            run_parallel_with_deadline([first, later, later], max_workers=1, timeout=0.1)
        assert first_started.is_set()
        assert (info.value.completed, info.value.total) == (0, 3)
    finally:
        release.set()

    time.sleep(0.3)  # give an (incorrect) surviving worker time to pick up more work
    assert later_runs == []


def test_late_result_does_not_leak_into_next_call():
    release = threading.Event()
    finished = threading.Event()

    def late():
        release.wait(_CAP)
        finished.set()
        return "stale-from-call-1"

    try:
        with pytest.raises(ParallelTimeout):
            run_parallel_with_deadline([late, lambda: "fast-1"], max_workers=2, timeout=0.1)
    finally:
        release.set()
    assert finished.wait(_CAP)  # the late result is now produced, after the caller left

    second = run_parallel_with_deadline([lambda: "n0", lambda: "n1"], max_workers=2, timeout=_CAP)

    assert second == ["n0", "n1"]
    assert "stale-from-call-1" not in second and "fast-1" not in second
