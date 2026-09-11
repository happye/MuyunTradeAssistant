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
import re
import json
import logging
from types import SimpleNamespace
from typing import Optional

from openai import OpenAI

from src.chat.prompts import CHAT_SYSTEM_PROMPT, TOOL_DEFINITIONS
from src.chat.session_store import SessionStore
from src.chat.tools import (
    TOOL_REGISTRY, init_engines, shutdown_engines, TOOL_ERROR_MARK,
)
from src.core.ai_model import (
    CONTEXT_WINDOW_FALLBACK, estimate_messages_tokens,
    is_deepseek_thinking_model, resolve_context_window, thinking_disabled_body,
)

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_MAX_HISTORY = 20
# 工具轮次默认值：实际硬上限 = 该值 × 2（见 ChatAgent.hard_tool_round_limit）——10 ⇒ 20 轮。
# 仅 settings.yaml chat.max_tool_rounds 缺失时兜底；2026-09-11 由 3 上调与配置基线对齐
DEFAULT_MAX_TOOL_ROUNDS = 10
DEFAULT_MAX_RESULT_LENGTH = 4000
# max_tokens 默认值：DeepSeek-V4 单次输出上限 384K token（官方文档 api-docs.deepseek.com）。
# 拉满使用——不自行设小的额度，避免长对话超额被静默截断、回答不完整损失用户。
# 边界保护见 _call_api()：若 API 拒绝该值（超实际上限）自动降级重试。
DEFAULT_MAX_TOKENS = 384000
# chat对话落盘目录（ISS-061顺带消化交接文档待办#2：每天一个文件，追加）
CHAT_REPORT_DIR = "./分析报告/chat"
# 会话持久化默认开关（ISS-092 聊天中断恢复）：逐消息落盘 ~/.muyun/chat_sessions/，
# q退出/崩溃/发送失败中断后，下次启动 chat 可恢复继续。settings.yaml chat.session_persist 可关
DEFAULT_SESSION_PERSIST = True
# 工具全部失败的连续轮次上限：失败轮不占 max_tool_rounds 轮次（失败信息回传AI，
# AI自行重试），但连续全失败超过此次数则停止循环（防工具坏了无限重试）。
DEFAULT_MAX_FAILED_ROUNDS = 3
# 上下文护栏（2026-09-11）：轮次上限提到 20 后，单轮内工具结果累计量翻倍。
# 预算口径 = provider 上下文窗口（官方值，见 ai.<provider>.context_window）× 该比例。
DEFAULT_CONTEXT_BUDGET_RATIO = 0.85
DEFAULT_KEEP_RECENT_TOOL_RESULTS = 6

# 伪工具调用文本：模型在请求不带tools时（如硬上限后的兜底调用）还想调工具，
# 会把 <tool_calls> 当正文输出并停止，用户看到的是一段假XML而非回答。剥离用。
_FAKE_TOOL_CALL_RE = re.compile(r"<tool_calls>.*?(?:</tool_calls>|$)", re.DOTALL)

# 被上下文护栏折叠过的工具结果前缀（幂等标记：二次折叠不重复处理）
_FOLDED_MARK = "[早前工具结果已折叠]"


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
        self.max_failed_rounds = chat_cfg.get("max_failed_rounds", DEFAULT_MAX_FAILED_ROUNDS)
        self.max_result_length = chat_cfg.get("max_result_length", DEFAULT_MAX_RESULT_LENGTH)
        self.persist = chat_cfg.get("persist", False)
        self.max_tokens = chat_cfg.get("max_tokens", DEFAULT_MAX_TOKENS)
        # 流式输出：正文增量打印（工具进度流式在ISS-059已做）；流式失败自动回退非流式
        self.stream = chat_cfg.get("stream", True)
        # 上一轮回答是否已流式打印到控制台（REPL据此避免重复打印）
        self._last_reply_printed = False

        # 上下文护栏（2026-09-11）：窗口按 provider 官方值取（settings.yaml ai.<provider>.context_window）
        guard_cfg = chat_cfg.get("context_guard", {}) or {}
        self.context_guard_enabled = guard_cfg.get("enabled", True)
        self.context_budget_ratio = guard_cfg.get("budget_ratio", DEFAULT_CONTEXT_BUDGET_RATIO)
        self.keep_recent_tool_results = guard_cfg.get(
            "keep_recent_tool_results", DEFAULT_KEEP_RECENT_TOOL_RESULTS)
        self._provider = (config.get("ai", {}) or {}).get("provider", "deepseek")
        self.context_window = resolve_context_window(config, self._provider)
        # 本次对话最近一次护栏状态（供 REPL/测试查看）
        self._last_context_estimate: Optional[int] = None

        # 会话持久化（ISS-092 聊天中断恢复）：逐消息落盘，启动时可恢复
        self.session_persist = chat_cfg.get("session_persist", DEFAULT_SESSION_PERSIST)
        self._session_store = SessionStore() if self.session_persist else None
        # 会话创建时间（首次落盘时定；resume 沿用原会话的，保持元数据连续）
        self._session_created: Optional[str] = None
        # current.json 归属权：resume/首次保存后本会话拥有该文件，可直接覆写；
        # 未拥有时首次保存前盘上若有文件先归档（防并发双开/漏网路径静默覆盖旧会话）
        self._owns_current_file = False
        # 最近一次发送是否失败（额度墙/网络异常），REPL 据此提示"会话已保存可恢复"
        self.last_send_error: Optional[str] = None

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

    @property
    def hard_tool_round_limit(self) -> int:
        """工具轮次硬上限 = max_tool_rounds × 2。

        循环 _run_conversation 与启动横幅共用同一出口，避免 ×2 系数散落多处后漂移。
        """
        return self.max_tool_rounds * 2

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
        if not base_url:
            logger.warning(f"Chat Agent: {provider} base_url未配置，可能误连OpenAI官方端点")
        self._model = provider_cfg.get("model", "")

        try:
            self._client = OpenAI(api_key=api_key, base_url=base_url, max_retries=2, timeout=180)
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

    def _build_params(self, base_params: dict) -> tuple[dict, str]:
        """构造 create 参数（thinking/max_tokens 按模型分派）。返回 (params, max_tokens键名)。

        DeepSeek 思考型模型默认开思考（官方默认 effort=high），项目按非思考模式设计
        （低延迟、低成本；且思考模式下 temperature 不生效），必须显式关闭。
        判定统一走 src.core.ai_model——2026-09-11 模型改名 deepseek-flash 后，
        原 `startswith("deepseek-v4")` 判定全线失配（详见该模块 docstring）。
        """
        params = dict(base_params)
        if is_deepseek_thinking_model(self._model):
            params.update(thinking_disabled_body(self._model))
            params["max_tokens"] = self.max_tokens
            _mt_key = "max_tokens"
        else:
            # kimi 等: 用 max_completion_tokens（kimi官方推荐，max_tokens已弃用）
            params["max_completion_tokens"] = self.max_tokens
            _mt_key = "max_completion_tokens"
        return params, _mt_key

    def _call_api(self, base_params: dict):
        """调用 chat.completions.create，注入 max_tokens 并兜底降级。

        - 显式传 max_tokens（默认拉满 384K，DeepSeek-V4 输出上限），避免服务端
          小默认值导致长回答被静默截断。
        - 边界保护：若 API 拒绝该 max_tokens（超模型实际上限），降级到 32768 重试一次，
          正常情况下不触发。
        """
        params, _mt_key = self._build_params(base_params)
        try:
            return self._client.chat.completions.create(**params)
        except Exception as e:
            err = str(e).lower()
            # 收窄匹配: 只对输出上限相关错误降级，避免context超限("maximum context length")误触发重试
            if "max_tokens" in err or "max_completion_tokens" in err or ("输出" in err and "超过" in err):
                for fallback in (32768, 8192, 4096):
                    logger.warning(
                        f"Chat Agent: {_mt_key}={self.max_tokens} 被API拒绝({e})，降级到{fallback}重试"
                    )
                    params[_mt_key] = fallback
                    try:
                        return self._client.chat.completions.create(**params)
                    except Exception as e2:
                        e2s = str(e2).lower()
                        if "max_tokens" in e2s or "max_completion_tokens" in e2s or ("输出" in e2s and "超过" in e2s):
                            continue
                        raise
                raise
            raise

    def _call_api_stream(self, base_params: dict):
        """流式调用：增量打印到控制台，返回 (message, finish_reason)。

        message 与非流式形状兼容（.content/.tool_calls/.reasoning_content）。
        创建阶段或中途异常向上抛，由调用方回退非流式 _call_api。
        """
        params, _ = self._build_params(base_params)
        stream = self._client.chat.completions.create(**params, stream=True)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict = {}   # index -> {"id","name","args": [str]}
        finish_reason = None
        printed = False

        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                delta = {}
            # 正文增量：实时打印
            piece = getattr(delta, "content", None)
            if piece:
                if not printed:
                    print()   # 与提示符换行
                    printed = True
                content_parts.append(piece)
                print(piece, end="", flush=True)
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_parts.append(rc)
            # 工具调用增量：按 index 聚合 id/name/arguments
            for tc in (getattr(delta, "tool_calls", None) or []):
                acc = tool_acc.setdefault(tc.index or 0, {"id": "", "name": "", "args": []})
                if tc.id:
                    acc["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        acc["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        acc["args"].append(fn.arguments)
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason

        if printed:
            print()   # 收尾换行

        # 组装与非流式兼容的 message 对象
        tool_calls = None
        if tool_acc:
            tool_calls = [
                SimpleNamespace(
                    id=acc["id"] or f"call_stream_{idx}",
                    type="function",
                    function=SimpleNamespace(name=acc["name"], arguments="".join(acc["args"])),
                )
                for idx, acc in sorted(tool_acc.items())
            ]
        message = SimpleNamespace(
            content="".join(content_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls,
        )
        return message, finish_reason

    @staticmethod
    def _trim_messages(messages: list[dict], max_history: int) -> list[dict]:
        """裁剪对话历史（保留 system + 最近 N 条），保证 tool/tool_calls 配对完整。

        裁剪切断 assistant(tool_calls)+tool 配对时，recent 开头会出现孤立 tool 消息
        （其 assistant tool_calls 已被裁掉）-> API 400
        "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'"。
        丢弃开头连续的孤立 tool 消息。
        """
        if max_history <= 0:
            return [messages[0]] if messages else []
        if len(messages) <= max_history + 1:
            return messages
        system_msg = messages[0]
        recent = messages[-max_history:]
        while recent and recent[0].get("role") == "tool":
            recent.pop(0)
        return [system_msg] + recent

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
        self._messages = self._trim_messages(self._messages, self.max_history)

        # 会话落盘（ISS-092）：发送失败/进程中断时提问已在磁盘上，
        # 下次启动恢复会话后不丢上下文
        self._persist_session()

        try:
            reply = self._run_conversation()
            self.last_send_error = None
            return reply
        except Exception as e:
            # 发送失败（额度墙/网络）：用户消息已入历史+落盘，
            # 退出后下次启动 chat 可恢复继续（REPL 据 last_send_error 提示）
            self.last_send_error = str(e)
            logger.error(f"Chat Agent对话异常: {e}")
            return f"对话处理出错: {e}"

    @staticmethod
    def _reply_parts(message, finish_reason: str) -> tuple[str, str]:
        """整理AI回复为 (base正文, suffix追加提示)。

        - content 为空：reasoning_content 兜底（思考型模型偶发空content，
          同 ai_modifier._parse_response 模式）；再空则占位提示。
        - 剥离伪 <tool_calls> XML 文本：模型在请求不带tools时还想调工具，
          会把工具调用当正文输出并停止（用户看到假XML、无真实回答）。
        - finish_reason=length：截断提示。
        流式输出时 base 已实时打印，只补打 suffix。
        """
        text = (message.content or "").strip()

        if not text:
            reasoning = getattr(message, "reasoning_content", "") or ""
            if reasoning.strip():
                logger.warning("Chat Agent: content为空，用reasoning_content兜底")
                text = reasoning.strip()
            else:
                logger.warning("Chat Agent: content为空且无reasoning_content")
                text = "（模型本次未返回有效回答，请重新提问或输入\"继续\"。）"

        suffix = ""
        # 剥离伪工具调用文本（模型无tools可用时可能把<tool_calls>当正文输出并停止）。
        # 含伪工具调用 = 模型本轮没答完（多半只剩一句"让我再搜索"引导语），
        # 必须提示用户，否则剥离后剩下的引导语仍是"无回答"。
        stripped = _FAKE_TOOL_CALL_RE.sub("", text).strip()
        if _FAKE_TOOL_CALL_RE.search(text):
            logger.warning("Chat Agent: 回复含伪工具调用文本，模型本轮未完成回答")
            if stripped:
                text = stripped
                suffix += ("\n\n⚠️ 模型试图继续调用工具，本次回答不完整，"
                           "可输入\"继续\"让模型继续。")
            else:
                text = "（模型本次仅返回工具调用占位文本，未生成回答，请重新提问或输入\"继续\"。）"
        else:
            text = stripped

        if finish_reason == "length":
            suffix += ("\n\n⚠️ 本回复触及模型输出上限被截断（finish_reason=length），"
                       "可输入\"继续\"补全。")
            logger.warning("Chat Agent: 回复被max_tokens截断(finish_reason=length)")
        return text, suffix

    @classmethod
    def _finalize_reply(cls, message, finish_reason: str) -> str:
        """完整最终文本 = base + suffix（非流式路径）。"""
        base, suffix = cls._reply_parts(message, finish_reason)
        return base + suffix

    # ── 上下文护栏（2026-09-11）─────────────────────────────

    def _fold_old_tool_results(self, keep_recent: int) -> int:
        """把最早的 tool 结果折叠成短桩，保留最近 keep_recent 条。返回折叠条数。

        用"折叠"而不是"删除"：tool 消息必须与其 assistant(tool_calls) 严格配对，
        删除会触发 API 400；改 content 则配对完整，只损失细节。
        已是折叠桩的跳过，保证幂等。
        """
        idxs = [i for i, m in enumerate(self._messages) if m.get("role") == "tool"]
        if len(idxs) <= keep_recent:
            return 0
        folded = 0
        for i in idxs[:len(idxs) - max(keep_recent, 0)]:
            content = self._messages[i].get("content") or ""
            if content.startswith(_FOLDED_MARK):
                continue
            self._messages[i]["content"] = (
                f"{_FOLDED_MARK} 原 {len(content)} 字已省略以控制上下文；"
                f"若仍需该数据请重新调用对应工具。"
            )
            folded += 1
        return folded

    def _guard_config(self) -> tuple[bool, float, int, int]:
        """读护栏配置 (启用, 预算比例, 保留最近N条, 窗口)。

        用 getattr 而非直接取属性：测试/轻量用法会用 object.__new__ 跳过 __init__
        （同 _persist_session 对 _session_store 的处理），此时退回模块默认值——
        CONTEXT_WINDOW_FALLBACK 取已核实窗口里较小的那个，宁可保守。
        """
        return (
            getattr(self, "context_guard_enabled", True),
            getattr(self, "context_budget_ratio", DEFAULT_CONTEXT_BUDGET_RATIO),
            getattr(self, "keep_recent_tool_results", DEFAULT_KEEP_RECENT_TOOL_RESULTS),
            getattr(self, "context_window", CONTEXT_WINDOW_FALLBACK),
        )

    def _guard_before_call(self) -> tuple[bool, str]:
        """请求前的上下文预算检查。返回 (是否可继续, 说明串)。

        1. 估算本次请求输入 token（system + tools + 全部消息，见 ai_model.estimate_messages_tokens）；
        2. 超过 窗口 × budget_ratio → 折叠最早的若干条工具结果（保留最近 N 条），并打印可见提示；
        3. 折叠后仍超过窗口本身 → 返回 False，调用方跳出循环改走"要求直接作答"兜底。
        """
        enabled, ratio, keep_recent, window = self._guard_config()
        if not enabled:
            return True, ""
        budget = int(window * ratio)
        estimate = estimate_messages_tokens(self._messages, TOOL_DEFINITIONS)
        self._last_context_estimate = estimate
        if estimate <= budget:
            return True, ""

        folded = self._fold_old_tool_results(keep_recent)
        estimate_after = estimate_messages_tokens(self._messages, TOOL_DEFINITIONS)
        self._last_context_estimate = estimate_after
        if folded:
            print(f"  🧹 上下文估算 {estimate_after:,}/{window:,} tokens"
                  f"（{100.0 * estimate_after / window:.0f}%），"
                  f"已折叠最早的 {folded} 条工具结果以控制上下文")
            logger.warning(
                f"chat上下文预算触发折叠: {folded}条工具结果 "
                f"(估算{estimate:,}->{estimate_after:,} tokens, 窗口{window})"
            )
        if estimate_after > window:
            logger.warning(
                f"chat上下文接近上限: 估算{estimate_after:,} tokens 超窗口{window}，停止调用工具"
            )
            return False, f"估算 {estimate_after:,} tokens 已超模型窗口 {window:,}"
        return True, ""

    def _run_conversation(self) -> str:
        """执行一轮对话（含function calling循环）

        轮次语义：
        - 工具轮次硬上限 = max_tool_rounds * 2（防无限循环）。
          复杂问题（如"行业看法+持仓+多阶段展望"）常需4-5个工具轮
          （查持仓→检索知识→个股分析→查新闻→再检索），3轮不够。
        - 失败轮不占轮次上限：一轮内所有工具都失败（TOOL_ERROR_MARK）时，
          失败信息已回传AI、AI下一轮可重试，不消耗轮次；
          连续全失败达 max_failed_rounds 则停止（防工具坏了无限重试）。
        - 原实现达到max_tool_rounds后最终调用不带tools，模型还想调工具时
          会把<tool_calls>伪XML当正文输出并停止 → 用户看到假工具调用、无真实回答。
          现改为：上限内每轮都带tools；超限后追加提示消息要求直接回答。
        - 上下文护栏（2026-09-11）：每轮请求前估算输入 token，超
          「provider 上下文窗口 × budget_ratio」即折叠最早的 tool 结果；
          折叠后仍超窗口则跳出并转入"要求直接作答"兜底。
        - 控制台流式进度：⏳等待AI（带 第X/N 轮）/ 模型叙述 / 🔧工具调用 / ✓✗结果。
        """
        hard_cap = self.hard_tool_round_limit
        round_num = 0
        consecutive_failures = 0
        context_stopped = False
        context_detail = ""
        while round_num < hard_cap and consecutive_failures < self.max_failed_rounds:
            # 上下文护栏：超预算先折叠旧工具结果，折叠后仍超窗口则跳出
            guard_ok, guard_detail = self._guard_before_call()
            if not guard_ok:
                context_stopped = True
                context_detail = guard_detail
                break
            # 调用AI（每轮都带tools：模型想调工具时走真tool_calls，而非输出伪XML文本）
            print(f"⏳ 等待 AI 响应...（第 {round_num + 1}/{hard_cap} 轮）")
            api_params = {
                "model": self._model,
                "messages": self._messages,
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
            }
            if self._should_pass_temperature(self._model):
                api_params["temperature"] = 0.3
            api_params["timeout"] = 180  # Chat模式需要更长超时

            # 流式优先（正文增量打印）；创建/中途失败回退非流式（max_tokens降级逻辑在非流式路径）
            streamed = False
            message = None
            finish_reason = None
            if self.stream:
                try:
                    message, finish_reason = self._call_api_stream(api_params)
                    streamed = True
                except Exception as e:
                    logger.warning(f"Chat Agent: 流式调用失败回退非流式: {e}")
                    print("  ⚠ 流式中断，回退完整响应...")
            if message is None:
                response = self._call_api(api_params)
                message = response.choices[0].message
                finish_reason = response.choices[0].finish_reason
            tool_calls = message.tool_calls

            if not tool_calls:
                # 无工具调用 → 整理并返回文本
                base, suffix = self._reply_parts(message, finish_reason)
                assistant_content = base + suffix
                # 正文已流式打印时 REPL 不再重复打印，只在此处补提示语
                self._last_reply_printed = bool(
                    streamed and (message.content or "").strip())
                if self._last_reply_printed and suffix.strip():
                    print(suffix.strip())
                self._messages.append({
                    "role": "assistant",
                    "content": assistant_content
                })
                self._persist_session()
                return assistant_content

            # 审查修复M1: tool_calls被max_tokens截断(finish_reason=length)时arguments多半残缺
            # -> json.loads失败->func_args={}->工具空参执行(AI装失忆)。检测length放弃执行残缺工具调用
            if finish_reason == "length":
                _trunc_content = (message.content or "") + (
                    "\n\n⚠️ 工具调用被截断（finish_reason=length），请重试或简化问题。"
                )
                logger.warning("Chat Agent: tool_calls被max_tokens截断(finish_reason=length)，放弃执行")
                self._messages.append({"role": "assistant", "content": _trunc_content})
                self._persist_session()
                self._last_reply_printed = streamed
                return _trunc_content

            # 有工具调用 → 流式显示模型叙述 + 执行工具 → 继续对话
            if not streamed and message.content and message.content.strip():
                print(f"\n{message.content.strip()}")
            # 手动构建assistant消息dict（含tool_calls，不用model_dump()）
            self._messages.append({
                "role": "assistant",
                "content": message.content or "",
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
            # 中途崩溃也能恢复到工具轮进行中的状态（ISS-092）
            self._persist_session()

            round_had_success = False
            for tool_call in tool_calls:
                func_name = tool_call.function.name
                func_args_str = tool_call.function.arguments

                try:
                    func_args = json.loads(func_args_str)
                except (json.JSONDecodeError, TypeError):
                    func_args = {}

                # 执行工具（流式进度：先打心跳，再报结果）
                args_brief = func_args_str.strip()
                if len(args_brief) > 80:
                    args_brief = args_brief[:80] + "..."
                print(f"  🔧 调用工具 {func_name}({args_brief})...")
                logger.info(f"Chat Agent调用工具: {func_name}({func_args})")
                tool_result = self._execute_tool(func_name, func_args)

                if self._is_tool_failure(tool_result):
                    err_brief = tool_result[len(TOOL_ERROR_MARK):].splitlines()[0][:100]
                    print(f"  ✗ {func_name} 执行失败：{err_brief}")
                else:
                    round_had_success = True
                    print(f"  ✓ {func_name} 完成（{len(tool_result)}字）")

                # 将工具结果加入历史
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })
                self._persist_session()

            # 失败轮不占轮次上限（AI下轮可重试）；有任一成功则计一轮并清零失败计数
            if round_had_success:
                consecutive_failures = 0
                round_num += 1
            else:
                consecutive_failures += 1
                logger.warning(
                    f"Chat Agent: 工具轮全部失败({consecutive_failures}/{self.max_failed_rounds})，"
                    f"不占轮次上限({round_num}/{hard_cap})"
                )

        # 循环结束：追加提示消息，要求直接回答（此调用不带tools，防止继续循环）
        if context_stopped:
            exit_note = f"上下文接近模型窗口上限（{context_detail}）"
            nudge = (
                "（系统提示：本次对话的上下文已接近模型窗口上限，"
                "请立即基于以上已获取的信息给出完整回答，不要再调用工具；"
                "并如实说明哪些数据因上下文限制被省略或需要另行查询。）"
            )
        elif round_num >= hard_cap:
            exit_note = "工具调用轮次已达上限"
            nudge = (
                "（系统提示：工具调用轮次已达上限，"
                "请基于以上已获取的信息直接给出完整回答，不要再调用工具。）"
            )
        else:
            exit_note = f"工具已连续失败 {consecutive_failures} 轮"
            nudge = (
                "（系统提示：工具已连续失败多次，请基于已有信息给出回答，"
                "并如实说明哪些数据未能获取。）"
            )
        print(f"  ⚠ {exit_note}，请 AI 直接作答...")
        logger.warning(f"Chat Agent: {exit_note}，要求模型直接回答")
        self._messages.append({"role": "user", "content": nudge})
        self._persist_session()
        final_params = {
            "model": self._model,
            "messages": self._messages,
        }
        if self._should_pass_temperature(self._model):
            final_params["temperature"] = 0.3
        final_params["timeout"] = 180

        # 最终兜底调用：流式优先，失败回退非流式
        streamed_final = False
        final_message = None
        final_fr = None
        if self.stream:
            try:
                final_message, final_fr = self._call_api_stream(final_params)
                streamed_final = True
            except Exception as e:
                logger.warning(f"Chat Agent: 最终调用流式失败回退非流式: {e}")
                print("  ⚠ 流式中断，回退完整响应...")
        if final_message is None:
            final_response = self._call_api(final_params)
            final_message = final_response.choices[0].message
            final_fr = final_response.choices[0].finish_reason
        base, suffix = self._reply_parts(final_message, final_fr)
        final_content = base + suffix
        self._last_reply_printed = bool(
            streamed_final and (final_message.content or "").strip())
        if self._last_reply_printed and suffix.strip():
            print(suffix.strip())
        self._messages.append({"role": "assistant", "content": final_content})
        self._persist_session()
        return final_content

    @staticmethod
    def _is_tool_failure(text: str) -> bool:
        """判断工具结果是否为失败（TOOL_ERROR_MARK 开头）。

        失败的工具调用不占工具轮次上限（失败信息已回传AI，AI会自行重试）；
        连续全失败轮次由 max_failed_rounds 单独兜底。
        """
        return bool(text) and text.startswith(TOOL_ERROR_MARK)

    def _execute_tool(self, func_name: str, func_args: dict) -> str:
        """执行单个工具函数

        Args:
            func_name: 工具名称
            func_args: 工具参数

        Returns:
            工具执行结果（纯文本；失败以 TOOL_ERROR_MARK 开头，供 _is_tool_failure 识别）
        """
        func = TOOL_REGISTRY.get(func_name)
        if not func:
            return f"{TOOL_ERROR_MARK}未知工具: {func_name}"

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
            return f"{TOOL_ERROR_MARK}工具参数错误: {e}"
        except Exception as e:
            logger.error(f"工具执行异常 {func_name}: {e}")
            return f"{TOOL_ERROR_MARK}工具执行失败: {e}"

    def reset_history(self) -> Optional[str]:
        """重置对话历史（保留system消息）。

        磁盘上的旧会话先归档留档（重命名，不删除），返回归档文件名供 REPL 提示。
        """
        store = getattr(self, "_session_store", None)
        archived_name = None
        if store is not None:
            try:
                path = store.archive_current()
                if path is not None:
                    archived_name = path.name
                    logger.info(f"chat: 重置前旧会话归档 {archived_name}")
            except OSError as e:
                logger.warning(f"chat会话归档失败（不影响重置）: {e}")
        self._messages = [self._messages[0]]
        self._session_created = None
        self._owns_current_file = False
        self._persist_session()
        return archived_name

    # ── 会话持久化与中断恢复（ISS-092）─────────────────────

    def _persist_session(self):
        """会话状态原子落盘 current.json。

        - 无 store（session_persist=false / 测试用 object.__new__ 构造）时静默跳过。
        - 首次保存前盘上若有非本会话文件，先归档再写（防并发双开 chat 或
          未走恢复路径时静默覆盖旧会话）。
        - 落盘失败只告警不打断对话（对话本身不受影响，仅失去中断恢复能力）。
        """
        store = getattr(self, "_session_store", None)
        if store is None:
            return
        try:
            if not self._owns_current_file and store.current_exists():
                archived = store.archive_current()
                if archived is not None:
                    logger.info(f"chat: 磁盘旧会话归档 {archived.name}（防覆盖）")
            meta = store.save(self._messages, model=self._model,
                              created_at=self._session_created)
            self._owns_current_file = True
            if self._session_created is None:
                self._session_created = meta["created_at"]
        except OSError as e:
            logger.warning(f"chat会话落盘失败（不影响对话，但中断后无法恢复）: {e}")

    def peek_saved_session(self) -> Optional[dict]:
        """磁盘上是否有可恢复的会话（非system消息≥1）。

        返回 {n_messages, updated_at, model} 或 None（未启用/无文件/仅system）。
        """
        store = getattr(self, "_session_store", None)
        if store is None:
            return None
        meta = store.peek()
        if not meta or meta.get("n_messages", 0) < 1:
            return None
        return meta

    def resume_saved_session(self) -> int:
        """恢复磁盘会话到内存。返回恢复的对话消息数（不含system，0=未恢复）。

        system 提示词换成当前版本（旧会话可能存着过时 prompt）；
        恢复后 current.json 归本会话所有，后续保存直接覆写。
        裁剪后对话为空（尾部连续 tool 消息被弹出/max_history 过小）时不接管
        不落盘，原文件保留——防"恢复"变"静默清空"。
        """
        store = getattr(self, "_session_store", None)
        if store is None:
            return 0
        data = store.load()
        if not data:
            return 0
        messages = data["messages"]
        messages[0] = {"role": "system", "content": CHAT_SYSTEM_PROMPT}
        trimmed = self._trim_messages(messages, self.max_history)
        if len(trimmed) <= 1:
            logger.warning(
                f"chat: 磁盘会话({len(messages)}条)按当前 max_history="
                f"{self.max_history} 裁剪后对话为空，未恢复（原文件保留，"
                f"调大 max_history_messages 后可再试）")
            return 0
        self._messages = trimmed
        self._session_created = data.get("created_at")
        self._owns_current_file = True
        self.last_send_error = None
        self._persist_session()
        return len(self._messages) - 1

    def archive_saved_session(self) -> Optional[str]:
        """归档磁盘当前会话（重命名留档，不删除）。返回归档文件名或 None。"""
        store = getattr(self, "_session_store", None)
        if store is None:
            return None
        try:
            path = store.archive_current()
        except OSError as e:
            logger.warning(f"chat会话归档失败: {e}")
            return None
        self._owns_current_file = False
        return path.name if path else None

    def list_sessions(self) -> list:
        """列出全部会话文件（current + 归档），按修改时间倒序。"""
        store = getattr(self, "_session_store", None)
        if store is None:
            return []
        return store.list_sessions()

    def has_unanswered_tail(self) -> bool:
        """会话尾部是否有未获回复的提问（发送失败/中断的标志）。

        尾部是 user 或 tool 消息 = 最后一轮没走完（提问后未获回答）。
        """
        msgs = self._messages
        return len(msgs) > 1 and msgs[-1].get("role") != "assistant"

    def _persist_turn(self, question: str, reply: str):
        """对话落盘：分析报告/chat/YYYY-MM-DD.md（每天一个文件，追加）。失败不影响主流程。"""
        from datetime import datetime
        from pathlib import Path
        try:
            report_dir = Path(CHAT_REPORT_DIR)
            report_dir.mkdir(parents=True, exist_ok=True)
            path = report_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
            ts = datetime.now().strftime("%H:%M")
            entry = f"\n## {ts} 提问\n\n{question}\n\n### 回答\n\n{reply}\n\n---\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)
        except OSError as e:
            logger.warning(f"chat落盘失败（不影响主流程）: {e}")


def _offer_session_resume(agent: "ChatAgent"):
    """启动期中断恢复提示（ISS-092）：磁盘上有未归档会话 → 问是否恢复。

    拒绝恢复则归档留档（session_时间戳.json，不删除）；EOF/Ctrl+C 当作拒绝
    （管道模式下 input 必然 EOF，不能因此崩掉 chat 启动）。
    """
    meta = agent.peek_saved_session()
    if not meta:
        return
    try:
        ans = input(
            f"  检测到上次会话（{meta['n_messages']}条消息，"
            f"{meta.get('updated_at', '')} 更新）。恢复上次对话？(y/N): "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        # 中止（管道 EOF / Ctrl+C）：不恢复也不归档，文件留在盘上下次再说
        print()
        return
    if ans in ("y", "yes"):
        n = agent.resume_saved_session()
        if n > 0:
            print(f"  ✅ 已恢复 {n} 条对话历史")
            if agent.has_unanswered_tail():
                print("  ⚠ 上次最后一条提问未获回复（可能中断于发送失败），请重新提问即可")
        else:
            print("  [!] 会话未能恢复（原因见上方告警；原文件未删除），已开新会话")
    else:
        archived = agent.archive_saved_session()
        if archived:
            print(f"  会话已归档: {archived}（在 ~/.muyun/chat_sessions/ 内可找回）")


def run_chat_repl(config: dict):
    """Chat REPL主循环

    Args:
        config: settings.yaml完整配置

    退出时（q/EOF/Ctrl+C/异常）finally释放chat资源：RAG模型/引擎/连接，
    避免几百MB的torch模型驻留整个start.py会话。
    """
    agent = None
    try:
        agent = ChatAgent(config)

        print()
        print("=" * 50)
        print("  暮云思辨投资助手 - Chat Agent Mode")
        print("  输入自然语言与AI对话，输入 q 退出")
        print(f"  工具调用轮次上限：{agent.hard_tool_round_limit} 轮"
              f"（settings.yaml chat.max_tool_rounds={agent.max_tool_rounds} ×2）")
        if agent.context_guard_enabled:
            print(f"  上下文护栏：{agent._provider} 窗口 {agent.context_window:,} tokens，"
                  f"估算超 {agent.context_budget_ratio:.0%} 自动折叠早期工具结果")
        else:
            print("  上下文护栏：已关闭（settings.yaml chat.context_guard.enabled=false）")
        print("=" * 50)
        print()

        if not agent._client:
            print("  [!] AI未配置，请在 configs/settings.yaml 中配置 AI API Key")
            print("  [!] 仍然可以尝试对话（部分功能可能不可用）")
            print()

        # 中断恢复（ISS-092）：有未归档会话先问是否恢复
        _offer_session_resume(agent)

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
                print("    - 列会话文件: 'sessions'（查看已保存/归档的对话，可找回）")
                print("    - 重置对话: 'reset'")
                print("    - 退出: 'q'")
                print()
                continue

            if user_input.lower() == "reset":
                archived = agent.reset_history()
                print("  对话历史已重置")
                if archived:
                    print(f"  旧会话已归档: {archived}（在 ~/.muyun/chat_sessions/ 内可找回）")
                continue

            if user_input.lower() == "sessions":
                sessions = agent.list_sessions()
                if not sessions:
                    if getattr(agent, "session_persist", True):
                        print("  暂无会话文件（对话会自动保存到 ~/.muyun/chat_sessions/）")
                    else:
                        print("  会话持久化已关闭（settings.yaml chat.session_persist），对话不落盘")
                else:
                    print()
                    print("  会话文件（~/.muyun/chat_sessions/，按时间倒序）：")
                    for s in sessions:
                        n = s.get("n_messages")
                        n_text = (f"{n}条消息"
                                  if isinstance(n, int) and n >= 0 else "（无法读取）")
                        mark = "  ← 当前会话" if s.get("is_current") else ""
                        print(f"    {s['name']:<42} {n_text:<8} "
                              f"{s.get('updated_at', '')}{mark}")
                    print("    （找回归档：先把现有 current.json 改名，再把归档文件改名为 current.json，重启 chat 即恢复）")
                    print()
                continue

            # 调用Chat Agent
            try:
                reply = agent.chat(user_input)
                if getattr(agent, "last_send_error", None) is not None:
                    # 发送失败（额度墙/网络）：提问已落盘，恢复会话即可续上
                    print(f"\n  ⚠ {reply}")
                    print("  会话已保存——q 退出后，下次启动 chat 选恢复即可继续\n")
                else:
                    # 流式输出时正文已实时打印，不重复；只打空行分隔
                    if getattr(agent, "_last_reply_printed", False):
                        print()
                    else:
                        print()
                        print(reply)
                        print()
                    if agent.persist:
                        agent._persist_turn(user_input, reply)
            except Exception as e:
                print(f"\n  [错误] {e}\n")
    finally:
        # 释放chat资源：RAG模型/引擎/AI连接/baostock，退出后内存还给主程序
        if agent is not None:
            try:
                _client = getattr(agent, "_client", None)
                if _client is not None and hasattr(_client, "close"):
                    _client.close()
            except Exception:
                pass
        shutdown_engines()
