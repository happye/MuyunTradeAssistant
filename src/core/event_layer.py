"""事件驱动层（Event Layer） - v0.8.1

在 AI Modifier Layer 之前主动检测重大市场事件，触发预警和策略调整。

架构位置：
  Signal → Decision → Event Layer(新) → AI Modifier → Strategy → Execution

核心能力：
  1. 关键词匹配：秒级检测宏观新闻中的重大事件
  2. 市场规则：检测沪深300暴跌、持仓股跌停等
  3. 持仓扫描：批量扫描持仓股新闻，AI判断影响
  4. AI分类：对关键词命中的事件做AI二次确认

v0.8.1 新增：
  - RAG策略知识增强：事件分类时注入相关策略知识上下文
  - 事件类型→策略知识映射：如"政策变化"自动检索政策应对策略
  - 通过 rag_service 参数注入，不影响现有调用方式

设计原则：
  - 关键词优先，AI辅助（简单事件关键词秒级，复杂事件AI确认）
  - 复用 NewsClient / MarketCache（不新建 event_client.py）
  - 独立 AI 客户端（不复用 AIModifier 实例）
  - 事件效果通过 AIModifierResult 传递（复用三层调节机制）
  - 优雅降级（AI不可用时仅关键词检测）
"""

import os
import json
import time
import logging
from typing import Optional
from datetime import datetime

import yaml
from openai import OpenAI

from src.data.models import MarketEvent, AIModifierResult, MarketState

logger = logging.getLogger(__name__)

# AI事件分类的系统提示词
# v0.8.6.3: 增加笨总现象级事件四要素判定（ISS-041 events）
EVENT_CLASSIFY_PROMPT = """你是一个专业的A股市场事件分析师。根据提供的新闻内容，判断这是否是一个需要投资者关注的重要事件。

你必须严格按照以下JSON格式输出分析结果，不要输出任何其他内容：

{
  "is_significant": true/false,
  "event_type": "policy/war/earnings/macro/black_swan/none",
  "sentiment": "bullish/bearish/neutral",
  "impact_level": 1-5,
  "scope": "market/sector/stock",
  "duration": "short/medium/long",
  "summary": "一句话摘要",
  "four_elements": {
    "authenticity": 0-100,
    "virality": 0-100,
    "scale": 0-100,
    "timeliness": 0-100
  }
}

判断标准：
1. is_significant: 是否值得投资者关注（小幅波动/日常消息→false）
2. event_type:
   - policy: 政策法规变化（降息/加息/行业监管/产业政策）
   - war: 地缘冲突/战争
   - earnings: 财报相关（业绩超预期/暴雷/分红）
   - macro: 宏观经济数据（GDP/CPI/PMI/进出口）
   - black_swan: 黑天鹅事件（极端罕见，只用于系统性风险）
   - none: 无重大事件
3. impact_level: 1=可忽略 2=需关注 3=重要 4=重大 5=极端
4. scope: 影响范围
5. duration: 预期持续时间

笨总现象级事件四要素（教学3）——只对 is_significant=true 的事件判定，否则各项给50：
- authenticity 真实性（最重要）：消息是否经得起推敲？单一来源/小道消息/明显夸张→低分；权威媒体/多源报道/数据支撑→高分。注意：你只能基于这条新闻文本判断，无法上网交叉验证，对真实性要保守，疑似伪信号给低分
- virality 传播性：是否出圈、全民讨论？冷门行业小众消息→低分；社会热点/全民关注→高分
- scale 规模性：行业体量是否足够大？细分小众领域→低分；万亿级主流产业→高分
- timeliness 时效性：是否首次发生？首次突破→高分；重复/后续跟进消息→低分

注意：宁可低估影响也不要夸大。只有真正重大的事件才标记为4-5级。"""


class EventLayer:
    """事件驱动层 - 主动检测市场事件并预警"""

    # v0.8.1: 事件类型 → RAG搜索关键词映射
    # 当检测到某类事件时，自动检索相关策略知识
    EVENT_RAG_QUERIES = {
        "policy": "政策变化应对策略 行业监管影响",
        "war": "地缘冲突 市场避险 黑天鹅应对",
        "earnings": "财报分析 业绩评估 估值调整",
        "macro": "宏观经济 市场周期 大盘环境",
        "black_swan": "黑天鹅事件 极端风险 危机应对 止损",
        "market_crash": "暴跌应对 止损策略 恐慌抛售",
    }

    def __init__(self, config: dict, ai_config: Optional[dict] = None, rag_service=None):
        """初始化事件驱动层

        Args:
            config: settings.yaml中的event配置节
            ai_config: settings.yaml中的ai配置节（用于AI分类）
            rag_service: RAG服务实例（v0.8.1可选，策略知识增强）
        """
        self.config = config
        self.enabled = config.get("enabled", True)
        self.rules_path = config.get("rules_path", "./src/scanner/event_rules.yaml")
        self.auto_scan = config.get("auto_scan_on_analyze", True)
        self.keyword_scan_enabled = config.get("keyword_scan", True)
        self.ai_classify_enabled = config.get("ai_classify", True)
        self.portfolio_scan_enabled = config.get("portfolio_scan", True)
        self.max_display = config.get("max_events_display", 10)
        self._rag_service = rag_service  # v0.8.1: RAG策略知识增强

        # 加载事件规则
        self.rules: list[dict] = []
        self.global_config: dict = {}
        self._load_rules()

        # 事件缓存
        self._event_cache: list[MarketEvent] = []
        self._cache_timestamp: float = 0.0
        self._cache_ttl: int = self.global_config.get("event_cache_ttl", 300)

        # AI客户端（独立于AIModifier）
        self._ai_client: Optional[OpenAI] = None
        self._ai_model: str = ""
        self._ai_available: bool = False
        if self.ai_classify_enabled and ai_config and ai_config.get("enabled", False):
            self._init_ai_client(ai_config)

        logger.info(
            f"EventLayer initialized: enabled={self.enabled}, "
            f"rules={len(self.rules)}, ai={self._ai_available}"
        )

    def _load_rules(self):
        """加载事件触发规则"""
        try:
            with open(self.rules_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            self.rules = data.get("rules", [])
            self.global_config = data.get("global", {})
            logger.info(f"事件规则加载成功: {len(self.rules)}条规则")
        except FileNotFoundError:
            logger.warning(f"事件规则文件不存在: {self.rules_path}")
        except Exception as e:
            logger.warning(f"事件规则加载失败: {e}")

    def _init_ai_client(self, ai_config: dict):
        """独立初始化AI客户端（用于事件分类）"""
        provider = ai_config.get("provider", "deepseek")
        provider_cfg = ai_config.get(provider, {})

        api_key = provider_cfg.get("api_key", "")
        if not api_key:
            env_key = f"{provider.upper()}_API_KEY"
            api_key = os.environ.get(env_key, "")

        if not api_key:
            logger.warning("EventLayer: AI API Key未配置，AI分类不可用")
            return

        base_url = provider_cfg.get("base_url", "")
        self._ai_model = provider_cfg.get("model", "")

        try:
            self._ai_client = OpenAI(api_key=api_key, base_url=base_url)
            self._ai_available = True
            logger.info(f"EventLayer AI initialized: provider={provider}, model={self._ai_model}")
        except Exception as e:
            logger.warning(f"EventLayer AI初始化失败: {e}")

    # ===== 核心检测方法 =====

    def scan_macro_events(self) -> list[MarketEvent]:
        """扫描宏观事件

        1. 获取宏观快讯（复用 NewsClient）
        2. 关键词匹配检测
        3. 可选AI二次分类

        Returns:
            检测到的宏观事件列表
        """
        if not self.keyword_scan_enabled:
            return []

        events = []

        try:
            from src.data.news_client import NewsClient
            macro_news = NewsClient.get_macro_news(max_count=20)
        except Exception as e:
            logger.warning(f"宏观快讯获取失败: {e}")
            return []

        if not macro_news:
            return []

        # 关键词匹配
        keyword_rules = [r for r in self.rules if r.get("trigger", {}).get("type") == "keyword"]

        for news_item in macro_news:
            title = news_item.get("title", "")
            summary = news_item.get("summary", "")
            text = f"{title} {summary}"

            for rule in keyword_rules:
                matched = self._match_keywords(text, rule)
                if matched:
                    event = self._create_event_from_rule(rule, source=title)
                    if event:
                        # AI二次分类确认
                        if self.ai_classify_enabled and self._ai_available and event.impact_level >= 3:
                            ai_event = self._ai_classify_event(title, summary)
                            if ai_event:
                                # AI确认是重大事件，使用AI的分类结果
                                if ai_event.impact_level >= event.impact_level:
                                    event = ai_event
                                    event.source = title
                                # AI认为不重大，降级
                                elif not ai_event.sentiment or ai_event.impact_level < 2:
                                    continue

                        events.append(event)
                        break  # 一条新闻只匹配一个规则

        return events

    def scan_market_rules(self, market_data: Optional[dict] = None) -> list[MarketEvent]:
        """扫描市场规则事件

        检查沪深300跌幅、持仓股涨跌等规则触发条件。

        Args:
            market_data: 市场数据dict，包含 index_change_pct 等

        Returns:
            检测到的市场事件列表
        """
        events = []

        # 市场指数规则
        index_rules = [r for r in self.rules if r.get("trigger", {}).get("type") == "market_index"]

        if market_data and index_rules:
            for rule in index_rules:
                conditions = rule.get("trigger", {}).get("conditions", [])
                matched = self._check_conditions(market_data, conditions)
                if matched:
                    event = self._create_event_from_rule(rule, source="沪深300行情")
                    if event:
                        events.append(event)

        return events

    def scan_portfolio_events(self, positions: list) -> list[MarketEvent]:
        """扫描持仓股事件

        对每只持仓股获取新闻，AI判断是否影响持仓。

        Args:
            positions: 持仓列表（PositionInfo对象）

        Returns:
            检测到的持仓股事件列表
        """
        if not self.portfolio_scan_enabled or not positions:
            return []

        events = []

        # 先检查价格规则
        stock_rules = [r for r in self.rules if r.get("trigger", {}).get("type") == "stock_price"]

        # 获取持仓股实时行情（复用MarketCache）
        try:
            from src.scanner.market_cache import MarketCache
            cache = MarketCache()
            market_data = cache.get_all_stocks()
        except Exception as e:
            logger.warning(f"获取市场行情失败: {e}")
            market_data = {}

        for pos in positions:
            code = pos.stock_code

            # 检查价格规则
            stock_info = market_data.get(code)
            if stock_info and stock_rules:
                for rule in stock_rules:
                    conditions = rule.get("trigger", {}).get("conditions", [])
                    matched = self._check_conditions(stock_info, conditions)
                    if matched:
                        event = self._create_event_from_rule(rule, source=f"{pos.stock_name or code}行情")
                        if event:
                            event.affected_codes = [code]
                            events.append(event)

            # 获取个股新闻，AI判断影响
            if self._ai_available:
                try:
                    from src.data.news_client import NewsClient
                    news = NewsClient.get_stock_news(code, max_count=5)
                    if news:
                        # 拼接新闻标题
                        titles = [n.get("title", "") for n in news if n.get("title")]
                        if titles:
                            news_text = "\n".join(f"- {t}" for t in titles[:5])
                            ai_event = self._ai_classify_portfolio_news(
                                pos.stock_name or code, code, news_text
                            )
                            if ai_event and ai_event.impact_level >= 3:
                                ai_event.affected_codes = [code]
                                events.append(ai_event)
                except Exception as e:
                    logger.warning(f"持仓股 {code} 新闻扫描失败: {e}")

        return events

    def detect_all(self, positions: list = None) -> list[MarketEvent]:
        """完整事件扫描

        按优先级：宏观事件 > 市场规则 > 持仓股事件

        Args:
            positions: 持仓列表（可选）

        Returns:
            所有检测到的市场事件，按impact_level降序
        """
        all_events = []

        # 1. 宏观事件
        macro_events = self.scan_macro_events()
        all_events.extend(macro_events)

        # 2. 市场规则事件
        market_data = self._get_market_index_data()
        market_events = self.scan_market_rules(market_data)
        all_events.extend(market_events)

        # 3. 持仓股事件
        if positions:
            portfolio_events = self.scan_portfolio_events(positions)
            all_events.extend(portfolio_events)

        # 去重（同类型同来源的事件只保留impact_level最高的）
        seen = set()
        unique_events = []
        for event in sorted(all_events, key=lambda e: e.impact_level, reverse=True):
            key = (event.event_type, event.source[:50] if event.source else "")
            if key not in seen:
                seen.add(key)
                unique_events.append(event)

        # 更新缓存
        self._event_cache = unique_events[:self.max_display]
        self._cache_timestamp = time.time()

        return unique_events[:self.max_display]

    def check_events(self) -> list[MarketEvent]:
        """快速检查：返回缓存中的活跃事件

        用于 analyze() 集成，几乎无性能开销。
        缓存过期时只做关键词+市场规则的快速扫描（不触发AI）。

        Returns:
            活跃事件列表
        """
        if not self.enabled:
            return []

        # 缓存有效
        if self._event_cache and (time.time() - self._cache_timestamp) < self._cache_ttl:
            return self._event_cache

        # 快速扫描（关键词+市场规则，不扫持仓股）
        events = []

        if self.keyword_scan_enabled:
            events.extend(self.scan_macro_events())

        market_data = self._get_market_index_data()
        events.extend(self.scan_market_rules(market_data))

        # 只保留impact_level >= 2的事件
        events = [e for e in events if e.impact_level >= 2]

        self._event_cache = events
        self._cache_timestamp = time.time()

        return events

    # ===== 事件转换 =====

    def to_ai_modifier_result(self, event: MarketEvent) -> AIModifierResult:
        """将市场事件转换为AI调节结果

        复用现有三层调节机制，无需修改 Strategy/Execution Layer。

        Args:
            event: 市场事件

        Returns:
            AIModifierResult: 可直接传入Orchestrator的调节结果
        """
        # 报告1.2 笨总教学三：真实性一票否决权 -- 真实性<30 的事件不驱动交易信号
        # （返回 neutral 空操作，仅靠 summary 标记提示用户核实）。
        # "信号路径一票否决 + 展示层保留提示"折中：AI 真实性是单条文本猜测、无法交叉验证，
        # 硬丢弃会误杀真事件；剥离信号影响已足够忠实笨总"假消息不该驱动行动"。
        _fe = getattr(event, "four_elements", None) or {}
        if isinstance(_fe, dict) and _fe.get("authenticity", 100) < 30:
            return AIModifierResult(
                sentiment="neutral",
                confidence=0.0,
                risk_level="low",
                event_type=event.event_type,
                summary=f"[真实性存疑-已忽略信号影响] {event.summary or event.source}",
                adjusted=False,
                score_adjustment=0.0,   # 不调整分数
                position_cap=1.0,        # 1.0=不限制（空操作，不驱动任何仓位变化）
            )

        # 基础映射
        result = AIModifierResult(
            sentiment=event.sentiment,
            confidence=min(event.impact_level / 5.0, 1.0),  # impact→confidence
            risk_level="high" if event.impact_level >= 4 else "medium" if event.impact_level >= 3 else "low",
            event_type=event.event_type,
            summary=f"[事件] {event.summary}" if event.summary else f"[事件] {event.source}",
            adjusted=True,
        )

        # 信号调节
        if event.sentiment == "bearish" and event.impact_level >= 3:
            result.score_adjustment = -(event.impact_level / 5.0) * 0.3  # 最多-0.3
        elif event.sentiment == "bullish" and event.impact_level >= 3:
            result.score_adjustment = (event.impact_level / 5.0) * 0.15  # 最多+0.15

        # 仓位调节
        if event.impact_level >= 5:
            result.position_cap = 0.3  # 黑天鹅级别
        elif event.impact_level >= 4:
            result.position_cap = 0.5
        elif event.impact_level >= 3:
            result.position_cap = 0.75

        # 状态干预
        if event.event_type == "black_swan" and event.impact_level >= 5:
            result.force_state = MarketState.PANIC.value

        return result

    # ===== 内部方法 =====

    def _match_keywords(self, text: str, rule: dict) -> bool:
        """检查文本是否匹配关键词规则

        Args:
            text: 新闻文本
            rule: 规则dict，trigger.keywords 含 bullish/bearish 列表

        Returns:
            是否匹配
        """
        keywords_cfg = rule.get("trigger", {}).get("keywords", {})
        min_matches = self.global_config.get("min_keyword_matches", 1)

        all_keywords = []
        for sentiment, kw_list in keywords_cfg.items():
            all_keywords.extend(kw_list)

        match_count = 0
        for kw in all_keywords:
            if kw in text:
                match_count += 1
                if match_count >= min_matches:
                    return True

        return False

    def _check_conditions(self, data: dict, conditions: list[dict]) -> bool:
        """检查数据是否满足条件

        Args:
            data: 数据dict（如市场行情）
            conditions: 条件列表 [{field, op, value}]

        Returns:
            是否所有条件都满足
        """
        for cond in conditions:
            field = cond.get("field", "")
            op = cond.get("op", "")
            value = cond.get("value", 0)

            actual = data.get(field) if isinstance(data, dict) else getattr(data, field, None)
            if actual is None:
                return False

            try:
                actual = float(actual)
                value = float(value)
            except (ValueError, TypeError):
                return False

            if op == ">" and not (actual > value):
                return False
            elif op == ">=" and not (actual >= value):
                return False
            elif op == "<" and not (actual < value):
                return False
            elif op == "<=" and not (actual <= value):
                return False
            elif op == "==" and not (actual == value):
                return False

        return True

    def _create_event_from_rule(self, rule: dict, source: str = "") -> Optional[MarketEvent]:
        """从规则创建事件

        Args:
            rule: 匹配的规则dict
            source: 事件来源（新闻标题/规则描述）

        Returns:
            MarketEvent 或 None
        """
        event_cfg = rule.get("event", {})
        if not event_cfg:
            return None

        # 如果关键词规则有bullish和bearish，需要根据匹配的是哪个来确定sentiment
        sentiment = event_cfg.get("sentiment", "neutral")

        return MarketEvent(
            event_type=event_cfg.get("event_type", "macro"),
            sentiment=sentiment,
            impact_level=event_cfg.get("impact_level", 2),
            scope=event_cfg.get("scope", "market"),
            duration=event_cfg.get("duration", "short"),
            source=source or rule.get("description", ""),
            summary=rule.get("display_name", rule.get("name", "")),
            timestamp=datetime.now().isoformat(),
            detection_method="keyword" if rule.get("trigger", {}).get("type") == "keyword" else "rule",
        )

    def _get_market_index_data(self) -> Optional[dict]:
        """获取沪深300指数数据

        Returns:
            dict 含 index_change_pct 等字段，或 None
        """
        try:
            from src.data.akshare_client import AKShareClient
            AKShareClient._ensure_baostock_login()
            import baostock as bs
            rs = bs.query_history_k_data_plus(
                "sh.000300",
                "date,close,pctChg",
                start_date=(datetime.now().strftime("%Y-%m-%d")),
                end_date=(datetime.now().strftime("%Y-%m-%d")),
            )
            if rs.error_code == '0' and rs.next():
                row = rs.get_row_data()
                return {
                    "index_change_pct": float(row[2]) if len(row) > 2 else 0.0,
                    "index_close": float(row[1]) if len(row) > 1 else 0.0,
                }
        except Exception as e:
            logger.debug(f"获取沪深300数据失败: {e}")

        return None

    def _ai_classify_event(self, title: str, summary: str) -> Optional[MarketEvent]:
        """AI二次分类宏观事件（v0.8.1增加RAG策略知识增强）

        Args:
            title: 新闻标题
            summary: 新闻摘要

        Returns:
            AI分类后的事件，或 None
        """
        if not self._ai_client:
            return None

        try:
            user_prompt = f"请判断以下新闻是否为需要投资者关注的重大事件：\n\n标题：{title}\n摘要：{summary}"

            # v0.8.1: RAG策略知识增强
            rag_context = self._get_rag_context_for_event(title, summary)
            if rag_context:
                user_prompt += f"\n\n--- 策略知识参考 ---\n{rag_context}"

            response = self._ai_client.chat.completions.create(
                model=self._ai_model,
                messages=[
                    {"role": "system", "content": EVENT_CLASSIFY_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_completion_tokens=300,
                timeout=15,
                **({"extra_body": {"thinking": {"type": "disabled"}}} if str(self._ai_model).startswith("deepseek-v4") else {}),
            )

            content = (response.choices[0].message.content or "").strip()
            if not content:
                return None

            # 解析JSON
            json_str = content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()

            data = json.loads(json_str)

            if not data.get("is_significant", False):
                return None

            # v0.8.6.3: 解析四要素（笨总现象级事件，ISS-041 events）
            four_elements = self._parse_four_elements(data.get("four_elements"))

            # 真实性低 → 事件降级（不真否决，提示用户核实）
            degraded = False
            if four_elements and four_elements.get("authenticity", 100) < 30:
                degraded = True
                # 真实性存疑的"重大"事件降为"需关注"，避免伪信号放大
                orig_level = int(data.get("impact_level", 2))
                data["impact_level"] = max(2, orig_level - 2)
                # 报告1.2 笨总教学三：真实性是一票否决权 -- 信号影响已在 to_ai_modifier_result 剥离(neutral)，
                # summary 打标提示用户核实（调研报告: AI只出疑似伪信号，用户最终确认，不硬丢弃）
                if data.get("summary"):
                    data["summary"] = f"🚫真实性存疑(可能伪信号) {data['summary']}"

            return MarketEvent(
                event_type=data.get("event_type", "macro"),
                sentiment=data.get("sentiment", "neutral"),
                impact_level=int(data.get("impact_level", 2)),
                scope=data.get("scope", "market"),
                duration=data.get("duration", "short"),
                summary=data.get("summary", ""),
                timestamp=datetime.now().isoformat(),
                detection_method="ai",
                four_elements=four_elements,
            )

        except Exception as e:
            logger.warning(f"AI事件分类失败: {e}")
            return None

    def _ai_classify_portfolio_news(
        self, stock_name: str, stock_code: str, news_text: str
    ) -> Optional[MarketEvent]:
        """AI判断持仓股新闻是否影响持仓（v0.8.1增加RAG策略知识增强）

        Args:
            stock_name: 股票名称
            stock_code: 股票代码
            news_text: 新闻标题列表

        Returns:
            AI判断后的事件，或 None
        """
        if not self._ai_client:
            return None

        try:
            user_prompt = (
                f"请判断以下关于持仓股 {stock_name}({stock_code}) 的新闻是否影响持仓安全：\n\n"
                f"{news_text}"
            )

            # v0.8.1: RAG策略知识增强 - 检索持仓股相关策略
            rag_context = ""
            if self._rag_service and self._rag_service.is_available():
                try:
                    search_query = f"{stock_name} 持仓风险 应对策略"
                    rag_context = self._rag_service.get_context(
                        search_query, target="event",
                        event_type="earnings",
                        top_k=3, max_length=800,
                    )
                    if rag_context:
                        user_prompt += f"\n\n--- 策略知识参考 ---\n{rag_context}"
                        logger.debug(f"EventLayer: RAG检索到持仓策略知识({len(rag_context)}字)")
                except Exception as e:
                    logger.debug(f"EventLayer: RAG检索异常(不影响主流程): {e}")

            response = self._ai_client.chat.completions.create(
                model=self._ai_model,
                messages=[
                    {"role": "system", "content": EVENT_CLASSIFY_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_completion_tokens=300,
                timeout=15,
                **({"extra_body": {"thinking": {"type": "disabled"}}} if str(self._ai_model).startswith("deepseek-v4") else {}),
            )

            content = (response.choices[0].message.content or "").strip()
            if not content:
                return None

            json_str = content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()

            data = json.loads(json_str)

            if not data.get("is_significant", False):
                return None

            # v0.8.6.3: 解析四要素 + 真实性低降级（同宏观事件逻辑）
            four_elements = self._parse_four_elements(data.get("four_elements"))
            if four_elements and four_elements.get("authenticity", 100) < 30:
                orig_level = int(data.get("impact_level", 2))
                data["impact_level"] = max(2, orig_level - 2)
                # 报告1.2 笨总教学三：真实性是一票否决权 -- 信号影响已在 to_ai_modifier_result 剥离(neutral)，
                # summary 打标提示用户核实（调研报告: AI只出疑似伪信号，用户最终确认，不硬丢弃）
                if data.get("summary"):
                    data["summary"] = f"🚫真实性存疑(可能伪信号) {data['summary']}"

            return MarketEvent(
                event_type=data.get("event_type", "earnings"),
                sentiment=data.get("sentiment", "neutral"),
                impact_level=int(data.get("impact_level", 2)),
                scope=data.get("scope", "stock"),
                duration=data.get("duration", "short"),
                summary=data.get("summary", ""),
                source=f"{stock_name}新闻",
                affected_codes=[stock_code],
                timestamp=datetime.now().isoformat(),
                detection_method="ai",
                four_elements=four_elements,
            )

        except Exception as e:
            logger.warning(f"AI持仓股事件分类失败 ({stock_code}): {e}")
            return None

    # ===== 笨总四要素（v0.8.6.3，ISS-041 events） =====

    @staticmethod
    def _parse_four_elements(raw) -> Optional[dict]:
        """解析 AI 返回的四要素 dict，校验+钳制 0-100。失败返回 None。

        四要素：authenticity(真实性)/virality(传播性)/scale(规模性)/timeliness(时效性)
        注意：真实性是 AI 基于单条文本的疑似判断，非多源交叉验证。
        """
        if not isinstance(raw, dict):
            return None
        keys = ("authenticity", "virality", "scale", "timeliness")
        result = {}
        for k in keys:
            v = raw.get(k)
            try:
                result[k] = max(0, min(100, float(v)))
            except (TypeError, ValueError):
                return None  # 任一要素缺失/非法 → 整体判 None（不半填充）
        return result

    def get_available_rules(self) -> list[dict]:
        """获取所有可用事件规则"""
        result = []
        for rule in self.rules:
            result.append({
                "name": rule.get("name", ""),
                "display_name": rule.get("display_name", ""),
                "description": rule.get("description", ""),
                "type": rule.get("trigger", {}).get("type", ""),
            })
        return result

    def is_available(self) -> bool:
        """检查事件层是否可用"""
        return self.enabled

    # ===== RAG策略知识增强（v0.8.1） =====

    def _get_rag_context_for_event(self, title: str, summary: str = "") -> str:
        """根据事件内容检索相关策略知识

        v0.8.1新增：为事件分类提供策略参考，帮助AI更准确判断事件影响。
        结合新闻关键词和事件类型映射的预定义搜索词。

        Args:
            title: 新闻标题
            summary: 新闻摘要

        Returns:
            策略知识上下文文本，或空字符串
        """
        if not self._rag_service or not self._rag_service.is_available():
            return ""

        try:
            # 组合搜索词：标题关键词 + 预定义事件类型搜索词
            search_parts = [title]

            # 尝试匹配事件类型关键词
            text = f"{title} {summary}"
            for event_type, query in self.EVENT_RAG_QUERIES.items():
                # 简单关键词匹配
                type_keywords = {
                    "policy": ["政策", "监管", "降息", "加息", "法规"],
                    "war": ["战争", "冲突", "地缘", "军事"],
                    "earnings": ["财报", "业绩", "盈利", "分红", "暴雷"],
                    "macro": ["GDP", "CPI", "PMI", "宏观经济", "经济数据"],
                    "black_swan": ["黑天鹅", "崩盘", "熔断", "危机"],
                    "market_crash": ["暴跌", "跌停", "恐慌", "大跌"],
                }
                keywords = type_keywords.get(event_type, [])
                if any(kw in text for kw in keywords):
                    search_parts.append(query)
                    break  # 只匹配最相关的事件类型

            search_query = " ".join(search_parts[:3])  # 限制搜索词长度
            rag_context = self._rag_service.get_context(
                search_query, target="event",
                top_k=3, max_length=800,
            )

            if rag_context:
                logger.debug(f"EventLayer: RAG检索到事件策略知识({len(rag_context)}字)")

            return rag_context or ""

        except Exception as e:
            logger.debug(f"EventLayer: RAG检索异常(不影响主流程): {e}")
            return ""
