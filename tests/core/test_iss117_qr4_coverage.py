# -*- coding: utf-8 -*-
"""ISS-117 QR4 覆盖合同验收测试（v0.8.29.5，架构师 QR4_ACCEPTANCE.md）。

固定验收矩阵 7 行 + 日历「生产→保存→读取→授权→最终 packet」全链 + Q-R1 行情
主体证券独立核对：

- 日历生产：供应商 query_trade_dates 全行保留（交易日/非交易日标志），核验查询
  正常结束、日期唯一、标志合法、返回集合与查询区间自然日全集完全一致——缺行/
  中途错误/重复/异常条目一律不写缓存（不盖覆盖合格章），绝不按星期补假记录。
- strict 只读消费：复核新 schema 覆盖记录逐自然日完整（不信任生产者自我声明）；
  旧 schema（缺覆盖元数据）不得授权硬退出（UNKNOWN），fear 展示路径不受影响。
- 授权门：分析日必须在已确认覆盖内；最近已完成交易日由明确记录判定；场次
  （盘前/盘中/15点后/周末/假日）用同份日历；2000 bar 配 2000 证据合法可授权、
  2026 live 配 2000 行情拒绝。
- Q-R1：行情主体证券独立核对（复算证券来自数据自身代码，不再是申请参数回填）。

零网络、零 AI、不触碰真实 ~/.muyun。
跑法：pytest tests/core/test_iss117_qr4_coverage.py
"""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data import akshare_client as quotes
from src.data.models import SignalFinding, StockData, TradePlan
from src.core.exit_signals import authorize_hard_findings
from src.core.fear_index import history as fear_history

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
    return TradePlan(**(dict(plan_id="qr4-coverage", opened_at="2026-10-01",
                             why_buy="synthetic", when_buy="synthetic", how_much=.2,
                             locked_initial_stop=8, current_stop=8, max_hold_days=180) | kw))


def _qualified_calendar(monkeypatch, tmp_path, now, days=None, built_at=None):
    """新 schema 覆盖合同缓存：days={date: 0|1} 完整自然日记录。"""
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=tz)
    monkeypatch.setattr(fear_history, "datetime", FrozenDateTime)
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    if days is None:
        days = {"2026-10-07": 1, "2026-10-08": 1, "2026-10-09": 1}
    fear_history._atomic_write_json(fear_history.BASE_DIR / "trade_dates.json", {
        "version": fear_history.FEAR_VERSION,
        "built_at": built_at if built_at is not None else now.isoformat(),
        "dates": sorted(d for d, f in days.items() if f == 1),
        "coverage": {"schema": 1, "start": min(days), "end": max(days), "days": days},
    })


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


# ── 矩阵行 1：合格完整日历，各场次合格三倍仍可硬退出 ─────────

@pytest.mark.parametrize("now,analysis,quote,days", [
    # 盘前（09:00）：当日未收盘，认上一交易日收盘
    (datetime(2026, 10, 9, 9), "2026-10-09", "2026-10-08",
     {"2026-10-08": 1, "2026-10-09": 1}),
    # 盘后（16:00）：当日已完成，当日收盘行情合格
    (datetime(2026, 10, 9, 16), "2026-10-09", "2026-10-09",
     {"2026-10-08": 1, "2026-10-09": 1}),
    # 周末：周六明确记录为非交易日，认周五
    (datetime(2026, 10, 10, 16), "2026-10-10", "2026-10-09",
     {"2026-10-09": 1, "2026-10-10": 0}),
    # 明确假日：假日记录在案，认上一交易日
    (datetime(2026, 10, 2, 16), "2026-10-02", "2026-09-30",
     {"2026-09-30": 1, "2026-10-01": 0, "2026-10-02": 0}),
], ids=["premarket_prev_close", "postmarket_same_day", "weekend", "explicit_holiday"])
def test_matrix1_qualified_calendar_authorizes_by_session(monkeypatch, tmp_path,
                                                          now, analysis, quote, days):
    _qualified_calendar(monkeypatch, tmp_path, now, days=days)
    ok, rej = authorize_hard_findings([_hard(as_of=quote)], "600000", live=True,
                                      analysis_date=analysis, mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of=quote))
    assert ok and not rej, [r.reason for r in rej]


# ── 矩阵行 2：缺/过期/坏日历、未知版本、缺覆盖元数据 → UNKNOWN，无新网络 ──

def _reject_reason(monkeypatch, tmp_path, write=None, quote="2026-10-09",
                   analysis="2026-10-09"):
    if write is not None:
        write()
    else:
        monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "empty-calendar")
    attempts = []
    monkeypatch.setattr(quotes, "_ensure_baostock_login",
                        lambda: attempts.append("login") or False)
    ok, rej = authorize_hard_findings([_hard(as_of=quote)], "600000", live=True,
                                      analysis_date=analysis, mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of=quote))
    return ok, rej, attempts


@pytest.mark.parametrize("kind", ["missing", "expired", "corrupt_built_at",
                                  "unknown_version", "no_coverage_meta"],
                         ids=["missing", "expired", "corrupt_built_at",
                              "unknown_version", "no_coverage_meta"])
def test_matrix2_unqualified_calendar_unknown_no_network(monkeypatch, tmp_path, kind):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 9, 16) if tz is None else datetime(2026, 10, 9, 16)
    monkeypatch.setattr(fear_history, "datetime", FrozenDateTime)
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    payload = {"version": fear_history.FEAR_VERSION,
               "built_at": datetime(2026, 10, 9, 16).isoformat(),
               "dates": ["2026-10-08", "2026-10-09"],
               "coverage": {"schema": 1, "start": "2026-10-08", "end": "2026-10-09",
                            "days": {"2026-10-08": 1, "2026-10-09": 1}}}
    if kind == "expired":
        payload["built_at"] = (datetime(2026, 10, 9, 16) - timedelta(days=8)).isoformat()
    elif kind == "corrupt_built_at":
        payload["built_at"] = "not-a-date"
    elif kind == "unknown_version":
        payload["version"] = "v0.0.0-unknown"
    elif kind == "no_coverage_meta":
        payload.pop("coverage")  # 旧 schema

    def _write():
        fear_history._atomic_write_json(fear_history.BASE_DIR / "trade_dates.json", payload)
    # missing：不写任何缓存文件；其余 kind：写入对应变形
    ok, rej, attempts = _reject_reason(monkeypatch, tmp_path,
                                       write=_write if kind != "missing" else None)
    assert not ok and rej
    assert attempts == [], attempts  # 无新网络：绝不触发供应商登录


# ── 矩阵行 3：覆盖待确认（边界外/稀疏行）不授权 ─────────────

def test_matrix3_cache_ends_friday_monday_analysis_rejected(monkeypatch, tmp_path):
    _qualified_calendar(monkeypatch, tmp_path, datetime(2026, 10, 12, 16),
                        built_at="2026-10-09T16:00:00",
                        days={"2026-10-07": 1, "2026-10-08": 1, "2026-10-09": 1})
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000", live=True,
                                      analysis_date="2026-10-12", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2026-10-09"))
    assert not ok and rej
    assert "覆盖" in rej[0].reason


def test_matrix3_future_row_cannot_prove_coverage(monkeypatch, tmp_path):
    """2000 一行 + 2026 未来一行：消费端逐自然日复核发现几十年空洞 → 拒绝。"""
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 12, 16) if tz is None else datetime(2026, 10, 12, 16)
    monkeypatch.setattr(fear_history, "datetime", FrozenDateTime)
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    fear_history._atomic_write_json(fear_history.BASE_DIR / "trade_dates.json", {
        "version": fear_history.FEAR_VERSION,
        "built_at": "2026-10-09T16:00:00",
        "dates": ["2000-01-04", "2026-11-02"],
        "coverage": {"schema": 1, "start": "2000-01-04", "end": "2026-11-02",
                     "days": {"2000-01-04": 1, "2026-11-02": 1}},
    })
    ok, rej = authorize_hard_findings([_hard(as_of="2000-01-04")], "600000", live=True,
                                      analysis_date="2026-10-12", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2000-01-04"))
    assert not ok and rej


# ── 矩阵行 4：生产端核验——缺行/中途错误/重复不盖覆盖合格章 ──

class _TradeDatesRS:
    def __init__(self, rows, error_code="0"):
        self._rows, self._i, self.error_code = rows, -1, error_code

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return self._rows[self._i]


def _prod_calendar(monkeypatch, tmp_path, now, rows, error_code="0"):
    """mock 供应商 query_trade_dates + 冻结时钟，跑 trading_days(allow_refresh=True)。"""
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=tz)
    monkeypatch.setattr(fear_history, "datetime", FrozenDateTime)
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    monkeypatch.setattr(quotes, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(quotes, "_call_with_timeout", lambda fn, timeout=None: fn())
    import baostock as bs
    monkeypatch.setattr(bs, "query_trade_dates",
                        lambda start_date, end_date: _TradeDatesRS(rows, error_code))


def _full_supplier_rows(now):
    """[now-400, now+30] 全自然日：周末 flag=0、其余 flag=1（合成供应商全量返回）。"""
    cur = (now - timedelta(days=400)).date()
    stop = (now + timedelta(days=30)).date()
    rows = []
    while cur <= stop:
        rows.append([cur.isoformat(), "0" if cur.weekday() >= 5 else "1"])
        cur += timedelta(days=1)
    return rows


def test_matrix4_production_writes_confirmed_coverage(monkeypatch, tmp_path):
    """正常生产：全量自然日返回 → 缓存写新 schema（coverage+兼容 dates）→
    strict 读取 qualified → 门授权 → 最终 packet EXIT（生产→保存→读取→授权→末端）。"""
    from src.core.decision_contract import DesiredAction
    now = datetime(2026, 10, 9, 12)
    _prod_calendar(monkeypatch, tmp_path, now, _full_supplier_rows(now))
    days = fear_history.trading_days()          # 生产路径（重建缓存）
    assert isinstance(days, list) and days      # 兼容列表语义保留
    cal = fear_history.trading_days(allow_refresh=False)   # strict 读取
    assert cal["qualified"] is True and cal["days"]
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-08")], "600000", live=True,
                                      analysis_date="2026-10-09", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2026-10-08"))
    assert ok and not rej, [r.reason for r in rej]
    engine = _offline_engine(monkeypatch)
    packet, _, out, _, _ = engine.analyze_packet(
        _sd(price=30, ma5=31, low_60d=10), confirmed_ratio=.2, position_state="HELD",
        current_position_ratio=.2, has_position=True, trade_plan=_plan(),
        today="2026-10-09", ai_enabled=False)
    assert packet.desired_action == DesiredAction.EXIT
    assert out.top_signal_authorized


def test_matrix4_supplier_missing_row_not_stamped(monkeypatch, tmp_path):
    """供应商少返回一天 → 生产端核验失败抛错、不写缓存（不盖覆盖合格章）。"""
    now = datetime(2026, 10, 9, 12)
    rows = _full_supplier_rows(now)
    rows.pop(len(rows) // 2)                    # 抽掉中间一天
    _prod_calendar(monkeypatch, tmp_path, now, rows)
    with pytest.raises(RuntimeError, match="不完整"):
        fear_history.trading_days()
    assert not (tmp_path / "calendar" / "trade_dates.json").exists()


def test_matrix4_supplier_error_not_stamped(monkeypatch, tmp_path):
    """查询未正常结束（error_code≠0）→ 不写缓存。"""
    now = datetime(2026, 10, 9, 12)
    _prod_calendar(monkeypatch, tmp_path, now, _full_supplier_rows(now), error_code="1")
    with pytest.raises(RuntimeError, match="未正常结束"):
        fear_history.trading_days()
    assert not (tmp_path / "calendar" / "trade_dates.json").exists()


def test_matrix4_supplier_duplicate_not_stamped(monkeypatch, tmp_path):
    """同日重复行 → 不写缓存（唯一性核验）。"""
    now = datetime(2026, 10, 9, 12)
    rows = _full_supplier_rows(now)
    rows.append(list(rows[10]))
    _prod_calendar(monkeypatch, tmp_path, now, rows)
    with pytest.raises(RuntimeError, match="重复"):
        fear_history.trading_days()
    assert not (tmp_path / "calendar" / "trade_dates.json").exists()


def test_matrix4_supplier_bad_flag_not_stamped(monkeypatch, tmp_path):
    """非法交易标志（非 0/1）→ 不写缓存。"""
    now = datetime(2026, 10, 9, 12)
    rows = _full_supplier_rows(now)
    rows[10][1] = "maybe"
    _prod_calendar(monkeypatch, tmp_path, now, rows)
    with pytest.raises(RuntimeError, match="非法交易标志"):
        fear_history.trading_days()


def test_matrix4_fear_display_still_served_by_legacy_cache(monkeypatch, tmp_path):
    """旧 schema 缓存继续服务 fear 展示（trading_days 原列表语义），仅硬授权 UNKNOWN。"""
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 9, 16) if tz is None else datetime(2026, 10, 9, 16)
    monkeypatch.setattr(fear_history, "datetime", FrozenDateTime)
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    fear_history._atomic_write_json(fear_history.BASE_DIR / "trade_dates.json", {
        "version": fear_history.FEAR_VERSION,
        "built_at": datetime(2026, 10, 9, 16).isoformat(),
        "dates": ["2026-10-08", "2026-10-09"]})   # 旧 schema：无 coverage
    assert fear_history.trading_days() == ["2026-10-08", "2026-10-09"]  # 展示不受影响
    cal = fear_history.trading_days(allow_refresh=False)
    assert cal["qualified"] is False              # 硬授权资格 UNKNOWN
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000", live=True,
                                      analysis_date="2026-10-09", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10))
    assert not ok and rej                         # 不得授权


# ── 矩阵行 5：当前行情错股/无证券/缺时点 → 拒授权且有原因 ────

@pytest.mark.parametrize("kw,frag", [
    (dict(stock_code="600999"), "不一致"),          # 错股
    (dict(quote_as_of=None), "复算"),                # 缺时点（复算 UNKNOWN）
], ids=["wrong_stock", "missing_timestamp"])
def test_matrix5_unqualified_current_data_rejected_with_reason(monkeypatch, tmp_path, kw, frag):
    _qualified_calendar(monkeypatch, tmp_path, datetime(2026, 10, 9, 16))
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000", live=False,
                                      analysis_date="2026-10-09", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10, **kw))
    assert not ok and rej
    assert frag in rej[0].reason


def test_matrix5_data_stock_code_missing_rejected(monkeypatch, tmp_path):
    """行情对象无 stock_code：主体不可核实 → 拒绝。"""
    _qualified_calendar(monkeypatch, tmp_path, datetime(2026, 10, 9, 16))
    data = _sd(price=30, ma5=31, low_60d=10)
    data.stock_code = ""
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000", live=False,
                                      analysis_date="2026-10-09", mode="jianzong",
                                      stock_data=data)
    assert not ok and rej
    assert "不一致" in rej[0].reason


def test_matrix5_suffixed_code_normalized_positive(monkeypatch, tmp_path):
    """带后缀代码沿既有规范化口径（split('.')[0]）一致 → 合法同股正例保留。"""
    _qualified_calendar(monkeypatch, tmp_path, datetime(2026, 10, 9, 16))
    ok, rej = authorize_hard_findings([_hard(as_of="2026-10-09")], "600000", live=False,
                                      analysis_date="2026-10-09", mode="jianzong",
                                      stock_data=_sd(stock_code="600000.SH",
                                                     price=30, ma5=31, low_60d=10))
    assert ok and not rej, [r.reason for r in rej]


# ── 矩阵行 6：历史 bar 回放合法，live 陈旧行情拒绝 ───────────

def test_matrix6_historical_bar_replay_authorized():
    """2000 年 bar 配 2000 证据（回测上下文，独立分析日期）→ 合法可授权。"""
    ok, rej = authorize_hard_findings([_hard(as_of="2000-01-04")], "600000", live=False,
                                      analysis_date="2000-01-04", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2000-01-04"))
    assert ok and not rej, [r.reason for r in rej]


def test_matrix6_same_quote_rejected_in_live_context(monkeypatch, tmp_path):
    """2026 live 配 2000 行情：覆盖合格也因陈旧拒绝。"""
    _qualified_calendar(monkeypatch, tmp_path, datetime(2026, 10, 9, 16))
    ok, rej = authorize_hard_findings([_hard(as_of="2000-01-04")], "600000", live=True,
                                      analysis_date="2026-10-09", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10,
                                                     quote_as_of="2000-01-04"))
    assert not ok and rej
    assert "陈旧" in rej[0].reason


# ── 矩阵行 7：坏日历同时存在有效价格止损 → 独立保护仍生效 ────

def test_matrix7_bad_calendar_price_stop_still_closes(monkeypatch, tmp_path):
    """日历缺覆盖元数据（三倍降提醒）时，价格止损等独立合格保护照常 CLOSE_ALL。"""
    from src.core.decision_contract import DesiredAction
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "empty-calendar")
    engine = _offline_engine(monkeypatch)
    packet, _, out, _, _ = engine.analyze_packet(
        _sd(price=7),                      # price 7 < current_stop 8 → 穿止损
        confirmed_ratio=.2, position_state="HELD", current_position_ratio=.2,
        has_position=True, trade_plan=_plan(), today="2026-10-09", ai_enabled=False)
    assert packet.desired_action == DesiredAction.EXIT
