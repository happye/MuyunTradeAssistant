"""市场级(L0)恐慌成分计算：7 成分 = 6 进聚合 + 涨跌停家数展示。

方向口径（方案定稿，全部单调，聚合层不再关心方向）：
  breadth    上涨家数占比   占比高=贪婪        -> percentile(反向)/threshold 兜底
  zt_heat    跌停数+炸板率  越高越恐慌         -> percentile 正向，两子项均值
  volatility 沪深300 20日波动 越高越恐慌        -> percentile 正向
  momentum   MA125 乖离率%  乖离高=贪婪        -> threshold 锚点
  turnover   两市成交额     分位低=冰点恐慌    -> percentile 反向
  margin     融资余额5日变化 加杠杆=贪婪        -> percentile 反向
  erp        100/PE-10Y     利差大=恐慌底部    -> percentile 正向
  (展示) zt_count/dt_count/zb_rate/turnover_value 不进聚合

纪律：当日与历史同源同口径（vol/mom 走 baostock 序列；margin/erp 走整段
历史序列末值，T-1 慢变量）；breadth/turnover/zt 为日内实时，data_ts 如实标注。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from . import MetricValue, _now_iso
from ..history import (
    _BREADTH_STOPS, _MOM_STOPS, INDEX_CODE, load_snapshots, load_zt_series,
    _load_series, _series_map, fetch_zt_counts,
)
from ..normalizer import MIN_PCT_SAMPLES, clamp_score, pct_rank, percentile_panic, threshold_panic

logger = logging.getLogger(__name__)


def _vol20_series(closes: pd.Series) -> pd.Series:
    return (closes.pct_change() * 100.0).rolling(20).std()


def _margin_chg_series(balance: pd.Series) -> pd.Series:
    return balance.pct_change(5) * 100.0


def compute_market_components(baseline: str, refresh: bool = False) -> list[MetricValue]:
    """计算基准交易日 baseline 的全部市场级成分。任何单项失败只降级自身。"""
    baseline_ymd = baseline.replace("-", "")
    out: list[MetricValue] = []

    # ── 1. 涨跌广度（新浪全市场快照，日内实时） ──
    out.append(_metric_breadth(baseline))

    # ── 2. 打板情绪（东财涨停池） ──
    out.append(_metric_zt_heat(baseline, baseline_ymd, refresh=refresh))

    # ── 3/4. 波动率 + 动量（baostock 沪深300 序列，基准日收盘口径） ──
    out.extend(_metric_vol_mom(baseline))

    # ── 5. 成交热度（实时额 vs 历史序列分位） ──
    out.append(_metric_turnover(baseline))

    # ── 6. 杠杆情绪 / 7. 股债性价比（整段历史序列末值，T-1） ──
    out.append(_metric_margin())
    out.append(_metric_erp())
    return out


def _metric_breadth(baseline: str) -> MetricValue:
    m = MetricValue("breadth", "涨跌广度", None, None, source="新浪全市场快照", data_ts=_now_iso())
    try:
        from src.scanner.market_cache import MarketCache
        df = MarketCache().get_all_stocks()
        if df is None or df.empty or "涨跌幅" not in df.columns:
            m.status, m.note = "MISSING", "全市场快照不可用"
            return m
        chg = pd.to_numeric(df["涨跌幅"], errors="coerce").dropna()
        if len(chg) < 100:
            m.status, m.note = "MISSING", f"快照样本不足({len(chg)}<100)"
            return m
        up, down = int((chg > 0).sum()), int((chg < 0).sum())
        denom = up + down
        if denom <= 0:
            m.status, m.note = "MISSING", "涨跌家数均为0"
            return m
        m.raw = up / denom
        m.extra = {"up": up, "down": down, "total": int(len(chg))}
        # 分位序列 = 历史快照积累的 breadth raw；不足走阈值兜底（方案 §3.3）
        hist = [
            (snap["metrics"] or {}).get("breadth")
            for d, snap in sorted(load_snapshots().items()) if d < baseline
        ]
        hist = [v for v in hist if v is not None]
        score = percentile_panic(m.raw, hist, higher_is_panic=False)
        if score is None:
            score = threshold_panic(m.raw, _BREADTH_STOPS)
            m.note = (f"上涨占比分位样本不足({len(hist)}/{MIN_PCT_SAMPLES})，"
                      f"暂用固定阈值口径；涨{up}/跌{down}")
        else:
            m.note = f"上涨占比{m.raw:.0%}，近{len(hist)}个快照日分位；涨{up}/跌{down}"
        m.score = clamp_score(score)
    except Exception as e:
        m.status, m.note = "MISSING", f"快照获取失败: {type(e).__name__}"
        logger.warning(f"恐慌指数成分(涨跌广度)获取失败: {e}")
    return m


def _metric_zt_heat(baseline: str, baseline_ymd: str, refresh: bool = False) -> MetricValue:
    m = MetricValue("zt_heat", "打板情绪", None, None, source="东财涨停池", data_ts=_now_iso())
    try:
        counts = fetch_zt_counts(baseline_ymd)
        if counts is None:
            m.status = "MISSING"
            m.note = "涨停池数据不可用（接口失败/非交易时段/未回填），打板成分缺失"
            return m
        m.raw = float(counts["dt"]) if counts.get("dt") is not None else None
        m.extra = {"zt": counts.get("zt"), "dt": counts.get("dt"),
                   "zb": counts.get("zb"), "zb_rate": counts.get("zb_rate")}
        series = load_zt_series()
        # 含当日（自身参与比较对分位无碍）：接口保留期短，样本稀缺时差一天就差一档
        hist_dt = [r["dt"] for r in series if r.get("dt") is not None and r["date"] <= baseline]
        hist_zb = [r["zb_rate"] for r in series
                   if r.get("zb_rate") is not None and r["date"] <= baseline]
        # 涨停池历史接口仅保留约20-30个交易日（实测），min_samples 特例放宽到 15，
        # 其余成分仍走 60 门槛；note 如实标注口径
        sub, missing = [], []
        s_dt = percentile_panic(m.extra["dt"], hist_dt, higher_is_panic=True, min_samples=15)
        if s_dt is None:
            missing.append("跌停数分位样本不足")
        else:
            sub.append(s_dt)
        s_zb = (percentile_panic(m.extra["zb_rate"], hist_zb, higher_is_panic=True, min_samples=15)
                if m.extra.get("zb_rate") is not None else None)
        if s_zb is None:
            missing.append("炸板率缺失或样本不足")
        else:
            sub.append(s_zb)
        if not sub:
            m.status = "MISSING"
            m.note = "；".join(missing) + "（跑 fear backfill 回填涨停池历史）"
            return m
        m.score = clamp_score(sum(sub) / len(sub))
        m.note = (f"跌停{m.extra['dt']}家/涨停{m.extra['zt']}家"
                  + (f"/炸板率{m.extra['zb_rate']:.0f}%" if m.extra.get("zb_rate") is not None else "")
                  + ("；" + "；".join(missing) if missing and len(sub) == 1 else ""))
    except Exception as e:
        m.status, m.note = "MISSING", f"涨停池获取失败: {type(e).__name__}"
        logger.warning(f"恐慌指数成分(打板情绪)获取失败: {e}")
    return m


def _metric_vol_mom(baseline: str) -> list[MetricValue]:
    mv = MetricValue("volatility", "市场波动", None, None,
                     source=f"baostock {INDEX_CODE} 日线", data_ts=baseline)
    mm = MetricValue("momentum", "市场动量", None, None,
                     source=f"baostock {INDEX_CODE} 日线", data_ts=baseline)
    try:
        rows = _load_series("hist_index_000300.json")
        if not rows:
            for m in (mv, mm):
                m.status, m.note = "MISSING", "沪深300日线序列缺失（轻量回填失败）"
            return [mv, mm]
        dates = [r["date"] for r in rows]
        closes = pd.Series([r["close"] for r in rows], index=dates, dtype=float).sort_index()
        # sort_index 防御：文件意外乱序时 index[-1] 必须仍是最新交易日
        vol = _vol20_series(closes)
        ma125 = closes.rolling(125).mean()
        if baseline not in closes.index:
            # 收盘后-17:30 窗口：baostock 当日日线尚未就绪 → 回退序列末日计算，
            # 标 STALE（用了 T-1 收盘口径），不造假也不整成分缺失
            asof = closes.index[-1]
            if (datetime.now() - datetime.strptime(asof, "%Y-%m-%d")).days <= 7:
                for m in (mv, mm):
                    m.data_ts = asof
                    m.status = "STALE"
                    m.extra["prefix"] = f"当日日线未出(收盘后约17:30就绪)，暂按{asof}收盘口径；"
            else:
                for m in (mv, mm):
                    m.status, m.note = "MISSING", f"基准日 {baseline} 不在日线序列中且序列过期"
                return [mv, mm]
        else:
            asof = baseline
        v_now, mom_now = float(vol[asof]), float((closes[asof] / ma125[asof] - 1) * 100)
        mv.raw, mm.raw = v_now, mom_now
        s_vol = percentile_panic(v_now, vol.dropna().tolist(), higher_is_panic=True)
        if s_vol is None:
            mv.status, mv.note = "MISSING", "波动分位样本不足，成分缺失（不猜分）"
        else:
            mv.note = mv.extra.get("prefix", "")
            mv.score = clamp_score(s_vol)
            mv.note += f"20日波动率{v_now:.2f}%/日，近250日分位"
        mm.score = clamp_score(threshold_panic(mom_now, _MOM_STOPS))
        mm.note = mm.extra.get("prefix", "") + \
            f"收盘价较MA125乖离{mom_now:+.1f}%（固定阈值口径）"
    except Exception as e:
        for m in (mv, mm):
            m.status, m.note = "MISSING", f"指数序列计算失败: {type(e).__name__}"
        logger.warning(f"恐慌指数成分(波动/动量)计算失败: {e}")
    return [mv, mm]


def _metric_turnover(baseline: str) -> MetricValue:
    m = MetricValue("turnover", "成交热度", None, None,
                    source="新浪快照(实时)/指数合计(历史)", data_ts=_now_iso())
    try:
        from src.core.benzong import data_provider
        turnover = data_provider.get_market_turnover()  # 万亿，内部45s硬超时
        if turnover is None:
            m.status, m.note = "MISSING", "当日成交额获取失败"
            return m
        m.raw = turnover
        hist = _turnover_hist_series()
        score = percentile_panic(turnover, hist, higher_is_panic=False)
        if score is None:
            m.status, m.note = "STALE", "成交额分位样本不足（历史序列缺失），成分缺失"
        else:
            m.score = clamp_score(score)
            m.note = f"两市成交额{turnover:.2f}万亿，历史序列分位（冰点=恐慌/天量=贪婪）"
    except Exception as e:
        m.status, m.note = "MISSING", f"成交额获取失败: {type(e).__name__}"
        logger.warning(f"恐慌指数成分(成交热度)获取失败: {e}")
    return m


def _metric_margin() -> MetricValue:
    m = MetricValue("margin", "杠杆情绪", None, None,
                    source="东财两融宏观序列(T-1)", data_ts="")
    try:
        rows = _load_series("hist_margin.json")
        if len(rows) < 7:
            m.status, m.note = "MISSING", "融资余额序列不足（轻量回填失败）"
            return m
        bal = pd.Series([r["balance_yi"] for r in rows],
                        index=[r["date"] for r in rows], dtype=float)
        chg = _margin_chg_series(bal).dropna()
        m.raw, m.data_ts = float(chg.iloc[-1]), chg.index[-1]
        score = percentile_panic(m.raw, chg.tolist(), higher_is_panic=False)
        if score is None:
            m.status, m.note = "STALE", "融资变化分位样本不足，成分缺失"
        else:
            m.score = clamp_score(score)
            m.note = f"融资余额5日变化{m.raw:+.1f}%（截至{m.data_ts}，加杠杆=贪婪）"
    except Exception as e:
        m.status, m.note = "MISSING", f"融资序列计算失败: {type(e).__name__}"
        logger.warning(f"恐慌指数成分(杠杆情绪)计算失败: {e}")
    return m


def _metric_erp() -> MetricValue:
    m = MetricValue("erp", "股债性价比", None, None,
                    source="乐咕沪深300PE + 10Y国债(T-1)", data_ts="")
    try:
        rows = _load_series("hist_erp.json")
        if len(rows) < MIN_PCT_SAMPLES:
            m.status, m.note = "MISSING", f"风险溢价序列不足({len(rows)}<{MIN_PCT_SAMPLES})"
            return m
        vals = [(r["date"], float(r["erp_pct"])) for r in rows]
        m.raw, m.data_ts = vals[-1][1], vals[-1][0]
        score = percentile_panic(m.raw, [v for _, v in vals], higher_is_panic=True)
        if score is None:
            m.status, m.note = "STALE", "风险溢价分位样本不足，成分缺失"
        else:
            m.score = clamp_score(score)
            m.note = (f"股债风险溢价{m.raw:.2f}pct（盈利收益率-10Y国债，自算日频，"
                      f"截至{m.data_ts}），利差大=恐慌底部特征")
    except Exception as e:
        m.status, m.note = "MISSING", f"风险溢价序列计算失败: {type(e).__name__}"
        logger.warning(f"恐慌指数成分(股债性价比)计算失败: {e}")
    return m


def _turnover_hist_series() -> list[float]:
    """历史成交额序列（万亿，沪+深相加）。任一源缺该日记 None 再剔除。"""
    sh = _series_map(_load_series("hist_turnover_sh.json"), "amount")
    sz = _series_map(_load_series("hist_turnover_sz.json"), "amount")
    dates = sorted(set(sh) & set(sz))
    return [sh[d] + sz[d] for d in dates]
