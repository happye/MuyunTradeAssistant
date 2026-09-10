"""测试 chat 会话中断恢复语义（ISS-092）：ChatAgent ↔ SessionStore 集成。

覆盖：
1. 发送失败（额度墙模拟）：提问已落盘 + last_send_error 置位；成功后清除
2. _run_conversation 逐 append 落盘：工具轮中途崩溃也能恢复到进行中状态（配对完整）
3. resume_saved_session：system 换当前提示词、裁剪生效、文件归属转移
4. peek 门控：仅 system 文件不算可恢复会话
5. reset_history：磁盘旧会话归档留档（不删除）
6. 防覆盖：未拥有文件时首次保存先归档盘上旧会话
7. 向后兼容：object.__new__ 构造、无 _session_store 属性的旧式 agent 照常工作

纯 mock（FakeClient 脚本 + 假工具注册），无网络。
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# 确保项目根在 path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 本用例走 agent.chat() 全流程，_run_conversation 会 print ⏳/🔧 等 emoji——
# 中文 Windows 下 stdout 重定向（管道/CI/日志）默认 cp936 编不出 emoji 会抛
# UnicodeEncodeError、被 chat() 误判成"发送失败"。errors="replace" 保证任何
# 输出环境下测试语义不受编码影响（同款处理见 test_chat_tool_failure.py）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from src.chat.agent import ChatAgent
from src.chat.prompts import CHAT_SYSTEM_PROMPT
from src.chat.session_store import SessionStore
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
    """按脚本序列返回响应的假 OpenAI 客户端。"""

    def __init__(self, script):
        self._script = script

    def create(self, **params):
        msg = self._script.pop(0)
        finish = "tool_calls" if msg.tool_calls else "stop"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason=finish)])


class FailingClient:
    """永远抛 402（额度墙）的假客户端。"""

    def create(self, **params):
        raise RuntimeError("402 Insufficient Balance")


def _store():
    d = tempfile.mkdtemp(prefix="muyun_chat_agent_")
    return SessionStore(base_dir=Path(d)), Path(d)


def _build_agent(store, script=None, failing=False, max_tool_rounds=1,
                 max_history=20, with_store_attr=True):
    """object.__new__ 构造最小可用 ChatAgent（跳过重型 __init__）。

    with_store_attr=False 时不设 _session_store 相关属性——验证旧式
    测试构造（落盘 getattr 静默跳过）完全兼容。
    """
    agent = object.__new__(ChatAgent)
    if failing:
        client = FailingClient()
    else:
        client = FakeClient(script or [])
    agent._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=client.create)))
    agent._model = "deepseek-v4-flash"
    agent.max_tool_rounds = max_tool_rounds
    agent.max_failed_rounds = 3
    agent.max_result_length = 4000
    agent.max_tokens = 384000
    agent.max_history = max_history
    agent.stream = False
    agent._last_reply_printed = False
    agent._messages = [{"role": "system", "content": "OLD_SYS"}]
    agent.last_send_error = None
    if with_store_attr:
        agent._session_store = store
        agent._session_created = None
        agent._owns_current_file = False
    return agent


def _register_ok_tool():
    def ok_tool():
        return "正常结果"
    chat_tools.TOOL_REGISTRY["ok_tool"] = ok_tool


def _unregister_fake_tools():
    chat_tools.TOOL_REGISTRY.pop("ok_tool", None)


# ── 发送失败：提问必须已在盘上 ──────────────────────────

def test_send_failure_persists_user_message():
    """额度墙抛异常：chat() 返回错误串，但用户消息已入历史+落盘。"""
    store, _ = _store()
    agent = _build_agent(store, failing=True)
    reply = agent.chat("帮我分析600519")
    assert reply.startswith("对话处理出错"), f"应返回错误串: {reply}"
    assert agent.last_send_error is not None
    assert "402" in agent.last_send_error
    # 盘上：提问已保存（下次启动恢复会话即可续上）
    data = store.load()
    assert data is not None
    msgs = data["messages"]
    assert msgs[-1] == {"role": "user", "content": "帮我分析600519"}
    assert agent.has_unanswered_tail() is True


def test_success_clears_last_send_error():
    store, _ = _store()
    agent = _build_agent(store, script=[_msg("回答完成")])
    agent.last_send_error = "上次的失败"
    reply = agent.chat("问个问题")
    assert "回答完成" in reply
    assert agent.last_send_error is None
    assert agent.has_unanswered_tail() is False


# ── 逐 append 落盘：工具轮进行中状态可恢复 ────────────────

def test_run_conversation_persists_tool_round_state():
    """工具轮对话全状态落盘：assistant(tool_calls)+tool 配对完整。"""
    store, _ = _store()
    _register_ok_tool()
    try:
        script = [
            _msg("我先查一下", tool_calls=[_tc("ok_tool", "{}", "t1")]),
            _msg("查完了，这是结论"),
        ]
        agent = _build_agent(store, script=script, max_tool_rounds=1)
        reply = agent.chat("分析600519")
        assert "结论" in reply
        msgs = store.load()["messages"]
        # system + user + assistant(tool_calls) + tool + assistant(final)
        assert [m["role"] for m in msgs] == [
            "system", "user", "assistant", "tool", "assistant"]
        assert msgs[2]["tool_calls"][0]["id"] == "t1"
        assert msgs[2]["tool_calls"][0]["function"]["name"] == "ok_tool"
        assert msgs[3]["tool_call_id"] == "t1"
    finally:
        _unregister_fake_tools()


# ── 恢复 ───────────────────────────────────────────────

def test_resume_saved_session_replaces_system_prompt():
    """resume：对话历史恢复，system 换当前版提示词，文件归属转移。"""
    store, _ = _store()
    store.save([
        {"role": "system", "content": "一年前的旧提示词"},
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    ], model="deepseek-v4-flash", created_at="2026-09-10 10:00:00")
    agent = _build_agent(store)
    n = agent.resume_saved_session()
    assert n == 2, "应恢复 2 条对话消息（不含system）"
    assert agent._messages[0]["content"] == CHAT_SYSTEM_PROMPT, \
        "system 必须换当前版（旧会话可能存着过时prompt）"
    assert agent._messages[1]["content"] == "旧问题"
    assert agent._messages[2]["content"] == "旧回答"
    assert agent._owns_current_file is True, "恢复后 current.json 归本会话所有"
    assert agent._session_created == "2026-09-10 10:00:00", "创建时间应沿用原会话"


def test_resume_applies_history_trim():
    """恢复时按当前 max_history 裁剪（配置改小后恢复不爆窗口）。"""
    store, _ = _store()
    msgs = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": f"问{i}"} for i in range(30)]
    store.save(msgs, model="m")
    agent = _build_agent(store, max_history=5)
    n = agent.resume_saved_session()
    assert n == 5
    assert agent._messages[-1]["content"] == "问29", "保留最近的"
    assert agent._messages[1]["content"] == "问25", "窗口从旧到新"


def test_resume_trimmed_empty_preserves_file():
    """裁剪后对话为空（中断于工具轮+窗口过小）时：不接管不落盘，原文件保留。

    ISS-092 审查 P1-1：旧实现先置 _owns_current_file=True 再 persist，
    current.json 被仅含 system 的内容覆盖——旧对话无归档无隔离，静默全灭。
    """
    store, _ = _store()
    # 中断于工具轮的典型崩溃态：assistant(tool_calls) + 连续 tool 消息
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "x", "arguments": "{}"}}]},
    ] + [{"role": "tool", "tool_call_id": "t1", "content": f"结果{i}"}
         for i in range(5)]
    store.save(msgs, model="m")
    agent = _build_agent(store, max_history=3)  # 窗口小于 tool 串长度
    n = agent.resume_saved_session()
    assert n == 0, "裁剪后为空应返回 0"
    assert agent._owns_current_file is False, "裁空时不得接管文件"
    # 原文件必须原样保留（不覆盖、不归档、不隔离）
    data = store.load()
    assert data is not None
    assert len(data["messages"]) == 7
    import glob
    assert glob.glob(str(Path(store.dir) / "*.json")) == [str(store.current_path)], \
        "盘上除 current.json 外不应有任何其他文件（无归档/隔离动作）"


def test_peek_saved_session_gate():
    """仅 system 的文件不算可恢复会话；未启用持久化返回 None。"""
    store, _ = _store()
    # 无文件
    agent = _build_agent(store)
    assert agent.peek_saved_session() is None
    # 仅 system
    store.save([{"role": "system", "content": "s"}], model="m")
    assert agent.peek_saved_session() is None
    # 有对话
    store.save([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "问"},
    ], model="m")
    meta = agent.peek_saved_session()
    assert meta is not None and meta["n_messages"] == 1
    # 未启用（store=None）
    agent2 = _build_agent(None)
    assert agent2.peek_saved_session() is None


def test_has_unanswered_tail():
    agent = _build_agent(None)
    agent._messages = [{"role": "system", "content": "s"}]
    assert agent.has_unanswered_tail() is False
    agent._messages.append({"role": "user", "content": "问"})
    assert agent.has_unanswered_tail() is True
    agent._messages.append({"role": "assistant", "content": "答"})
    assert agent.has_unanswered_tail() is False
    # 工具轮进行中（尾部是tool）也算未完成
    agent._messages[-1] = {"role": "tool", "tool_call_id": "t", "content": "r"}
    assert agent.has_unanswered_tail() is True


# ── 重置与防覆盖 ────────────────────────────────────────

def test_reset_history_archives_disk_session():
    """reset：磁盘旧会话归档留档（不删除），current 变全新空会话。"""
    store, _ = _store()
    agent = _build_agent(store, script=[_msg("答")])
    agent.chat("问")
    assert store.peek()["n_messages"] == 2
    archived = agent.reset_history()
    assert archived is not None and archived.startswith("session_")
    # 归档内容还在
    files = list(Path(store.dir).glob("session_*.json"))
    assert len(files) == 1
    import json as _json
    with open(files[0], encoding="utf-8") as f:
        assert len(_json.load(f)["messages"]) == 3
    # current 重置为仅 system
    assert store.peek()["n_messages"] == 0
    assert len(agent._messages) == 1


def test_persist_archives_foreign_file_before_first_save():
    """未拥有文件时首次保存：盘上旧会话先归档，不静默覆盖（并发双开/漏网路径）。"""
    store, _ = _store()
    # 另一个会话留下的文件
    store.save([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "旧会话的提问"},
        {"role": "assistant", "content": "旧会话的回答"},
    ], model="other")
    agent = _build_agent(store, script=[_msg("新会话的回答")])
    agent.chat("新会话第一问")
    entries = store.list_sessions()
    archived = [e for e in entries if not e["is_current"]]
    assert len(archived) == 1 and archived[0]["n_messages"] == 2, \
        "旧会话应被归档而不是覆盖"
    peek = store.peek()
    assert peek["n_messages"] == 2, "current 是新会话的 user+assistant"


# ── 向后兼容：旧式 object.__new__ 构造（无 store 属性）──

def test_chat_without_store_attr_still_works():
    """不带 _session_store 属性的 agent（旧测试构造法）chat() 全流程不炸。"""
    agent = _build_agent(None, script=[_msg("正常回答")], with_store_attr=False)
    reply = agent.chat("任何问题")
    assert "正常回答" in reply
    assert not hasattr(agent, "_session_store") or agent._session_store is None


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
    # 收尾：清临时目录
    import shutil
    for d in Path(tempfile.gettempdir()).glob("muyun_chat_agent_*"):
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
