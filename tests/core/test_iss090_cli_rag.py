# -*- coding: utf-8 -*-
"""ISS-090 CLI 分析路径接 RAG 回归：门控/单次提示/失败记忆/四处接线/scanner懒接线

跑法：pytest tests/core/test_iss090_cli_rag.py 或直接 python 执行。
"""
import contextlib
import io
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import src.rag.service as svc_mod


@contextlib.contextmanager
def _swap(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


@contextlib.contextmanager
def _quiet():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield


import src.rag.service as svc_mod0


@pytest.fixture()
def _reset_rag_globals():
    with _swap(svc_mod0, "_rag_service_singleton", None), _swap(svc_mod0, "_rag_service_failed", False),             _swap(svc_mod0, "_cli_rag_announced", False):
        yield


def test_cli_rag_env_off(monkeypatch, _reset_rag_globals):
    monkeypatch.setenv("MUYUN_CLI_RAG", "0")
    import src.rag.service as svc_mod
    assert svc_mod.get_cli_rag_service() is None


def test_cli_rag_enabled_announces_once(monkeypatch, capsys, _reset_rag_globals):
    monkeypatch.setenv("MUYUN_CLI_RAG", "")   # 默认启用
    import src.rag.service as svc_mod

    class FakeSvc:
        doc_count = 837

    calls = []
    monkeypatch.setattr(svc_mod, "get_rag_service", lambda: calls.append(1) or FakeSvc())
    assert svc_mod.get_cli_rag_service() is not None
    assert svc_mod.get_cli_rag_service() is not None
    out = capsys.readouterr().out
    assert out.count("RAG知识增强：已启用") == 1, "启用提示只打一次"
    assert len(calls) == 2   # 单例返回前每次都会调（首调加载、次调命中单例——mock 下计数即可）


def test_rag_failure_memo(monkeypatch, _reset_rag_globals):
    """初始化失败后进程内记忆：第二次调用不再构造 RAGService（防反复 18s 加载）。"""
    import src.rag.service as svc_mod
    builds = []

    class FakeSvc:
        def is_available(self):
            return False

    monkeypatch.setattr(svc_mod, "RAGService", lambda *a, **k: builds.append(1) or FakeSvc())
    with _quiet():
        assert svc_mod.get_rag_service() is None
        assert svc_mod.get_rag_service() is None
    assert len(builds) == 1, "失败后应记忆，不再重复构造"


def test_cli_main_four_sites_wired():
    """cli/main.py 四处 Orchestrator 构造必须带 rag_service=_cli_rag()（结构断言）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "cli" / "main.py").read_text(encoding="utf-8")
    assert src.count("rag_service=_cli_rag()") == 4
    assert "def _cli_rag():" in src


def test_scanner_lazy_rag_wiring(monkeypatch):
    """scanner 深析懒接线：_get_orchestrator 传入 rag_service 且来自门控。"""
    import src.scanner.scanner_engine as se
    captured = {}

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    sentinel = object()
    monkeypatch.setattr(se, "Orchestrator", FakeOrchestrator)
    import src.rag.service as svc_mod
    monkeypatch.setattr(svc_mod, "get_cli_rag_service", lambda: sentinel)
    engine = se.ScannerEngine(
        rules_path="./src/scanner/scan_rules.yaml", skills_dir="./src/skills",
        enabled_skills=None, signal_weights=None, skill_types=None,
        ai_config={"enabled": False}, cache_ttl=300,
    )
    with _quiet():
        engine._get_orchestrator()
    assert captured.get("rag_service") is sentinel


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
