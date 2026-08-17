"""测试 chat 工具失败与轮次上限的语义（ISS-059）。

语义：
1. 失败轮（一轮内所有工具都返回 TOOL_ERROR_MARK）不占工具轮次上限，AI 可重试
2. 连续全失败达 max_failed_rounds → 停止循环，用"如实说明数据缺失"的提示要求直接回答
3. 一轮内有任一工具成功 → 计一轮并清零失败计数
4. 工具层所有失败返回统一以 TOOL_ERROR_MARK 开头（_execute_tool + tools.py 内部）

用 FakeClient 模拟 API 返回序列，不依赖真实网络/AI。
"""
import os
import sys
from types import SimpleNamespace

# 确保项目根在 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chat.agent import ChatAgent
from src.chat import tools as chat_tools


def _tc(name, arguments="{}", id_="t1"):
    return SimpleNamespace(
        id=id_, type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _msg(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls,
                           reasoning_content="")


class FakeClient:
    """按脚本序列返回响应的假 OpenAI 客户端，记录每次调用的参数。"""

    def __init__(self, script):
        self._script = script
        self.calls = []

    def create(self, **params):
        idx = len(self.calls)
        self.calls.append(params)
        msg = self._script[idx]
        finish = "tool_calls" if msg.tool_calls else "stop"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason=finish)])


def _build_agent(script, max_tool_rounds=1, max_failed_rounds=3):
    """构造最小可用 ChatAgent（object.__new__ 跳过重型 __init__）。"""
    agent = object.__new__(ChatAgent)
    fake = FakeClient(script)
    agent._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake.create)))
    agent._fake = fake  # 测试断言用
    agent._model = "deepseek-v4-flash"
    agent.max_tool_rounds = max_tool_rounds
    agent.max_failed_rounds = max_failed_rounds
    agent.max_result_length = 4000
    agent.max_tokens = 384000
    agent.stream = False
    agent._last_reply_printed = False
    agent._messages = [{"role": "system", "content": "s"}]
    return agent


def _register_fake_tools(counts):
    """注册假工具：flaky（前2次失败后成功）/ fail（永远失败）/ ok（永远成功）"""
    def flaky_tool():
        counts["flaky"] += 1
        if counts["flaky"] <= 2:
            return chat_tools.TOOL_ERROR_MARK + "模拟网络失败"
        return "成功的数据"

    def fail_tool():
        counts["fail"] += 1
        return chat_tools.TOOL_ERROR_MARK + "模拟工具异常"

    def ok_tool():
        counts["ok"] += 1
        return "正常结果"

    chat_tools.TOOL_REGISTRY["flaky_tool"] = flaky_tool
    chat_tools.TOOL_REGISTRY["fail_tool"] = fail_tool
    chat_tools.TOOL_REGISTRY["ok_tool"] = ok_tool


def _unregister_fake_tools():
    for n in ("flaky_tool", "fail_tool", "ok_tool"):
        chat_tools.TOOL_REGISTRY.pop(n, None)


def test_is_tool_failure_marker():
    """失败标记识别：TOOL_ERROR_MARK 开头=失败，其余=成功。"""
    assert ChatAgent._is_tool_failure(chat_tools.TOOL_ERROR_MARK + "超时") is True
    assert ChatAgent._is_tool_failure("正常分析结果") is False
    assert ChatAgent._is_tool_failure("") is False
    assert ChatAgent._is_tool_failure(None) is False


def test_execute_unknown_tool_returns_marked_failure():
    """_execute_tool 未知工具 → TOOL_ERROR_MARK 开头的失败。"""
    agent = object.__new__(ChatAgent)
    agent.max_result_length = 4000
    result = agent._execute_tool("no_such_tool", {})
    assert result.startswith(chat_tools.TOOL_ERROR_MARK)


def test_failed_rounds_do_not_consume_cap_and_retry_succeeds():
    """连续2轮失败（不占上限）→ 第3轮重试成功 → 第4次调用正常回答。

    旧语义下（失败也计轮次，max_tool_rounds=1→硬上限2）：2轮失败即耗尽上限，
    第3次调用是无tools的兜底调用，重试永远不会发生。
    新语义：失败不占轮次，第3次调用仍是带tools的正常轮。
    """
    counts = {"flaky": 0}
    _register_fake_tools(counts)
    try:
        script = [
            _msg("先试试", tool_calls=[_tc("flaky_tool", "{}", "t1")]),      # 失败1（不占轮次）
            _msg("再试一次", tool_calls=[_tc("flaky_tool", "{}", "t2")]),    # 失败2（不占轮次）
            _msg("继续", tool_calls=[_tc("flaky_tool", "{}", "t3")]),        # 成功（计第1轮）
            _msg("最终回答：数据已拿到", tool_calls=None),                     # 正常回答
        ]
        agent = _build_agent(script, max_tool_rounds=1, max_failed_rounds=3)
        result = agent._run_conversation()
    finally:
        _unregister_fake_tools()

    assert result == "最终回答：数据已拿到"
    assert counts["flaky"] == 3, "工具应被重试3次"
    assert len(agent._fake.calls) == 4, "4次API调用（3轮带tools + 1轮回答）"
    for params in agent._fake.calls:
        assert "tools" in params, "循环内的回答轮也带tools（无tools兜底调用只在循环退出后出现）"
    tool_contents = [m["content"] for m in agent._messages if m["role"] == "tool"]
    assert "成功的数据" in tool_contents, "重试成功的工具结果应进入历史"


def test_consecutive_failures_abort_with_honest_nudge():
    """连续全失败达 max_failed_rounds → 停止循环，提示AI如实说明数据缺失。"""
    counts = {"fail": 0}
    _register_fake_tools(counts)
    try:
        script = [
            _msg("试", tool_calls=[_tc("fail_tool", "{}", "t1")]),
            _msg("再试", tool_calls=[_tc("fail_tool", "{}", "t2")]),
            _msg("还试", tool_calls=[_tc("fail_tool", "{}", "t3")]),
            _msg("抱歉，数据获取失败，无法给出完整回答", tool_calls=None),  # 兜底调用
        ]
        agent = _build_agent(script, max_tool_rounds=1, max_failed_rounds=3)
        result = agent._run_conversation()
    finally:
        _unregister_fake_tools()

    assert result == "抱歉，数据获取失败，无法给出完整回答"
    assert len(agent._fake.calls) == 4, "3轮失败 + 1轮兜底"
    # 兜底调用的提示语必须是"如实说明数据缺失"变体，而非"轮次上限"变体
    nudge_found = any(
        "如实说明哪些数据未能获取" in m.get("content", "")
        for m in agent._messages if m["role"] == "user"
    )
    assert nudge_found, "连续失败退出应使用'如实说明数据缺失'提示语"
    assert "tools" not in agent._fake.calls[3]


def test_mixed_round_counts_as_success():
    """一轮中一个工具成功一个失败 → 计一轮（成功结果在，失败信息也回传AI）。"""
    counts = {"ok": 0, "fail": 0}
    _register_fake_tools(counts)
    try:
        script = [
            _msg("", tool_calls=[_tc("ok_tool", "{}", "t1"), _tc("fail_tool", "{}", "t2")]),
            _msg("回答", tool_calls=None),
        ]
        agent = _build_agent(script, max_tool_rounds=1, max_failed_rounds=3)
        result = agent._run_conversation()
    finally:
        _unregister_fake_tools()

    assert result == "回答"
    assert len(agent._fake.calls) == 2, "混合轮计1轮后正常回答，共2次调用"
    tool_contents = [m["content"] for m in agent._messages if m["role"] == "tool"]
    assert "正常结果" in tool_contents
    assert any(c.startswith(chat_tools.TOOL_ERROR_MARK) for c in tool_contents), \
        "失败信息也应回传AI"


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
