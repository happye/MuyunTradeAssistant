"""AI 模型能力判定与上下文规格（单一事实源）。

## 为什么需要这个模块（2026-09-11 实测事故）

DeepSeek 于 2026-09-10 发布 V4.1 Flash，官方 API 模型名改为 ``deepseek-flash``
（legacy ``deepseek-v4-flash`` 仍兼容并路由到同一模型）。项目 settings.yaml 随之改名后，
全库 10 处 ``str(model).startswith("deepseek-v4")`` 判断**全部失配**：

- 这些判断的用途是「DeepSeek 思考型模型默认开启思考模式 → 显式传 thinking.type=disabled」；
- 失配后不再传该参数 → **全项目静默退回思考模式（官方默认 effort=high）**；
- 实测证据：模型 deepseek-flash + tools 时，不传 thinking 的响应带 reasoning_content
  （思考中，len=79），传 thinking.type=disabled 的响应无 reasoning_content（未思考）。
- 副作用：思考模式不支持 temperature（官方原文：设置不报错但**不生效**）、top_p 被抬到
  下限 0.95、输出 token 大增（输出价 2 倍档），而项目所有 prompt 都按非思考模式调过。

官方出处（2026-09-11 核对）：
- 模型与上下文：https://api-docs.deepseek.com/quick_start/pricing
  「Use ``deepseek-flash`` as the model name」「CONTEXT LENGTH 1M」「MAX OUTPUT MAXIMUM: 384K」
  「THINKING MODE Supports both non-thinking and thinking (**default**) modes」
- 思考开关语法：https://api-docs.deepseek.com/guides/thinking_mode
  ``{"thinking": {"type": "enabled/disabled"}}``（OpenAI 格式经 extra_body 传入）
  「Thinking mode is enabled by default, with the default effort being ``high``」

## 使用方式

想"非思考"就在请求里展开本模块的返回值：

    **thinking_disabled_body(model)

非 DeepSeek 思考型模型返回空 dict，展开后零影响。
"""

from typing import Any

# 官方在售 / 兼容的 DeepSeek 思考型模型名前缀（2026-09-11 核）。
# 注意：不要写成 "deepseek-v4"——那正是本次事故的根因（改名后失配）。
_DEEPSEEK_PREFIX = "deepseek"

# 历史遗留的非思考模型（deepseek-chat 系列，2026-07-24 已下线）。
# 它们不接受 thinking 参数，传了会被判参数非法，故显式排除。
_NON_THINKING_LEGACY = ("deepseek-chat",)

# benzong/theme_locator 等处的"AI 未配置模型名"兜底值。
# 原值为已改名的 deepseek-v4-flash（仍兼容但属旧名），统一为官方当前名。
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"


def is_deepseek_thinking_model(model: Any) -> bool:
    """模型是否属于「默认开启思考、需显式关闭」的 DeepSeek 系模型。

    判定用前缀 ``deepseek``（而不是某一个版本号），这样官方每次发新模型
    （deepseek-flash / 未来的 deepseek-xxx）都自动覆盖，不必再改 10 处调用点。
    deepseek-chat 系列为非思考模型，显式排除。
    """
    name = str(model or "").strip().lower()
    if not name.startswith(_DEEPSEEK_PREFIX):
        return False
    return not name.startswith(_NON_THINKING_LEGACY)


def estimate_tokens(text: str) -> int:
    """粗估一段文本的 token 数（用于上下文护栏预算，非精确计费）。

    系数来自 2026-09-11 实测校准：真实 API 返回 usage.prompt_tokens=4898，
    对应 chat 的 system prompt(3988 字) + tools 定义(6634 字) + 短提问(19 字)。
    取 CJK/1.2 + 非CJK/3.5 时估算 5187（**高估 5.9%**，偏保守）——
    护栏宁可早触发折叠，也不要在真超窗口时才被动报错。

    参考量级（官方）：Kimi 文档称中文 1 token ≈ 1.5-2 汉字。
    注意：本估算**不含图片输入**（项目当前不发送图片）。
    """
    if not text:
        return 0
    cjk = 0
    for ch in text:
        if ("\u4e00" <= ch <= "\u9fff"          # 汉字
                or "\u3000" <= ch <= "\u303f"    # 中文标点
                or "\uff00" <= ch <= "\uffef"):  # 全角符号
            cjk += 1
    other = len(text) - cjk
    return int(cjk / 1.2 + other / 3.5) + 4


def estimate_messages_tokens(messages: list, tools: Any = None) -> int:
    """估算一次 chat.completions 请求的输入 token 总量。

    计入：每条消息 content、assistant 的 tool_calls arguments、以及 tools 定义本身
    （后者是每次请求都带上的固定开销，实测约 2.8K tokens，不能漏算）。
    """
    total = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif content:
            total += estimate_tokens(str(content))
        for tc in (m.get("tool_calls") or []):
            fn = (tc or {}).get("function") or {}
            total += estimate_tokens(str(fn.get("name", "")))
            total += estimate_tokens(str(fn.get("arguments", "")))
        total += 4  # 每条消息的角色/分隔开销
    if tools:
        try:
            import json
            total += estimate_tokens(json.dumps(tools, ensure_ascii=False))
        except (TypeError, ValueError):
            total += estimate_tokens(str(tools))
    return total


# 未在 settings.yaml 声明窗口时的兜底值：取两个已核实窗口里**较小**的那个
# （Kimi k2.6 = 262,144，官方 platform.moonshot.cn/docs/pricing/chat），
# 宁可保守也不要按大窗口放行。
CONTEXT_WINDOW_FALLBACK = 262_144


def resolve_context_window(config: dict, provider: str) -> int:
    """读取当前 provider 的上下文窗口（settings.yaml ai.<provider>.context_window）。

    官方数值（2026-09-11 核对，改前先回官方文档复核）：
    - deepseek 1,000,000  https://api-docs.deepseek.com/quick_start/pricing
    - kimi       262,144  https://platform.moonshot.cn/docs/pricing/chat
    """
    try:
        val = ((config or {}).get("ai", {}).get(provider, {}) or {}).get("context_window")
        ival = int(val)
        return ival if ival > 0 else CONTEXT_WINDOW_FALLBACK
    except (TypeError, ValueError):
        return CONTEXT_WINDOW_FALLBACK


def thinking_disabled_body(model: Any) -> dict:
    """返回可直接 ``**`` 展开进 create() 的 thinking 关闭参数。

    - DeepSeek 思考型模型 -> ``{"extra_body": {"thinking": {"type": "disabled"}}}``
    - 其他（kimi 等）      -> ``{}``（展开后等价不传）

    这样调用点只写 ``**thinking_disabled_body(model)``，不必各自维护判断分支。
    """
    if is_deepseek_thinking_model(model):
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}
