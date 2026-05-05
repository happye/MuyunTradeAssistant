"""新闻数据获取客户端 - v0.8.0 AI Modifier配套

数据源：
1. AKShare stock_news_em(symbol) — 个股新闻（标题+内容+时间+来源）
2. AKShare stock_info_global_em() — 全球宏观快讯（标题+摘要+时间）

缓存机制：
- 个股新闻：同会话同一股票只抓一次（内存缓存）
- 宏观快讯：缓存1小时（可配置 cache_ttl）
"""

import time
import logging
from typing import Optional
from datetime import datetime

import akshare as ak

logger = logging.getLogger(__name__)


class NewsClient:
    """新闻数据获取客户端"""

    # 内存缓存
    _stock_news_cache: dict[str, tuple[float, list[dict]]] = {}  # code -> (timestamp, news_list)
    _macro_news_cache: tuple[float, list[dict]] = (0.0, [])       # (timestamp, news_list)
    _cache_ttl: int = 3600  # 默认1小时

    @classmethod
    def configure(cls, cache_ttl: int = 3600):
        """配置缓存参数

        Args:
            cache_ttl: 缓存有效期（秒），默认3600（1小时）
        """
        cls._cache_ttl = cache_ttl

    @classmethod
    def get_stock_news(cls, stock_code: str, max_count: int = 10) -> list[dict]:
        """获取个股新闻

        使用 AKShare stock_news_em(symbol) 获取东方财富个股新闻。
        缓存策略：同会话同一股票只抓一次。

        Args:
            stock_code: 股票代码（如 600519）
            max_count: 最多返回条数

        Returns:
            list[dict]: 新闻列表，每条包含 title/content/time/source
        """
        # 检查缓存
        if stock_code in cls._stock_news_cache:
            cached_time, cached_news = cls._stock_news_cache[stock_code]
            # 个股新闻：同会话内不重复抓取
            logger.info(f"个股新闻命中缓存: {stock_code} ({len(cached_news)}条)")
            return cached_news[:max_count]

        try:
            # AKShare stock_news_em 接受6位代码
            code = stock_code.strip().zfill(6)
            df = ak.stock_news_em(symbol=code)

            if df is None or df.empty:
                logger.warning(f"个股新闻返回空: {stock_code}")
                return []

            news_list = []
            for _, row in df.head(max_count).iterrows():
                news_item = {
                    "title": str(row.get("新闻标题", row.get("title", ""))),
                    "content": str(row.get("新闻内容", row.get("content", ""))),
                    "time": str(row.get("发布时间", row.get("time", ""))),
                    "source": str(row.get("文章来源", row.get("source", ""))),
                }
                # 清理空值
                if news_item["title"] or news_item["content"]:
                    news_list.append(news_item)

            # 写入缓存
            cls._stock_news_cache[stock_code] = (time.time(), news_list)
            logger.info(f"个股新闻获取成功: {stock_code} ({len(news_list)}条)")

            return news_list[:max_count]

        except Exception as e:
            logger.warning(f"获取个股新闻失败 {stock_code}: {e}")
            return []

    @classmethod
    def get_macro_news(cls, max_count: int = 10) -> list[dict]:
        """获取全球宏观财经快讯

        使用 AKShare stock_info_global_em() 获取东方财富全球财经快讯。
        缓存策略：缓存1小时（可配置）。

        Args:
            max_count: 最多返回条数

        Returns:
            list[dict]: 新闻列表，每条包含 title/summary/time
        """
        # 检查缓存
        cached_time, cached_news = cls._macro_news_cache
        if cached_news and (time.time() - cached_time) < cls._cache_ttl:
            logger.info(f"宏观快讯命中缓存 ({len(cached_news)}条)")
            return cached_news[:max_count]

        try:
            df = ak.stock_info_global_em()

            if df is None or df.empty:
                logger.warning("宏观快讯返回空")
                return cached_news[:max_count] if cached_news else []

            news_list = []
            for _, row in df.head(max_count).iterrows():
                news_item = {
                    "title": str(row.get("标题", row.get("title", ""))),
                    "summary": str(row.get("摘要", row.get("content", ""))),
                    "time": str(row.get("发布时间", row.get("time", ""))),
                }
                if news_item["title"]:
                    news_list.append(news_item)

            # 写入缓存
            cls._macro_news_cache = (time.time(), news_list)
            logger.info(f"宏观快讯获取成功 ({len(news_list)}条)")

            return news_list[:max_count]

        except Exception as e:
            logger.warning(f"获取宏观快讯失败: {e}")
            return cached_news[:max_count] if cached_news else []

    @classmethod
    def gather_news_for_analysis(cls, stock_code: str, max_per_source: int = 10) -> dict:
        """为AI分析收集新闻数据

        收集两类新闻：
        1. 个股新闻: stock_news_em(symbol) → 10条
        2. 宏观快讯: stock_info_global_em() → 10条(缓存1小时)

        Args:
            stock_code: 股票代码
            max_per_source: 每类新闻最大条数

        Returns:
            dict: {
                "stock_news": [...],   # 个股新闻
                "macro_news": [...],   # 宏观快讯
                "stock_code": str,     # 股票代码
                "gathered_at": str,    # 收集时间
            }
        """
        stock_news = cls.get_stock_news(stock_code, max_per_source)
        macro_news = cls.get_macro_news(max_per_source)

        return {
            "stock_news": stock_news,
            "macro_news": macro_news,
            "stock_code": stock_code,
            "gathered_at": datetime.now().isoformat(),
        }

    @classmethod
    def format_news_for_ai(cls, news_data: dict) -> str:
        """将新闻数据格式化为AI可读的文本

        Args:
            news_data: gather_news_for_analysis 返回的数据

        Returns:
            str: 格式化后的新闻文本
        """
        parts = []

        # 个股新闻
        stock_news = news_data.get("stock_news", [])
        if stock_news:
            parts.append(f"【{news_data['stock_code']} 个股新闻】")
            for i, news in enumerate(stock_news, 1):
                title = news.get("title", "")
                content = news.get("content", "")
                # 截断过长内容（最多200字）
                if len(content) > 200:
                    content = content[:200] + "..."
                parts.append(f"{i}. {title}")
                if content:
                    parts.append(f"   {content}")

        # 宏观快讯
        macro_news = news_data.get("macro_news", [])
        if macro_news:
            parts.append("\n【宏观财经快讯】")
            for i, news in enumerate(macro_news, 1):
                title = news.get("title", "")
                summary = news.get("summary", "")
                parts.append(f"{i}. {title}")
                if summary and summary != "nan":
                    # 截断过长摘要
                    if len(summary) > 150:
                        summary = summary[:150] + "..."
                    parts.append(f"   {summary}")

        return "\n".join(parts) if parts else "暂无相关新闻数据"

    @classmethod
    def clear_cache(cls):
        """清空所有缓存"""
        cls._stock_news_cache.clear()
        cls._macro_news_cache = (0.0, [])
        logger.info("新闻缓存已清空")
