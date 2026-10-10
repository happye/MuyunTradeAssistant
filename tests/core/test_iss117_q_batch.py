# -*- coding: utf-8 -*-
"""ISS-117 Q1–Q7 补修批验收测试（v0.8.29.1，架构师 S01_ACCEPTANCE.md）。

Q1 消费边界资格门 / Q2 事件硬权限收口+证券适用范围 / Q3 AI红线软化 /
Q4 cap 增量语义 / Q5 三倍价格资格 / Q6 公告规范化与子信号隔离 / Q7 行级日期有效性。

零网络、零 AI、不触碰真实 ~/.muyun。
跑法：pytest tests/core/test_iss117_q_batch.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.models import SignalFinding, StrategyDecision, StrategyState, StockData, \
    SignalType, PositionAction, TradeLifecycle, MarketState, TradePlan
from src.core.exit_signals import check_top_signals, authorize_hard_findings
from src.core.plan_guard import PlanGuard
from src.data.models import MarketEvent
from src.core.exit_signals.stock import check_stock_top_signal, _check_triple_up_rule


def _hard_finding(**kw):
    base = dict(signal_id="exit.stock.triple_up",
                source="StockData.price/ma5/low_60d",
                securities=["600519"], as_of=datetime.now().strftime("%Y-%m-%d"),
                data_quality="OK", action_scope="exit", verified=True,
                strategy_binding="jiaoxue6-8/mode-rule（既有周期策略纪律）",
                detail="个股:三倍定律+破5日线", reason="既有周期策略纪律")
    base.update(kw)
    return SignalFinding(**base)


# ── Q1：消费边界资格门 ──────────────────────────────────

def test_q1_gate_authorized_positive():
    """合法硬信号正例：白名单 signal_id + 绑定 + 证券 + 新鲜时点 + OK 质量 → 通过。"""
    ok, rej = authorize_hard_findings([_hard_finding()], "600519", live=True)
    assert len(ok) == 1 and not rej


def test_q1_gate_rejects_wrong_security():
    """错股（适用证券不含当前标的）→ 拒绝 + 诊断保留。"""
    ok, rej = authorize_hard_findings([_hard_finding(securities=["000001"])], "600519", live=True)
    assert not ok and len(rej) == 1
    assert "适用证券" in rej[0].reason and rej[0].action_scope == "research"


def test_q1_gate_rejects_stale_and_future_as_of():
    yesterday = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    ok1, rej1 = authorize_hard_findings([_hard_finding(as_of=yesterday)], "600519", live=True)
    assert not ok1 and "过期" in rej1[0].reason
    future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    ok2, rej2 = authorize_hard_findings([_hard_finding(as_of=future)], "600519", live=True)
    assert not ok2 and "未来" in rej2[0].reason


def test_q1_gate_backtest_waives_staleness():
    """Q5：历史回测按当前 bar 资格——live=False 时陈旧 as_of 不作过期拒收。"""
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    ok, rej = authorize_hard_findings([_hard_finding(as_of=old)], "600519", live=False)
    assert len(ok) == 1 and not rej


def test_q1_gate_rejects_unknown_binding_and_quality():
    ok, rej = authorize_hard_findings(
        [_hard_finding(strategy_binding=" forged", data_quality="UNKNOWN")], "600519", live=True)
    assert not ok and len(rej) == 1
    assert "策略绑定" in rej[0].reason or "数据资格" in rej[0].reason


def test_q1_gate_rejects_forged_cache_finding():
    """缓存自授：verified=True + 漂亮的 detail 也不能越过白名单/证券/时点门。"""
    forged = _hard_finding(signal_id="exit.stock.reduction_title",   # 非白名单
                           securities=["600519"],
                           verified=True,
                           detail="个股:实控人减持(标题线索)")
    ok, rej = authorize_hard_findings([forged], "600519", live=True)
    assert not ok and "不在允许硬退出清单" in rej[0].reason


def test_q1_plan_guard_requires_authorized_flag():
    """旧字符串/未授权实例不触发 P1 CLOSE_ALL（缓存重放同门）。"""
    st = StrategyState(current_position_ratio=0.2)
    st.lifecycle = TradeLifecycle.HOLD
    dec = StrategyDecision(
        decision=SignalType.SELL, state=MarketState.TRANSITION, new_state=st,
        position_action=PositionAction.CLOSE_ALL, position_ratio=0.0,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        top_signal="板块:渗透率突破30%魔咒", top_signal_authorized=False)
    out = PlanGuard().evaluate(dec, _mk_plan(), _mk_sd(), today="2026-10-10")
    assert out.sell_path != "top_signal"
    assert not any("高位止盈" in r for r in (out.strategy_reasons or []))


def test_q1_plan_guard_authorized_still_closes():
    st = StrategyState(current_position_ratio=0.2)
    st.lifecycle = TradeLifecycle.HOLD
    dec = StrategyDecision(
        decision=SignalType.SELL, state=MarketState.TRANSITION, new_state=st,
        position_action=PositionAction.CLOSE_ALL, position_ratio=0.0,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        top_signal="个股:三倍定律+破5日线", top_signal_authorized=True)
    out = PlanGuard().evaluate(dec, _mk_plan(), _mk_sd(), today="2026-10-10")
    assert out.sell_path == "top_signal"
    assert out.position_action == PositionAction.CLOSE_ALL


def _mk_plan(**kw):
    base = dict(plan_id="x", opened_at="2026-10-01", why_buy="t", when_buy="t",
                stock_code="600519", stock_name="贵州茅台", entry_price=11.7,
                ratio=0.1, how_much=0.1, locked_initial_stop=10, current_stop=10)
    base.update(kw)
    return TradePlan(**base)


def _mk_sd(**kw):
    base = dict(stock_code="600519", stock_name="贵州茅台", price=11.78, volume=1000)
    base.update(kw)
    return StockData(**base)


# ── Q2：事件硬权限收口 + 证券适用范围 ────────────────────

def test_q2_event_no_force_state_no_cap_even_verified():
    """本批无独立核验器：事件一律不 force_state、不硬限仓（含真实性=100 空向黑天鹅）。"""
    from src.core.event_layer import EventLayer
    el = EventLayer({"enabled": False})
    ev = MarketEvent(event_type="black_swan", impact_level=5, sentiment="bearish",
                     detection_method="official", scope="market",
                     four_elements={"who": "x", "what": "y", "when": "z",
                                    "how": "w", "authenticity": 100})
    r = el.to_ai_modifier_result(ev)
    assert r.force_state is None, "Q2：事件不得强制 PANIC"
    assert r.position_cap == 1.0, "Q2：事件不得硬限仓"
    assert r.adjusted is True and r.confidence == pytest.approx(1.0)  # 软调节与可信度保留


def test_q2_event_scope_stock_mismatch_excluded():
    """仅涉及 600999 的 stock 范围事件不适用于 600000（Q2 探针场景）。"""
    from src.core.orchestrator import _event_applies_to_stock
    ev = MarketEvent(event_type="macro", impact_level=5, sentiment="bearish",
                     scope="stock", affected_codes=["600999"])
    assert _event_applies_to_stock(ev, "600000") is False
    assert _event_applies_to_stock(ev, "600999") is True


def test_q2_event_unscoped_is_not_market_wide():
    """空证券 ≠ 全市场：scope=stock 但未列 affected_codes → 不适用。"""
    from src.core.orchestrator import _event_applies_to_stock
    ev = MarketEvent(event_type="macro", impact_level=5, scope="stock",
                     affected_codes=[])
    assert _event_applies_to_stock(ev, "600519") is False


def test_q2_event_market_scope_applies():
    from src.core.orchestrator import _event_applies_to_stock
    ev = MarketEvent(event_type="macro", impact_level=3, scope="market")
    assert _event_applies_to_stock(ev, "600519") is True


def test_q2_event_auth_qualified_gate():
    from src.core.orchestrator import _event_auth_qualified
    assert _event_auth_qualified(SimpleNamespace(four_elements={"authenticity": 30})) is True
    assert _event_auth_qualified(SimpleNamespace(four_elements={"authenticity": 29})) is False
    assert _event_auth_qualified(SimpleNamespace(four_elements=None)) is False


# ── Q3：AI invalidate = 红线候选，不硬否决 ────────────────

def _mk_ai(payload):
    class _Fn:
        def __init__(outer, holder):
            outer.holder = holder

        def create(inner, **kwargs):
            msg = SimpleNamespace(content=json.dumps(inner.holder.payload), reasoning_content=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    import types
    c = types.SimpleNamespace()
    c.chat = types.SimpleNamespace(completions=type("C", (), {"create": staticmethod(lambda **k: None)})())
    # 上式静态方法不可访问 holder，改用实例闭包
    class _Completions:
        def __init__(self):
            self.h = payload

        def create(self, **kwargs):
            msg = SimpleNamespace(content=json.dumps(self.h), reasoning_content=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    c.chat = types.SimpleNamespace(completions=_Completions())
    return c


def test_q3_ai_invalidate_true_is_candidate_not_veto():
    """Q3：结构化 invalidate=True + conf=0 → 不写硬 invalidate，产生待核验警告。"""
    from src.core.benzong.dimensions import risk_deduction as rd
    payload = {"score": 10, "confidence": 0.0, "invalidate": True,
               "reasoning": "疑似造假", "raw_ai_response": "{}"}
    client = _mk_ai(json.dumps(payload))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rd, "_call_ai_for_score", lambda *a, **k: {
            "score": 10.0, "confidence": 0.0, "invalidate": True,
            "reasoning": "疑似造假", "raw_ai_response": json.dumps(payload), "warnings": []})
        out = rd.score("600519", "某股", data_summary={
            "announcements": [{"title": "x", "date": "2026-10-01"}]},
            ai_client=client, ai_model="deepseek-flash")
    assert out.get("invalidate") is not True, "Q3：AI 红线候选不得写硬 invalidate"
    assert any("红线候选" in w for w in out.get("warnings", []))


# ── Q4：cap 增量语义 ─────────────────────────────────────

def test_q4_cap_delta_partial_add():
    """HOLD+ADD 当前 .2/目标 .6/cap .3 → 目标 .3（新增 .1），不再 ADD .6。"""
    from src.core.orchestrator import _soft_cap_target
    t, demote, skipped = _soft_cap_target(0.2, 0.6, 0.3)
    assert t == pytest.approx(0.3) and demote is False and skipped is False


def test_q4_cap_reduce_direction_untouched():
    """当前 .8/目标 .6（REDUCE 方向）/cap .3 → 目标保持 .6（不放大卖出）。"""
    from src.core.orchestrator import _soft_cap_target
    t, demote, skipped = _soft_cap_target(0.8, 0.6, 0.3)
    assert t == pytest.approx(0.6) and skipped is True


def test_q4_cap_delta_zero_demotes_to_hold():
    """当前 .2/目标 .6/cap .1 → delta=0 → 转 HOLD（动作说加、目标反降的缺陷修复）。"""
    from src.core.orchestrator import _soft_cap_target
    t, demote, _ = _soft_cap_target(0.2, 0.6, 0.1)
    assert t == pytest.approx(0.2) and demote is True


def test_q4_cap_unknown_current_skipped():
    """当前权重未知 → 不伪造增量，保留策略目标 + skipped。"""
    from src.core.orchestrator import _soft_cap_target
    t, demote, skipped = _soft_cap_target(None, 0.6, 0.3)
    assert t == pytest.approx(0.6) and skipped is True and demote is False


# ── Q5：三倍定律价格/时点资格 ────────────────────────────

def test_q5_triple_inf_ma5_disqualified():
    """price=30/low=10/MA5=Inf → 无资格（不标 OK/verified/exit）。"""
    f = _check_triple_up_rule(
        StockData(stock_code="x", stock_name="x", price=30.0, volume=1000,
                  ma5=float("inf"), low_60d=10.0), code="x")
    assert f is None


def test_q5_triple_carries_quote_as_of():
    """as_of 必须来自来源数据（quote_as_of），供消费边界门做新鲜度核验。"""
    as_of = datetime.now().isoformat(timespec="seconds")
    f = _check_triple_up_rule(
        StockData(stock_code="x", stock_name="x", price=40.0, volume=1000,
                  ma5=42.0, low_60d=10.0, quote_as_of=as_of), code="x")
    assert f is not None and f.action_scope == "exit" and f.as_of == as_of[:10]


# ── Q6：公告规范化 + 子信号隔离 ──────────────────────────

def test_q6_legacy_str_announcements_normalized():
    """旧格式字符串公告不崩（Q6 探针：ann.get 抛错吞掉整组结果）。"""
    fs = check_stock_top_signal(
        StockData(stock_code="x", stock_name="x", price=40.0, volume=1000,
                  ma5=42.0, low_60d=10.0),
        "x", announcements=["控股股东拟减持股份"])   # 纯字符串（旧格式）
    assert fs and any(f.signal_id == "research.stock.reduction_title" for f in fs)


def test_q6_subsignal_failure_isolated_keeps_triple():
    """单个子信号异常 → 诊断条目 + 保留已合格的三倍纪律（不吞掉独立保护）。"""
    import src.core.exit_signals.stock as stock
    orig = stock._check_holder_reduction
    def _boom(*a, **k):
        raise RuntimeError("公告源故障")
    stock._check_holder_reduction = _boom
    try:
        fs = check_stock_top_signal(
            StockData(stock_code="x", stock_name="x", price=40.0, volume=1000,
                      ma5=42.0, low_60d=10.0),
            "x", announcements=[{"title": "某公司控股股东拟减持"}])
    finally:
        stock._check_holder_reduction = orig
    assert any(f.signal_id == "exit.stock.triple_up" for f in fs), "独立保护不得被吞"
    assert any(f.signal_id.startswith("diag.stock.") for f in fs), "失败须显式诊断"


# ── Q7：行级日期有效性 ──────────────────────────────────

def test_q7_flagbearer_same_day_rows_not_20d(monkeypatch):
    """21 行同一天的旗手数据 ≠ 20 日窗口（Q7）。"""
    import baostock as real_bs
    import src.core.exit_signals.sector as sector

    d = datetime.now().strftime("%Y-%m-%d")
    rows = [[d, "10.0"]] * 25   # 同一天重复 25 行

    class _FakeRS:
        error_code = "0"
        fields = ["date", "close"]

        def __init__(self):
            self._rows = list(rows)
            self._i = 0

        def next(self):
            if self._i >= len(self._rows):
                return False
            self._i += 1
            return True

        def get_row_data(self):
            return self._rows[self._i - 1]

    import src.data.akshare_client as ak_mod
    monkeypatch.setattr(ak_mod, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(real_bs, "query_history_k_data_plus", lambda **k: _FakeRS())
    monkeypatch.setattr(ak_mod, "_call_with_timeout", lambda fn, timeout=20: fn())
    assert sector._fetch_20d_change_pct("600170") is None, "同日重复行不构成 20 日窗口"


def test_q7_flagbearer_future_date_rows_dropped(monkeypatch):
    """未来日期行无资格：剔除后不足 21 条 → None。"""
    import baostock as real_bs
    import src.core.exit_signals.sector as sector

    future = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    rows = [[future, "10.0"]] * 25
    import src.data.akshare_client as ak_mod
    monkeypatch.setattr(ak_mod, "_ensure_baostock_login", lambda: True)

    class _FakeRS:
        error_code = "0"
        fields = ["date", "close"]

        def __init__(self):
            self._rows = list(rows)
            self._i = 0

        def next(self):
            if self._i >= len(self._rows):
                return False
            self._i += 1
            return True

        def get_row_data(self):
            return self._rows[self._i - 1]

    monkeypatch.setattr(real_bs, "query_history_k_data_plus", lambda **k: _FakeRS())
    monkeypatch.setattr(ak_mod, "_call_with_timeout", lambda fn, timeout=20: fn())
    assert sector._fetch_20d_change_pct("600170") is None


def test_q7_holder_future_stat_date_disqualified(monkeypatch, tmp_path):
    """股东统计日 2099-01-01 → 无资格（不得标 OK）。"""
    import src.core.exit_signals.stock as stock
    import pandas as pd
    df = pd.DataFrame({
        "股东户数统计截止日": ["2099-01-01", "2098-01-01"],
        "股东户数-本次": ["200", "100"],
        "股东户数-上次": ["100", "95"],
    })
    monkeypatch.setattr("src.core.benzong.data_provider._safe_call", lambda *a, **k: df)
    monkeypatch.setattr(stock, "_holder_cache_dir", lambda: tmp_path)
    assert stock._check_holder_count_surge("600519") is None


# ── R4a：三层隔离异常路径端到端（Q6 裁决：一个来源报错与有效硬纪律并存）──────

def test_r4a_macro_layer_error_keeps_triple_exit(monkeypatch):
    """宏观层抛异常 → diag 条目 + 三倍定律 exit 资格存活（guard P0-1 固化）。"""
    import src.core.exit_signals as ex
    import src.core.exit_signals.stock as stock
    monkeypatch.setattr(stock, "_check_holder_count_surge", lambda code: None)
    monkeypatch.setattr(stock, "_check_margin_surge", lambda code: None)
    sd = StockData(stock_code="x", stock_name="x", price=40.0, volume=1000,
                   ma5=42.0, low_60d=10.0)
    monkeypatch.setattr(ex, "check_macro_top_signal",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("宏观层故障")))
    fs = ex.check_top_signals(sd, "x", market_turnover_trillion=None,
                              trade_plan=None, live=True)
    diag = [f for f in fs if f.signal_id == "diag.macro.error"]
    assert diag and diag[0].action_scope == "none" and "宏观层" in diag[0].detail
    assert any(f.signal_id == "exit.stock.triple_up" and f.action_scope == "exit"
               for f in fs), "独立硬纪律不得被同层异常吞掉"


def test_r4a_sector_layer_error_keeps_triple_exit(monkeypatch):
    import src.core.exit_signals as ex
    import src.core.exit_signals.stock as stock
    monkeypatch.setattr(stock, "_check_holder_count_surge", lambda code: None)
    monkeypatch.setattr(stock, "_check_margin_surge", lambda code: None)
    sd = StockData(stock_code="x", stock_name="x", price=40.0, volume=1000,
                   ma5=42.0, low_60d=10.0)
    plan = _mk_plan(penetration_stage="30+", flagbearer_code="600170")
    monkeypatch.setattr(ex, "check_sector_top_signal",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("板块层故障")))
    fs = ex.check_top_signals(sd, "x", market_turnover_trillion=None,
                              trade_plan=plan, live=True)
    assert any(f.signal_id == "diag.sector.error" for f in fs)
    assert any(f.signal_id == "exit.stock.triple_up" for f in fs)


def test_r4a_stock_layer_error_produces_diag(monkeypatch):
    import src.core.exit_signals as ex
    sd = StockData(stock_code="x", stock_name="x", price=10, volume=1000)
    monkeypatch.setattr(ex, "check_stock_top_signal",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("个股层故障")))
    fs = ex.check_top_signals(sd, "x", market_turnover_trillion=None,
                              trade_plan=None, live=False)
    assert any(f.signal_id == "diag.stock.error" for f in fs)
    assert all(f.action_scope != "exit" for f in fs)
