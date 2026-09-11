"""chat 上下文护栏回归测试（2026-09-11）。

背景：工具轮次上限提到 20 轮后，单轮对话内工具结果累计量翻倍。护栏在每次请求前
估算输入 token（system + tools + 全部消息），超「provider 官方窗口 × budget_ratio」
即折叠最早的 tool 结果；折叠后仍超窗口则停止调工具、要求模型直接作答。

本文件锁死：
1. 折叠只动 content、**不删除 tool 消息**（删除会破坏 assistant(tool_calls)/tool 配对 → API 400）；
2. 折叠幂等（二次折叠不重复处理）；
3. 未超预算时零副作用；
4. 超预算先折叠、折叠后仍超窗口则返回 False；
5. 关掉护栏时完全跳过；
6. 端到端：进度行带「第 X/N 轮」，超窗口时走"上下文"版兜底提示且最终调用不带 tools。

用假 client，不依赖真实网络/AI。
"""
import io
import os
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from src.chat import agent as agent_mod
from src.chat.agent import ChatAgent, _FOLDED_MARK


def _tool_msg(i, content):
    return {"role": "tool", "tool_call_id": f"c{i}", "content": content}


def _mk_agent(window=800, ratio=0.85, keep=3, messages=None):
    a = object.__new__(ChatAgent)
    a.context_guard_enabled = True
    a.context_budget_ratio = ratio
    a.keep_recent_tool_results = keep
    a.context_window = window
    a._provider = "test"
    a._last_context_estimate = None
    a._messages = messages if messages is not None else [{"role": "system", "content": "s"}]
    a._session_store = None
    return a


def _ten_tool_messages():
    msgs = [{"role": "system", "content": "s"}]
    for i in range(10):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "t", "arguments": "{}"}}]})
        msgs.append(_tool_msg(i, "x" * 400))
    return msgs


# ── 1. 折叠语义 ──────────────────────────────────────────

def test_fold_keeps_recent_and_never_deletes_tool_messages():
    a = _mk_agent(messages=_ten_tool_messages(), keep=3)
    n_before = sum(1 for m in a._messages if m.get("role") == "tool")

    folded = a._fold_old_tool_results(3)

    assert folded == 7
    assert sum(1 for m in a._messages if m.get("role") == "tool") == n_before, \
        "折叠不得删除 tool 消息（否则 assistant(tool_calls)/tool 配对断裂 -> API 400）"
    tool_msgs = [m for m in a._messages if m.get("role") == "tool"]
    folded_flags = [m["content"].startswith(_FOLDED_MARK) for m in tool_msgs]
    assert folded_flags == [True] * 7 + [False] * 3, "应为「最早的 7 条折叠、最近 3 条保留」"
    assert tool_msgs[-1]["content"] == "x" * 400


def test_fold_is_idempotent():
    a = _mk_agent(messages=_ten_tool_messages(), keep=3)
    assert a._fold_old_tool_results(3) == 7
    assert a._fold_old_tool_results(3) == 0, "已折叠的不应重复折叠"
    assert a._fold_old_tool_results(99) == 0


# ── 2. 护栏判定 ──────────────────────────────────────────

def test_guard_noop_when_under_budget(monkeypatch):
    monkeypatch.setattr(agent_mod, "TOOL_DEFINITIONS", [])
    a = _mk_agent(window=1_000_000, messages=_ten_tool_messages())
    ok, detail = a._guard_before_call()
    assert ok is True and detail == ""
    assert not any(m["content"].startswith(_FOLDED_MARK) for m in a._messages)
    assert a._last_context_estimate > 0


def test_guard_folds_then_allows_when_back_under_budget(monkeypatch):
    monkeypatch.setattr(agent_mod, "TOOL_DEFINITIONS", [])
    a = _mk_agent(window=800, ratio=0.85, keep=3, messages=_ten_tool_messages())
    before = agent_mod.estimate_messages_tokens(a._messages, [])

    ok, detail = a._guard_before_call()

    assert before > 800 * 0.85, "构造的用例应确实超预算"
    assert ok is True and detail == ""
    assert sum(1 for m in a._messages
               if m["content"].startswith(_FOLDED_MARK)) == 7
    assert a._last_context_estimate < before, "折叠后估算应下降"


def test_guard_stops_when_still_over_window(monkeypatch):
    monkeypatch.setattr(agent_mod, "TOOL_DEFINITIONS", [])
    a = _mk_agent(window=200, ratio=0.85, keep=3, messages=_ten_tool_messages())
    ok, detail = a._guard_before_call()
    assert ok is False
    assert "tokens" in detail and "窗口" in detail


def test_guard_disabled_skips_everything(monkeypatch):
    monkeypatch.setattr(agent_mod, "TOOL_DEFINITIONS", [])
    a = _mk_agent(window=200, messages=_ten_tool_messages())
    a.context_guard_enabled = False
    ok, detail = a._guard_before_call()
    assert ok is True and detail == ""
    assert a._last_context_estimate is None, "关闭时不应做估算"
    assert not any(m["content"].startswith(_FOLDED_MARK) for m in a._messages)


# ── 3. 端到端：进度行 + 超窗口兜底 ───────────────────────

class _Client:
    def __init__(self, with_tool=True):
        self.calls = []
        self.tool_rounds = 0
        self._with_tool = with_tool

    def create(self, **params):
        self.calls.append(params)
        if "tools" in params and self._with_tool:
            self.tool_rounds += 1
            tc = SimpleNamespace(id=f"c{self.tool_rounds}", type="function",
                                 function=SimpleNamespace(name="ok_tool", arguments="{}"))
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=[tc], reasoning_content=""),
                finish_reason="tool_calls")])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="最终回答", tool_calls=None, reasoning_content=""),
            finish_reason="stop")])


def _wire(a, client, max_tool_rounds=3):
    a._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=client.create)))
    a._model = "deepseek-flash"
    a.max_tool_rounds = max_tool_rounds
    a.max_failed_rounds = 3
    a.max_result_length = 4000
    a.max_tokens = 384000
    a.stream = False
    a._last_reply_printed = False
    return a


def _run(a):
    from src.chat import tools as chat_tools
    saved = dict(chat_tools.TOOL_REGISTRY)
    chat_tools.TOOL_REGISTRY["ok_tool"] = lambda **kw: "正常结果"
    try:
        with redirect_stdout(io.StringIO()) as buf:
            reply = a._run_conversation()
    finally:
        chat_tools.TOOL_REGISTRY.clear()
        chat_tools.TOOL_REGISTRY.update(saved)
    return reply, buf.getvalue()


def test_progress_line_shows_round_number(monkeypatch):
    """轮次进度：每轮等待提示必须带「第 X/N 轮」（N = hard_cap = max_tool_rounds×2）。"""
    monkeypatch.setattr(agent_mod, "TOOL_DEFINITIONS", [])
    client = _Client(with_tool=True)
    a = _wire(_mk_agent(window=10_000_000, keep=3), client, max_tool_rounds=3)
    a._messages = [{"role": "system", "content": "s"}]

    _, out = _run(a)

    assert "第 1/6 轮" in out, out
    assert "第 3/6 轮" in out, out
    assert client.tool_rounds == 6


def test_context_stop_yields_human_message_and_no_tools_final_call(monkeypatch):
    """超窗口时：跳出循环、注入「上下文」版提示、最终调用不带 tools。"""
    monkeypatch.setattr(agent_mod, "TOOL_DEFINITIONS", [])
    client = _Client(with_tool=True)
    a = _wire(_mk_agent(window=100, keep=3), client, max_tool_rounds=3)
    a._messages = [{"role": "system", "content": "s"}, _tool_msg(0, "x" * 2000)]

    reply, out = _run(a)

    assert client.tool_rounds == 0, "护栏拦住后不应再发起带 tools 的调用"
    assert sum(1 for c in client.calls if "tools" not in c) == 1
    assert "上下文" in out and "已超模型窗口" in out
    nudge = [m for m in a._messages if m.get("role") == "user" and "上下文" in str(m.get("content"))]
    assert nudge, "应注入上下文版 nudge（而非轮次上限版）"
    assert reply == "最终回答"
