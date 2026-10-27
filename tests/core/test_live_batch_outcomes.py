# -*- coding: utf-8 -*-
"""T05-L：live 深分析批量(l 多代码 / la / l all)的真实结果语义、当日已析记录与账本加固。

锁死语义(T05-L 结果部分):
- analyze_live 返回 LiveOutcome(COMPLETE / QUOTE_ONLY, 普通 Enum, 不是 str 混入);
  完全取不到数据仍抛 SystemExit
- 三个批循环只把 COMPLETE 记成功; 仅行情记降级且不写当日已析; None/bool/字符串/Mock 等
  任何其它返回都是"结果未知": 不计成功/失败/未开始, 不写账本, 不写当日已析
- 只有 l 多代码 / l all 的完整分析写当日已析(la 不新增同日缓存); 写失败必须可见
- mark_deep_analyzed 按原始根合并: 保留其它日期与同日其它条目(含坏/旧条目); 文件坏根/
  非 UTF-8/截断/当日节点非 dict 一律拒写且原文件字节不变
- l all 只认带 outcome=complete 标记的当日记录; 旧版无标记记录重新分析, 并在原确认前
  预告会重新花 AI 费用
- batch_task_mark 拒绝显式 not_started=None 与缺失/非 dict 的 done/degraded/failed
  (不再重置成 {} 洗白); batch_task_not_started 不得借新 code 洗白旧的坏/缺 stop_reason
- 启动预算描述按 kind 读写同门: ba 只认 ba schema, l/la/l all 只认 live schema

红灯设计: 业务红灯用例(真实 analyze_live 仅行情、mock 返回 None、真实文件合并/坏根、
账本加固的 ba 变体)的失败断言不依赖新符号, 旧代码下是断言失败而不是 ImportError/
AttributeError; 新符号(LiveOutcome / live 预算 API)一律在用例体内惰性取用, 导入期不触发
(live kind 变体本身需要 live 预算 API, 旧代码下以 AttributeError 红灯)。

全部离线: analyze_live 与数据源用替身, 账本/当日已析文件重定向到 tmp_path, 不碰真实
~/.muyun, 不触网, 不花 AI。

跑法: pytest tests/core/test_live_batch_outcomes.py
"""

import inspect
import json
import os
import re
import sys
import unittest.mock as mock
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

# patch 期间不得让模块首次 import(按值绑定会把替身永久捕获进导入方): 文件顶部先预导入
import start
import src.cli.main as cli_main
import src.cli.session_state as ss
import src.core.analysis_service as analysis_service
import src.core.benzong.batch_scorer as batch_scorer
import src.core.shadow_diff as shadow_diff
import src.data.account_service as account_module
import src.data.akshare_client as ak_mod
import src.data.portfolio as portfolio_mod
import src.data.proposals as proposals_module
from src.data.models import StockData


MODES = ["live_multi", "live_all", "live_scan_all"]
KIND_OF = {"live_multi": "l", "live_all": "la", "live_scan_all": "l all"}
SCAN_SOURCE = "bz scan 测试"
# 完整分析后写当日已析的 source: l 多代码固定标签, l all 取扫描来源, la 不写
DEEP_SOURCE = {"live_multi": "手动多代码", "live_all": None, "live_scan_all": SCAN_SOURCE}
KINDS = ["l", "la", "l all", "ba"]
LIVE_KINDS = ["l", "la", "l all"]
CODES2 = ["600519", "000001"]
CODES3 = ["600001", "600002", "600003"]


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch, tmp_path):
    """账本/当日已析/扫描快照全部落 tmp_path: 不碰真实 ~/.muyun。"""
    monkeypatch.setattr(ss, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", tmp_path / "batch_tasks.json")
    monkeypatch.setattr(ss, "_DEEP_ANALYZED_FILE", tmp_path / "deep_analyzed.json")
    monkeypatch.setattr(ss, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(ss, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    monkeypatch.setattr(ss, "_WATCH_FILE", tmp_path / "watchlist.jsonl")


# ── 通用工具 ─────────────────────────────────────────────────

def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _write_deep(root):
    ss._DEEP_ANALYZED_FILE.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")


def _read_deep():
    return json.loads(ss._DEEP_ANALYZED_FILE.read_text(encoding="utf-8"))


def _ledger_bytes():
    return ss._BATCH_TASKS_FILE.read_bytes()


def _latest():
    runs = ss.get_recent_batch_tasks(10)
    assert runs, "必须登记过批量任务 run"
    return runs[0]


def _edit_latest(fn):
    """直接改账本里最新 run 的原始 JSON(模拟旧文件/被篡改的记录, 绕过写侧校验)。"""
    path = ss._BATCH_TASKS_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data["tasks"][0])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _start_run(kind, codes=("A", "B"), key="k", budget=None):
    if budget is None:
        rid = ss.batch_task_start(kind, key, list(codes))
    else:
        rid = ss.batch_task_start(kind, key, list(codes), budget=budget)
    assert isinstance(rid, str) and rid, "run 必须登记成功"
    return rid, key


def _budget_for(kind, seconds=60):
    """该 kind 合同下的合法预算描述: ba 用 ba schema, live 三类用 live schema。"""
    if kind == "ba":
        return batch_scorer.budget_descriptor(seconds)
    return ss.live_budget_descriptor(seconds)


def _summary(out):
    lines = [ln for ln in out.splitlines() if "批量完成" in ln]
    assert len(lines) == 1, f"批量汇总行应恰有一行: {lines}"
    return lines[0]


# ── analyze_live 结果脚本(惰性取枚举成员, 旧代码下不在导入期失败) ──

def _complete():
    return cli_main.LiveOutcome.COMPLETE


def _quote_only():
    return cli_main.LiveOutcome.QUOTE_ONLY


def _ret(value):
    """脚本项: 原样返回任意值(用于构造未知返回)。"""
    return lambda: value


def _entry(code):
    return mock.MagicMock(stock_code=code, stock_name="N" + code, from_ledger=False)


class _Env:
    """live 三入口共用的离线环境(替身都在 monkeypatch 下, 结束自动还原)。

    - analyze_live: 记录调用, 按 script 返回/抛出(缺省返回 COMPLETE)
    - PortfolioManager: MagicMock; request_account_facts 返回同一份 facts
    - AKShareClient.prefetch_realtime_quotes: 无网络替身
    - l all: get_last_scan 返回 codes 对应 items; builtins.input 记录提示并返回 confirm
      (l 多代码 / la 不得新增确认: 它们一旦调 input 即失败)
    - 账本与当日已析缺省走真实实现(落 tmp_path); spy_ledger / spy_deep 可换成记录器
    """

    def __init__(self, monkeypatch, capsys, mode, codes, script=None, *,
                 real_analyze=False, confirm="y"):
        self.mp = monkeypatch
        self.capsys = capsys
        self.mode = mode
        self.codes = list(codes)
        self.script = dict(script or {})
        self.confirm = confirm
        self.calls = []        # [(code, kwargs)]
        self.pre = []          # 每次 input 瞬间之前新增的输出
        self.prompts = []
        self.buf = ""
        self.on_call = None
        self.on_input = None
        self.facts = mock.MagicMock(name="acct_facts")
        self.facts.holding_entries.return_value = ([_entry(c) for c in self.codes], False)
        self._wire(real_analyze)

    def _wire(self, real_analyze):
        mp = self.mp
        if not real_analyze:
            def fake_analyze(code, **kw):
                self.calls.append((code, kw))
                if self.on_call:
                    self.on_call(code)
                item = self.script.get(code, _complete)
                if isinstance(item, BaseException) or (
                        isinstance(item, type) and issubclass(item, BaseException)):
                    raise item
                return item()
            mp.setattr(cli_main, "analyze_live", fake_analyze)

        self.pm = mock.MagicMock(name="PortfolioManager")
        self.pm.return_value.request_account_facts.return_value = self.facts
        mp.setattr(portfolio_mod, "PortfolioManager", self.pm)
        mp.setattr(ak_mod.AKShareClient, "prefetch_realtime_quotes",
                   staticmethod(lambda codes: len(codes)))
        if self.mode == "live_scan_all":
            items = [{"code": c, "name": "N" + c} for c in self.codes]
            mp.setattr(ss, "get_last_scan", lambda: {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "source": SCAN_SOURCE, "count": len(items), "items": items})
        mp.setattr("builtins.input", self._input)

    def _drain(self):
        chunk = self.capsys.readouterr().out
        self.buf += chunk
        return chunk

    def _input(self, prompt=""):
        self.prompts.append(prompt)
        self.pre.append(self._drain())
        if self.mode != "live_scan_all":
            raise AssertionError("l 多代码 / la 不得新增确认门")
        if self.on_input:
            self.on_input()
        return self.confirm

    def run(self, **extra):
        args = {"live_multi": {"codes": list(self.codes)}, "live_all": {},
                "live_scan_all": {"force": False}}[self.mode]
        args.update(extra)
        start.run_cli(self.mode, args)
        return self.out()

    def out(self):
        self._drain()
        return self.buf

    def spy_ledger(self, run_id="RUN-1", with_budget=False):
        """账本函数换成记录器; with_budget=False 时 start 用旧签名(传 budget 即 TypeError)。"""
        seen = {"start": [], "mark": [], "ns": []}
        if with_budget:
            def fake_start(kind, key, codes, snapshot=None, budget=None):
                seen["start"].append((kind, key, list(codes), budget))
                return run_id
        else:
            def fake_start(kind, key, codes, snapshot=None):
                seen["start"].append((kind, key, list(codes), None))
                return run_id

        def fake_mark(kind, key, code, ok, note="", run_id=None, degraded=False):
            seen["mark"].append({"kind": kind, "key": key, "code": code, "ok": ok,
                                 "note": note, "run_id": run_id, "degraded": degraded})
            return True

        def fake_not_started(kind, key, code, run_id=None, reason=""):
            seen["ns"].append((kind, key, code, run_id, reason))
            return True

        self.mp.setattr(ss, "batch_task_start", fake_start)
        self.mp.setattr(ss, "batch_task_mark", fake_mark)
        self.mp.setattr(ss, "batch_task_not_started", fake_not_started)
        return seen

    def spy_deep(self, result=True):
        """mark_deep_analyzed 换成记录器, 返回 [(codes, source)]。"""
        calls = []

        def fake(codes, source=""):
            calls.append((list(codes), source))
            return result

        self.mp.setattr(ss, "mark_deep_analyzed", fake)
        return calls


def _deep_expect(mode, codes):
    """该入口对"完整分析"应有的当日已析写入记录。"""
    source = DEEP_SOURCE[mode]
    return [] if source is None else [([c], source) for c in codes]


# ═══════ 1. LiveOutcome 与真实 analyze_live 返回值 ═══════

def _quote_only_market(monkeypatch):
    """get_stock_data 只有价格没有任何技术指标: 真实 analyze_live 走仅行情分支。"""
    def fake_get_stock_data(code, *a, **k):
        return StockData(stock_code=code, stock_name="仅行情股" + code, price=10.0,
                         volume=1000.0, change_pct=1.0)

    monkeypatch.setattr(ak_mod, "get_stock_data", fake_get_stock_data)
    monkeypatch.setattr(cli_main, "_print_daily_research_view", lambda *a, **k: None)


def _isolate_account(monkeypatch, tmp_path):
    monkeypatch.setattr(portfolio_mod, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")


def _stub_complete_market(monkeypatch):
    """真实 analyze_live 完整路径(有指标 -> 编排器 -> 摘要)的离线替身: 只替换外部叶子。

    返回假编排器, 供断言"确实走过编排器"。账户事实走真实 PortfolioManager(空账户, tmp)。
    """
    def fake_get_stock_data(code, *a, **k):
        return StockData(stock_code=code, stock_name="测试股", price=10.0, volume=100000,
                         change_pct=1.0, avg_volume_20=100000.0,
                         ma5=9.8, ma20=9.5, ma60=9.0, quote_as_of="2026-09-30")

    orch = mock.MagicMock(name="orchestrator")
    orch.analyze.return_value = (mock.MagicMock(), mock.MagicMock(),
                                 mock.MagicMock(), mock.MagicMock())
    monkeypatch.setattr(ak_mod, "get_stock_data", fake_get_stock_data)
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    monkeypatch.setattr(cli_main, "load_config", lambda *a, **k: {})
    monkeypatch.setattr(cli_main, "build_live_orchestrator",
                        lambda config, rag_service=None: orch)
    monkeypatch.setattr(cli_main, "_watch_pool_touch", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "_print_plain_summary", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "_print_daily_research_view", lambda *a, **k: None)
    monkeypatch.setattr(cli_main._evidence, "record_evidence", lambda *a, **k: None)
    monkeypatch.setattr(analysis_service, "build_decision_packet", lambda *a, **k: None)
    monkeypatch.setattr(shadow_diff, "capture_shadow", lambda *a, **k: None)
    return orch


def test_live_outcome_is_plain_enum_with_two_members():
    import enum
    outcome = cli_main.LiveOutcome
    assert issubclass(outcome, enum.Enum)
    assert not issubclass(outcome, str), "不得是 str 混入: 字符串不能冒充结果"
    assert not issubclass(outcome, int), "不得是 int 混入: bool/数字不能冒充结果"
    assert {m.name for m in outcome} == {"COMPLETE", "QUOTE_ONLY"}
    assert outcome.COMPLETE is not outcome.QUOTE_ONLY
    for member in outcome:
        for fake in (member.name, member.name.lower(), member.value, True, 1):
            assert member != fake, f"{member!r} 不得等于 {fake!r}"


def test_analyze_live_price_only_returns_quote_only(monkeypatch, capsys):
    _quote_only_market(monkeypatch)
    result = cli_main.analyze_live("600519", compact=True)
    assert result is cli_main.LiveOutcome.QUOTE_ONLY
    assert "仅实时数据可用，无法进行技术分析" in capsys.readouterr().out


def test_analyze_live_quote_fallback_returns_quote_only(monkeypatch):
    """完整数据取不到、走实时行情兜底: 同样只是仅行情, 不是完成。"""
    monkeypatch.setattr(ak_mod, "get_stock_data", lambda code, *a, **k: None)
    monkeypatch.setattr(ak_mod.AKShareClient, "get_realtime_quote", staticmethod(
        lambda code, retry=1: {"stock_code": code, "stock_name": "兜底股", "price": 10.0,
                               "open": 9.9, "high": 10.1, "low": 9.8,
                               "change_pct": 1.0, "volume": 1000.0}))
    monkeypatch.setattr(cli_main, "_print_daily_research_view", lambda *a, **k: None)
    assert cli_main.analyze_live("600519", compact=True) is cli_main.LiveOutcome.QUOTE_ONLY


def test_analyze_live_no_data_at_all_still_exits(monkeypatch):
    monkeypatch.setattr(ak_mod, "get_stock_data", lambda code, *a, **k: None)
    monkeypatch.setattr(ak_mod.AKShareClient, "get_realtime_quote",
                        staticmethod(lambda code, retry=1: None))
    with pytest.raises(SystemExit):
        cli_main.analyze_live("600519", compact=True)


def test_analyze_live_full_path_returns_complete(monkeypatch, tmp_path):
    """完整编排器路径之后返回 COMPLETE(接线断言: 少一个 return 则全批变"结果未知")。"""
    _isolate_account(monkeypatch, tmp_path)
    orch = _stub_complete_market(monkeypatch)
    result = cli_main.analyze_live("600519", compact=True)
    assert result is cli_main.LiveOutcome.COMPLETE
    orch.analyze.assert_called_once()


# ═══════ 2. 业务红灯: 真实 analyze_live 仅行情 / 完整 -> 批循环记账 ═══════

@pytest.mark.parametrize("mode", MODES)
def test_quote_only_real_analyze_live_is_degraded_not_success(mode, monkeypatch, capsys):
    """真实 analyze_live 只拿到行情: 不记当日已析、账本降级而非完成、汇总 0 成功。

    旧代码里 analyze_live 返回 None, 循环把仅行情当成功并写当日已析 -> 本用例断言失败。
    """
    _quote_only_market(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2, real_analyze=True)
    out = env.run()

    assert ss.get_deep_analyzed() == {}, "仅行情不得写当日已析"
    raw = ss._DEEP_ANALYZED_FILE
    text = raw.read_text(encoding="utf-8") if raw.exists() else ""
    assert all(code not in text for code in CODES2)
    run = _latest()
    assert run["kind"] == KIND_OF[mode]
    assert run["done"] == {}, "仅行情不得计为完成"
    assert set(run["degraded"]) == set(CODES2)
    assert run["failed"] == {}
    assert "仅行情" in out
    assert "批量完成：0/2 成功" in out


def test_complete_real_analyze_live_end_to_end_marks_done_and_deep(monkeypatch, tmp_path, capsys):
    """接线全链: run_cli -> 真实 analyze_live 完整路径 -> 账本 done + 带标记的当日已析。"""
    _isolate_account(monkeypatch, tmp_path)
    _stub_complete_market(monkeypatch)
    monkeypatch.setattr(ak_mod.AKShareClient, "prefetch_realtime_quotes",
                        staticmethod(lambda codes: len(codes)))
    start.run_cli("live_multi", {"codes": list(CODES2)})
    out = capsys.readouterr().out

    run = _latest()
    assert set(run["done"]) == set(CODES2)
    assert run["degraded"] == {} and run["failed"] == {}
    deep = ss.get_deep_analyzed()
    assert set(deep) == set(CODES2)
    assert all(ss.deep_record_complete(rec) for rec in deep.values())
    assert "批量完成：2/2 成功" in out


# ═══════ 3. 结果未知: None/bool/字符串/Mock 都不冒充成功 ═══════

@pytest.mark.parametrize("mode", MODES)
def test_none_return_is_unknown_not_counted_nor_marked(mode, monkeypatch, capsys):
    """mock 的 analyze_live 返回 None: 不计成功、不写账本、不写当日已析。

    旧代码把任何不抛异常的返回都计成功 -> 旧代码汇总 2/2 成功, 本用例断言失败。
    """
    env = _Env(monkeypatch, capsys, mode, CODES2,
               script={c: _ret(None) for c in CODES2})
    out = env.run()
    assert "批量完成：0/2 成功" in out
    assert "结果未知" in out
    run = _latest()
    assert run["done"] == {} and run["degraded"] == {} and run["failed"] == {}
    assert ss.get_deep_analyzed() == {}


UNKNOWN_RETURNS = [
    pytest.param(_ret(None), id="None"),
    pytest.param(_ret(True), id="True"),
    pytest.param(_ret(1), id="int-1"),
    pytest.param(_ret("complete"), id="str-complete"),
    pytest.param(_ret("COMPLETE"), id="str-COMPLETE"),
    pytest.param(_ret("quote_only"), id="str-quote_only"),
    pytest.param(_ret(mock.MagicMock()), id="MagicMock"),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("thunk", UNKNOWN_RETURNS)
def test_every_non_outcome_return_is_unknown(mode, thunk, monkeypatch, capsys):
    env = _Env(monkeypatch, capsys, mode, CODES2, script={c: thunk for c in CODES2})
    seen = env.spy_ledger()
    deep = env.spy_deep()
    out = env.run()
    assert seen["mark"] == [], "未知结果不得写账本任何状态"
    assert seen["ns"] == []
    assert deep == [], "未知结果不得写当日已析"
    assert "结果未知" in out
    assert "批量完成：0/2 成功" in out


# ═══════ 4. 批循环: COMPLETE / QUOTE_ONLY / 失败 / 混合 ═══════

@pytest.mark.parametrize("mode", MODES)
def test_complete_marks_done_and_deep_analyzed_per_mode(mode, monkeypatch, capsys):
    env = _Env(monkeypatch, capsys, mode, CODES2)
    seen = env.spy_ledger()
    deep = env.spy_deep()
    out = env.run()

    assert [c for c, _ in env.calls] == CODES2
    for _, kw in env.calls:
        assert kw.get("compact") is True
        assert kw.get("account_facts") is env.facts, "全批共享同一份账户事实"
        assert "ai_overrides" in kw and "ai_debug" in kw
    assert env.pm.return_value.request_account_facts.call_count == 1, "全批只读一次账户快照"
    assert [(m["kind"], m["code"], m["ok"], m["degraded"]) for m in seen["mark"]] == [
        (KIND_OF[mode], c, True, False) for c in CODES2]
    assert deep == _deep_expect(mode, CODES2), "只有 l 多代码 / l all 写当日已析, la 不写"
    assert "批量完成：2/2 成功" in out


@pytest.mark.parametrize("mode", MODES)
def test_quote_only_marks_degraded_and_never_deep(mode, monkeypatch, capsys):
    env = _Env(monkeypatch, capsys, mode, CODES2, script={c: _quote_only for c in CODES2})
    seen = env.spy_ledger()
    deep = env.spy_deep()
    out = env.run()

    assert [(m["kind"], m["code"], m["ok"], m["degraded"]) for m in seen["mark"]] == [
        (KIND_OF[mode], c, True, True) for c in CODES2]
    assert all("仅行情" in m["note"] for m in seen["mark"])
    assert deep == [], "仅行情不得写当日已析"
    assert "仅行情" in out
    assert "批量完成：0/2 成功" in out


@pytest.mark.parametrize("mode", MODES)
def test_exit_and_exception_are_failed_and_not_deep(mode, monkeypatch, capsys):
    codes = CODES3
    env = _Env(monkeypatch, capsys, mode, codes,
               script={codes[0]: SystemExit, codes[1]: RuntimeError("boom")})
    seen = env.spy_ledger()
    deep = env.spy_deep()
    out = env.run()

    marks = {m["code"]: m for m in seen["mark"]}
    assert marks[codes[0]]["ok"] is False and "数据获取失败" in marks[codes[0]]["note"]
    assert marks[codes[1]]["ok"] is False and "boom" in marks[codes[1]]["note"]
    assert marks[codes[2]]["ok"] is True and marks[codes[2]]["degraded"] is False
    assert deep == _deep_expect(mode, [codes[2]]), "失败股不写当日已析, 其余照常"
    assert "批量完成：1/3 成功" in out
    assert _summary(out).rstrip().endswith(f"；失败: {codes[0]}, {codes[1]}")


@pytest.mark.parametrize("mode", MODES)
def test_mixed_outcomes_summary_counts_and_failures_last(mode, monkeypatch, capsys):
    codes = ["600001", "600002", "600003", "600004", "600005"]
    env = _Env(monkeypatch, capsys, mode, codes, script={
        "600001": _complete, "600002": _quote_only, "600003": _ret(None),
        "600004": SystemExit, "600005": RuntimeError("boom")})
    out = env.run()

    run = _latest()
    assert set(run["done"]) == {"600001"}
    assert set(run["degraded"]) == {"600002"}
    assert set(run["failed"]) == {"600004", "600005"}
    for name in ("done", "degraded", "failed"):
        assert "600003" not in run[name], "结果未知的股票不进任何状态"
    assert set(ss.get_deep_analyzed()) == (
        set() if DEEP_SOURCE[mode] is None else {"600001"})

    line = _summary(out)
    assert line.strip().startswith("批量完成：1/5 成功"), "分子只数完整分析"
    assert "仅行情" in line and "结果未知 1" in line
    assert line.rstrip().endswith("；失败: 600004, 600005"), "失败清单保持在最后"
    assert line.index("仅行情") < line.index("；失败")
    assert line.index("结果未知") < line.index("；失败")


@pytest.mark.parametrize("mode", ["live_multi", "live_scan_all"])
def test_deep_analyzed_write_failure_is_visible(mode, monkeypatch, capsys):
    """当日已析写失败不得静默: 提示用户, 业务结果(完成)不变。"""
    env = _Env(monkeypatch, capsys, mode, CODES2)
    deep = env.spy_deep(result=False)
    out = env.run()
    assert [c for c, _ in deep] == [[c] for c in CODES2]
    assert out.count("当日已析记录未保存") >= 2, "每只写失败都要有可见提示"
    assert set(_latest()["done"]) == set(CODES2)
    assert "批量完成：2/2 成功" in out


def test_la_never_touches_deep_analyzed_even_on_complete(monkeypatch, capsys):
    env = _Env(monkeypatch, capsys, "live_all", CODES2)

    def boom(*a, **k):
        raise AssertionError("la 不新增同日缓存: 不得调用 mark_deep_analyzed")

    monkeypatch.setattr(ss, "mark_deep_analyzed", boom)
    out = env.run()
    assert set(_latest()["done"]) == set(CODES2)
    assert "当日已析记录未保存" not in out


# ═══════ 5. mark_deep_analyzed: 原始根合并 / 坏根拒写(真实 tmp 文件) ═══════

def test_mark_deep_analyzed_signature_unchanged():
    params = list(inspect.signature(ss.mark_deep_analyzed).parameters.values())
    assert [p.name for p in params] == ["codes", "source"]
    assert params[1].default == ""


def test_mark_deep_analyzed_writes_complete_marker_on_fresh_file():
    assert ss.DEEP_ANALYZED_COMPLETE == "complete"
    assert ss.mark_deep_analyzed(["600519", "", "000001"], source="手动多代码") is True
    day = _read_deep()[_today()]
    assert set(day) == {"600519", "000001"}, "空代码不写"
    for code in ("600519", "000001"):
        rec = day[code]
        assert set(rec) == {"time", "source", "outcome"}
        assert rec["outcome"] == "complete" and rec["source"] == "手动多代码"
        assert re.fullmatch(r"\d{2}:\d{2}", rec["time"])
        assert ss.deep_record_complete(rec)


def test_mark_deep_analyzed_merges_history_and_keeps_other_entries_untouched():
    seed = {
        "meta": {"v": 1},
        "2026-01-01": {"HIST": {"time": "09:00", "source": "old"}},
        _today(): {
            "OLD": {"time": "09:30", "source": "旧版"},           # 旧格式: 无 outcome
            "BADSTR": "oops", "BADNULL": None, "BADLIST": [1],    # 坏条目原样保留
        },
    }
    _write_deep(seed)
    assert ss.mark_deep_analyzed(["NEW"], source="s") is True

    root = _read_deep()
    assert root.get("meta") == seed["meta"], "非日期键也要原样保留"
    assert root.get("2026-01-01") == seed["2026-01-01"], "其它日期必须保留"
    day = root.get(_today())
    assert isinstance(day, dict)
    assert set(day) == {"OLD", "BADSTR", "BADNULL", "BADLIST", "NEW"}
    for key in ("OLD", "BADSTR", "BADNULL", "BADLIST"):
        assert day[key] == seed[_today()][key], f"同日其它条目 {key} 不得被改动"
    assert day["NEW"].get("outcome") == "complete" and day["NEW"]["source"] == "s"
    assert "outcome" not in day["OLD"], "旧记录不得被顺手补上完成标记"


def test_mark_deep_analyzed_remark_overwrites_only_that_code():
    _write_deep({_today(): {
        "A": {"time": "09:30", "source": "旧版"},
        "B": {"time": "09:31", "source": "x", "outcome": "complete"}}})
    assert ss.mark_deep_analyzed(["A"], source="重析") is True
    day = _read_deep()[_today()]
    assert day["A"].get("outcome") == "complete" and day["A"]["source"] == "重析"
    assert day["B"] == {"time": "09:31", "source": "x", "outcome": "complete"}


BAD_ROOTS = [
    pytest.param(b"[]", id="list-root"),
    pytest.param(b"null", id="null-root"),
    pytest.param(b'"x"', id="string-root"),
    pytest.param(b"123", id="number-root"),
    pytest.param(b'{"a":', id="truncated"),
    pytest.param(b"\xff\xfe\x00{", id="invalid-utf8"),
]


@pytest.mark.parametrize("raw", BAD_ROOTS)
def test_mark_deep_analyzed_bad_root_refused_and_bytes_untouched(raw):
    """坏根不得被洗成新根: 返回 False, 原文件字节不变, 不留临时文件。"""
    path = ss._DEEP_ANALYZED_FILE
    path.write_bytes(raw)
    assert ss.mark_deep_analyzed(["600519"], source="s") is False
    assert path.read_bytes() == raw
    assert not list(path.parent.glob("deep_analyzed.json.*.tmp"))
    assert ss.get_deep_analyzed() == {}


@pytest.mark.parametrize("node", [[1], "x", 5, None], ids=["list", "str", "int", "null"])
def test_mark_deep_analyzed_bad_today_node_refused_and_bytes_untouched(node):
    raw = json.dumps({"2026-01-01": {"X": {"time": "09:00", "source": "s"}},
                      _today(): node}, ensure_ascii=False).encode("utf-8")
    path = ss._DEEP_ANALYZED_FILE
    path.write_bytes(raw)
    assert ss.mark_deep_analyzed(["600519"], source="s") is False
    assert path.read_bytes() == raw


def test_mark_deep_analyzed_write_failure_returns_false_and_keeps_file(monkeypatch):
    raw = json.dumps({_today(): {"A": {"time": "09:30", "source": "旧版"}}}).encode("utf-8")
    ss._DEEP_ANALYZED_FILE.write_bytes(raw)
    monkeypatch.setattr(ss, "_atomic_write_json", lambda *a, **k: False)
    assert ss.mark_deep_analyzed(["B"], source="s") is False
    assert ss._DEEP_ANALYZED_FILE.read_bytes() == raw


def test_get_deep_analyzed_returns_today_dict_entries_qualified_or_not():
    _write_deep({
        "2026-01-01": {"HIST": {"time": "09:00", "source": "old", "outcome": "complete"}},
        _today(): {"OLD": {"time": "09:30", "source": "旧版"},
                   "NEW": {"time": "09:31", "source": "s", "outcome": "complete"},
                   "BAD": "oops"},
    })
    got = ss.get_deep_analyzed()
    assert set(got) == {"OLD", "NEW"}, "今天的 dict 条目全返回(含无标记旧记录), 坏条目/其它日期不返回"
    assert ss.deep_record_complete(got["NEW"]) is True
    assert ss.deep_record_complete(got["OLD"]) is False


@pytest.mark.parametrize("rec,expected", [
    ({"outcome": "complete"}, True),
    ({"time": "09:30", "source": "s", "outcome": "complete"}, True),
    ({"time": "09:30", "source": "s"}, False),
    ({"outcome": "COMPLETE"}, False),
    ({"outcome": "quote_only"}, False),
    ({"outcome": True}, False),
    ({"outcome": None}, False),
    ({}, False),
    ("complete", False),
    (["complete"], False),
    (None, False),
])
def test_deep_record_complete_only_true_for_dict_with_complete_marker(rec, expected):
    assert ss.deep_record_complete(rec) is expected


# ═══════ 6. l all: 只认带完成标记的当日记录 ═══════

def _seed_today(records, **other_dates):
    root = dict(other_dates)
    root[_today()] = records
    _write_deep(root)


def _qualified(time="09:30"):
    return {"time": time, "source": "x", "outcome": "complete"}


def test_l_all_skips_qualified_record_and_runs_the_rest(monkeypatch, capsys):
    codes = ["600001", "600002"]
    _seed_today({"600001": _qualified()})
    env = _Env(monkeypatch, capsys, "live_scan_all", codes)
    out = env.run()
    assert [c for c, _ in env.calls] == ["600002"]
    assert "今天已深分析 1 只" in out
    assert "批量完成：1/1 成功" in out
    assert "旧" not in env.pre[0], "只有合格记录时不得出现旧版记录提示"
    assert "1 只" in env.prompts[0]


def test_l_all_old_format_records_are_reanalyzed_with_cost_notice_before_prompt(monkeypatch, capsys):
    """无完成标记的旧记录不再被当作已析: 重新分析, 并在原 y/N 确认前预告 AI 费用。"""
    codes = ["600001", "600002", "600003"]
    _seed_today({
        "600001": {"time": "09:30", "source": "旧版"},      # 旧格式, 无标记
        "600002": _qualified("09:31"),
    })
    env = _Env(monkeypatch, capsys, "live_scan_all", codes)
    out = env.run()

    assert [c for c, _ in env.calls] == ["600001", "600003"], "旧记录要重析, 合格记录才跳过"
    pre = env.pre[0]
    assert "旧" in pre and "重新" in pre and "AI" in pre, f"确认前必须预告旧记录重析费用: {pre!r}"
    assert "今天已深分析 1 只" in pre, "只有合格记录算已跳过"
    assert "2 只" in env.prompts[0], "y/N 提示必须把重析的旧记录算进去"
    assert "批量完成：2/2 成功" in out
    deep = ss.get_deep_analyzed()
    assert ss.deep_record_complete(deep["600001"]), "重析完成后补上完成标记"
    assert deep["600002"] == _qualified("09:31")


@pytest.mark.parametrize("record", [
    {"time": "09:30", "source": "x", "outcome": "quote_only"},
    {"time": "09:30", "source": "x", "outcome": "COMPLETE"},
    {"time": "09:30", "source": "x", "outcome": True},
], ids=["quote_only", "upper", "bool"])
def test_l_all_non_marker_outcome_values_do_not_qualify(record, monkeypatch, capsys):
    _seed_today({"600001": record})
    env = _Env(monkeypatch, capsys, "live_scan_all", ["600001", "600002"])
    env.run()
    assert [c for c, _ in env.calls] == ["600001", "600002"]


def test_l_all_all_qualified_says_done_and_never_prompts(monkeypatch, capsys):
    codes = ["600001", "600002"]
    _seed_today({c: _qualified() for c in codes})
    env = _Env(monkeypatch, capsys, "live_scan_all", codes)
    out = env.run()
    assert "全都分析过了" in out
    assert env.prompts == [], "全部合格跳过时不得再弹确认"
    assert env.calls == []


# ═══════ 7. 账本加固: batch_task_mark / batch_task_not_started ═══════

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("bad", [None, [], "x", 0, False],
                         ids=["null", "list", "str", "zero", "false"])
def test_mark_rejects_explicit_non_dict_not_started(kind, bad):
    """not_started 字段存在但不是 dict(含显式 null)=> 无法核定旧状态, 拒绝且文件不变。"""
    rid, key = _start_run(kind)
    _edit_latest(lambda rec: rec.__setitem__("not_started", bad))
    before = _ledger_bytes()
    assert ss.batch_task_mark(kind, key, "A", True, run_id=rid) is False
    assert _ledger_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field", ["done", "degraded", "failed"])
@pytest.mark.parametrize("bad", ["__missing__", [], "x", None, 5],
                         ids=["missing", "list", "str", "null", "int"])
@pytest.mark.parametrize("ok", [True, False], ids=["ok", "fail"])
def test_mark_rejects_missing_or_non_dict_status_maps(kind, field, bad, ok):
    """done/degraded/failed 缺失或非 dict: 不得重置成 {} 洗白, 返回 False 且字节不变。"""
    rid, key = _start_run(kind)

    def _break(rec):
        if bad == "__missing__":
            rec.pop(field)
        else:
            rec[field] = bad

    _edit_latest(_break)
    before = _ledger_bytes()
    assert ss.batch_task_mark(kind, key, "A", ok, run_id=rid) is False
    assert _ledger_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
def test_mark_still_works_on_healthy_record(kind):
    rid, key = _start_run(kind)
    assert ss.batch_task_mark(kind, key, "A", True, run_id=rid) is True
    assert ss.batch_task_mark(kind, key, "B", True, "仅行情", run_id=rid, degraded=True) is True
    run = _latest()
    assert "A" in run["done"] and "B" in run["degraded"]


@pytest.mark.parametrize("kind", LIVE_KINDS)
def test_mark_tolerates_healthy_not_started_dict_but_refuses_its_codes(kind):
    rid, key = _start_run(kind, budget=_budget_for(kind))
    assert ss.batch_task_not_started(kind, key, "B", run_id=rid,
                                     reason="budget_exhausted") is True
    assert ss.batch_task_mark(kind, key, "A", True, run_id=rid) is True, "不相干 code 照常记账"
    before = _ledger_bytes()
    assert ss.batch_task_mark(kind, key, "B", True, run_id=rid) is False, \
        "已确认未开始的 code 不得被晚到结果洗成已开始"
    assert _ledger_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stop", ["weird", None, 5, [], "BUDGET_EXHAUSTED", ""],
                         ids=["weird", "null", "int", "list", "upper", "empty"])
def test_not_started_refuses_when_stop_reason_present_but_not_whitelisted(kind, stop):
    """run 级 stop_reason 已存在却不是 budget_exhausted: 不得借新 code 把它洗成合法。"""
    rid, key = _start_run(kind, budget=_budget_for(kind))
    _edit_latest(lambda rec: rec.__setitem__("stop_reason", stop))
    before = _ledger_bytes()
    assert ss.batch_task_not_started(kind, key, "B", run_id=rid,
                                     reason="budget_exhausted") is False
    assert _ledger_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
def test_not_started_refuses_new_code_when_old_entries_lack_stop_reason(kind):
    """not_started 已有条目却缺 stop_reason(旧状态不可核定): 新 code 不得补写洗白。"""
    rid, key = _start_run(kind, budget=_budget_for(kind))
    _edit_latest(lambda rec: rec.__setitem__("not_started", {"A": "budget_exhausted"}))
    before = _ledger_bytes()
    assert ss.batch_task_not_started(kind, key, "B", run_id=rid,
                                     reason="budget_exhausted") is False
    assert _ledger_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
def test_not_started_accepts_clean_and_empty_dict_records(kind):
    rid, key = _start_run(kind, budget=_budget_for(kind))
    _edit_latest(lambda rec: rec.__setitem__("not_started", {}))   # 空 dict 不算旧状态
    assert ss.batch_task_not_started(kind, key, "B", run_id=rid,
                                     reason="budget_exhausted") is True
    run = _latest()
    assert run["not_started"] == {"B": "budget_exhausted"}
    assert run["stop_reason"] == "budget_exhausted"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("case", ["matching", "cross_schema", "missing", "extra_key", "bool_seconds"])
def test_not_started_is_gated_by_the_kinds_own_budget(kind, case):
    """live 三类认 live schema, ba 认 ba schema; 跨 schema/缺失/脏描述都不得登记未开始。"""
    rid, key = _start_run(kind)
    mine = _budget_for(kind)
    other = _budget_for("l" if kind == "ba" else "ba")

    def _set(rec):
        if case == "matching":
            rec["budget"] = dict(mine)
        elif case == "cross_schema":
            rec["budget"] = dict(other)
        elif case == "missing":
            rec.pop("budget", None)
        elif case == "extra_key":
            rec["budget"] = dict(mine, extra=1)
        else:
            rec["budget"] = dict(mine, budget_seconds=True)

    _edit_latest(_set)
    before = _ledger_bytes()
    got = ss.batch_task_not_started(kind, key, "B", run_id=rid, reason="budget_exhausted")
    if case == "matching":
        assert got is True
        assert _latest()["not_started"] == {"B": "budget_exhausted"}
    else:
        assert got is False
        assert _ledger_bytes() == before


@pytest.mark.parametrize("which", ["ba", "live"])
def test_not_started_refuses_unsupported_kind_even_with_valid_budget(which):
    rid, key = _start_run("zz")
    _edit_latest(lambda rec: rec.__setitem__("budget", _budget_for("ba" if which == "ba" else "l")))
    before = _ledger_bytes()
    assert ss.batch_task_not_started("zz", key, "B", run_id=rid,
                                     reason="budget_exhausted") is False
    assert _ledger_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
def test_not_started_refuses_bad_reason_wrong_run_and_foreign_code(kind):
    rid, key = _start_run(kind, budget=_budget_for(kind))
    before = _ledger_bytes()
    assert ss.batch_task_not_started(kind, key, "B", run_id=rid, reason="other") is False
    assert ss.batch_task_not_started(kind, key, "B", run_id="not-the-run",
                                     reason="budget_exhausted") is False
    assert ss.batch_task_not_started(kind, key, "ZZZ", run_id=rid,
                                     reason="budget_exhausted") is False
    assert ss.batch_task_not_started(kind, key, "B", run_id=None,
                                     reason="budget_exhausted") is False
    assert _ledger_bytes() == before


# ═══════ 8. 预算描述: live 合同与按 kind 读写同门 ═══════

def test_live_budget_constants_and_signature():
    assert ss.LIVE_BATCH_KINDS == ("l", "la", "l all")
    assert ss.LIVE_BUDGET_SCHEMA == "muyun.live.budget/v1"
    assert ss.LIVE_BUDGET_POLICY == "stop_starting_new_stock"
    assert ss.LIVE_BUDGET_STOP_REASON == "budget_exhausted"
    assert ss.LIVE_BUDGET_SCHEMA != batch_scorer.BUDGET_SCHEMA, "live 与 ba 是两份独立合同"
    names = [p.name for p in inspect.signature(ss.batch_task_start).parameters.values()]
    assert names == ["kind", "key", "codes", "snapshot", "budget"]


@pytest.mark.parametrize("seconds", [1, 0.5, 60, 600.0, 1e-3])
def test_live_budget_descriptor_valid(seconds):
    desc = ss.live_budget_descriptor(seconds)
    assert desc == {"schema": "muyun.live.budget/v1", "policy": "stop_starting_new_stock",
                    "budget_seconds": float(seconds)}
    assert ss.validate_live_budget_descriptor(desc) == desc
    assert ss.validate_live_budget_descriptor(dict(desc)) == desc


@pytest.mark.parametrize("seconds", [
    pytest.param(True, id="true"), pytest.param(False, id="false"),
    pytest.param(0, id="zero"), pytest.param(-1, id="neg-int"),
    pytest.param(-0.5, id="neg-float"), pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="inf"), pytest.param(-float("inf"), id="neg-inf"),
    pytest.param("60", id="str"), pytest.param(None, id="none"),
    pytest.param([], id="list"), pytest.param({}, id="dict"),
])
def test_live_budget_descriptor_rejects_bad_seconds(seconds):
    assert ss.live_budget_descriptor(seconds) is None


def _good_live():
    return {"schema": "muyun.live.budget/v1", "policy": "stop_starting_new_stock",
            "budget_seconds": 60.0}


@pytest.mark.parametrize("raw", [
    pytest.param(None, id="none"),
    pytest.param([], id="list"),
    pytest.param("x", id="str"),
    pytest.param(60, id="int"),
    pytest.param({"schema": "muyun.live.budget/v1",
                  "policy": "stop_starting_new_stock"}, id="missing-seconds"),
    pytest.param(dict(_good_live(), extra=1), id="extra-key"),
    pytest.param(dict(_good_live(), schema="muyun.ba.budget/v1"), id="ba-schema"),
    pytest.param(dict(_good_live(), schema="muyun.live.budget/v2"), id="schema-v2"),
    pytest.param(dict(_good_live(), policy="hard_kill"), id="bad-policy"),
    pytest.param(dict(_good_live(), budget_seconds=True), id="bool-seconds"),
    pytest.param(dict(_good_live(), budget_seconds=float("nan")), id="nan-seconds"),
    pytest.param(dict(_good_live(), budget_seconds=0), id="zero-seconds"),
    pytest.param(dict(_good_live(), budget_seconds="60"), id="str-seconds"),
])
def test_validate_live_budget_descriptor_rejects_bad_shapes(raw):
    assert ss.validate_live_budget_descriptor(raw) is None


def test_validate_batch_budget_selects_contract_by_kind():
    ba = batch_scorer.budget_descriptor(60)
    live = ss.live_budget_descriptor(60)
    assert ba["schema"] == "muyun.ba.budget/v1"
    assert ss.validate_batch_budget("ba", ba) == ba
    assert ss.validate_batch_budget("ba", live) is None, "ba 不认 live schema"
    for kind in LIVE_KINDS:
        assert ss.validate_batch_budget(kind, live) == live
        assert ss.validate_batch_budget(kind, ba) is None, "live 不认 ba schema"
    for kind in ("zz", "bz", "", None):
        assert ss.validate_batch_budget(kind, ba) is None
        assert ss.validate_batch_budget(kind, live) is None


def test_batch_budget_stop_reason_by_kind():
    for kind in ["ba"] + LIVE_KINDS:
        assert ss.batch_budget_stop_reason(kind) == "budget_exhausted"
    for kind in ("zz", "bz", "", None):
        assert ss.batch_budget_stop_reason(kind) is None


@pytest.mark.parametrize("kind", LIVE_KINDS)
def test_start_stores_canonical_live_budget_for_live_kinds(kind):
    rid = ss.batch_task_start(kind, "k", ["A"], budget=ss.live_budget_descriptor(60))
    run = _latest()
    assert run["run_id"] == rid
    assert run["budget"] == {"schema": "muyun.live.budget/v1",
                             "policy": "stop_starting_new_stock", "budget_seconds": 60.0}


@pytest.mark.parametrize("kind", LIVE_KINDS)
@pytest.mark.parametrize("bad", ["ba_schema", "extra_key", "bool_seconds", "nan_seconds", "not_dict"])
def test_start_does_not_store_bad_or_cross_schema_budget_on_live_kinds(kind, bad):
    desc = {
        "ba_schema": lambda: batch_scorer.budget_descriptor(60),
        "extra_key": lambda: dict(ss.live_budget_descriptor(60), extra=1),
        "bool_seconds": lambda: dict(ss.live_budget_descriptor(60), budget_seconds=True),
        "nan_seconds": lambda: dict(ss.live_budget_descriptor(60), budget_seconds=float("nan")),
        "not_dict": lambda: "muyun.live.budget/v1",
    }[bad]()
    rid = ss.batch_task_start(kind, "k", ["A", "B"], budget=desc)
    assert isinstance(rid, str) and rid, "预算不合法也必须照常登记 run"
    run = _latest()
    assert run["run_id"] == rid and "budget" not in run
    assert run["codes"] == ["A", "B"]


def test_start_ba_kind_keeps_ba_contract_and_rejects_live_descriptor():
    ba = batch_scorer.budget_descriptor(60)
    rid = ss.batch_task_start("ba", "k1", ["A"], budget=ba)
    assert _latest()["run_id"] == rid and _latest()["budget"] == ba
    rid2 = ss.batch_task_start("ba", "k2", ["A"], budget=ss.live_budget_descriptor(60))
    assert isinstance(rid2, str) and rid2
    assert _latest()["run_id"] == rid2 and "budget" not in _latest()


def test_start_unsupported_kind_never_stores_budget_but_creates_run():
    for desc in (batch_scorer.budget_descriptor(60), ss.live_budget_descriptor(60)):
        rid = ss.batch_task_start("zz", "k", ["A"], budget=desc)
        assert isinstance(rid, str) and rid
        assert "budget" not in _latest()


def test_start_without_budget_keeps_legacy_record_shape():
    ss.batch_task_start("l", "k", ["A"])
    run = _latest()
    assert "budget" not in run and "not_started" not in run and "stop_reason" not in run
    assert set(run) >= {"run_id", "kind", "key", "codes", "done", "degraded", "failed"}



# ── 独立复核 P2 回归（code-quality-guard 探针转正）────────────────

def test_review_p2_mark_deep_deeply_nested_file_refused_not_raised():
    """超深嵌套坏文件（RecursionError）→ 拒写返回 False，原文件不动，绝不抛出。"""
    bad = "[" * 200000 + "]" * 200000
    ss._DEEP_ANALYZED_FILE.write_text(bad, encoding="utf-8")
    before = ss._DEEP_ANALYZED_FILE.read_bytes()
    assert ss.mark_deep_analyzed(["600519"], source="x") is False
    assert ss._DEEP_ANALYZED_FILE.read_bytes() == before
    assert ss.get_deep_analyzed() == {}


def test_review_p2_marker_failure_never_aborts_batch(capsys):
    """当日已析写入异常（含被替换函数直接抛错）只提示未保存，后续股票照常分析。"""
    calls = []

    def fake(code, **kw):
        calls.append(code)
        return cli_main.LiveOutcome.COMPLETE

    def raising_mark(codes, source=""):
        raise RecursionError("boom")

    orig = ss.mark_deep_analyzed
    ss.mark_deep_analyzed = raising_mark
    try:
        res = start._run_live_batch_loop(
            ss, fake, {}, kind="l", key="k",
            entries=[("600519", "a"), ("000001", "b")],
            account_facts=object(), run_id=None, deadline=None,
            deep_source="手动多代码")
    finally:
        ss.mark_deep_analyzed = orig
    assert calls == ["600519", "000001"]
    assert res["complete"] == ["600519", "000001"]
    assert capsys.readouterr().out.count("当日已析记录未保存") == 2
