"""观察池（v0.8.12）解析、存储层与执行路径测试

- parse_input: "watch"/"watch add <代码|#N>"/"watch rm"/"观察池" 分流
- session_state: 事件追加/在池重放（remove 抵消、重复 add 防御）、watch_entry_of
- _watch_pool_touch: WATCH 无持仓 → added；同股二次分析 → in_pool 不重复入库；
  BUY / 有持仓 / entry触发 → None（不入池）
- _print_plain_summary: added/in_pool 两态文案（措辞与事实一致）
- watch_pool list: mock 行情/K线/指数，断言涨跌与走势渲染；空池提示；add/rm 幂等

零网络、零 AI、不触碰真实 ~/.muyun（session_state 与 main 的状态文件常量重定向 tmp_path）。
跑法：pytest tests/core/test_watch_pool.py
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import start
from src.cli import session_state
from src.cli import main as cli_main


# ── parse_input 分流 ─────────────────────────────────────

def test_parse_watch_list_default():
    assert start.parse_input("watch") == ("watch_pool", {"action": "list", "code": "", "name": "", "price": None})
    assert start.parse_input("观察池")[0] == "watch_pool"


def test_parse_watch_add():
    mode, args = start.parse_input("watch add 600519")
    assert mode == "watch_pool" and args["action"] == "add" and args["code"] == "600519"
    mode, args = start.parse_input("watch add 600519 贵州茅台 1257.5")
    assert args["name"] == "贵州茅台" and args["price"] == 1257.5
    mode, args = start.parse_input("watch 加入 000001")
    assert args["action"] == "add" and args["code"] == "000001"


def test_parse_watch_rm():
    mode, args = start.parse_input("watch rm 600519")
    assert mode == "watch_pool" and args["action"] == "rm" and args["code"] == "600519"
    mode, args = start.parse_input("watch 移出 000001")
    assert args["action"] == "rm"


# ── session_state 存储层 ─────────────────────────────────

def _redirect_state(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(session_state, "_WATCH_FILE", tmp_path / "watchlist.jsonl")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")


def test_watch_add_remove_replay(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    item = {"code": "600519", "name": "贵州茅台", "price": 1257.0}
    assert session_state.append_watch_event("add", [item], "watch add 600519") is True
    active = session_state.get_watch_active()
    assert len(active) == 1 and active[0]["items"][0]["code"] == "600519"
    entry = session_state.watch_entry_of("600519")
    assert entry is not None and entry["item"]["price"] == 1257.0

    assert session_state.append_watch_event("remove", [{"code": "600519"}], "watch rm 600519") is True
    assert session_state.get_watch_active() == []
    assert session_state.watch_entry_of("600519") is None


def test_watch_remove_unknown_code_is_noop(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    session_state.append_watch_event("remove", [{"code": "600519"}], "watch rm 600519")
    assert session_state.get_watch_active() == []   # remove 未入池码是 no-op


def test_watch_readd_after_remove_new_anchor(monkeypatch, tmp_path):
    """移出后再入池 → 新事件新锚点（旧的入池价不再出现）。"""
    _redirect_state(monkeypatch, tmp_path)
    session_state.append_watch_event("add", [{"code": "000001", "price": 10.0}], "第一次")
    session_state.append_watch_event("remove", [{"code": "000001"}], "移出")
    session_state.append_watch_event("add", [{"code": "000001", "price": 12.0}], "第二次")
    active = session_state.get_watch_active()
    assert len(active) == 1 and active[0]["items"][0]["price"] == 12.0
    assert active[0]["source"] == "第二次"


def test_watch_invalid_action_rejected(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    assert session_state.append_watch_event("bogus", [{"code": "600519"}], "x") is False
    assert session_state.append_watch_event("add", [], "x") is False
    assert session_state.get_watch_events() == []


def test_watch_separate_from_scan_history(monkeypatch, tmp_path):
    """观察池与扫描历史是两个文件——互不污染。"""
    _redirect_state(monkeypatch, tmp_path)
    session_state.append_watch_event("add", [{"code": "600519"}], "watch")
    session_state.save_last_scan([{"code": "000001"}], "scan market 健康回调")
    assert len(session_state.get_watch_active()) == 1
    assert len(session_state.get_scan_history()) == 1
    assert session_state.get_scan_history()[0]["items"][0]["code"] == "000001"


# ── _watch_pool_touch 钩子 ───────────────────────────────

class _FakeDecision:
    def __init__(self, value):
        self.decision = type("D", (), {"value": value})()


class _FakeSD:
    def __init__(self, code="600519", name="贵州茅台", price=1250.0):
        self.stock_code = f"{code}.SH"
        self.stock_name = name
        self.price = price


def test_touch_adds_on_watch(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    sd = _FakeSD()
    info = cli_main._watch_pool_touch(_FakeDecision("WATCH"), None, sd)
    assert info and info["status"] == "added" and info["item"]["code"] == "600519"
    # 同股二次分析 → in_pool 不重复入库
    info2 = cli_main._watch_pool_touch(_FakeDecision("HOLD"), None, _FakeSD(price=1260.0))
    assert info2 and info2["status"] == "in_pool"
    assert len(session_state.get_watch_active()) == 1
    assert session_state.get_watch_active()[0]["items"][0]["price"] == 1250.0  # 锚点不变


def test_touch_ignores_buy_sell_and_positions(monkeypatch, tmp_path):
    _redirect_state(monkeypatch, tmp_path)
    assert cli_main._watch_pool_touch(_FakeDecision("BUY"), None, _FakeSD()) is None
    assert cli_main._watch_pool_touch(_FakeDecision("SELL"), None, _FakeSD()) is None

    # 有持仓（entry_exit 带 exit_triggered）→ 不入池
    ee = type("E", (), {"entry_exit": {"exit_triggered": True}})()
    assert cli_main._watch_pool_touch(_FakeDecision("HOLD"), ee, _FakeSD()) is None

    # 对抗审查 P1-1 回归：有持仓 + HOLD + 无买卖点触发 → 不入池（此前缺陷：会被入池）
    held_pos = type("P", (), {"current_ratio": 0.3})()
    assert cli_main._watch_pool_touch(_FakeDecision("HOLD"), None, _FakeSD(), pos=held_pos) is None
    # 空仓记录（current_ratio=0）不挡入池
    empty_pos = type("P", (), {"current_ratio": 0.0})()
    info = cli_main._watch_pool_touch(_FakeDecision("WATCH"), None, _FakeSD(), pos=empty_pos)
    assert info and info["status"] == "added"

    # 无价数据 → 不入池
    assert cli_main._watch_pool_touch(_FakeDecision("WATCH"), None, _FakeSD(price=None)) is None
    assert len(session_state.get_watch_active()) == 1   # 只有空仓 WATCH 那一条


# ── 人话摘要两态文案 ─────────────────────────────────────

def _real_watch_sd():
    """真实末端 StrategyDecision（F2 后摘要走终态分支——测试必须锁真实路径）。"""
    from src.data.models import (
        PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
    )
    return StrategyDecision(
        decision=SignalType.WATCH, position_action=PositionAction.STAY_OUT,
        lifecycle_before=TradeLifecycle.FLAT, lifecycle_after=TradeLifecycle.FLAT,
        new_state=StrategyState(),
    )


def test_summary_watch_added_and_in_pool(monkeypatch, tmp_path, capsys):
    _redirect_state(monkeypatch, tmp_path)
    sd = _FakeSD(price=1250.0)
    result = _FakeDecision("WATCH")

    cli_main._print_plain_summary(result, _real_watch_sd(), sd,
                                  watch_info={"status": "added", "item": {"code": "600519", "price": 1250.0}})
    out1 = capsys.readouterr().out
    assert "已放进观察池" in out1 and "入池价 1250.0" in out1

    cli_main._print_plain_summary(result, _real_watch_sd(), _FakeSD(price=1300.0),
                                  watch_info={"status": "in_pool",
                                              "entry": {"timestamp": "2026-09-20T10:00:00",
                                                        "item": {"code": "600519", "price": 1250.0}}})
    out2 = capsys.readouterr().out
    assert "已在观察池" in out2 and "2026-09-20 加入" in out2 and "+4.0%" in out2


def test_summary_watch_without_pool_info_falls_back(monkeypatch, tmp_path, capsys):
    """入池失败（watch_info=None）→ 措辞不再承诺「放进观察池」。"""
    _redirect_state(monkeypatch, tmp_path)
    cli_main._print_plain_summary(_FakeDecision("WATCH"), _real_watch_sd(), _FakeSD(), watch_info=None)
    out = capsys.readouterr().out
    assert "适合观望" in out and "已放进观察池" not in out


# ── watch 命令显示 ───────────────────────────────────────

def _patch_watch_env(monkeypatch, tmp_path, records, quotes, kline_df=None, bars=None):
    _redirect_state(monkeypatch, tmp_path)
    monkeypatch.setattr(session_state, "get_watch_active", lambda: records)
    monkeypatch.setattr("src.data.akshare_client.AKShareClient.get_realtime_quotes",
                        lambda codes, retry=1: quotes)
    if kline_df is not None:
        monkeypatch.setattr(
            "src.data.akshare_client.AKShareClient.get_historical_kline",
            lambda code, period="daily", adjust="qfq", start_date=None, end_date=None, retry=3: kline_df)
    else:
        # M1 网络审计修复：K 线兜底必须 mock——否则 watch 渲染的逐票日线预热会
        # 真连 baostock 登录（离线集合里隐藏的真实外源调用）。返回最小非空 df
        # 而非 None，让 kline_cnt 计数与成本行文案保持真实路径行为
        import pandas as _pd
        monkeypatch.setattr(
            "src.data.akshare_client.AKShareClient.get_historical_kline",
            lambda code, period="daily", adjust="qfq", start_date=None, end_date=None, retry=3:
                _pd.DataFrame({"日期": ["2020-01-02"], "收盘": [99.0]}))
    import src.core.fear_index.history as fear_hist
    monkeypatch.setattr(fear_hist, "_bs_index_kline",
                        (lambda code, fields, start, end: bars) if bars is not None
                        else (lambda code, fields, start, end: []))
    monkeypatch.setattr(cli_main, "_REVIEW_REPORT_DIR", tmp_path / "报告")
    monkeypatch.setattr(cli_main, "_REVIEW_INDEX_CACHE", tmp_path / "idx.json")


def test_watch_list_known_numbers(monkeypatch, tmp_path, capsys):
    """入池价 100 → 现价 110：+10.00%，基准 +2%，超额 +8pp；报告落盘。"""
    entry_day = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    records = [{"timestamp": f"{entry_day}T10:00:00", "source": "watch add 600519",
                "items": [{"code": "600519", "name": "贵州茅台", "price": 100.0}]}]
    _patch_watch_env(monkeypatch, tmp_path, records,
                     {"600519": {"price": 110.0}}, None, [[entry_day, "3000.0"], [today, "3060.0"]])
    cli_main.watch_pool({"action": "list"})
    out = capsys.readouterr().out.replace("\n", "")
    assert "+10.00" in out and "+8.00" in out
    assert "1 只在池" in out or "1 只在池" in out.replace(" ", "")
    assert "已入磁盘缓存" in out or "逐票日线" in out
    reports = list((tmp_path / "报告").glob("watch_*.md"))
    assert len(reports) == 1 and "600519" in reports[0].read_text(encoding="utf-8")


def test_watch_list_empty_pool(monkeypatch, tmp_path, capsys):
    _patch_watch_env(monkeypatch, tmp_path, [], {}, None, None)
    cli_main.watch_pool({"action": "list"})
    out = capsys.readouterr().out
    assert "观察池是空的" in out and "自动入池" in out


def test_watch_add_then_rm_via_command(monkeypatch, tmp_path, capsys):
    _redirect_state(monkeypatch, tmp_path)
    monkeypatch.setattr("src.data.akshare_client.AKShareClient.get_realtime_quote",
                        lambda code, retry=1: {"price": 12.34})
    cli_main.watch_pool({"action": "add", "code": "000001", "name": "平安银行", "price": None})
    out1 = capsys.readouterr().out
    assert "已入观察池" in out1 and "12.34" in out1
    assert session_state.watch_entry_of("000001") is not None
    # 重复 add 拒绝
    cli_main.watch_pool({"action": "add", "code": "000001", "name": "", "price": None})
    assert "已在观察池" in capsys.readouterr().out
    # rm
    cli_main.watch_pool({"action": "rm", "code": "000001"})
    assert "已移出观察池" in capsys.readouterr().out
    assert session_state.watch_entry_of("000001") is None
    # rm 不在池的
    cli_main.watch_pool({"action": "rm", "code": "000001"})
    assert "不在观察池" in capsys.readouterr().out


def test_watch_missing_price_uses_kline_fallback(monkeypatch, tmp_path, capsys):
    """入池价缺失（实时价拉失败）→ 展示走 K 线兜底。"""
    entry_day = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    records = [{"timestamp": f"{entry_day}T10:00:00", "source": "watch add 000001",
                "items": [{"code": "000001", "name": "平安银行", "price": None}]}]
    kline_df = pd.DataFrame({"日期": [entry_day], "收盘": ["5.0"]})
    _patch_watch_env(monkeypatch, tmp_path, records,
                     {"000001": {"price": 5.5}}, kline_df, None)
    cli_main.watch_pool({"action": "list"})
    out = capsys.readouterr().out
    assert "+10.00" in out   # (5.5-5.0)/5.0
