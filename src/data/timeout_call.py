"""单次超时调用：三个数据源包装器共用（T05-B）。

契约：
- ``fn`` 在独立 daemon 线程执行；调用方最多等待 ``timeout`` 秒。
- ``timeout`` 为 None 时无限等待；负数或 NaN 预算按 0 处理，立即抛
  ``TimeoutError``（与旧 ``Future.result`` 行为一致）；正常正数预算不变。
- 到点抛 ``TimeoutError``；worker 可能继续运行，迟到结果只进本次调用的队列并被丢弃。
- 正常完成时原样返回 ``fn`` 的返回值，``None`` 是合法值，不是哨兵。
- ``fn`` 抛出的异常按原实例交回调用线程重抛，包括非 ``Exception`` 的 ``BaseException``。
- 每次调用独立 ``queue.Queue(maxsize=1)``；不使用 executor，不 join，不做全局登记。

限制：
- 线程不能被强杀；``fn`` 已发起的网络/socket、文件写入、日志等副作用可能在调用方
  放弃后继续发生，本函数不作无副作用承诺。
- ``maxsize=1`` 只界住单次调用的结果对象，不是线程数或总内存上界。
"""
import queue
import threading
import time

_OK = "ok"
_ERROR = "error"


def call_with_timeout(fn, *args, timeout: float, **kwargs):
    """Run ``fn(*args, **kwargs)`` with a hard wait budget.

    ``None`` waits indefinitely. Negative and NaN budgets are normalized to
    zero so they raise ``TimeoutError`` immediately, matching the old
    ``Future.result`` behavior. A completed value still wins at zero. Exceptions
    raised by ``fn`` are re-raised as the same instance in the calling thread.
    No worker is joined and no result is reused across calls.
    """
    budget = None if timeout is None else float(timeout)
    if budget is not None and (budget < 0 or budget != budget):
        budget = 0.0
    slot = queue.Queue(maxsize=1)

    def _run():
        try:
            item = (_OK, fn(*args, **kwargs))
        except BaseException as error:
            item = (_ERROR, error)
        slot.put(item)

    worker = threading.Thread(target=_run, name="muyun-timeout-call", daemon=True)
    worker.start()
    try:
        status, payload = slot.get(timeout=budget)
    except queue.Empty:
        raise TimeoutError("timeout_call expired") from None
    if status == _ERROR:
        raise payload
    return payload


class ParallelTimeout(TimeoutError):
    """``run_parallel_with_deadline`` 总预算到点；``completed`` 为已完成的任务数。"""

    def __init__(self, completed: int, total: int):
        super().__init__(f"parallel deadline expired ({completed}/{total} done)")
        self.completed = completed
        self.total = total


def run_parallel_with_deadline(fns, *, max_workers: int, timeout: float):
    """并行跑无参可调用对象，共用一个总等待预算（T05-D）。

    契约：
    - 起 ``min(max_workers, len(fns))`` 个 daemon 线程从任务队列取活，不使用 executor，
      不 join，所以放弃等待后不会拖住解释器退出。
    - 全部完成时按输入顺序返回结果列表。
    - 总预算到点抛 ``ParallelTimeout``（``TimeoutError`` 子类，带 ``completed``）；
      ``None`` 无限等待，负数或 NaN 按 0 处理（与 ``call_with_timeout`` 一致）。
    - 任一任务抛出的异常按原实例在调用线程重抛（先到先抛）。
    - 每次调用独立队列；迟到结果被丢弃。放弃后每个 worker 至多再开始一个任务
      （放弃标记与取任务之间的窗口），其余未取走的任务不再开始。

    限制同 ``call_with_timeout``：线程不能被强杀，已开始任务的副作用可能继续。
    """
    fns = list(fns)
    total = len(fns)
    if total == 0:
        return []
    budget = None if timeout is None else float(timeout)
    if budget is not None and (budget < 0 or budget != budget):
        budget = 0.0
    tasks = queue.Queue()
    for index, fn in enumerate(fns):
        tasks.put((index, fn))
    done = queue.Queue()
    abandoned = threading.Event()

    def _work():
        while not abandoned.is_set():
            try:
                index, fn = tasks.get_nowait()
            except queue.Empty:
                return
            try:
                item = (index, _OK, fn())
            except BaseException as error:
                item = (index, _ERROR, error)
            done.put(item)

    for _ in range(max(1, min(int(max_workers), total))):
        threading.Thread(target=_work, name="muyun-parallel-call", daemon=True).start()

    deadline = None if budget is None else time.monotonic() + budget
    results = [None] * total
    completed = 0
    try:
        while completed < total:
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                index, status, payload = done.get(timeout=wait)
            except queue.Empty:
                raise ParallelTimeout(completed, total) from None
            if status == _ERROR:
                raise payload
            results[index] = payload
            completed += 1
    finally:
        if completed < total:
            abandoned.set()
    return results
