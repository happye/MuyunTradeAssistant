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
