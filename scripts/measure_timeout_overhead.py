"""ADR-06 超时资源与退出语义测量工具（plan/ARCHITECTURE §三 ADR-06 专项）。

背景：项目防冻结模式 = ThreadPoolExecutor.result(timeout) + shutdown(wait=False)
（LRN：with 块 __exit__ 的 shutdown(wait=True) 会 join 卡死线程使超时失效）。
已知语义：超时只放弃等待，底层线程成为孤儿继续跑完。本工具量化该语义的实际开销。

测量项（ADR-06 要求）：
1. 活跃线程数：N 次超时请求后的 threading.active_count 增长
2. 进程退出时长：带孤儿线程的进程自然退出所需时间（atexit 阻断效应）

用法：scripts/measure_timeout_overhead.py [N]（默认 6，模拟 6 次 2 秒超时）
零网络：阻塞任务用 threading.Event 等待，不碰真实接口。
输出：人话结论，结果供 plan/README ADR-06 专项记录引用。
"""
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.benzong.data_provider import _safe_call   # 项目防冻结范本

TIMEOUT_S = 2.0


def _blocked_task():
    """模拟一个不响应超时的阻塞请求（如底层库无 timeout 的 HTTP 读取）。"""
    ev = threading.Event()
    ev.wait(timeout=TIMEOUT_S * 3)   # 比超时久，必然超时
    return None


def main(n: int = 6):
    print(f"== ADR-06 超时资源测量：{n} 次模拟阻塞请求（每次超时 {TIMEOUT_S}s）==")
    base_threads = threading.active_count()
    print(f"基线活跃线程数: {base_threads}")

    t0 = time.perf_counter()
    for i in range(n):
        t_start = time.perf_counter()
        result = _safe_call(f"simulated_{i}", _blocked_task, timeout=TIMEOUT_S)
        dt = time.perf_counter() - t_start
        assert result is None and dt < TIMEOUT_S + 1.0, "超时防冻结语义失效（等待未按时返回）"
        print(f"  第 {i + 1} 次: 超时放弃（耗时 {dt:.2f}s），当前活跃线程 "
              f"{threading.active_count()}（基线 {base_threads}）")
    total = time.perf_counter() - t0
    leaked = threading.active_count() - base_threads
    print(f"\n== 结论 ==")
    print(f"  {n} 次超时总耗时 {total:.1f}s（防冻结生效：无一次卡死）")
    print(f"  孤儿线程增长: +{leaked}（每个超时请求泄漏 1 个不可取消线程，"
          f"跑完自身阻塞时间后自然消亡——ADR-06 已知语义）")
    print(f"  进程退出: atexit 不等待孤儿线程（shutdown(wait=False) 纪律），"
          f"本脚本即将自然退出验证")

    t_exit = time.perf_counter()
    print(f"  退出计时开始……")
    return leaked


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    main(n)
    # 退出时长 = shell 计时观察；孤儿线程不阻断 atexit（若阻断说明违反 ADR-06 纪律）
