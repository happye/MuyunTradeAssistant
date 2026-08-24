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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 单个数据源调用的硬超时（秒）。akshare 底层 requests 默认无 timeout，
# 一个接口卡住会挂起到 OS 报 WinError 10060；用线程级超时包住，超时放弃走降级。
# 可由 settings.yaml 的 benzong.data_call_timeout 覆盖。
DATA_CALL_TIMEOUT = 30.0


def _fix_curl_ssl_paths():
    """调用公共 SSL 修复（src.data.source_check.fix_curl_ssl_paths）。

    v0.8.6.3：原本地实现已提取为公共函数，供 news_client 等共享。
    保留本 wrapper 供 data_provider._safe_call 调用，避免改调用点。
    """
    from src.data.source_check import fix_curl_ssl_paths
    fix_curl_ssl_paths()


def _safe_call(func_name: str, fn, *args, timeout: Optional[float] = None, **kwargs):
    """统一的"失败返回 None + 日志"包装，带线程级硬超时。

    akshare 底层 requests 默认无 timeout，接口卡住会挂起到 WinError 10060/10054。
    用 ThreadPoolExecutor 包住：超时放弃该调用返回 None（走降级），不冻结主流程。
    失败时按错误类型给可操作 hint（与 E1-B5 一致）。
    """
    _fix_curl_ssl_paths()  # 确保 akshare curl_cffi 接口 SSL 可用
    to = timeout if timeout is not None else DATA_CALL_TIMEOUT
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=to)
    except FuturesTimeout:
        # v0.8.7.2 审查修复：不能用 with 块——__exit__ 的 shutdown(wait=True) 会 join
        # 卡死的线程，"硬超时"退化成等底层 requests OS级放弃（分钟级）。改为立即
        # 放弃等待（孤儿线程自行消亡，范本 source_check._probe_t）。
        logger.warning(f"data_provider.{func_name} 超时(>{to:.0f}s)，放弃走降级")
        return None
    except Exception as e:
        err_str = str(e)
        hint = ""
        if "ProxyError" in err_str or "10057" in err_str:
            hint = " | 提示：代理可能干扰，可临时关代理"
        elif "Timeout" in err_str or "timeout" in err_str.lower() or "10060" in err_str:
            hint = " | 提示：网络超时，可稍后重试"
        elif "10054" in err_str or "ConnectionReset" in err_str or "连接" in err_str:
            hint = " | 提示：连接被重置，可能反爬/代理，可稍后重试"
        elif "登录失败" in err_str:
            hint = " | 提示：BaoStock 临时故障"
        logger.warning(f"data_provider.{func_name} 失败: {type(e).__name__}: {err_str[:80]}{hint}")
        return None
    finally:
        # 无论成败都立即释放执行器（不 join 孤儿线程）；成功路径同样需要
        ex.shutdown(wait=False)


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
    数据源（v0.8.6.4 多源降级）：
    1. AKShare stock_news_em（东方财富个股新闻，主源）
    2. AKShare stock_zh_a_disclosure_report_cninfo（巨潮官方公告，备用源）
    主源失败 → 备用源，避免新闻源挂掉时风险维/景气维无数据。
    """
    try:
        import akshare as ak
    except ImportError:
        return []

    cutoff = datetime.now() - timedelta(days=days)

    # 主源：东方财富个股新闻
    df = _safe_call("stock_news_em", lambda: ak.stock_news_em(symbol=code))
    items = _parse_news_df(df, cutoff) if (df is not None and not df.empty) else []

    # 备用源：巨潮官方公告（主源失败或空时）
    if not items:
        backup = _safe_call(
            "stock_zh_a_disclosure_report_cninfo",
            lambda: _fetch_cninfo_disclosure(ak, code, days),
            timeout=25,
        )
        if backup:
            items = _parse_news_df(backup, cutoff)

    return items


def _fetch_cninfo_disclosure(ak, code: str, days: int):
    """巨潮信息网官方公告（备用新闻源）。返回 DataFrame 或 None。

    v0.8.6.5 修复：market 参数必须用 '沪深京'（akshare 默认值），不能用
    '上证'/'深证'（会 KeyError），否则备用源从落地起就没生效过。
    """
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        return ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code, market="沪深京", start_date=start, end_date=end
        )
    except Exception as e:
        logger.debug(f"巨潮公告拉取失败 {code}: {e}")
        return None


def _parse_news_df(df, cutoff) -> list[dict]:
    """从新闻/公告 DataFrame 解析为统一 [{title,date,content,source}] 列表。"""
    if df is None or df.empty:
        return []
    items = []
    try:
        for _, row in df.iterrows():
            title = row.get("新闻标题") or row.get("公告标题") or row.get("title") or ""
            date_str = row.get("发布时间") or row.get("公告时间") or row.get("date") or ""
            content = row.get("新闻内容") or row.get("content") or ""
            source = row.get("文章来源") or row.get("来源") or row.get("source") or ""
            try:
                date_dt = datetime.fromisoformat(str(date_str).split(" ")[0]) if date_str else None
                if date_dt and date_dt < cutoff:
                    continue
            except (ValueError, AttributeError):
                pass
            items.append({
                "title": str(title)[:200],
                "date": str(date_str)[:10],
                "content": str(content)[:500],
                "source": str(source)[:50],
            })
    except Exception as e:
        logger.warning(f"新闻/公告解析失败: {e}")
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
    复用 scanner.MarketCache 的新浪全市场快照（绕代理）。

    注意（v0.8.7.2 起）：MarketCache 的 L1 全市场快照已是类级共享缓存（所有实例共用
    5 分钟 TTL），本函数每次 `MarketCache()` 新建实例也能命中缓存。批量场景仍由
    auto_score_batch 开头拉一次后经 market_turnover 参数注入 get_data_summary，
    保证整批用同一成交额口径。
    """
    try:
        from src.scanner.market_cache import MarketCache
        mc = MarketCache()
        df = _safe_call("market_cache.get_all_stocks", mc.get_all_stocks, timeout=20)
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


def get_data_summary(code: str, market_turnover: Optional[float] = None) -> dict:
    """一次性拉所有可用数据，给 6 维评分器使用。

    Args:
        code: 股票代码
        market_turnover: 外部注入的全市场成交额（万亿）。批量场景由 auto_score_batch
            开头拉一次后注入，避免每只股票重打 sina。None 时本函数内部自调 get_market_turnover。

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

    if market_turnover is not None:
        summary["market_turnover"] = market_turnover
        summary["fetch_status"]["market_turnover"] = True
    else:
        summary["market_turnover"] = get_market_turnover()
        summary["fetch_status"]["market_turnover"] = summary["market_turnover"] is not None

    success_count = sum(1 for v in summary["fetch_status"].values() if v)
    logger.info(f"data_provider 数据拉取 {code}: {success_count}/5 成功")

    return summary
