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

from src.data.timeout_call import call_with_timeout, run_parallel_with_deadline

logger = logging.getLogger(__name__)

# 单个数据源调用的硬超时（秒）。akshare 底层 requests 默认无 timeout，
# 一个接口卡住会挂起到 OS 报 WinError 10060；用线程级超时包住，超时放弃走降级。
# 可由 settings.yaml 的 benzong.data_call_timeout 覆盖。
DATA_CALL_TIMEOUT = 30.0
# 行业景气 get_industry_metrics 的外层总预算与内层单节预算（秒），调用时读模块全局，测试可缩小。
INDUSTRY_METRICS_TIMEOUT_S = 150.0
INDUSTRY_SECTION_TIMEOUT_S = 150.0


def _fix_curl_ssl_paths():
    """调用公共 SSL 修复（src.data.source_check.fix_curl_ssl_paths）。

    v0.8.6.3：原本地实现已提取为公共函数，供 news_client 等共享。
    保留本 wrapper 供 data_provider._safe_call 调用，避免改调用点。
    """
    from src.data.source_check import fix_curl_ssl_paths
    fix_curl_ssl_paths()


_BROKEN_SOURCES: set = set()


def _fast_fail_enabled() -> bool:
    return os.environ.get("MUYUN_FAST_FAIL", "").strip().lower() in ("1", "true", "on")


def _skip_sources() -> set:
    return {x.strip() for x in os.environ.get("MUYUN_SKIP_SOURCES", "").split(",") if x.strip()}


def _breaker_key(func_name: str) -> str:
    import re as _re
    return _re.sub(r"_?\d{6,8}$", "", func_name)


def _safe_call(func_name: str, fn, *args, timeout: Optional[float] = None, **kwargs):
    """统一的"失败返回 None + 日志"包装，带线程级硬超时。

    akshare 底层 requests 默认无 timeout，接口卡住会挂起到 WinError 10060/10054。
    T05-B：用共享 daemon 线程 + 单槽队列限制等待；超时放弃该调用返回 None
    （走降级），不冻结主流程。worker 不能被强杀，底层请求可能仍在运行，迟到
    结果丢弃，不跨调用复用。失败时按错误类型给可操作 hint（与 E1-B5 一致）。
    """
    _fix_curl_ssl_paths()  # 确保 akshare curl_cffi 接口 SSL 可用
    to = timeout if timeout is not None else DATA_CALL_TIMEOUT
    # 本地审查补丁（opt-in）：MUYUN_FAST_FAIL=1 时启用进程级熔断——同一数据源首次
    # 超时/连接失败/非JSON响应后，本进程内后续调用直接降级（不再每只股票等 30s）；
    # MUYUN_SKIP_SOURCES=a,b 直接跳过；MUYUN_DATA_CALL_TIMEOUT 覆盖默认超时。
    _key = _breaker_key(func_name)
    if _fast_fail_enabled():
        if _key in _skip_sources() or _key in _BROKEN_SOURCES:
            logger.debug(f"data_provider.{func_name} 已熔断/跳过，直接降级")
            return None
        if timeout is None and os.environ.get("MUYUN_DATA_CALL_TIMEOUT"):
            to = float(os.environ["MUYUN_DATA_CALL_TIMEOUT"])
    try:
        return call_with_timeout(fn, *args, timeout=to, **kwargs)
    except TimeoutError:
        if _fast_fail_enabled():
            _BROKEN_SOURCES.add(_key)
        logger.warning(
            f"data_provider.{func_name} 超时(>{to:.0f}s)，已停止等待，底层调用可能仍在运行，放弃走降级"
        )
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
        if _fast_fail_enabled() and (hint or type(e).__name__ in ("JSONDecodeError", "ConnectionError", "ConnectTimeout", "ReadTimeout")):
            _BROKEN_SOURCES.add(_key)
        logger.warning(f"data_provider.{func_name} 失败: {type(e).__name__}: {err_str[:80]}{hint}")
        return None


def _robust(func_name: str, key: str, fn, ttl: float = 7 * 86400, timeout: Optional[float] = None):
    """短超时 + 1 次退避重试 + 磁盘最近成功缓存（TTL 内直接用，失败时 30 天内旧值兜底）。

    与 _safe_call 共用熔断：已熔断的源不再打网络，但仍可读缓存（新鲜或过期兜底）。
    """
    from src.data.robust_fetch import robust_call
    _fix_curl_ssl_paths()
    k = _breaker_key(func_name)
    broken = _fast_fail_enabled() and (k in _BROKEN_SOURCES or k in _skip_sources())

    def call():
        if broken:
            raise ConnectionError("source circuit-broken")
        return fn()

    to = timeout if timeout is not None else min(
        DATA_CALL_TIMEOUT, float(os.environ.get("MUYUN_DATA_CALL_TIMEOUT") or 10))
    v = robust_call(func_name, key, call, ttl=ttl, timeout=to, retries=0 if broken else 1,
                    quiet=broken)
    if v is None and _fast_fail_enabled() and not broken:
        _BROKEN_SOURCES.add(k)
    return v


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
    df = _robust("stock_zyjs_ths", code, lambda: ak.stock_zyjs_ths(symbol=code))
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
    df2 = _robust("stock_individual_info_em", code, lambda: ak.stock_individual_info_em(symbol=code))
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

    # 试 3：新浪公司简介页（主营业务/公司简介，真实页面文本）
    from src.data import robust_fetch as _rf
    info = _robust("sina_corp_info", code, lambda: _rf.sina_corp_info(code))
    if info:
        return "\n".join(f"{k}: {v}" for k, v in info.items()) + "\n(来源: 新浪财经公司简介)"

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
        # 修复：backup 是 DataFrame，'if backup:' 在空 DataFrame 上抛
        # 'truth value of a DataFrame is ambiguous'（v0.8.6.5 起存在，
        # 主源有数据时走不到此分支，凌晨主源空+巨潮也空时暴露）
        if backup is not None and not backup.empty:
            items = _parse_news_df(backup, cutoff)
            for it in items:
                # F3（plan/fusion P1-3 修复）：巨潮官方公告打源标——research_snapshot
                # 的 official_date 判定（"巨潮"子串）依赖它；巨潮 DataFrame 无来源列，
                # 不打标则官方公告通道恒空、严格 PIT 快照收不到公告。
                # 显示侧更信息量（此前为空串），无消费方依赖空值（grep 核对）。
                orig = it.get("source") or ""
                it["source"] = "巨潮公告" + (f"｜{orig}" if orig else "")

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
            from src.data.akshare_client import _call_with_timeout
            rs = bs.query_stock_industry(code=f"{prefix}.{code}")
            if rs.error_code == '0':
                # v0.8.9.5（彻查批 P1-2）：bs.next() 读取包线程级硬超时（ISS-047 同类点）
                def _read_rows():
                    rws = []
                    while rs.next():
                        rws.append(rs.get_row_data())
                    return rws

                rows = _call_with_timeout(_read_rows, timeout=15)
                if rows:
                    f = rs.fields
                    industry = rows[0][f.index('industry')]
                    stock_name = rows[0][f.index('code_name')]
                    from src.data import robust_fetch as _rf
                    q = _robust("tencent_quote_fundamentals", code,
                                lambda: _rf.tencent_quote_fundamentals(code), ttl=86400) or {}
                    fmt = (lambda v: f"{v}亿" if v is not None else "")
                    return {
                        "industry_name": industry,
                        "stock_name": stock_name,
                        "total_market_cap": fmt(q.get("total_mcap_yi")),
                        "circulating_market_cap": fmt(q.get("circ_mcap_yi")),
                        "pe_ttm": q.get("pe_ttm"),
                        "pb": q.get("pb"),
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

    df = _robust("stock_individual_info_em", code, lambda: ak.stock_individual_info_em(symbol=code))
    if df is None or df.empty:
        return _industry_info_alt(code)

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


def _industry_info_alt(code: str) -> Optional[dict]:
    """替代源：新浪申万行业/概念 + 腾讯 PE/PB/市值。全部取不到返回 None（不编造）。"""
    from src.data import robust_fetch as _rf
    ind = _robust("sina_industry_concepts", code, lambda: _rf.sina_industry_concepts(code)) or {}
    q = _robust("tencent_quote_fundamentals", code, lambda: _rf.tencent_quote_fundamentals(code),
                ttl=86400) or {}
    if not ind.get("industry") and not q:
        return None
    concepts = [c for c in (ind.get("concepts") or []) if c != ind.get("industry")]
    fmt = (lambda v: f"{v}亿" if v is not None else "")
    return {
        "industry_name": ind.get("industry", ""),
        "stock_name": q.get("stock_name", ""),
        "total_market_cap": fmt(q.get("total_mcap_yi")),
        "circulating_market_cap": fmt(q.get("circ_mcap_yi")),
        "pe_ttm": q.get("pe_ttm"),
        "pb": q.get("pb"),
        "concepts": concepts,
        "industry_raw": {"source": "sina_sw_industry+tencent_qt", **ind, **q},
    }


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
        df = _safe_call("market_cache.get_all_stocks", mc.get_all_stocks, timeout=45) # 45s: 内部含3次重试+备用源，20s会误杀慢网下的合法拉取
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


def get_industry_metrics(code: str, industry_name: str = "") -> tuple:
    """ISS-083 景气度接线（v0.8.8.7）：客观行业数据摘要，供 industry_prosperity 维 prompt。

    三级桥接（**只用手写链**——自举链未经人工复核，不参与评分）：
      L1 手写链代表公司代码直配（最强信号，第0步探针实测命中主力，56%）
      L2 链名/别名匹配——**只在手写链子集内匹配**（监督审查 P2-4：全量匹配后
         否决会让 auto 链别名遮蔽 manual 次长匹配）
      L3 COMMODITY_MAP 商品关键词（13 类：生猪/钢铁/有色/化工…）
    需求段：链 demand_data 绑定（缓存键与 chat 路径共享）；宏观 PMI/PPI 通用底色。
    三节**并行**拉取（监督审查 P1-1：串行最坏 200-275s 必穿聚合上限；并行后
    最坏=max≈150s），全部复用 industry_data 的缓存/超时/降级文本。

    Returns:
        (metrics|None, status)：status ∈ "matched"（命中）/"not_matched"（设计内
        未命中，非故障）/ "failed"（拉取失败）。未命中与失败都返回 None metrics，
        由 status 区分——get_data_summary 据此记三态 fetch_status（未命中不该
        被告警成「数据源失败」，P2-1）。
    回测隔离：回测引擎显式构造 data_summary（A03 禁网修复），本函数只在 live
    get_data_summary 被调——industry_data 接口全是"今天"锚定，进回测即前瞻。
    """
    from src.data import industry_data as ind

    class _BridgeMiss(Exception):
        """三级桥接全部未命中（设计内状态，非故障）。"""

    def _safe_section(key: str, fn):
        """节级兜底：industry_data._cached 理论上不抛（异常转缺失文本），
        这里再兜一层防未知 bug 拖垮整包——坏节转缺失标记，好节照常存活。"""
        try:
            return fn()
        except Exception as e:
            return f"[数据缺失]（{key} 异常: {type(e).__name__}）"

    def _pull() -> dict:
        chains = ind.load_chains()
        manual_chains = {n: c for n, c in chains.items()
                         if ind._graph_sources.get(n) == "manual"}
        chain_name, chain_cfg = None, None
        matched_via = None
        # L1：手写链代表公司代码直配
        for n, cfg in manual_chains.items():
            if code in ind._company_codes(cfg):
                chain_name, chain_cfg = n, cfg
                matched_via = "company_code"
                break
        # L2：链名/别名（仅手写链子集）
        if chain_name is None:
            h = ind.match_chain(industry_name or "", chains=manual_chains)
            if h:
                chain_name, chain_cfg = h
                matched_via = "chain_name"

        if chain_name is not None:
            logger.info(f"industry_metrics 命中 {code}: 链[{chain_name}] via {matched_via}")
            # 三节并行（T05-D：daemon 线程共用总预算，超时放弃后不拖住解释器退出）
            commodity_text, demand_text, macro_text = run_parallel_with_deadline(
                [lambda: _safe_section("commodity", lambda: ind.get_commodity_section(chain_cfg)),
                 lambda: _safe_section("demand", lambda: ind.get_demand_section(chain_cfg)),
                 lambda: _safe_section("macro", ind.format_macro)],
                max_workers=3, timeout=INDUSTRY_SECTION_TIMEOUT_S)
            return {
                "chain": chain_name,
                "matched_via": matched_via,
                "commodity": commodity_text,
                "demand": demand_text,
                "macro": macro_text,
            }

        # L3：COMMODITY_MAP 商品关键词映射（无需求绑定，双节并行）
        cm = ind.match_commodity(industry_name or "")
        if not cm:
            raise _BridgeMiss()
        logger.info(f"industry_metrics 命中 {code}: 商品[{cm[0]}] via commodity_keyword")
        commodity_text, macro_text = run_parallel_with_deadline(
            [lambda: _safe_section("commodity",
                                   lambda: ind.get_generic_commodity_section(industry_name or "")),
             lambda: _safe_section("macro", ind.format_macro)],
            max_workers=2, timeout=INDUSTRY_SECTION_TIMEOUT_S)
        return {
            "chain": f"商品:{cm[0]}",
            "matched_via": "commodity_keyword",
            "commodity": commodity_text,
            "demand": "",
            "macro": macro_text,
        }
    try:
        result = call_with_timeout(_pull, timeout=INDUSTRY_METRICS_TIMEOUT_S)
    except _BridgeMiss:
        return None, "not_matched"
    except TimeoutError:
        logger.warning(
            "industry_metrics 超时(>150s)，本次放弃走降级"
            "（已完成的节已入 industry_data 缓存，下次调用更快）")
        return None, "failed"
    except Exception as e:
        logger.warning(f"industry_metrics 拉取失败: {type(e).__name__}: {str(e)[:80]}")
        return None, "failed"
    return result, "matched"


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

    # ISS-083：客观行业数据摘要（商品锚/需求/宏观），只在手写链/商品映射命中时非 None。
    # 回测路径显式构造 data_summary 不经过本函数——结构性隔离（禁网+防前瞻）。
    # 三态 fetch_status（监督审查 P2-1）：True=命中 / "not_matched"=设计内未命中
    # （truthy，不触发「数据源失败」告警）/ False=拉取失败（告警）。
    industry_name = (summary["industry"] or {}).get("industry_name", "")
    summary["industry_metrics"], metrics_status = get_industry_metrics(code, industry_name)
    summary["fetch_status"]["industry_metrics"] = (
        True if metrics_status == "matched"
        else ("not_matched" if metrics_status == "not_matched" else False))

    summary["kline"] = get_recent_kline(code)
    summary["fetch_status"]["kline"] = summary["kline"] is not None and not summary["kline"].empty if summary["kline"] is not None else False

    if market_turnover is not None:
        summary["market_turnover"] = market_turnover
        summary["fetch_status"]["market_turnover"] = True
    else:
        summary["market_turnover"] = get_market_turnover()
        summary["fetch_status"]["market_turnover"] = summary["market_turnover"] is not None

    success_count = sum(1 for v in summary["fetch_status"].values() if v)
    logger.info(f"data_provider 数据拉取 {code}: {success_count}/6 成功")

    return summary
