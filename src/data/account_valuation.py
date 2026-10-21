"""账户 NAV 估值（AccountSnapshot.nav / nav_priced_at 的唯一生产者）。

此前 AccountService.snapshot() 只做纯文件重放，没有任何代码给 nav 赋值，导致数量账户
的 _qualified_weight 永远"缺价格或NAV"、所有持仓都显示"权重待核"，加仓也被冻结。

口径：
- NAV = 账本现金（cash_available + cash_reserved）+ Σ(数量 × 同日行情价)
- 行情来源和 l/la 定价相同：AKShareClient 批量实时行情，并写入 _QUOTE_PREFETCH，
  让随后单只 get_realtime_quote 拿到同一行情行（同价、同 effective_at）
- nav_priced_at = 各行情源时点里最晚的一个（交易所时区）；按源时点定价，不拿抓取墙钟顶替
- 保守规则（宁可未知也不给部分值）：任一持仓缺有效价格/源时点、时点在未来、
  各股不在同一交易日、现金未知、账本 PARTIAL/有隔离事件 → nav 保持 None
- 账本快照本身仍是纯文件重放；估值是在其上的一层只读计算，返回新对象，不写盘
- MUYUN_NAV_QUOTES=0 关闭联网估值（测试默认关闭，用注入的 quote_fn 覆盖）
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime
from typing import Callable, Optional

from src.core.source_time import parse_source_time

logger = logging.getLogger(__name__)

QuoteFn = Callable[[list], dict]   # [codes] -> {code: {"price": float, "effective_at": str}}


def default_quote_fn(codes: list) -> dict:
    """批量行情，并写入预取映射（l/la 随后同源命中）。"""
    from src.data.akshare_client import AKShareClient
    AKShareClient.prefetch_realtime_quotes(codes)
    out = {}
    for c in codes:
        pre = AKShareClient._QUOTE_PREFETCH.get(str(c))
        if pre is not None:
            out[str(c)] = pre[1]
    return out


def nav_quotes_enabled() -> bool:
    return os.environ.get("MUYUN_NAV_QUOTES", "1").strip().lower() not in ("0", "false", "off")


def compute_nav(snapshot, quote_fn: QuoteFn, *, as_of: Optional[datetime] = None
                ) -> tuple[Optional[float], Optional[datetime], str]:
    """返回 (nav, nav_priced_at, reason)。nav=None 时 reason 说明缺口。"""
    if snapshot is None:
        return None, None, "无账户快照"
    if snapshot.isolated_events or snapshot.data_completeness == "PARTIAL":
        return None, None, "账本存在隔离事件/PARTIAL——NAV 不估"
    if snapshot.cash_available is None:
        return None, None, "现金未知"
    if not math.isfinite(float(snapshot.cash_available)):
        return None, None, "现金非有限（未知）"
    reserved = float(snapshot.cash_reserved or 0.0)
    if not math.isfinite(reserved):
        return None, None, "冻结现金非有限（未知）"
    cash = float(snapshot.cash_available) + reserved
    held = [(h.security_id, int(h.quantity or 0)) for h in snapshot.holdings if int(h.quantity or 0) > 0]
    if not held:
        return None, None, "无数量持仓（NAV 无需估值）"
    try:
        quotes = quote_fn([c for c, _ in held]) or {}
    except Exception as e:
        return None, None, f"行情获取失败: {type(e).__name__}"
    try:
        from zoneinfo import ZoneInfo
        sh = ZoneInfo("Asia/Shanghai")
        now = as_of if as_of is not None else datetime.now(sh)
        now = now.replace(tzinfo=sh) if now.tzinfo is None else now.astimezone(sh)
    except Exception:
        now = as_of or datetime.now().astimezone()
    total = cash
    times = []
    for code, qty in held:
        q = quotes.get(code) or quotes.get(str(code))
        if not q:
            return None, None, f"{code} 缺行情"
        try:
            price = float(q.get("price"))
        except (TypeError, ValueError):
            return None, None, f"{code} 价格非法"
        if not math.isfinite(price) or price <= 0:
            return None, None, f"{code} 价格非法"
        parsed = parse_source_time(q.get("effective_at"))
        if parsed is None:
            return None, None, f"{code} 行情无源时点"
        dt, has_time = parsed
        if (dt > now) if has_time else (dt.date() > now.date()):
            return None, None, f"{code} 行情时点在未来"
        times.append(dt)
        total += qty * price
    if len({t.date() for t in times}) != 1:
        return None, None, "各持仓行情不在同一交易日——NAV 不拼接"
    return round(total, 2), max(times), "ok"


def priced_snapshot(snapshot, quote_fn: Optional[QuoteFn] = None, *, as_of: Optional[datetime] = None):
    """返回带 nav/nav_priced_at 的快照副本；已有 NAV 或估值失败时原样返回。"""
    if snapshot is None or snapshot.nav is not None:
        return snapshot
    if quote_fn is None:
        if not nav_quotes_enabled():
            return snapshot
        quote_fn = default_quote_fn
    nav, at, reason = compute_nav(snapshot, quote_fn, as_of=as_of)
    if nav is None:
        logger.info(f"NAV 未估值（权重保持待核）: {reason}")
        return snapshot
    return snapshot.model_copy(update={"nav": nav, "nav_priced_at": at})
