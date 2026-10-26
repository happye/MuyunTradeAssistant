# -*- coding: utf-8 -*-
"""T05-J：ba/bz 多代码共用批评分的可选「到期不再开始下一只」启动预算。

全部用假钟/假评分器，无 sleep、无网络、无真实供应商 AI（合同 J5）。
覆盖：
- 调度器：准备阶段边界到期不启动任何股票；精确截止；已开始股票跨过截止后不再启动后续；
  complete/degraded/failed/not_started 互斥；重复 roster；回调异常隔离；
  deadline 由调用方传入且不重置；standalone 秒数只在入口派生一次
- ba 接线：配置冻结/账本登记在单调起点之后；账本只落秒数不落单调钟；
  摘要区分「确认未开始」与「未记录结果」；旧 mock 无 budget 参数仍兼容
- 账本：预算描述与 not_started 严格校验、fail-closed、与 done/degraded/failed 互斥；
  旧记录只读兼容；tasks 视图隔离坏描述/坏映射，不渲染原始字符串/秘密
"""

import json
import os
import sys
import time
import types
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import src.cli.main as cli_main
import src.cli.session_state as ss
import src.core.benzong.batch_scorer as batch_scorer

_DIMS = ("industry_prosperity", "business_purity", "valuation_position",
         "industry_leader", "market_recognition", "risk_deduction")


# ── 有限替身 ────────────────────────────────────────────────

class _FakeClock:
    """可推进的单调钟替身（不 sleep、不依赖真实时间流逝）。"""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _FakeClock()
    monkeypatch.setattr(batch_scorer, "time",
                        types.SimpleNamespace(monotonic=c.monotonic))
    return c


def _structured(total=60, name="x", confs=None):
    confs = dict(confs or {})
    r = mock.MagicMock()
    r.score.total_score = total
    r.score.grade.return_value = "B"
    r.score.effective_grade.return_value = "B"
    r.score.normalized_score.return_value = float(total)
    r.score.stock_name = name
    r.overall_confidence = 0.8
    r.warnings = []
    r.invalidate = False
    r.dimensions_meta = {d: {"score": total, "confidence": confs.get(d, 0.8)}
                         for d in _DIMS}
    return r


def _degraded(total=60, name="x"):
    return _structured(total, name=name, confs={"risk_deduction": 0})


def _patch_prep(monkeypatch, clock=None, freeze_step=None, build_step=None, turnover_step=None):
    """替换配置冻结/客户端构造/全市场成交额，记录准备阶段各边界是否被调用。"""
    state = {"freeze": 0, "build": 0, "turnover": 0}

    def freeze(config=None):
        state["freeze"] += 1
        if freeze_step:
            freeze_step(clock)
        return {"frozen": True}

    def build(config=None):
        state["build"] += 1
        if build_step:
            build_step(clock)
        return object(), "m"

    def turnover():
        state["turnover"] += 1
        if turnover_step:
            turnover_step(clock)
        return 1.2

    monkeypatch.setattr(batch_scorer, "freeze_batch_config", freeze)
    monkeypatch.setattr(batch_scorer, "_build_ai_client", build)
    monkeypatch.setattr(batch_scorer, "get_market_turnover", turnover)
    return state


def _patch_auto(monkeypatch, fn):
    """替换逐股评分器；fn(code, kw) 返回结果/None 或抛异常。"""
    calls = []

    def fake(code, **kw):
        calls.append((code, kw))
        out = fn(code, kw)
        return _structured(60, name=code) if out is None else out

    monkeypatch.setattr(batch_scorer, "auto_score", fake)
    return calls


def _redirect_ledger(monkeypatch, tmp_path):
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    return path


def _row(code, total=60):
    return {"code": code, "name": code, "total_score": total,
            "normalized_score": float(total), "grade": "B", "effective_grade": "B",
            "confidence": 0.8, "dim_scores": {}, "dim_confidences": {},
            "warnings": [], "invalidate": False}


def _flat(text):
    return text.replace("\n", "").replace(" ", "")


# ── 1. 调度器：预算语义（假钟，无 sleep）──────────────────

def test_no_budget_keeps_old_behavior(monkeypatch):
    """默认 None：不启用预算，结果形状/调用次数与旧行为一致。"""
    _patch_prep(monkeypatch)
    calls = _patch_auto(monkeypatch, lambda code, kw: None)
    res = batch_scorer.auto_score_batch(["A", "B"], top_n=10)
    assert [c for c, _ in calls] == ["A", "B"]
    assert res["scored"] == 2 and res["failed"] == 0
    assert res["not_started"] == [] and res["stop_reason"] is None


def test_standalone_budget_seconds_derives_deadline_once(clock, monkeypatch):
    """standalone 只传秒数：入口派生一次截止点；已开始股票跑完后不再开新股票。"""
    _patch_prep(monkeypatch)

    def fn(code, kw):
        if code == "A":
            clock.advance(11)     # 预算 10 秒，A 自己跑过了截止点
        return None

    calls = _patch_auto(monkeypatch, fn)
    records = []
    res = batch_scorer.auto_score_batch(
        ["A", "B", "C"], top_n=10, budget_seconds=10,
        result_cb=lambda rec: records.append(rec))
    assert [c for c, _ in calls] == ["A"], "已开始的 A 允许跑完，B/C 不得再开始"
    assert res["not_started"] == ["B", "C"]
    assert res["stop_reason"] == batch_scorer.BUDGET_STOP_REASON
    assert [r["code"] for r in res["ranked"]] == ["A"]
    assert res["failed"] == 0
    ns = [r for r in records if r.get("status") == "not_started"]
    assert [r["code"] for r in ns] == ["B", "C"]
    assert all(r["ok"] is False and r["reason"] == batch_scorer.BUDGET_STOP_REASON
               for r in ns)


def test_prep_expiry_at_entry_starts_nothing(monkeypatch):
    """入口即到期：配置/客户端/取数/评分一律不发生，整批显式 not_started。"""
    clock = _FakeClock(100.0)
    monkeypatch.setattr(batch_scorer, "time",
                        types.SimpleNamespace(monotonic=clock.monotonic))
    state = _patch_prep(monkeypatch)
    calls = _patch_auto(monkeypatch, lambda code, kw: None)
    records = []
    res = batch_scorer.auto_score_batch(
        ["A", "B"], deadline=100.0,
        result_cb=lambda rec: records.append(rec))
    assert calls == [] and state == {"freeze": 0, "build": 0, "turnover": 0}
    assert res["not_started"] == ["A", "B"]
    assert res["stop_reason"] == batch_scorer.BUDGET_STOP_REASON
    assert res["ranked"] == [] and res["scored"] == 0
    assert [r["code"] for r in records] == ["A", "B"]


def test_exact_cutoff_boundary_is_not_started(clock, monkeypatch):
    """now == deadline 视为到期（>=）：准备阶段正好卡点也不开新股票。"""
    clock.now = 1000.0

    def freeze_step(c):
        c.now = 1010.0     # 冻结恰好用满 10 秒预算

    state = _patch_prep(monkeypatch, clock=clock, freeze_step=freeze_step)
    calls = _patch_auto(monkeypatch, lambda code, kw: None)
    res = batch_scorer.auto_score_batch(["A", "B"], deadline=1010.0)
    assert state["freeze"] == 1 and state["build"] == 0 and state["turnover"] == 0
    assert calls == []
    assert res["not_started"] == ["A", "B"]


def test_client_build_expiry_prevents_market_and_scoring(clock, monkeypatch):
    """准备边界：客户端构造跨过截止 → 不再拉全市场成交额、不再评分。"""
    state = _patch_prep(monkeypatch, clock=clock,
                        build_step=lambda c: c.advance(10))
    calls = _patch_auto(monkeypatch, lambda code, kw: None)
    res = batch_scorer.auto_score_batch(["A", "B"], deadline=clock.now + 5)
    assert state["build"] == 1 and state["turnover"] == 0
    assert calls == [] and res["not_started"] == ["A", "B"]


def test_market_turnover_expiry_prevents_scoring(clock, monkeypatch):
    """准备边界：整批成交额取数跨过截止 → 不新建任何逐股评分调用。"""
    state = _patch_prep(monkeypatch, clock=clock,
                        turnover_step=lambda c: c.advance(10))
    calls = _patch_auto(monkeypatch, lambda code, kw: None)
    res = batch_scorer.auto_score_batch(["A", "B"], deadline=clock.now + 5)
    assert state["build"] == 1 and state["turnover"] == 1
    assert calls == [] and res["not_started"] == ["A", "B"]


def test_current_stock_crosses_deadline_then_remaining_not_started(clock, monkeypatch):
    """当前股票跨过截止可完整跑完；后续股票不再开始（不做强制取消）。"""
    _patch_prep(monkeypatch)

    def fn(code, kw):
        if code == "A":
            clock.advance(6)     # 预算 5 秒：A 开始后越界
        return None

    calls = _patch_auto(monkeypatch, fn)
    res = batch_scorer.auto_score_batch(["A", "B", "C"], deadline=clock.now + 5)
    assert [c for c, _ in calls] == ["A"]
    assert [r["code"] for r in res["ranked"]] == ["A"]
    assert res["not_started"] == ["B", "C"]
    assert res["scored"] == 1 and res["failed"] == 0


def test_mixed_complete_degraded_failed_then_not_started(clock, monkeypatch):
    """complete/degraded/failed/not_started 四态互斥且计数可区分。"""
    _patch_prep(monkeypatch)

    def fn(code, kw):
        if code == "A":
            clock.advance(1)
            return _structured(80, name="A")
        if code == "B":
            clock.advance(1)
            return _degraded(70, name="B")
        if code == "C":
            clock.advance(3)     # 到截止点
            raise RuntimeError("网络挂")
        return None

    calls = _patch_auto(monkeypatch, fn)
    records = []
    res = batch_scorer.auto_score_batch(
        ["A", "B", "C", "D"], deadline=clock.now + 5,
        result_cb=lambda rec: records.append(rec))
    assert [c for c, _ in calls] == ["A", "B", "C"], "C 已开始，异常也要跑完路径"
    assert [r["code"] for r in res["ranked"]] == ["A", "B"]
    assert [f["code"] for f in res["failures"]] == ["C"]
    assert res["not_started"] == ["D"], "只有从未开始过的 D 才能算 not_started"
    assert res["scored"] == 2 and res["failed"] == 1
    by_code = {r["code"]: r for r in records}
    assert by_code["A"]["status"] == "complete"
    assert by_code["B"]["status"] == "degraded" and by_code["B"]["degraded_dims"]
    assert by_code["C"]["status"] == "failed"
    assert by_code["D"]["status"] == "not_started"


def test_timeout_during_started_stock_is_failure_not_not_started(clock, monkeypatch):
    """已开始股票超时异常 → 失败，绝不倒推成“未开始”。"""
    _patch_prep(monkeypatch)

    def fn(code, kw):
        if code == "A":
            raise TimeoutError("AI 超时")
        return None

    _patch_auto(monkeypatch, fn)
    res = batch_scorer.auto_score_batch(["A", "B"], deadline=clock.now + 60)
    assert [f["code"] for f in res["failures"]] == ["A"]
    assert "A" not in res["not_started"], "已开始股票超时/异常不得倒推成未开始"
    assert res["not_started"] == [], "预算未到期时 B 应正常开始"
    assert res["scored"] == 1


def test_duplicate_roster_never_marks_started_code_not_started(clock, monkeypatch):
    """重复 roster：已传给 auto_score 的代码即使后面重复出现也不算 not_started。"""
    _patch_prep(monkeypatch)

    def fn(code, kw):
        if code == "A":
            clock.advance(11)
        return None

    _patch_auto(monkeypatch, fn)
    res = batch_scorer.auto_score_batch(["A", "B", "A"], deadline=clock.now + 10)
    assert res["not_started"] == ["B"]
    assert res["not_started"].count("B") == 1


def test_duplicate_roster_preserves_order_and_dedup(clock, monkeypatch):
    """到期后未开始的重复代码按首次出现顺序去重。"""
    _patch_prep(monkeypatch)
    _patch_auto(monkeypatch, lambda code, kw: None)
    res = batch_scorer.auto_score_batch(
        ["B", "A", "B", "C", "A"], deadline=clock.now - 1)
    assert res["not_started"] == ["B", "A", "C"]


def test_deadline_from_caller_is_not_reset(clock, monkeypatch):
    """同一个绝对 deadline 不得在调度层重新起算；显式 deadline 优先于秒数。"""
    _patch_prep(monkeypatch)

    def fn(code, kw):
        clock.advance(6)      # 相对秒数若被重算（1000 秒）就不会到期
        return None

    _patch_auto(monkeypatch, fn)
    res = batch_scorer.auto_score_batch(
        ["A", "B"], deadline=clock.now + 5, budget_seconds=1000)
    assert res["not_started"] == ["B"]


def test_not_started_callback_exception_isolated(clock, monkeypatch):
    """not_started 记账回调异常：计数如实上报，不影响 ranked/not_started 业务结果。"""
    _patch_prep(monkeypatch)

    def fn(code, kw):
        if code == "A":
            clock.advance(11)
        return None

    _patch_auto(monkeypatch, fn)

    def cb(rec):
        raise RuntimeError("账本坏了")

    res = batch_scorer.auto_score_batch(["A", "B", "C"], deadline=clock.now + 10,
                                        result_cb=cb)
    assert res["not_started"] == ["B", "C"]
    assert res["scored"] == 1
    assert res["result_cb_errors"] == 3, "A 成功 + B/C 未开始，三次回调异常都要如实计数"


def test_progress_cb_receives_only_started_codes(clock, monkeypatch):
    """progress 仍是纯展示：只为真正开始的股票回调，不冒充未开始项。"""
    _patch_prep(monkeypatch)

    def fn(code, kw):
        if code == "A":
            clock.advance(11)
        return None

    _patch_auto(monkeypatch, fn)
    progress = []
    res = batch_scorer.auto_score_batch(
        ["A", "B"], deadline=clock.now + 10,
        progress_cb=lambda i, t, c, s: progress.append(c))
    assert progress == ["A"]
    assert res["not_started"] == ["B"]


def test_invalid_budget_arguments_raise_value_error(monkeypatch):
    _patch_prep(monkeypatch)
    _patch_auto(monkeypatch, lambda code, kw: None)
    for bad in (0, -1, float("nan"), float("inf"), True, "60", object()):
        with pytest.raises(ValueError):
            batch_scorer.auto_score_batch(["A"], budget_seconds=bad)
    for bad in (float("nan"), float("inf"), True, "100"):
        with pytest.raises(ValueError):
            batch_scorer.auto_score_batch(["A"], deadline=bad)


def test_budget_does_not_change_scoring_call_arguments(clock, monkeypatch):
    """预算只影响“是否开始”，已开始股票的评分/缓存/强制刷新入参不变。"""
    _patch_prep(monkeypatch)
    calls = _patch_auto(monkeypatch, lambda code, kw: None)
    batch_scorer.auto_score_batch(["A"], top_n=10, force_refresh=True,
                                  deadline=clock.now + 60)
    assert len(calls) == 1
    code, kw = calls[0]
    assert code == "A"
    assert set(kw) == {"force_refresh", "config", "ai_client", "ai_model",
                       "rag_service", "market_turnover", "allow_client_rebuild"}
    assert kw["force_refresh"] is True
    assert kw["allow_client_rebuild"] is False
    assert kw["config"] == {"frozen": True}


# ── 2. budget 描述校验（严格白名单）────────────────────────

def test_budget_descriptor_validates_and_normalizes():
    desc = batch_scorer.budget_descriptor(60)
    assert desc == {"schema": batch_scorer.BUDGET_SCHEMA,
                    "policy": batch_scorer.BUDGET_POLICY,
                    "budget_seconds": 60.0}
    assert batch_scorer.validate_budget_descriptor(dict(desc)) == desc
    assert batch_scorer.budget_descriptor(0.5)["budget_seconds"] == 0.5


@pytest.mark.parametrize("bad", [
    None, "60", [],
    {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock"},
    {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
     "budget_seconds": 60, "extra": 1},
    {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
     "budget_seconds": True},
    {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
     "budget_seconds": float("nan")},
    {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
     "budget_seconds": float("inf")},
    {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
     "budget_seconds": 0},
    {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
     "budget_seconds": -3},
    {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
     "budget_seconds": "60"},
    {"schema": "muyun.ba.other/v1", "policy": "stop_starting_new_stock",
     "budget_seconds": 60},
    {"schema": "muyun.ba.budget/v1", "policy": "hard_wall",
     "budget_seconds": 60},
])
def test_budget_descriptor_rejects_malformed(bad):
    assert batch_scorer.validate_budget_descriptor(bad) is None
    assert batch_scorer.budget_descriptor(bad) is None


# ── 3. ba 接线：单一单调起点 + 账本预算 + 摘要四态 ─────────

def _fake_batch_factory(captured, rows=None, events=None, result_extra=None):
    """可配置的 auto_score_batch 替身：记录调用时账本状态与 deadline 参数。"""

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None, deadline=None):
        captured["codes"] = list(codes)
        captured["force_refresh"] = force_refresh
        captured["config"] = config
        captured["deadline"] = deadline
        captured["call_count"] = captured.get("call_count", 0) + 1
        captured["ledger_at_call"] = (
            json.loads(captured["ledger_path"].read_text(encoding="utf-8"))
            if captured["ledger_path"].exists() else None)
        for ev in (events or []):
            if result_cb:
                result_cb(dict(ev))
        out_rows = list(rows or [])
        out = {"ranked": out_rows, "top_n": out_rows[:len(codes)],
               "failures": [], "scanned": len(codes),
               "scored": len(out_rows), "failed": 0}
        if result_extra:
            out.update(result_extra)
        return out

    return fake_batch


def test_ba_budget_starts_before_freeze_and_registers_descriptor(monkeypatch, tmp_path):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: {"frozen": True})
    captured = {"ledger_path": ledger}
    monkeypatch.setattr(batch_scorer, "auto_score_batch",
                        _fake_batch_factory(captured, rows=[_row("600519")],
                                            events=[{"code": "600519", "ok": True,
                                                     "status": "complete"}]))
    started = time.monotonic()
    cli_main.benzong_batch_analyze([{"code": "600519", "name": "茅台"}],
                                   budget=60, src_label="bzscan")
    deadline = captured["deadline"]
    assert isinstance(deadline, float)
    assert started + 59.9 <= deadline <= time.monotonic() + 60.0, \
        "预算必须在 ba 入口起一次单调钟（不早于确认、不晚于配置冻结）"
    assert captured["call_count"] == 1 and captured["force_refresh"] is False
    record = captured["ledger_at_call"]["tasks"][0]
    assert record["budget"] == {
        "schema": batch_scorer.BUDGET_SCHEMA,
        "policy": batch_scorer.BUDGET_POLICY,
        "budget_seconds": 60.0,
    }, "账本必须只落有限秒数描述（不落单调钟绝对值）"
    text = ledger.read_text(encoding="utf-8")
    assert "deadline" not in text and "monotonic" not in text


def test_ba_no_budget_does_not_pass_new_kwargs(monkeypatch, tmp_path):
    """J1 兼容：无预算时不给 auto_score_batch 新增参数、不给 start 新增 budget。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: {"frozen": True})
    seen = {}

    def old_batch(codes, top_n=None, force_refresh=False, config=None,
                  progress_cb=None, result_cb=None):
        seen["args"] = (top_n, force_refresh)
        if result_cb:
            result_cb({"code": codes[0], "ok": True, "status": "complete"})
        return {"ranked": [_row(codes[0])], "top_n": [], "failures": [],
                "scanned": 1, "scored": 1, "failed": 0}

    monkeypatch.setattr(batch_scorer, "auto_score_batch", old_batch)
    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")
    assert seen["args"] == (1, False)
    record = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
    assert "budget" not in record


def test_ba_budget_summary_distinguishes_unstarted_from_unknown(
        monkeypatch, tmp_path, capsys):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: {"frozen": True})
    captured = {"ledger_path": ledger}
    events = [
        {"code": "600519", "ok": True, "status": "complete"},
        {"code": "000001", "ok": False, "status": "not_started",
         "reason": batch_scorer.BUDGET_STOP_REASON},
        {"code": "300750", "ok": False, "status": "not_started",
         "reason": batch_scorer.BUDGET_STOP_REASON},
    ]
    monkeypatch.setattr(batch_scorer, "auto_score_batch",
                        _fake_batch_factory(
                            captured, rows=[_row("600519")], events=events,
                            result_extra={"not_started": ["000001", "300750"],
                                          "stop_reason": batch_scorer.BUDGET_STOP_REASON}))
    cli_main.benzong_batch_analyze(
        [{"code": "600519"}, {"code": "000001"}, {"code": "300750"}],
        budget=60, src_label="x")
    out = capsys.readouterr().out
    flat = _flat(out)
    assert "预算未开始2" in flat and "未记录结果0" in flat
    assert "已完成1" in flat and "降级0" in flat and "失败0" in flat
    assert "预算到期" in flat and "非强制中断" in flat, "摘要必须说明不是硬取消"
    assert "确认前缓存预览不计入" in flat
    record = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
    assert record["not_started"] == {
        "000001": batch_scorer.BUDGET_STOP_REASON,
        "300750": batch_scorer.BUDGET_STOP_REASON,
    }
    assert record["stop_reason"] == batch_scorer.BUDGET_STOP_REASON


def test_ba_budget_all_unstarted_reports_no_started_stock(monkeypatch, tmp_path, capsys):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: {"frozen": True})
    captured = {"ledger_path": ledger}
    events = [{"code": c, "ok": False, "status": "not_started",
               "reason": batch_scorer.BUDGET_STOP_REASON} for c in ("600519", "000001")]
    monkeypatch.setattr(batch_scorer, "auto_score_batch",
                        _fake_batch_factory(
                            captured, rows=[], events=events,
                            result_extra={"not_started": ["600519", "000001"],
                                          "stop_reason": batch_scorer.BUDGET_STOP_REASON}))
    cli_main.benzong_batch_analyze([{"code": "600519"}, {"code": "000001"}],
                                   budget=0.1, src_label="x")
    out = capsys.readouterr().out
    flat = _flat(out)
    assert "预算未开始2" in flat and "未记录结果0" in flat
    assert "未产出可展示评分" in flat, "整批未开始时只报告证据范围（不是失败）"
    assert "均有确认未开始记录" in flat
    assert "没有任何股票开始评分" not in flat, "不得再声称全批未开始/零费用承诺"
    assert "未产生评分费用" not in flat and "零费用" not in flat
    assert "失败1" not in flat and "失败2" not in flat


def test_ba_not_started_event_without_budget_is_unreliable(monkeypatch, tmp_path, capsys):
    """无预算运行出现 not_started 事件 → 视为未知/不可靠，绝不记账。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: {"frozen": True})
    captured = {"ledger_path": ledger}
    monkeypatch.setattr(batch_scorer, "auto_score_batch",
                        _fake_batch_factory(
                            captured, rows=[],
                            events=[{"code": "600519", "ok": False,
                                     "status": "not_started",
                                     "reason": batch_scorer.BUDGET_STOP_REASON}]))
    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")
    out = capsys.readouterr().out
    flat = _flat(out)
    assert "结果记录不可靠" in flat
    assert "未记录结果1" in flat
    record = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
    assert "not_started" not in record and "stop_reason" not in record


def test_ba_not_started_event_wrong_reason_is_unreliable(monkeypatch, tmp_path, capsys):
    """not_started 原因必须在白名单内；未知原因不计数、不落账本。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: {"frozen": True})
    captured = {"ledger_path": ledger}
    monkeypatch.setattr(batch_scorer, "auto_score_batch",
                        _fake_batch_factory(
                            captured, rows=[],
                            events=[{"code": "600519", "ok": False,
                                     "status": "not_started",
                                     "reason": "timeout"}]))
    cli_main.benzong_batch_analyze([{"code": "600519"}], budget=60, src_label="x")
    out = capsys.readouterr().out
    flat = _flat(out)
    assert "结果记录不可靠" in flat
    assert "预算未开始1" not in flat and "未记录结果1" in flat
    record = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
    assert "not_started" not in record


def test_ba_ledger_write_failure_for_not_started_reports_incomplete(
        monkeypatch, tmp_path, capsys):
    """not_started 记账写盘失败 → 明确提示记录不完整，不误报评分失败。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: {"frozen": True})
    captured = {"ledger_path": ledger}
    events = [{"code": "000001", "ok": False, "status": "not_started",
               "reason": batch_scorer.BUDGET_STOP_REASON}]

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None, deadline=None):
        monkeypatch.setattr(ss, "_atomic_write_json", lambda *a, **k: False)
        result_cb({"code": "600519", "ok": True, "status": "complete"})
        for ev in events:
            result_cb(dict(ev))
        rows = [_row("600519")]
        return {"ranked": rows, "top_n": rows, "failures": [], "scanned": 2,
                "scored": 1, "failed": 0, "not_started": ["000001"],
                "stop_reason": batch_scorer.BUDGET_STOP_REASON}

    monkeypatch.setattr(batch_scorer, "auto_score_batch", fake_batch)
    cli_main.benzong_batch_analyze([{"code": "600519"}, {"code": "000001"}],
                                   budget=60, src_label="x")
    out = capsys.readouterr().out
    flat = _flat(out)
    assert "任务记录写入失败" in flat and "进度可能不完整" in flat
    assert "失败1" not in flat, "账本写失败不得改判评分失败"
    assert "已完成1" in flat and "预算未开始1" in flat


def test_ba_invalid_budget_direct_call_rejected_before_business(monkeypatch, capsys):
    """绕过解析直接给坏预算：冻结/账本/客户端一律不发生。"""
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: (_ for _ in ()).throw(
                            AssertionError("坏预算不得冻结配置")))
    monkeypatch.setattr(ss, "batch_task_start",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("坏预算不得登记账本")))
    for bad in (0, -1, float("nan"), float("inf"), "60", True):
        cli_main.benzong_batch_analyze([{"code": "600519"}], budget=bad)
    out = capsys.readouterr().out
    assert out.count("预算参数无效") >= 6


# ── 4. 账本：预算描述/not_started 严格写入，旧记录只读兼容 ──────

def test_ledger_budget_descriptor_write_gate(tmp_path, monkeypatch):
    _redirect_ledger(monkeypatch, tmp_path)
    valid = {"schema": "muyun.ba.budget/v1",
             "policy": "stop_starting_new_stock", "budget_seconds": 60}
    token = ss.batch_task_start("ba", "with-budget", ["A", "B"], budget=valid)
    assert isinstance(token, str) and token
    rec = ss.get_recent_batch_tasks(1)[0]
    assert rec["budget"] == {"schema": "muyun.ba.budget/v1",
                             "policy": "stop_starting_new_stock",
                             "budget_seconds": 60.0}

    for i, bad in enumerate([
        dict(valid, extra="leak"),
        dict(valid, budget_seconds=True),
        dict(valid, budget_seconds=-1),
        {"schema": "other", "policy": valid["policy"], "budget_seconds": 60},
        {"schema": valid["schema"], "policy": "hard_wall", "budget_seconds": 60},
    ]):
        token = ss.batch_task_start("ba", f"bad-budget-{i}", ["A"], budget=bad)
        assert isinstance(token, str) and token, "坏描述只丢预算字段，run 仍登记"
        rec = ss.get_recent_batch_tasks(1)[0]
        assert "budget" not in rec


def test_ledger_not_started_fail_closed_and_bound_to_latest_run(tmp_path, monkeypatch):
    _redirect_ledger(monkeypatch, tmp_path)
    valid = {"schema": "muyun.ba.budget/v1",
             "policy": "stop_starting_new_stock", "budget_seconds": 60}
    reason = batch_scorer.BUDGET_STOP_REASON
    token = ss.batch_task_start("ba", "k", ["A", "B"], budget=valid)

    assert ss.batch_task_not_started("ba", "k", "A", run_id=token, reason=reason) is True
    rec = ss.get_recent_batch_tasks(1)[0]
    assert rec["not_started"] == {"A": reason}
    assert rec["stop_reason"] == reason

    assert ss.batch_task_not_started("ba", "k", "B") is False, "缺令牌必须拒绝"
    assert ss.batch_task_not_started("ba", "k", "B", run_id="old-run",
                                     reason=reason) is False
    assert ss.batch_task_not_started("ba", "k", "Z", run_id=token,
                                     reason=reason) is False, "清单外代码必须拒绝"
    assert ss.batch_task_not_started("ba", "k", "B", run_id=token,
                                     reason="timeout") is False, "非白名单原因必须拒绝"

    no_budget = ss.batch_task_start("ba", "nobudget", ["A"])
    assert ss.batch_task_not_started("ba", "nobudget", "A", run_id=no_budget,
                                     reason=reason) is False, "无预算 run 不得记未开始"

    assert ss.batch_task_mark("ba", "k", "B", True, run_id=token) is True
    assert ss.batch_task_not_started("ba", "k", "B", run_id=token,
                                     reason=reason) is False, "已完成不可改判未开始"
    rec = next(t for t in ss.get_recent_batch_tasks(5) if t.get("key") == "k")
    assert "B" in rec["done"] and "B" not in rec.get("not_started", {})


def test_ledger_budget_new_run_does_not_inherit_old_not_started(tmp_path, monkeypatch):
    _redirect_ledger(monkeypatch, tmp_path)
    valid = {"schema": "muyun.ba.budget/v1",
             "policy": "stop_starting_new_stock", "budget_seconds": 60}
    reason = batch_scorer.BUDGET_STOP_REASON
    first = ss.batch_task_start("ba", "k", ["A", "B"], budget=valid)
    assert ss.batch_task_not_started("ba", "k", "A", run_id=first, reason=reason)
    second = ss.batch_task_start("ba", "k", ["A", "B"], budget=valid)
    fresh = ss.get_recent_batch_tasks(1)[0]
    assert fresh["run_id"] == second
    assert "not_started" not in fresh and "stop_reason" not in fresh
    old = ss.get_recent_batch_tasks(2)[1]
    assert old["not_started"] == {"A": reason}


# ── 5. tasks 视图：预算/未开始可见，坏描述与冲突只读隔离 ──────

def _seed(path, records):
    path.write_text(json.dumps({"tasks": records}, ensure_ascii=False), encoding="utf-8")


def _render_tasks(monkeypatch, tmp_path, capsys, records):
    path = _redirect_ledger(monkeypatch, tmp_path)
    _seed(path, records)
    before = path.read_bytes()
    cli_main.batch_tasks_cmd()
    out = capsys.readouterr().out
    assert path.read_bytes() == before, "tasks 只读展示不得改写账本"
    return out, _flat(out)


_VALID_BUDGET = {"schema": "muyun.ba.budget/v1",
                 "policy": "stop_starting_new_stock", "budget_seconds": 60}


def test_tasks_view_renders_budget_not_started_and_overrun_policy(
        monkeypatch, tmp_path, capsys):
    rec = {"kind": "ba", "key": "预算记录", "codes": ["A", "B"],
           "run_id": "RUN-T05J", "done": {"A": "2026-10-26T01:00:00"},
           "degraded": {}, "failed": {}, "not_started": {"B": "budget_exhausted"},
           "stop_reason": "budget_exhausted", "budget": dict(_VALID_BUDGET),
           "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "预算:60秒" in flat
    assert "到期不再开始新股票" in flat
    assert "终止:预算到期" in flat
    assert "已开始的股票会跑完" in flat, "必须展示超时策略（准备/单只可能超出）"
    assert "非强制中断" in flat and "无费用上限" in flat
    assert "预算未开始1" in flat and "未记录结果0" in flat and "已完成1" in flat
    assert "预算到期未开始" in flat, "确认未开始项必须逐项可见且非失败"


def test_tasks_view_legacy_record_shows_budget_unrecorded(monkeypatch, tmp_path, capsys):
    rec = {"kind": "ba", "key": "旧无预算记录", "codes": ["A"],
           "run_id": "RUN-LEGACY", "done": {"A": "t"}, "degraded": {}, "failed": {},
           "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "预算:未记录" in flat
    assert "预算未开始" not in flat, "无预算字段的旧记录不强行显示未开始状态"
    assert "已完成1" in flat and "未记录结果0" in flat


def test_tasks_view_isolates_malformed_budget_descriptors(monkeypatch, tmp_path, capsys):
    secret = "sk-SECRET-T05J"
    bads = [
        {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
         "budget_seconds": True},
        {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
         "budget_seconds": float("nan")},
        {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
         "budget_seconds": float("inf")},
        {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
         "budget_seconds": 60, "note": secret},
        {"schema": "muyun.ba.budget/v1", "policy": "stop_starting_new_stock",
         "budget_seconds": 60, "api_key": secret},
        {"schema": secret, "policy": "stop_starting_new_stock", "budget_seconds": 60},
    ]
    for i, bad in enumerate(bads):
        rec = {"kind": "ba", "key": f"坏预算-{i}", "codes": ["A"],
               "run_id": f"RUN-BAD-{i}", "done": {}, "degraded": {},
               "failed": {}, "budget": bad, "not_started": {"A": "budget_exhausted"},
               "ts_started": "t", "ts_updated": "t"}
        out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
        assert "描述校验未通过" in flat, f"坏描述必须显式标未通过: {bad!r}"
        assert "预算:60秒" not in flat
        assert secret not in out, "不得渲染存库原始字符串/秘密"
        assert "sk-SECRET" not in out


def test_tasks_view_isolates_invalid_not_started_values(monkeypatch, tmp_path, capsys):
    rec = {"kind": "ba", "key": "坏未开始", "codes": ["A", "B", "C"],
           "run_id": "RUN-NS-BAD", "done": {}, "degraded": {}, "failed": {},
           "budget": dict(_VALID_BUDGET),
           "not_started": {"A": "sk-SECRET-RAW", "B": None,
                           "C": "budget_exhausted"},
           "stop_reason": "budget_exhausted",
           "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "无效记录值" in flat and "待确认" in flat
    assert "预算未开始1" in flat, "只有白名单原因计入确认未开始"
    assert "sk-SECRET" not in out
    assert "未记录结果2" in flat


# ═══════ fix2（主控独立复核 BLOCKERS，仍属原 J1-J4 范围）═══════

# ── BLOCKER 2：ba 首次准备（配置冻结）前核同一 deadline ─────────

def _expiry_env(monkeypatch, tmp_path):
    """过期路径环境：配置读取/冻结/client/取数/评分全部计数并禁止调用。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    state = {"load": 0, "freeze": 0, "build": 0, "turnover": 0, "score": 0}
    import src.config as config_module

    def load():
        state["load"] += 1
        return {"ai": {"deepseek": {"model": "m"}}}

    monkeypatch.setattr(config_module, "load_config", load)

    def freeze(config=None):
        state["freeze"] += 1
        raise AssertionError("预算已过期时不得冻结/load 配置")

    monkeypatch.setattr(batch_scorer, "freeze_batch_config", freeze)
    monkeypatch.setattr(
        batch_scorer, "_build_ai_client",
        lambda config=None: (state.__setitem__("build", state["build"] + 1),
                             (object(), "m"))[1])
    monkeypatch.setattr(
        batch_scorer, "get_market_turnover",
        lambda: state.__setitem__("turnover", state["turnover"] + 1) or 1.2)
    monkeypatch.setattr(
        batch_scorer, "auto_score",
        lambda code, **kw: (state.__setitem__("score", state["score"] + 1)
                            or _structured(60, name=code)))
    return ledger, state


def _assert_expired_budget_run(ledger, state, codes):
    assert state == {"load": 0, "freeze": 0, "build": 0, "turnover": 0,
                     "score": 0}, "过期后不得 load 配置/构造 client/取数/评分"
    rec = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
    assert rec["budget"]["budget_seconds"] > 0, "仍须登记合法预算 run"
    assert rec["not_started"] == {c: batch_scorer.BUDGET_STOP_REASON for c in codes}
    assert rec["stop_reason"] == batch_scorer.BUDGET_STOP_REASON
    return rec


def test_fix2_ba_tiny_budget_expires_before_first_freeze(
        monkeypatch, tmp_path, capsys):
    """1e-300 合法极小预算：deadline 已过期时不得 load_config/构造 client。"""
    ledger, state = _expiry_env(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "monotonic", _FakeClock(1000.0).monotonic)
    cli_main.benzong_batch_analyze(
        [{"code": "600519"}, {"code": "000001"}], budget=1e-300, src_label="tiny")
    _assert_expired_budget_run(ledger, state, ["600519", "000001"])
    out = _flat(capsys.readouterr().out)
    assert "预算未开始2" in out and "未记录结果0" in out


def test_fix2_ba_budget_exhausted_during_notice_before_freeze(
        monkeypatch, tmp_path, capsys):
    """提示输出阶段假钟耗尽：首次 freeze 前同一 deadline 过期，不得 load 配置。"""
    ledger, state = _expiry_env(monkeypatch, tmp_path)

    class _TickClock:
        def __init__(self):
            self.n = 0

        def monotonic(self):
            self.n += 1
            return 1000.0 + self.n      # 每次读取推进 1 秒

    monkeypatch.setattr(time, "monotonic", _TickClock().monotonic)
    cli_main.benzong_batch_analyze(
        [{"code": "600519"}, {"code": "000001"}], budget=0.5, src_label="notify")
    _assert_expired_budget_run(ledger, state, ["600519", "000001"])
    out = _flat(capsys.readouterr().out)
    assert "预算未开始2" in out
    assert "非强制中断" in out


# ── BLOCKER 3：执行/账本/汇总共用保序去重名单 ─────────────────

def test_fix2_batch_dedupes_codes_once_and_keeps_force_refresh(monkeypatch):
    """预算充足时重复代码也只真实评分一次（force_refresh 不重复付费）。"""
    _patch_prep(monkeypatch)
    calls = _patch_auto(monkeypatch, lambda code, kw: None)
    res = batch_scorer.auto_score_batch(
        ["A", "A", "B", "A"], top_n=10, force_refresh=True)
    assert [c for c, _ in calls] == ["A", "B"], "重复代码不得重复调用 auto_score"
    assert all(kw["force_refresh"] is True for _, kw in calls)
    assert res["scanned"] == 2 and res["scored"] == 2
    assert [r["code"] for r in res["ranked"]] == ["A", "B"]


def test_fix2_ba_duplicate_roster_shared_dedupe_ledger_and_counts(
        monkeypatch, tmp_path, capsys):
    """ba 真实链路：重复清单只评分/记账一次，roster/汇总用同一去重名单，无假未知。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    _patch_prep(monkeypatch)
    calls = _patch_auto(monkeypatch, lambda code, kw: None)
    cli_main.benzong_batch_analyze(
        [{"code": "600519"}, {"code": "600519"}, {"code": "000001"}],
        force_refresh=True, src_label="dup")
    assert [c for c, _ in calls] == ["600519", "000001"]
    assert all(kw["force_refresh"] is True for _, kw in calls)
    rec = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
    assert rec["codes"] == ["600519", "000001"], \
        "账本 roster 必须与执行名单共用保序去重"
    assert set(rec["done"]) == {"600519", "000001"}
    flat = _flat(capsys.readouterr().out)
    assert "已完成2" in flat and "未记录结果0" in flat
    assert "结果记录不可靠" not in flat, "重复 roster 不得制造假未知"


# ── BLOCKER 4：四态写门双向（晚到结果不得洗白未开始；坏映射 fail-closed）──

def test_fix2_late_result_cannot_overwrite_confirmed_not_started(
        tmp_path, monkeypatch):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    reason = batch_scorer.BUDGET_STOP_REASON
    token = ss.batch_task_start("ba", "k", ["A", "B"], budget=dict(_VALID_BUDGET))
    assert ss.batch_task_not_started("ba", "k", "A", run_id=token, reason=reason) is True
    before = ledger.read_bytes()
    assert ss.batch_task_mark("ba", "k", "A", True, run_id=token) is False
    assert ss.batch_task_mark("ba", "k", "A", False, "晚到失败", run_id=token) is False
    assert ss.batch_task_mark("ba", "k", "A", True, run_id=token,
                              degraded=True) is False
    assert ledger.read_bytes() == before, "拒绝路径不得写盘"
    rec = next(t for t in ss.get_recent_batch_tasks(5) if t.get("key") == "k")
    assert rec["not_started"] == {"A": reason}
    assert "A" not in rec["done"] and "A" not in rec["failed"]
    assert "A" not in rec.get("degraded", {})
    assert ss.batch_task_mark("ba", "k", "B", True, run_id=token) is True


def test_fix2_not_started_fail_closed_when_state_maps_corrupt(
        tmp_path, monkeypatch):
    """已有 done/degraded/failed 映射损坏（无法核定）时不得写未开始、不抹未知。

    真实可达性边界：本产品写入端始终生成三个映射；损坏/缺失映射只可能来自坏账本、
    手工篡改或旧格式，属防御性 fail-closed，不会在正常 ba run 中出现。
    """
    reason = batch_scorer.BUDGET_STOP_REASON
    for field in ("done", "degraded", "failed", "missing"):
        ledger = _redirect_ledger(monkeypatch, tmp_path)
        rec = {"kind": "ba", "key": f"bad-{field}", "codes": ["A"],
               "run_id": "RUN-CORRUPT", "done": {}, "degraded": {}, "failed": {},
               "budget": dict(_VALID_BUDGET),
               "ts_started": "t", "ts_updated": "t"}
        if field == "missing":
            rec.pop("degraded")
        else:
            rec[field] = "A"      # 类型损坏：无法核定是否已有结果
        ledger.write_text(json.dumps({"tasks": [rec]}, ensure_ascii=False),
                          encoding="utf-8")
        before = ledger.read_bytes()
        assert ss.batch_task_not_started(
            "ba", f"bad-{field}", "A", run_id="RUN-CORRUPT", reason=reason) is False
        assert ledger.read_bytes() == before
        stored = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
        assert "not_started" not in stored, "不得覆盖/抹掉损坏的旧状态"


def test_fix2_no_budget_legacy_mark_semantics_preserved(tmp_path, monkeypatch):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    token = ss.batch_task_start("ba", "legacy", ["A"])
    assert ss.batch_task_mark("ba", "legacy", "A", True, run_id=token) is True
    assert ss.batch_task_mark("ba", "legacy", "A", False, "晚到", run_id=token) is True
    rec = ss.get_recent_batch_tasks(1)[0]
    assert "not_started" not in rec
    assert "A" in rec["failed"]


# ── BLOCKER 5：tasks 读门联核（预算/终止/身份/原因都齐才算确认未开始）──

def _ns_record(**over):
    rec = {"kind": "ba", "key": "联核", "codes": ["A", "B"],
           "run_id": "RUN-NS-GATE", "done": {}, "degraded": {}, "failed": {},
           "not_started": {"A": "budget_exhausted"},
           "stop_reason": "budget_exhausted",
           "budget": dict(_VALID_BUDGET),
           "ts_started": "t", "ts_updated": "t"}
    rec.update(over)
    return rec


def test_fix2_tasks_joint_gate_confirms_fully_verifiable_record(
        monkeypatch, tmp_path, capsys):
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [_ns_record()])
    assert "预算未开始1" in flat
    assert "确认未调用评分" in out
    assert "终止:预算到期" in flat


@pytest.mark.parametrize("over", [
    {"kind": "l all"},                                    # 非 ba
    {"kind": "la"},
    {"run_id": None},                                     # 旧身份
    {"run_id": ""},
    {"run_id": 123},
    {"budget": None},                                     # 无预算
    {"budget": dict(_VALID_BUDGET, budget_seconds=True)},  # BLOCKER 例
    {"budget": dict(_VALID_BUDGET, budget_seconds="60")},
    {"budget": dict(_VALID_BUDGET, budget_seconds=float("nan"))},
    {"budget": dict(_VALID_BUDGET, extra="sk-SECRET-GATE")},
    {"stop_reason": None},                                # 缺/错终止原因
    {"stop_reason": "timeout"},
    {"stop_reason": ""},
])
def test_fix2_tasks_isolates_not_started_without_joint_gate(
        over, monkeypatch, tmp_path, capsys):
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [_ns_record(**over)])
    assert "预算未开始1" not in flat, "联核不通过不得计确认未开始"
    assert "预算未开始0" in flat
    assert "确认未调用评分" not in out
    assert "待确认" in flat
    assert "sk-SECRET" not in out


def test_fix2_ledger_budget_writer_rejects_non_numeric_seconds(
        tmp_path, monkeypatch):
    """写入端继续只保存严格类型：布尔/字符串/nan/inf/None 秒数不落盘。"""
    _redirect_ledger(monkeypatch, tmp_path)
    for i, seconds in enumerate((True, "60", float("nan"), float("inf"), None)):
        token = ss.batch_task_start("ba", f"strict-{i}", ["A"], budget={
            "schema": "muyun.ba.budget/v1",
            "policy": "stop_starting_new_stock",
            "budget_seconds": seconds,
        })
        assert isinstance(token, str) and token
        rec = ss.get_recent_batch_tasks(1)[0]
        assert "budget" not in rec, f"非数值秒数不得落盘: {seconds!r}"



def test_tasks_view_flags_not_started_conflicts_without_double_count(
        monkeypatch, tmp_path, capsys):
    rec = {"kind": "ba", "key": "冲突", "codes": ["A", "B"],
           "run_id": "RUN-NS-CONFLICT", "done": {"A": "t"}, "degraded": {},
           "failed": {}, "budget": dict(_VALID_BUDGET),
           "not_started": {"A": "budget_exhausted", "B": "budget_exhausted"},
           "stop_reason": "budget_exhausted", "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "状态冲突1" in flat
    assert "预算未开始1" in flat and "已完成0" in flat
    assert "未记录结果0" in flat
    assert "已完成2" not in flat and "预算未开始2" not in flat
    assert "预算未开始" in out


def test_tasks_view_invalid_stop_reason_not_rendered(monkeypatch, tmp_path, capsys):
    rec = {"kind": "ba", "key": "坏终止原因", "codes": ["A"],
           "run_id": "RUN-STOP-BAD", "done": {}, "degraded": {}, "failed": {},
           "budget": dict(_VALID_BUDGET), "not_started": {},
           "stop_reason": "sk-SECRET-STOP", "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "原因校验未通过" in flat
    assert "sk-SECRET" not in out
    assert "预算:60秒" in flat


# ═══════ final_gate（主控综合复核残余，J4/J7 范围）═══════
# A：确认性文案只消费 tasks 已算出的合格未开始资格，不独立重判。
# B：not_started 字段已存在但非 dict → 拒绝且不改盘，不重置抹掉未知。
# C：三态原始 map 不可读/同代码旧值待确认时逐项隔离，可核的其它项仍确认。
# D：未产出可展示评分时只报告证据范围，不声称全批未开始/零费用。

def test_gate_a_stop_line_needs_qualified_not_started(monkeypatch, tmp_path, capsys):
    """run_id 缺失 → 联合门不通过：不得出现“确认未开始/确认未调用评分”承诺。"""
    rec = _ns_record(run_id=None)
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "确认未开始" not in out
    assert "确认未调用评分" not in out
    assert "未开始状态待确认" in flat, "只写已记录到期原因 + 未开始待确认"
    assert "预算未开始0" in flat


def test_gate_a_no_confirmation_when_budget_invalid(monkeypatch, tmp_path, capsys):
    rec = _ns_record(budget=dict(_VALID_BUDGET, budget_seconds=True))
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "描述校验未通过" in flat
    assert "确认未开始" not in out and "确认未调用评分" not in out
    assert "预算未开始0" in flat


def test_gate_a_qualified_record_uses_computed_count(monkeypatch, tmp_path, capsys):
    rec = _ns_record()
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "已确认1只未开始" in flat, "确认句必须复用 tasks 算出的合格数量"
    assert "确认未调用评分" in out


def test_gate_b_existing_not_started_non_dict_refused(tmp_path, monkeypatch):
    reason = batch_scorer.BUDGET_STOP_REASON
    for i, bad in enumerate(([], "text", None, True, 5)):
        ledger = _redirect_ledger(monkeypatch, tmp_path)
        key = f"ns-bad-{i}"
        rec = {"kind": "ba", "key": key, "codes": ["A"], "run_id": "RUN-B",
               "done": {}, "degraded": {}, "failed": {},
               "budget": dict(_VALID_BUDGET), "not_started": bad,
               "stop_reason": reason, "ts_started": "t", "ts_updated": "t"}
        ledger.write_text(json.dumps({"tasks": [rec]}, ensure_ascii=False),
                          encoding="utf-8")
        before = ledger.read_bytes()
        assert ss.batch_task_not_started("ba", key, "A", run_id="RUN-B",
                                         reason=reason) is False
        assert ledger.read_bytes() == before, "非 dict 的 not_started 不得被重置/改写"
        stored = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
        assert stored["not_started"] == bad, "未知旧状态必须原样保留"


def test_gate_b_existing_dict_bad_items_preserved(tmp_path, monkeypatch):
    reason = batch_scorer.BUDGET_STOP_REASON
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    rec = {"kind": "ba", "key": "k", "codes": ["A", "B"], "run_id": "RUN-D",
           "done": {}, "degraded": {}, "failed": {},
           "budget": dict(_VALID_BUDGET),
           "not_started": {"A": "weird-old"},
           "stop_reason": reason, "ts_started": "t", "ts_updated": "t"}
    ledger.write_text(json.dumps({"tasks": [rec]}, ensure_ascii=False), encoding="utf-8")
    assert ss.batch_task_not_started("ba", "k", "B", run_id="RUN-D",
                                     reason=reason) is True
    stored = json.loads(ledger.read_text(encoding="utf-8"))["tasks"][0]
    assert stored["not_started"] == {"A": "weird-old", "B": reason}, \
        "已有 dict 中的其它坏项必须保留，不为了新项清空旧状态"
    before = ledger.read_bytes()
    assert ss.batch_task_not_started("ba", "k", "A", run_id="RUN-D",
                                     reason=reason) is False, "不得覆盖未知旧原因"
    assert ledger.read_bytes() == before


def test_gate_c_unreadable_state_map_blocks_not_started_confirmation(
        monkeypatch, tmp_path, capsys):
    for field in ("done", "degraded", "failed"):
        rec = _ns_record()
        rec[field] = "corrupt-map"
        out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
        assert "预算未开始0" in flat, f"{field} 不可读时不得确认未开始"
        assert "确认未调用评分" not in out
        assert "待确认" in flat


def test_gate_c_pending_old_value_isolates_only_that_code(
        monkeypatch, tmp_path, capsys):
    rec = _ns_record(codes=["A", "B"], done={"A": ""},
                     not_started={"A": batch_scorer.BUDGET_STOP_REASON,
                                  "B": batch_scorer.BUDGET_STOP_REASON})
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "预算未开始1" in flat, "B 资料完整仍应独立确认，不整批一刀切"
    assert "预算未开始2" not in flat
    assert "确认未调用评分" in out
    assert "A:done记录值无效" in flat
    assert "待确认" in flat


def test_gate_d_partial_not_started_summary_marks_unknown(
        monkeypatch, tmp_path, capsys):
    """2 股仅 1 股明确 ns、另 1 股无结果：只能报告证据范围，不声称零费用/全批未开始。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_scorer, "freeze_batch_config",
                        lambda config=None: {"frozen": True})
    captured = {"ledger_path": ledger}
    reason = batch_scorer.BUDGET_STOP_REASON
    events = [{"code": "600519", "ok": False, "status": "not_started",
               "reason": reason}]
    monkeypatch.setattr(batch_scorer, "auto_score_batch",
                        _fake_batch_factory(
                            captured, rows=[], events=events,
                            result_extra={"not_started": ["600519"],
                                          "stop_reason": reason}))
    cli_main.benzong_batch_analyze([{"code": "600519"}, {"code": "000001"}],
                                   budget=60, src_label="partial")
    out = capsys.readouterr().out
    flat = _flat(out)
    assert "未产出可展示评分" in flat
    assert "已确认未开始1只" in flat
    assert "其他1只结果待确认" in flat
    assert "没有任何股票开始评分" not in flat
    assert "未产生评分费用" not in flat and "零费用" not in flat
    assert "全部确认未开始" not in flat


# ═══════ idempotence：幂等早返回必须与 tasks 读门一致 ═══════
# 已有 not_started[code]=='budget_exhausted' 时，只有 run.stop_reason 也是白名单
# 原因才能返回 True；缺失/非法 stop_reason 返回 False 且文件 bytes 不变（不自动修补）。
# 正常完整记录的幂等仍 True 且 bytes 不变（不重写）。

def _idem_record(**over):
    rec = {"kind": "ba", "key": "idem", "codes": ["A"], "run_id": "RUN-IDEM",
           "done": {}, "degraded": {}, "failed": {},
           "not_started": {"A": "budget_exhausted"},
           "stop_reason": "budget_exhausted",
           "budget": dict(_VALID_BUDGET),
           "ts_started": "t", "ts_updated": "t"}
    rec.update(over)
    return rec


def _seed_and_call_idem(tmp_path, monkeypatch, rec):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    ledger.write_text(json.dumps({"tasks": [rec]}, ensure_ascii=False),
                      encoding="utf-8")
    before = ledger.read_bytes()
    ok = ss.batch_task_not_started("ba", rec["key"], "A",
                                   run_id=rec["run_id"],
                                   reason=batch_scorer.BUDGET_STOP_REASON)
    return ok, before, ledger


def test_gate_f_idempotent_requires_run_stop_reason(tmp_path, monkeypatch):
    rec = _idem_record()
    rec.pop("stop_reason")
    ok, before, ledger = _seed_and_call_idem(tmp_path, monkeypatch, rec)
    assert ok is False, "幂等成功必须同时要求 run.stop_reason 合法"
    assert ledger.read_bytes() == before, "拒绝路径不得自动修补/写盘"


def test_gate_f_idempotent_rejects_invalid_run_stop_reason(tmp_path, monkeypatch):
    rec = _idem_record(stop_reason="timeout")
    ok, before, ledger = _seed_and_call_idem(tmp_path, monkeypatch, rec)
    assert ok is False, "非法 stop_reason 的幂等早返回不得报成功"
    assert ledger.read_bytes() == before, "拒绝路径不得自动修补/写盘"


def test_gate_f_idempotent_normal_record_still_true_and_unchanged(
        tmp_path, monkeypatch):
    rec = _idem_record()
    ok, before, ledger = _seed_and_call_idem(tmp_path, monkeypatch, rec)
    assert ok is True, "完整正常记录的幂等仍应成功"
    assert ledger.read_bytes() == before, "幂等成功不得重写/改动文件"


