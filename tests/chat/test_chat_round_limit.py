"""chat 工具轮次上限契约回归测试（2026-09-11，轮次 10→20 调整）。

锁住三条契约，防再次漂移（历史病根：settings / 代码默认 / 文档三处各说各话）：
1. `configs/settings.yaml` 的 `chat.max_tool_rounds` 与代码缺省 `DEFAULT_MAX_TOOL_ROUNDS` 一致
2. `hard_tool_round_limit == max_tool_rounds × 2`（×2 系数只在一处出口，防散落）
3. 循环真的按该硬上限熔断：跑满 hard_cap 轮工具后，追加 nudge 且最终调用**不带 tools**

用假 client 模拟"模型永不停止调工具"，不依赖真实网络/AI。
"""
import io
import os
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import yaml

from src.chat import agent as agent_mod
from src.chat import tools as chat_tools
from src.chat.agent import ChatAgent, DEFAULT_MAX_TOOL_ROUNDS

_SETTINGS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "configs", "settings.yaml"))


def _load_chat_cfg() -> dict:
    with open(_SETTINGS, encoding="utf-8") as f:
        return yaml.safe_load(f)["chat"]


def test_settings_matches_code_default():
    """settings.yaml 的 chat.max_tool_rounds 必须与代码缺省常量一致。

    两处不一致时：配置缺失/被删会静默退回另一个基线，用户无感知
    （本项目已多次因"改配置不改代码/改代码不改配置"造成账实漂移）。
    """
    assert _load_chat_cfg()["max_tool_rounds"] == DEFAULT_MAX_TOOL_ROUNDS


def test_hard_limit_is_double():
    """×2 系数契约：hard_tool_round_limit = max_tool_rounds × 2。"""
    agent = object.__new__(ChatAgent)
    for n in (0, 1, 3, 5, 10, 20):
        agent.max_tool_rounds = n
        assert agent.hard_tool_round_limit == n * 2


def test_configured_settings_yields_20_rounds():
    """当前配置基线（10）× 2 = 实际 20 轮——写死此断言使回调被显式发现。"""
    agent = object.__new__(ChatAgent)
    agent.max_tool_rounds = _load_chat_cfg()["max_tool_rounds"]
    assert agent.hard_tool_round_limit == 20


class _ForeverToolClient:
    """模型永不停止调工具：每轮都返回 tool_calls，直到请求不带 tools。"""

    def __init__(self):
        self.calls = []
        self.tool_rounds = 0

    def create(self, **params):
        self.calls.append(params)
        if "tools" not in params:
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="最终回答", tool_calls=None,
                                        reasoning_content=""),
                finish_reason="stop")])
        self.tool_rounds += 1
        tc = SimpleNamespace(
            id=f"c{self.tool_rounds}", type="function",
            function=SimpleNamespace(name="ok_tool", arguments="{}"))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[tc], reasoning_content=""),
            finish_reason="tool_calls")])


def _build_agent(client, max_tool_rounds):
    agent = object.__new__(ChatAgent)
    agent._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=client.create)))
    agent._model = "deepseek-v4-flash"
    agent.max_tool_rounds = max_tool_rounds
    agent.max_failed_rounds = 3
    agent.max_result_length = 4000
    agent.max_tokens = 384000
    agent.stream = False
    agent._last_reply_printed = False
    agent._messages = [{"role": "system", "content": "s"}]
    agent._session_store = None
    return agent


def _run(client, max_tool_rounds):
    agent = _build_agent(client, max_tool_rounds)
    saved = dict(chat_tools.TOOL_REGISTRY)
    chat_tools.TOOL_REGISTRY["ok_tool"] = lambda **kw: "正常结果"
    try:
        with redirect_stdout(io.StringIO()):
            reply = agent._run_conversation()
    finally:
        chat_tools.TOOL_REGISTRY.clear()
        chat_tools.TOOL_REGISTRY.update(saved)
    return agent, reply


def test_loop_stops_exactly_at_hard_cap():
    """模型无限调工具时，成功轮严格停在 hard_cap，之后恰好一次不带 tools 的兜底调用。"""
    client = _ForeverToolClient()
    agent, reply = _run(client, max_tool_rounds=3)          # 硬上限 6

    assert client.tool_rounds == 6
    assert sum(1 for c in client.calls if "tools" not in c) == 1
    assert any("轮次已达上限" in str(m.get("content", "")) for m in agent._messages)
    assert reply == "最终回答"


def test_hard_cap_scales_with_config():
    """配置翻倍 → 实际轮数翻倍（锁住"改配置真的生效"，防 ×2 出口被写死）。"""
    for rounds in (1, 2, 5):
        client = _ForeverToolClient()
        _run(client, max_tool_rounds=rounds)
        assert client.tool_rounds == rounds * 2
