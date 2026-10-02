"""来源时点解析与未来判定（O1/Y3 收敛：估值门与影子门共用同一口径）。

此前两处各自解析：估值门（portfolio._parse_source_time，N2 起）严格解析、
naive 按交易所时区；影子门（shadow_diff._parse_cutoff）naive 只比日期——
同一输入两判（R14 Y3 实证：当天未来 naive 行情可过影子资格）。本模块收敛
为小型纯函数供两个消费者共用；单文件无状态纯函数，不建新架构层。

语义（与 N2 估值门口径逐条对齐）：
- aware 带偏移 → 归一 Asia/Shanghai（同一实际时刻不同偏移等价）
- naive 带时刻（新浪本地墙钟形态）→ 显式按 Asia/Shanghai 解释——按实际时刻
  比较（当天未来不再漏判，Y3）
- 纯日期（YYYY-MM-DD，日历合法）→ 当日 00:00 上海（日精度——按日期判未来，
  不伪造盘中时点）
- 非法日历（2026-02-30）/非法时刻（99:99:99）/垃圾尾巴/空 → None（明确未知
  ——不截取坏字符串救回日期）
"""

from datetime import datetime
from typing import Optional, Tuple


def parse_source_time(raw) -> Optional[Tuple[datetime, bool]]:
    """来源时点**严格解析**：返回 (交易所时区 aware datetime, 是否时刻精度)；
    非法/缺失/垃圾 → None（资格函数按缺口处理）。"""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        from zoneinfo import ZoneInfo
        sh = ZoneInfo("Asia/Shanghai")
    except Exception:
        return None
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None  # 非法日历（如 2026-02-30）
        return datetime(d.year, d.month, d.day, tzinfo=sh), False
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        dt = (dt.replace(tzinfo=sh) if dt.tzinfo is None else dt.astimezone(sh))
    except Exception:
        return None
    return dt, True


def source_time_in_future(raw, as_of: datetime) -> bool:
    """时点是否晚于 as_of（实际时刻比较；资格门共用——Y3 收敛）。

    - 时刻精度（含 naive 按上海解释）→ aware 实际时刻比较——当天未来不漏判
    - 日精度 → 按上海日期比较（日精度不假造盘中时点）
    - 不可解析 → False（不算未来——坏值不冒充时点，资格函数另行按
      unparsable 降级；与 naive 墙钟基准不可得时同保守方向）"""
    parsed = parse_source_time(raw)
    if parsed is None:
        return False
    dt, has_time = parsed
    try:
        from zoneinfo import ZoneInfo
        sh = ZoneInfo("Asia/Shanghai")
        cutoff = (as_of.replace(tzinfo=sh) if as_of.tzinfo is None
                  else as_of.astimezone(sh))
    except Exception:
        # 比较基准归一不可得且 as_of 是 naive——无法确定实际时刻，保守不判未来
        return False if as_of.tzinfo is None else (dt > as_of if has_time
                                                   else dt.date() > as_of.date())
    if has_time:
        return dt > cutoff
    return dt.date() > cutoff.date()
