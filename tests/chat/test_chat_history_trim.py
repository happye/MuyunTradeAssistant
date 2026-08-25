"""测试 ChatAgent 历史裁剪保证 tool/tool_calls 配对完整。

修复 bug：对话多轮后消息超过 max_history 触发裁剪，若裁剪边界落在
assistant(tool_calls) 与其 tool 结果之间，recent 开头出现孤立 tool 消息
（assistant tool_calls 被裁掉）-> DeepSeek API 400:
"Messages with role 'tool' must be a response to a preceding message with 'tool_calls'"
"""
import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.chat.agent import ChatAgent


def test_trim_drops_orphan_tool_at_start():
    """recent 开头若是孤立 tool（其 assistant tool_calls 已被裁掉），必须丢弃。"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        # max_history=4 -> recent = 最后4条，开头是孤立 tool（上面的 assistant tool_calls 被裁掉）
        {"role": "tool", "tool_call_id": "c1", "content": "t1"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a3"},
    ]
    result = ChatAgent._trim_messages(msgs, max_history=4)
    roles = [m["role"] for m in result]
    assert roles[0] == "system"
    assert roles[1] != "tool", "开头孤立 tool 必须丢弃"
    assert roles == ["system", "assistant", "user", "assistant"]


def test_trim_keeps_paired_tool_calls():
    """assistant(tool_calls)+tool 配对都在 recent 内时，不丢弃。"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "t1"},
        {"role": "assistant", "content": "final"},
    ]
    # max_history=4 -> recent=[user, assistant(tool_calls), tool, assistant]，配对完整
    result = ChatAgent._trim_messages(msgs, max_history=4)
    roles = [m["role"] for m in result]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]


def test_trim_drops_multiple_orphan_tools():
    """开头连续多个孤立 tool（同一被裁 assistant 的多个 tool_calls 响应）都要丢弃。"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}},
                        {"id": "c2", "type": "function", "function": {"name": "g", "arguments": "{}"}}]},
        # max_history=5 -> recent 开头是 c1, c2 两条孤立 tool
        {"role": "tool", "tool_call_id": "c1", "content": "t1"},
        {"role": "tool", "tool_call_id": "c2", "content": "t2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a3"},
    ]
    result = ChatAgent._trim_messages(msgs, max_history=5)
    roles = [m["role"] for m in result]
    assert roles[0] == "system"
    assert "tool" not in roles, "所有孤立 tool 都应丢弃"


def test_trim_no_change_when_short():
    """消息数不超过阈值时原样返回。"""
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert ChatAgent._trim_messages(msgs, 20) is msgs


if __name__ == "__main__":
    test_trim_drops_orphan_tool_at_start()
    test_trim_keeps_paired_tool_calls()
    test_trim_drops_multiple_orphan_tools()
    test_trim_no_change_when_short()
    print("4/4 passed")
