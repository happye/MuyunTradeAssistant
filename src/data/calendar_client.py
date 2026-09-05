"""预期事件日历数据源客户端（v0.8.7 预期管理 Phase 1）

数据源：
1. AKShare bond_zh_us_rate(start_date) - 中美国债收益率历史（东方财富，含中国10年期）
2. AKShare stock_report_disclosure(market, period) - 财报预约披露日历（巨潮资讯）
3. Baostock sh.000300 日K - 沪深300历史 close（point-in-time 安全）

缓存机制（TTL 差异化）：
- 国债收益率：12h（盘中不拉，收盘后拉一次）
- 沪深300日K：1天
- 财报披露日历：披露季中 6h，披露季外 24h

设计原则（仿 NewsClient + ISS-047 三层硬保护）：
- 所有 akshare/baostock 调用走 _safe_call（src/core/benzong/data_provider.py，线程级硬超时 30s）
- 失败返回 None / 空 list，不抛异常
- 不编造数据：拿不到就返回 None，让上层标"数据失效"
"""

import time
import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _is_disclosure_season() -> bool:
    """判断当前是否在强制披露月（4月年报/8月中报/10月三季报）"""
    return datetime.now().month in (4, 8, 10)


def current_disclosure_periods() -> list[str]:
    """返回当前最该关注的 1-2 个财报 period（akshare stock_report_disclosure 格式）。

    period 格式："{year}一季" / "{year}半年报" / "{year}三季" / "{year}年报"
    逻辑：优先返回"正在披露/即将披露"的最近一期；若临近下一披露窗口则也返回下一期。
    """
    now = datetime.now()
    y = now.year
    m = now.month
    # 各报告期的强制披露窗口：一季报4月、半年报8月、三季报10月、年报次年4月
    if m <= 4:
        return [f"{y-1}年报", f"{y}一季"]
    if m <= 8:
        return [f"{y}半年报"]
    if m <= 10:
        return [f"{y}三季", f"{y}半年报"]
    return [f"{y}三季", f"{y}年报"]


class CalendarClient:
    """预期事件日历数据源客户端"""

    # 内存缓存：key -> (timestamp, data)
    _bond_rate_cache: tuple[float, Optional[object]] = (0.0, None)
    _index_history_cache: tuple[float, Optional[list]] = (0.0, None)
    _disclosure_cache: dict[str, tuple[float, list]] = {}

    _BOND_TTL = 12 * 3600
    _INDEX_TTL = 24 * 3600
    _DISCLOSURE_TTL_ON = 6 * 3600   # 披露季中
    _DISCLOSURE_TTL_OFF = 24 * 3600  # 披露季外

    @classmethod
    def get_bond_yield_history(cls, years: int = 3) -> Optional[object]:
        """获取中美国债收益率历史 DataFrame（含中国10年期列）。

        Returns: DataFrame 或 None（失败）。列含日期 + 各期限收益率。
        """
        now = time.time()
        cached_ts, cached = cls._bond_rate_cache
        if cached is not None and (now - cached_ts) < cls._BOND_TTL:
            return cached
        from src.core.benzong.data_provider import _safe_call

        start = (datetime.now() - timedelta(days=years * 365 + 30)).strftime("%Y%m%d")

        def _fetch():
            import akshare as ak
            return ak.bond_zh_us_rate(start_date=start)

        df = _safe_call("calendar.bond_yield", _fetch, timeout=30)
        if df is None or df.empty:
            return None
        cls._bond_rate_cache = (now, df)
        return df

    @classmethod
    def get_cn_10y_rate_series(cls, years: int = 3) -> Optional[list[float]]:
        """提取中国10年期国债收益率序列（最新在末尾）。

        列名匹配：含"中国"和"10年"且不含"-"（排除"10年-2年"期限利差列）。
        Returns: 收益率序列（旧->新）或 None。
        """
        df = cls.get_bond_yield_history(years=years)
        if df is None:
            return None
        rate_col = None
        for c in df.columns:
            if "中国" in c and "10年" in c and "-" not in c:
                rate_col = c
                break
        if rate_col is None:
            logger.warning("bond_zh_us_rate 未找到中国10年国债列，实际列: %s", list(df.columns))
            return None
        series = df[rate_col].dropna().tolist()
        return series if series else None

    @classmethod
    def get_index_close_history(cls, symbol: str = "sh.000300", years: int = 3) -> Optional[list[float]]:
        """获取指数日K close 历史序列（baostock，point-in-time 安全）。

        Args:
            symbol: baostock 指数代码，默认 sh.000300 沪深300
            years: 回溯年数
        Returns: close 序列（旧->新）或 None（失败）。
        """
        now = time.time()
        cached_ts, cached = cls._index_history_cache
        if cached is not None and (now - cached_ts) < cls._INDEX_TTL:
            return cached
        from src.core.benzong.data_provider import _safe_call

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=years * 365 + 30)).strftime("%Y-%m-%d")

        def _fetch():
            from src.data.akshare_client import _ensure_baostock_login
            import baostock as bs
            if not _ensure_baostock_login():
                return None
            rs = bs.query_history_k_data_plus(
                symbol, "date,close",
                start_date=start, end_date=end,
                frequency="d", adjustflag="3",
            )
            if rs.error_code != "0":
                logger.warning("baostock 指数查询失败: %s %s", rs.error_code, rs.error_msg)
                return None
            closes = []
            while rs.next():
                val = rs.get_row_data()[1]
                try:
                    closes.append(float(val))
                except (ValueError, TypeError):
                    continue
            return closes

        closes = _safe_call("calendar.index_history", _fetch, timeout=30)
        if not closes:
            return None
        cls._index_history_cache = (now, closes)
        return closes

    @classmethod
    def get_stock_disclosure(cls, period: str, market: str = "沪深京") -> list[dict]:
        """获取财报预约披露日历（巨潮资讯，akshare 动态源）。

        Args:
            period: "{year}一季"/"{year}半年报"/"{year}三季"/"{year}年报"
            market: "沪深京"/"深市"/"沪市" 等，默认"沪深京"
        Returns: [{code, name, disclose_date}, ...] 或空 list（失败）。
        """
        now = time.time()
        cache_key = f"{market}:{period}"
        if cache_key in cls._disclosure_cache:
            cached_ts, cached = cls._disclosure_cache[cache_key]
            ttl = cls._DISCLOSURE_TTL_ON if _is_disclosure_season() else cls._DISCLOSURE_TTL_OFF
            if (now - cached_ts) < ttl:
                return cached
        from src.core.benzong.data_provider import _safe_call

        def _fetch():
            import akshare as ak
            try:
                return ak.stock_report_disclosure(market=market, period=period)
            except ValueError as e:
                # ISS-079：巨潮对"预约披露表尚未发布"的报告期返回空列表，akshare
                # 对空数据直接 temp_df.columns=[10列] 崩 Length mismatch（实测
                # 2026-09 的 2026三季；每年预约表未发布的强制披露期同窗口复发）。
                # "该期间无预约披露表"是合法业务空态而非故障——转空 DataFrame
                # 走正常解析入缓存，不再每次 expect 重复打接口+刷英文告警。
                # 仅吞 Length mismatch 签名，其余异常原样上抛给 _safe_call 告警。
                if "Length mismatch" in str(e):
                    logger.info(f"财报预约披露表 {period} 尚未发布（巨潮返回空），按无事件处理")
                    import pandas as pd
                    return pd.DataFrame()
                raise

        df = _safe_call("calendar.disclosure", _fetch, timeout=30)
        if df is None:
            # 真故障（超时/网络/接口异常）：不缓存，下次 expect 重试
            return []
        if df.empty:
            # ISS-079：业务空态（如预约披露表尚未发布的期间）入缓存——原早退
            # 绕过缓存写入，每次 expect 都重复打接口
            cls._disclosure_cache[cache_key] = (now, [])
            return []
        events = []
        for _, row in df.iterrows():
            code = str(row.get("股票代码", ""))
            name = str(row.get("股票简称", ""))
            # 披露日期取最新预约/实际披露：实际披露 > 三次变更 > 二次变更 > 初次变更 > 首次预约
            disc_date = ""
            for col in ["实际披露", "三次变更", "二次变更", "初次变更", "首次预约"]:
                val = row.get(col)
                s = str(val) if val is not None else ""
                if s and s not in ("nan", "NaT", "None"):
                    disc_date = s
                    break
            if code and disc_date:
                events.append({"code": code, "name": name, "disclose_date": disc_date[:10]})
        cls._disclosure_cache[cache_key] = (now, events)
        return events

    @classmethod
    def clear_cache(cls):
        """清空所有缓存（改逻辑后调试用）"""
        cls._bond_rate_cache = (0.0, None)
        cls._index_history_cache = (0.0, None)
        cls._disclosure_cache.clear()
