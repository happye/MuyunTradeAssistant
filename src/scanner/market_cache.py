"""全市场行情数据缓存 - Scanner基础设施

核心职责：
1. 获取全市场A股/ETF行情数据（efinance批量接口）
2. 内存缓存 + TTL过期策略
3. 行业板块 / 概念板块数据缓存
4. 盘中/盘后/非交易日区分

数据源（v0.8.0 Phase 2 更新）：
- 全市场A股: efinance.stock.get_realtime_quotes() ~0.4s（替代AKShare爬虫）
- ETF/行业板块: 仍用AKShare（efinance无对应接口）

设计约束：
- efinance 批量API 0.4秒获取5800+只A股，稳定可靠
- 盘中行情变化快，TTL=5min
- 盘后数据不变，长缓存至次日开盘
"""

import time
import logging
from datetime import datetime, timedelta, time as dtime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# efinance原始列名 → 标准化列名（与原AKShare列名一致，下游无需改动）
_EFINANCE_COLUMN_MAP = {
    "股票代码": "代码",
    "股票名称": "名称",
    "最新价": "最新价",
    "涨跌幅": "涨跌幅",
    "涨跌额": "涨跌额",
    "成交量": "成交量",
    "成交额": "成交额",
    "最高": "最高",
    "最低": "最低",
    "今开": "今开",
    "昨日收盘": "昨收",
    "量比": "量比",
    "换手率": "换手率",
    "动态市盈率": "市盈率-动态",
    "总市值": "总市值",
    "流通市值": "流通市值",
}


class MarketCache:
    """全市场行情数据缓存

    使用场景：Scanner初筛需要全市场行情数据。

    缓存策略：
    - L1 全市场A股: efinance.stock.get_realtime_quotes() ~0.4s, 盘中5min/盘后至次日
    - L1 全市场ETF: ak.fund_etf_spot_em() ~5s, 同上
    - L2 行业板块列表: ak.stock_board_industry_name_em() ~3s, 10min
    - L2 行业成分股: ak.stock_board_industry_cons_em() ~1s/行业, 30min
    - L2 概念板块列表: ak.stock_board_concept_name_em() ~3s, 10min
    - L2 概念成分股: ak.stock_board_concept_cons_em() ~1s/概念, 30min
    """

    # 默认盘中缓存TTL（秒）
    DEFAULT_TRADING_TTL = 300  # 5分钟

    def __init__(self, ttl_seconds: int = 0):
        """初始化缓存

        Args:
            ttl_seconds: 盘中缓存TTL（秒），0使用默认值300
        """
        self._ttl = ttl_seconds or self.DEFAULT_TRADING_TTL

        # L1 缓存: 全市场行情
        self._stock_df: Optional[pd.DataFrame] = None
        self._stock_timestamp: float = 0.0
        self._stock_count: int = 0

        # L1 缓存: ETF行情
        self._etf_df: Optional[pd.DataFrame] = None
        self._etf_timestamp: float = 0.0
        self._etf_count: int = 0

        # L2 缓存: 行业板块列表
        self._industry_df: Optional[pd.DataFrame] = None
        self._industry_timestamp: float = 0.0

        # L2 缓存: 概念板块列表
        self._concept_df: Optional[pd.DataFrame] = None
        self._concept_timestamp: float = 0.0

        # L2 缓存: 行业成分股（按行业名缓存）
        self._industry_stocks: dict[str, list[str]] = {}
        self._industry_stocks_ts: dict[str, float] = {}
        self._industry_stocks_ttl = 1800  # 30分钟

        # L2 缓存: 概念成分股（按概念名缓存）
        self._concept_stocks: dict[str, list[str]] = {}
        self._concept_stocks_ts: dict[str, float] = {}
        self._concept_stocks_ttl = 1800  # 30分钟

    @staticmethod
    def _without_proxy():
        """返回一个上下文管理器，临时清除HTTP代理环境变量

        国内金融数据API（东方财富等）直连即可，走代理反而会导致：
        1. 代理无法正确转发这些请求（返回空响应/JSON解析失败）
        2. 代理增加延迟（原本0.4s的请求变4min）
        3. 代理IP可能被金融网站封禁

        用法：
            with MarketCache._without_proxy():
                df = ef.stock.get_realtime_quotes()
        """
        import contextlib

        @contextlib.contextmanager
        def _proxy_disabled():
            proxy_keys = [
                "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy",
            ]
            saved = {}
            for key in proxy_keys:
                if key in __import__("os").environ:
                    saved[key] = __import__("os").environ.pop(key)
            try:
                yield
            finally:
                __import__("os").environ.update(saved)

        return _proxy_disabled()

    def get_all_stocks(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取全市场A股行情（带缓存+重试）

        数据源优先级：
        1. 新浪财经API（并行分页，~0.3秒，字段齐全含市净率）
        2. efinance（东方财富API，备用，无市净率）

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            全市场行情DataFrame（列名标准化），空DataFrame表示获取失败
        """
        # 禁用AKShare/efinance内部的tqdm进度条
        import os
        os.environ["TQDM_DISABLE"] = "1"

        if not force_refresh and self._stock_df is not None and not self._is_expired(self._stock_timestamp):
            logger.info(f"MarketCache: A股缓存命中({self._stock_count}只)")
            return self._stock_df

        # 主数据源：新浪财经API
        df = self._fetch_sina_market()
        if df is not None and not df.empty:
            self._stock_df = df
            self._stock_timestamp = time.time()
            self._stock_count = len(df)
            logger.info(f"MarketCache: A股行情获取成功({self._stock_count}只, 新浪源)")
            return df

        # 备用：efinance
        logger.warning("MarketCache: 新浪API失败，尝试efinance备用...")
        df = self._fetch_efinance_market()
        if df is not None and not df.empty:
            self._stock_df = df
            self._stock_timestamp = time.time()
            self._stock_count = len(df)
            logger.info(f"MarketCache: A股行情获取成功({self._stock_count}只, efinance备用)")
            return df

        # 全部失败，返回过期缓存
        if self._stock_df is not None:
            logger.warning("MarketCache: 所有数据源失败，使用过期缓存（兜底）")
            return self._stock_df
        return pd.DataFrame()

    def _fetch_sina_market(self) -> Optional[pd.DataFrame]:
        """通过新浪财经API获取全市场A股行情（并行分页）

        接口：vip.stock.finance.sina.com.cn Market_Center.getHQNodeData
        特点：每页最多80条，并行获取约73页，总计0.3-5秒
        字段齐全：涨跌幅、换手率、市盈率、市净率、总市值等

        Returns:
            标准化后的DataFrame，失败返回None
        """
        import requests as _requests
        from concurrent.futures import ThreadPoolExecutor, as_completed

        BASE_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        COMMON_PARAMS = "&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=init"
        HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        PAGE_SIZE = 80

        # 新浪API不走代理（直连更稳定）
        session = _requests.Session()
        session.trust_env = False

        def _fetch_page(page: int) -> list:
            try:
                with self._without_proxy():
                    resp = session.get(
                        f"{BASE_URL}?page={page}&num={PAGE_SIZE}{COMMON_PARAMS}",
                        timeout=30, headers=HEADERS,
                    )
                    if resp.status_code == 200:
                        return resp.json() or []
            except Exception:
                pass
            return []

        # 估算总页数（A股约5800只，每页80）
        total_pages = 73
        all_stocks = []

        try:
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(_fetch_page, p): p for p in range(1, total_pages + 1)}
                for future in as_completed(futures):
                    result = future.result()
                    all_stocks.extend(result)
        except Exception as e:
            logger.error(f"MarketCache: 新浪API并行获取异常: {e}")
            return None

        if not all_stocks:
            logger.warning("MarketCache: 新浪API返回空数据")
            return None

        # 去重
        seen = set()
        unique = []
        for s in all_stocks:
            code = s.get("code", "")
            if code and code not in seen:
                seen.add(code)
                unique.append(s)

        # 转为DataFrame并标准化列名
        df = pd.DataFrame(unique)
        df = self._normalize_sina_columns(df)
        return df

    @staticmethod
    def _normalize_sina_columns(df: pd.DataFrame) -> pd.DataFrame:
        """将新浪API原始字段名标准化为AKShare兼容格式

        新浪字段 → 标准列名：
        code→代码, name→名称, trade→最新价, changepercent→涨跌幅,
        settlement→昨收, open→今开, high→最高, low→最低,
        volume→成交量, amount→成交额, turnoverratio→换手率,
        per→市盈率-动态, pb→市净率, mktcap→总市值, nmc→流通市值

        同时计算：振幅 = (最高 - 最低) / 昨收 * 100
        """
        # 字段映射
        rename_map = {
            "code": "代码", "name": "名称", "trade": "最新价",
            "changepercent": "涨跌幅", "settlement": "昨收",
            "open": "今开", "high": "最高", "low": "最低",
            "volume": "成交量", "amount": "成交额",
            "turnoverratio": "换手率", "per": "市盈率-动态",
            "pb": "市净率", "mktcap": "总市值", "nmc": "流通市值",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        # 保留标准列
        keep_cols = [c for c in df.columns if c in set(rename_map.values()) | {"振幅"}]

        # 计算振幅
        if "最高" in df.columns and "最低" in df.columns and "昨收" in df.columns:
            high = pd.to_numeric(df["最高"], errors="coerce")
            low = pd.to_numeric(df["最低"], errors="coerce")
            prev_close = pd.to_numeric(df["昨收"], errors="coerce")
            df["振幅"] = ((high - low) / prev_close * 100).round(2)
            keep_cols.append("振幅")

        # 市值单位：新浪返回万元，转为元（与AKShare/efinance一致）
        for col in ["总市值", "流通市值"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce") * 10000

        # 只保留标准列
        df = df[[c for c in keep_cols if c in df.columns]]

        return df

    def _fetch_efinance_market(self) -> Optional[pd.DataFrame]:
        """通过efinance获取全市场行情（备用数据源）

        efinance使用东方财富API，无市净率字段。

        Returns:
            标准化后的DataFrame，失败返回None
        """
        try:
            import efinance as ef
            with self._without_proxy():
                df = ef.stock.get_realtime_quotes()
            if df is not None and not df.empty:
                return self._normalize_efinance_columns(df)
        except Exception as e:
            logger.error(f"MarketCache: efinance获取失败: {e}")
        return None

    @staticmethod
    def _normalize_efinance_columns(df: pd.DataFrame) -> pd.DataFrame:
        """将efinance原始列名标准化为AKShare兼容格式

        同时：
        1. 重命名列（按_EFINANCE_COLUMN_MAP）
        2. 计算振幅 = (最高 - 最低) / 昨收 * 100
        3. 将 '-' 字符串替换为 NaN（efinance用'-'表示无效值）
        4. 删除不需要的efinance特有列

        Args:
            df: efinance原始DataFrame

        Returns:
            标准化后的DataFrame
        """
        # Step 1: 将 '-' 替换为 NaN（efinance的空值标记）
        df = df.replace("-", pd.NA)

        # Step 2: 计算振幅（efinance无此字段，需手动算）
        if "最高" in df.columns and "最低" in df.columns and "昨日收盘" in df.columns:
            high = pd.to_numeric(df["最高"], errors="coerce")
            low = pd.to_numeric(df["最低"], errors="coerce")
            prev_close = pd.to_numeric(df["昨日收盘"], errors="coerce")
            df["振幅"] = ((high - low) / prev_close * 100).round(2)

        # Step 3: 重命名列
        rename_map = {k: v for k, v in _EFINANCE_COLUMN_MAP.items() if k in df.columns}
        df = df.rename(columns=rename_map)

        # Step 4: 保留下游需要的列（删除efinance特有列如"行情ID"、"市场类型"等）
        keep_cols = [c for c in df.columns if c in set(_EFINANCE_COLUMN_MAP.values()) | {"振幅"}]
        df = df[keep_cols]

        return df

    def get_all_etfs(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取全市场ETF行情（带缓存）

        数据源: ak.fund_etf_spot_em()
        约5秒完成。

        Args:
            force_refresh: 是否强制刷新

        Returns:
            ETF行情DataFrame
        """
        if not force_refresh and self._etf_df is not None and not self._is_expired(self._etf_timestamp):
            logger.info(f"MarketCache: ETF缓存命中({self._etf_count}只)")
            return self._etf_df

        logger.info("MarketCache: 获取全市场ETF行情...")
        try:
            import akshare as ak
            with self._without_proxy():
                df = ak.fund_etf_spot_em()
            if df is not None and not df.empty:
                self._etf_df = df
                self._etf_timestamp = time.time()
                self._etf_count = len(df)
                logger.info(f"MarketCache: ETF行情获取成功({self._etf_count}只)")
                return df
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"MarketCache: ETF行情获取失败: {e}")
            if self._etf_df is not None:
                return self._etf_df
            return pd.DataFrame()

    def get_industry_boards(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取行业板块列表（带缓存）

        数据源: ak.stock_board_industry_name_em()
        返回496个行业板块，含涨跌幅、总市值、换手率等。

        Args:
            force_refresh: 是否强制刷新

        Returns:
            行业板块DataFrame
        """
        ttl = 600  # 行业板块10分钟TTL
        if (not force_refresh
                and self._industry_df is not None
                and (time.time() - self._industry_timestamp) < ttl):
            logger.info("MarketCache: 行业板块缓存命中")
            return self._industry_df

        logger.info("MarketCache: 获取行业板块列表...")
        try:
            import akshare as ak
            with self._without_proxy():
                df = ak.stock_board_industry_name_em()
            if df is not None and not df.empty:
                self._industry_df = df
                self._industry_timestamp = time.time()
                logger.info(f"MarketCache: 行业板块获取成功({len(df)}个)")
                return df
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"MarketCache: 行业板块获取失败: {e}")
            if self._industry_df is not None:
                return self._industry_df
            return pd.DataFrame()

    def get_stocks_by_industry(self, industry_name: str) -> list[str]:
        """获取指定行业的成分股代码列表（带缓存）

        数据源: ak.stock_board_industry_cons_em(symbol=行业名)
        单个行业约1秒，结果缓存30分钟。

        Args:
            industry_name: 行业名称（如"半导体"）

        Returns:
            股票代码列表（如["688981", "002049", ...]）
        """
        # 缓存命中检查
        if industry_name in self._industry_stocks:
            ts = self._industry_stocks_ts.get(industry_name, 0)
            if (time.time() - ts) < self._industry_stocks_ttl:
                logger.info(f"MarketCache: 行业成分股缓存命中({industry_name})")
                return self._industry_stocks[industry_name]

        logger.info(f"MarketCache: 获取行业成分股({industry_name})...")
        try:
            import akshare as ak
            with self._without_proxy():
                df = ak.stock_board_industry_cons_em(symbol=industry_name)
            if df is not None and not df.empty:
                # 查找代码列
                code_col = None
                for col in ["代码", "code", "symbol"]:
                    if col in df.columns:
                        code_col = col
                        break

                if code_col:
                    codes = df[code_col].astype(str).tolist()
                    self._industry_stocks[industry_name] = codes
                    self._industry_stocks_ts[industry_name] = time.time()
                    logger.info(f"MarketCache: 行业成分股获取成功({industry_name}, {len(codes)}只)")
                    return codes

            return []
        except Exception as e:
            logger.error(f"MarketCache: 行业成分股获取失败({industry_name}): {e}")
            # 返回过期缓存（如果有）
            return self._industry_stocks.get(industry_name, [])

    def get_concept_boards(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取概念板块列表（带缓存）"""
        ttl = 600  # 概念板块10分钟TTL
        if (not force_refresh
                and self._concept_df is not None
                and (time.time() - self._concept_timestamp) < ttl):
            logger.info("MarketCache: 概念板块缓存命中")
            return self._concept_df

        logger.info("MarketCache: 获取概念板块列表...")
        try:
            import akshare as ak
            with self._without_proxy():
                df = ak.stock_board_concept_name_em()
            if df is not None and not df.empty:
                self._concept_df = df
                self._concept_timestamp = time.time()
                logger.info(f"MarketCache: 概念板块获取成功({len(df)}个)")
                return df
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"MarketCache: 概念板块获取失败: {e}")
            if self._concept_df is not None:
                return self._concept_df
            return pd.DataFrame()

    def get_stocks_by_concept(self, concept_name: str) -> list[str]:
        """获取指定概念的成分股代码列表（带缓存）"""
        if concept_name in self._concept_stocks:
            ts = self._concept_stocks_ts.get(concept_name, 0)
            if (time.time() - ts) < self._concept_stocks_ttl:
                logger.info(f"MarketCache: 概念成分股缓存命中({concept_name})")
                return self._concept_stocks[concept_name]

        logger.info(f"MarketCache: 获取概念成分股({concept_name})...")
        try:
            import akshare as ak
            with self._without_proxy():
                df = ak.stock_board_concept_cons_em(symbol=concept_name)
            if df is not None and not df.empty:
                code_col = None
                for col in ["代码", "code", "symbol"]:
                    if col in df.columns:
                        code_col = col
                        break

                if code_col:
                    codes = df[code_col].astype(str).tolist()
                    self._concept_stocks[concept_name] = codes
                    self._concept_stocks_ts[concept_name] = time.time()
                    logger.info(f"MarketCache: 概念成分股获取成功({concept_name}, {len(codes)}只)")
                    return codes

            return []
        except Exception as e:
            logger.error(f"MarketCache: 概念成分股获取失败({concept_name}): {e}")
            return self._concept_stocks.get(concept_name, [])

    def is_trading_hours(self) -> bool:
        """判断当前是否在A股交易时段

        A股交易时间：周一至周五 9:30-11:30, 13:00-15:00
        """
        now = datetime.now()

        # 周末不交易
        if now.weekday() >= 5:
            return False

        # 检查是否在交易时段
        t = now.time()
        morning = dtime(9, 30) <= t <= dtime(11, 30)
        afternoon = dtime(13, 0) <= t <= dtime(15, 0)

        return morning or afternoon

    def _is_expired(self, timestamp: float) -> bool:
        """判断缓存是否过期（区分盘中/盘后/非交易日）

        盘中：TTL过期（默认5分钟）
        盘后/非交易日：长缓存（如果缓存是今天创建的，不过期）

        Args:
            timestamp: 缓存创建时间戳

        Returns:
            True表示已过期
        """
        if not self.is_trading_hours():
            # 非交易时段
            cache_time = datetime.fromtimestamp(timestamp)
            now = datetime.now()

            # 如果缓存是今天盘后创建的，不过期
            if cache_time.date() == now.date() and cache_time.hour >= 15:
                return False

            # 如果缓存是昨天或更早的盘后创建的，且现在是盘前，不过期
            # （交易日开盘前用昨天的收盘数据也是合理的）
            if cache_time.date() >= (now.date() - timedelta(days=1)):
                return False

        # 盘中：TTL过期
        return (time.time() - timestamp) > self._ttl

    def refresh(self):
        """强制刷新所有缓存"""
        self._stock_df = None
        self._stock_timestamp = 0.0
        self._etf_df = None
        self._etf_timestamp = 0.0
        self._industry_df = None
        self._industry_timestamp = 0.0
        self._concept_df = None
        self._concept_timestamp = 0.0
        self._industry_stocks.clear()
        self._industry_stocks_ts.clear()
        self._concept_stocks.clear()
        self._concept_stocks_ts.clear()
        logger.info("MarketCache: 所有缓存已清除")

    def get_cache_status(self) -> dict:
        """返回缓存状态信息（用于CLI展示）

        Returns:
            缓存状态字典
        """
        status = {
            "trading_hours": self.is_trading_hours(),
            "stocks": {
                "cached": self._stock_df is not None,
                "count": self._stock_count if self._stock_df is not None else 0,
                "age_seconds": round(time.time() - self._stock_timestamp) if self._stock_timestamp else 0,
                "expired": self._is_expired(self._stock_timestamp) if self._stock_timestamp else True,
            },
            "etfs": {
                "cached": self._etf_df is not None,
                "count": self._etf_count if self._etf_df is not None else 0,
                "age_seconds": round(time.time() - self._etf_timestamp) if self._etf_timestamp else 0,
                "expired": self._is_expired(self._etf_timestamp) if self._etf_timestamp else True,
            },
            "industries": {
                "cached": self._industry_df is not None,
                "count": len(self._industry_df) if self._industry_df is not None else 0,
            },
            "concepts": {
                "cached": self._concept_df is not None,
                "count": len(self._concept_df) if self._concept_df is not None else 0,
            },
        }
        return status
