"""Chat Agent — 自然语言对话代理 (v0.8.0 Phase 5)

核心流程：
1. 用户输入 → 发送给AI（含function定义）
2. AI判断是否需要调用工具 → 返回tool_calls或纯文本
3. 如有tool_calls → 执行工具 → 将结果回传AI → AI生成回复
4. 如无tool_calls → 直接输出AI文本回复

架构约束：
- 独立OpenAI客户端（不复用AIModifier实例）
- 纯文本输出（不使用Rich Console）
- 对话历史管理（滑动窗口）
"""

import os
import json
import logging
from typing import Optional

from openai import OpenAI

from src.chat.prompts import CHAT_SYSTEM_PROMPT, TOOL_DEFINITIONS
from src.chat.tools import TOOL_REGISTRY, init_engines

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_MAX_HISTORY = 20
DEFAULT_MAX_TOOL_ROUNDS = 3
DEFAULT_MAX_RESULT_LENGTH = 4000
# max_tokens 默认值：DeepSeek-V4 单次输出上限 384K token（官方文档 api-docs.deepseek.com）。
# 拉满使用——不自行设小的额度，避免长对话超额被静默截断、回答不完整损失用户。
# 边界保护见 _call_api()：若 API 拒绝该值（超实际上限）自动降级重试。
DEFAULT_MAX_TOKENS = 384000


class ChatAgent:
    """Chat Agent — 自然语言对话代理"""

    def __init__(self, config: dict):
        """初始化Chat Agent

        Args:
            config: settings.yaml完整配置
        """
        self.config = config

        # Chat配置
        chat_cfg = config.get("chat", {})
        self.max_history = chat_cfg.get("max_history_messages", DEFAULT_MAX_HISTORY)
        self.max_tool_rounds = chat_cfg.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)
        self.max_result_length = chat_cfg.get("max_result_length", DEFAULT_MAX_RESULT_LENGTH)
        self.max_tokens = chat_cfg.get("max_tokens", DEFAULT_MAX_TOKENS)

        # 初始化底层引擎
        init_engines(config)

        # 初始化AI客户端（独立于AIModifier）
        self._client: Optional[OpenAI] = None
        self._model: str = ""
        self._init_ai_client()

        # 对话历史
        self._messages: list[dict] = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT}
        ]

    def _init_ai_client(self):
        """初始化AI客户端（复用settings.yaml的ai配置）"""
        ai_config = self.config.get("ai", {})
        if not ai_config.get("enabled", False):
            logger.warning("Chat Agent: AI未启用")
            return

        provider = ai_config.get("provider", "deepseek")
        provider_cfg = ai_config.get(provider, {})

        # API Key: 配置文件 > 环境变量
        api_key = provider_cfg.get("api_key", "")
        if not api_key:
            env_key = f"{provider.upper()}_API_KEY"
            api_key = os.environ.get(env_key, "")

        if not api_key:
            logger.warning(f"Chat Agent: {provider} API Key未配置")
            return

        base_url = provider_cfg.get("base_url", "")
        self._model = provider_cfg.get("model", "")

        try:
            self._client = OpenAI(api_key=api_key, base_url=base_url)
            logger.info(
                f"Chat Agent initialized: provider={provider}, "
                f"model={self._model}"
            )
        except Exception as e:
            logger.error(f"Chat Agent初始化失败: {e}")

    @staticmethod
    def _should_pass_temperature(model: str) -> bool:
        """判断模型是否支持temperature参数

        kimi-k2.6 和 kimi-k2.5 不支持 temperature/top_p 等参数。
        与 AIModifier._should_pass_temperature() 逻辑一致。
        """
        unsupported_models = ("kimi-k2.6", "kimi-k2.5")
        return not model.startswith(unsupported_models)

    def _call_api(self, base_params: dict):
        """调用 chat.completions.create，注入 max_tokens 并兜底降级。

        - 显式传 max_tokens（默认拉满 384K，DeepSeek-V4 输出上限），避免服务端
          小默认值导致长回答被静默截断。
        - 边界保护：若 API 拒绝该 max_tokens（超模型实际上限），降级到 32768 重试一次，
          正常情况下不触发。
        """
        params = dict(base_params)
        params["max_tokens"] = self.max_tokens
        # DeepSeek-V4 默认思考模式；保持非思考（与旧 deepseek-chat 行为一致）需 thinking.type=disabled。
        # kimi 不支持 thinking 参数，不传。
        if self._model and self._model.startswith("deepseek"):
            params["extra_body"] = {"thinking": {"type": "disabled"}}
        try:
            return self._client.chat.completions.create(**params)
        except Exception as e:
            err = str(e).lower()
            if "max_tokens" in err or "maximum" in err or "too long" in err or "exceed" in err:
                logger.warning(
                    f"Chat Agent: max_tokens={self.max_tokens} 被API拒绝({e})，降级到32768重试"
                )
                params["max_tokens"] = 32768
                return self._client.chat.completions.create(**params)
            raise

    def chat(self, user_input: str) -> str:
        """处理用户输入，返回AI回复

        Args:
            user_input: 用户输入的自然语言

        Returns:
            AI回复文本
        """
        if not self._client:
            return "Chat Agent未初始化（AI API未配置）。请在configs/settings.yaml中配置AI。"

        # 追加用户消息
        self._messages.append({"role": "user", "content": user_input})

        # 裁剪历史（保留system消息 + 最近N条）
        if len(self._messages) > self.max_history + 1:
            system_msg = self._messages[0]
            recent = self._messages[-self.max_history:]
            self._messages = [system_msg] + recent

        try:
            return self._run_conversation()
        except Exception as e:
            logger.error(f"Chat Agent对话异常: {e}")
            return f"对话处理出错: {e}"

    def _run_conversation(self) -> str:
        """执行一轮对话（含function calling循环）

        最多 self.max_tool_rounds 轮工具调用（防止无限循环）
        """
        for round_num in range(self.max_tool_rounds):
            # 调用AI
            api_params = {
                "model": self._model,
                "messages": self._messages,
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
            }
            if self._should_pass_temperature(self._model):
                api_params["temperature"] = 0.3
            api_params["timeout"] = 60  # Chat模式需要更长超时

            response = self._call_api(api_params)
            message = response.choices[0].message

            # 检查是否需要调用工具
            tool_calls = message.tool_calls

            if not tool_calls:
                # 无工具调用 → 直接返回文本
                assistant_content = message.content or ""
                # 检测输出截断（finish_reason=length 表示超 max_tokens 或上下文长度）
                if response.choices[0].finish_reason == "length":
                    assistant_content += (
                        "\n\n⚠️ 本回复触及模型输出上限被截断（finish_reason=length），"
                        "可输入\"继续\"补全。"
                    )
                    logger.warning("Chat Agent: 回复被max_tokens截断(finish_reason=length)")
                self._messages.append({
                    "role": "assistant",
                    "content": assistant_content
                })
                return assistant_content

            # 有工具调用 → 执行工具 → 继续对话
            # 手动构建assistant消息dict（含tool_calls，不用model_dump()）
            self._messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in message.tool_calls
                ],
            })

            for tool_call in tool_calls:
                func_name = tool_call.function.name
                func_args_str = tool_call.function.arguments

                try:
                    func_args = json.loads(func_args_str)
                except json.JSONDecodeError:
                    func_args = {}

                # 执行工具
                logger.info(f"Chat Agent调用工具: {func_name}({func_args})")
                tool_result = self._execute_tool(func_name, func_args)

                # 将工具结果加入历史
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # 超过最大轮次，再做一次无工具的调用获取最终回复
        logger.info("Chat Agent: 达到最大工具调用轮次，获取最终回复")
        final_params = {
            "model": self._model,
            "messages": self._messages,
        }
        if self._should_pass_temperature(self._model):
            final_params["temperature"] = 0.3
        final_params["timeout"] = 60

        final_response = self._call_api(final_params)
        final_content = final_response.choices[0].message.content or ""
        # 检测输出截断
        if final_response.choices[0].finish_reason == "length":
            final_content += (
                "\n\n⚠️ 本回复触及模型输出上限被截断（finish_reason=length），"
                "可输入\"继续\"补全。"
            )
            logger.warning("Chat Agent: 最终回复被max_tokens截断(finish_reason=length)")
        self._messages.append({"role": "assistant", "content": final_content})
        return final_content

    def _execute_tool(self, func_name: str, func_args: dict) -> str:
        """执行单个工具函数

        Args:
            func_name: 工具名称
            func_args: 工具参数

        Returns:
            工具执行结果（纯文本）
        """
        func = TOOL_REGISTRY.get(func_name)
        if not func:
            return f"未知工具: {func_name}"

        try:
            result = func(**func_args)
            # 截断过长的结果（防止token爆炸）。审查修复 M5：原 result[:max-100] 截断末尾，
            # 但 format_analysis_result 把 决策理由/风险提示 放在末尾 -> 被切掉，AI 基于残缺信息回答。
            # 改为保留首尾（决策在前 + 理由/风险在末），截断中间。
            if len(result) > self.max_result_length:
                keep = (self.max_result_length - 100) // 2
                result = result[:keep] + "\n\n...(中间部分截断)...\n\n" + result[-keep:]
            return result
        except TypeError as e:
            return f"工具参数错误: {e}"
        except Exception as e:
            logger.error(f"工具执行异常 {func_name}: {e}")
            return f"工具执行失败: {e}"

    def reset_history(self):
        """重置对话历史（保留system消息）"""
        self._messages = [self._messages[0]]


def run_chat_repl(config: dict):
    """Chat REPL主循环

    Args:
        config: settings.yaml完整配置
    """
    agent = ChatAgent(config)

    print()
    print("=" * 50)
    print("  暮云思辨投资助手 - Chat Agent Mode")
    print("  输入自然语言与AI对话，输入 q 退出")
    print("=" * 50)
    print()

    if not agent._client:
        print("  [!] AI未配置，请在 configs/settings.yaml 中配置 AI API Key")
        print("  [!] 仍然可以尝试对话（部分功能可能不可用）")
        print()

    while True:
        try:
            user_input = input("chat> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  退出Chat模式")
            break

        if not user_input:
            continue

        if user_input.lower() in ("q", "quit", "exit"):
            print("  退出Chat模式")
            break

        if user_input.lower() in ("h", "help", "?"):
            print()
            print("  可用操作（自然语言）：")
            print("    - 查询行业: '半导体板块有哪些股票'")
            print("    - 分析股票: '分析 600519' 或 '茅台怎么样'")
            print("    - 市场扫描: '帮我扫描放量突破的股票'")
            print("    - 查看持仓: '我的持仓' 或 '查看持仓'")
            print("    - 获取新闻: '600519有什么新闻'")
            print("    - 策略查询: '止损怎么设' 或 '套牢了怎么办' (v0.8.1)")
            print("    - 重置对话: 'reset'")
            print("    - 退出: 'q'")
            print()
            continue

        if user_input.lower() == "reset":
            agent.reset_history()
            print("  对话历史已重置")
            continue

        # 调用Chat Agent
        try:
            reply = agent.chat(user_input)
            print()
            print(reply)
            print()
        except Exception as e:
            print(f"\n  [错误] {e}\n")
