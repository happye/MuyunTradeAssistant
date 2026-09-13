"""恐慌指数 scope 解析。

v0.8.10（P1）仅支持 market 全市场；个股/板块/主题词解析框架先行，
实际计算在后续阶段接线，当前返回明确的「未上线」提示，不返回半成品数据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Scope:
    kind: str            # market / stock / sector
    value: str           # 代码或主题词（market 为空串）
    supported: bool      # 当前版本是否支持计算
    message: str = ""    # 不支持时给用户的人话提示


_CODE_RE = re.compile(r"^\d{6}(\.(sh|sz|SH|SZ))?$")


def parse_scope(text: str) -> Scope:
    t = (text or "").strip()
    if not t or t.lower() == "market":
        return Scope("market", "", True)
    if _CODE_RE.match(t):
        return Scope(
            "stock", t, False,
            message="个股情绪温度在后续版本上线，当前仅支持全市场：直接运行 fear")
    return Scope(
        "sector", t, False,
        message="板块/概念下钻在后续版本上线，当前仅支持全市场：直接运行 fear")
