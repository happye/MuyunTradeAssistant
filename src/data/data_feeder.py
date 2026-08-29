"""历史数据回放器 - 回测框架专用

从Baostock获取历史K线，对每个交易日重建完整的StockData（含技术指标+大盘数据）。
与akshare_client.calculate_indicators不同，本模块可以"回到过去"：
  - 输入截止日期 → 返回该日期的完整技术指标
  - 均线/RSI/MACD等基于截止日期前的历史数据计算
  - 大盘趋势也基于截止日期的指数数据判断
"""

import pandas as pd
import baostock as bs
import logging
from datetime import datetime, timedelta
from typing import Optional

from src.data.models import StockData


def _use_legacy_sma_indicators() -> bool:
    """C01/C02 A/B 开关（第三轮审查）：MUYUN_INDICATOR_LEGACY=1 时退回 v0.8.7.6 前的
    SMA 口径做对照。默认 Wilder/通达信口径（ewm alpha=1/N）。只读 env 不写，无污染。"""
    import os
    return os.environ.get("MUYUN_INDICATOR_LEGACY", "") == "1"

logger = logging.getLogger(__name__)


class DataFeeder:
    """历史数据回放器

    用法:
        feeder = DataFeeder("600519", start_date="2024-01-01", end_date="2025-01-01")
        feeder.load()  # 一次性加载所有数据
        for date, stock_data in feeder.iterate():
            # stock_data 是该交易日的完整 StockData
            ...
    """

    def __init__(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        index_code: str = "sh.000300",
    ):
        """
        Args:
            stock_code: 股票代码，如 600519
            start_date: 回测起始日期 YYYY-MM-DD
            end_date: 回测结束日期 YYYY-MM-DD
            index_code: 大盘指数代码，默认沪深300
        """
        self.stock_code = stock_code.strip().zfill(6)
        self.start_date = start_date
        self.end_date = end_date
        self.index_code = index_code

        # Baostock代码格式
        if self.stock_code.startswith(('6', '9')):
            self.bs_code = f"sh.{self.stock_code}"
        else:
            self.bs_code = f"sz.{self.stock_code}"

        # 加载后填充
        self._stock_df: Optional[pd.DataFrame] = None
        self._index_df: Optional[pd.DataFrame] = None
        self._stock_name: str = self.stock_code
        self._dates: list[str] = []  # 交易日期列表

    def load(self) -> bool:
        """加载历史数据（个股K线 + 大盘指数）

        一次性拉取所有数据，后续iterate不再网络请求。

        Returns:
            bool: 是否加载成功
        """
        try:
            lg = bs.login()
            if lg.error_code != '0':
                logger.error(f"Baostock登录失败: {lg.error_msg}")
                return False

            # 加载个股数据 - 需要足够的历史数据计算MA60等指标
            # 向前多取320天，确保MA250(年线)等长周期指标有效
            calc_start = self._shift_date(self.start_date, days=-320)
            self._stock_df = self._fetch_stock_kline(calc_start, self.end_date)
            if self._stock_df is None or self._stock_df.empty:
                logger.error(f"个股K线数据获取失败: {self.stock_code}")
                bs.logout()
                return False

            # 加载大盘指数
            self._index_df = self._fetch_index_kline(calc_start, self.end_date)

            bs.logout()

            # 筛选回测区间内的交易日期
            mask = (self._stock_df['date'] >= self.start_date) & (self._stock_df['date'] <= self.end_date)
            self._dates = self._stock_df.loc[mask, 'date'].tolist()

            logger.info(f"DataFeeder加载完成: {self.stock_code}, {len(self._dates)}个交易日 "
                        f"({self._dates[0] if self._dates else 'N/A'} ~ {self._dates[-1] if self._dates else 'N/A'})")
            return True

        except Exception as e:
            logger.error(f"DataFeeder加载异常: {e}")
            return False

    def iterate(self):
        """迭代器：逐日产出 (date_str, StockData)

        Yields:
            tuple[str, StockData]: (日期, 完整股票数据)
        """
        if self._stock_df is None:
            raise RuntimeError("请先调用 load() 加载数据")

        for date in self._dates:
            stock_data = self._build_stock_data(date)
            if stock_data:
                yield date, stock_data

    def get_date_count(self) -> int:
        """获取回测交易日数量"""
        return len(self._dates)

    def get_kline_until(self, date: str) -> Optional[pd.DataFrame]:
        """返回截至 date（含）的个股K线切片（bar 时点）。

        v0.8.7.5 审计修复 A03：回测建仓笨总评分改用 bar 时点 K线，
        替代此前 rule_scorer 内部实时拉取（前瞻偏差+回测触发网络）。
        列为 baostock 原始小写列（date/close/low...），评分器兼容多种列名。
        """
        if self._stock_df is None or self._stock_df.empty:
            return None
        try:
            return self._stock_df[self._stock_df['date'] <= date].copy()
        except Exception:
            return None

    def get_next_trading_day(self, date: str) -> Optional[str]:
        """获取指定日期的下一个交易日

        Args:
            date: 当前日期 YYYY-MM-DD

        Returns:
            下一交易日的日期字符串，如果是最后一天则返回None
        """
        if date not in self._dates:
            return None
        idx = self._dates.index(date)
        if idx + 1 < len(self._dates):
            return self._dates[idx + 1]
        return None

    def get_open_price(self, date: str) -> Optional[float]:
        """获取指定日期的开盘价

        Args:
            date: 日期 YYYY-MM-DD

        Returns:
            开盘价，数据不可用时返回None
        """
        if self._stock_df is None:
            return None
        row = self._stock_df[self._stock_df['date'] == date]
        if row.empty:
            return None
        open_val = row.iloc[0]['open']
        if pd.notna(open_val):
            return float(open_val)
        return None

    def _build_stock_data(self, date: str) -> Optional[StockData]:
        """构建指定日期的完整StockData

        核心逻辑：
        1. 截止该日期的所有历史K线
        2. 重新计算均线/RSI/MACD/布林带/KDJ
        3. 获取该日期的大盘趋势
        """
        df = self._stock_df
        if df is None:
            return None

        # 截止该日期的数据
        cutoff = df[df['date'] <= date].copy()
        if len(cutoff) < 5:
            return None

        latest = cutoff.iloc[-1]
        price = float(latest['close'])
        volume = int(float(latest['volume']))

        # 涨跌幅
        change_pct = None
        if len(cutoff) >= 2 and float(cutoff.iloc[-2]['close']) > 0:
            prev_close = float(cutoff.iloc[-2]['close'])
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        # 计算均线
        close = cutoff['close'].astype(float)
        ma5 = self._safe_rolling(close, 5)
        ma10 = self._safe_rolling(close, 10)
        ma20 = self._safe_rolling(close, 20)
        ma60 = self._safe_rolling(close, 60)
        ma120 = self._safe_rolling(close, 120)

        # 成交量均值
        vol = cutoff['volume'].astype(float)
        avg_volume_20 = self._safe_rolling(vol, 20)

        # 60日高低（v0.8.7.7 C07 修复：守卫 >=20 → >=60 与字段名一致，
        # 原 20-59 根K线时给的是伪"60日高点"，污染价格位置/突破判定）
        recent_60 = cutoff.tail(60)
        high_60d = float(recent_60['high'].astype(float).max()) if len(recent_60) >= 60 else None
        low_60d = float(recent_60['low'].astype(float).min()) if len(recent_60) >= 60 else None

        # 120日高低（v0.8.7.7 C04 修复：守卫 >=60 → >=120 对齐 live 路径，
        # 原 60-119 根K线时回测有值/live 为 None，回测突破信号无法迁移实盘）
        recent_120 = cutoff.tail(120)
        high_120d = float(recent_120['high'].astype(float).max()) if len(recent_120) >= 120 else None
        low_120d = float(recent_120['low'].astype(float).min()) if len(recent_120) >= 120 else None

        # MACD
        macd_dif, macd_dea, macd_hist = None, None, None
        if len(cutoff) >= 26:
            macd_dif, macd_dea, macd_hist = self._calc_macd(cutoff)

        # RSI
        rsi_6, rsi_12, rsi_24 = None, None, None
        if len(cutoff) >= 24:
            rsi_6, rsi_12, rsi_24 = self._calc_rsi(cutoff)

        # 布林带
        boll_upper, boll_mid, boll_lower = None, None, None
        if len(cutoff) >= 20:
            boll_upper, boll_mid, boll_lower = self._calc_boll(cutoff)

        # KDJ
        kdj_k, kdj_d, kdj_j = None, None, None
        if len(cutoff) >= 9:
            kdj_k, kdj_d, kdj_j = self._calc_kdj(cutoff)

        # ATR
        atr_14 = None
        if len(cutoff) >= 14:
            atr_14 = self._calc_atr(cutoff, period=14)

        # 大盘趋势
        index_trend, index_ma20, index_ma60, index_ma250, index_close, index_change_pct, index_high_250d = self._get_index_at_date(date)
        weekly_snapshot = self._build_timeframe_snapshot(cutoff, freq="W-FRI")
        monthly_snapshot = self._build_timeframe_snapshot(cutoff, freq="ME")

        return StockData(
            stock_code=self.stock_code,
            stock_name=self._stock_name,
            price=price,
            open=float(latest['open']) if pd.notna(latest['open']) else None,
            high=float(latest['high']) if pd.notna(latest['high']) else None,
            low=float(latest['low']) if pd.notna(latest['low']) else None,
            change_pct=change_pct,
            volume=volume,
            ma5=ma5, ma10=ma10, ma20=ma20, ma60=ma60, ma120=ma120,
            avg_volume_20=avg_volume_20,
            high_60d=high_60d, low_60d=low_60d,
            high_120d=high_120d, low_120d=low_120d,
            macd_dif=macd_dif, macd_dea=macd_dea, macd_hist=macd_hist,
            rsi_6=rsi_6, rsi_12=rsi_12, rsi_24=rsi_24,
            boll_upper=boll_upper, boll_mid=boll_mid, boll_lower=boll_lower,
            kdj_k=kdj_k, kdj_d=kdj_d, kdj_j=kdj_j,
            atr_14=atr_14,
            index_trend=index_trend,
            index_ma20=index_ma20,
            index_ma60=index_ma60,
            index_ma250=index_ma250,
            index_close=index_close,
            index_change_pct=index_change_pct,
            index_high_250d=index_high_250d,
            weekly=weekly_snapshot,
            monthly=monthly_snapshot,
        )

    def _build_timeframe_snapshot(self, cutoff: pd.DataFrame, freq: str) -> Optional[dict]:
        """将截止当前交易日的日线聚合为周线或月线摘要。"""
        if cutoff is None or cutoff.empty:
            return None

        frame = cutoff.copy()
        frame['date'] = pd.to_datetime(frame['date'])
        frame = frame.set_index('date').sort_index()

        aggregated = frame.resample(freq).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
        }).dropna(subset=['close'])

        if aggregated.empty:
            return None

        close = aggregated['close'].astype(float)
        aggregated['ma5'] = close.rolling(5).mean()
        aggregated['ma10'] = close.rolling(10).mean()
        aggregated['ma20'] = close.rolling(20).mean()
        latest = aggregated.iloc[-1]

        close_val = float(latest['close'])
        ma5 = round(float(latest['ma5']), 2) if pd.notna(latest['ma5']) else None
        ma10 = round(float(latest['ma10']), 2) if pd.notna(latest['ma10']) else None
        ma20 = round(float(latest['ma20']), 2) if pd.notna(latest['ma20']) else None

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

    def _get_index_at_date(self, date: str) -> tuple:
        """获取指定日期的大盘趋势数据

        Returns:
            (trend, ma20, ma60, ma250, close, change_pct, high_250d)
        """
        if self._index_df is None or self._index_df.empty:
            return None, None, None, None, None, None, None

        idx = self._index_df[self._index_df['date'] <= date]
        if len(idx) < 20:
            return None, None, None, None, None, None, None

        close = idx['close'].astype(float)
        ma20 = self._safe_rolling(close, 20)
        ma60 = self._safe_rolling(close, 60)
        ma250 = self._safe_rolling(close, 250)
        latest_close = float(close.iloc[-1])

        # 近250日最高价（用于计算回撤幅度）
        high_250d = None
        if 'high' in idx.columns:
            recent_250 = idx.tail(250)
            if len(recent_250) >= 60:
                high_250d = float(recent_250['high'].astype(float).max())

        # 涨跌幅
        change_pct = None
        if len(idx) >= 2:
            prev = float(close.iloc[-2])
            if prev > 0:
                change_pct = round((latest_close - prev) / prev * 100, 2)

        # 趋势判断
        trend = "NEUTRAL"
        if ma60 is not None:
            if ma20 > ma60 and latest_close > ma20:
                trend = "BULLISH"
            elif ma20 < ma60 and latest_close < ma20:
                trend = "BEARISH"
        elif ma20 is not None:
            if latest_close > ma20:
                trend = "BULLISH"
            else:
                trend = "BEARISH"

        return trend, ma20, ma60, ma250, latest_close, change_pct, high_250d

    # ===== 数据获取 =====

    def _fetch_stock_kline(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """获取个股历史K线"""
        try:
            rs = bs.query_history_k_data_plus(
                self.bs_code,
                "date,code,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",  # v0.8.7.5 审计修复 A05：前复权，与实盘 akshare adjust="qfq" 对齐。
                # 此前缺省="3"不复权：除权日假暴跌触发虚假止损；分红丢失致回测/基准收益系统性偏差
            )

            if rs.error_code != '0':
                logger.error(f"个股K线查询失败: {rs.error_msg}")
                return None

            rows = []
            while (rs.error_code == '0') and rs.next():
                rows.append(rs.get_row_data())

            if not rows:
                return None

            df = pd.DataFrame(rows, columns=rs.fields)

            # 类型转换
            for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 过滤无效行
            df = df.dropna(subset=['close', 'volume'])
            df = df[df['volume'] > 0].reset_index(drop=True)

            return df

        except Exception as e:
            logger.error(f"获取个股K线异常: {e}")
            return None

    def _fetch_index_kline(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """获取大盘指数K线"""
        try:
            rs = bs.query_history_k_data_plus(
                self.index_code,
                "date,close,high,preclose",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
            )

            rows = []
            while (rs.error_code == '0') and rs.next():
                rows.append(rs.get_row_data())

            if not rows:
                return None

            df = pd.DataFrame(rows, columns=rs.fields)
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            if 'high' in df.columns:
                df['high'] = pd.to_numeric(df['high'], errors='coerce')
            df = df.dropna(subset=['close']).reset_index(drop=True)

            return df

        except Exception as e:
            logger.warning(f"大盘指数获取异常（不影响回测）: {e}")
            return None

    # ===== 技术指标计算 =====

    @staticmethod
    def _safe_rolling(series: pd.Series, window: int) -> Optional[float]:
        """安全计算滚动均值"""
        if len(series) < window:
            # 数据不足window，用可用数据计算
            if len(series) >= max(3, window // 2):
                val = float(series.tail(min(len(series), window)).mean())
                return round(val, 2) if pd.notna(val) else None
            return None
        val = float(series.rolling(window=window).mean().iloc[-1])
        return round(val, 2) if pd.notna(val) else None

    @staticmethod
    def _calc_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
        """计算MACD"""
        close = df['close'].astype(float)
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        hist = (dif - dea) * 2
        return round(float(dif.iloc[-1]), 3), round(float(dea.iloc[-1]), 3), round(float(hist.iloc[-1]), 3)

    @staticmethod
    def _calc_rsi(df: pd.DataFrame, periods: list = None):
        """计算RSI"""
        if periods is None:
            periods = [6, 12, 24]
        close = df['close'].astype(float)
        results = []
        for period in periods:
            if len(close) < period:
                results.append(None)
                continue
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            if _use_legacy_sma_indicators():
                avg_gain = gain.rolling(window=period).mean()
                avg_loss = loss.rolling(window=period).mean()
            else:
                # v0.8.7.7 C01 修复：Wilder RMA（通达信 SMA(X,N,1) = ewm alpha=1/N），
                # 与通达信/同花顺/TradingView 对齐；原 rolling().mean() 判反率最高 14%
                avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
                avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            val = float(rsi.iloc[-1])
            results.append(round(val, 2) if pd.notna(val) else None)
        return tuple(results) if len(results) == 3 else (None, None, None)

    @staticmethod
    def _calc_boll(df: pd.DataFrame, period: int = 20, std_dev: int = 2):
        """计算布林带"""
        close = df['close'].astype(float)
        mid = close.rolling(window=period).mean()
        # v0.8.7.7 C05 修复：显式 ddof=1 对齐通达信 STD（估算标准差）——
        # 原依赖 pandas 默认值，换 numpy.std()/显式 ddof=0 会静默变口径
        std = close.rolling(window=period).std(ddof=1)
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        u = float(upper.iloc[-1])
        m = float(mid.iloc[-1])
        l = float(lower.iloc[-1])
        return (round(u, 2) if pd.notna(u) else None,
                round(m, 2) if pd.notna(m) else None,
                round(l, 2) if pd.notna(l) else None)

    @staticmethod
    def _calc_kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3):
        """计算KDJ"""
        low_list = df['low'].astype(float).rolling(window=n, min_periods=1).min()
        high_list = df['high'].astype(float).rolling(window=n, min_periods=1).max()
        close = df['close'].astype(float)
        rsv = (close - low_list) / (high_list - low_list) * 100
        # 第三轮审查 C06 复核结论（2026-08-29 实测）：单日一字涨停/跌停时窗口含前日
        # 价格区间，分母>0，RSV 自然=100/0（实测连板 K=94.85/4.73，极值正确）；
        # NaN 只在"整个窗口全平"时出现，而那正是无波动的平盘——50=中性语义正确。
        # fillna(50) 保留不动。若未来想区分"连续同价一字板"方向，需另行设计。
        rsv = rsv.fillna(50)
        k = rsv.ewm(com=m1 - 1, adjust=False).mean()
        d = k.ewm(com=m2 - 1, adjust=False).mean()
        j = 3 * k - 2 * d
        return round(float(k.iloc[-1]), 2), round(float(d.iloc[-1]), 2), round(float(j.iloc[-1]), 2)

    
    @staticmethod
    def _calc_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """计算 Average True Range

        ATR 基于截止日前的历史数据计算，使用 shift(1) 避免前视偏差：
        true_range = max(high-low, |high-prev_close|, |low-prev_close|)
        """
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        close = df['close'].astype(float)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        if len(true_range) < period:
            return None
        if _use_legacy_sma_indicators():
            atr = true_range.rolling(window=period).mean().iloc[-1]
        else:
            # v0.8.7.7 C02 修复：通达信 EXPMEMA（Wilder）口径，原 SMA(TR) 偏差 10.1%
            atr = true_range.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]
        return round(float(atr), 2) if pd.notna(atr) else None

    @staticmethod
    def _shift_date(date_str: str, days: int) -> str:
        """日期偏移"""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        shifted = dt + timedelta(days=days)
        return shifted.strftime("%Y-%m-%d")
