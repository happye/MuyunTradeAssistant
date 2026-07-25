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

_STATIC_EVENTS_PATH = os.path.join("configs", "static_events.yaml")


def _parse_date(s: str) -> Optional[date]:
    """解析 YYYY-MM-DD，失败返回 None"""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class EventCalendar:
    """预期事件日历聚合器"""

    def __init__(self, as_of_date: Optional[date] = None):
        """Args: as_of_date: 基准日期（默认今天），Phase 2 回测用"""
        self._as_of = as_of_date or date.today()

    def load_static_events(self) -> list[CalendarEvent]:
        """加载静态事件表，过滤已落地，算 days_to_event。"""
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
            ev_date = _parse_date(item.get("date", ""))
            if ev_date is None:
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
        """检测过期未标 landed 的静态事件（date < today-7天），返回 warning 文本列表。"""
        if not os.path.exists(_STATIC_EVENTS_PATH):
            return []
        try:
            with open(_STATIC_EVENTS_PATH, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            return []
        warnings = []
        for item in cfg.get("events", []):
            if item.get("landed", False):
                continue
            ev_date = _parse_date(item.get("date", ""))
            if ev_date is None:
                continue
            days_past = (self._as_of - ev_date).days
            if days_past > 7:
                warnings.append(
                    f"静态事件表可能过时：'{item.get('name')}' 已过期 {days_past} 天，"
                    f"请更新 configs/static_events.yaml（landed=true 或改日期）"
                )
        return warnings

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
