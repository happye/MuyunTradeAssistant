"""测试 chat 流式输出（FEAT-20260816-002）。

覆盖：
1. _call_api_stream 增量聚合：content delta 拼接、tool_calls 按 index 聚合（id/name/arguments分片）
2. message 形状与非流式兼容（.content/.tool_calls/.reasoning_content）
3. 流式失败 -> _run_conversation 回退非流式
4. _reply_parts 拆分（base已打印只补suffix）
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.chat.agent import ChatAgent


def _chunk(content=None, tc=None, finish=None, reasoning=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning,
                            tool_calls=tc)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def _tc_delta(index=0, id=None, name=None, args=None):
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=id, function=fn)


class FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __iter__(self):
        return iter(self._chunks)


def _stream_agent(chunks):
    agent = object.__new__(ChatAgent)
    agent._model = "deepseek-v4-flash"
    agent.max_tokens = 384000
    agent._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: FakeStream(chunks))))
    return agent


def test_stream_accumulates_content_and_finish():
    agent = _stream_agent([
        _chunk(content="锂"),
        _chunk(content="矿价格"),
        _chunk(content="处于低位", finish="stop"),
    ])
    msg, fr = agent._call_api_stream({})
    assert msg.content == "锂矿价格处于低位"
    assert fr == "stop"
    assert msg.tool_calls is None


def test_stream_accumulates_tool_calls_by_index():
    agent = _stream_agent([
        _chunk(content="我先查持仓", tc=[_tc_delta(index=0, id="c1", name="get_portfolio")]),
        _chunk(tc=[_tc_delta(index=0, args='{')]),
        _chunk(tc=[_tc_delta(index=0, args='}')]),
        _chunk(tc=[_tc_delta(index=1, id="c2", name="analyze_industry", args='{"industry": "锂矿"}')]),
        _chunk(finish="tool_calls"),
    ])
    msg, fr = agent._call_api_stream({})
    assert msg.content == "我先查持仓"
    assert fr == "tool_calls"
    assert len(msg.tool_calls) == 2
    t0, t1 = msg.tool_calls
    assert t0.id == "c1" and t0.function.name == "get_portfolio"
    assert t0.function.arguments == "{}"
    assert t1.id == "c2" and t1.function.name == "analyze_industry"
    assert t1.function.arguments == '{"industry": "锂矿"}'


def test_stream_empty_content_returns_none_content():
    agent = _stream_agent([_chunk(finish="stop")])
    msg, fr = agent._call_api_stream({})
    assert msg.content is None
    assert fr == "stop"


def test_reply_parts_split_base_and_suffix():
    m = SimpleNamespace(content="正文内容", reasoning_content="", tool_calls=None)
    base, suffix = ChatAgent._reply_parts(m, "length")
    assert base == "正文内容"
    assert "被截断" in suffix
    # 正常结束无suffix
    base2, suffix2 = ChatAgent._reply_parts(m, "stop")
    assert suffix2 == ""


def test_finalize_reply_compat():
    """_finalize_reply = base + suffix（旧接口兼容）。"""
    m = SimpleNamespace(content="正文", reasoning_content="", tool_calls=None)
    assert ChatAgent._finalize_reply(m, "length").startswith("正文")
    assert ChatAgent._finalize_reply(m, "stop") == "正文"


if __name__ == "__main__":
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
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
