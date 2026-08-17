"""测试 chat 退出资源清理 shutdown_engines。

shutdown_engines 职责：
1. 关闭编排器内 AI 客户端连接（httpx 无可靠析构）
2. baostock 登出（复用 akshare_client._baostock_logout）
3. 置空模块级引擎单例（RAG torch 模型/FAISS 索引随之可被 GC）
4. gc.collect()

chat 退出后不清理的话，RAG 模型（内存大头）随模块级单例驻留整个
start.py 会话，重进 chat 还会二次加载模型。
"""
import os
import sys
from types import SimpleNamespace

# 确保项目根在 path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.chat import tools as chat_tools


def test_shutdown_nulls_all_engine_globals():
    """置空全部模块级引擎单例。"""
    chat_tools._orchestrator = object()
    chat_tools._scanner_engine = object()
    chat_tools._portfolio_manager = object()
    chat_tools._rag_service = object()
    chat_tools.shutdown_engines()
    assert chat_tools._orchestrator is None
    assert chat_tools._scanner_engine is None
    assert chat_tools._portfolio_manager is None
    assert chat_tools._rag_service is None


def test_shutdown_closes_ai_client():
    """编排器持有AI客户端时，close() 必须被调用。"""
    closed = []

    class FakeClient:
        def close(self):
            closed.append(1)

    chat_tools._orchestrator = SimpleNamespace(
        ai_modifier=SimpleNamespace(_client=FakeClient()))
    chat_tools.shutdown_engines()
    assert closed == [1], "AI客户端close()未被调用"


def test_shutdown_idempotent_and_safe_on_none():
    """全部为None时调用不抛异常，且可重复调用。"""
    chat_tools._orchestrator = None
    chat_tools._scanner_engine = None
    chat_tools._portfolio_manager = None
    chat_tools._rag_service = None
    chat_tools.shutdown_engines()
    chat_tools.shutdown_engines()
    assert chat_tools._orchestrator is None


def test_shutdown_survives_broken_close():
    """AI客户端close()抛异常不影响其余清理。"""
    class BrokenClient:
        def close(self):
            raise RuntimeError("close失败")

    chat_tools._orchestrator = SimpleNamespace(
        ai_modifier=SimpleNamespace(_client=BrokenClient()))
    chat_tools._rag_service = object()
    chat_tools.shutdown_engines()
    assert chat_tools._orchestrator is None
    assert chat_tools._rag_service is None


if __name__ == "__main__":
    import traceback

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
