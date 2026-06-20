"""笨总评分体系数据提供层（v0.8.6.2）

核心职责：把 AKShare / Baostock 等底层数据接口包装成 6 维评分器需要的形态。
设计原则：
- **失败不抛异常**：所有接口失败返回 None / 空 list（按 E1-B5 风格记日志 + hint）
- **不编造数据**：拿不到就返回 None，让上层 dim 评分器走"confidence=0 + warning"路径
- **复用现有降级链路**：调 akshare_client 的接口（已有 ProxyError/Timeout 重试 + hint）
- **缓存交给 cache.py 上层调度**，本层不缓存

接口列表：
- get_business_introduction(code) -> Optional[str]：主营业务介绍
- get_recent_announcements(code, days) -> list[dict]：最近公告/新闻
- get_industry_info(code) -> Optional[dict]：所属行业 + 行业内位置
- get_recent_kline(code, lookback_days) -> Optional[DataFrame]：近期 K 线
- get_market_turnover(date=None) -> Optional[float]：全市场成交额（万亿，用于流动性系数）
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _fix_curl_ssl_paths():
    """调用公共 SSL 修复（src.data.source_check.fix_curl_ssl_paths）。

    v0.8.6.3：原本地实现已提取为公共函数，供 news_client 等共享。
    保留本 wrapper 供 data_provider._safe_call 调用，避免改调用点。
    """
    from src.data.source_check import fix_curl_ssl_paths
    fix_curl_ssl_paths()


def _safe_call(func_name: str, fn, *args, **kwargs):
    """统一的"失败返回 None + 日志"包装。

    失败时按错误类型给可操作 hint（与 E1-B5 一致）。
    """
    _fix_curl_ssl_paths()  # 确保 akshare curl_cffi 接口 SSL 可用
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        err_str = str(e)
        hint = ""
        if "ProxyError" in err_str or "10057" in err_str:
            hint = " | 提示：代理可能干扰，可临时关代理"
        elif "Timeout" in err_str or "timeout" in err_str.lower():
            hint = " | 提示：网络超时，可稍后重试"
        elif "登录失败" in err_str:
            hint = " | 提示：BaoStock 临时故障"
        logger.warning(f"data_provider.{func_name} 失败: {type(e).__name__}: {err_str[:80]}{hint}")
        return None


def get_business_introduction(code: str) -> Optional[str]:
    """获取公司主营业务介绍。

    数据源优先级：
    1. AKShare stock_zyjs_ths（同花顺主营介绍，最详细）
    2. AKShare stock_individual_info_em（东方财富个股摘要，次选）
    """
    try:
        import akshare as ak
    except ImportError:
        logger.warning("akshare 未安装，无法获取主营介绍")
        return None

    # 试 1：同花顺主营介绍
    df = _safe_call("stock_zyjs_ths", lambda: ak.stock_zyjs_ths(symbol=code))
    if df is not None and not df.empty:
        # 通常返回主营业务、产品类型、产品名称等多列
        try:
            text_parts = []
            for col in df.columns:
                vals = df[col].dropna().astype(str).tolist()
                if vals and col not in ("股票代码", "code"):
                    text_parts.append(f"{col}: {' / '.join(vals[:3])}")
            if text_parts:
                return "\n".join(text_parts)
        except Exception as e:
            logger.warning(f"主营介绍解析失败: {e}")

    # 试 2：东方财富个股信息（兜底）
    df2 = _safe_call("stock_individual_info_em", lambda: ak.stock_individual_info_em(symbol=code))
    if df2 is not None and not df2.empty:
        try:
            # 转 dict 形态
            info = dict(zip(df2["item"], df2["value"]))
            relevant = ["主营业务", "经营范围", "公司简介", "总市值", "行业"]
            text_parts = [f"{k}: {info[k]}" for k in relevant if k in info]
            if text_parts:
                return "\n".join(text_parts)
        except Exception as e:
            logger.warning(f"个股信息解析失败: {e}")

    return None


def get_recent_announcements(code: str, days: int = 30) -> list[dict]:
    """获取最近 N 天公告/新闻列表。

    返回：[{title, date, content, source}, ...]
    数据源：AKShare stock_news_em（东方财富个股新闻）
    """
    try:
        import akshare as ak
    except ImportError:
        return []

    df = _safe_call("stock_news_em", lambda: ak.stock_news_em(symbol=code))
    if df is None or df.empty:
        return []

    cutoff = datetime.now() - timedelta(days=days)
    items = []
    try:
        for _, row in df.iterrows():
            # 列名可能是 ['关键词', '新闻标题', '新闻内容', '发布时间', '文章来源', '新闻链接']
            title = row.get("新闻标题") or row.get("title") or ""
            date_str = row.get("发布时间") or row.get("date") or ""
            content = row.get("新闻内容") or row.get("content") or ""
            source = row.get("文章来源") or row.get("source") or ""

            # 过滤：仅保留 days 天内
            try:
                date_dt = datetime.fromisoformat(str(date_str).split(" ")[0]) if date_str else None
                if date_dt and date_dt < cutoff:
                    continue
            except (ValueError, AttributeError):
                pass  # 解析失败保留

            items.append({
                "title": str(title)[:200],
                "date": str(date_str)[:10],
                "content": str(content)[:500],
                "source": str(source)[:50],
            })
    except Exception as e:
        logger.warning(f"公告解析失败: {e}")

    return items


def get_industry_info(code: str) -> Optional[dict]:
    """获取股票所属行业 + 行业内简单信息。

    数据源（v0.8.6.3 修复 ISS-041/1a：em 接口被反爬，换 Baostock）：
    1. Baostock query_stock_industry（证监会行业分类，已验证可用）
    2. AKShare stock_individual_info_em（em 兜底，常被反爬）

    返回：{industry_name, stock_name, total_market_cap, circulating_market_cap, industry_raw}
    """
    from src.data.akshare_client import _ensure_baostock_login, AKShareClient
    prefix, _ = AKShareClient._normalize_stock_code(code)

    # 主数据源：Baostock 证监会行业分类
    try:
        import baostock as bs
        if _ensure_baostock_login():
            rs = bs.query_stock_industry(code=f"{prefix}.{code}")
            if rs.error_code == '0':
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    f = rs.fields
                    industry = rows[0][f.index('industry')]
                    stock_name = rows[0][f.index('code_name')]
                    return {
                        "industry_name": industry,
                        "stock_name": stock_name,
                        "total_market_cap": "",
                        "circulating_market_cap": "",
                        "industry_raw": {"source": "baostock", "industry": industry},
                    }
            else:
                logger.warning(f"Baostock 行业查询失败: {rs.error_msg}")
    except Exception as e:
        logger.warning(f"Baostock 行业查询异常: {type(e).__name__}: {str(e)[:80]}")

    # 兜底：东方财富个股信息（常被反爬，仅作 fallback）
    try:
        import akshare as ak
    except ImportError:
        return None

    df = _safe_call("stock_individual_info_em", lambda: ak.stock_individual_info_em(symbol=code))
    if df is None or df.empty:
        return None

    try:
        info = dict(zip(df["item"], df["value"]))
        return {
            "industry_name": info.get("行业", ""),
            "stock_name": info.get("股票简称", ""),
            "total_market_cap": info.get("总市值", ""),
            "circulating_market_cap": info.get("流通市值", ""),
            "industry_raw": info,
        }
    except Exception as e:
        logger.warning(f"行业信息解析失败: {e}")
        return None


def get_recent_kline(code: str, lookback_days: int = 750):
    """获取最近 N 天日 K（默认 ~3 年，用于历史估值位置维度）。

    返回 DataFrame 或 None。复用 src/data/akshare_client.py 已有降级链。
    """
    try:
        from src.data.akshare_client import AKShareClient

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
        df = _safe_call(
            "get_historical_kline",
            AKShareClient.get_historical_kline,
            code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            return None
        return df
    except ImportError:
        return None


def get_market_turnover(date: Optional[str] = None) -> Optional[float]:
    """获取全市场单日成交额（万亿）。用于笨总流动性系数。

    Args:
        date: YYYY-MM-DD，默认最新交易日（当前仅支持最新交易日）

    返回：成交额（万亿，如 1.2 表示 1.2 万亿）。失败返回 None。

    数据源（v0.8.6.3 修复 ISS-041/1a：em 接口被反爬，换新浪源）：
    复用 scanner.MarketCache 的新浪全市场快照（绕代理 + 带缓存），
    若用户先 scan 后 bz 可缓存命中秒回。
    """
    try:
        from src.scanner.market_cache import MarketCache
        mc = MarketCache()
        df = mc.get_all_stocks()
        if df is None or df.empty:
            return None
        if "成交额" not in df.columns:
            logger.warning("全市场快照无 成交额 列")
            return None
        total = df["成交额"].sum()
        if total <= 0:
            return None
        trillion = total / 1e12  # 元 → 万亿
        logger.info(f"全市场单日成交额: {trillion:.2f} 万亿 (新浪源, {len(df)} 只)")
        return round(trillion, 2)
    except Exception as e:
        logger.warning(f"成交额获取失败(MarketCache): {type(e).__name__}: {str(e)[:80]}")
        return None


def get_data_summary(code: str) -> dict:
    """一次性拉所有可用数据，给 6 维评分器使用。

    返回：{business_intro, announcements, industry, kline, market_turnover, fetch_status}
    每个键对应一个数据源，失败的留 None；fetch_status 记录哪些拉到/拉失败。
    """
    summary = {
        "business_intro": None,
        "announcements": [],
        "industry": None,
        "kline": None,
        "market_turnover": None,
        "fetch_status": {},
    }

    # 串行拉（顺序无所谓，每个独立失败）
    summary["business_intro"] = get_business_introduction(code)
    summary["fetch_status"]["business_intro"] = summary["business_intro"] is not None

    summary["announcements"] = get_recent_announcements(code, days=30)
    summary["fetch_status"]["announcements"] = len(summary["announcements"]) > 0

    summary["industry"] = get_industry_info(code)
    summary["fetch_status"]["industry"] = summary["industry"] is not None

    summary["kline"] = get_recent_kline(code)
    summary["fetch_status"]["kline"] = summary["kline"] is not None and not summary["kline"].empty if summary["kline"] is not None else False

    summary["market_turnover"] = get_market_turnover()
    summary["fetch_status"]["market_turnover"] = summary["market_turnover"] is not None

    success_count = sum(1 for v in summary["fetch_status"].values() if v)
    logger.info(f"data_provider 数据拉取 {code}: {success_count}/5 成功")

    return summary
