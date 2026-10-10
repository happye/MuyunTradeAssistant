"""历史数据回放器 - 回测框架专用

从Baostock获取历史K线，对每个交易日重建完整的StockData（含技术指标+大盘数据）。
与akshare_client.calculate_indicators不同，本模块可以"回到过去"：
  - 输入截止日期 → 返回该日期的完整技术指标
  - 均线/RSI/MACD等基于截止日期前的历史数据计算
  - 大盘趋势也基于截止日期的指数数据判断
"""

import bisect as _bisect

import numpy as np
import pandas as pd
import baostock as bs
import logging
from datetime import datetime, timedelta
from typing import Optional

from src.data.models import StockData
from src.data.akshare_client import _call_with_timeout
from concurrent.futures import TimeoutError as _FuturesTimeout


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
        # v0.8.9.5（彻查批 P2-1）：向量化预计算缓存（见 _ensure_precomputed）
        self._precomputed = False
        self._date_pos: dict[str, int] = {}
        self._tf: dict[str, dict] = {}

    def load(self) -> bool:
        """加载历史数据（个股K线 + 大盘指数）

        一次性拉取所有数据，后续iterate不再网络请求。

        Returns:
            bool: 是否加载成功
        """
        try:
            # 抑制 baostock "login success!/logout failed!" 直接打印噪声（同 akshare_client）
            from src.data.akshare_client import _quiet_baostock_print
            with _quiet_baostock_print():
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
                with _quiet_baostock_print():
                    bs.logout()
                return False

            # 加载大盘指数
            self._index_df = self._fetch_index_kline(calc_start, self.end_date)

            with _quiet_baostock_print():
                bs.logout()

            # v0.8.9.5（P2-1）：显式按日期排序（旧逐bar切片隐式依赖 baostock 返回
            # 的升序；显式排序让 bisect 定位与旧 cutoff.iloc[-1] 语义在有序输入下
            # 完全一致，乱序输入则修正为"最新一行=最大日期"）
            self._stock_df = self._stock_df.sort_values("date").reset_index(drop=True)
            if self._index_df is not None and not self._index_df.empty:
                self._index_df = self._index_df.sort_values("date").reset_index(drop=True)

            # 筛选回测区间内的交易日期
            mask = (self._stock_df['date'] >= self.start_date) & (self._stock_df['date'] <= self.end_date)
            self._dates = self._stock_df.loc[mask, 'date'].tolist()

            # v0.8.9.5（P2-1）：一次性向量化预计算全部指标（O(n)，替代逐bar
            # 对全量切片重算的 O(n²)；因果滤波全序列取行与截断重算数值一致，
            # 等价性由 tests/backtest/test_datafeeder_vectorized_equiv.py 锁死）
            self._ensure_precomputed()

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

    # ===== v0.8.9.5（P2-1）向量化预计算 =====
    # 旧实现逐 bar 对 cutoff 切片重算全部指标 = O(n²)（实测 15.7ms/bar，
    # 5年回测单股 18.8s 纯数据构建）。所有指标均为因果滤波（rolling/ewm 只依赖
    # 过去数据），全序列一次算完再按行取值与逐 bar 截断重算数值完全一致
    # （tests/backtest/test_datafeeder_vectorized_equiv.py 逐字段逐 bar 锁死）。

    def _ensure_precomputed(self):
        """懒初始化预计算（load() 已调过则跳过；兼容测试直接注入 _stock_df 的用法）。

        全部状态用 getattr 防御式读取：既有测试会用 object.__new__ 构造后直接
        注入 _stock_df/_index_df（不经 __init__）。个股与指数各自独立预计算——
        只注入其一的测试（如 d04 行为锁）也能得到正确结果。
        """
        if getattr(self, "_precomputed", False):
            return
        sdf = getattr(self, "_stock_df", None)
        if sdf is not None and not sdf.empty:
            self._stock_df = sdf.sort_values("date").reset_index(drop=True)
            self._precompute_stock_columns()
            self._precompute_timeframe_buckets()
            self._stock_precomputed = True
        idf = getattr(self, "_index_df", None)
        if idf is not None and not idf.empty:
            self._index_df = idf.sort_values("date").reset_index(drop=True)
            self._precompute_index_columns()
        self._precomputed = True

    def _precompute_stock_columns(self):
        df = self._stock_df
        close = df['close'].astype(float)
        high = df['high'].astype(float) if 'high' in df.columns else None
        low = df['low'].astype(float) if 'low' in df.columns else None
        vol = df['volume'].astype(float)
        legacy = _use_legacy_sma_indicators()

        # 均线（round 2，与 _safe_rolling 口径一致）
        for w, name in ((5, 'MA5'), (10, 'MA10'), (20, 'MA20'), (60, 'MA60'), (120, 'MA120')):
            df[name] = close.rolling(w).mean().round(2)
        # 20日均量（旧实现 _safe_rolling(vol,20)，round 2）
        df['AVGVOL20'] = vol.rolling(20).mean().round(2)

        # RSI（Wilder/legacy 双口径，与 _calc_rsi 一致）
        # 旧实现外层守卫 len(cutoff)>=24 才算全部三个 RSI（不足时三个全 None），
        # 向量化按行取值必须复刻该统一边界（而非各自 period），否则次新股
        # 前 24 根内 rsi_6/rsi_12 会提前生效（审查 P2 实测确认的分歧）
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        n = len(df)
        for period, col in ((6, 'RSI6'), (12, 'RSI12'), (24, 'RSI24')):
            if legacy:
                ag = gain.rolling(window=period).mean()
                al = loss.rolling(window=period).mean()
            else:
                ag = gain.ewm(alpha=1.0 / period, adjust=False).mean()
                al = loss.ewm(alpha=1.0 / period, adjust=False).mean()
            rsi = (100 - (100 / (1 + ag / al))).astype(float).round(2)
            # 统一旧外层守卫：len<24 全 None；len>=24 时前 23 行 None（覆盖 period<24 的提前生效）
            if n < 24:
                rsi.iloc[:] = np.nan
            else:
                rsi.iloc[:23] = np.nan
            df[col] = rsi

        # MACD（round 3；len<26 → None）
        ema_fast = close.ewm(span=12, adjust=False).mean()
        ema_slow = close.ewm(span=26, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=9, adjust=False).mean()
        hist = (dif - dea) * 2
        df['MACD_DIF'] = dif.round(3)
        df['MACD_DEA'] = dea.round(3)
        df['MACD_HIST'] = hist.round(3)
        if n < 26:
            df.loc[:, ['MACD_DIF', 'MACD_DEA', 'MACD_HIST']] = np.nan
        else:
            df.loc[df.index[:25], ['MACD_DIF', 'MACD_DEA', 'MACD_HIST']] = np.nan

        # 布林带（rolling 自带 NaN<20；ddof=1 与 _calc_boll 一致；round 2）
        mid = close.rolling(20).mean()
        std = close.rolling(20).std(ddof=1)
        df['BOLL_U'] = (mid + 2 * std).round(2)
        df['BOLL_M'] = mid.round(2)
        df['BOLL_L'] = (mid - 2 * std).round(2)

        # KDJ（与 _calc_kdj 一致；len<9 → None）
        if high is not None and low is not None:
            low_list = low.rolling(window=9, min_periods=1).min()
            high_list = high.rolling(window=9, min_periods=1).max()
            rsv = ((close - low_list) / (high_list - low_list) * 100).fillna(50)
            k = rsv.ewm(com=2, adjust=False).mean()
            d = k.ewm(com=2, adjust=False).mean()
            df['KDJ_K'] = k.round(2)
            df['KDJ_D'] = d.round(2)
            df['KDJ_J'] = (3 * k - 2 * d).round(2)
            if n < 9:
                df.loc[:, ['KDJ_K', 'KDJ_D', 'KDJ_J']] = np.nan
            else:
                df.loc[df.index[:8], ['KDJ_K', 'KDJ_D', 'KDJ_J']] = np.nan

            # ATR（与 _calc_atr 一致；len<14 → None；round 2）
            prev_close = close.shift(1)
            tr = pd.concat(
                [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1)
            if legacy:
                atr = tr.rolling(window=14).mean()
            else:
                atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
            atr = atr.astype(float).round(2)
            if n < 14:
                atr.iloc[:] = np.nan
            elif not legacy:
                atr.iloc[:13] = np.nan
            df['ATR14'] = atr

            # 60/120 日高低点（rolling max/min 自带 NaN<窗口，与旧守卫一致）
            df['HIGH60'] = high.rolling(60).max()
            df['LOW60'] = low.rolling(60).min()
            df['HIGH120'] = high.rolling(120).max()
            df['LOW120'] = low.rolling(120).min()

        # 当日涨跌幅（旧口径：len>=2 且 prev>0；round 2）
        prev_close = close.shift(1)
        chg = ((close - prev_close) / prev_close * 100).round(2)
        chg[prev_close <= 0] = np.nan
        df['CHG_PCT'] = chg

        # 日期→行号（bisect 定位，O(log n)）
        self._date_pos = {d: i for i, d in enumerate(df['date'].tolist())}
        self._dates_sorted = df['date'].tolist()

    def _precompute_index_columns(self):
        """指数侧预计算（_get_index_at_date 按行取值用；独立于个股——测试可能只注入指数）。"""
        idx = self._index_df
        iclose = idx['close'].astype(float)
        idx['IDX_MA20'] = iclose.rolling(20).mean().round(2)
        idx['IDX_MA60'] = iclose.rolling(60).mean().round(2)
        idx['IDX_MA250'] = iclose.rolling(250).mean().round(2)
        if 'high' in idx.columns:
            idx['IDX_HIGH250'] = idx['high'].astype(float).rolling(250).max()
        iprev = iclose.shift(1)
        ichg = ((iclose - iprev) / iprev * 100).round(2)
        ichg[iprev <= 0] = np.nan
        idx['IDX_CHG'] = ichg
        # 日期升序列表缓存（_get_index_at_date 每 bar bisect 用，避免现场建 list）
        self._index_dates = idx['date'].tolist()

    def _precompute_timeframe_buckets(self):
        """周/月桶预计算（完整桶来自全量 resample；当前桶逐 bar 用行号切片）。"""
        df = self._stock_df
        if not hasattr(self, "_tf"):
            self._tf = {}
        dt = pd.to_datetime(df['date'])
        for kind, freq in (("weekly", "W-FRI"), ("monthly", "ME")):
            if kind == "weekly":
                labels = dt + pd.to_timedelta((4 - dt.dt.weekday) % 7, unit="D")
            else:
                labels = dt.dt.to_period("M").dt.to_timestamp(how="end").dt.normalize()
            df[f"_bucket_{kind}"] = labels
            agg = (
                df.set_index(dt)
                .resample(freq)
                .agg({'open': 'first', 'high': 'max', 'low': 'min',
                      'close': 'last', 'volume': 'sum'})
                .dropna(subset=['close'])
            )
            nn = agg['close'].notna()
            changes = labels.ne(labels.shift())
            first_idx = np.where(changes)[0]
            bucket_first = np.zeros(len(df), dtype=int)
            bucket_first[first_idx] = first_idx
            bucket_first = np.maximum.accumulate(bucket_first)
            self._tf[kind] = {
                "row_labels": labels.values,          # 每行所属桶标签
                "bucket_first": bucket_first,          # 每行所属桶首行号
                "nonnull_labels": agg.index[nn].values,  # 非空桶标签（升序）
                "nonnull_closes": agg['close'][nn].astype(float).values,
            }

    def _build_timeframe_snapshot(self, cutoff: pd.DataFrame, freq: str) -> Optional[dict]:
        """将截止当前交易日的日线聚合为周线或月线摘要。

        v0.8.9.5（P2-1）：逐 bar 重算的参照实现——生产路径已改走
        _timeframe_snapshot_at（预计算+当前桶切片，O(桶大小)/bar），本方法保留
        作为 tests/backtest/test_datafeeder_vectorized_equiv.py 的旧口径基准，
        不得删除（等价性回归依赖它）。
        """
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

    def _timeframe_snapshot_at(self, pos: int, kind: str) -> Optional[dict]:
        """pos 行的周/月快照——与旧 _build_timeframe_snapshot(cutoff) 数值等价。

        完整桶（早于当前桶）用全量 resample 预计算结果；当前桶用 ≤pos 行现算。
        """
        tf = self._tf.get(kind)
        if tf is None:
            return None
        df = self._stock_df
        current_label = tf["row_labels"][pos]
        first_row = int(tf["bucket_first"][pos])
        sl = df.iloc[first_row:pos + 1]
        # 当前桶 close 全 NaN 的防御守卫（生产 fetch 已 dropna close 不可达；
        # 测试直接注入脏数据时不崩。注意与旧参照实现的取舍差异：旧实现
        # dropna 后会返回上一完整桶的快照，本守卫返回 None——宁缺勿错）
        close_col = sl['close'].dropna()
        if close_col.empty:
            return None
        partial_close = float(close_col.iloc[-1])
        k = int(np.searchsorted(tf["nonnull_labels"], current_label, side="left"))
        closes = list(tf["nonnull_closes"][:k]) + [partial_close]

        def _ma(window):
            if len(closes) < window:
                return None
            return round(float(np.mean(closes[-window:])), 2)

        ma5, ma10, ma20 = _ma(5), _ma(10), _ma(20)
        close_val = round(partial_close, 2)

        trend = 'NEUTRAL'
        if ma20 is not None:
            if close_val > ma20 and (ma5 is None or ma10 is None or ma5 >= ma10):
                trend = 'BULLISH'
            elif close_val < ma20 and (ma5 is None or ma10 is None or ma5 <= ma10):
                trend = 'BEARISH'

        return {
            'close': close_val,
            'ma5': ma5,
            'ma10': ma10,
            'ma20': ma20,
            'trend': trend,
        }

    def _build_stock_data(self, date: str) -> Optional[StockData]:
        """构建指定日期的完整StockData（v0.8.9.5 向量化版，数值与旧逐bar版等价）。

        核心逻辑：
        1. bisect 定位 ≤date 的最后一行（旧实现为 O(n) 布尔切片，此处 O(log n)）
        2. 指标从预计算列按行取值（因果滤波，与截断重算等价）
        3. 大盘趋势与周/月快照同样走预计算
        """
        # 注意：_ensure_precomputed 会替换 _stock_df 对象（排序+新增指标列），
        # 引用必须在预计算之后获取
        self._ensure_precomputed()
        if not getattr(self, "_stock_precomputed", False):
            return None
        df = self._stock_df
        if df is None:
            return None

        pos = _bisect.bisect_right(self._dates_sorted, date) - 1
        if pos < 0 or pos >= len(df):
            return None
        cutoff_len = pos + 1
        if cutoff_len < 5:
            return None

        row = df.iloc[pos]

        def _num(col):
            v = row[col] if col in df.columns else np.nan
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return None
            return fv if np.isfinite(fv) else None

        price = float(row['close'])
        volume = int(float(row['volume']))
        change_pct = _num('CHG_PCT')

        weekly_snapshot = self._timeframe_snapshot_at(pos, "weekly")
        monthly_snapshot = self._timeframe_snapshot_at(pos, "monthly")
        (index_trend, index_ma20, index_ma60, index_ma250, index_close,
         index_change_pct, index_high_250d) = self._get_index_at_date(date)

        return StockData(
            stock_code=self.stock_code,
            stock_name=self._stock_name,
            price=price,
            open=_num('open'),
            high=_num('high'),
            low=_num('low'),
            change_pct=change_pct,
            volume=volume,
            ma5=_num('MA5'), ma10=_num('MA10'), ma20=_num('MA20'),
            ma60=_num('MA60'), ma120=_num('MA120'),
            avg_volume_20=_num('AVGVOL20'),
            high_60d=_num('HIGH60'), low_60d=_num('LOW60'),
            high_120d=_num('HIGH120'), low_120d=_num('LOW120'),
            macd_dif=_num('MACD_DIF'), macd_dea=_num('MACD_DEA'), macd_hist=_num('MACD_HIST'),
            rsi_6=_num('RSI6'), rsi_12=_num('RSI12'), rsi_24=_num('RSI24'),
            boll_upper=_num('BOLL_U'), boll_mid=_num('BOLL_M'), boll_lower=_num('BOLL_L'),
            kdj_k=_num('KDJ_K'), kdj_d=_num('KDJ_D'), kdj_j=_num('KDJ_J'),
            atr_14=_num('ATR14'),
            index_trend=index_trend,
            index_ma20=index_ma20,
            index_ma60=index_ma60,
            index_ma250=index_ma250,
            index_close=index_close,
            index_change_pct=index_change_pct,
            index_high_250d=index_high_250d,
            weekly=weekly_snapshot,
            monthly=monthly_snapshot,
            # ISS-117 Q5/P0-2：bar 日期即 point-in-time as_of——消费边界资格门
            # （authorize_hard_findings）据此核验三倍定律等 bar 衍生硬信号的新鲜度。
            # 不填则 data_quality=UNKNOWN，回测中既有硬纪律会被资格门静默降权。
            quote_as_of=date,
        )

    def _get_index_at_date(self, date: str) -> tuple:
        """获取指定日期的大盘趋势数据（v0.8.9.5 向量化版，数值与旧版等价）。

        Returns:
            (trend, ma20, ma60, ma250, close, change_pct, high_250d)
        """
        idx_df = self._index_df
        if idx_df is None or idx_df.empty:
            return None, None, None, None, None, None, None
        # 引用在预计算之后获取（_ensure_precomputed 会替换 _index_df 对象）
        self._ensure_precomputed()
        idx = self._index_df
        # v0.8.9.5（审查 P3）：bisect 用预计算缓存的日期列表（原现场 list(idx['date'])
        # 每 bar 重建 O(n)，与向量化初衷相悖；缓存列表同时消掉死属性 _index_date_pos）
        pos = _bisect.bisect_right(getattr(self, "_index_dates", None) or list(idx['date']), date) - 1
        if pos < 0 or pos + 1 < 20:
            return None, None, None, None, None, None, None
        row = idx.iloc[pos]

        def _num(col):
            if col not in idx.columns:
                return None
            v = row[col]
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return None
            return fv if np.isfinite(fv) else None

        ma20 = _num('IDX_MA20')
        ma60 = _num('IDX_MA60')
        ma250 = _num('IDX_MA250')
        latest_close = float(row['close'])
        change_pct = _num('IDX_CHG')
        high_250d = _num('IDX_HIGH250')

        # 趋势判断（与旧版逐字一致：无 ma250 分支）
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

            # v0.8.9.5（彻查批 P1-2）：bs.next() 读取包线程级硬超时——本函数是
            # 回测唯一网络入口，socket 挂起会冻结整个回测（ISS-047 同类点收尾）
            def _read_rows():
                rows = []
                while (rs.error_code == '0') and rs.next():
                    rows.append(rs.get_row_data())
                return rows

            try:
                rows = _call_with_timeout(_read_rows, timeout=30)
            except _FuturesTimeout:
                logger.warning(f"Baostock 读取个股K线超时 {self.stock_code}，回测数据加载失败")
                return None

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

            # v0.8.9.5（彻查批 P1-2）：同上，指数K线读取也包超时
            def _read_rows():
                rows = []
                while (rs.error_code == '0') and rs.next():
                    rows.append(rs.get_row_data())
                return rows

            try:
                rows = _call_with_timeout(_read_rows, timeout=30)
            except _FuturesTimeout:
                logger.warning("Baostock 读取大盘指数K线超时，大盘趋势走降级")
                return None

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
        """安全计算滚动均值

        v0.8.7.8 审计修复 D01（第四轮审查 ISS-072）：
        原实现在 `len(series) >= max(3, window // 2)` 时用"现有数据的均值"兜底，
        于是字段名叫 ma60 却装着 30 根均线的伪值。而 live 路径
        （akshare_client 用 `rolling(w).mean()`，min_periods 默认 = w）不足 w 根
        直接为 None —— 同一个字段回测有值、实盘无值，回测信号无法迁移实盘。
        这是本项目第 5 次复发同类不对称（ma120 / ATR / high_120d / high_60d / 本处）。

        实测暴露面（600519 2024，预热 320 自然日 ≈ 216 根）：
        ma60/ma120 靠预热躲过，仅 index_ma250 前 34 根 bar（占 14%）受影响。
        """
        if len(series) < window:
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
