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
from datetime import datetime, timedelta

import yaml
import pandas as pd
from openai import OpenAI

from src.core.ai_model import thinking_disabled_body, NO_TEMPERATURE_MODELS
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
  "scope": "market/sector/stock/unknown",
  "affected_codes": ["受影响的6位股票代码列表；scope=market 或无法确定具体股票时留空数组"],
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
                matched, matched_sentiments = self._match_keywords(text, rule)
                if matched:
                    event = self._create_event_from_rule(rule, source=title)
                    if event:
                        # v0.8.7.6 审计修复 B09：earnings_surprise 这类"利好+利空关键词同规则"
                        # 但 rule 无 sentiment 字段 → 恒 neutral，暴雷新闻被判中性并只给
                        # position_cap。修法：规则显式声明 sentiment 优先；否则按实际命中的
                        # 关键词方向判定（双向同时命中=方向不明，维持 neutral）。
                        if not rule.get("event", {}).get("sentiment") and matched_sentiments:
                            if len(matched_sentiments) == 1:
                                event.sentiment = next(iter(matched_sentiments))
                            # 双向命中：保持 neutral（风险侧利空词已单独命中黑天鹅规则线）
                        # v0.8.7.6 审计修复 B10：关键词来源是**全球**快讯（stock_info_global_em），
                        # 单个"暴跌"就能命中 black_swan → impact 5 强制 PANIC+仓位30%，AI 确认
                        # 失败/未启用也拦不住（"美股暴跌"误伤 A 股个股）。修法：关键词触发的
                        # impact5 事件必须 AI 确认同级别才保留；AI 失败/未启用一律降级为 3。
                        if (event.detection_method == "keyword"
                                and event.impact_level >= 5):
                            if not (self.ai_classify_enabled and self._ai_available):
                                event.impact_level = 3
                                logger.warning(
                                    f"关键词命中 impact5 事件但 AI 确认不可用，降级 impact 5→3: {event.summary}"
                                )
                            else:
                                ai_event = self._ai_classify_event(title, summary)
                                if ai_event:
                                    # AI确认是重大事件，使用AI的分类结果
                                    if ai_event.impact_level >= event.impact_level:
                                        event = ai_event
                                        event.source = title
                                    # AI认为不重大，降级
                                    elif not ai_event.sentiment or ai_event.impact_level < 2:
                                        continue
                                    elif ai_event.impact_level < 5:
                                        event.impact_level = ai_event.impact_level
                                else:
                                    event.impact_level = 3
                                    logger.warning(
                                        f"关键词命中 impact5 事件但 AI 确认失败，降级 impact 5→3: {event.summary}"
                                    )
                        elif self.ai_classify_enabled and self._ai_available and event.impact_level >= 3:
                            # 非 impact5 事件：维持原 AI 二次分类行为
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
                        # v0.8.7.6 审计修复 B25：指数规则按 YAML 顺序取第一个命中即停——
                        # 原 market_crash(-3%) 与 market_slump(-1.5%) 无互斥，跌3%时同帧产出
                        # 两条同源事件叠加上调影响力。YAML 中 crash（更重）排在 slump 之前。
                        break

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
            # v0.8.7.6 审计修复 B08：market_data 是全市场 DataFrame，原 market_data.get(code)
            # 是按"列名"查找恒 None → 持仓暴跌/跌停两条价格规则从未触发过（死功能）。
            # 改为按"代码"列取行并转成规则所需字段 dict。
            stock_info = self._lookup_stock_row(market_data, code)
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

    @staticmethod
    def _lookup_stock_row(market_data, code: str) -> Optional[dict]:
        """从全市场行情中取单只持仓股的规则字段 dict（B08 修复的取数辅助）。

        market_data 是 MarketCache.get_all_stocks() 的 DataFrame（列：代码/涨跌幅/最新价...），
        事件规则（stock_plunge/stock_limit_down）的 conditions 用字段名 change_pct。
        返回 None = 该股不在快照中/行情不可用。
        """
        try:
            if market_data is None or isinstance(market_data, dict):
                return None
            if "代码" not in market_data.columns:
                return None
            code_norm = str(code).split(".")[-1].zfill(6)
            mask = market_data["代码"].astype(str).str.zfill(6) == code_norm
            rows = market_data[mask]
            if rows.empty:
                return None
            r = rows.iloc[0]

            def _f(colname):
                try:
                    v = r.get(colname)
                    return float(v) if v is not None and pd.notna(v) else None
                except (TypeError, ValueError):
                    return None

            return {"change_pct": _f("涨跌幅"), "price": _f("最新价")}
        except Exception:
            return None

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
        # ISS-117 D2/D3（S0）：影响等级 ≠ 可信程度。四要素缺失/无真实性评分 = 事实
        # 资格 UNKNOWN——confidence=0，不产生仓位上限/状态硬覆盖，仅作提示。
        _fe = getattr(event, "four_elements", None)
        _auth = _fe.get("authenticity") if isinstance(_fe, dict) else None
        if not isinstance(_auth, (int, float)):
            return AIModifierResult(
                sentiment=event.sentiment,
                confidence=0.0,
                risk_level="high" if event.impact_level >= 4 else "medium" if event.impact_level >= 3 else "low",
                event_type=event.event_type,
                summary=f"[事件未核实-仅提示] {event.summary or event.source}",
                adjusted=False,
                score_adjustment=0.0,
                position_cap=1.0,
            )
        if _auth < 30:
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

        # 基础映射（D2：confidence 来自真实性评分，不再用 impact 冒充）
        result = AIModifierResult(
            sentiment=event.sentiment,
            confidence=min(_auth / 100.0, 1.0),
            risk_level="high" if event.impact_level >= 4 else "medium" if event.impact_level >= 3 else "low",
            event_type=event.event_type,
            summary=f"[事件] {event.summary}" if event.summary else f"[事件] {event.source}",
            adjusted=True,
        )

        # 信号调节（影响等级决定幅度；真实性资格已过 30 门槛）
        if event.sentiment == "bearish" and event.impact_level >= 3:
            result.score_adjustment = -(event.impact_level / 5.0) * 0.3  # 最多-0.3
        elif event.sentiment == "bullish" and event.impact_level >= 3:
            result.score_adjustment = (event.impact_level / 5.0) * 0.15  # 最多+0.15

        # ISS-117 Q2（S01 验收裁决）：AI 自评真实性不等于原件核验——事件在本批
        # 一律不获得 force_state 与硬限仓（position_cap），只保留有界软调节 + 提示。
        # 后续若开放事件硬权限，须绑定明确已批准政策与证据 resolver，不得仅提高阈值。
        return result

    # ===== 内部方法 =====

    # 关键词命中前的否定前缀（B25 修复："不降息"/"未兑现利好" 不应命中利好词）
    _NEGATION_PREFIXES = ("不", "未", "无", "非", "难", "没")

    def _match_keywords(self, text: str, rule: dict) -> tuple:
        """检查文本是否匹配关键词规则

        Args:
            text: 新闻文本
            rule: 规则dict，trigger.keywords 含 bullish/bearish 列表

        Returns:
            (是否匹配, 命中的方向集合) —— B09 修复需要方向信息判定 sentiment
        """
        keywords_cfg = rule.get("trigger", {}).get("keywords", {})
        min_matches = self.global_config.get("min_keyword_matches", 1)

        matched_sentiments = set()
        match_count = 0
        for sentiment, kw_list in keywords_cfg.items():
            for kw in kw_list:
                idx = text.find(kw)
                if idx < 0:
                    continue
                # B25 修复：否定前缀守卫——命中位置前 2 字符内出现否定词则不算命中
                #（如"不降息"/"降息预期落空"不判利好）。中文否定常紧邻或隔1字。
                prefix = text[max(0, idx - 2):idx]
                if any(neg in prefix for neg in self._NEGATION_PREFIXES):
                    continue
                matched_sentiments.add(sentiment)
                match_count += 1
                if match_count >= min_matches:
                    return True, matched_sentiments

        return False, matched_sentiments

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

    # B20 修复：进程级短缓存（300s）——批量分析时每只股打一次 baostock 没必要
    _index_data_cache: tuple = (None, 0.0)
    _INDEX_CACHE_TTL = 300.0

    def _get_market_index_data(self) -> Optional[dict]:
        """获取沪深300指数数据

        v0.8.7.6 审计修复 B20：原 start_date==end_date==今天——非交易日/盘前必然查不到
        返回 None → market_crash/market_slump 两条指数规则静默全失效；且无缓存，
        批量分析每只股打一次 baostock。现取最近10天取最后一行 + 进程级 300s 缓存。

        Returns:
            dict 含 index_change_pct 等字段，或 None
        """
        cached, cached_ts = EventLayer._index_data_cache
        if cached is not None and (time.time() - cached_ts) < EventLayer._INDEX_CACHE_TTL:
            return cached
        try:
            from src.data.akshare_client import AKShareClient, _call_with_timeout
            from concurrent.futures import TimeoutError as _FuturesTimeout
            AKShareClient._ensure_baostock_login()
            import baostock as bs
            rs = bs.query_history_k_data_plus(
                "sh.000300",
                "date,close,pctChg",
                start_date=((datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")),
                end_date=(datetime.now().strftime("%Y-%m-%d")),
            )

            # v0.8.9.5（彻查批 P1-2）：bs.next() 读取包线程级硬超时（ISS-047 同类点）
            def _read_rows():
                last_row = None
                while rs.next():
                    last_row = rs.get_row_data()  # 取最后一行（最近交易日）
                return last_row

            try:
                last_row = _call_with_timeout(_read_rows, timeout=30)
            except _FuturesTimeout:
                logger.warning("Baostock 读取沪深300行情超时，市场规则事件本轮跳过")
                return None
            if last_row:
                result = {
                    "index_change_pct": float(last_row[2]) if len(last_row) > 2 else 0.0,
                    "index_close": float(last_row[1]) if len(last_row) > 1 else 0.0,
                }
                EventLayer._index_data_cache = (result, time.time())
                return result
        except Exception as e:
            logger.debug(f"获取沪深300数据失败: {e}")

        return None

    # v0.8.9.5（彻查批 P3）：sentiment 白名单——AI 返回中文（"利空"）或异常值时
    # 归 neutral，避免 to_ai_modifier_result 的 =="bearish" 全 miss 后静默按中性降权
    _VALID_SENTIMENTS = ("bullish", "bearish", "neutral")

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
                # v0.8.9.5（彻查批 P3）：kimi-k2.x 不支持 temperature，走统一判定清单
                **({"temperature": 0.1} if not str(self._ai_model or "").startswith(NO_TEMPERATURE_MODELS) else {}),
                max_completion_tokens=300,
                timeout=15,
                **thinking_disabled_body(self._ai_model),
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

            # v0.8.9.5（彻查批 P3）：sentiment 白名单归一（AI 返回中文/异常值→neutral）
            if data.get("sentiment") not in self._VALID_SENTIMENTS:
                data["sentiment"] = "neutral"

            # ISS-117 Q-R2（两个解析入口同款约束）：scope 缺省/非法 = unknown
            # （不得默认全市场）；affected_codes 只保留规范化 6 位代码。
            # 检测墙钟不是来源公开时点——事件仅获软调节资格（Q2 收口），提示用户核实。
            _scope_raw = str(data.get("scope") or "").strip().lower()
            scope = _scope_raw if _scope_raw in ("market", "sector", "stock") else "unknown"
            affected_codes = []
            for _c in (data.get("affected_codes") or []):
                _c = str(_c).strip().split(".")[0]
                if _c.isdigit() and len(_c) == 6 and _c not in affected_codes:
                    affected_codes.append(_c)

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
                scope=scope,
                affected_codes=affected_codes,
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
                # v0.8.9.5（彻查批 P3）：kimi-k2.x 不支持 temperature，走统一判定清单
                **({"temperature": 0.1} if not str(self._ai_model or "").startswith(NO_TEMPERATURE_MODELS) else {}),
                max_completion_tokens=300,
                timeout=15,
                **thinking_disabled_body(self._ai_model),
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

            # v0.8.9.5（彻查批 P3）：sentiment 白名单归一（AI 返回中文/异常值→neutral）
            if data.get("sentiment") not in self._VALID_SENTIMENTS:
                data["sentiment"] = "neutral"

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

            # ISS-117 Q-R2：scope 缺省/非法 = unknown。范围不明 → 不写 affected_codes
            #（_event_applies_to_stock 对 unknown/空 codes 判不适用）——「范围不明则
            # 不适用软调节」如实兑现，不得借请求上下文自我认定全市场。
            _scope_raw = str(data.get("scope") or "").strip().lower()
            scope = _scope_raw if _scope_raw in ("market", "sector", "stock") else "unknown"

            return MarketEvent(
                event_type=data.get("event_type", "earnings"),
                sentiment=data.get("sentiment", "neutral"),
                impact_level=int(data.get("impact_level", 2)),
                scope=scope,
                duration=data.get("duration", "short"),
                summary=data.get("summary", ""),
                source=f"{stock_name}新闻",
                affected_codes=[stock_code] if scope != "unknown" else [],
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
