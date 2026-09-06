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
from datetime import datetime, date

import akshare as ak

logger = logging.getLogger(__name__)


class NewsClient:
    """新闻数据获取客户端"""

    # 内存缓存
    _stock_news_cache: dict[str, tuple[float, list[dict]]] = {}  # code -> (timestamp, news_list)
    _macro_news_cache: tuple[float, list[dict]] = (0.0, [])       # (timestamp, news_list)
    _cache_ttl: int = 3600  # 默认1小时
    _debug: bool = False    # debug模式开关

    @classmethod
    def configure(cls, cache_ttl: int = 3600, debug: bool = False):
        """配置缓存参数

        Args:
            cache_ttl: 缓存有效期（秒），默认3600（1小时）
            debug: 是否开启debug模式（打印新闻抓取详情）
        """
        cls._cache_ttl = cache_ttl
        cls._debug = debug

    @classmethod
    # ISS-091：个股新闻当日磁盘缓存（~/.muyun/news_cache/{code}_{date}.json）
    # 内存缓存跨进程即失效，REPL 重启后同日重复分析会重爬新闻——磁盘层补齐
    @classmethod
    def _news_disk_path(cls, stock_code: str):
        from pathlib import Path
        d = Path.home() / ".muyun" / "news_cache"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            return None
        return d / f"{stock_code.strip().zfill(6)}_{date.today().isoformat()}.json"

    @classmethod
    def _news_disk_load(cls, stock_code: str):
        f = cls._news_disk_path(stock_code)
        if f is None or not f.exists():
            return None
        try:
            import json
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else None
        except Exception:
            return None

    @classmethod
    def _news_disk_save(cls, stock_code: str, news_list: list) -> None:
        f = cls._news_disk_path(stock_code)
        if f is None:
            return
        try:
            import json
            f.write_text(json.dumps(news_list, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

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
            # 个股新闻：同会话内不重复抓取；但跨天必须重抓——
            # v0.8.7.6 审计修复 B11：REPL 常开跨天后第2天还拿第1天新闻做情绪调节，
            # source_count 显示正常用户无从察觉。缓存键含日期（昨天缓存今天当未命中）。
            if cached_time == date.today().isoformat():
                logger.info(f"个股新闻命中缓存: {stock_code} ({len(cached_news)}条)")
                return cached_news[:max_count]
            logger.info(f"个股新闻缓存已跨天({cached_time})，重抓: {stock_code}")

        # ISS-091：内存未命中（进程重启）→ 查当日磁盘缓存
        disk_news = cls._news_disk_load(stock_code)
        if disk_news is not None:
            cls._stock_news_cache[stock_code] = (date.today().isoformat(), disk_news)
            logger.info(f"个股新闻命中当日磁盘缓存: {stock_code} ({len(disk_news)}条)")
            return disk_news[:max_count]

        try:
            # AKShare stock_news_em 接受6位代码
            code = stock_code.strip().zfill(6)
            # v0.8.6.3: 修复 curl_cffi SSL（中文路径致 curl 77），复用公共修复
            from src.data.source_check import fix_curl_ssl_paths
            fix_curl_ssl_paths()
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
            cls._stock_news_cache[stock_code] = (date.today().isoformat(), news_list)  # B11: 存日期字符串供跨天失效
            cls._news_disk_save(stock_code, news_list)   # ISS-091: 当日磁盘缓存
            logger.info(f"个股新闻获取成功: {stock_code} ({len(news_list)}条)")

            if cls._debug:
                print(f"\n[NEWS DEBUG] 个股新闻 {stock_code}: 获取{len(news_list)}条")
                for i, n in enumerate(news_list[:3], 1):
                    print(f"  {i}. {n.get('title', '?')[:60]}")
                if len(news_list) > 3:
                    print(f"  ... 共{len(news_list)}条")

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
            # v0.8.6.3: 修复 curl_cffi SSL（中文路径致 curl 77），复用公共修复
            from src.data.source_check import fix_curl_ssl_paths
            fix_curl_ssl_paths()
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

            if cls._debug:
                print(f"\n[NEWS DEBUG] 宏观快讯: 获取{len(news_list)}条")
                for i, n in enumerate(news_list[:3], 1):
                    print(f"  {i}. {n.get('title', '?')[:60]}")

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
