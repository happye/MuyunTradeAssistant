"""web 后台任务管理（threading + task_id）

长任务（scan 4分钟/analyze 60s）后台跑，htmx 轮询查状态。
单用户本地用，全局 task dict（不考虑多用户隔离）。
"""

import threading
import uuid
from datetime import datetime

_tasks: dict[str, dict] = {}
_lock = threading.Lock()


def start_task(fn, *args, **kwargs) -> str:
    """启动后台任务，返回 task_id。fn 返回结果或抛异常。"""
    task_id = uuid.uuid4().hex[:8]
    with _lock:
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
        except Exception as e:
            with _lock:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["error"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return task_id


def get_task(task_id: str):
    """查任务状态。返回 {status, result, error, started} 或 None。"""
    with _lock:
        return _tasks.get(task_id)
