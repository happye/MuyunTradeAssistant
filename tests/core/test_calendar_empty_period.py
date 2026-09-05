"""ISS-079 财报预约披露表「未发布期间」空态回归（2026-09-05）

锁死语义：巨潮预约披露接口对尚未发布预约表的报告期返回空列表，akshare
`stock_report_disclosure` 对空数据直接 `temp_df.columns=[10列]` 崩
`ValueError: Length mismatch: Expected axis has 0 elements, new values have 10
elements`（实测 2026-09-05 的 2026三季；5-6 月的半年报表同窗口复发）。
「该期间无预约披露表」是合法业务空态而非故障：

1. 不得再以 WARNING「data_provider.calendar.disclosure 失败」形式暴露
   （旧行为：每次 expect 刷一条英文告警，用户被吓到）
2. 空态结果必须入缓存——旧行为失败不缓存，每次 expect 重复打接口
3. 返回 []，上层 get_disclosure_events 按无事件处理

纯 mock（patch akshare.stock_report_disclosure），无网络。
跑法：pytest tests/core/test_calendar_empty_period.py
"""
import contextlib
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.calendar_client import CalendarClient


@contextlib.contextmanager
def _clean_disclosure_cache():
    CalendarClient._disclosure_cache.clear()
    try:
        yield
    finally:
        CalendarClient._disclosure_cache.clear()


def test_unpublished_period_is_empty_state_not_failure(monkeypatch, caplog):
    import akshare as ak
    calls = {"n": 0}

    def _boom(market="沪深京", period="2026三季"):
        calls["n"] += 1
        raise ValueError(
            "Length mismatch: Expected axis has 0 elements, new values have 10 elements")

    monkeypatch.setattr(ak, "stock_report_disclosure", _boom)

    with _clean_disclosure_cache(), caplog.at_level(logging.WARNING):
        events = CalendarClient.get_stock_disclosure("2026三季")
        assert events == [], "未发布期间应返回空事件列表"
        assert not any("calendar.disclosure" in (r.message or "")
                       for r in caplog.records), \
            "空态不应再报「calendar.disclosure 失败」WARNING（旧病：每次 expect 刷英文告警）"

        CalendarClient.get_stock_disclosure("2026三季")
        assert calls["n"] == 1, \
            "空态结果应入缓存（旧病：失败不缓存，每次 expect 重复打接口）"


def test_genuine_failure_still_warns(monkeypatch, caplog):
    """非空态类异常（超时/网络/接口变更）必须保持原告警路径，不许被吞。"""
    import akshare as ak

    def _timeout(market="沪深京", period="2026三季"):
        raise TimeoutError("connect timeout")

    monkeypatch.setattr(ak, "stock_report_disclosure", _timeout)

    with _clean_disclosure_cache(), caplog.at_level(logging.WARNING):
        events = CalendarClient.get_stock_disclosure("2026三季")
        assert events == []
        assert any("calendar.disclosure" in (r.message or "") for r in caplog.records), \
            "真故障（如超时）仍必须走 _safe_call 告警，静默吞掉=fail-open"
