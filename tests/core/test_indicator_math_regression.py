# -*- coding: utf-8 -*-
"""技术指标公式回归测试（第三轮审查 C 区块，ISS-071）

锁死 v0.8.7.7 的修复：
- C01: RSI = Wilder RMA（通达信 SMA(X,N,1)），live/回测/rsi_6_series 三处同口径
- C02: ATR = Wilder/通达信 EXPMEMA，live/回测同口径
- C03: live 路径 RSI/MACD/BOLL/KDJ 无波动数据返回 None 而非 nan
- C04/C07: high_120d 守卫 120、high_60d 守卫 60（live/回测对称）
- C05: BOLL 显式 ddof=1（通达信 STD）
- C06 复核: 单日一字板 RSV 自然取极值（K 进超买/超卖区），全平窗口 50=中性

跑法：pytest tests/core/test_indicator_math_regression.py
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
import pandas as pd
import pytest

from src.data.data_feeder import DataFeeder
from src.data.akshare_client import AKShareClient


# ── 参考实现（通达信/Wilder 权威口径）─────────────────────────────

def ref_rsi_wilder(close: pd.Series, period: int) -> pd.Series:
    """Wilder RMA：alpha=1/N（通达信 SMA(X,N,1)）"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ref_atr_wilder(high, low, close, period=14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def make_df(n=300, seed=42):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n))))
    high = close * (1 + np.abs(rng.normal(0.006, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0.006, 0.005, n)))
    high = np.maximum(high, np.maximum(close, low))
    low = np.minimum(low, np.minimum(close, high))
    return pd.DataFrame({"close": close, "high": high, "low": low})


def to_cn(df):
    """英文列名 → live 路径的中文列名"""
    return df.rename(columns={"close": "收盘", "high": "最高", "low": "最低"})


# ── C01: RSI Wilder 口径（live 与回测双路径）──────────────────────

@pytest.mark.parametrize("period", [6, 12, 24])
def test_rsi_wilder_backtest_path(period):
    df = make_df()
    ours = DataFeeder._calc_rsi(df, periods=[6, 12, 24])[[6, 12, 24].index(period)]
    ref = float(ref_rsi_wilder(df["close"], period).iloc[-1])
    assert ours is not None and abs(ours - ref) < 0.02, f"RSI-{period} 偏离 Wilder: {ours} vs {ref}"


@pytest.mark.parametrize("period", [6, 12, 24])
def test_rsi_wilder_live_path(period):
    df = make_df()
    ours = AKShareClient._calculate_rsi(to_cn(df), n=[6, 12, 24])[[6, 12, 24].index(period)]
    ref = float(ref_rsi_wilder(df["close"], period).iloc[-1])
    assert ours is not None and abs(ours - ref) < 0.02, f"live RSI-{period} 偏离 Wilder: {ours} vs {ref}"


def test_rsi_live_backtest_consistent():
    """live 与回测两套实现同数据必须同值（C01 同类点锁死）"""
    df = make_df()
    bt = DataFeeder._calc_rsi(df, periods=[6, 12, 24])
    live = AKShareClient._calculate_rsi(to_cn(df), n=[6, 12, 24])
    for b, l in zip(bt, live):
        assert b is not None and l is not None and abs(b - l) < 0.01, f"live/回测 RSI 不一致: {l} vs {b}"


# ── C02: ATR Wilder 口径 ─────────────────────────────────────────

def test_atr_wilder_backtest_path():
    df = make_df()
    ours = DataFeeder._calc_atr(df, 14)
    ref = float(ref_atr_wilder(df["high"], df["low"], df["close"], 14).iloc[-1])
    assert ours is not None and abs(ours - ref) / ref < 0.005, f"ATR 偏离 Wilder: {ours} vs {ref}"


def test_atr_wilder_live_path():
    df = make_df()
    ours = AKShareClient._calculate_atr(to_cn(df), 14)
    ref = float(ref_atr_wilder(df["high"], df["low"], df["close"], 14).iloc[-1])
    assert ours is not None and abs(ours - ref) / ref < 0.005, f"live ATR 偏离 Wilder: {ours} vs {ref}"


# ── C03: 无波动/脏数据返回 None 而非 nan ─────────────────────────

def test_rsi_live_flat_returns_none_not_nan():
    """连续停牌/全天横盘 gain=loss=0 → 0/0。live 路径必须返回 None（与回测对齐），
    原裸返回 nan 会被 skill_engine 的 `is not None` 守卫放行 → 信号静默蒸发"""
    flat = pd.DataFrame({"收盘": [10.0] * 60})
    r6, r12, r24 = AKShareClient._calculate_rsi(flat, n=[6, 12, 24])
    assert r6 is None and r12 is None and r24 is None
    assert not any(isinstance(v, float) and np.isnan(v) for v in (r6, r12, r24))


def test_live_macd_boll_kdj_no_nan_on_dirty_data():
    dirty = pd.DataFrame({"收盘": [10.0] * 40, "最高": [10.0] * 40, "最低": [10.0] * 40})
    macd = AKShareClient._calculate_macd(dirty)
    boll = AKShareClient._calculate_boll(dirty)
    kdj = AKShareClient._calculate_kdj(dirty)
    for name, trio in (("MACD", macd), ("BOLL", boll), ("KDJ", kdj)):
        assert not any(isinstance(v, float) and np.isnan(v) for v in trio), f"{name} 裸返回 nan: {trio}"


# ── C04/C07: 窗口守卫（借 __new__ 构造最小 DataFeeder，不触网络）──

def _feeder_with_klines(n_rows, end="2024-06-28"):
    f = DataFeeder.__new__(DataFeeder)
    f.stock_code = "600000"
    f.bs_code = "sh.600000"
    f._stock_name = "测试"
    dates = pd.bdate_range(end=end, periods=n_rows).strftime("%Y-%m-%d")
    f._stock_df = pd.DataFrame({
        "date": dates, "code": "sh.600000",
        "open": [10.0] * n_rows, "high": [10.5] * n_rows,
        "low": [9.5] * n_rows, "close": [10.0] * n_rows,
        "volume": [1e6] * n_rows, "amount": [1e7] * n_rows,
    })
    f._index_df = None
    return f


def test_high60d_guard_requires_60_bars():
    """C07: 守卫 >=20 → >=60。59 根K线时不得给伪 60 日高点"""
    f59 = _feeder_with_klines(59)
    sd59 = f59._build_stock_data("2024-06-28")
    assert sd59 is not None and sd59.high_60d is None, "59根K线不应有 high_60d"
    f60 = _feeder_with_klines(60)
    sd60 = f60._build_stock_data("2024-06-28")
    assert sd60 is not None and sd60.high_60d is not None, "60根K线应有 high_60d"


def test_high120d_guard_requires_120_bars():
    """C04: 回测守卫 >=60 → >=120，对齐 live。119 根时不得有 high_120d"""
    f119 = _feeder_with_klines(119)
    sd119 = f119._build_stock_data("2024-06-28")
    assert sd119 is not None and sd119.high_120d is None, "119根K线不应有 high_120d（live/回测对称）"
    f120 = _feeder_with_klines(120)
    sd120 = f120._build_stock_data("2024-06-28")
    assert sd120 is not None and sd120.high_120d is not None, "120根K线应有 high_120d"


# ── C05: BOLL 显式 ddof=1 ────────────────────────────────────────

def test_boll_explicit_ddof1():
    df = make_df()
    u, m, l = DataFeeder._calc_boll(df, 20, 2)
    mid = df["close"].rolling(20).mean()
    std1 = df["close"].rolling(20).std(ddof=1)
    std0 = df["close"].rolling(20).std(ddof=0)
    upper1 = float((mid + 2 * std1).iloc[-1])
    upper0 = float((mid + 2 * std0).iloc[-1])
    assert abs(u - upper1) < 0.02, f"BOLL 上轨偏离 ddof=1: {u} vs {upper1}"
    assert abs(u - upper0) > 0.001, "ddof=1 与 ddof=0 参考重合，测试失去区分度"


# ── C06 复核: 一字板极值信号不丢失 + 全平中性 ────────────────────

def test_kdj_limit_up_reaches_overbought():
    """C06 复核：单日一字板时窗口含前日区间，RSV 自然=100，K 进超买区（非中性 50）"""
    rows, p = [], 10.0
    for _ in range(30):
        rows.append({"close": p, "high": p * 1.02, "low": p * 0.98})
        p *= 1.01
    for _ in range(3):
        p *= 1.10
        rows.append({"close": p, "high": p, "low": p})
    k, d, j = DataFeeder._calc_kdj(pd.DataFrame(rows))
    assert k > 80, f"一字涨停后 K={k}，应为超买（C06 假阳性回归锁）"


def test_kdj_flat_window_neutral():
    flat = pd.DataFrame({"close": [10.0] * 40, "high": [10.0] * 40, "low": [10.0] * 40})
    k, d, j = DataFeeder._calc_kdj(flat)
    assert k == 50.0, f"全平窗口 K 应为中性 50，实际 {k}"


# ── A/B 开关：MUYUN_INDICATOR_LEGACY=1 可复现旧 SMA 口径 ─────────

def test_legacy_switch_restores_sma(monkeypatch):
    df = make_df()
    monkeypatch.setenv("MUYUN_INDICATOR_LEGACY", "1")
    ours_sma = DataFeeder._calc_rsi(df, periods=[6, 12, 24])[1]
    delta = close_sma = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    ref_sma = float((100 - 100 / (1 + gain.rolling(12).mean() / loss.rolling(12).mean())).iloc[-1])
    assert abs(ours_sma - ref_sma) < 0.02, "LEGACY 开关未恢复 SMA 口径"
    monkeypatch.delenv("MUYUN_INDICATOR_LEGACY")
    ours_wilder = DataFeeder._calc_rsi(df, periods=[6, 12, 24])[1]
    ref_wilder = float(ref_rsi_wilder(df["close"], 12).iloc[-1])
    assert abs(ours_wilder - ref_wilder) < 0.02, "默认应为 Wilder 口径"
