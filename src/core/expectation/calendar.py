"""预期事件日历聚合（v0.8.7 预期管理 Phase 1）

合并静态事件表（configs/static_events.yaml）+ akshare 财报披露日历，
输出未来 N 天的事件列表，按倒计时排序。

point-in-time 说明：
- 静态事件表：日期手动维护，point-in-time 安全
- 财报披露日历：stock_report_disclosure 返回当前最新预约，公司可能改期，
  回测用会有前瞻偏差。as_of_date 参数备 Phase 2 回测（当前用 today）。
"""

import os
import logging
from datetime import datetime, date
from typing import Optional

import yaml

from src.data.models import CalendarEvent

logger = logging.getLogger(__name__)

# ISS-080（v0.8.8.5）：过期一次性事件的自动归档宽限期（与原 stale 告警阈值一致）
_GRACE_DAYS = 7

_STATIC_EVENTS_PATH = os.path.join("configs", "static_events.yaml")


def _parse_date(s: str) -> Optional[date]:
    """解析 YYYY-MM-DD，失败返回 None"""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _next_occurrence(as_of: date, every: str, day: int) -> Optional[date]:
    """ISS-080：周期事件档期——返回宽限期内（>= as_of-7天）的下一个档期。

    monthly=每月 day 日；quarter=每季首月（1/4/7/10）day 日。day 超出当月天数
    取当月最后一天兜底。最多向后扫两年，扫不到返回 None。
    """
    months = list(range(1, 13)) if every == "monthly" else [1, 4, 7, 10] if every == "quarter" else None
    if not months:
        return None
    for delta_months in range(0, 25):
        y = as_of.year + (as_of.month - 1 + delta_months) // 12
        m = (as_of.month - 1 + delta_months) % 12 + 1
        if m not in months:
            continue
        import calendar as _cal
        last = _cal.monthrange(y, m)[1]
        c = date(y, m, min(max(1, day), last))
        if (c - as_of).days >= -_GRACE_DAYS:
            return c
    return None


def _render_recur_name(template: str, pub: date) -> str:
    """ISS-080：周期事件名占位符——{m}=数据所属月份（公布月-1，1月回绕12月），
    {q}=数据所属季度（公布月 1/4/7/10 → 上季度 4/1/2/3）。

    中国宏观数据命名惯例：8月CPI 于 9月9-11日公布、三季度GDP 于 10月中旬公布，
    故名称取"数据月/数据季"而非公布月。
    """
    data_month = pub.month - 1 or 12
    data_q = {1: 4, 4: 1, 7: 2, 10: 3}.get(pub.month)
    return (template or "").replace("{m}", str(data_month)).replace("{q}", str(data_q or ""))


class EventCalendar:
    """预期事件日历聚合器"""

    def __init__(self, as_of_date: Optional[date] = None):
        """Args: as_of_date: 基准日期（默认今天），Phase 2 回测用"""
        self._as_of = as_of_date or date.today()

    def load_static_events(self) -> list[CalendarEvent]:
        """加载静态事件表，过滤已落地，算 days_to_event。

        v0.8.8.5（ISS-080）自动化：① 过期一次性事件（超过宽限期）自动归档——
        不再需要手动标 landed；② recur 周期事件（monthly/quarter）自动滚动到
        下一个档期——不再需要每月手动加条目。旧 schema 完全兼容。
        """
        self._auto_archived = []
        if not os.path.exists(_STATIC_EVENTS_PATH):
            logger.warning("静态事件表不存在: %s", _STATIC_EVENTS_PATH)
            return []
        try:
            with open(_STATIC_EVENTS_PATH, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("静态事件表读取失败: %s", e)
            return []
        events = []
        for item in cfg.get("events", []):
            if item.get("landed", False):
                continue
            recur_every = str((item.get("recur") or "")).lower()
            if recur_every:
                # ISS-080：周期事件自动滚动
                try:
                    day = int(item.get("day", 10))
                except (TypeError, ValueError):
                    continue
                ev_date = _next_occurrence(self._as_of, recur_every, day)
                if ev_date is None:
                    continue
                window = item.get("date_window_days", item.get("window_days", 0))
                try:
                    window = int(window)
                except (TypeError, ValueError):
                    window = 0
                events.append(CalendarEvent(
                    name=_render_recur_name(item.get("name", ""), ev_date),
                    date=ev_date.isoformat(),
                    days_to_event=(ev_date - self._as_of).days,
                    event_type=item.get("type", "macro"),
                    impact=item.get("impact", "medium"),
                    source="static",
                    note=item.get("note", ""),
                    date_window_days=window,
                    landed=False,
                ))
                continue
            ev_date = _parse_date(item.get("date", ""))
            if ev_date is None:
                continue
            days_past = (self._as_of - ev_date).days
            if days_past > _GRACE_DAYS:
                # ISS-080：过期自动归档（无需手动标 landed），计数供汇总展示
                self._auto_archived.append((item.get("name", ""), days_past))
                continue
            days = (ev_date - self._as_of).days
            events.append(CalendarEvent(
                name=item.get("name", ""),
                date=ev_date.isoformat(),
                days_to_event=days,
                event_type=item.get("type", "macro"),
                impact=item.get("impact", "medium"),
                source="static",
                note=item.get("note", ""),
                date_window_days=item.get("date_window_days", 0),
                landed=False,
            ))
        return events

    def check_stale_static_events(self) -> list[str]:
        """v0.8.8.5（ISS-080）：过期事件自动归档，逐条「请更新」催办移除。

        返回至多一条汇总（有自动归档时），让用户看到自动化已生效——
        防静默：若表被清空/周期规则被误删，这里仍能从"无归档也无事件"暴露。
        """
        self.load_static_events()
        archived = getattr(self, "_auto_archived", [])
        if not archived:
            return []
        name, days = max(archived, key=lambda t: t[1])
        return [
            f"静态事件表：{len(archived)} 条过期事件已自动归档"
            f"（最近：'{name}'，{days} 天前）——过期事件无需手动维护，"
            f"周期事件自动滚动"
        ]

    def get_disclosure_events(self, codes: Optional[list[str]] = None) -> list[CalendarEvent]:
        """拉财报披露日历，可选过滤持仓股 codes。

        Args: codes: 持仓股代码列表（6位），None 则拉全部（量大，慎用）
        """
        from src.data.calendar_client import CalendarClient, current_disclosure_periods
        periods = current_disclosure_periods()
        events = []
        for period in periods:
            rows = CalendarClient.get_stock_disclosure(period)
            for row in rows:
                if codes and row["code"] not in codes:
                    continue
                ev_date = _parse_date(row.get("disclose_date", ""))
                if ev_date is None:
                    continue
                days = (ev_date - self._as_of).days
                events.append(CalendarEvent(
                    name=f"{row.get('name', row['code'])} {period}披露",
                    date=ev_date.isoformat(),
                    days_to_event=days,
                    event_type="earnings",
                    impact="medium",
                    source="akshare",
                    note=f"财报披露日（{period}）",
                    affected_codes=[row["code"]] if codes else [],
                    landed=False,
                ))
        return events

    def get_upcoming_events(
        self, days: int = 30, codes: Optional[list[str]] = None,
    ) -> list[CalendarEvent]:
        """合并静态+财报事件，返回未来 days 天内（days_to_event 0~days），按倒计时排序。

        codes=None 时只返回静态事件（避免拉全市场5534条财报）；传持仓股 codes 才拉对应财报。
        """
        events = self.load_static_events()
        if codes:
            events += self.get_disclosure_events(codes=codes)
        upcoming = [e for e in events if 0 <= e.days_to_event <= days]
        upcoming.sort(key=lambda e: e.days_to_event)
        return upcoming
