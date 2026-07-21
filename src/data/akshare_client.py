"""AKShare数据获取模块 - Phase 2

支持多数据源：
1. AKShare (东方财富/新浪) - 实时行情
2. Baostock - 历史K线备用
"""

import pandas as pd
import akshare as ak
import baostock as bs
from datetime import datetime, timedelta
from typing import Optional
import logging
import time
import random

from src.data.models import StockData

logger = logging.getLogger(__name__)

# 模块初始化时登录baostock
_bs_login_status = None

# v0.8.6.4：baostock socket 读取可能卡住，统一用线程级超时保护
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

DEFAULT_TIMEOUT = 30.0


def _call_with_timeout(fn, *args, timeout: float = DEFAULT_TIMEOUT, **kwargs):
    """线程级硬超时包装：fn 卡住超过 timeout 秒则放弃，抛 TimeoutError。
    akshare/baostock 底层无 timeout，靠这个防 WinError 10060 冻结。
    """
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn, *args, **kwargs)
        return fut.result(timeout=timeout)


def _ensure_baostock_login():
    """确保baostock已登录（模块级别全局状态）

    v0.8.6.5：心跳重连——连接闲置被服务端断开后，下次查询用死连接会报错，
    现已登录状态先做轻量心跳(query_stock_basic)，失败则 logout+重 login。
    注：baostock 库的 print(login success/you don't login)是它直接 print 的，
    走 logging 静音不了，但无害，保留正常输出(不重定向stdout，避免改过头)。
    """
    global _bs_login_status
    if _bs_login_status:
        if not _baostock_ping():
            logger.info("Baostock 连接已断开(闲置超时)，重新登录")
            _baostock_logout()
            _bs_login_status = False
        else:
            return True

    try:
        lg = bs.login()
        _bs_login_status = lg.error_code == '0'
        if _bs_login_status:
            logger.info("Baostock登录成功")
        else:
            logger.warning(f"Baostock登录失败: {lg.error_msg}")
    except Exception as e:
        logger.warning(f"Baostock登录异常: {e}")
        _bs_login_status = False
    return _bs_login_status


def _baostock_ping() -> bool:
    """轻量心跳：query_stock_basic 一只常见股，验证连接活着。失败返回 False。"""
    try:
        def _q():
            return bs.query_stock_basic(code="sh.600000")
        rs = _call_with_timeout(_q, timeout=8)
        return rs is not None and rs.error_code == '0'
    except Exception:
        return False


def _baostock_logout():
    """登出baostock并重置全局登录状态"""
    global _bs_login_status
    try:
        bs.logout()
    except Exception:
        pass
    _bs_login_status = False


class AKShareClient:
    """AKShare金融数据客户端"""

    @staticmethod
    def _get_random_ua():
        """生成随机User-Agent降低被识别风险"""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
        return random.choice(user_agents)

    @staticmethod
    def _retry_with_backoff(func, *args, max_retries=3, base_delay=2, timeout=30.0, **kwargs):
        """带指数退避的重试装饰器 + 线程级硬超时。

        遇到网络不可恢复错误（ProxyError/RemoteDisconnected/ConnectionError）时
        直接跳过不重试，避免浪费时间等待注定失败的请求。

        v0.8.6.4（ISS-047）：加 timeout 线程级硬超时。akshare 底层 requests 默认
        无 timeout，接口卡住会挂起到 OS 报 WinError 10060/10054 冻结整个命令。
        用 ThreadPoolExecutor 包住单次调用，超时放弃走重试/降级。

        Args:
            func: 要重试的函数
            *args: 函数参数
            max_retries: 最大重试次数
            base_delay: 基础延迟秒数
            timeout: 单次调用硬超时秒数（默认 30s）
            **kwargs: 函数关键字参数

        Returns:
            函数返回值或None
        """
        from concurrent.futures import TimeoutError as FuturesTimeout
        # 不可恢复的错误关键词，遇到直接放弃
        SKIP_RETRY_KEYWORDS = ['ProxyError', 'RemoteDisconnected', 'ConnectionReset',
                                'ConnectTimeout', 'Max retries exceeded']

        for attempt in range(max_retries):
            try:
                return _call_with_timeout(func, *args, timeout=timeout, **kwargs)
            except FuturesTimeout:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0.5, 1.5)
                    logger.warning(f"{func.__name__} 超时(>{timeout:.0f}s) (尝试 {attempt + 1}/{max_retries}), {delay:.1f}秒后重试...")
                    time.sleep(delay)
                else:
                    logger.error(f"{func.__name__} 超时最终失败(>{timeout:.0f}s)")
                    return None
            except Exception as e:
                error_str = str(e)
                # 不可恢复错误，直接放弃
                if any(kw in error_str for kw in SKIP_RETRY_KEYWORDS):
                    logger.warning(f"{func.__name__} 网络不可恢复错误，跳过重试: {type(e).__name__}")
                    return None

                if attempt < max_retries - 1:
                    # 指数退避 + 随机抖动
                    delay = base_delay * (2 ** attempt) + random.uniform(0.5, 1.5)
                    logger.warning(f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}, {delay:.1f}秒后重试...")
                    time.sleep(delay)
                else:
                    logger.error(f"{func.__name__} 最终失败: {e}")
                    return None
        return None

    @classmethod
    def _normalize_stock_code(cls, code: str) -> tuple[str, str]:
        """标准化股票代码，返回(市场前缀, 纯代码)

        例如:
        - 600519 -> (sh, 600519)
        - 000001 -> (sz, 000001)
        - 300750 -> (sz, 300750)
        - 159201 -> (sz, 159201)  深圳ETF
        - 510300 -> (sh, 510300)  上海ETF
        - 560100 -> (sh, 560100)  上海ETF
        - 920001 -> (bj, 920001)  北交所
        - 830001 -> (bj, 830001)  北交所
        """
        code = code.strip().zfill(6)
        if code.startswith('920') or code.startswith('8'):
            # 北交所：920xxx（新代码段）或 8xxxxx（老代码段）
            return "bj", code
        elif code.startswith('6'):
            return "sh", code
        elif code.startswith('9'):
            # 900xxx 上海B股（非北交所的9开头代码）
            return "sh", code
        elif code.startswith(('0', '3')):
            return "sz", code
        elif code.startswith('15'):
            # 159xxx / 150xxx 深圳ETF/LOF
            return "sz", code
        elif code.startswith(('51', '52', '56', '58')):
            # 510xxx/511xxx/512xxx/513xxx/515xxx/516xxx/518xxx 上海ETF
            # 520xxx/560xxx/580xxx 上海ETF/LOF
            return "sh", code
        else:
            return "sh", code

    @classmethod
    def get_realtime_quote(cls, stock_code: str, retry: int = 1) -> Optional[dict]:
        """获取实时行情（单只股票）- 使用多级备用策略

        优先级：Baostock（已确认可用）> 东方财富/新浪（可能被封）

        Args:
            stock_code: 股票代码
            retry: 每个数据源的重试次数（默认1次，快速失败）

        Returns:
            dict: 实时行情数据，包含价格、涨跌、成交量等
        """
        prefix, code = cls._normalize_stock_code(stock_code)

        # ETF/指数代码检测：AKShare的stock_zh_a_spot_em()只覆盖A股股票，
        # 不包含ETF(15xx/51xx)和指数(000xxx)，跳过避免浪费时间
        is_etf_or_index = code.startswith(('15', '51', '56'))

        # --- 策略1：Baostock（优先，因为本机已验证可用）---
        try:
            bs_quote = cls._fetch_baostock_realtime(stock_code)
            if bs_quote:
                logger.info(f"Baostock实时行情成功 {stock_code}")
                return bs_quote
        except Exception as e:
            logger.warning(f"Baostock实时行情失败 {stock_code}: {e}")

        # --- 策略2：ETF专用API（东方财富基金ETF接口）---
        if is_etf_or_index:
            logger.info(f"{stock_code} 是ETF代码，尝试AKShare ETF专用接口")
            try:
                etf_df = cls._retry_with_backoff(ak.fund_etf_spot_em, max_retries=retry, base_delay=3)
                if etf_df is not None and not etf_df.empty:
                    code_col = None
                    for col in ['代码', 'code', 'symbol']:
                        if col in etf_df.columns:
                            code_col = col
                            break
                    if code_col:
                        row = etf_df[etf_df[code_col] == code]
                        if not row.empty:
                            data = row.iloc[0].to_dict()
                            name_col = next((c for c in ['名称', 'name'] if c in data), None)
                            price_col = next((c for c in ['最新价', 'price'] if c in data), None)
                            change_col = next((c for c in ['涨跌幅', 'change'] if c in data), None)
                            volume_col = next((c for c in ['成交量', 'volume'] if c in data), None)
                            return {
                                "stock_code": code,
                                "stock_name": data.get(name_col, code) if name_col else code,
                                "price": float(data.get(price_col, 0)) if price_col else 0,
                                "change_pct": float(data.get(change_col, 0)) if change_col else 0,
                                "volume": int(float(data.get(volume_col, 0))) if volume_col else 0,
                                "open": float(data.get('今开', 0)),
                                "high": float(data.get('最高', 0)),
                                "low": float(data.get('最低', 0)),
                                "close_yesterday": float(data.get('昨收', 0)),
                            }
                    logger.info(f"ETF接口未找到 {stock_code} 的行情数据")
                else:
                    logger.warning(f"ETF实时行情接口返回空数据")
            except Exception as e:
                logger.warning(f"ETF专用接口失败 {stock_code}: {e}")
            return None

        # --- 策略3：A股股票接口（东方财富全市场）---
        # 注意：stock_zh_a_spot()（新浪分页爬虫）已移除，69页×1秒=太慢
        fetchers = []

        # 东方财富全市场（单次API调用覆盖全市场，失败快）
        def _fetch_em_all():
            # 禁用AKShare内部的tqdm进度条，避免在CLI中产生混淆
            import os
            os.environ["TQDM_DISABLE"] = "1"
            try:
                return ak.stock_zh_a_spot_em()
            finally:
                os.environ.pop("TQDM_DISABLE", None)
        fetchers.append(("东方财富全市场", _fetch_em_all))

        last_error = None
        for name, fetcher in fetchers:
            try:
                df = cls._retry_with_backoff(fetcher, max_retries=retry, base_delay=3)
                if df is None or df.empty:
                    continue

                # 在结果中查找目标股票
                # 注意：不同接口的代码列名可能不同
                code_col = None
                for col in ['代码', 'code', 'symbol', '代码']:
                    if col in df.columns:
                        code_col = col
                        break

                if code_col is None:
                    continue

                row = df[df[code_col] == code]
                if row.empty:
                    continue

                data = row.iloc[0].to_dict()

                # 尝试找到正确的列名（不同接口返回的列名不同）
                name_col = None
                for col in ['名称', 'name', '股票名称']:
                    if col in data:
                        name_col = col
                        break

                price_col = None
                for col in ['最新价', 'price', '当前价']:
                    if col in data:
                        price_col = col
                        break

                change_col = None
                for col in ['涨跌幅', '涨跌额', 'change']:
                    if col in data:
                        change_col = col
                        break

                volume_col = None
                for col in ['成交量', 'volume']:
                    if col in data:
                        volume_col = col
                        break

                return {
                    "stock_code": code,
                    "stock_name": data.get(name_col, code) if name_col else code,
                    "price": float(data.get(price_col, 0)) if price_col else 0,
                    "change_pct": float(data.get(change_col, 0)) if change_col else 0,
                    "volume": int(data.get(volume_col, 0)) if volume_col else 0,
                    "open": float(data.get('今开', data.get('开盘', 0))),
                    "high": float(data.get('最高', data.get('high', 0))),
                    "low": float(data.get('最低', data.get('low', 0))),
                    "close_yesterday": float(data.get('昨收', data.get('close', 0))),
                }
            except Exception as e:
                last_error = e
                logger.warning(f"数据源 [{name}] 获取失败: {e}")
                continue

        # E1-B5：所有源都失败时，根据错误类型给出可操作建议
        err_str = str(last_error) if last_error else ""
        hint = ""
        if "ProxyError" in err_str or "10057" in err_str or "代理" in err_str:
            hint = " | 提示：可能是系统代理(127.0.0.1:7890)干扰金融 API，可临时关闭代理或重试"
        elif "Timeout" in err_str or "timeout" in err_str:
            hint = " | 提示：网络超时，可稍后重试或检查网络稳定性"
        elif "登录失败" in err_str or "login" in err_str.lower():
            hint = " | 提示：BaoStock 登录失败，可能是其服务端临时故障，等几分钟重试"
        elif last_error is None:
            hint = " | 提示：所有数据源静默返回失败，可能是非交易时段或股票代码无效"
        logger.error(f"所有实时行情接口均失败 {stock_code}, 最后错误: {last_error}{hint}")
        return None

    @classmethod
    def get_historical_kline(
        cls,
        stock_code: str,
        period: str = "daily",
        adjust: str = "qfq",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        retry: int = 3
    ):
        """获取历史K线数据（多数据源备用）

        优先使用AKShare，失败后自动切换到Baostock

        Args:
            stock_code: 股票代码（如 600519）
            period: K线周期 daily/weekly/monthly
            adjust: 复权类型 qfq(前复权)/hfq(后复权)/None
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            retry: 最大重试次数

        Returns:
            DataFrame: K线数据或None
        """
        prefix, code = cls._normalize_stock_code(stock_code)

        # 默认获取最近120个交易日的数据
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

        period_map = {
            "daily": "daily",
            "weekly": "weekly",
            "monthly": "monthly"
        }
        period_value = period_map.get(period, "daily")

        # 尝试Baostock（已验证可用，优先）
        # 转换日期格式：AKShare用YYYYMMDD，Baostock用YYYY-MM-DD
        bs_start = cls._format_date_for_baostock(start_date)
        bs_end = cls._format_date_for_baostock(end_date)
        df = cls._fetch_baostock_kline(stock_code, period, bs_start, bs_end)

        if df is not None and not df.empty:
            logger.info(f"Baostock历史K线成功 {stock_code}，获取 {len(df)} 行")
            return df

        # 如果Baostock失败，尝试AKShare
        # ETF用专用基金ETF接口，A股用stock_zh_a_hist
        is_etf_or_index = code.startswith(('15', '51', '56'))

        if is_etf_or_index:
            # ETF专用历史K线接口
            logger.info(f"{stock_code} 是ETF代码，尝试AKShare ETF历史K线接口")
            try:
                def _fetch_etf_hist():
                    return ak.fund_etf_hist_em(
                        symbol=code,
                        period=period_value,
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust
                    )
                etf_df = cls._retry_with_backoff(_fetch_etf_hist, max_retries=1, base_delay=3)
                if etf_df is not None and not etf_df.empty:
                    logger.info(f"AKShare ETF历史K线成功 {stock_code}，获取 {len(etf_df)} 行")
                    return etf_df
            except Exception as e:
                logger.warning(f"ETF历史K线接口失败 {stock_code}: {e}")
            return df  # 返回Baostock的结果（可能为None）

        # A股股票：用stock_zh_a_hist

        def _fetch_hist():
            return ak.stock_zh_a_hist(
                symbol=code,
                period=period_value,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust
            )

        df = cls._retry_with_backoff(_fetch_hist, max_retries=1, base_delay=3)
        if df is None:
            logger.warning(f"AKShare获取失败，尝试Baostock备用 {stock_code}")
            # 转换日期格式：AKShare用YYYYMMDD，Baostock用YYYY-MM-DD
            bs_start = cls._format_date_for_baostock(start_date)
            bs_end = cls._format_date_for_baostock(end_date)
            df = cls._fetch_baostock_kline(stock_code, period, bs_start, bs_end)

        return df

    @classmethod
    def get_stock_basic_name(cls, code: str) -> Optional[str]:
        """baostock query_stock_basic 取 code_name（含 ST/*ST 前缀）。ISS-053 基本面恶化判定用。

        返回**当前** code_name（非历史日期），回测历史有前瞻偏差（见 ISS-053 gap）。
        数据源实测：sz.000005 -> "ST星源"，sh.600519 -> "贵州茅台"。
        失败/超时返回 None（fail-open，不假退出）。
        """
        if not _ensure_baostock_login():
            return None
        try:
            prefix, _code = cls._normalize_stock_code(code)
            bs_code = f"{prefix}.{_code}"

            def _read():
                rs = bs.query_stock_basic(code=bs_code)
                if rs is None or rs.error_code != '0':
                    return None
                name = None
                while rs.next():
                    row = rs.get_row_data()
                    if 'code_name' in rs.fields:
                        name = row[rs.fields.index('code_name')]
                return name
            try:
                return _call_with_timeout(_read, timeout=15)
            except FuturesTimeout:
                logger.warning(f"query_stock_basic 超时 {code}")
                _baostock_logout()
                return None
        except Exception as e:
            logger.warning(f"get_stock_basic_name 异常 {code}: {e}")
            if 'socket' in str(e).lower() or '10038' in str(e):
                _baostock_logout()
            return None

    @classmethod
    def get_latest_forecast(cls, code: str, *, since_date: Optional[str] = None) -> Optional[dict]:
        """baostock query_forecast_report 取最近一条业绩预告。ISS-053 预亏/预减判定用。

        取近 540 天预告，按 profitForcastExpPubDate 倒序取最新；since_date (YYYY-MM-DD)
        给定时滤 pub_date >= since_date（只取建仓后发布的新预告，避假退出，审视点3）。
        全部预告都在 since_date 之前发布时返回 None。
        返回 {type, abstract, pub_date, stat_date} 或 None。fail-open：异常返回 None。
        """
        if not _ensure_baostock_login():
            return None
        try:
            prefix, _code = cls._normalize_stock_code(code)
            bs_code = f"{prefix}.{_code}"
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=540)).strftime("%Y-%m-%d")

            def _read():
                rs = bs.query_forecast_report(code=bs_code, start_date=start, end_date=end)
                if rs is None or rs.error_code != '0':
                    return []
                idx_pub = rs.fields.index('profitForcastExpPubDate')
                idx_stat = rs.fields.index('profitForcastExpStatDate')
                idx_type = rs.fields.index('profitForcastType')
                idx_abs = rs.fields.index('profitForcastAbstract')
                parsed = []
                while rs.next():
                    row = rs.get_row_data()
                    parsed.append({
                        "type": row[idx_type],
                        "abstract": row[idx_abs],
                        "pub_date": row[idx_pub],
                        "stat_date": row[idx_stat],
                    })
                return parsed
            try:
                parsed = _call_with_timeout(_read, timeout=30)
            except FuturesTimeout:
                logger.warning(f"query_forecast_report 超时 {code}")
                _baostock_logout()
                return None
            if not parsed:
                return None
            # 按 pub_date 倒序取最新；since_date 给定时跳过建仓前发布（已定价，避假退出）
            for fc in sorted(parsed, key=lambda d: d.get("pub_date") or "", reverse=True):
                pub = fc.get("pub_date")
                if since_date and pub and pub < since_date:
                    continue
                return fc
            return None  # 全部预告在建仓前发布
        except Exception as e:
            logger.warning(f"get_latest_forecast 异常 {code}: {e}")
            if 'socket' in str(e).lower() or '10038' in str(e):
                _baostock_logout()
            return None

    @classmethod
    def _fetch_baostock_realtime(cls, stock_code: str) -> Optional[dict]:
        """使用Baostock获取最新行情（当日最近交易日的收盘/最高/最低/成交量）

        注意：Baostock不是真正的实时行情，但能获取最近交易日的K线数据，
        包含开盘、收盘、最高、最低、成交量，比完全无数据好。

        Args:
            stock_code: 股票代码

        Returns:
            dict: 简化版实时行情数据
        """
        prefix, code = cls._normalize_stock_code(stock_code)
        bs_code = f"{prefix}.{code}"

        try:
            if not _ensure_baostock_login():
                return None

            # 获取最近5天的日K线，取最后一天作为"实时"数据
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,code,open,high,low,close,preclose,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="d"
            )

            if rs.error_code != '0':
                logger.warning(f"Baostock实时行情查询失败 {stock_code}: {rs.error_msg}")
                return None

            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())

            if not data_list:
                return None

            # 取最近一条（最后一行）
            latest = data_list[-1]
            fields = rs.fields  # ['date', 'code', 'open', 'high', 'low', 'close', 'preclose', 'volume', 'amount']

            close_val = float(latest[fields.index('close')])
            preclose_val = float(latest[fields.index('preclose')])
            change_pct_val = 0.0
            if preclose_val != 0:
                change_pct_val = round((close_val - preclose_val) / preclose_val * 100, 2)

            return {
                "stock_code": code,
                "stock_name": code,  # Baostock日K线不含名称，由上层用候选股数据补充
                "price": close_val,
                "open": float(latest[fields.index('open')]),
                "high": float(latest[fields.index('high')]),
                "low": float(latest[fields.index('low')]),
                "close_yesterday": preclose_val,
                "volume": int(float(latest[fields.index('volume')])),
                "change_pct": change_pct_val,
                "source": "baostock"
            }

        except Exception as e:
            logger.warning(f"_fetch_baostock_realtime异常 {stock_code}: {e}")
            # socket错误时重置连接状态，下次_ensure_baostock_login会重新连接
            if '10038' in str(e) or 'socket' in str(e).lower():
                logger.warning("检测到socket异常，重置Baostock连接")
                _baostock_logout()
            return None

    @classmethod
    def _fetch_baostock_kline(
        cls,
        stock_code: str,
        period: str = "daily",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """使用Baostock获取历史K线（备用数据源）

        Args:
            stock_code: 股票代码
            period: K线周期
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame: 标准化格式的K线数据
        """
        if not _ensure_baostock_login():
            return None

        try:
            prefix, code = cls._normalize_stock_code(stock_code)
            bs_code = f"{prefix}.{code}"

            # Baostock周期映射
            freq_map = {
                "daily": "d",
                "weekly": "w",
                "monthly": "m"
            }
            freq = freq_map.get(period, "d")

            # preclose仅日线支持，周线/月线使用原字段
            if freq == "d":
                fields = "date,code,open,high,low,close,preclose,volume,amount"
            else:
                fields = "date,code,open,high,low,close,volume,amount"
            rs = bs.query_history_k_data_plus(
                bs_code,
                fields,
                start_date=start_date,
                end_date=end_date,
                frequency=freq
            )

            if rs.error_code != '0':
                logger.error(f"Baostock查询失败: {rs.error_msg}")
                return None

            # v0.8.6.4：bs.next() 读取结果可能 socket 卡住，线程级超时保护
            def _read_all():
                data = []
                while (rs.error_code == '0') & rs.next():
                    data.append(rs.get_row_data())
                return data
            try:
                data_list = _call_with_timeout(_read_all, timeout=30)
            except FuturesTimeout:
                logger.warning(f"Baostock 读取K线超时 {stock_code}，走降级")
                return None

            if not data_list:
                return None

            df = pd.DataFrame(data_list, columns=rs.fields)

            # 标准化列名
            df = df.rename(columns={
                'date': '日期',
                'code': '代码',
                'open': '开盘',
                'high': '最高',
                'low': '最低',
                'close': '收盘',
                'volume': '成交量',
                'amount': '成交额'
            })

            # 转换数据类型（preclose仅日线有，保持英文列名供下游使用）
            for col_name in ['preclose']:
                if col_name in df.columns:
                    df[col_name] = pd.to_numeric(df[col_name], errors='coerce')
            for col in ['开盘', '最高', '最低', '收盘', '成交量', '成交额']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            return df

        except Exception as e:
            logger.error(f"Baostock获取K线异常 {stock_code}: {e}")
            # socket错误时重置连接状态
            if '10038' in str(e) or 'socket' in str(e).lower():
                logger.warning("检测到socket异常，重置Baostock连接")
                _baostock_logout()
            return None

    @staticmethod
    def _baostock_adjust_code(bs_code: str) -> str:
        """Baostock复权代码转换"""
        # Baostock: 3表示后复权，2表示前复权，1表示不复权
        return "3"  # 默认后复权

    @staticmethod
    def _format_date_for_baostock(date_str: Optional[str]) -> Optional[str]:
        """将YYYYMMDD格式转换为YYYY-MM-DD格式供Baostock使用

        Args:
            date_str: YYYYMMDD格式日期字符串

        Returns:
            YYYY-MM-DD格式日期字符串
        """
        if date_str is None:
            return None
        if len(date_str) == 8 and date_str.isdigit():
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return date_str  # 已经是正确格式或非日期字符串

    @classmethod
    def calculate_indicators(cls, stock_code: str, require_historical: bool = False) -> Optional[StockData]:
        """计算完整的技术指标数据（降级模式支持）

        Args:
            stock_code: 股票代码
            require_historical: 是否强制要求历史K线（True时失败返回None，False时仅降级）

        Returns:
            StockData: 包含所有技术指标的股票数据，可能部分指标为None
        """
        # 禁用AKShare内部的tqdm进度条，避免在CLI中产生混淆
        import os as _os
        _os.environ["TQDM_DISABLE"] = "1"

        # 首先尝试获取实时行情
        quote = cls.get_realtime_quote(stock_code)

        # 尝试获取历史K线计算技术指标
        df = None
        weekly_df = None
        monthly_df = None
        try:
            df = cls.get_historical_kline(stock_code, period="daily", adjust="qfq")
        except Exception as e:
            logger.warning(f"获取历史K线异常 {stock_code}: {e}")
        try:
            weekly_df = cls.get_historical_kline(stock_code, period="weekly", adjust="qfq")
        except Exception as e:
            logger.warning(f"获取周线K线异常 {stock_code}: {e}")
        try:
            monthly_df = cls.get_historical_kline(stock_code, period="monthly", adjust="qfq")
        except Exception as e:
            logger.warning(f"获取月线K线异常 {stock_code}: {e}")

        # 如果历史K线获取失败
        if df is None or df.empty:
            if not quote:
                # 实时行情也失败了，完全无法获取数据
                logger.error(f"实时行情和历史K线均获取失败 {stock_code}")
                return None
            else:
                # 有实时行情但没有历史K线，降级返回实时数据
                logger.warning(f"历史K线获取失败 {stock_code}，降级返回实时数据（无技术指标）")
                return StockData(
                    stock_code=quote["stock_code"],
                    stock_name=quote["stock_name"],
                    price=quote["price"],
                    open=quote.get("open"),
                    high=quote.get("high"),
                    low=quote.get("low"),
                    change_pct=quote.get("change_pct"),
                    volume=quote.get("volume"),
                    ma5=None, ma10=None, ma20=None, ma60=None,
                    avg_volume_20=None, high_60d=None, low_60d=None,
                    macd_dif=None, macd_dea=None, macd_hist=None,
                    rsi_6=None, rsi_12=None, rsi_24=None,
                    boll_upper=None, boll_mid=None, boll_lower=None,
                    kdj_k=None, kdj_d=None, kdj_j=None,
                )

        # 有历史K线数据，继续计算技术指标
        try:
            # 确保数据按日期排序
            df = df.sort_values('日期').reset_index(drop=True)

            # 计算均线（AKShare的K线数据不包含均线）
            close = df['收盘'].astype(float)
            df['MA5'] = close.rolling(window=5).mean()
            df['MA10'] = close.rolling(window=10).mean()
            df['MA20'] = close.rolling(window=20).mean()
            df['MA60'] = close.rolling(window=60).mean()

            # 取最近60天的数据
            recent = df.tail(60).copy()
            latest = df.iloc[-1]

            # 使用实时行情数据填充价格信息（更准确），否则使用历史K线数据
            if quote:
                stock_code_val = quote["stock_code"]
                stock_name_val = quote["stock_name"]
                price_val = quote["price"]
                open_val = quote.get("open")
                high_val = quote.get("high")
                low_val = quote.get("low")
                change_pct_val = quote.get("change_pct")
                volume_val = quote.get("volume")
            else:
                # 使用历史K线最后一天的数据
                stock_code_val = stock_code
                stock_name_val = stock_code  # 没有名称
                price_val = float(latest['收盘']) if '收盘' in latest else 0
                open_val = float(latest['开盘']) if '开盘' in latest else None
                high_val = float(latest['最高']) if '最高' in latest else None
                low_val = float(latest['最低']) if '最低' in latest else None
                # 优先使用preclose计算涨跌幅，备选使用涨跌幅字段
                if 'preclose' in latest and pd.notna(latest['preclose']) and float(latest['preclose']) != 0:
                    change_pct_val = round((price_val - float(latest['preclose'])) / float(latest['preclose']) * 100, 2)
                else:
                    change_pct_val = float(latest.get('涨跌幅', 0)) if latest.get('涨跌幅') else 0
                volume_val = int(float(latest['成交量'])) if '成交量' in latest else 0

            stock_data = StockData(
                stock_code=stock_code_val,
                stock_name=stock_name_val,
                price=price_val,
                open=open_val,
                high=high_val,
                low=low_val,
                change_pct=change_pct_val,
                volume=volume_val,
                # 均线计算
                ma5=round(float(latest['MA5']), 2) if pd.notna(latest['MA5']) else None,
                ma10=round(float(latest['MA10']), 2) if pd.notna(latest['MA10']) else None,
                ma20=round(float(latest['MA20']), 2) if pd.notna(latest['MA20']) else None,
                ma60=round(float(latest['MA60']), 2) if pd.notna(latest['MA60']) else None,
                # 成交量
                avg_volume_20=int(recent['成交量'].tail(20).mean()) if len(recent) >= 20 else None,
                # 位置信息
                high_60d=float(recent['最高'].max()) if len(recent) >= 20 else None,
                low_60d=float(recent['最低'].min()) if len(recent) >= 20 else None,
            )

            # MACD计算 (如果数据足够)
            if len(df) >= 26:
                stock_data.macd_dif, stock_data.macd_dea, stock_data.macd_hist = cls._calculate_macd(df)

            # RSI计算
            if len(df) >= 24:
                stock_data.rsi_6, stock_data.rsi_12, stock_data.rsi_24 = cls._calculate_rsi(df)

            # 布林带计算
            if len(df) >= 20:
                stock_data.boll_upper, stock_data.boll_mid, stock_data.boll_lower = cls._calculate_boll(df)

            # KDJ计算
            if len(df) >= 9:
                stock_data.kdj_k, stock_data.kdj_d, stock_data.kdj_j = cls._calculate_kdj(df)

            # 大盘环境数据（沪深300趋势）
            try:
                index_info = cls._get_index_trend()
                if index_info:
                    stock_data.index_trend = index_info["trend"]
                    stock_data.index_ma20 = index_info["ma20"]
                    stock_data.index_ma60 = index_info["ma60"]
                    stock_data.index_ma250 = index_info.get("ma250")
                    stock_data.index_close = index_info["close"]
                    stock_data.index_change_pct = index_info.get("change_pct")
                    stock_data.index_high_250d = index_info.get("high_250d")
                    stock_data.index_change_20d = index_info.get("change_20d")
                    ma250_str = f", MA250={index_info['ma250']:.0f}" if index_info.get('ma250') else ""
                    logger.info(f"大盘趋势: {index_info['trend']} (MA20={index_info['ma20']:.0f}, MA60={index_info['ma60']:.0f}{ma250_str})")
            except Exception as e:
                logger.warning(f"大盘数据获取失败（不影响个股分析）: {e}")

            stock_data.weekly = cls._build_timeframe_snapshot(weekly_df)
            stock_data.monthly = cls._build_timeframe_snapshot(monthly_df)

            # ===== 超跌分析扩展字段（oversold_confirm skill 用）=====
            # 多周期跌幅（个股绝对跌幅）
            if len(df) >= 6:
                stock_data.change_5d = round((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100, 2)
            if len(df) >= 21:
                stock_data.change_20d = round((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21] * 100, 2)
            if len(df) >= 121:
                stock_data.change_120d = round((close.iloc[-1] - close.iloc[-121]) / close.iloc[-121] * 100, 2)
            # 120日高低点（补死字段，声明未填充）
            if len(df) >= 120:
                recent_120 = df.tail(120)
                stock_data.high_120d = float(recent_120['最高'].max())
                stock_data.low_120d = float(recent_120['最低'].min())
            # 近20日收盘序列（企稳/底背离用）
            stock_data.close_series = [round(float(x), 2) for x in close.tail(20).tolist() if pd.notna(x)]
            # 近5日成交量序列（连续日企稳用）
            stock_data.volume_series = [int(float(x)) for x in df['成交量'].tail(5).tolist() if pd.notna(x)]
            # 近20日RSI-6序列（底背离用，单独算避免改_calculate_rsi签名）
            if len(df) >= 24:
                _delta = close.diff()
                _gain = _delta.clip(lower=0).rolling(window=6, min_periods=6).mean()
                _loss = (-_delta.clip(upper=0)).rolling(window=6, min_periods=6).mean()
                _rs = _gain / _loss.replace(0, pd.NA)
                _rsi_series = (100 - (100 / (1 + _rs))).round(2)
                stock_data.rsi_6_series = [float(x) for x in _rsi_series.tail(20).dropna().tolist()]

            # 实控人减持公告（ISS-052 激活 top_signal 数据源）-- live 路径填充。
            # 回测路径 DataFeeder._build_stock_data 不走此函数 -> recent_announcements 缺省 None -> 跳过减持子信号
            # （回测公告非 point-in-time，不可回测，诚实声明）。
            try:
                from src.core.benzong.data_provider import get_recent_announcements
                stock_data.recent_announcements = get_recent_announcements(stock_code_val)
            except Exception as e:
                logger.warning(f"公告获取失败 {stock_code_val}（top_signal 减持子信号降级跳过）: {e}")
                stock_data.recent_announcements = None

            return stock_data

        except Exception as e:
            logger.error(f"计算技术指标失败 {stock_code}: {e}")
            import traceback
            traceback.print_exc()
            # 降级：返回只有实时数据的StockData
            if quote:
                return StockData(
                    stock_code=quote["stock_code"],
                    stock_name=quote["stock_name"],
                    price=quote["price"],
                    open=quote.get("open"),
                    high=quote.get("high"),
                    low=quote.get("low"),
                    change_pct=quote.get("change_pct"),
                    volume=quote.get("volume"),
                    ma5=None, ma10=None, ma20=None, ma60=None,
                    avg_volume_20=None, high_60d=None, low_60d=None,
                    macd_dif=None, macd_dea=None, macd_hist=None,
                    rsi_6=None, rsi_12=None, rsi_24=None,
                    boll_upper=None, boll_mid=None, boll_lower=None,
                    kdj_k=None, kdj_d=None, kdj_j=None,
                )
            else:
                # 完全没有数据
                return None

    @staticmethod
    def _build_timeframe_snapshot(df: Optional[pd.DataFrame]) -> Optional[dict]:
        """将周线/月线K线转换为统一摘要。"""
        if df is None or df.empty:
            return None

        frame = df.copy()
        date_col = '日期' if '日期' in frame.columns else 'date' if 'date' in frame.columns else None
        close_col = '收盘' if '收盘' in frame.columns else 'close' if 'close' in frame.columns else None
        if close_col is None:
            return None
        if date_col is not None:
            frame = frame.sort_values(date_col).reset_index(drop=True)

        close = frame[close_col].astype(float)
        frame['MA5_TF'] = close.rolling(window=5).mean()
        frame['MA10_TF'] = close.rolling(window=10).mean()
        frame['MA20_TF'] = close.rolling(window=20).mean()

        latest = frame.iloc[-1]
        close_val = float(latest[close_col])
        ma5 = round(float(latest['MA5_TF']), 2) if pd.notna(latest['MA5_TF']) else None
        ma10 = round(float(latest['MA10_TF']), 2) if pd.notna(latest['MA10_TF']) else None
        ma20 = round(float(latest['MA20_TF']), 2) if pd.notna(latest['MA20_TF']) else None

        trend = 'NEUTRAL'
        if ma20 is not None:
            if close_val > ma20 and (ma5 is None or ma10 is None or ma5 >= ma10):
                trend = 'BULLISH'
            elif close_val < ma20 and (ma5 is None or ma10 is None or ma5 <= ma10):
                trend = 'BEARISH'

        return {
            'close': round(close_val, 2),
            'ma5': ma5,
            'ma10': ma10,
            'ma20': ma20,
            'trend': trend,
        }

    @staticmethod
    def _calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
        """计算MACD指标

        Args:
            df: 包含收盘价数据的DataFrame
            fast: 快线周期
            slow: 慢线周期
            signal: 信号线周期

        Returns:
            (dif, dea, hist): MACD差值线、信号线、柱状图
        """
        import pandas as pd

        close = df['收盘'].astype(float)

        # 计算EMA
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()

        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        hist = (dif - dea) * 2

        return round(float(dif.iloc[-1]), 3), round(float(dea.iloc[-1]), 3), round(float(hist.iloc[-1]), 3)

    @staticmethod
    def _calculate_rsi(df: pd.DataFrame, n: list = [6, 12, 24]):
        """计算RSI指标

        Args:
            df: 包含收盘价数据的DataFrame
            n: RSI周期列表

        Returns:
            (rsi_6, rsi_12, rsi_24): 各周期RSI值
        """
        import pandas as pd

        close = df['收盘'].astype(float)
        results = []

        for period in n:
            if len(close) < period:
                results.append(None)
                continue

            delta = close.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)

            avg_gain = gain.rolling(window=period).mean()
            avg_loss = loss.rolling(window=period).mean()

            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            results.append(round(float(rsi.iloc[-1]), 2))

        return tuple(results) if len(results) == 3 else (None, None, None)

    @staticmethod
    def _calculate_boll(df: pd.DataFrame, period: int = 20, std_dev: int = 2):
        """计算布林带指标

        Args:
            df: 包含收盘价数据的DataFrame
            period: 周期
            std_dev: 标准差倍数

        Returns:
            (upper, mid, lower): 上轨、中轨、下轨
        """
        close = df['收盘'].astype(float)

        mid = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()

        upper = mid + std_dev * std
        lower = mid - std_dev * std

        return round(float(upper.iloc[-1]), 2), round(float(mid.iloc[-1]), 2), round(float(lower.iloc[-1]), 2)

    @staticmethod
    def _get_index_trend(index_code: str = "sh.000300") -> Optional[dict]:
        """获取大盘指数趋势（基于沪深300）

        通过Baostock获取沪深300历史K线，计算MA20/MA60/MA250(年线)，
        判断大盘处于牛市(BULLISH)、熊市(BEARISH)还是中性(NEUTRAL)。

        年线（250日均线）是A股公认的"牛熊分界线"。

        容错机制：
        - 获取失败返回None，不影响个股分析
        - 数据不足250日时，用MA20/MA60降级判断
        - MA20和MA60都无效时返回NEUTRAL

        Args:
            index_code: 指数代码，默认沪深300

        Returns:
            dict: {trend, ma20, ma60, ma250, close, change_pct, high_250d} 或 None
        """
        if not _ensure_baostock_login():
            logger.warning("Baostock未登录，跳过大盘数据获取")
            return None

        try:
            # 计算日期范围（最近400个自然日，确保250个交易日用于年线计算）
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

            rs = bs.query_history_k_data_plus(
                index_code,
                "date,close,high,preclose",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
            )

            def _read_index_rows():
                rows = []
                while (rs.error_code == '0') and rs.next():
                    rows.append(rs.get_row_data())
                return rows
            try:
                rows = _call_with_timeout(_read_index_rows, timeout=30)
            except FuturesTimeout:
                logger.warning(f"Baostock 读取大盘数据超时 {index_code}，走降级")
                return None

            if not rows or len(rows) < 20:
                logger.warning(f"大盘数据不足（{len(rows)}行），无法判断趋势")
                return None

            df = pd.DataFrame(rows, columns=rs.fields)
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['preclose'] = pd.to_numeric(df['preclose'], errors='coerce')
            if 'high' in df.columns:
                df['high'] = pd.to_numeric(df['high'], errors='coerce')

            # 计算均线
            ma20 = df['close'].rolling(20).mean().iloc[-1]
            ma60 = df['close'].rolling(60).mean().iloc[-1] if len(df) >= 60 else None
            ma250 = df['close'].rolling(250).mean().iloc[-1] if len(df) >= 250 else None
            latest_close = df['close'].iloc[-1]

            # 近250日最高价（用于计算回撤幅度）
            high_250d = None
            if 'high' in df.columns and len(df) >= 60:
                high_250d = float(df['high'].tail(250).max())

            # 计算涨跌幅
            change_pct = None
            if pd.notna(df['preclose'].iloc[-1]) and df['preclose'].iloc[-1] > 0:
                change_pct = round((df['close'].iloc[-1] - df['preclose'].iloc[-1]) / df['preclose'].iloc[-1] * 100, 2)

            # 近20日涨跌幅（超跌相对跌幅用，oversold_confirm）
            change_20d = None
            if len(df) >= 21:
                change_20d = round((df['close'].iloc[-1] - df['close'].iloc[-21]) / df['close'].iloc[-21] * 100, 2)

            # 判断趋势（基于年线的牛熊判断优先）
            trend = "NEUTRAL"
            if pd.notna(ma250):
                # 有年线数据时：年线上方=BULLISH，下方=BEARISH
                if latest_close > ma250:
                    trend = "BULLISH"
                else:
                    trend = "BEARISH"
            elif pd.notna(ma60):
                # 无年线时降级用MA20/MA60
                if ma20 > ma60 and latest_close > ma20:
                    trend = "BULLISH"
                elif ma20 < ma60 and latest_close < ma20:
                    trend = "BEARISH"
                else:
                    trend = "NEUTRAL"
            elif pd.notna(ma20):
                if latest_close > ma20:
                    trend = "BULLISH"
                else:
                    trend = "BEARISH"

            return {
                "trend": trend,
                "ma20": round(float(ma20), 2) if pd.notna(ma20) else None,
                "ma60": round(float(ma60), 2) if pd.notna(ma60) else None,
                "ma250": round(float(ma250), 2) if pd.notna(ma250) else None,
                "close": round(float(latest_close), 2),
                "change_pct": change_pct,
                "high_250d": high_250d,
                "change_20d": change_20d,
            }

        except Exception as e:
            logger.warning(f"大盘趋势获取异常: {e}")
            # socket错误时重置连接状态
            if '10038' in str(e) or 'socket' in str(e).lower():
                logger.warning("检测到socket异常，重置Baostock连接")
                _baostock_logout()
            return None

    @staticmethod
    def _calculate_kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3):
        """计算KDJ指标

        Args:
            df: 包含High/Low/Close数据的DataFrame
            n: RSV周期
            m1: K值平滑
            m2: D值平滑

        Returns:
            (k, d, j): KDJ三个值
        """
        low_list = df['最低'].rolling(window=n, min_periods=1).min()
        high_list = df['最高'].rolling(window=n, min_periods=1).max()

        close = df['收盘'].astype(float)

        rsv = (close - low_list) / (high_list - low_list) * 100
        rsv = rsv.fillna(50)

        k = rsv.ewm(com=m1 - 1, adjust=False).mean()
        d = k.ewm(com=m2 - 1, adjust=False).mean()
        j = 3 * k - 2 * d

        return round(float(k.iloc[-1]), 2), round(float(d.iloc[-1]), 2), round(float(j.iloc[-1]), 2)


# 导出便捷函数
get_stock_data = AKShareClient.calculate_indicators
get_realtime_quote = AKShareClient.get_realtime_quote
