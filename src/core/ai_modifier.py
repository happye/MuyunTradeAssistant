"""AI调节层（AI Modifier Layer） - v0.8.0

在 Decision Layer → Strategy Layer 之间插入，用AI分析新闻/情绪，调节信号和仓位。

架构位置：
  Signal Layer → Decision Layer → AI Modifier(新) → Strategy Layer → Execution Layer

AI只负责：
  - 信息理解（新闻摘要、事件提取）
  - 情绪判断（市场情绪倾向和置信度）
  - 事件解析（政策/战争/财报/宏观/黑天鹅）

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

logger = logging.getLogger(__name__)

# AI分析的系统提示词
SYSTEM_PROMPT = """你是一个专业的A股市场分析师。你的任务是根据提供的新闻数据，分析市场情绪和事件影响。

你必须严格按照以下JSON格式输出分析结果，不要输出任何其他内容：

{
  "sentiment": "bullish/bearish/neutral",
  "confidence": 0.0-1.0,
  "risk_level": "low/medium/high",
  "narrative_shift": true/false,
  "event_type": "policy/war/earnings/macro/black_swan/none",
  "summary": "一句话摘要",
  "key_events": ["事件1", "事件2"]
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
6. summary: 用一句中文概括核心影响
7. key_events: 列出最关键的1-3个事件

注意：宁可低估影响也不要夸大。只有真正重大的事件才标记为high risk或black_swan。"""


class AIModifier:
    """AI调节层 - 分析新闻并调节交易信号

    使用DeepSeek/Kimi API分析新闻，输出AIModifierResult。
    """

    def __init__(self, config: dict):
        """初始化AI调节层

        Args:
            config: settings.yaml中的ai配置节
        """
        self.config = config
        self.enabled = config.get("enabled", True)
        self.provider = config.get("provider", "deepseek")

        # 调节参数
        modifier_cfg = config.get("modifier", {})
        self.sentiment_weight = modifier_cfg.get("sentiment_weight", 0.3)
        self.risk_position_cap = modifier_cfg.get("risk_position_cap", 0.7)
        self.max_news = modifier_cfg.get("max_news_per_stock", 10)
        self.cache_ttl = modifier_cfg.get("cache_ttl", 3600)

        # 配置新闻客户端缓存
        NewsClient.configure(cache_ttl=self.cache_ttl)

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

            # Step 3: 构建用户提示
            stock_name = stock_data.stock_name or stock_data.stock_code
            user_prompt = (
                f"请分析以下关于 {stock_name}（{stock_data.stock_code}）的新闻数据：\n\n"
                f"{news_text}\n\n"
                f"当前股价: {stock_data.price}"
            )

            if stock_data.change_pct is not None:
                user_prompt += f"，涨跌幅: {stock_data.change_pct}%"

            # Step 4: 调用AI API
            model = self._model_pro if use_pro else self._model
            logger.info(f"AI Modifier: 调用 {model} 分析 {stock_data.stock_code}...")

            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,  # 低温度=更确定性的输出
                max_tokens=500,
                timeout=30,
            )

            # Step 5: 解析响应
            content = response.choices[0].message.content.strip()
            ai_result = self._parse_response(content)

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
        # high risk → 仓位上限打折
        if result.risk_level == "high":
            result.position_cap = self.risk_position_cap
            adjusted = True
        elif result.risk_level == "medium":
            result.position_cap = 1.0 - (1.0 - self.risk_position_cap) * 0.5  # 中间值
            adjusted = True

        # Layer 3: 状态干预
        # black_swan → 强制PANIC
        if result.event_type == "black_swan":
            result.force_state = MarketState.PANIC.value
            result.position_cap = 0.3  # 极端情况：仓位上限30%
            result.score_adjustment = -0.5  # 强力压制买入
            adjusted = True

        # 叙事转变 → 加强调节力度
        if result.narrative_shift and result.confidence > 0.5:
            result.score_adjustment *= 1.5
            result.position_cap *= 0.85
            adjusted = True

        result.adjusted = adjusted

    def _parse_response(self, content: str) -> Optional[AIModifierResult]:
        """解析AI API的JSON响应

        Args:
            content: AI返回的文本内容

        Returns:
            AIModifierResult 或 None
        """
        # 尝试提取JSON（可能被markdown代码块包裹）
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()

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
                narrative_shift=bool(data.get("narrative_shift", False)),
                event_type=event_type,
                summary=str(data.get("summary", "")),
                key_events=data.get("key_events", []),
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"AI响应解析失败: {e}, content={content[:200]}")
            return None

    @staticmethod
    def _disabled_result() -> AIModifierResult:
        """返回禁用状态的默认结果（不调节任何信号）"""
        return AIModifierResult(
            sentiment="neutral",
            confidence=0.0,
            risk_level="low",
            adjusted=False,
            summary="AI调节未启用",
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
