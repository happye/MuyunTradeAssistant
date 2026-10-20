"""板块成分股替代源（THS/东财概念板块接口失败时使用）。

只用真实数据：新浪行情中心的申万行业节点（sw1_/sw2_/sw3_）成分股。
- 常用主题词 → 申万行业节点的静态映射（军工/电力/煤炭/石油油气/电网设备/银行/半导体…）
- 未命中静态映射时，按新浪节点名模糊匹配（节点表 getHQNodes 走磁盘缓存）
- 成分股列表经 robust_fetch.robust_call 磁盘缓存（ttl 1 天，过期兜底 30 天）
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from src.data import robust_fetch as rf

logger = logging.getLogger(__name__)

_API = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center."

# 主题词 → [(新浪申万节点, 显示名)]
THEME_TO_SW: dict[str, list[tuple[str, str]]] = {
    "军工": [("sw1_650000", "国防军工")],
    "国防军工": [("sw1_650000", "国防军工")],
    "电力": [("sw2_410100", "电力")],
    "煤炭": [("sw1_740000", "煤炭")],
    "石油": [("sw1_750000", "石油石化")],
    "油气": [("sw2_750100", "油气开采Ⅱ"), ("sw2_750200", "油服工程")],
    "石油石化": [("sw1_750000", "石油石化")],
    "电网设备": [("sw2_630800", "电网设备")],
    "电网": [("sw2_630800", "电网设备")],
    "特高压": [("sw2_630800", "电网设备")],
    "银行": [("sw1_480000", "银行")],
    "半导体": [("sw2_270100", "半导体")],
    "芯片": [("sw2_270100", "半导体")],
}

LAST_SOURCE: dict = {}


def _fetch_nodes() -> list[tuple[str, str]]:
    r = rf._http_get(_API + "getHQNodes", timeout=8.0)
    data = json.loads(r.content.decode("gbk", errors="ignore"))
    out: list[tuple[str, str]] = []

    def walk(n):
        if isinstance(n, list):
            if len(n) >= 3 and isinstance(n[0], str) and isinstance(n[2], str) and n[2].startswith("sw"):
                out.append((n[2], n[0]))
            for x in n:
                walk(x)
    walk(data)
    return out


def _fetch_members(node: str) -> list[str]:
    codes: list[str] = []
    for page in range(1, 20):
        r = rf._http_get(_API + f"getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node={node}", timeout=6.0)
        rows = json.loads(r.content.decode("gbk", errors="ignore") or "[]") or []
        codes += [str(x.get("code")) for x in rows if x.get("code")]
        if len(rows) < 100:
            break
    return sorted(set(codes))


def resolve_nodes(keywords: list[str], node_fetcher: Optional[Callable] = None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    rest = []
    for kw in keywords:
        kw = str(kw or "").strip()
        if not kw:
            continue
        hit = THEME_TO_SW.get(kw)
        if hit:
            found += hit
        else:
            rest.append(kw)
    if rest:
        nodes = rf.robust_call("sina_sw_nodes", "all", node_fetcher or _fetch_nodes,
                               ttl=7 * 86400, stale_max=90 * 86400, quiet=True) or []
        for kw in rest:
            m = [(c, n) for c, n in nodes if kw in n]
            m.sort(key=lambda x: x[0])  # sw1 < sw2 < sw3：优先粗粒度
            found += m[:2]
    return list(dict.fromkeys(found))


def alt_theme_members(keywords: list[str], member_fetcher: Optional[Callable] = None,
                      node_fetcher: Optional[Callable] = None) -> tuple[set[str], list[str]]:
    """返回 (成分股代码集合, 使用的板块名列表)。全部失败返回 (set(), [])。"""
    codes: set[str] = set()
    used: list[str] = []
    fetch = member_fetcher or _fetch_members
    for node, name in resolve_nodes(keywords, node_fetcher):
        v = rf.robust_call("sina_sw_members", node, lambda n=node: fetch(n),
                           ttl=86400, stale_max=30 * 86400, timeout=20.0)
        if v:
            codes.update(v)
            used.append(f"申万·{name}({rf.LAST_STATUS.get(('sina_sw_members', node))})")
    LAST_SOURCE.clear()
    LAST_SOURCE.update({"keywords": list(keywords), "boards": used, "count": len(codes)})
    return codes, used
