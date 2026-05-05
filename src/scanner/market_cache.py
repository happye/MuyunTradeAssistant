"""全市场行情数据缓存 - Scanner基础设施

核心职责：
1. 获取全市场A股/ETF行情数据（AKShare批量接口）
2. 内存缓存 + TTL过期策略
3. 行业板块数据缓存
4. 盘中/盘后/非交易日区分

设计约束：
- ak.stock_zh_a_spot_em() 全市场约4分钟（58页），必须缓存
- 盘中行情变化快，TTL=5min
- 盘后数据不变，长缓存至次日开盘
"""

import time
import logging
from datetime import datetime, timedelta, time as dtime
from typing import Optional

import pandas as pd
import akshare as ak

logger = logging.getLogger(__name__)


class MarketCache:
    """全市场行情数据缓存

    使用场景：Scanner初筛需要全市场行情数据，
    每次拉取约4分钟，必须缓存避免重复请求。

    缓存策略：
    - L1 全市场A股: ak.stock_zh_a_spot_em() ~4min, 盘中5min/盘后至次日
    - L1 全市场ETF: ak.fund_etf_spot_em() ~5s, 同上
    - L2 行业板块列表: ak.stock_board_industry_name_em() ~3s, 10min
    - L2 行业成分股: ak.stock_board_industry_cons_em() ~1s/行业, 30min
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

        # L2 缓存: 行业成分股（按行业名缓存）
        self._industry_stocks: dict[str, list[str]] = {}
        self._industry_stocks_ts: dict[str, float] = {}
        self._industry_stocks_ttl = 1800  # 30分钟

    def get_all_stocks(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取全市场A股行情（带缓存+重试）

        数据源: ak.stock_zh_a_spot_em()
        返回5000+只A股的实时行情，包含：
        代码、名称、最新价、涨跌幅、涨跌额、成交量、成交额、
        振幅、最高、最低、今开、昨收、量比、换手率、市盈率-动态、市净率

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            全市场行情DataFrame，空DataFrame表示获取失败
        """
        if not force_refresh and self._stock_df is not None and not self._is_expired(self._stock_timestamp):
            logger.info(f"MarketCache: A股缓存命中({self._stock_count}只)")
            return self._stock_df

        # 带重试的数据获取（网络超时常见）
        max_retries = 2
        for attempt in range(max_retries + 1):
            logger.info(
                f"MarketCache: 获取全市场A股行情(约4分钟)..."
                + (f" 第{attempt + 1}次尝试" if attempt > 0 else "")
            )
            start_time = time.time()

            try:
                df = ak.stock_zh_a_spot_em()
                elapsed = time.time() - start_time

                if df is not None and not df.empty:
                    self._stock_df = df
                    self._stock_timestamp = time.time()
                    self._stock_count = len(df)
                    logger.info(
                        f"MarketCache: A股行情获取成功 "
                        f"({self._stock_count}只, {elapsed:.1f}秒)"
                    )
                    return df
                else:
                    logger.warning("MarketCache: A股行情返回空数据")

            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(
                    f"MarketCache: A股行情获取失败(第{attempt + 1}次, {elapsed:.1f}秒): {e}"
                )
                if attempt < max_retries:
                    wait = 5 * (attempt + 1)
                    logger.info(f"MarketCache: {wait}秒后重试...")
                    time.sleep(wait)

        # 所有重试都失败，返回过期缓存（如果有）
        if self._stock_df is not None:
            logger.warning("MarketCache: 所有重试失败，使用过期A股缓存（兜底）")
            return self._stock_df
        return pd.DataFrame()

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
        self._industry_stocks.clear()
        self._industry_stocks_ts.clear()
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
        }
        return status
