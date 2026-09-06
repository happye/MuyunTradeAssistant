# -*- coding: utf-8 -*-
"""ISS-088 网络防护层单测：RateLimiter 节流 + CircuitBreaker 熔断（时钟注入，不真睡）。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from src.data.net_guard import CircuitBreaker, RateLimiter


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_rate_limiter_first_call_no_sleep():
    clock, sleeps = FakeClock(), []
    rl = RateLimiter(intervals={"sina": 0.4}, clock=clock, sleeper=sleeps.append)
    rl.wait("sina")
    assert sleeps == []


def test_rate_limiter_second_call_sleeps_near_interval():
    clock, sleeps = FakeClock(), []
    rl = RateLimiter(intervals={"sina": 0.4}, clock=clock, sleeper=sleeps.append)
    rl.wait("sina")
    clock.advance(0.1)
    rl.wait("sina")
    # 间隔 0.4 ± 20% 抖动 → 应睡 [0.22, 0.38] 之间（0.4*1.2-0.1 到 0.4*0.8-0.1）
    assert len(sleeps) == 1
    assert 0.22 - 1e-9 <= sleeps[0] <= 0.38 + 1e-9


def test_rate_limiter_keys_independent():
    clock, sleeps = FakeClock(), []
    rl = RateLimiter(intervals={"sina": 0.4}, default_interval=0.0, clock=clock, sleeper=sleeps.append)
    rl.wait("sina")
    rl.wait("other")   # 未登记 key 用 default_interval=0 → 不睡
    rl.wait("sina")    # 同 key 连发 → 睡
    assert sleeps and len(sleeps) == 1


def test_breaker_opens_after_consecutive_failures():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=3, cooldown=120.0, clock=clock)
    assert cb.allow("sina_batch")
    cb.record_failure("sina_batch")
    cb.record_failure("sina_batch")
    assert cb.allow("sina_batch"), "未达阈值不应熔断"
    cb.record_failure("sina_batch")
    assert not cb.allow("sina_batch"), "连续3次失败应熔断"


def test_breaker_half_open_after_cooldown():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=2, cooldown=120.0, clock=clock)
    cb.record_failure("x")
    cb.record_failure("x")
    assert not cb.allow("x")
    clock.advance(121.0)
    assert cb.allow("x"), "冷却结束应放行试探（half-open）"
    cb.record_success("x")
    assert cb.allow("x"), "试探成功应复位"


def test_breaker_success_resets_failure_count():
    cb = CircuitBreaker(threshold=3, cooldown=60.0)
    cb.record_failure("x")
    cb.record_failure("x")
    cb.record_success("x")   # 成功清零
    cb.record_failure("x")
    cb.record_failure("x")
    assert cb.allow("x"), "成功后失败计数应从零起算"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
