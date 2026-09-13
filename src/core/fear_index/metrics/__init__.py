"""恐慌指数指标层：MetricValue 数据结构 + 市场级(L0)成分计算。

成分统一输出 MetricValue：raw 为原始值、score 为 0-100 恐慌分（None=缺失）、
status/source/note 支撑成分级溯源与显式降级（方案 P-可溯源/P-显式降级）。
计算层零 AI 调用；数据缺失标 MISSING，绝不补默认分。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MetricValue:
    """单个恐慌成分的计算结果。"""
    name: str                    # 机读名（breadth/zt_heat/...）
    label: str                   # 展示名
    raw: Optional[float]         # 原始值（口径见 note）
    score: Optional[float]       # 0-100 恐慌分；None=缺失（不进聚合）
    status: str = "OK"           # OK / STALE / MISSING
    source: str = ""             # 数据源标签
    data_ts: str = ""            # 数据基准时刻（ISO）
    note: str = ""               # 口径/降级说明（如"分位样本不足，暂用固定阈值口径"）
    in_aggregate: bool = True    # False=仅展示不进总分（如涨停家数）
    extra: dict = field(default_factory=dict)  # 附加展示信息

    def as_dict(self) -> dict:
        return {
            "name": self.name, "label": self.label, "raw": self.raw,
            "score": None if self.score is None else round(self.score, 1),
            "status": self.status, "source": self.source, "data_ts": self.data_ts,
            "note": self.note, "in_aggregate": self.in_aggregate, "extra": self.extra,
        }


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
