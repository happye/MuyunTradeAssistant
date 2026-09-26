"""RAG 线程上限硬件保护测试（2026-09-26，用户 14700 缩肛叮嘱）

锁死语义：
1. MUYUN_RAG_MAX_THREADS 未设 → 零动作（默认行为不变，不 import torch）
2. 设 N → torch.set_num_threads(N)（+interop 尽力而为）
3. 非法值 → 告警不抛（加载不被阻断）

跑法：pytest tests/rag/test_thread_cap.py -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import torch

from src.rag import embedding as embed


def test_thread_cap_unset_is_noop(monkeypatch):
    monkeypatch.delenv("MUYUN_RAG_MAX_THREADS", raising=False)
    calls = []
    monkeypatch.setattr(torch, "set_num_threads", lambda n: calls.append(n))
    embed._apply_thread_cap()
    assert calls == []  # 未设环境变量零动作


def test_thread_cap_limits_torch_threads(monkeypatch):
    monkeypatch.setenv("MUYUN_RAG_MAX_THREADS", "4")
    calls = []
    monkeypatch.setattr(torch, "set_num_threads", lambda n: calls.append(("threads", n)))
    monkeypatch.setattr(torch, "set_num_interop_threads", lambda n: calls.append(("interop", n)))
    embed._apply_thread_cap()
    assert ("threads", 4) in calls
    assert ("interop", 4) in calls


def test_thread_cap_floor_at_one(monkeypatch):
    monkeypatch.setenv("MUYUN_RAG_MAX_THREADS", "0")
    calls = []
    monkeypatch.setattr(torch, "set_num_threads", lambda n: calls.append(n))
    embed._apply_thread_cap()
    assert calls == [1]  # 下限 1（0 核无意义）


def test_thread_cap_bad_value_warns_not_raises(monkeypatch):
    monkeypatch.setenv("MUYUN_RAG_MAX_THREADS", "abc")
    embed._apply_thread_cap()  # 不抛——加载不被阻断
