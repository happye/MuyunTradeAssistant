# -*- coding: utf-8 -*-
"""2026-10-20 试跑问题回归：WATCH 持仓动作、决策/动作展示一致、K 线补算量比/60日、
稳健抓取缓存、垫片交易日历不再把当日/未来标休市。全部零网络。"""
import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.core.strategy_layer import StrategyLayer  # noqa: E402
from src.data.models import (  # noqa: E402
    DecisionResult, MarketState, PositionAction, SignalType, StockData, StrategyState,
)


def _dr(decision=SignalType.WATCH):
    return DecisionResult(stock=StockData(stock_code="000001", stock_name="t", price=10.0,
                                          volume=1, ma20=10.5, ma60=11.0),
                          state=MarketState.TRANSITION, decision=decision, score=0.1,
                          signals=[], trace=[])


@pytest.mark.parametrize("ratio,expected", [
    (0.15, PositionAction.HOLD_POSITION),   # 持仓 + WATCH → 维持（不是「空仓观望」）
    (None, PositionAction.HOLD_POSITION),   # 有仓但权重未知 → 维持
    (0.0, PositionAction.STAY_OUT),         # 确认空仓 → 观望
])
def test_watch_action_depends_on_position(ratio, expected):
    sl = StrategyLayer()
    st = StrategyState(current_position_ratio=ratio)
    act, _ = sl._calculate_position(SignalType.WATCH, MarketState.TRANSITION, _dr(), st)
    assert act == expected


def _load_pipeline():
    spec = importlib.util.spec_from_file_location("_pipeline_mod", _ROOT / "scripts" / "pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    import os
    cwd = os.getcwd()
    try:
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)
    return mod


def test_decision_label_shows_engine_to_strategy_downgrade():
    p = _load_pipeline()
    c = {"decision": "BUY", "strategy_decision": "WATCH", "position_action": "STAY_OUT",
         "downgrade_reasons": ["信号确认: 等待确认"]}
    lab = p.decision_label(c)
    assert lab.startswith("引擎BUY→WATCH") and "确认" in lab
    assert p.decision_label({"decision": "BUY", "strategy_decision": "BUY"}) == "BUY"
    assert "WATCH→空仓观望、持仓维持" in p.DECISION_RULE


def test_kline_metrics_from_rows():
    from src.scanner.scanner_engine import ScannerEngine
    start = datetime(2026, 1, 1)
    rows = [((start + timedelta(days=i)).strftime("%Y-%m-%d"), 10.0, 100.0) for i in range(60)]
    rows.append(((start + timedelta(days=60)).strftime("%Y-%m-%d"), 12.0, 50.0))
    m = ScannerEngine.kline_metrics_from_rows(rows)
    assert m["量比"] == 0.5
    assert m["60日涨跌幅"] == 20.0
    short = ScannerEngine.kline_metrics_from_rows(rows[:5])
    assert short == {"量比": None, "60日涨跌幅": None}   # 数据不足不编造


def test_volume_ratio_filter_applied_after_kline_enrich(monkeypatch):
    from src.scanner.scanner_engine import ScannerEngine
    eng = ScannerEngine.__new__(ScannerEngine)
    monkeypatch.setattr(eng, "_kline_metrics", lambda codes: {
        "000001": {"量比": 0.6, "60日涨跌幅": 5.0}, "000002": {"量比": 1.8, "60日涨跌幅": 1.0}})
    df = pd.DataFrame({"代码": ["000001", "000002", "000003"], "涨跌幅": [-1.0, -1.0, -1.0]})
    out = eng._apply_filters_with_kline_enrich(
        df, [{"field": "change_pct", "op": "between", "value": [-3, -0.1]},
             {"field": "volume_ratio", "op": "lt", "value": 1.0}], "change_60d")
    assert out["代码"].tolist() == ["000001"]   # 000003 缺 K 线 → 不满足；000002 放量被剔
    assert "60日涨跌幅" in out.columns


def test_robust_call_cache_retry_and_stale(tmp_path, monkeypatch):
    from src.data import robust_fetch as rf
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("JSONDecodeError-like")
        return {"v": 1}

    assert rf.robust_call("t", "k", flaky, retries=1, backoff=0) == {"v": 1}
    assert calls["n"] == 2
    # TTL 内直接命中缓存，不再调用
    assert rf.robust_call("t", "k", lambda: 1 / 0, ttl=3600, backoff=0) == {"v": 1}
    assert rf.LAST_STATUS[("t", "k")] == "fresh_cache"
    # 过 TTL 且失败 → 过期兜底
    assert rf.robust_call("t", "k", lambda: 1 / 0, ttl=0, retries=0) == {"v": 1}
    assert rf.LAST_STATUS[("t", "k")] == "stale_cache"
    # 超 stale_max → None（不编造）
    assert rf.robust_call("t", "k", lambda: 1 / 0, ttl=0, stale_max=-1, retries=0) is None
    # 超时被短路
    t = time.time()
    assert rf.robust_call("t", "slow", lambda: time.sleep(3), timeout=0.2, retries=0) is None
    assert time.time() - t < 2


def test_shim_calendar_prefers_sina_and_never_marks_unknown_days_closed(monkeypatch):
    from src.data import bs_shim
    import akshare as ak
    monkeypatch.setattr(ak, "tool_trade_date_hist_sina", lambda: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(bs_shim, "_tx_kline", lambda *a, **k: (("2026-10-15",), ("2026-10-16",)))
    rs = bs_shim.query_trade_dates("2026-10-14", "2026-10-25")
    rows = rs.get_data().values.tolist()
    assert rows[-1][0] == "2026-10-16"          # 无证据的 10-17 之后不返回（不误标休市）
    monkeypatch.setattr(ak, "tool_trade_date_hist_sina",
                        lambda: pd.DataFrame({"trade_date": ["2026-10-16", "2026-10-19", "2026-10-20"]}))
    rows = dict(bs_shim.query_trade_dates("2026-10-16", "2026-10-20").get_data().values.tolist())
    assert rows == {"2026-10-16": "1", "2026-10-17": "0", "2026-10-18": "0",
                    "2026-10-19": "1", "2026-10-20": "1"}


def test_trading_days_ttl_display_without_rebuild_and_strict_rejects_incomplete(monkeypatch, tmp_path):
    from src.core.fear_index import history as h
    monkeypatch.setattr(h, "BASE_DIR", tmp_path)
    today = datetime.now()
    dates = [(today - timedelta(days=3)).strftime("%Y-%m-%d")]
    (tmp_path / "trade_dates.json").write_text(json.dumps({
        "version": h.FEAR_VERSION, "built_at": today.isoformat(timespec="seconds"),
        "dates": dates}), encoding="utf-8")

    import src.data.akshare_client as ac
    monkeypatch.setattr(ac, "_ensure_baostock_login",
                        lambda: (_ for _ in ()).throw(AssertionError("TTL display must not refresh")))
    assert h.trading_days() == dates
    strict = h.trading_days(allow_refresh=False)
    assert strict["qualified"] is False and strict["legacy_dates"] == dates

    coverage = {"schema": 1, "start": "2026-10-01", "end": "2026-10-03",
                "days": {"2026-10-01": 1, "2026-10-02": 0}}
    (tmp_path / "trade_dates.json").write_text(json.dumps({
        "version": h.FEAR_VERSION, "built_at": today.isoformat(timespec="seconds"),
        "dates": dates, "coverage": coverage}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        h.trading_days(allow_refresh=False)
