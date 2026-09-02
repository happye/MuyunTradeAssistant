"""ISS-078 confirm 硬门覆盖补齐回归（2026-09-03）

锁死语义：la/lall（逐持仓 AI 深析）、scan（analyze_portfolio，逐持仓 AI）、
events（事件层 AI 分类）与 l all/ba 同属批量 AI 费用操作，无 confirm 必须拒绝，
且拒绝时不得执行 run_cli（mock 拦截防真跑烧钱）。

跑法：pytest tests/chat/test_chat_confirm_gate_coverage.py
"""
import contextlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.chat.tools import TOOL_ERROR_MARK, run_command


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
    import io
    import contextlib as _cl
    buf = io.StringIO()
    with _cl.redirect_stdout(buf), _cl.redirect_stderr(buf):
        yield buf


def test_confirm_gate_blocks_la_scan_events():
    import start as start_mod
    calls = []

    def _fake_run_cli(mode, args):
        calls.append((mode, args))

    for cmd in ("la", "lall", "scan", "events"):
        calls.clear()
        with _swap(start_mod, "run_cli", _fake_run_cli), _quiet():
            result = run_command(cmd)
        assert result.startswith(TOOL_ERROR_MARK), \
            f"'{cmd}' 无 confirm 应被硬门拒绝，实际放行（mode={calls[:1]}）"
        assert "confirm=true" in result, f"'{cmd}' 拒绝消息应引导 confirm=true"
        assert not calls, f"'{cmd}' 被拒后不应执行 run_cli"


def test_confirm_gate_passes_la_with_confirm():
    import start as start_mod
    calls = []

    def _fake_run_cli(mode, args):
        calls.append((mode, args))

    with _swap(start_mod, "run_cli", _fake_run_cli), _quiet():
        result = run_command("la", True)
    assert not result.startswith(TOOL_ERROR_MARK), f"带 confirm 应放行: {result[:80]}"
    assert calls and calls[0][0] == "live_all"
