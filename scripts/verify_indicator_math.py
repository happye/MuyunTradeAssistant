# -*- coding: utf-8 -*-
"""
技术指标公式数学正确性验证（第三轮对抗审查 C 区块）

不是"读代码看像不像对"，而是用已知输入算已知输出，跟权威参考实现逐项比对。

跑法：
    .\\.venv\\Scripts\\python.exe scripts\\verify_indicator_math.py

判定口径：
    - 与通达信/同花顺（A股主流口径）不一致  -> FAIL
    - 与回测路径（data_feeder）不一致        -> FAIL（回测结论无法迁移到实盘）
    - 边界产生 NaN/inf                        -> WARN
"""
import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.data_feeder import DataFeeder  # noqa: E402

FAILS = []
WARNS = []
OKS = []


def fail(item, detail):
    FAILS.append((item, detail))


def warn(item, detail):
    WARNS.append((item, detail))


def ok(item, detail=""):
    OKS.append((item, detail))


# ── 参考实现（权威口径）─────────────────────────────────────────────

def wilder_rma(series, period):
    """Wilder 平滑（通达信 SMA(X,N,1) / TradingView RMA）：alpha = 1/N"""
    alpha = 1.0 / period
    out = series.copy().astype(float)
    result = np.empty(len(out))
    prev = np.nan
    for i, v in enumerate(out.values):
        if np.isnan(prev):
            prev = v if not np.isnan(v) else 0.0
        else:
            prev = prev + alpha * ((0.0 if np.isnan(v) else v) - prev)
        result[i] = prev
    return pd.Series(result, index=out.index)


def ref_rsi_wilder(close, period):
    """通达信/TradingView 标准 RSI：Wilder 平滑的涨跌幅均值"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ref_rsi_sma(close, period):
    """本项目当前实现：简单移动平均"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ref_ema_tdx(series, n):
    """通达信 EMA(X,N) = alpha 2/(N+1) 递归，首值 seeded"""
    return series.ewm(span=n, adjust=False).mean()


def ref_kdj_tdx(high, low, close, n=9, m1=3, m2=3):
    """通达信 KDJ：K=SMA(RSV,M1,1)，SMA(X,N,1) 即 alpha=1/N 的 Wilder 平滑"""
    llv = low.rolling(window=n, min_periods=1).min()
    hhv = high.rolling(window=n, min_periods=1).max()
    rsv = (close - llv) / (hhv - llv) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()
    d = k.ewm(alpha=1.0 / m2, adjust=False).mean()
    return k, d, 3 * k - 2 * d


def ref_atr_wilder(high, low, close, period=14):
    """Wilder ATR（原始定义 / 多数国外平台口径）"""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


# ── 构造测试数据 ────────────────────────────────────────────────────

def make_df(n=260, seed=7):
    """造一段带趋势和波动的 OHLC，模拟真实 A 股日线"""
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0004, 0.022, n)
    close = 20.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0.006, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0.006, 0.005, n)))
    high = np.maximum(high, np.maximum(close, low))
    low = np.minimum(low, np.minimum(close, high))
    return pd.DataFrame({"close": close, "high": high, "low": low})


def main():
    df = make_df()
    close = df["close"]
    high = df["high"]
    low = df["low"]

    print("=" * 78)
    print("技术指标公式数学正确性验证")
    print("=" * 78)

    # ── 1. RSI ──────────────────────────────────────────────────────
    # v0.8.7.7 C01 修复后本项目即为 Wilder 口径（MUYUN_INDICATOR_LEGACY=1 可切回旧 SMA 对照）
    print("\n[1] RSI：本项目（Wilder，已修复）vs 通达信/TradingView Wilder 口径")
    got = DataFeeder._calc_rsi(df.rename(columns={}), periods=[6, 12, 24])
    # _calc_rsi 期望 'close' 列
    got = DataFeeder._calc_rsi(df, periods=[6, 12, 24])
    for idx, period in enumerate([6, 12, 24]):
        ours = got[idx]
        sma_val = float(ref_rsi_sma(close, period).iloc[-1])
        wilder_val = float(ref_rsi_wilder(close, period).iloc[-1])
        delta = abs(wilder_val - sma_val)
        print(f"  RSI-{period:<3} 本项目(Wilder)={ours:>7.2f}  "
              f"Wilder={wilder_val:>7.2f}  (旧SMA={sma_val:.2f}, 两口径差={delta:.2f})")
        # 修复后：本项目必须与 Wilder 一致（否则 C01 复发）；与旧 SMA 不同是预期
        if abs(ours - wilder_val) > 0.011:
            fail(f"RSI-{period}", f"实现与 Wilder 口径不一致 (ours={ours}, wilder={wilder_val:.2f})——C01 回归？")
        if delta > 3.0:
            warn(f"RSI-{period}",
                 f"旧SMA 与 Wilder 差 {delta:.2f} 点（提示两口径本就系统性不同，非本项目问题）")
    if not any(f[0].startswith("RSI") for f in FAILS):
        ok("RSI Wilder 口径", "三周期均与通达信/TradingView Wilder 一致（C01 已修复；旧口径可 MUYUN_INDICATOR_LEGACY=1 复现）")

    # ── 2. MACD ─────────────────────────────────────────────────────
    print("\n[2] MACD：EMA 口径 + 柱状图是否 *2（通达信为 *2）")
    dif, dea, hist = DataFeeder._calc_macd(df)
    ema12 = ref_ema_tdx(close, 12)
    ema26 = ref_ema_tdx(close, 26)
    ref_dif = ema12 - ema26
    ref_dea = ref_dif.ewm(span=9, adjust=False).mean()
    ref_hist_tdx = (ref_dif - ref_dea) * 2
    print(f"  DIF={dif}  DEA={dea}  HIST={hist}")
    print(f"  参考 DIF={ref_dif.iloc[-1]:.3f}  DEA={ref_dea.iloc[-1]:.3f}  "
          f"HIST(通达信*2)={ref_hist_tdx.iloc[-1]:.3f}")
    if abs(dif - float(ref_dif.iloc[-1])) > 0.002:
        fail("MACD DIF", f"{dif} != {ref_dif.iloc[-1]:.3f}")
    else:
        ok("MACD DIF", "EMA(12)-EMA(26)，adjust=False，与通达信一致")
    if abs(dea - float(ref_dea.iloc[-1])) > 0.002:
        fail("MACD DEA", f"{dea} != {ref_dea.iloc[-1]:.3f}")
    else:
        ok("MACD DEA", "EMA(DIF,9)，与通达信一致")
    if abs(hist - float(ref_hist_tdx.iloc[-1])) > 0.003:
        fail("MACD HIST", f"柱状图口径 {hist} != 通达信 2*(DIF-DEA)={ref_hist_tdx.iloc[-1]:.3f}")
    else:
        ok("MACD HIST", "2*(DIF-DEA)，符合 A 股通达信口径（非国外 DIF-DEA）")

    # ── 3. BOLL 标准差 ddof ─────────────────────────────────────────
    print("\n[3] BOLL：标准差 ddof 口径（通达信 STD=估算标准差 ddof=1）")
    u, m, l = DataFeeder._calc_boll(df, 20, 2)
    mid_ref = close.rolling(20).mean()
    std_ddof1 = close.rolling(20).std(ddof=1)
    std_ddof0 = close.rolling(20).std(ddof=0)
    print(f"  本项目 U={u} M={m} L={l}")
    print(f"  ddof=1(通达信STD)  U={float((mid_ref+2*std_ddof1).iloc[-1]):.2f}")
    print(f"  ddof=0(总体/TradingView) U={float((mid_ref+2*std_ddof0).iloc[-1]):.2f}")
    if abs(u - float((mid_ref + 2 * std_ddof1).iloc[-1])) < 0.02:
        # v0.8.7.7 C05 已修复：源码已显式 std(ddof=1)，原"隐式依赖"WARN 消除
        ok("BOLL ddof", "显式 std(ddof=1)，对齐通达信 STD（估算标准差），不再依赖 pandas 默认值")
    else:
        fail("BOLL ddof", f"上轨 {u} 与 ddof=1 参考不符")

    # ── 4. KDJ 平滑系数 ─────────────────────────────────────────────
    print("\n[4] KDJ：ewm(com=m1-1) 是否等价于通达信 SMA(RSV,M1,1)")
    k, d, j = DataFeeder._calc_kdj(df)
    k_ref, d_ref, j_ref = ref_kdj_tdx(high, low, close)
    print(f"  本项目 K={k} D={d} J={j}")
    print(f"  通达信  K={float(k_ref.iloc[-1]):.2f} D={float(d_ref.iloc[-1]):.2f} "
          f"J={float(j_ref.iloc[-1]):.2f}")
    if abs(k - float(k_ref.iloc[-1])) < 0.02 and abs(d - float(d_ref.iloc[-1])) < 0.02:
        ok("KDJ 平滑", "ewm(com=m1-1) => alpha=1/m1，与通达信 SMA(X,M1,1) 完全等价")
    else:
        fail("KDJ 平滑", f"K={k} vs 通达信 {float(k_ref.iloc[-1]):.2f}")
    if abs(j - (3 * k - 2 * d)) > 0.05:
        fail("KDJ J", "J != 3K-2D")
    else:
        ok("KDJ J", "J=3K-2D 正确")

    # ── 5. ATR 平滑方式 ─────────────────────────────────────────────
    print("\n[5] ATR：本项目（Wilder/通达信 EXPMEMA，已修复）vs Wilder 参考")
    atr_ours = DataFeeder._calc_atr(df, 14)
    atr_wilder = float(ref_atr_wilder(high, low, close, 14).iloc[-1])
    diff_pct = abs(atr_ours - atr_wilder) / atr_wilder * 100
    print(f"  本项目(Wilder)={atr_ours}  Wilder参考={atr_wilder:.2f}  偏差={diff_pct:.1f}%")
    if diff_pct > 10:
        fail("ATR 口径", f"与 Wilder ATR 偏差 {diff_pct:.1f}%（>10%），"
                        f"用户在通达信看到的 ATR 与本系统不一致——C02 回归？")
    elif diff_pct > 5:
        warn("ATR 口径", f"与 Wilder ATR 偏差 {diff_pct:.1f}%")
    else:
        ok("ATR 口径", "与 Wilder/通达信 EXPMEMA 一致（C02 已修复；旧口径可 MUYUN_INDICATOR_LEGACY=1 复现）")

    # ── 6. 边界：NaN / inf ──────────────────────────────────────────
    print("\n[6] 边界情形")

    # 6a. 全平窗口（high==low==close，RSV 分母为 0）+ 单日一字板方向实测（C06 复核）
    flat = pd.DataFrame({
        "close": [10.0] * 40, "high": [10.0] * 40, "low": [10.0] * 40,
    })
    try:
        kf, df_, jf = DataFeeder._calc_kdj(flat)
        print(f"  全平窗口(40日)  K={kf} D={df_} J={jf}")
        if kf != 50.0:
            fail("KDJ 全平窗口", f"窗口全平时 K={kf}，应为中性 50")
        else:
            # C06 复核（2026-08-29）：单日一字板时窗口含前日区间，RSV 自然=100/0，
            # 不会走 fillna；全平窗口 50=中性是正确语义。下方方向实测为证。
            ok("KDJ 全平窗口", "50=中性，语义正确（C06 假阳性已复核）")
        # 单日一字涨停方向实测：3 连一字板后 K 应进超买区（非 50）
        rows, p = [], 10.0
        for _ in range(30):
            rows.append({"close": p, "high": p * 1.02, "low": p * 0.98})
            p *= 1.01
        for _ in range(3):
            p *= 1.10
            rows.append({"close": p, "high": p, "low": p})
        limit_up = pd.DataFrame(rows)
        kl, dl, jl = DataFeeder._calc_kdj(limit_up)
        print(f"  3连一字涨停后   K={kl} D={dl} J={jl}")
        if kl is None or kl <= 80:
            fail("KDJ 一字板方向", f"一字涨停后 K={kl}，应为超买（>80）——极值信号丢失")
        else:
            ok("KDJ 一字板方向", f"一字涨停 K={kl}（超买区），极值信号未丢失（C06 复核通过）")
    except Exception as e:
        fail("KDJ 一字板", f"抛异常: {e}")

    # 6b. RSI 连续上涨（avg_loss==0 -> 除零）
    up = pd.DataFrame({
        "close": list(np.linspace(10, 30, 60)),
        "high": list(np.linspace(10, 30, 60) + 0.1),
        "low": list(np.linspace(10, 30, 60) - 0.1),
    })
    up["high"] = up[["high", "close", "low"]].max(axis=1)
    up["low"] = up[["high", "close", "low"]].min(axis=1)
    r = DataFeeder._calc_rsi(up, periods=[6, 12, 24])
    print(f"  连续上涨(涨跌幅恒定) RSI={r}")
    if any(v is None for v in r):
        ok("RSI 除零(回测路径)", "avg_loss=0 时回测路径返回 None，有防护")
    elif any(isinstance(v, float) and np.isnan(v) for v in r):
        fail("RSI 除零", "回测路径返回 NaN 而非 None")
    else:
        print("     （涨跌幅恒定时 gain/loss 均非 0，未触发除零）")

    # 6c. RSI 完全无波动（gain=0 且 loss=0 -> 0/0）
    flat2 = pd.DataFrame({
        "close": [10.0] * 60, "high": [10.05] * 60, "low": [9.95] * 60,
    })
    r2 = DataFeeder._calc_rsi(flat2, periods=[6, 12, 24])
    print(f"  完全无波动 RSI={r2}")
    if any(v is None for v in r2):
        ok("RSI 0/0(回测路径)", "返回 None，有防护")
    elif any(isinstance(v, float) and np.isnan(v) for v in r2):
        fail("RSI 0/0", f"返回 NaN 而非 None: {r2}")
    else:
        ok("RSI 0/0", f"返回有限值 {r2}")

    # ── 7. 汇总 ─────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"通过 {len(OKS)} 项 | 警告 {len(WARNS)} 项 | 失败 {len(FAILS)} 项")
    print("=" * 78)
    if FAILS:
        print("\n【FAIL】")
        for item, detail in FAILS:
            print(f"  - {item}: {detail}")
    if WARNS:
        print("\n【WARN】")
        for item, detail in WARNS:
            print(f"  - {item}: {detail}")
    if OKS:
        print("\n【OK】")
        for item, detail in OKS:
            print(f"  - {item}: {detail}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
