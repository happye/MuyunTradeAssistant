"""恐慌指数聚合器：可用成分加权合成总分，缺失成分剔除权重并显式展示。

聚合规则（方案 §3.4）：
    总分 = Σ(可用成分得分 × 权重) / Σ(可用成分权重)
缺失的成分不参与（绝不补默认分 50）；全部缺失返回 None。
"""
from __future__ import annotations

from typing import Optional

from .normalizer import clamp_score
from .metrics import MetricValue

# 默认等权（CNN 框架惯例）；configs/fear_index.yaml 可覆盖
DEFAULT_WEIGHTS = {
    "breadth": 1.0,
    "zt_heat": 1.0,
    "volatility": 1.0,
    "momentum": 1.0,
    "turnover": 1.0,
    "margin": 1.0,
    "erp": 1.0,
}


def aggregate(components: list[MetricValue],
              weights: Optional[dict] = None) -> tuple[Optional[float], list[str], list[str]]:
    """加权合成恐慌总分。

    Returns:
        (score 0-100 或 None, used 成分名列表, missing 成分名列表)
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in DEFAULT_WEIGHTS})
    num = denom = 0.0
    used, missing = [], []
    for c in components:
        if not c.in_aggregate:
            continue
        if c.score is None:
            missing.append(c.name)
            continue
        weight = w.get(c.name, 1.0)
        if weight <= 0:
            missing.append(c.name)
            continue
        num += c.score * weight
        denom += weight
        used.append(c.name)
    if denom <= 0:
        return None, used, missing
    return clamp_score(num / denom), used, missing


# 档位映射（0-100，越高越恐慌；方案 §3.1）
TIERS = [
    (20.0, "极度贪婪", "#d9534f"),
    (40.0, "贪婪", "#f0ad4e"),
    (60.0, "中性", "#9e9e9e"),
    (80.0, "恐慌", "#5b9bd5"),
    (float("inf"), "极度恐慌", "#2e6da4"),
]


def tier_of(score: Optional[float]) -> str:
    if score is None:
        return "无法评估"
    for upper, name, _ in TIERS:
        if score < upper:
            return name
    return "极度恐慌"


def tier_color(score: Optional[float]) -> str:
    if score is None:
        return "grey"
    for upper, _, color in TIERS:
        if score < upper:
            return color
    return "#2e6da4"
