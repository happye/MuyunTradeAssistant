"""分析证据层测试（C1，v0.8.17）：记录/证据卡/同股两次比较。

锁死语义：
1. record_evidence：JSONL 追加 + 证据卡落盘（同分钟 _2 防覆盖）；失败不抛
2. diff_evidence：只列变化项——决策转向/价格评分 delta/信号新增转向消失/新警告；
   不足两次返回 None；旧记录缺字段视为"当时未记录"不误报
3. 隔离：_EVIDENCE_FILE/_REPORT_DIR 全部重定向 tmp（不碰真实 ~/.muyun 与 分析报告/）
"""

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.cli import evidence as ev
from src.data.models import DecisionResult, MarketState, SignalType, StockData, SkillSignal


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "_EVIDENCE_FILE", tmp_path / "analysis_evidence.jsonl")
    monkeypatch.setattr(ev, "_REPORT_DIR", tmp_path / "报告" / "analysis")


def _mk_result(code="600519", price=100.0, decision="HOLD", score=0.55, warnings=None):
    stock = StockData(stock_code=code, stock_name="贵州茅台", price=price,
                      change_pct=0.0, open=price, high=price, low=price, volume=1000)
    signals = [
        SkillSignal(skill_name="s1", skill_alias="趋势", signal=SignalType.BUY,
                    confidence=0.8, reason=[]),
        SkillSignal(skill_name="s2", skill_alias="超跌", signal=SignalType.HOLD,
                    confidence=0.5, reason=[]),
    ]
    return DecisionResult(stock=stock, state=MarketState.RISK_ON, decision=SignalType[decision],
                          score=score, signals=signals, reason=["理由1", "理由2"],
                          warnings=warnings or [])


def _mk_strategy(action="HOLD_POSITION", sell_path=None):
    return SimpleNamespace(
        position_action=SimpleNamespace(value=action), sell_path=sell_path)


def test_record_evidence_appends_jsonl_and_card(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    rec = ev.record_evidence(_mk_result(), _mk_strategy(sell_path="trend_exit"), source="l 600519")
    assert rec is not None and rec["code"] == "600519"
    assert rec["decision"] == "HOLD" and rec["sell_path"] == "trend_exit"
    assert rec["signals"][0]["skill"] == "趋势"
    rows = [json.loads(l) for l in (tmp_path / "analysis_evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["score"] == 0.55
    cards = list((tmp_path / "报告" / "analysis").glob("*.md"))
    assert len(cards) == 1 and "600519" in cards[0].name
    assert "决策理由" in cards[0].read_text(encoding="utf-8")


def test_record_evidence_same_minute_no_overwrite(monkeypatch, tmp_path):
    """同分钟同股两次记录 → _2 防覆盖（两份卡都保留）。"""
    _redirect(monkeypatch, tmp_path)
    ev.record_evidence(_mk_result(), _mk_strategy(), source="a")
    ev.record_evidence(_mk_result(score=0.6), _mk_strategy(), source="b")
    cards = list((tmp_path / "报告" / "analysis").glob("*.md"))
    assert len(cards) == 2


def test_record_evidence_failure_does_not_raise(monkeypatch, tmp_path):
    """写盘失败 → 返回 None 不抛（证据是附属产出，不拖垮分析）。"""
    # 用"文件占用目录路径"制造 OSError（NotADirectoryError 是 OSError 子类）
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ev, "_STATE_DIR", blocker / "sub")
    assert ev.record_evidence(_mk_result(), _mk_strategy(), source="x") is None


def test_diff_only_reports_changes(monkeypatch, tmp_path):
    """只列变化项：价格/决策/转向/新警告；未变字段不出现。"""
    _redirect(monkeypatch, tmp_path)
    # 旧记录：HOLD，价 100，趋势 BUY
    ev.record_evidence(_mk_result(price=100.0, decision="HOLD", score=0.55,
                                  warnings=["新闻缺失"]), _mk_strategy(sell_path=None), source="a")
    # 新记录：BUY，价 110，趋势转 HOLD？不——新记录 BUY 决策 + 趋势 BUY 保持、超跌转 BUY
    r_new = _mk_result(price=110.0, decision="BUY", score=0.72, warnings=["新闻缺失", "新警告X"])
    r_new.signals[1].signal = SignalType.BUY   # 超跌: HOLD→BUY 转向
    ev.record_evidence(r_new, _mk_strategy(action="ADD", sell_path=None), source="b")

    d = ev.diff_evidence("600519")
    assert d is not None
    assert d["changes"]["price"] == (100.0, 110.0, 10.0)
    assert d["changes"]["decision"] == ("HOLD", "BUY")
    assert d["changes"]["position_action"] == ("HOLD_POSITION", "ADD")
    kinds = {s["skill"]: s["kind"] for s in d["signal_changes"]}
    assert kinds == {"超跌": "转向"}   # 趋势保持 BUY 不报；超跌转向
    assert d["new_warnings"] == ["新警告X"]
    assert "score" not in d["changes"] or abs(d["changes"]["score"][2] - 0.17) < 1e-6


def test_diff_insufficient_records(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert ev.diff_evidence("600519") is None   # 无记录
    ev.record_evidence(_mk_result(), _mk_strategy(), source="a")
    assert ev.diff_evidence("600519") is None   # 仅 1 条


def test_diff_old_record_missing_fields_not_reported(monkeypatch, tmp_path):
    """旧记录缺 sell_path 字段（当时未记录）→ 不误报为变化。"""
    _redirect(monkeypatch, tmp_path)
    old = {"ts": "2026-09-01T10:00:00", "code": "600519", "name": "茅台", "source": "a",
           "price": 100.0, "decision": "HOLD", "score": 0.55,
           "position_action": "HOLD_POSITION", "position_ratio": 0.0,
           "signals": [], "warnings": []}   # 无 sell_path 键
    (tmp_path / "analysis_evidence.jsonl").write_text(
        json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    ev.record_evidence(_mk_result(), _mk_strategy(sell_path=None), source="b")
    d = ev.diff_evidence("600519")
    assert d is not None and "sell_path" not in d["changes"]   # None vs None 不算变化


def test_evidence_survives_corrupt_lines(monkeypatch, tmp_path):
    """坏编码/截断行隔离（M2 同款逐行容错），有效行照常比较。"""
    _redirect(monkeypatch, tmp_path)
    good_old = json.dumps({"ts": "2026-09-01T10:00:00", "code": "600519", "name": "m",
                           "source": "a", "price": 100.0, "decision": "HOLD", "score": 0.5,
                           "position_action": None, "position_ratio": 0.0, "sell_path": None,
                           "signals": [], "warnings": []}, ensure_ascii=False)
    good_new = json.dumps({"ts": "2026-09-02T10:00:00", "code": "600519", "name": "m",
                           "source": "b", "price": 110.0, "decision": "BUY", "score": 0.7,
                           "position_action": None, "position_ratio": 0.0, "sell_path": None,
                           "signals": [], "warnings": []}, ensure_ascii=False)
    (tmp_path / "analysis_evidence.jsonl").write_bytes(
        b"\xff\xfe bad\n" + good_old.encode("utf-8") + b"\n"
        + b'{"ts": "2026-09-03' + b"\n" + good_new.encode("utf-8") + b"\n")
    d = ev.diff_evidence("600519")
    assert d is not None and d["changes"]["decision"] == ("HOLD", "BUY")


# ── list_diffable（用户走查 2026-09-26：diff 无参数列可对比股票）──

def test_list_diffable_groups_by_code(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    _EVIDENCE = tmp_path / "analysis_evidence.jsonl"
    rows = [
        {"ts": "2026-09-01T10:00:00", "code": "600519", "name": "贵州茅台", "source": "l",
         "price": 100.0, "decision": "HOLD", "score": 0.5, "position_action": None,
         "position_ratio": 0.0, "sell_path": None, "signals": [], "warnings": []},
        {"ts": "2026-09-02T10:00:00", "code": "600519", "name": "贵州茅台", "source": "l",
         "price": 101.0, "decision": "HOLD", "score": 0.5, "position_action": None,
         "position_ratio": 0.0, "sell_path": None, "signals": [], "warnings": []},
        {"ts": "2026-09-03T10:00:00", "code": "000001", "name": "平安银行", "source": "l",
         "price": 10.0, "decision": "BUY", "score": 0.6, "position_action": None,
         "position_ratio": 0.0, "sell_path": None, "signals": [], "warnings": []},
        {"ts": "2026-09-04T10:00:00", "code": "300750", "name": "宁德时代", "source": "l",
         "price": 200.0, "decision": "HOLD", "score": 0.5, "position_action": None,
         "position_ratio": 0.0, "sell_path": None, "signals": [], "warnings": []},
    ]
    _EVIDENCE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                         encoding="utf-8")
    out = ev.list_diffable()
    # 只有 600519 有两条 ≥2；000001/300750 各一条不入选；按最近时间降序
    assert [e["code"] for e in out] == ["600519"]
    assert out[0]["count"] == 2 and out[0]["latest_ts"] == "2026-09-02T10:00:00"


def test_list_diffable_empty_and_missing_file(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert ev.list_diffable() == []  # 文件不存在
    (tmp_path / "analysis_evidence.jsonl").write_text("{broken\n", encoding="utf-8")
    assert ev.list_diffable() == []  # 只有坏行
