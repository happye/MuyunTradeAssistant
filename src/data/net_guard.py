"""全局网络防护层（v0.8.9.1，ISS-088）：按源节流 + 失败熔断。

背景：项目此前各数据模块独立请求，无全局节奏控制——批量场景（l 多代码/la/
bz scan）对同一数据源的突发连发是触发反爬限流的主要形态。本模块提供两个
进程级原语，供数据调用点按"数据源 key"使用：

- RateLimiter：同 key 两次调用之间的最小间隔 + ±JITTER 随机抖动，
  打散请求节奏（固定 2^n 退避已有，这里补"请求前节流"）。
- CircuitBreaker：连续 threshold 次失败 → 熔断 cooldown 秒（期间 allow()
  返回 False，调用方直接跳到下一个数据源，不再撞被封的源加重封禁）；
  冷却结束后放行一次试探（half-open），成功即复位。

设计约定：
- 时钟可注入（clock/sleeper 参数），测试不真睡；
- 线程安全（批量在主线程串行，但 chat/web 可能并发）；
- 失败语义由调用方定义（网络异常/超时/空结果都算 failure，自行调用
  record_failure），本模块不捕获异常。

用法：
    from src.data.net_guard import rate_limiter, circuit_breaker
    if not circuit_breaker.allow("sina_batch"):
        return None  # 熔断中，直接换源
    rate_limiter.wait("sina_batch")
    try:
        ...请求...
        circuit_breaker.record_success("sina_batch")
    except Exception:
        circuit_breaker.record_failure("sina_batch")
        raise
"""

import random
import threading
import time
from typing import Callable, Dict, Optional


class RateLimiter:
    """按 key 的最小请求间隔节流器（线程安全）。

    Args:
        intervals: key -> 最小间隔秒；未登记的 key 用 default_interval
        clock: 单调时钟（默认 time.monotonic，测试可注入）
        sleeper: 睡眠函数（默认 time.sleep，测试可注入）
        jitter: 每次间隔的随机抖动比例（±jitter），打散雷同节奏
    """

    JITTER = 0.2

    def __init__(
        self,
        intervals: Optional[Dict[str, float]] = None,
        default_interval: float = 0.15,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._intervals = dict(intervals or {})
        self._default = default_interval
        self._clock = clock
        self._sleeper = sleeper
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str) -> None:
        """必要时睡眠，保证同 key 两次 wait 之间至少隔 interval±jitter 秒。"""
        interval = self._intervals.get(key, self._default)
        with self._lock:
            now = self._clock()
            last = self._last.get(key)
            jittered = interval * (1.0 + random.uniform(-self.JITTER, self.JITTER))
            next_allowed = (last + jittered) if last is not None else now
            delay = next_allowed - now
            self._last[key] = max(now, next_allowed)
        if delay > 0:
            self._sleeper(delay)


class CircuitBreaker:
    """按 key 的连续失败熔断器（线程安全）。

    状态机：closed（正常）--连续 threshold 次 failure--> open（熔断 cooldown 秒）
    --冷却结束--> half-open（allow 放行一次试探）--成功--> closed / --失败--> open。
    """

    def __init__(
        self,
        threshold: int = 3,
        cooldown: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._threshold = max(1, int(threshold))
        self._cooldown = max(0.0, float(cooldown))
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: Dict[str, int] = {}
        self._opened_at: Dict[str, float] = {}

    def allow(self, key: str) -> bool:
        """是否放行该源的请求。open 期间返回 False（调用方换下一个源）。"""
        with self._lock:
            opened_at = self._opened_at.get(key)
            if opened_at is None:
                return True
            if self._clock() - opened_at >= self._cooldown:
                # half-open：放行一次试探（清 open 标记，失败会重新 open）
                del self._opened_at[key]
                return True
            return False

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key] = self._failures.get(key, 0) + 1
            if self._failures[key] >= self._threshold:
                self._opened_at[key] = self._clock()
                self._failures.pop(key, None)


# 进程级单例：各数据调用点共享同一份节流/熔断状态
# 间隔按"源"区分：sina/东财 HTTP 接口从严（0.4s），baostock 是持久连接查询从宽
rate_limiter = RateLimiter(intervals={
    "sina": 0.4,
    "eastmoney": 0.4,
    "baostock": 0.05,
})
circuit_breaker = CircuitBreaker(threshold=3, cooldown=120.0)
