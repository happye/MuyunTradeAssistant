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

# ── 模型名常量（唯一出处；模型换代只改这里，别在调用点写字符串字面量）──

# benzong/theme_locator 等处的"AI 未配置模型名"兜底值。
# 原值为已改名的 deepseek-v4-flash（仍兼容但属旧名），统一为官方当前名。
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"

# Kimi 当前在售模型名兜底（官方 model 清单见 platform.moonshot.cn/docs/pricing/chat）
DEFAULT_KIMI_MODEL = "kimi-k2.6"

# 不接受 temperature/top_p 等采样参数的模型前缀（Kimi K2.x 系列）。
# 原先 chat/agent._should_pass_temperature 与 core/ai_modifier._should_pass_temperature
# **各硬编码一份**——模型换代时极易只改一处（同一类"散落字面量"正是本次模型改名事故的根因）。
# 新增 Kimi 型号时先确认其采样参数支持情况，再决定是否加入本清单。
NO_TEMPERATURE_MODELS = ("kimi-k2.6", "kimi-k2.5")


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


# 官方换算比例（DeepSeek 官方「Token 用量计算」文档，2026-09-11 核对）：
#   https://api-docs.deepseek.com/quick_start/token_usage
#   「1 个英文字符 ≈ 0.3 个 token；1 个中文字符 ≈ 0.6 个 token」
#   即 1 token ≈ 3.33 英文字符 ≈ 1.67 汉字。Kimi 官方口径一致（普通中文 1 token ≈ 1.5-2 汉字）。
_TOKENS_PER_CJK = 0.6
_TOKENS_PER_OTHER = 0.3
# 结构开销系数：官方比例只描述"自然语言"，而真实请求还带 tools 的 JSON schema、role 标记、
# 特殊 token。实测两条真实请求（prompt_tokens 4893 / 4898）对比按官方比例算出的 4366，
# 真实值高约 12%。取 1.15 使估算略偏保守——护栏宁可早折叠，也不要在真超窗口时才被动报错。
_STRUCTURE_OVERHEAD = 1.15


def estimate_tokens(text: str) -> int:
    """粗估一段文本的 token 数（用于上下文护栏预算，非精确计费）。

    基准 = **官方换算比例**（中文 0.6 token/字、英文 0.3 token/字符）× 结构开销 1.15。
    校准（2026-09-11 实测）：system prompt(3988字) + tools 定义(6634字) + 短提问(19字)
    对应真实 usage.prompt_tokens = 4893/4898，本估算约 5025（+2.7%，偏保守方向）。

    历史教训：初版用 CJK/1.2（= 0.83 token/字），比官方 0.6 高估 39%——结果虽因
    恰好抵消结构开销而看起来准，但"系数无出处"不可维护，故改为官方比例 + 显式开销项。
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
    return int((cjk * _TOKENS_PER_CJK + other * _TOKENS_PER_OTHER) * _STRUCTURE_OVERHEAD) + 4


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
