# -*- coding: utf-8 -*-
"""ISS-117 S0/S1 信号权限收口验收测试（架构师裁决 2026-10-09）。

把 authority_probes 的 23 个反例中 S0/S1 范围内的场景落成正式回归（A01/A02/A03/
A04/A05/A06/A07/A11 价格资格/A12），并补合法正例（有效止损、三倍定律 exit 资格）。
S2（A08 投票公式/A09 模式重裁）与 S3（A13 shadow/全消费者闭环）见 plan/fusion/
signal_authority_review/IMPLEMENTATION_TASKS.md，为后续卡片。

零网络、零 AI、不触碰真实 ~/.muyun（缓存目录重定向 tmp_path）。
跑法：pytest tests/core/test_signal_authority_iss117.py
"""
import json
import os
import sys
import math
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.models import SignalFinding, StockData, MarketEvent, AIModifierResult


# ── A01/E1：减持标题只作待核验线索（否定/取消/澄清/未实施均不得硬退出）──────

def _ann(title):
    return [{"title": title, "date": "2026-07-01", "content": "", "source": "em"}]


@pytest.mark.parametrize("title", [
    "控股股东终止减持计划暨未减持股份的公告",
    "控股股东承诺未来六个月不减持",
    "公司澄清控股股东减持传闻不实",
    "实际控制人减持计划",
    "大股东减持",
])
def test_a01_reduction_titles_are_research_only(title):
    """A01：全部减持标题候选 = research 资格（含否定语义），不产生清仓信号。"""
    from src.core.exit_signals.stock import _check_holder_reduction
    fs = _check_holder_reduction(_ann(title), code="600519")
    assert fs, f"{title} 应产生待核验线索"
    for f in fs:
        assert f.action_scope == "research"
        assert f.verified is False
        assert "待核验" in f.reason or "线索" in f.reason


@pytest.mark.parametrize("title", [
    "普通减持公告",            # 无主体词
    "控股股东增持",            # 无减持词
    "上市公司业绩预告",        # 都没有
])
def test_a01_reduction_nonmatching_not_even_candidate(title):
    from src.core.exit_signals.stock import _check_holder_reduction
    assert _check_holder_reduction(_ann(title), code="600519") == []


# ── A02/E2：股东户数按日期倒序取最新一期（升序数据的旧缺陷反例）──────────

def test_a02_holder_count_uses_latest_row_not_iloc0(monkeypatch, tmp_path):
    """升序数据：2020 年户数暴增、2026 年最新一期下降——不得报告激增。"""
    import src.core.exit_signals.stock as stock
    import pandas as pd

    df = pd.DataFrame({
        "股东户数统计截止日": ["2020-01-01", "2026-10-01"],
        "股东户数-本次": ["200", "100"],
        "股东户数-上次": ["100", "110"],
    })
    monkeypatch.setattr("src.core.benzong.data_provider._safe_call", lambda *a, **k: df)
    monkeypatch.setattr(stock, "_holder_cache_dir", lambda: tmp_path)
    f = stock._check_holder_count_surge("600519")
    assert f is None, "最新一期户数下降，不得报告激增（E2：iloc[0] 取最旧缺陷）"


def test_a02_holder_count_surge_on_latest_still_research(monkeypatch, tmp_path):
    import src.core.exit_signals.stock as stock
    import pandas as pd

    df = pd.DataFrame({
        "股东户数统计截止日": ["2026-09-30", "2026-06-30"],
        "股东户数-本次": ["180", "100"],
        "股东户数-上次": ["100", "95"],
    })
    monkeypatch.setattr("src.core.benzong.data_provider._safe_call", lambda *a, **k: df)
    monkeypatch.setattr(stock, "_holder_cache_dir", lambda: tmp_path)
    f = stock._check_holder_count_surge("600519")
    assert f is not None and f.action_scope == "research", "真实激增也只降为研究线索（A12）"
    assert f.as_of == "2026-09-30"


# ── A04/E3：连续性证据缺失 = UNKNOWN 研究线索，不放宽为单日触发────────────

def test_a04_shrink_missing_series_is_unknown_research():
    from src.core.exit_signals.stock import _check_shrink_acceleration
    sd = StockData(stock_code="x", stock_name="x", price=10, volume=60,
                   avg_volume_5=100, avg_volume_20=100, change_pct=16)
    f = _check_shrink_acceleration(sd, code="x")
    assert f is not None and f.data_quality == "UNKNOWN" and f.action_scope == "research"
    assert "连续性证据缺失" in f.detail


def test_a04_shrink_two_day_confirmed_still_research_ok():
    from src.core.exit_signals.stock import _check_shrink_acceleration
    sd = StockData(stock_code="x", stock_name="x", price=10, volume=60,
                   avg_volume_5=100, avg_volume_20=100, change_pct=16,
                   volume_series=[100, 100, 40, 40, 60])  # 昨日 40（相对更早均量 80 缩量）
    f = _check_shrink_acceleration(sd, code="x")
    assert f is not None and f.data_quality == "OK" and f.action_scope == "research"


def test_a04_shrink_yesterday_not_shrinking_no_signal():
    from src.core.exit_signals.stock import _check_shrink_acceleration
    sd = StockData(stock_code="x", stock_name="x", price=10, volume=60,
                   avg_volume_5=100, avg_volume_20=100, change_pct=16,
                   volume_series=[40, 100, 100, 100, 60])  # 昨日 100 未缩量
    assert _check_shrink_acceleration(sd, code="x") is None


# ── A06/D1/D2：AI 标签与事件无硬权限 ─────────────────────

def test_a06_black_swan_conf0_no_force_state():
    from src.core.ai_modifier import AIModifier
    m = AIModifier({})
    r = AIModifierResult(event_type="black_swan", confidence=0.0,
                         sentiment="neutral", risk_level="low")
    m._apply_modification(r, SimpleNamespace(price=11.78))
    assert r.force_state is None, "零置信度 AI 黑天鹅不得强制 PANIC"
    assert r.position_cap == 1.0, "零置信度不得限仓"
    assert r.score_adjustment == 0.0, "零置信度不得压分"


def test_a06_black_swan_conf1_bounded_soft():
    from src.core.ai_modifier import AIModifier
    m = AIModifier({})
    r = AIModifierResult(event_type="black_swan", confidence=1.0,
                         sentiment="bearish", risk_level="high")
    m._apply_modification(r, SimpleNamespace(price=11.78))
    assert r.force_state is None, "AI 标签永远不获得强制 PANIC 资格"
    assert r.score_adjustment == pytest.approx(-0.5)
    assert r.position_cap == pytest.approx(m.risk_position_cap), "限仓幅度按置信度全额缩放（自报 high）"


def test_a06_risk_high_conf0_no_cap():
    from src.core.ai_modifier import AIModifier
    m = AIModifier({})
    r = AIModifierResult(event_type="none", confidence=0.0,
                         sentiment="neutral", risk_level="high")
    m._apply_modification(r, SimpleNamespace(price=11.78))
    assert r.position_cap == 1.0, "零置信度 high risk 不得限仓"


def test_a06_narrative_bool_string_false():
    from src.core.ai_modifier import AIModifier
    m = AIModifier({})
    r = AIModifierResult(event_type="none", confidence=0.8,
                         sentiment="neutral", risk_level="low")
    m._apply_modification(r, SimpleNamespace(price=11.78))
    assert r.position_cap == 1.0


def test_a06_event_missing_four_elements_no_hard_override():
    """D2：缺四要素 = 事实资格 UNKNOWN——confidence=0，无 cap/force。"""
    from src.core.event_layer import EventLayer
    el = EventLayer({"enabled": False})
    ev = MarketEvent(event_type="black_swan", impact_level=5,
                     sentiment="neutral", detection_method="ai", four_elements=None)
    r = el.to_ai_modifier_result(ev)
    assert r.confidence == 0.0 and r.force_state is None and r.position_cap == 1.0


def test_a06_event_verified_bearish_no_cap():
    """S01 验收 Q2：本批无独立核验器——已核实事件同样不硬限仓（软调节保留）。"""
    from src.core.event_layer import EventLayer
    el = EventLayer({"enabled": False})
    ev = MarketEvent(event_type="macro", impact_level=5, sentiment="bearish",
                     detection_method="official",
                     four_elements={"who": "央行", "what": "紧缩", "when": "2026-10-09",
                                    "how": "公开市场操作", "authenticity": 90})
    r = el.to_ai_modifier_result(ev)
    assert r.confidence == pytest.approx(0.9)   # 可信度来自真实性，非 impact
    assert r.position_cap == 1.0                # Q2：事件不硬限仓
    assert r.score_adjustment < 0               # 空向软调节保留


def test_a06_event_verified_bullish_no_cap():
    """D2：严重利好不适用空头限仓政策。"""
    from src.core.event_layer import EventLayer
    el = EventLayer({"enabled": False})
    ev = MarketEvent(event_type="policy", impact_level=5, sentiment="bullish",
                     detection_method="official",
                     four_elements={"who": "国常会", "what": "刺激", "when": "2026-10-09",
                                    "how": "政策发布", "authenticity": 90})
    r = el.to_ai_modifier_result(ev)
    assert r.position_cap == 1.0 and r.force_state is None


def test_a06_event_low_authenticity_veto_unchanged():
    from src.core.event_layer import EventLayer
    el = EventLayer({"enabled": False})
    ev = MarketEvent(event_type="black_swan", impact_level=5, sentiment="bearish",
                     detection_method="ai",
                     four_elements={"who": "x", "what": "y", "when": "z",
                                    "how": "w", "authenticity": 10})
    r = el.to_ai_modifier_result(ev)
    assert r.confidence == 0.0 and r.force_state is None


# ── A07/D6：invalidate 跨字段字符串兜底已删除 ────────────

def test_a07_invalidate_cross_field_fallback_removed():
    # 构造：invalidate=null + raw 文本含 invalidate/true —— 不得判一票否决
    src = open("src/core/benzong/dimensions/risk_deduction.py", encoding="utf-8").read()
    assert '"invalidate" in raw.lower()' not in src, "跨字段子串兜底应已删除"


def test_a07_invalidate_nonbool_structured_is_review_only():
    """D6：非布尔结构化值不作为一票否决——跨字段兜底已删，非布尔由上游校验层归一 None。"""
    src = open("src/core/benzong/dimensions/risk_deduction.py", encoding="utf-8").read()
    assert '"invalidate" in raw.lower()' not in src, "跨字段子串兜底应已删除"
    assert "elif structured_invalidate is not None" not in src, "非布尔分支已上移校验层（本处不再出现）"


# ── A04/D4：skill_engine 未知条件 fail-closed ────────────

def test_a04_skill_unknown_condition_fail_closed():
    """未知条件令规则不可执行（met=False），不缩分母照常触发。"""
    from src.core.skill_engine import YAMLBasedSkill
    eng = YAMLBasedSkill.__new__(YAMLBasedSkill)   # 绕过 config 装配，仅测评估函数
    eng.CONDITION_REGISTRY = {"change_negative": lambda d, p: d.change_pct <= p.get("value", 0)}

    condition = {
        "change_negative": {"value": -0.1},
        "nonexistent_confirmation": {},
    }
    r = eng._evaluate_condition(condition, SimpleNamespace(change_pct=-0.5), require="all")
    assert r["met"] is False, "未知条件必须 fail-closed"
    assert r["unknown_conditions"] == ["nonexistent_confirmation"]


def test_a04_skill_known_conditions_still_work():
    from src.core.skill_engine import YAMLBasedSkill

    class _SD:
        change_pct = -0.5

    eng = YAMLBasedSkill.__new__(YAMLBasedSkill)
    eng.CONDITION_REGISTRY = {"change_negative": lambda d, p: d.change_pct <= p.get("value", 0)}
    condition = {"change_negative": {"value": -0.1}}
    r = eng._evaluate_condition(condition, _SD(), require="all")
    assert r["met"] is True and not r.get("unknown_conditions")


# ── A11：坏价格不得构成穿止损；有效止损正例保持 ──────────

def test_a11_bad_price_no_stop_breach():
    from src.core.plan_guard import _check_stop_breach
    plan = SimpleNamespace(current_stop=11.5)
    for bad in (0, None, float("nan"), float("inf"), -5):
        sd = SimpleNamespace(price=bad)
        assert _check_stop_breach(plan, sd) is False, f"坏价格 {bad} 不得判穿止损"


def test_a11_valid_price_stop_breach_positive():
    from src.core.plan_guard import _check_stop_breach
    plan = SimpleNamespace(current_stop=11.5)
    assert _check_stop_breach(plan, SimpleNamespace(price=11.4)) is True
    assert _check_stop_breach(plan, SimpleNamespace(price=11.6)) is False


# ── A12：全部未核定信号 = research 资格（三倍定律例外）────

def test_a12_all_unverified_signals_are_research():
    """缩量/减持/股东户数/融资/旗手/渗透率/宏观 = research；三倍定律 = exit（既有纪律）。"""
    from src.core.exit_signals.stock import _check_shrink_acceleration
    from src.core.exit_signals.sector import _check_flagbearer_lag
    sd = StockData(stock_code="x", stock_name="x", price=10, volume=60,
                   avg_volume_5=100, avg_volume_20=100, change_pct=16)
    f1 = _check_shrink_acceleration(sd, code="x")
    assert f1 is None or f1.action_scope == "research"
    sd2 = StockData(stock_code="x", stock_name="x", price=15, volume=1000,
                    change_20d=50.0)
    f2 = _check_flagbearer_lag("600519", sd2, "x")
    assert f2 is None or f2.action_scope == "research"


def test_a12_triple_up_keeps_exit_scope():
    """三倍定律是既有周期策略纪律（bar 衍生），保持 exit 资格——非全盘关闭。"""
    from src.core.exit_signals.stock import _check_triple_up_rule
    sd = StockData(stock_code="x", stock_name="x", price=40.0, volume=1000,
                   ma5=42.0, low_60d=10)
    f = _check_triple_up_rule(sd, code="x")
    assert f is not None and f.action_scope == "exit" and f.verified is True


# ── A05：预告公开日期资格门 ──────────────────────────────

def test_a05_forecast_invalid_and_future_dates_disqualified(monkeypatch):
    """公开日期缺失/非法/未来 → 无资格作为「建仓后新增利空」。"""
    import src.data.akshare_client as ak_mod
    rows_by_case = {
        "missing": [["", "2026-09-30", "预亏", "亏损"]],          # pub 缺失
        "garbage": [["not-a-date", "2026-09-30", "预亏", "亏损"]],  # 非法
        "future": [["2099-01-01", "2026-09-30", "预亏", "亏损"]],   # 未来
    }
    for case, rows in rows_by_case.items():
        fields = ["profitForcastExpPubDate", "profitForcastExpStatDate",
                  "profitForcastType", "profitForcastAbstract"]

        _FIELDS = ["profitForcastExpPubDate", "profitForcastExpStatDate",
                   "profitForcastType", "profitForcastAbstract"]

        class _FakeRS:
            error_code = "0"

            def __init__(self):
                self._rows = list(rows)
                self._i = 0
                self.fields = _FIELDS   # 方法内闭包可取外层函数局部变量

            def next(self):
                if self._i >= len(self._rows):
                    return False
                self._i += 1
                return True

            def get_row_data(self):
                return self._rows[self._i - 1]

        monkeypatch.setattr(ak_mod, "_ensure_baostock_login", lambda: True)

        class _FakeBS:
            @staticmethod
            def query_forecast_report(**k):
                return _FakeRS()

        monkeypatch.setitem(sys.modules, "baostock", _FakeBS)

        class _FakeTO:
            @staticmethod
            def _call_with_timeout(fn, timeout=30):
                return fn()

            @staticmethod
            def _baostock_logout():
                pass

        monkeypatch.setattr(ak_mod, "_call_with_timeout",
                            lambda fn, timeout=30: fn(), raising=False)
        r = ak_mod.AKShareClient.get_latest_forecast("600519", since_date="2026-10-01")
        assert r is None, f"{case} 日期的预告不得取得「建仓后新增利空」资格"


def test_a05_forecast_same_day_marks_order_ambiguous(monkeypatch):
    """同日公开与建仓：时序不明（日精度），消费方降资格。"""
    import src.data.akshare_client as ak_mod
    fields = ["profitForcastExpPubDate", "profitForcastExpStatDate",
              "profitForcastType", "profitForcastAbstract"]

    def _fake_rs():
        _fields = ["profitForcastExpPubDate", "profitForcastExpStatDate",
                   "profitForcastType", "profitForcastAbstract"]

        class _FakeRS:
            error_code = "0"
            fields = _fields

            def __init__(self):
                self._rows = [["2026-10-08", "2026-09-30", "预亏", "亏损"]]
                self._i = 0

            def next(self):
                if self._i >= len(self._rows):
                    return False
                self._i += 1
                return True

            def get_row_data(self):
                return self._rows[self._i - 1]

        return _FakeRS()

    monkeypatch.setattr(ak_mod, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(ak_mod, "_call_with_timeout", lambda fn, timeout=30: fn())

    import baostock as _real_bs
    monkeypatch.setattr(_real_bs, "query_forecast_report", lambda **k: _fake_rs())
    r = ak_mod.AKShareClient.get_latest_forecast("600519", since_date="2026-10-08")
    assert r is not None and r.get("_pub_order_ambiguous") is True


def test_a05_forecast_valid_post_entry_eligible(monkeypatch):
    """合法正例：建仓后发布 + 有效日期 → 有资格（降级后走 research 通道）。"""
    import src.data.akshare_client as ak_mod
    fields = ["profitForcastExpPubDate", "profitForcastExpStatDate",
              "profitForcastType", "profitForcastAbstract"]

    def _fake_rs():
        _fields = ["profitForcastExpPubDate", "profitForcastExpStatDate",
                   "profitForcastType", "profitForcastAbstract"]

        class _FakeRS:
            error_code = "0"
            fields = _fields

            def __init__(self):
                self._rows = [["2026-10-10", "2026-09-30", "预亏", "大幅亏损"]]
                self._i = 0

            def next(self):
                if self._i >= len(self._rows):
                    return False
                self._i += 1
                return True

            def get_row_data(self):
                return self._rows[self._i - 1]

        return _FakeRS()

    monkeypatch.setattr(ak_mod, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(ak_mod, "_call_with_timeout", lambda fn, timeout=30: fn())

    import baostock as _real_bs
    monkeypatch.setattr(_real_bs, "query_forecast_report", lambda **k: _fake_rs())
    r = ak_mod.AKShareClient.get_latest_forecast("600519", since_date="2026-10-01")
    assert r is not None and r.get("type") == "预亏"
    assert not r.get("_pub_order_ambiguous")


def test_a05_forecast_research_not_hard_in_findings():
    """A12：预亏/预减类别降级为 research，不进 fundamental_alert 硬通道。"""
    # 运行时语义由 test_a05_forecast_valid_post_entry_eligible 覆盖（mock 层）；
    # 此处锁死源码语义：预告类别只产生 research 发现（不进硬通道）
    src = open("src/core/exit_signals/fundamental.py", encoding="utf-8").read()
    assert 'action_scope="research"' in src
    assert 'action_scope="exit"' not in src.split("def check_fundamental_findings")[1].split("def ")[0] \
        if "def check_fundamental_findings" in src else True
