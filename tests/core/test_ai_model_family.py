"""模型能力判定 + 上下文规格回归测试（2026-09-11，deepseek-flash 改名事故）。

事故：DeepSeek 2026-09-10 发布 V4.1 Flash，官方 API 名改为 deepseek-flash。
项目随之改名后，全库 10 处 `str(model).startswith("deepseek-v4")` 判断全部失配
→ 不再传 thinking.type=disabled → 全项目静默退回思考模式（官方默认 effort=high）。

本文件锁死四条：
1. 模型系判定用「deepseek 前缀」而非某个版本号——官方换名不会再打翻；
2. **settings.yaml 里实际配置的模型名必须被判定识别**（改名事故的直接回归）；
3. 上下文窗口按官方数值写进配置（改前先回官方文档复核，见注释出处）；
4. 全库不再残留 `startswith("deepseek-v4")` 这类硬编码版本判定（扫同类点转测试）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import yaml

from src.core.ai_model import (
    CONTEXT_WINDOW_FALLBACK, DEFAULT_DEEPSEEK_MODEL, estimate_messages_tokens,
    estimate_tokens, is_deepseek_thinking_model, resolve_context_window,
    thinking_disabled_body,
)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SETTINGS = os.path.join(_ROOT, "configs", "settings.yaml")

# 2026-09-11 实测锚点：真实 API 返回 usage.prompt_tokens=4898，对应
# system prompt(3988字) + tool 定义(6634字) + 短提问(19字) 的一次请求。
_MEASURED_PROMPT_TOKENS = 4898


def _load_ai_cfg() -> dict:
    with open(_SETTINGS, encoding="utf-8") as f:
        return yaml.safe_load(f)["ai"]


# ── 1. 模型系判定 ────────────────────────────────────────

def test_current_and_legacy_deepseek_names_all_detected():
    """当前官方名与 legacy 别名都必须被识别（否则 thinking 关不掉）。"""
    for name in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4.1-flash",
                 "deepseek-v4-pro", "deepseek-flash-vision-exp", "DeepSeek-Flash"):
        assert is_deepseek_thinking_model(name) is True, name


def test_future_deepseek_name_auto_covers():
    """用前缀判定：官方将来发 deepseek-xxx 新模型无需改 10 处调用点。"""
    assert is_deepseek_thinking_model("deepseek-something-2027") is True


def test_settings_configured_model_is_detected():
    """改名事故的直接回归：settings.yaml 里配的模型必须被判为 DeepSeek 思考型。

    这条测试若在 2026-09-11 之前就存在，改名当天就会红——而不是等人工审查发现。
    """
    cfg = _load_ai_cfg()
    provider = cfg["provider"]
    model = cfg[provider]["model"]
    if provider == "deepseek":
        assert is_deepseek_thinking_model(model) is True, (
            f"settings.yaml ai.{provider}.model={model} 未被识别为 DeepSeek 思考型模型，"
            f"会导致 thinking 关不掉（成本/延迟静默劣化）")


def test_legacy_non_thinking_and_other_providers_excluded():
    """deepseek-chat 是非思考模型（传 thinking 会参数非法）；kimi 不是 DeepSeek。"""
    assert is_deepseek_thinking_model("deepseek-chat") is False
    assert is_deepseek_thinking_model("kimi-k2.6") is False
    assert is_deepseek_thinking_model(None) is False
    assert is_deepseek_thinking_model("") is False


def test_thinking_disabled_body_shape():
    """请求体形状须与官方文档一致：extra_body={"thinking":{"type":"disabled"}}。"""
    assert thinking_disabled_body("deepseek-flash") == {
        "extra_body": {"thinking": {"type": "disabled"}}}
    assert thinking_disabled_body("kimi-k2.6") == {}
    assert thinking_disabled_body(None) == {}


def test_default_model_constant_is_current_official_name():
    assert DEFAULT_DEEPSEEK_MODEL == "deepseek-flash"


def test_no_hardcoded_model_version_predicate_left_in_src():
    """扫同类点转测试：src/ 下不得再有 `startswith("deepseek-<版本号>")` 式硬编码判定。

    用 AST 结构化检测而不是字符串匹配——否则本仓库自己的 docstring/注释
    （引用旧写法说明事故经过）会被误判成违规代码。
    """
    import ast

    offenders = []
    for root, dirs, files in os.walk(os.path.join(_ROOT, "src")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn)
            with open(p, encoding="utf-8", errors="replace") as f:
                try:
                    tree = ast.parse(f.read())
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "startswith"):
                    continue
                if not node.args:
                    continue
                arg = node.args[0]
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                # 只拦"带版本号"的前缀（deepseek-v4 / deepseek-2027...），
                # 允许 deepseek-chat 这类按能力命名的前缀
                if re.match(r"^deepseek-\d", arg.value.strip().lower()):
                    offenders.append(f"{p}:{node.lineno} startswith({arg.value!r})")
    assert not offenders, f"仍有硬编码模型版本判定（官方换名即失效）: {offenders}"


# ── 2. 上下文规格 ────────────────────────────────────────

def test_official_context_windows_in_settings():
    """官方窗口数值锁死（改前先回官方文档复核，出处见 settings.yaml 行内注释）。"""
    cfg = _load_ai_cfg()
    assert cfg["deepseek"]["context_window"] == 1_000_000
    assert cfg["kimi"]["context_window"] == 262_144


def test_resolve_context_window_reads_config_and_falls_back():
    cfg = {"ai": {"deepseek": {"context_window": 1000000},
                  "kimi": {"context_window": 262144}}}
    assert resolve_context_window(cfg, "deepseek") == 1_000_000
    assert resolve_context_window(cfg, "kimi") == 262_144
    # 未知 provider / 非法值 -> 取已核实窗口里较小的那个（保守）
    assert resolve_context_window(cfg, "openai") == CONTEXT_WINDOW_FALLBACK
    assert resolve_context_window({"ai": {"x": {"context_window": "abc"}}}, "x") == CONTEXT_WINDOW_FALLBACK
    assert resolve_context_window({"ai": {"x": {"context_window": 0}}}, "x") == CONTEXT_WINDOW_FALLBACK
    assert resolve_context_window({}, "deepseek") == CONTEXT_WINDOW_FALLBACK


# ── 3. token 估算（用真实 API 实测值校准）────────────────

def test_estimate_tokens_calibrated_within_20pct_of_measured():
    """估算器须贴合实测 4898 tokens（允许 ±20%，方向偏保守=高估）。"""
    from src.chat.prompts import CHAT_SYSTEM_PROMPT, TOOL_DEFINITIONS
    est = estimate_messages_tokens(
        [{"role": "system", "content": CHAT_SYSTEM_PROMPT},
         {"role": "user", "content": "请查询我的持仓，只调用工具，不要解释。"}],
        TOOL_DEFINITIONS)
    dev = (est - _MEASURED_PROMPT_TOKENS) / _MEASURED_PROMPT_TOKENS
    assert -0.20 <= dev <= 0.30, f"估算 {est} 相对实测 {_MEASURED_PROMPT_TOKENS} 偏差 {dev:.1%}"
    assert est >= _MEASURED_PROMPT_TOKENS * 0.9, "宁可高估，不要低估"


def test_estimate_tokens_basic_properties():
    assert estimate_tokens("") == 0            # 空文本零开销（每条消息的固定开销另计）
    assert estimate_tokens("a" * 350) < estimate_tokens("汉" * 350), "中文更费 token"
    assert estimate_tokens("汉" * 100) > estimate_tokens("汉" * 10)


def test_estimate_messages_tokens_counts_tools_and_tool_calls():
    """tools 定义是每轮固定开销（实测约 2.8K），不能漏算；tool_calls 参数也要算。"""
    msgs = [{"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "get_portfolio", "arguments": "{\"x\": \"" + "1" * 500 + "\"}"}}]}]
    without_tools = estimate_messages_tokens(msgs)
    with_tools = estimate_messages_tokens(msgs, [{"type": "function", "function": {"name": "f"}}])
    assert with_tools > without_tools
    assert without_tools > 100, "tool_calls arguments 必须计入"


def test_estimate_messages_tokens_tolerates_odd_input():
    """非 dict / None content 不得抛异常（历史消息可能来自旧版本存档）。"""
    assert estimate_messages_tokens([{"role": "user", "content": None}, "not-a-dict", {}]) >= 0
    assert estimate_messages_tokens(None) == 0
