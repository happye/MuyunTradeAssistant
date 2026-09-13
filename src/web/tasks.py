"""web 后台任务管理（threading + task_id）

长任务（scan 4分钟/analyze 60s）后台跑，htmx 轮询查状态。
单用户本地用，全局 task dict（不考虑多用户隔离）。
"""

import threading
import uuid
from datetime import datetime
import time

_tasks: dict[str, dict] = {}
_lock = threading.Lock()
# v0.8.9.5（彻查批 A-3）：已完成任务的保留时长（超时清理防 _tasks 无界增长）
_DONE_TTL_SECONDS = 3600


def _prune_done_tasks_locked() -> None:
    """清理超时的已完成任务（须持 _lock 调用）。running 任务永不清理。

    v0.8.9.5（审查 P3）：按完成时刻（finished_ts，缺省回退 started）起算保留期——
    长任务（scan 4 分钟）完成后仍享完整保留窗。
    """
    stale = []
    for tid, t in _tasks.items():
        if t["status"] not in ("done", "error"):
            continue
        finished = t.get("finished_ts")
        if finished is None:
            finished = datetime.fromisoformat(t["started"]).timestamp()
        if time.time() - finished > _DONE_TTL_SECONDS:
            stale.append(tid)
    for tid in stale:
        _tasks.pop(tid, None)


def start_task(fn, *args, **kwargs) -> str:
    """启动后台任务，返回 task_id。fn 返回结果或抛异常。"""
    task_id = uuid.uuid4().hex[:8]
    with _lock:
        _prune_done_tasks_locked()
        _tasks[task_id] = {
            "status": "running",
            "result": None,
            "error": None,
            "started": datetime.now().isoformat(timespec="seconds"),
        }

    def _run():
        try:
            result = fn(*args, **kwargs)
            with _lock:
                _tasks[task_id]["status"] = "done"
                _tasks[task_id]["result"] = result
                _tasks[task_id]["finished_ts"] = time.time()
        except Exception as e:
            with _lock:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["error"] = f"{type(e).__name__}: {e}"
                _tasks[task_id]["finished_ts"] = time.time()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return task_id


def get_task(task_id: str):
    """查任务状态。返回 {status, result, error, started} 或 None。"""
    with _lock:
        return _tasks.get(task_id)
