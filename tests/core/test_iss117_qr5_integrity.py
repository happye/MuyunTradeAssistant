# -*- coding: utf-8 -*-
"""ISS-117 QR5 日历完整性验收测试（v0.8.29.6，架构师 QR5_ACCEPTANCE.md）。

三个红反例语义落正式回归（架构师探针 qr5_coverage_probes 的业务语义，自包含实现）：

1. 覆盖记录**多出的范围外日期**不得借「不缺行」混过核验——声称 [10-10,10-11] 全
   休市、另带一条 2000 年记录的缓存，strict 消费端精确集合核验拒绝；最终 packet
   不 EXIT、未授权。
2. **原始 JSON 重复键**在 dict 覆盖前拦截——同一天先写「交易」后写「休市」的
   冲突缓存，strict 专用加载（object_pairs_hook）在解析阶段拒绝；最终 packet
   不 EXIT、未授权。
3. 生产端供应商返回**多一条范围外记录**（432 行遍历确实发生）→ 精确集合核验
   抛错、不写合格缓存；查询参数与遍历次数有断言。

保留正例：完整生产全链（生产→保存→读取→授权→最终 packet）、缺行/中途错误/
重复行/坏标志、错股、盘前/盘后、周末/假日、历史 bar、独立价格止损——由
test_iss117_qr4_coverage.py / test_iss117_qr3_closeout.py 承接，本文件不重复。

零网络、零 AI、不触碰真实 ~/.muyun。
跑法：pytest tests/core/test_iss117_qr5_integrity.py
"""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data import akshare_client as quotes
from src.data.models import StockData, TradePlan
from src.core.fear_index import history as fear_history


def _sd(**kw):
    return StockData(**(dict(stock_code="600000", stock_name="synthetic", price=10,
                             volume=1000, avg_volume_20=1000, change_pct=0,
                             quote_as_of="2026-10-09") | kw))


def _plan(**kw):
    return TradePlan(**(dict(plan_id="qr5-integrity", opened_at="2026-10-01",
                             why_buy="synthetic", when_buy="synthetic", how_much=.2,
                             locked_initial_stop=8, current_stop=8, max_hold_days=180) | kw))


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


def _freeze(monkeypatch, now):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=tz)
    monkeypatch.setattr(fear_history, "datetime", FrozenDateTime)
    monkeypatch.setattr(fear_history, "BASE_DIR", None)  # 占位，由调用方覆盖


def _write_calendar(monkeypatch, tmp_path, now, coverage):
    _freeze(monkeypatch, now)
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    fear_history._atomic_write_json(fear_history.BASE_DIR / "trade_dates.json", {
        "version": fear_history.FEAR_VERSION,
        "built_at": now.isoformat(),
        "dates": sorted(d for d, f in coverage["days"].items() if f == 1),
        "coverage": coverage,
    })


def _final_packet(monkeypatch, quote_day, analysis_day):
    engine = _offline_engine(monkeypatch)
    return engine.analyze_packet(
        _sd(price=30, ma5=31, low_60d=10, quote_as_of=quote_day),
        confirmed_ratio=.2, position_state="HELD", current_position_ratio=.2,
        has_position=True, trade_plan=_plan(), today=analysis_day, ai_enabled=False)


# ── 反例一：范围外多日期不得借「不缺行」获得授权 ─────────────

def test_extra_out_of_range_row_cannot_authorize_old_quote(monkeypatch, tmp_path):
    """声称覆盖 [10-10,10-11]（两天均明确休市）+ 偷带一条 2000 年记录：
    strict 精确集合核验拒绝；真实末端 packet 不 EXIT、未授权。"""
    from src.core.decision_contract import DesiredAction
    _write_calendar(monkeypatch, tmp_path, datetime(2026, 10, 11, 16),
                    {"schema": 1, "start": "2026-10-10", "end": "2026-10-11",
                     "days": {"2026-10-10": 0, "2026-10-11": 0, "2000-01-04": 1}})
    # strict 层：集合精确核验直接拒绝（多行诊断）
    with pytest.raises(RuntimeError, match="范围外"):
        fear_history.trading_days(allow_refresh=False)
    # 末端：最终 packet 不 EXIT、未授权
    packet, dr, out, _, _ = _final_packet(monkeypatch, "2000-01-04", "2026-10-11")
    assert packet.desired_action != DesiredAction.EXIT
    assert not out.top_signal_authorized
    assert any("行情" in w or "日历" in w for w in dr.warnings), dr.warnings


# ── 反例二：原始 JSON 重复键在 dict 覆盖前拦截 ───────────────

def test_raw_duplicate_json_key_rejected_before_dict_merge(monkeypatch, tmp_path):
    """同一天先写「交易1」后写「休市0」：strict 专用加载在原始解析阶段拒绝
    （json.load 的后写覆盖会隐藏冲突），真实末端 packet 不 EXIT、未授权。"""
    from src.core.decision_contract import DesiredAction
    _freeze(monkeypatch, datetime(2026, 10, 9, 16))
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    fear_history.BASE_DIR.mkdir(parents=True, exist_ok=True)
    # 故意不用 dict 序列化：dict 会先吃掉重复键，测不到真实消费路径
    raw = ('{"version":' + json.dumps(fear_history.FEAR_VERSION)
           + ',"built_at":"2026-10-09T16:00:00",'
           + '"dates":["2026-10-08","2026-10-09"],'
           + '"coverage":{"schema":1,"start":"2026-10-08","end":"2026-10-09",'
           + '"days":{"2026-10-08":1,"2026-10-09":1,"2026-10-09":0}}}')
    (fear_history.BASE_DIR / "trade_dates.json").write_text(raw, encoding="utf-8")
    with pytest.raises(RuntimeError, match="重复键"):
        fear_history.trading_days(allow_refresh=False)
    packet, dr, out, _, _ = _final_packet(monkeypatch, "2026-10-08", "2026-10-09")
    assert packet.desired_action != DesiredAction.EXIT
    assert not out.top_signal_authorized
    assert dr.warnings, "拒绝原因须对用户可见"


# ── 反例三：生产端多一条范围外记录 → 不盖合格章 ──────────────

class _CountingRS:
    error_code = "0"

    def __init__(self, rows, calls):
        self._rows, self._i, self._calls = rows, -1, calls

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        self._calls["rows"] += 1
        return self._rows[self._i]


def test_producer_refuses_extra_out_of_range_row(monkeypatch, tmp_path):
    """供应商返回查询区间全部 431 个自然日 + 多一条 2000 年记录：查询参数与
    432 行遍历确实发生；生产端精确集合核验抛错、不写合格缓存。"""
    now = datetime(2026, 10, 11, 16)
    _freeze(monkeypatch, now)
    monkeypatch.setattr(fear_history, "BASE_DIR", tmp_path / "calendar")
    calls = {"query": [], "rows": 0}

    def query(start_date, end_date):
        calls["query"].append((start_date, end_date))
        cur = datetime.fromisoformat(start_date).date()
        stop = datetime.fromisoformat(end_date).date()
        rows = []
        while cur <= stop:
            rows.append([cur.isoformat(), "1" if cur.weekday() < 5 else "0"])
            cur += timedelta(days=1)
        return _CountingRS(rows + [["2000-01-04", "1"]], calls)  # 多一条范围外记录

    import baostock as bs
    monkeypatch.setattr(quotes, "_ensure_baostock_login", lambda: True)
    monkeypatch.setattr(quotes, "_call_with_timeout", lambda fn, timeout=None: fn())
    monkeypatch.setattr(bs, "query_trade_dates", query)
    with pytest.raises(RuntimeError, match="范围外"):
        fear_history.trading_days()
    # 查询确实发生且参数正确（冻结时钟 now-400 ~ now+30）
    assert calls["query"] == [((now.date() - timedelta(days=400)).isoformat(),
                               (now.date() + timedelta(days=30)).isoformat())]
    assert calls["rows"] == 431 + 1  # 目标全集 431 行 + 范围外 1 行，全部遍历
    assert not (tmp_path / "calendar" / "trade_dates.json").exists()


# ── 单元级：共用校验助手的精确集合语义 ──────────────────────

def test_verify_days_rejects_missing_and_extra_with_clear_diff():
    from src.core.fear_index.history import _verify_calendar_days
    with pytest.raises(RuntimeError, match="缺 1 天"):
        _verify_calendar_days({"2026-10-08": 1}, "2026-10-08", "2026-10-09")
    with pytest.raises(RuntimeError, match="范围外"):
        _verify_calendar_days({"2026-10-08": 1, "2026-10-09": 1, "2000-01-04": 1},
                              "2026-10-08", "2026-10-09")
    with pytest.raises(RuntimeError, match="重复日期"):
        _verify_calendar_days({"2026-10-08": 1, "2026-10-8": 0},
                              "2026-10-08", "2026-10-09")  # 归一化后同键不同标志
    with pytest.raises(RuntimeError, match="非法交易标志"):
        _verify_calendar_days({"2026-10-08": True}, "2026-10-08", "2026-10-08")
    # 精确相等通过
    assert _verify_calendar_days({"2026-10-08": 1, "2026-10-09": 0},
                                 "2026-10-08", "2026-10-09") == {
        "2026-10-08": 1, "2026-10-09": 0}


def test_strict_load_rejects_duplicate_key_at_any_depth(tmp_path):
    """重复键拦截覆盖任意层级（顶层/coverage/days 均受保护）。"""
    p = tmp_path / "trade_dates.json"
    p.write_text('{"a":1,"a":2}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="重复键"):
        fear_history._load_json_strict(p)
    p.write_text('{"x":{"y":1,"y":2}}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="重复键"):
        fear_history._load_json_strict(p)
    # 合法 JSON 正常返回
    p.write_text('{"a":1,"b":{"c":2}}', encoding="utf-8")
    assert fear_history._load_json_strict(p) == {"a": 1, "b": {"c": 2}}
    # 文件缺失 → None（走「缓存无效」拒绝路径，与宽容版语义一致）
    assert fear_history._load_json_strict(tmp_path / "nope.json") is None


# ── 消费者范围防护：交易日选取限于已核定区间 ─────────────────

def test_gate_selects_trading_day_within_confirmed_range(monkeypatch, tmp_path):
    """门内最近已完成交易日选取带下界（覆盖起点）——即使上游核验被绕过，
    消费者也不使用核定区间外的日期（防御纵深，QR5 指示 3）。"""
    from src.core.exit_signals import authorize_hard_findings
    from src.data.models import SignalFinding
    _write_calendar(monkeypatch, tmp_path, datetime(2026, 10, 9, 16),
                    {"schema": 1, "start": "2026-10-08", "end": "2026-10-09",
                     "days": {"2026-10-08": 1, "2026-10-09": 1}})
    cal = fear_history.trading_days(allow_refresh=False)
    assert cal["qualified"] and cal["start"] == "2026-10-08"
    # 正常授权路径不受范围防护影响
    f = SignalFinding(signal_id="exit.stock.triple_up", source="synthetic",
                      securities=["600000"], as_of="2026-10-09", data_quality="OK",
                      action_scope="exit", verified=True,
                      strategy_binding="jiaoxue6-8/mode-rule（既有周期策略纪律）",
                      detail="x", reason="x")
    ok, rej = authorize_hard_findings([f], "600000", live=True,
                                      analysis_date="2026-10-09", mode="jianzong",
                                      stock_data=_sd(price=30, ma5=31, low_60d=10))
    assert ok and not rej, [r.reason for r in rej]
