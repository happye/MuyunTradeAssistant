# -*- coding: utf-8 -*-
"""v0.8.9.5 彻查批 P3 清扫修复回归。

- trail_pct 配置单位归一（>1 视为百分数；旧 0.3 口径不变）
- event_layer AI 分类 sentiment 白名单 + kimi temperature 守卫
- benzong _annotate_sector_meta kimi temperature 守卫
- web tasks 过期清理
"""
import os
import sys
import json
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.entry_exit.exit_rules import check_take_profit
from src.data.models import StockData


def _sd(price, high):
    return StockData(stock_code="600519", stock_name="贵州茅台", price=price, volume=1000)


# ── trail_pct 单位归一 ───────────────────────────────────────
# 注意：移动止盈分支要求 high_since_entry > entry_price（代码守卫），构造 high=110

def test_trail_pct_fraction_unchanged():
    """旧口径 0.3=30%：回撤 9% 不触发，36% 触发。"""
    cfg = {"take_profit": {"trail_pct": 0.3}}
    # high=110, price=100 → 回撤 9.1% < 30% 不触发（盈利 0% 也不达 tier1 10%）
    assert check_take_profit(_sd(100, 110), cfg, entry_price=100.0, high_since_entry=110.0) is None
    # price=70 → 回撤 36.4% ≥ 30% 触发 EXIT
    sig = check_take_profit(_sd(70, 110), cfg, entry_price=100.0, high_since_entry=110.0)
    assert sig is not None and sig.exit_action == "EXIT"


def test_trail_pct_percent_form_normalized():
    """新口径 trail_pct=10 表达 10%：回撤 10.9% 即触发（旧实现会算成 1000% 永不触发）。"""
    cfg = {"take_profit": {"trail_pct": 10}}
    sig = check_take_profit(_sd(98, 110), cfg, entry_price=100.0, high_since_entry=110.0)
    assert sig is not None and sig.exit_action == "EXIT"


def test_trail_pct_percent_no_premature_trigger():
    """trail_pct=10（10%）：回撤 4.5% 不触发。"""
    cfg = {"take_profit": {"trail_pct": 10}}
    assert check_take_profit(_sd(105, 110), cfg, entry_price=100.0, high_since_entry=110.0) is None


# ── event_layer sentiment 白名单 + temperature 守卫 ────────────

class _FakeAIClient:
    """返回固定 JSON 的伪 OpenAI client，并捕获 create kwargs。"""

    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

        class _Fn:
            def __init__(outer, holder):
                outer.holder = holder

            def create(inner, **kwargs):
                inner.holder.kwargs = kwargs
                msg = SimpleNamespace(content=json.dumps(inner.holder.payload), reasoning_content=None)
                choice = SimpleNamespace(message=msg)
                return SimpleNamespace(choices=[choice])

        self.chat = SimpleNamespace(completions=_Fn(self))


def _make_event_layer():
    from src.core.event_layer import EventLayer
    el = EventLayer({"enabled": False})
    return el


def test_event_classify_sentiment_whitelist():
    el = _make_event_layer()
    client = _FakeAIClient({
        "is_significant": True, "event_type": "earnings",
        "sentiment": "利空",  # 非白名单值 → 必须归 neutral
        "impact_level": 3, "scope": "market", "duration": "short", "summary": "x",
    })
    el._ai_client = client
    el._ai_model = "deepseek-flash"
    el._ai_available = True
    ev = el._ai_classify_event("标题", "摘要")
    assert ev is not None and ev.sentiment == "neutral"
    assert client.kwargs.get("temperature") == 0.1  # deepseek 支持 temperature


def test_event_classify_kimi_no_temperature():
    el = _make_event_layer()
    client = _FakeAIClient({"is_significant": False})
    el._ai_client = client
    el._ai_model = "kimi-k2.6"
    el._ai_available = True
    el._ai_classify_event("标题", "摘要")
    assert "temperature" not in client.kwargs, "kimi-k2.6 不支持 temperature，不应传"


def test_annotate_sector_meta_kimi_no_temperature():
    from src.core.benzong.auto_scorer import _annotate_sector_meta
    client = _FakeAIClient({"flagbearer_code": "600519", "penetration_stage": "1-10"})
    fb, ps = _annotate_sector_meta(
        client, "kimi-k2.6", "600519", "贵州茅台",
        {"industry": {"industry_name": "白酒"}},
    )
    assert "temperature" not in client.kwargs
    assert fb == "600519" and ps == "1-10"


# ── web tasks 过期清理 ───────────────────────────────────────

def test_web_tasks_prune_stale_done():
    from src.web import tasks as t
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
    with t._lock:
        t._tasks.clear()
        t._tasks["old"] = {"status": "done", "result": 1, "error": None, "started": old}
        t._tasks["new"] = {"status": "done", "result": 2, "error": None,
                           "started": datetime.now().isoformat(timespec="seconds")}
    t.start_task(lambda: None)
    with t._lock:
        assert "old" not in t._tasks and "new" in t._tasks
        t._tasks.clear()
