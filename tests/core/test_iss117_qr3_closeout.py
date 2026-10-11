# -*- coding: utf-8 -*-
"""ISS-117 QR3 收口批验收测试（v0.8.29.4，架构师 QR3_ACCEPTANCE.md）。

把架构师 QR2/QR3 探针的合同语义落为正式回归（探针本身是架构师证据，不进产品测试树）：

- Q-R5 日历资格：授权门只消费**只读严格交易日历**（trading_days(allow_refresh=False)），
  缺失/过期/损坏/格式错误/覆盖不足一律 fail-closed 拒绝硬授权；绝不触发供应商登录、
  绝不按星期猜测。周末/模拟节假日/历史 bar 正例保留。
- Q-R5/Q-R1 analysis_date 严格解析：非法非空值不截断不回退，拒绝全部 exit 资格。
- Q-R1 缺数据资格：缺行情对象 / 缺行情时点 / 复算结果资格不足（UNKNOWN 质量、
  身份或证券不符）→ 拒绝；模式适用性检查不依赖行情对象存在。
- Q-R2 证券范围：消费者先拒 unknown/非法 scope；持仓解析保持 AI 返回的事件主体
  证券（请求股不强制替换）；MarketEvent 缺 scope 默认 unknown。
- Q-R7 sector 窗口：行日期完整严格解析（不截断）、请求窗口内、端点可比性。

零网络、零 AI、不触碰真实 ~/.muyun（日历读全部重定向 tmp_path）。
跑法：pytest tests/core/test_iss117_qr3_closeout.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data import akshare_client as quotes
from src.data.models import (MarketEvent, SignalFinding, StockData, TradePlan,
                             SignalType, PositionAction, TradeLifecycle, MarketState,
                             StrategyState)
from src.core.exit_signals import authorize_hard_findings, sector
from src.core.event_layer import EventLayer
from src.core.fear_index import history as fear_history

# ── 构造器 ────────────────────────────────────────────────

_HARD_BINDING = "jiaoxue6-8/mode-rule（既有周期策略纪律）"


def _hard(**kw):
    base = dict(signal_id="exit.stock.triple_up", source="synthetic",
                securities=["600000"], as_of="2026-10-09", data_quality="OK",
                action_scope="exit", verified=True, strategy_binding=_HARD_BINDING,
                detail="synthetic cached claim", reason="既有周期策略纪律")
    base.update(kw)
    return SignalFinding(**base)


def _sd(**kw):
    return StockData(**(dict(stock_code="600000", stock_name="synthetic", price=10,
                             volume=1000, avg_volume_20=1000, change_pct=0,
                             quote_as_of="2026-10-09") | kw))


def _plan(**kw):
    return TradePlan(**(dict(plan_id="qr3-closeout", opened_at="2026-10-01",
                             why_buy="synthetic", when_buy="synthetic", how_much=.2,
                             locked_initial_stop=8, current_stop=8, max_hold_days=180) | kw))


def _strategy(**kw):
    return SimpleNamespace(decision=kw.pop("decision", SignalType.HOLD),
                           position_action=kw.pop("position_action", PositionAction.HOLD_POSITION),
                           position_ratio=kw.pop("position_ratio", .2), **kw)


# ── 日历 fixture（qr3_calendar_probes 同款：冻结时钟 + 合成缓存） ──

def _cached_calendar(monkeypatch, tmp_path, now, built_at=None, dates=None):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=tz)
    monkeypatch.setattr(fear_history, "datetime", FrozenDateTime)
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    fear_history._atomic_write_json(fear_history.BASE_DIR / "trade_dates.json", {
        "version": fear_history.FEAR_VERSION,
        "built_at": built_at if built_at is not None else now.isoformat(),
        # 独立合成市场日历（含节假日空档）
        "dates": dates or ["2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"],
    })


# ── Q-R5：有效日历正例（周末/节假日授权保留） ──────────────

@pytest.mark.parametrize("now,quote_day", [
    (datetime(2026, 10, 10, 16), "2026-10-09"),   # 周六分析 → 最近交易日=周五
    (datetime(2026, 10, 2, 16), "2026-09-30"),    # 模拟节假日 → 上一个交易日
], ids=["weekend", "synthetic_holiday"])
def test_qr5_qualified_cached_calendar_preserves_live_exit(monkeypatch, tmp_path, now, quote_day):
    """有效缓存日历下，周末/节假日分析认上一交易日，合格行情仍获硬授权。"""
    _cached_calendar(monkeypatch, tmp_path, now)
    ok, rej = authorize_hard_findings([_hard(as_of=quote_day)], "600000", live=True,
                                      analysis_date=now.date().isoformat(),
                                      mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of=quote_day))
    assert ok and not rej, [r.reason for r in rej]


def test_qr5_trading_day_premarket_accepts_previous_close(monkeypatch, tmp_path):
    """guard P1：交易日 15:00 前当日行情尚不存在——盘前分析认上一交易日收盘，
    不误判「行情已陈旧」（场次语义与 recent_trade_date 展示口径一致）。"""
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 8, 9),
                     dates=["2026-09-29", "2026-09-30", "2026-10-07", "2026-10-08"])
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-07")], "600000", live=True,
                                      analysis_date="2026-10-08", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2026-10-07"))
    assert ok and not rej, [r.reason for r in rej]


def test_qr5_trading_day_postmarket_still_requires_same_day(monkeypatch, tmp_path):
    """对照：交易日 15:00 后（收盘后）当日口径生效——昨日收盘行情视为陈旧。"""
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 8, 16),
                     dates=["2026-09-29", "2026-09-30", "2026-10-07", "2026-10-08"])
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-07")], "600000", live=True,
                                      analysis_date="2026-10-08", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2026-10-07"))
    assert not ok and rej


@pytest.mark.parametrize("stamp", ["2020-01-10garbage", "2026-02-30"])
def test_qr5_invalid_live_analysis_date_does_not_fall_back_to_today(stamp):
    """非法 live 分析日期（垃圾尾巴/不存在日期）不截断不回退当天，拒绝全部 exit。"""
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000",
                                      live=True, analysis_date=stamp,
                                      stock_data=_sd(price=30, ma5=31, low_60d=10))
    assert not ok and rej
    assert "分析日期非法" in rej[0].reason


def test_qr5_analysis_date_garbage_tail_backtest_rejected():
    """回测 analysis_date 垃圾尾巴同样拒绝（QR2 探针语义入库）。"""
    ok, rej = authorize_hard_findings([_hard(as_of="2020-01-10")], "600000",
                                      live=False, analysis_date="2020-01-10garbage",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2020-01-10"))
    assert not ok and rej


# ── Q-R5：日历异常分支 fail-closed ────────────────────────

def _stale_quote_sd():
    return _sd(price=30, ma5=31, low_60d=10, quote_as_of="2000-01-01")


def test_qr5_stale_quote_rejected_with_plain_diagnostic(monkeypatch, tmp_path):
    """陈旧行情（2000 年）在合格日历下拒绝硬授权，诊断含「陈旧/过期」等价措辞。"""
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 10, 16))
    ok, rej = authorize_hard_findings([_hard(as_of="2000-01-01")], "600000", live=True,
                                      analysis_date="2026-10-10", mode="jianzong",
                                      stock_data=_stale_quote_sd())
    assert not ok and rej
    reason = rej[0].reason
    assert ("陈旧" in reason or "过期" in reason) and "行情" in reason, reason


def test_qr5_corrupt_calendar_built_at_rejects_hard_exit(monkeypatch, tmp_path):
    """缓存 built_at 非法 → 日历不可核实 → 陈旧行情不得重新获得硬退出（fail-closed）。"""
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 10, 16),
                     built_at="not-a-date")
    ok, rej = authorize_hard_findings([_hard(as_of="2000-01-01")], "600000", live=True,
                                      analysis_date="2026-10-10", mode="jianzong",
                                      stock_data=_stale_quote_sd())
    assert not ok and rej
    assert "交易日历不可核实" in rej[0].reason


def test_qr5_calendar_provider_failure_rejects_hard_exit(monkeypatch, tmp_path):
    """日历获取失败（含按星期猜测的展示兜底）不得当作已核实交易日——拒绝授权。"""
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 10, 16))

    def _unavailable(*a, **kw):
        raise RuntimeError("synthetic calendar unavailable")
    monkeypatch.setattr(fear_history, "trading_days", _unavailable)
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000", live=True,
                                      analysis_date="2026-10-10", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10))
    assert not ok and rej


def test_qr5_missing_calendar_cache_never_launches_supplier(monkeypatch, tmp_path):
    """日历缓存缺失：门只读缓存，绝不触发 baostock 登录，且拒绝硬授权（fail-closed）。"""
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "empty-calendar")
    attempts = []

    def _login():
        attempts.append("baostock login")
        return False
    monkeypatch.setattr(quotes, "_ensure_baostock_login", _login)
    ok, rej = authorize_hard_findings([_hard(as_of="2000-01-01")], "600000", live=True,
                                      analysis_date="2026-10-11", stock_data=_stale_quote_sd())
    assert attempts == [], attempts
    assert not ok and rej, "缓存缺失必须拒绝硬授权（不放行）"


def test_qr5_expired_calendar_cache_rejects_hard_exit(monkeypatch, tmp_path):
    """日历缓存超过 7 天 → 不可核实 → 拒绝（不静默沿用旧日历）。"""
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 10, 16),
                     built_at=(datetime(2026, 10, 10, 16) - timedelta(days=8)).isoformat())
    ok, rej = authorize_hard_findings([_hard(as_of="2026-09-01")], "600000", live=True,
                                      analysis_date="2026-10-10", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2026-09-01"))
    assert not ok and rej


def test_qr5_calendar_bad_date_format_rejects(monkeypatch, tmp_path):
    """日历含非法日期条目 → 格式错误 → 拒绝（格式错误不得部分采信）。"""
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 10, 16),
                     dates=["2026-09-29", "2026-09-30", "garbage", "2026-10-09"])
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000", live=True,
                                      analysis_date="2026-10-10", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10))
    assert not ok and rej


def test_qr5_calendar_gap_beyond_tolerance_rejects(monkeypatch, tmp_path):
    """日历最后日期距分析日期超过容忍窗（覆盖不足）→ 拒绝，旧日历不冒充最新。"""
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 10, 16),
                     dates=["2026-08-03", "2026-08-04", "2026-08-05"])
    ok, rej = authorize_hard_findings([_hard(as_of="2026-08-05")], "600000", live=True,
                                      analysis_date="2026-10-10", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2026-08-05"))
    assert not ok and rej
    assert "覆盖" in rej[0].reason


# ── Q-R1：缺数据资格 + 复算结果资格 ───────────────────────

@pytest.mark.parametrize("data", [None,
                                  _sd(price=30, ma5=31, low_60d=10, quote_as_of=None)],
                         ids=["missing_current_data", "missing_data_timestamp"])
def test_qr1_hard_authorization_requires_qualified_current_data(data):
    """缺行情对象或缺行情时点：复算无从确认/结果 UNKNOWN → 一律拒绝。"""
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000",
                                      live=False, analysis_date="2026-10-09",
                                      stock_data=data, mode="jianzong")
    assert not ok, (ok, [r.reason for r in rej])


def test_qr1_mode_applicability_positive_control():
    """同一发现：剑宗适用（复算确认 → 授权）；气宗不适用（拒绝）。"""
    kwargs = dict(live=False, analysis_date="2020-01-10",
                  stock_data=_sd(price=30, ma5=31, low_60d=10, quote_as_of="2020-01-10"))
    ok_j, rej_j = authorize_hard_findings([_hard(as_of="2020-01-10")], "600000",
                                          mode="jianzong", **kwargs)
    assert ok_j and not rej_j, [r.reason for r in rej_j]
    ok_q, rej_q = authorize_hard_findings([_hard(as_of="2020-01-10")], "600000",
                                          mode="qizong", **kwargs)
    assert not ok_q and rej_q


def test_qr1_mode_check_independent_of_stock_data():
    """气宗不适用判定不依赖行情对象存在：stock_data=None 依然拒绝（不以跳过放行）。"""
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000",
                                      live=False, analysis_date="2026-10-09",
                                      stock_data=None, mode="qizong")
    assert not ok and rej
    assert "气宗" in rej[0].reason


# ── Q-R2：消费者范围门 + 事件主体证券 ─────────────────────

def test_qr2_unknown_scope_with_codes_rejected_at_consumer():
    """unknown + affected_codes 含本股：范围未知不得借 codes 获得适用性。"""
    from src.core.orchestrator import _event_applies_to_stock
    ev = MarketEvent(scope="unknown", affected_codes=["600000"], event_type="black_swan",
                     sentiment="bearish", impact_level=5,
                     four_elements={"authenticity": 100})
    assert _event_applies_to_stock(ev, "600000") is False


def test_qr2_illegal_scope_string_rejected():
    """scope 非法字符串（如 AI 幻觉值）同样不适用。"""
    from src.core.orchestrator import _event_applies_to_stock
    ev = MarketEvent(scope="galaxy", affected_codes=["600000"], event_type="macro")
    assert _event_applies_to_stock(ev, "600000") is False


def test_qr2_market_default_is_unknown_for_legacy_events():
    """QR2 收口：旧/缺字段不再默认 market 全市场权限——默认 unknown。"""
    ev = MarketEvent(event_type="macro")
    assert ev.scope == "unknown"
    from src.core.orchestrator import _event_applies_to_stock
    assert _event_applies_to_stock(ev, "600000") is False


@pytest.mark.parametrize("scope,codes,stock,expected", [
    ("market", [], "600000", True),      # 显式 market 正例保留
    ("stock", ["600000"], "600000", True),   # 合法本股规则正例保留
    ("stock", ["600999"], "600000", False),  # 明确别股不适用
    ("sector", [], "600000", False),     # sector 无代码映射不适用
], ids=["market_ok", "own_stock_ok", "other_stock_no", "sector_no_codes_no"])
def test_qr2_scope_securities_positive_controls(scope, codes, stock, expected):
    from src.core.orchestrator import _event_applies_to_stock
    ev = MarketEvent(scope=scope, affected_codes=codes, event_type="macro")
    assert _event_applies_to_stock(ev, stock) is expected


def _fake_ai_layer(payload):
    obj = object.__new__(EventLayer)
    obj._ai_model = "deepseek-flash"
    obj._rag_service = None

    class _Completions:
        def create(inner_self, **kw):
            msg = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    obj._ai_client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    return obj


def _event_payload(**kw):
    return dict(is_significant=True, event_type="black_swan", sentiment="bearish",
                impact_level=5, four_elements={k: 100 for k in
                    ("authenticity", "virality", "scale", "timeliness")}) | kw


def test_qr2_portfolio_parser_keeps_explicit_other_stock():
    """AI 明确解析出别股（scope=stock + 别股代码）不得被请求股强制替换。"""
    from src.core.event_layer import EventLayer
    obj = _fake_ai_layer(_event_payload(scope="stock", affected_codes=["600999"]))
    ev = obj._ai_classify_portfolio_news("synthetic", "600000", "synthetic news")
    assert ev is not None
    assert ev.affected_codes == ["600999"], ev.model_dump()
    from src.core.orchestrator import _event_applies_to_stock
    assert _event_applies_to_stock(ev, "600000") is False


def test_qr2_unknown_scan_event_never_reaches_conversion(monkeypatch):
    """真实解析→扫描→编排消费全链：unknown 事件绝不进入软调节转换。"""
    from src.core.event_layer import EventLayer
    from src.data.news_client import NewsClient
    from src.scanner.market_cache import MarketCache

    obj = _fake_ai_layer(_event_payload())  # 无 scope 字段 → unknown
    obj.portfolio_scan_enabled = True
    obj.rules = []
    obj._ai_available = True
    calls = []
    monkeypatch.setattr(MarketCache, "get_all_stocks", lambda self: {})

    def _news(code, max_count=5):
        calls.append((code, max_count))
        return [{"title": "synthetic scope-unknown news"}]
    monkeypatch.setattr(NewsClient, "get_stock_news", _news)
    events = obj.scan_portfolio_events(
        [SimpleNamespace(stock_code="600000", stock_name="synthetic")])
    assert calls == [("600000", 5)] and len(events) == 1
    assert events[0].scope == "unknown"

    conversions = []

    def _convert(event):
        conversions.append(event)
        return EventLayer.to_ai_modifier_result(object.__new__(EventLayer), event)
    engine = _offline_engine(monkeypatch)
    engine.event_layer = SimpleNamespace(enabled=True, auto_scan=True,
                                         check_events=lambda: events,
                                         to_ai_modifier_result=_convert)
    engine.analyze_packet(_sd(), confirmed_ratio=.2, position_state="HELD",
                          current_position_ratio=.2, has_position=True,
                          trade_plan=_plan(), today="2026-10-10", ai_enabled=False)
    assert conversions == [], conversions


# ── packet 级末端保护（最终 packet 不得 EXIT） ─────────────

def _offline_engine(monkeypatch):
    from src.core.benzong import data_provider
    from src.core.exit_signals import stock as stock_mod
    from src.core.orchestrator import Orchestrator
    engine = Orchestrator(enabled_skills=[])
    monkeypatch.setattr(engine.skill_engine, "execute_all", lambda data: [])
    monkeypatch.setattr(engine, "_compute_fundamental_findings", lambda *a: (None, []))
    monkeypatch.setattr(data_provider, "get_market_turnover", lambda *a, **k: 1.0)
    monkeypatch.setattr(stock_mod, "_check_holder_count_surge", lambda code: None)
    monkeypatch.setattr(stock_mod, "_check_margin_surge", lambda code: None)
    return engine


def test_qr5_stale_quote_packet_not_exit_with_plain_wording(monkeypatch, tmp_path):
    """packet 级：陈旧行情经真实聚合/门/执行链后不得 EXIT，授权标志 False。"""
    from src.core.decision_contract import DesiredAction
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 10, 16))
    engine = _offline_engine(monkeypatch)
    packet, dr, out, _, _ = engine.analyze_packet(
        _stale_quote_sd(), confirmed_ratio=.2, position_state="HELD",
        current_position_ratio=.2, has_position=True, trade_plan=_plan(),
        today="2026-10-10", ai_enabled=False)
    assert packet.desired_action != DesiredAction.EXIT, packet.desired_action
    assert not out.top_signal_authorized
    assert any(("陈旧" in w or "过期" in w) and "行情" in w for w in dr.warnings), dr.warnings


def test_qr5_valid_triple_still_exits_live(monkeypatch, tmp_path):
    """合格当前数据正例：合法三倍纪律经新门仍 EXIT（合格正例不因新门普遍降权）。"""
    from src.core.decision_contract import DesiredAction
    _cached_calendar(monkeypatch, tmp_path, datetime(2026, 10, 10, 16))
    engine = _offline_engine(monkeypatch)
    packet, _, out, _, _ = engine.analyze_packet(
        _sd(price=30, ma5=31, low_60d=10), confirmed_ratio=.2, position_state="HELD",
        current_position_ratio=.2, has_position=True, trade_plan=_plan(),
        today="2026-10-10", ai_enabled=False)
    assert packet.desired_action == DesiredAction.EXIT
    assert out.top_signal_authorized


# ── Q-R7：sector 请求窗口与端点资格 ───────────────────────

class _RS:
    error_code = "0"

    def __init__(self, rows):
        self.rows, self.i = rows, -1

    def next(self):
        self.i += 1
        return self.i < len(self.rows)

    def get_row_data(self):
        return self.rows[self.i]


def _sector_rows(monkeypatch, rows):
    import baostock as bs
    calls = []
    monkeypatch.setattr(quotes, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(quotes, "_call_with_timeout", lambda fn, **kw: fn())

    def _query(*a, **kw):
        calls.append(kw)
        return _RS(rows)
    monkeypatch.setattr(bs, "query_history_k_data_plus", _query)
    result = sector._fetch_20d_change_pct("600001")
    assert len(calls) == 1
    assert calls[0]["adjustflag"] == "2"
    return result


def test_qr7_garbage_tail_date_not_truncated_into_valid(monkeypatch):
    """行日期垃圾尾巴不得被 [:10] 截成合法日历（完整严格解析）。"""
    import pandas as pd
    rows = [[d.strftime("%Y-%m-%d") + "garbage", "10"]
            for d in pd.bdate_range(end="2026-10-09", periods=21)]
    assert _sector_rows(monkeypatch, rows) is None


def test_qr7_old_window_rows_not_current_evidence(monkeypatch):
    """供应商无视窗口参数返回的 2000 年 21 行：请求窗口过滤后不构成当前证据。"""
    import pandas as pd
    rows = [[d.strftime("%Y-%m-%d"), "10"]
            for d in pd.bdate_range("2000-01-01", periods=21)]
    assert _sector_rows(monkeypatch, rows) is None


def test_qr7_real_same_window_rows_positive(monkeypatch):
    """合法同窗 21 个不同交易日观测保留（正例，随墙钟不漂移：末端=2天前）。"""
    import pandas as pd
    end = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    rows = [[d.strftime("%Y-%m-%d"), "10"]
            for d in pd.bdate_range(end=end, periods=21)]
    assert _sector_rows(monkeypatch, rows) == 0


def test_qr7_conflicting_duplicate_rejected(monkeypatch):
    """同日收盘冲突（数据可疑）放弃——既有 Q7 语义在此批次回归中保留。"""
    import pandas as pd
    rows = [[d.strftime("%Y-%m-%d"), "10"]
            for d in pd.bdate_range(end="2026-10-09", periods=21)]
    rows.append([rows[-1][0], "20"])
    assert _sector_rows(monkeypatch, rows) is None


def test_qr7_endpoint_lag_beyond_buffer_rejected(monkeypatch):
    """端点滞后分支直达：21 个连续自然日（含非工作日日期，供应商异常数据形态）
    全落窗口前段，最后观测距请求端点 >12 天 → 端点不可比拒绝。"""
    end_dt = datetime.now() - timedelta(days=2)
    # 连续自然日 21 天：first=end-33 ≥ start=end-38，last=end-13 → lag=13
    last = end_dt - timedelta(days=13)
    rows = [[(last - timedelta(days=i)).strftime("%Y-%m-%d"), "10"]
            for i in range(20, -1, -1)]
    assert _sector_rows(monkeypatch, rows) is None
