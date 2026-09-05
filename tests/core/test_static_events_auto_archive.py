"""ISS-080 静态事件表自动化回归（v0.8.8.5，2026-09-05）

锁死语义（用户裁决：手动维护工作量太大，简化/自动化）：
1. 过期一次性事件（>7 天宽限）自动归档——不再逐条刷「请更新 static_events.yaml」
   催办告警，改为至多一条汇总（可见自动化已生效，不静默）
2. 周期事件 recur（monthly/quarter）自动滚动到下一个档期，{m}/{q} 占位符解析，
   无需每月手动加"中国X月CPI/PPI"条目
3. 旧 schema（一次性 date + landed）100% 兼容；landed=true 仍跳过
4. 真故障仍留痕（文件缺失/解析失败 warning 不动）

纯 mock（monkeypatch _STATIC_EVENTS_PATH 指向临时 yaml），无网络。
跑法：pytest tests/core/test_static_events_auto_archive.py
"""
import os
import sys
from datetime import date

import pytest
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.expectation import calendar as cal_mod
from src.core.expectation.calendar import EventCalendar


@pytest.fixture
def events_file(tmp_path, monkeypatch):
    p = tmp_path / "static_events.yaml"
    monkeypatch.setattr(cal_mod, "_STATIC_EVENTS_PATH", str(p))
    yield p


def _write(path, events):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"events": events}, f, allow_unicode=True)


AS_OF = date(2026, 9, 5)


def test_past_oneoff_auto_archived_no_per_item_nag(events_file):
    _write(events_file, [
        {"name": "美联储议息-7月", "date": "2026-07-29", "type": "policy",
         "impact": "high", "landed": False},
        {"name": "中国8月CPI/PPI", "date": "2026-09-09", "type": "macro",
         "impact": "medium", "landed": False},
    ])
    cal = EventCalendar(as_of_date=AS_OF)
    stale = cal.check_stale_static_events()
    text = "\n".join(stale)
    assert "请更新" not in text, "旧催办文案应被移除（自动归档后无需手动维护）"
    assert "静态事件表可能过时" not in text, "逐条过时告警应被移除"
    # 未来事件不受影响
    names = [e.name for e in cal.load_static_events()]
    assert "中国8月CPI/PPI" in names and "美联储议息-7月" not in names


def test_archive_summary_visible_not_silent(events_file):
    _write(events_file, [
        {"name": "美联储议息-7月", "date": "2026-07-29", "type": "policy", "landed": False},
    ])
    stale = EventCalendar(as_of_date=AS_OF).check_stale_static_events()
    assert len(stale) == 1 and "自动归档" in stale[0], \
        "归档必须留一条汇总让用户知道自动化生效（防静默空表）"
    assert "美联储议息-7月" in stale[0], "汇总应含被归档的事件名"


def test_recent_past_within_grace_not_flagged(events_file):
    _write(events_file, [
        {"name": "刚过期事件", "date": "2026-09-03", "type": "macro", "landed": False},
    ])
    stale = EventCalendar(as_of_date=AS_OF).check_stale_static_events()
    assert stale == [], "宽限期（≤7天）内过期不告警不归档"


def test_monthly_recur_rolls_forward(events_file):
    _write(events_file, [
        {"name": "中国{m}月CPI/PPI", "recur": "monthly", "day": 10,
         "window_days": 2, "type": "macro", "impact": "medium", "landed": False},
    ])
    cal = EventCalendar(as_of_date=AS_OF)
    evs = [e for e in cal.load_static_events() if "CPI" in e.name]
    assert len(evs) == 1, "月度周期事件应恰好展开出下一个档期"
    e = evs[0]
    assert e.name == "中国8月CPI/PPI", f"公布月9月→数据月8月，实际: {e.name}"
    assert e.date == "2026-09-10" and e.days_to_event == 5, f"实际: {e.date} {e.days_to_event}"
    assert e.date_window_days == 2


def test_quarter_recur_rolls_forward(events_file):
    _write(events_file, [
        {"name": "中国{q}季度GDP", "recur": "quarter", "day": 17,
         "window_days": 5, "type": "macro", "impact": "high", "landed": False},
    ])
    cal = EventCalendar(as_of_date=AS_OF)
    evs = [e for e in cal.load_static_events() if "GDP" in e.name]
    assert len(evs) == 1
    e = evs[0]
    # 2026-07-17 档期已过期(50天)→滚动到 2026-10-17，数据季度=三季度
    assert e.name == "中国3季度GDP" and e.date == "2026-10-17"


def test_legacy_schema_and_landed_still_respected(events_file):
    _write(events_file, [
        {"name": "未来一次性", "date": "2026-09-17", "type": "policy", "landed": False},
        {"name": "已落地", "date": "2026-09-20", "type": "policy", "landed": True},
    ])
    cal = EventCalendar(as_of_date=AS_OF)
    names = [e.name for e in cal.load_static_events()]
    assert names == ["未来一次性"], f"landed=true 必须仍被跳过: {names}"


def test_recur_never_flagged_stale(events_file):
    _write(events_file, [
        {"name": "中国{m}月CPI/PPI", "recur": "monthly", "day": 10, "landed": False},
    ])
    assert EventCalendar(as_of_date=AS_OF).check_stale_static_events() == [], \
        "周期事件自动滚动，永不过时"
