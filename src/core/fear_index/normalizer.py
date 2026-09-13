"""恐慌指数归一化器：所有成分统一映射为 0-100「恐慌分」（越高越恐慌）。

双模式（方案 docs/2026-09-13_市场恐慌指数_评估与实现方案.md §3.3）：
- percentile：当日值在自身近 N 交易日序列中的分位（滚动窗口，历史回放时由调用方
  保证 point-in-time——窗口只含截至当日的样本，本模块不裁剪）
- threshold：固定阈值分段线性插值（CNN 式），历史样本不足时的显式兜底

方向约定：每个成分由调用方声明 higher_is_panic，统一换算为「越高越恐慌」，
聚合层不再关心方向。序列样本不足时返回 None，由调用方显式降级并标注，
绝不静默混口径（方案 P-显式降级）。
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

# 滚动分位最少样本数：低于此值 percentile 返回 None（调用方降级 threshold 并标注）
MIN_PCT_SAMPLES = 60

# 默认分位窗口（交易日）
PCT_WINDOW = 250


def _is_finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def pct_rank(value: float, series: Sequence[float], window: int = PCT_WINDOW,
             min_samples: int = MIN_PCT_SAMPLES) -> Optional[float]:
    """value 在 series 末 window 个有效样本中的百分位（0-100）。

    series 按时间升序；样本（去 None/非有限值后）不足 min_samples 返回 None。
    """
    vals = [float(x) for x in series if _is_finite(x)][-window:]
    if len(vals) < min_samples:
        return None
    v = float(value)
    below = sum(1 for x in vals if x <= v)
    return below / len(vals) * 100.0


def percentile_panic(
    value: float,
    series: Sequence[float],
    higher_is_panic: bool = True,
    window: int = PCT_WINDOW,
    min_samples: int = MIN_PCT_SAMPLES,
) -> Optional[float]:
    """分位归一化：higher_is_panic=True 时恐慌分=分位，否则=100-分位。样本不足返回 None。

    min_samples 可按成分特例放宽（如涨停池历史接口仅保留约30个交易日，打板成分
    用 15 起评），调用方须在 note 中如实标注口径。
    """
    r = pct_rank(value, series, window, min_samples)
    if r is None:
        return None
    return r if higher_is_panic else 100.0 - r


def threshold_panic(value: float, stops: Sequence[tuple]) -> float:
    """固定阈值分段线性插值。

    stops: [(x_i, panic_score_i)] 按 x 严格升序给出若干锚点（方向已折算进 panic_score），
    两端外推取端点分。x 需为有限值，忽略非法锚点。
    """
    pts = sorted(
        (float(x), float(s)) for x, s in stops
        if _is_finite(x) and _is_finite(s)
    )
    if not pts:
        return 50.0
    v = float(value)
    if v <= pts[0][0]:
        return pts[0][1]
    if v >= pts[-1][0]:
        return pts[-1][1]
    for (x0, s0), (x1, s1) in zip(pts, pts[1:]):
        if x1 == x0:
            continue
        if x0 <= v <= x1:
            w = (v - x0) / (x1 - x0)
            return s0 + w * (s1 - s0)
    return 50.0


def clamp_score(x: float) -> float:
    """恐慌分截断到 [0, 100]，防插值/均值运算浮点溢出。"""
    return max(0.0, min(100.0, float(x)))
