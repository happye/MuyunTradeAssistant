"""AI调节层（AI Modifier Layer） - v0.8.1

在 Decision Layer → Strategy Layer 之间插入，用AI分析新闻/情绪，调节信号和仓位。

架构位置：
  Signal Layer → Decision Layer → AI Modifier(新) → Strategy Layer → Execution Layer

AI只负责：
  - 信息理解（新闻摘要、事件提取）
  - 情绪判断（市场情绪倾向和置信度）
  - 事件解析（政策/战争/财报/宏观/黑天鹅）

v0.8.1 新增：
  - RAG策略知识增强：AI分析时注入相关策略知识上下文
  - 通过 rag_service 参数注入，不影响现有调用方式

AI不负责：
  - 决策输出（BUY/SELL/HOLD由Decision Layer决定）
  - 交易执行（由Execution Layer决定）

三层调节机制：
  1. 信号调节: buy_score *= (1 - confidence × sentiment_weight)
  2. 仓位调节: 高风险→max_position *= risk_position_cap
  3. 状态干预: event_type == "black_swan" → force PANIC
"""

import os
import json
import logging
from typing import Optional

from openai import OpenAI

from src.data.models import AIModifierResult, StockData, MarketState
from src.data.news_client import NewsClient
from src.core.ai_model import NO_TEMPERATURE_MODELS, thinking_disabled_body
from src.core.tech_context import TechContextBuilder

logger = logging.getLogger(__name__)

# AI分析的系统提示词（v0.8.3 增强：技术面背景感知）
SYSTEM_PROMPT = """你是一个专业的A股市场分析师。你的任务是根据提供的新闻数据，结合当前技术面背景，分析市场情绪和事件影响。

重要：你必须结合技术面背景来判断新闻的真实影响程度：
- 如果技术面与新闻方向一致（如上升趋势+利好新闻），影响放大
- 如果技术面与新闻方向相反（如上升趋势+利空新闻），影响削弱，需在理由中说明
- 如果新闻与技术面方向矛盾且技术面趋势明确，以技术面为准
- 新闻情绪分析不能脱离技术面背景——同样的新闻在不同技术面下影响可能截然不同

你必须严格按照以下JSON格式输出分析结果，不要输出任何其他内容：

{
  "sentiment": "bullish/bearish/neutral",
  "confidence": 0.0-1.0,
  "risk_level": "low/medium/high",
  "narrative_shift": true/false,
  "event_type": "policy/war/earnings/macro/black_swan/none",
  "summary": "一句话摘要（必须提及新闻与技术面的关系）",
  "key_events": ["事件1", "事件2"],
  "tech_context_awareness": true/false,
  "tech_context_used": ["趋势方向", "成交量", "大盘环境"]
}

分析要点：
1. sentiment: 综合判断新闻对个股/市场的情绪影响（bullish=利好, bearish=利空, neutral=中性）
2. confidence: 你对情绪判断的确信程度（0=完全不确定, 1=非常确定）
3. risk_level: 当前新闻暗示的风险等级（low=低风险, medium=需警惕, high=高风险）
4. narrative_shift: 是否有改变市场叙事逻辑的重大事件（如政策转向、行业巨变）
5. event_type: 最重大的事件类型
   - policy: 政策法规变化（降息/加息/行业监管/产业政策）
   - war: 地缘冲突/战争
   - earnings: 财报相关（业绩超预期/暴雷/分红）
   - macro: 宏观经济数据（GDP/CPI/PMI/进出口）
   - black_swan: 黑天鹅事件（极端罕见，只用于真正的系统性风险）
   - none: 无重大事件
6. summary: 用一句中文概括核心影响，必须明确说明你是如何结合技术面背景的
7. key_events: 列出最关键的1-3个事件
8. tech_context_awareness: 你是否在分析中使用了技术面背景信息（true/false）
9. tech_context_used: 你使用了哪些技术面要素（从以下选: 趋势方向, 均线排列, 价格位置, 成交量, 动量指标, 大盘环境）
   如果未使用任何技术面要素，填 []

注意：宁可低估影响也不要夸大。只有真正重大的事件才标记为high risk或black_swan。"""


class AIModifier:
    """AI调节层 - 分析新闻并调节交易信号

    使用DeepSeek/Kimi API分析新闻，输出AIModifierResult。
    """

    def __init__(self, config: dict, rag_service=None):
        """初始化AI调节层

        Args:
            config: settings.yaml中的ai配置节
            rag_service: RAG服务实例（v0.8.1可选，策略知识增强）
        """
        self.config = config
        self.enabled = config.get("enabled", True)
        self.provider = config.get("provider", "deepseek")
        self.debug = config.get("debug", False)
        self._rag_service = rag_service  # v0.8.1: RAG策略知识增强

        # 调节参数
        modifier_cfg = config.get("modifier", {})
        self.sentiment_weight = modifier_cfg.get("sentiment_weight", 0.3)
        self.risk_position_cap = modifier_cfg.get("risk_position_cap", 0.7)
        self.max_news = modifier_cfg.get("max_news_per_stock", 10)
        self.cache_ttl = modifier_cfg.get("cache_ttl", 3600)
        self.tech_context_enabled = modifier_cfg.get("tech_context_enabled", True)

        # 配置新闻客户端缓存
        NewsClient.configure(cache_ttl=self.cache_ttl, debug=self.debug)

        # 初始化OpenAI客户端
        self._client: Optional[OpenAI] = None
        self._model: str = ""
        self._model_pro: str = ""
        self._api_key: str = ""

        if self.enabled:
            self._init_client()

    def _init_client(self):
        """根据provider初始化OpenAI兼容客户端"""
        provider_cfg = self.config.get(self.provider, {})

        # API Key: 配置文件 > 环境变量
        api_key = provider_cfg.get("api_key", "")
        if not api_key:
            env_key = f"{self.provider.upper()}_API_KEY"
            api_key = os.environ.get(env_key, "")

        if not api_key:
            logger.warning(
                f"AI Modifier: {self.provider} API Key未配置。"
                f"请在 configs/settings.yaml 的 ai.{self.provider}.api_key 中填写，"
                f"或设置环境变量 {self.provider.upper()}_API_KEY"
            )
            self.enabled = False
            return

        self._api_key = api_key
        base_url = provider_cfg.get("base_url", "")
        self._model = provider_cfg.get("model", "")
        self._model_pro = provider_cfg.get("model_pro", self._model)

        try:
            self._client = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )
            logger.info(
                f"AI Modifier initialized: provider={self.provider}, "
                f"model={self._model}, base_url={base_url}"
            )
        except Exception as e:
            logger.error(f"AI Modifier初始化失败: {e}")
            self.enabled = False

    @staticmethod
    def _should_pass_temperature(model: str) -> bool:
        """判断模型是否支持 temperature 参数。

        不支持清单统一来自 src.core.ai_model.NO_TEMPERATURE_MODELS
        （与 ChatAgent._should_pass_temperature 共用同一份，防模型换代只改一处）。
        参考: https://platform.kimi.com/docs/api/chat

        Args:
            model: 模型名称

        Returns:
            True 表示可以传 temperature 参数
        """
        return not model.startswith(NO_TEMPERATURE_MODELS)

    def analyze(
        self,
        stock_data: StockData,
        use_pro: bool = False,
    ) -> AIModifierResult:
        """执行AI分析流程

        1. 抓取新闻（个股+宏观）
        2. 格式化为AI可读文本
        3. 调用AI API分析
        4. 解析结果并应用调节

        Args:
            stock_data: 股票数据
            use_pro: 是否使用pro模型（深度分析）

        Returns:
            AIModifierResult: AI分析结果（含调节参数）
        """
        if not self.enabled or not self._client:
            return self._disabled_result()

        try:
            # Step 1: 收集新闻
            news_data = NewsClient.gather_news_for_analysis(
                stock_data.stock_code, self.max_news
            )

            stock_news = news_data.get("stock_news", [])
            macro_news = news_data.get("macro_news", [])

            if not stock_news and not macro_news:
                logger.info("AI Modifier: 无新闻数据，跳过AI分析")
                return self._disabled_result()

            # Step 2: 格式化新闻
            news_text = NewsClient.format_news_for_ai(news_data)

            # 提前获取stock_name（RAG和prompt都需要）
            stock_name = stock_data.stock_name or stock_data.stock_code

            # Step 2.5: RAG策略知识增强（v0.8.1）
            rag_context = ""
            if self._rag_service and self._rag_service.is_available():
                try:
                    # 智能搜索query：股票名 + 事件关键词
                    search_query = self._build_rag_query(stock_name, news_text)
                    rag_context = self._rag_service.get_context(
                        search_query, target="modifier",
                        stock_name=stock_name,
                        top_k=3, max_length=1500,
                    )
                    if rag_context:
                        logger.info(f"AI Modifier: RAG检索到策略知识上下文({len(rag_context)}字)")
                except Exception as e:
                    logger.debug(f"AI Modifier: RAG检索异常(不影响主流程): {e}")

            # Step 3: 构建用户提示
            # v0.8.3 Phase B: 注入技术面上下文
            tech_context_text = ""
            tech_context_data = None
            if self.tech_context_enabled:
                tech_context_data = TechContextBuilder().build(stock_data)
                tech_context_text = TechContextBuilder.to_text(tech_context_data)

            user_prompt = (
                f"请分析以下关于 {stock_name}（{stock_data.stock_code}）的新闻数据：\n\n"
                f"{news_text}\n\n"
                f"## 当前技术面背景\n{tech_context_text}\n\n"
                f"当前股价: {stock_data.price}"
            )

            if stock_data.change_pct is not None:
                user_prompt += f"，涨跌幅: {stock_data.change_pct}%"

            # 注入RAG策略知识上下文（v0.8.1）
            if rag_context:
                user_prompt += f"\n\n--- 策略知识参考 ---\n{rag_context}"

            # Debug: 打印完整输入
            if self.debug:
                print("\n" + "=" * 60)
                print(f"[AI DEBUG] Provider: {self.provider}, Model: {self._model}")
                print(f"[AI DEBUG] Tech Context: "
                      f"trend={tech_context_data['trend']['direction'] if tech_context_data else 'N/A'}, "
                      f"MA={tech_context_data['trend']['ma_arrangement'] if tech_context_data else 'N/A'}, "
                      f"vol={tech_context_data['volume']['vol_trend'] if tech_context_data else 'N/A'}")
                print(f"[AI DEBUG] System Prompt ({len(SYSTEM_PROMPT)}字):")
                print(SYSTEM_PROMPT[:500] + ("..." if len(SYSTEM_PROMPT) > 500 else ""))
                print(f"\n[AI DEBUG] User Prompt ({len(user_prompt)}字):")
                print(user_prompt[:1000] + ("..." if len(user_prompt) > 1000 else ""))
                print("=" * 60)

            # Step 4: 调用AI API
            model = self._model_pro if use_pro else self._model
            logger.info(f"AI Modifier: 调用 {model} 分析 {stock_data.stock_code}...")

            # 构建API调用参数（不同模型/提供商支持的参数不同）
            api_params = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            }

            # temperature: kimi-k2.6/kimi-k2.5不支持此参数
            # max_completion_tokens: Kimi官方推荐替代已弃用的max_tokens
            if self._should_pass_temperature(model):
                api_params["temperature"] = 0.1  # 低温度=更确定性输出
            api_params["max_completion_tokens"] = 800
            api_params["timeout"] = 30
            # DeepSeek 思考型模型默认思考模式，保持非思考需 thinking.type=disabled；kimi 等返回空 dict
            api_params.update(thinking_disabled_body(model))

            if self.debug:
                logger.debug(f"AI Modifier DEBUG: API参数={api_params}")

            response = self._client.chat.completions.create(**api_params)

            # v0.8.7.6 审计修复 B17：截断检测——max_completion_tokens=800 偏紧，
            # 输出被截断时 _parse_response 缺字段全取默认（conf 0/neutral/low）→
            # 三分支都不满足 → adjusted=False 静默"不调节"。检测到 length 先放大重试一次。
            if getattr(response.choices[0], "finish_reason", None) == "length":
                logger.warning("AI Modifier: 响应被截断(finish_reason=length)，加倍 max_completion_tokens 重试一次")
                api_params["max_completion_tokens"] = 1600
                response = self._client.chat.completions.create(**api_params)
                if getattr(response.choices[0], "finish_reason", None) == "length":
                    logger.warning("AI Modifier: 重试仍截断，结果按解析降级处理（可能静默不调节）")

            # Step 5: 解析响应
            message = response.choices[0].message
            content = (message.content or "").strip()
            # 思考型模型（v4-flash/v4-pro/kimi-k2-thinking）可能在reasoning_content中产出分析
            reasoning_content = ""
            if hasattr(message, 'reasoning_content') and message.reasoning_content:
                reasoning_content = message.reasoning_content

            # Debug: 打印完整输出
            if self.debug:
                print("\n" + "=" * 60)
                print(f"[AI DEBUG] Response content ({len(content)}字):")
                print(content[:2000] + ("..." if len(content) > 2000 else ""))
                if reasoning_content:
                    print(f"\n[AI DEBUG] Reasoning content ({len(reasoning_content)}字):")
                    print(reasoning_content[:1000] + ("..." if len(reasoning_content) > 1000 else ""))
                print("=" * 60)

            ai_result = self._parse_response(content, reasoning_content)

            if ai_result is None:
                logger.warning("AI Modifier: 响应解析失败，跳过AI调节")
                return self._disabled_result()

            # 补充元数据
            ai_result.source_count = len(stock_news) + len(macro_news)

            # Step 6: 应用三层调节
            self._apply_modification(ai_result, stock_data)

            logger.info(
                f"AI Modifier: sentiment={ai_result.sentiment}, "
                f"confidence={ai_result.confidence:.2f}, "
                f"risk={ai_result.risk_level}, "
                f"event={ai_result.event_type}, "
                f"adjusted={ai_result.adjusted}"
            )

            return ai_result

        except Exception as e:
            logger.warning(f"AI Modifier分析异常: {e}")
            return self._disabled_result()

    def _apply_modification(
        self, result: AIModifierResult, stock_data: StockData
    ):
        """应用三层调节机制

        Layer 1 - 信号调节: bearish时压制buy_score
        Layer 2 - 仓位调节: 高风险时降低仓位上限
        Layer 3 - 状态干预: 黑天鹅强制PANIC

        Args:
            result: AI分析结果（会被原地修改）
            stock_data: 股票数据（用于判断当前状态）
        """
        adjusted = False

        # Layer 1: 信号调节
        # bearish → 压制买入信号（score_adjustment < 0）
        # bullish → 轻微增强（不显著，AI只是参考）
        if result.sentiment == "bearish" and result.confidence > 0.3:
            result.score_adjustment = -(result.confidence * self.sentiment_weight)
            adjusted = True
        elif result.sentiment == "bullish" and result.confidence > 0.5:
            result.score_adjustment = result.confidence * self.sentiment_weight * 0.3
            adjusted = True

        # Layer 2: 仓位调节
        # ISS-117 D1（S0）：AI 自报风险标签无硬权限——限仓幅度按自报置信度有界缩放
        # （confidence=0 → 不限制；confidence=1 → 全额折扣），不再无条件打折
        if result.risk_level == "high":
            result.position_cap = 1.0 - (1.0 - self.risk_position_cap) * min(result.confidence, 1.0)
            adjusted = True
        elif result.risk_level == "medium":
            result.position_cap = 1.0 - (1.0 - self.risk_position_cap) * 0.5 * min(result.confidence, 1.0)
            adjusted = True

        # Layer 3: 状态干预
        # ISS-117 D1（S0）：AI black_swan 标签不再 force PANIC（未核实标签只有研究提醒
        # 资格）——压分幅度按自报置信度有界缩放，confidence=0 时无任何调节
        if result.event_type == "black_swan":
            result.score_adjustment = -0.5 * min(result.confidence, 1.0)
            adjusted = True

        # 叙事转变 → 加强调节力度
        if result.narrative_shift and result.confidence > 0.5:
            result.score_adjustment *= 1.5
            result.position_cap *= 0.85
            adjusted = True

        result.adjusted = adjusted

    def _parse_response(self, content: str, reasoning_content: str = "") -> Optional[AIModifierResult]:
        """解析AI API的JSON响应

        Args:
            content: AI返回的文本内容
            reasoning_content: 思考型模型的推理过程（v4-flash/v4-pro等）

        Returns:
            AIModifierResult 或 None
        """
        # 空内容处理
        if not content or not content.strip():
            if reasoning_content and reasoning_content.strip():
                logger.warning(
                    f"AI返回空content但有reasoning_content(len={len(reasoning_content)})，"
                    f"尝试从推理内容提取JSON"
                )
                # 尝试从reasoning_content末尾提取JSON
                content = self._extract_json_from_reasoning(reasoning_content)
                if not content:
                    logger.warning("AI响应content为空，且reasoning_content中未找到JSON")
                    return None
            else:
                logger.warning("AI响应content为空，跳过解析")
                return None

        # 尝试提取JSON（可能被markdown代码块包裹）
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()

        # 修复截断的JSON（token耗尽时AI可能返回不完整JSON）
        json_str = self._repair_truncated_json(json_str)

        try:
            data = json.loads(json_str)

            # 验证必要字段
            sentiment = data.get("sentiment", "neutral")
            if sentiment not in ("bullish", "bearish", "neutral"):
                sentiment = "neutral"

            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))

            risk_level = data.get("risk_level", "low")
            if risk_level not in ("low", "medium", "high"):
                risk_level = "low"

            event_type = data.get("event_type", "none")
            if event_type not in ("policy", "war", "earnings", "macro", "black_swan", "none"):
                event_type = "none"

            return AIModifierResult(
                sentiment=sentiment,
                confidence=confidence,
                risk_level=risk_level,
                # ISS-117 D1（S0）：叙事布尔量严格解析——bool("false") 曾被当 True
            narrative_shift=(data.get("narrative_shift", False) is True
                             or (isinstance(data.get("narrative_shift"), str)
                                 and data.get("narrative_shift").lower() == "true")),
                event_type=event_type,
                summary=str(data.get("summary", "")),
                key_events=data.get("key_events", []),
                # v0.8.3 Phase B: 技术面感知
                tech_context_awareness=bool(data.get("tech_context_awareness", False)),
                tech_context_used=data.get("tech_context_used", []),
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"AI响应解析失败: {e}, content={content[:200]}")
            return None

    @staticmethod
    def _repair_truncated_json(s: str) -> str:
        """尝试修复被截断的JSON字符串。

        常见截断场景：key_events数组中最后一个字符串未关闭，整个JSON未收尾。
        策略：用括号/引号计数法补全缺失的关闭符号。
        """
        import json as _json
        s = s.strip()
        if not s:
            return s
        try:
            _json.loads(s)
            return s
        except _json.JSONDecodeError:
            pass

        # 去掉末尾可能的逗号，再计数未关闭的括号和引号
        repaired = s.rstrip().rstrip(',').rstrip()
        depth_brace = 0
        depth_bracket = 0
        in_string = False
        escape_next = False
        for ch in repaired:
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth_brace += 1
            elif ch == '}':
                depth_brace -= 1
            elif ch == '[':
                depth_bracket += 1
            elif ch == ']':
                depth_bracket -= 1

        suffix = '"' if in_string else ''
        suffix += ']' * max(depth_bracket, 0)
        suffix += '}' * max(depth_brace, 0)
        candidate = repaired + suffix
        try:
            _json.loads(candidate)
            return candidate
        except _json.JSONDecodeError:
            return s

    @staticmethod
    def _extract_json_from_reasoning(reasoning: str) -> str:
        """从思考型模型的reasoning_content中尝试提取JSON

        思考型模型（deepseek-v4-flash/pro）有时把JSON放在推理过程末尾，
        而content字段为空。此方法尝试从推理文本末尾提取JSON。

        Args:
            reasoning: reasoning_content文本

        Returns:
            提取到的JSON字符串，或空字符串
        """
        import re
        # 尝试匹配最外层 { } 对
        matches = re.findall(r'\{[^{}]*"sentiment"[^{}]*\}', reasoning, re.DOTALL)
        if matches:
            return matches[-1]  # 取最后一个匹配

        # 更宽松的匹配：找最后的 { } 块
        last_brace = reasoning.rfind('{')
        if last_brace >= 0:
            candidate = reasoning[last_brace:]
            # 找到匹配的 }
            depth = 0
            for i, ch in enumerate(candidate):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return candidate[:i + 1]

        return ""

    @staticmethod
    def _disabled_result() -> AIModifierResult:
        """返回禁用状态的默认结果（不调节任何信号）"""
        return AIModifierResult(
            sentiment="neutral",
            confidence=0.0,
            risk_level="low",
            adjusted=False,
            summary="AI调节未启用",
            tech_context_awareness=False,
            tech_context_used=[],
        )

    def switch_provider(self, provider: str):
        """切换AI提供商

        Args:
            provider: "deepseek" 或 "kimi"
        """
        if provider not in ("deepseek", "kimi"):
            logger.warning(f"不支持的AI提供商: {provider}")
            return

        self.provider = provider
        self._init_client()

    def is_available(self) -> bool:
        """检查AI调节层是否可用（已配置且API Key有效）"""
        return self.enabled and self._client is not None

    # ===== RAG策略知识增强辅助方法（v0.8.1） =====

    # 事件关键词 → RAG搜索词映射
    _EVENT_RAG_KEYWORDS = {
        "政策": "政策变化 行业监管",
        "监管": "行业监管 合规风险",
        "降息": "货币政策 利率影响",
        "加息": "货币政策 利率影响",
        "战争": "地缘冲突 避险策略",
        "冲突": "地缘冲突 市场风险",
        "财报": "财务分析 业绩评估",
        "业绩": "业绩评估 估值分析",
        "暴雷": "风险事件 止损策略",
        "分红": "分红策略 价值投资",
        "暴跌": "暴跌应对 止损",
        "大涨": "追涨策略 动量",
        "重组": "资产重组 事件驱动",
        "退市": "退市风险 止损",
    }

    def _build_rag_query(self, stock_name: str, news_text: str) -> str:
        """构建智能RAG搜索query

        结合股票名、新闻中的事件关键词、以及行业特征，
        生成更有针对性的搜索query，提高检索精度。

        Args:
            stock_name: 股票名称
            news_text: 格式化的新闻文本

        Returns:
            优化后的搜索query
        """
        parts = [stock_name]

        # 从新闻文本中提取事件关键词
        for keyword, rag_query in self._EVENT_RAG_KEYWORDS.items():
            if keyword in news_text:
                parts.append(rag_query)

        # 限制搜索词长度（避免query过长导致语义稀释）
        query = " ".join(parts[:4])  # 最多4个片段
        return query[:200]  # 长度上限
