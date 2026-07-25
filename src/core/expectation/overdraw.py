"""预期透支度算法（环境温度计，v0.8.7 预期管理 Phase 1）

诚实降级：akshare 无一致预期值数据源，真正的"预期差"无法算。
本指标是三维度环境信号（利率/估值/短期动能），用价格反应代理预期透支。
明确标注"非预期差"，Phase 2 接入一致预期值后才升级。

算法（见 plan）：
- 利率环境：10Y国债收益率3年百分位。<20%=宽松已price in（降息预期透支）
- 估值环境：沪深300 close 3年百分位。>80%=估值偏高，利好易透支
- 短期动能：沪深300近20日涨跌幅。>10%=超买，事件落地易利好出尽
- score = 利率透支分位×0.4 + 估值透支分位×0.4 + 短期动能权重×0.2
"""

import logging
from typing import Optional

from src.data.models import ExpectationOverdraw

logger = logging.getLogger(__name__)


def _percentile(value: float, series: list[float]) -> Optional[float]:
    """value 在 series 中的百分位（0-100），series 空返回 None。"""
    if not series:
        return None
    le = sum(1 for v in series if v <= value)
    return le / len(series) * 100.0


def _rate_overdraw_score(percentile: Optional[float]) -> float:
    """利率透支分位：收益率越低（分位越低）=宽松已price in=透支越高。"""
    if percentile is None:
        return 0.0
    if percentile < 20:
        return (20 - percentile) / 20 * 100
    if percentile < 50:
        return (50 - percentile) / 30 * 50
    return 0.0


def _momentum_weight(change_20d: Optional[float]) -> float:
    """短期动能透支权重：涨幅越大越透支（超买），跌幅越大越不透支。"""
    if change_20d is None:
        return 0.0
    if change_20d > 10:
        return 80.0
    if change_20d > 5:
        return 50.0
    if change_20d > 0:
        return 30.0
    if change_20d > -10:
        return 10.0
    return 0.0


class ExpectationOverdrawCalculator:
    """预期透支度计算器（环境温度计）"""

    def calculate(self) -> ExpectationOverdraw:
        from src.data.calendar_client import CalendarClient
        rates = CalendarClient.get_cn_10y_rate_series(years=3)
        closes = CalendarClient.get_index_close_history(years=3)

        rate_val = rates[-1] if rates else None
        rate_pct = _percentile(rate_val, rates) if (rate_val is not None and rates) else None

        idx_val = closes[-1] if closes else None
        val_pct = _percentile(idx_val, closes) if (idx_val is not None and closes) else None

        # 短期动能：近20日涨跌幅（用第 -21 个 close 做基准，含20日区间）
        mom = None
        if closes and len(closes) >= 21:
            mom = (closes[-1] / closes[-21] - 1) * 100

        # 评分
        rate_score = _rate_overdraw_score(rate_pct)
        val_score = val_pct if val_pct is not None else 0.0  # 估值分位直接用（越高越透支）
        mom_w = _momentum_weight(mom)
        score = rate_score * 0.4 + val_score * 0.4 + mom_w * 0.2

        dims_ok = sum(1 for x in (rate_pct, val_pct, mom) if x is not None)
        status = "ok" if dims_ok == 3 else ("partial" if dims_ok > 0 else "failed")

        interpretation = self._build_interpretation(rate_pct, val_pct, mom, score)
        return ExpectationOverdraw(
            score=round(score, 1),
            rate_percentile=round(rate_pct, 1) if rate_pct is not None else None,
            rate_value=round(rate_val, 4) if rate_val is not None else None,
            valuation_percentile=round(val_pct, 1) if val_pct is not None else None,
            valuation_value=round(idx_val, 2) if idx_val is not None else None,
            momentum_20d=round(mom, 2) if mom is not None else None,
            interpretation=interpretation,
            data_status=status,
        )

    def _build_interpretation(
        self, rate_pct: Optional[float], val_pct: Optional[float],
        mom: Optional[float], score: float,
    ) -> str:
        parts = []
        if rate_pct is not None and rate_pct < 20:
            parts.append(f"利率已price in宽松预期（10Y处3年{rate_pct:.0f}%分位）")
        elif rate_pct is not None and rate_pct > 80:
            parts.append(f"利率偏紧（10Y处3年{rate_pct:.0f}%分位）")
        if val_pct is not None and val_pct > 80:
            parts.append(f"估值偏高（{val_pct:.0f}%分位），利好易透支")
        elif val_pct is not None and val_pct < 20:
            parts.append(f"估值偏低（{val_pct:.0f}%分位）")
        if mom is not None and mom > 10:
            parts.append(f"短期超买（+{mom:.1f}%），事件落地易利好出尽")
        if not parts:
            parts.append("环境信号中性，无明显透支")
        level = "高" if score >= 70 else ("中" if score >= 40 else "低")
        parts.append(f"-> 透支度 {score:.0f}/100（{level}）")
        return "；".join(parts)
