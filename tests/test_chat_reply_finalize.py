"""测试 ChatAgent._finalize_reply 回复兜底逻辑。

修复 bug：chat 提问后模型把 <tool_calls> 伪XML当正文输出并停止，
或 content 为空（思考型模型偶发），导致用户看到假工具调用/空白、无真实回答。
兜底三层：content空→reasoning_content→占位提示；剥离伪XML；length截断提示。
"""
import os
import sys
from types import SimpleNamespace

# 确保项目根在 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chat.agent import ChatAgent


def _msg(content=None, reasoning=""):
    """构造模拟的API返回message对象（OpenAI SDK message 属性形状）"""
    return SimpleNamespace(content=content, reasoning_content=reasoning)


def test_normal_content_passthrough():
    """正常content原样返回（strip前后空白）。"""
    m = _msg("  锂矿行业的三阶段分析……  ")
    assert ChatAgent._finalize_reply(m, "stop") == "锂矿行业的三阶段分析……"


def test_empty_content_falls_back_to_reasoning():
    """content为空但reasoning_content有内容 → 用reasoning兜底。"""
    m = _msg(None, reasoning="模型推理过程：先看持仓再看行业……")
    result = ChatAgent._finalize_reply(m, "stop")
    assert result == "模型推理过程：先看持仓再看行业……"


def test_empty_content_no_reasoning_placeholder():
    """content和reasoning都为空 → 占位提示（绝不静默输出空）。"""
    m = _msg(None)
    result = ChatAgent._finalize_reply(m, "stop")
    assert "未返回有效回答" in result


def test_fake_tool_calls_stripped():
    """正文夹杂伪<tool_calls>XML → 剥离XML保留正文。"""
    content = (
        "我先总结已有信息。\n\n"
        "<tool_calls>\n<invoke name=\"search_knowledge\">\n"
        "<parameter name=\"query\" string=\"true\">周期</parameter>\n"
        "</invoke>\n</tool_calls>\n\n"
        "以下是回答正文。"
    )
    result = ChatAgent._finalize_reply(_msg(content), "stop")
    assert "<tool_calls>" not in result
    assert "我先总结已有信息。" in result
    assert "以下是回答正文。" in result


def test_fake_tool_calls_with_leadin_adds_notice():
    """引导句+伪工具调用XML（用户复现场景）→ 剥离XML、保留引导句、追加不完整警告。"""
    content = (
        "现在让我再搜索一下锂矿行业相关的策略知识，以便从周期角度给你更全面的分析。\n\n"
        "<tool_calls>\n<invoke name=\"search_knowledge\">\n"
        "<parameter name=\"query\" string=\"true\">顺周期投资</parameter>\n"
        "</invoke>\n</tool_calls>"
    )
    result = ChatAgent._finalize_reply(_msg(content), "stop")
    assert "<tool_calls>" not in result
    assert "让我再搜索一下" in result
    assert "不完整" in result and "继续" in result


def test_only_fake_tool_calls_placeholder():
    """回复仅含伪工具调用XML（无任何正文）→ 占位提示。"""
    content = (
        "<tool_calls>\n<invoke name=\"search_knowledge\">\n"
        "<parameter name=\"query\" string=\"true\">顺周期投资</parameter>\n"
        "</invoke>\n</tool_calls>"
    )
    result = ChatAgent._finalize_reply(_msg(content), "stop")
    assert "<tool_calls>" not in result
    assert "占位文本" in result


def test_unterminated_fake_tool_calls_stripped():
    """伪XML未闭合（输出被截断）→ 也剥离到末尾。"""
    content = "让我再搜索。\n\n<tool_calls>\n<invoke name=\"search_kno"
    result = ChatAgent._finalize_reply(_msg(content), "stop")
    assert "<tool_calls>" not in result
    assert "让我再搜索。" in result


def test_length_adds_truncation_notice():
    """finish_reason=length → 追加截断提示。"""
    m = _msg("一段很长的回答")
    result = ChatAgent._finalize_reply(m, "length")
    assert "被截断" in result
    assert result.startswith("一段很长的回答")


def test_whitespace_content_treated_as_empty():
    """content为纯空白（如"\n"）→ 与空content同样兜底，不输出空白行。"""
    m = _msg("\n  ")
    result = ChatAgent._finalize_reply(m, "stop")
    assert "未返回有效回答" in result
    assert result.strip() != ""


if __name__ == "__main__":
    import sys
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
