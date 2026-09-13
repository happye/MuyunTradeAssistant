# -*- coding: utf-8 -*-
"""DataFeeder 向量化（P2-1，v0.8.9.5）数值等价性回归。

v0.8.9.5 把逐 bar 全量切片重算指标的 O(n²) 实现改为"全序列一次预计算 + 按行取值"。
所有指标均为因果滤波（rolling/ewm 只依赖过去数据），两种实现必须逐字段逐 bar 数值
完全一致。本测试用多组随机 OHLCV（含缺口/停牌日/跨周跨月边界）对照：
- 日线指标：旧口径直接用保留下来的 _safe_rolling/_calc_* 静态方法在截断序列上现算
- 周/月快照：旧口径用保留的 _build_timeframe_snapshot(cutoff) 参照实现
- 大盘：旧 _get_index_at_date 逻辑在本文件内按原实现复刻对照

若本测试失败，禁止发布——回测行为基线（ISS-074 口径）会被破坏。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pandas as pd

from src.data.data_feeder import DataFeeder


def _make_ohlcv(rng, n):
    close = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.02, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.012, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.012, n)))
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    dates = pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d")
    # 制造缺口：随机删 5% 的交易日（停牌），再重新编号日期制造跳空
    keep = rng.random(n) > 0.05
    dates = np.array(dates)[keep]
    return pd.DataFrame({
        "date": dates,
        "open": np.asarray(open_)[keep],
        "high": np.asarray(high)[keep],
        "low": np.asarray(low)[keep],
        "close": np.asarray(close)[keep],
        "volume": volume[keep],
        "amount": (np.asarray(close)[keep] * volume[keep]),
    })


def _old_build_reference(feeder: DataFeeder, date: str):
    """按 v0.8.9.5 之前的 _build_stock_data 原实现逐字段复刻（对照基准）。"""
    df = feeder._stock_df
    cutoff = df[df['date'] <= date].copy()
    if len(cutoff) < 5:
        return None
    latest = cutoff.iloc[-1]
    price = float(latest['close'])
    volume = int(float(latest['volume']))
    change_pct = None
    if len(cutoff) >= 2 and float(cutoff.iloc[-2]['close']) > 0:
        prev_close = float(cutoff.iloc[-2]['close'])
        change_pct = round((price - prev_close) / prev_close * 100, 2)
    close = cutoff['close'].astype(float)
    vol = cutoff['volume'].astype(float)
    recent_60 = cutoff.tail(60)
    recent_120 = cutoff.tail(120)
    out = {
        "price": price, "volume": volume, "change_pct": change_pct,
        "open": float(latest['open']) if pd.notna(latest['open']) else None,
        "high": float(latest['high']) if pd.notna(latest['high']) else None,
        "low": float(latest['low']) if pd.notna(latest['low']) else None,
        "ma5": DataFeeder._safe_rolling(close, 5),
        "ma10": DataFeeder._safe_rolling(close, 10),
        "ma20": DataFeeder._safe_rolling(close, 20),
        "ma60": DataFeeder._safe_rolling(close, 60),
        "ma120": DataFeeder._safe_rolling(close, 120),
        "avg_volume_20": DataFeeder._safe_rolling(vol, 20),
        "high_60d": float(recent_60['high'].astype(float).max()) if len(recent_60) >= 60 else None,
        "low_60d": float(recent_60['low'].astype(float).min()) if len(recent_60) >= 60 else None,
        "high_120d": float(recent_120['high'].astype(float).max()) if len(recent_120) >= 120 else None,
        "low_120d": float(recent_120['low'].astype(float).min()) if len(recent_120) >= 120 else None,
        "macd_dif": None, "macd_dea": None, "macd_hist": None,
        "rsi_6": None, "rsi_12": None, "rsi_24": None,
        "boll_upper": None, "boll_mid": None, "boll_lower": None,
        "kdj_k": None, "kdj_d": None, "kdj_j": None,
        "atr_14": None,
    }
    if len(cutoff) >= 26:
        out["macd_dif"], out["macd_dea"], out["macd_hist"] = DataFeeder._calc_macd(cutoff)
    if len(cutoff) >= 24:
        out["rsi_6"], out["rsi_12"], out["rsi_24"] = DataFeeder._calc_rsi(cutoff)
    if len(cutoff) >= 20:
        out["boll_upper"], out["boll_mid"], out["boll_lower"] = DataFeeder._calc_boll(cutoff)
    if len(cutoff) >= 9:
        out["kdj_k"], out["kdj_d"], out["kdj_j"] = DataFeeder._calc_kdj(cutoff)
    if len(cutoff) >= 14:
        out["atr_14"] = DataFeeder._calc_atr(cutoff, period=14)
    out["weekly"] = feeder._build_timeframe_snapshot(cutoff, freq="W-FRI")
    out["monthly"] = feeder._build_timeframe_snapshot(cutoff, freq="ME")
    return out


def _old_index_reference(feeder: DataFeeder, date: str):
    """按 v0.8.9.5 之前的 _get_index_at_date 原实现复刻（对照基准）。"""
    idx = feeder._index_df
    if idx is None or idx.empty:
        return (None, None, None, None, None, None, None)
    sel = idx[idx['date'] <= date]
    if len(sel) < 20:
        return (None, None, None, None, None, None, None)
    close = sel['close'].astype(float)
    ma20 = DataFeeder._safe_rolling(close, 20)
    ma60 = DataFeeder._safe_rolling(close, 60)
    ma250 = DataFeeder._safe_rolling(close, 250)
    latest_close = float(close.iloc[-1])
    high_250d = None
    if 'high' in sel.columns:
        recent_250 = sel.tail(250)
        if len(recent_250) >= 250:
            high_250d = float(recent_250['high'].astype(float).max())
    change_pct = None
    if len(sel) >= 2:
        prev = float(close.iloc[-2])
        if prev > 0:
            change_pct = round((latest_close - prev) / prev * 100, 2)
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
    return (trend, ma20, ma60, ma250, latest_close, change_pct, high_250d)


_FIELDS = [
    "price", "volume", "change_pct", "open", "high", "low",
    "ma5", "ma10", "ma20", "ma60", "ma120", "avg_volume_20",
    "high_60d", "low_60d", "high_120d", "low_120d",
    "macd_dif", "macd_dea", "macd_hist",
    "rsi_6", "rsi_12", "rsi_24",
    "boll_upper", "boll_mid", "boll_lower",
    "kdj_k", "kdj_d", "kdj_j", "atr_14",
]

_TF_KEYS = ["close", "ma5", "ma10", "ma20", "trend"]
_IDX_KEYS = ["index_trend", "index_ma20", "index_ma60", "index_ma250",
             "index_close", "index_change_pct", "index_high_250d"]


def _build_feeder(stock_df, index_df, start, end):
    feeder = DataFeeder("600519", start, end)
    feeder._stock_df = stock_df
    feeder._index_df = index_df
    mask = (stock_df['date'] >= start) & (stock_df['date'] <= end)
    feeder._dates = stock_df.loc[mask, 'date'].tolist()
    return feeder


def _compare_case(seed, use_legacy):
    # env 开关必须恢复：MUYUN_INDICATOR_LEGACY 泄漏会让后续指标数学回归
    # （Wilder 默认口径）在进程内全挂（全量跑实测）
    old_env = os.environ.get("MUYUN_INDICATOR_LEGACY")
    if use_legacy:
        os.environ["MUYUN_INDICATOR_LEGACY"] = "1"
    else:
        os.environ.pop("MUYUN_INDICATOR_LEGACY", None)
    try:
        return _compare_case_inner(seed, use_legacy)
    finally:
        if old_env is None:
            os.environ.pop("MUYUN_INDICATOR_LEGACY", None)
        else:
            os.environ["MUYUN_INDICATOR_LEGACY"] = old_env


def _compare_case_inner(seed, use_legacy):
    rng = np.random.default_rng(seed)
    n = 420
    stock_df = _make_ohlcv(rng, n)
    index_df = stock_df.copy()
    # 起点取第 7 根（cutoff_len>=5 的最小可行区），覆盖指标预热区——
    # 审查 P2 实测：从 100 起比对会漏掉 RSI 等在预热区的掩码分歧
    start, end = str(stock_df['date'].iloc[6]), str(stock_df['date'].iloc[-1])
    feeder = _build_feeder(stock_df, index_df, start, end)

    checked = 0
    for date in feeder._dates:
        new_sd = feeder._build_stock_data(date)
        ref = _old_build_reference(feeder, date)
        assert (new_sd is None) == (ref is None), f"seed={seed} date={date} None 分歧"
        if new_sd is None:
            continue
        dump = new_sd.model_dump()
        for f in _FIELDS:
            got, exp = dump.get(f), ref.get(f)
            if got is None and exp is None:
                continue
            assert got is not None and exp is not None, \
                f"seed={seed} legacy={use_legacy} date={date} field={f}: None 分歧 got={got} exp={exp}"
            assert abs(float(got) - float(exp)) <= 1e-9, \
                f"seed={seed} legacy={use_legacy} date={date} field={f}: got={got} exp={exp}"
        for tf_key in ("weekly", "monthly"):
            got_tf, exp_tf = dump.get(tf_key), ref.get(tf_key)
            assert (got_tf is None) == (exp_tf is None), \
                f"seed={seed} legacy={use_legacy} date={date} {tf_key} None 分歧"
            if got_tf is None:
                continue
            for k in _TF_KEYS:
                g, e = got_tf.get(k), exp_tf.get(k)
                if g is None or e is None:
                    assert g == e, f"seed={seed} {tf_key}.{k} None 分歧"
                elif isinstance(g, str) or isinstance(e, str):
                    assert g == e, f"seed={seed} {tf_key}.{k}: got={g} exp={e}"
                else:
                    assert abs(float(g) - float(e)) <= 1e-9, \
                        f"seed={seed} legacy={use_legacy} date={date} {tf_key}.{k}: got={g} exp={e}"
        idx_new = feeder._get_index_at_date(date)
        idx_old = _old_index_reference(feeder, date)
        for i, k in enumerate(_IDX_KEYS):
            g, e = idx_new[i], idx_old[i]
            if g is None or e is None:
                assert g == e, f"seed={seed} legacy={use_legacy} date={date} {k}: None 分歧 got={g} exp={e}"
            elif isinstance(g, str) or isinstance(e, str):
                assert g == e, f"seed={seed} legacy={use_legacy} date={date} {k}: got={g} exp={e}"
            else:
                assert abs(float(g) - float(e)) <= 1e-9, \
                    f"seed={seed} legacy={use_legacy} date={date} {k}: got={g} exp={e}"
        checked += 1
    return checked


def test_vectorized_equivalence_wilder_multi_seed():
    total = 0
    for seed in (7, 42, 2026):
        total += _compare_case(seed, use_legacy=False)
    assert total > 300, f"等价性校验bar数不足: {total}"


def test_vectorized_equivalence_legacy_sma():
    total = _compare_case(99, use_legacy=True)
    assert total > 100


if __name__ == "__main__":
    import time
    t0 = time.time()
    n1 = _compare_case(7, use_legacy=False)
    n2 = _compare_case(42, use_legacy=False)
    n3 = _compare_case(2026, use_legacy=False)
    n4 = _compare_case(99, use_legacy=True)
    print(f"PASS: {n1 + n2 + n3 + n4} bars 逐字段等价（含 legacy 口径），耗时 {time.time()-t0:.1f}s")
