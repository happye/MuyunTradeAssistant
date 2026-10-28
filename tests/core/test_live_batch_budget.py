# -*- coding: utf-8 -*-
"""T05-L：live 深分析批量(l 多代码 / la / l all)的可选 `--budget` 启动预算。

锁死语义(T05-L 预算部分):
- 解析: `l <2+ 代码> --budget S` 与裸 `600519 000001 --budget S` -> live_multi;
  `l all [-f] --budget S` -> live_scan_all; `la|lall --budget S` -> live_all;
  无预算时返回的 dict 与旧黄金逐项一致(不新增 budget 键)
- 单只(含单个 #N 引用/重复同一代码)带预算 -> 拒绝, 且先于 resolve_index/get_last_scan;
  坏值(缺值/0/负/nan/inf/非数字/重复)在任何状态读取之前拒绝; 预算数字永不当股票代码
- run_cli 门: 非法程序化预算 -> "预算参数无效", 零业务动作; 单只 live 仍 "不支持 --budget"
- 同一 monotonic 截止点: 到期(含恰好等于截止点)不再开始后续股票, 标题打印后到调用前再核一次;
  准备阶段(预取/账户快照)计入预算; l all 在用户确认之后才起算, la 到期早于账户快照则
  不读快照、不登记 run、不声称"预算未开始"(名单未知)
- 调度器确认从未开始的代码经 batch_task_not_started 登记, 绝不传给 analyze_live
- 无预算: 每个入口打印"可选时限"提示(l all 在 y/N 之前), batch_task_start 保持旧签名,
  不打印"启动预算"; 有预算: batch_task_start 收到合法 live 预算描述
- 实际执行/账本 roster/计数共用保序去重名单
- tasks: live 类预算行用"单只分析"(ba 保持"单只评分"); 预算按 kind 同门校验
- 帮助/横幅列出三条 --budget 示例且不撑破菜单边框

假钟: 用 monkeypatch 替换 start._monotonic; 全程无 sleep、无网络、无 AI, 状态文件落 tmp_path。

跑法: pytest tests/core/test_live_batch_budget.py
"""

import builtins
import json
import os
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
import src.core.benzong.batch_scorer as batch_scorer
import src.data.akshare_client as ak_mod
import src.data.portfolio as portfolio_mod


MODES = ["live_multi", "live_all", "live_scan_all"]
KIND_OF = {"live_multi": "l", "live_all": "la", "live_scan_all": "l all"}
SCAN_SOURCE = "bz scan 测试"
CODES2 = ["600519", "000001"]
CODES3 = ["600001", "600002", "600003"]
LIVE_BUDGET = {"schema": "muyun.live.budget/v1",
               "policy": "stop_starting_new_stock"}
# 全角括号用码点构造(项目约定: Python 字符串里不写全角括号字符)
_LP, _RP = chr(0xFF08), chr(0xFF09)


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch, tmp_path):
    """账本/当日已析/扫描快照全部落 tmp_path: 不碰真实 ~/.muyun。"""
    monkeypatch.setattr(ss, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", tmp_path / "batch_tasks.json")
    monkeypatch.setattr(ss, "_DEEP_ANALYZED_FILE", tmp_path / "deep_analyzed.json")
    monkeypatch.setattr(ss, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(ss, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    monkeypatch.setattr(ss, "_WATCH_FILE", tmp_path / "watchlist.jsonl")


# ── 假钟 ─────────────────────────────────────────────────────

class _Clock:
    """假单调钟: 只在测试显式推进时前进(无 sleep)。"""

    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class _JumpClock(_Clock):
    """首次读取(截止点起算)之后, 所有后续读取都返回远超任何测试预算的时刻。

    用来表达"起算后立刻到期", 不依赖起算与提示打印的先后顺序。
    """

    def __init__(self, t=1000.0):
        super().__init__(t)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.t if self.reads == 1 else self.t + 1e6


def _clock(monkeypatch, cls=_Clock, t=1000.0):
    clock = cls(t)
    monkeypatch.setattr(start, "_monotonic", clock)
    return clock


def _advance_on_print(monkeypatch, needle, clock, seconds):
    """输出里首次出现 needle 就推进假钟: 模拟"标题打印后到真正调用前预算到期"。"""
    fired = []

    def _print(*args, **kwargs):
        if not fired and needle in " ".join(str(a) for a in args):
            fired.append(True)
            clock.advance(seconds)
        builtins.print(*args, **kwargs)

    monkeypatch.setattr(start, "print", _print, raising=False)
    return fired


# ── 离线环境 ─────────────────────────────────────────────────

def _complete():
    return cli_main.LiveOutcome.COMPLETE


def _entry(code):
    return mock.MagicMock(stock_code=code, stock_name="N" + code, from_ledger=False)


def _latest():
    runs = ss.get_recent_batch_tasks(10)
    assert runs, "必须登记过批量任务 run"
    return runs[0]


def _summary(out):
    lines = [ln for ln in out.splitlines() if "批量完成" in ln]
    assert len(lines) == 1, f"批量汇总行应恰有一行: {lines}"
    return lines[0]


class _Env:
    """live 三入口共用的离线环境(替身都在 monkeypatch 下, 结束自动还原)。

    - analyze_live: 记录调用, 缺省返回 COMPLETE; on_call(code) 可推进假钟
    - PortfolioManager: MagicMock; request_account_facts 触发 on_snapshot 后返回同一份 facts
    - prefetch_realtime_quotes: 无网络替身, 触发 on_prefetch
    - l all: get_last_scan 返回 codes 对应 items; builtins.input 记录提示, 触发 on_input 后返回 confirm
      (l 多代码 / la 不得新增确认: 它们一旦调 input 即失败)
    - 账本与当日已析缺省走真实实现(落 tmp_path)
    """

    def __init__(self, monkeypatch, capsys, mode, codes, *, confirm="y"):
        self.mp = monkeypatch
        self.capsys = capsys
        self.mode = mode
        self.codes = list(codes)
        self.confirm = confirm
        self.calls = []
        self.pre = []
        self.prompts = []
        self.buf = ""
        self.on_call = None
        self.on_prefetch = None
        self.on_snapshot = None
        self.on_input = None
        self.facts = mock.MagicMock(name="acct_facts")
        self.facts.holding_entries.return_value = ([_entry(c) for c in self.codes], False)
        self._wire()

    def _wire(self):
        mp = self.mp

        def fake_analyze(code, **kw):
            self.calls.append((code, kw))
            if self.on_call:
                self.on_call(code)
            return _complete()

        mp.setattr(cli_main, "analyze_live", fake_analyze)

        def snapshot(**kw):
            if self.on_snapshot:
                self.on_snapshot()
            return self.facts

        self.pm = mock.MagicMock(name="PortfolioManager")
        self.pm.return_value.request_account_facts.side_effect = snapshot
        mp.setattr(portfolio_mod, "PortfolioManager", self.pm)

        def prefetch(codes):
            if self.on_prefetch:
                self.on_prefetch()
            return len(codes)

        mp.setattr(ak_mod.AKShareClient, "prefetch_realtime_quotes", staticmethod(prefetch))
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

    def args(self, **extra):
        base = {"live_multi": {"codes": list(self.codes)}, "live_all": {},
                "live_scan_all": {"force": False}}[self.mode]
        base.update(extra)
        return base

    def run(self, **extra):
        start.run_cli(self.mode, self.args(**extra))
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


def _forbid_state_reads(monkeypatch):
    """解析层拒绝路径不得读取会话扫描状态(#N 解析/最近扫描/计数)。"""
    calls = []

    def boom(*a, **k):
        calls.append("touched")
        raise AssertionError("拒绝路径不得读取会话扫描状态")

    monkeypatch.setattr(ss, "get_last_scan", boom)
    monkeypatch.setattr(ss, "resolve_index", boom)
    monkeypatch.setattr(ss, "last_scan_count", boom)
    return calls


def _forbid_live_business(monkeypatch):
    """run_cli 拒绝路径不得触碰任何业务: 分析/预取/账户/账本/扫描/确认。"""
    calls = []

    def boom(*a, **k):
        calls.append("touched")
        raise AssertionError("拒绝路径不得触碰业务")

    monkeypatch.setattr(cli_main, "analyze_live", boom)
    monkeypatch.setattr(ak_mod.AKShareClient, "prefetch_realtime_quotes", staticmethod(boom))
    monkeypatch.setattr(portfolio_mod, "PortfolioManager", boom)
    for name in ("batch_task_start", "batch_task_mark", "batch_task_not_started",
                 "get_last_scan", "get_deep_analyzed", "mark_deep_analyzed"):
        monkeypatch.setattr(ss, name, boom)
    monkeypatch.setattr("builtins.input", boom)
    return calls


# ═══════ 1. 解析: 支持的入口与写法 ═══════

MULTI_OK = [
    pytest.param("l 600519,000001 --budget 600", ["600519", "000001"], 600.0, id="comma"),
    pytest.param("l 600519 000001 --budget 600", ["600519", "000001"], 600.0, id="space"),
    pytest.param("l 600519,000001 --budget=600", ["600519", "000001"], 600.0, id="equals"),
    pytest.param("l --budget 600 600519,000001", ["600519", "000001"], 600.0, id="before-codes"),
    pytest.param("live 600519,000001 --budget 90", ["600519", "000001"], 90.0, id="live-alias"),
    pytest.param("l 600519，000001 --budget 600", ["600519", "000001"], 600.0, id="cn-comma"),
    pytest.param("l 600519、000001 --budget=0.5", ["600519", "000001"], 0.5, id="cn-dunhao"),
    pytest.param("600519 000001 --budget 600", ["600519", "000001"], 600.0, id="bare-space"),
    pytest.param("600519 000001 --budget=600", ["600519", "000001"], 600.0, id="bare-equals"),
    pytest.param("l 600519,000001,300750 --budget 45", ["600519", "000001", "300750"], 45.0,
                 id="three-codes"),
]


@pytest.mark.parametrize("text,codes,seconds", MULTI_OK)
def test_parse_multi_code_budget(text, codes, seconds):
    assert start.parse_input(text) == ("live_multi", {"codes": codes, "budget": seconds})


SCAN_ALL_OK = [
    pytest.param("l all --budget 60", False, 60.0, id="plain"),
    pytest.param("l all -f --budget 60", True, 60.0, id="f-then-budget"),
    pytest.param("l all --budget 60 -f", True, 60.0, id="budget-then-f"),
    pytest.param("l all --force --budget=60", True, 60.0, id="force-then-equals"),
    pytest.param("l all --budget=60 --force", True, 60.0, id="equals-then-force"),
    pytest.param("l all --budget=0.5", False, 0.5, id="fraction"),
]


@pytest.mark.parametrize("text,force,seconds", SCAN_ALL_OK)
def test_parse_l_all_budget_any_order(text, force, seconds):
    assert start.parse_input(text) == ("live_scan_all", {"force": force, "budget": seconds})


@pytest.mark.parametrize("text,seconds", [
    ("la --budget 600", 600.0), ("lall --budget=600", 600.0), ("la --budget 0.5", 0.5)])
def test_parse_la_budget(text, seconds):
    assert start.parse_input(text) == ("live_all", {"budget": seconds})


@pytest.mark.parametrize("text,expected", [
    pytest.param("l 600519,000001", ("live_multi", {"codes": ["600519", "000001"]}), id="l-multi"),
    pytest.param("l 600519 000001", ("live_multi", {"codes": ["600519", "000001"]}), id="l-space"),
    pytest.param("600519 000001", ("live_multi", {"codes": ["600519", "000001"]}), id="bare-multi"),
    pytest.param("l all", ("live_scan_all", {"force": False}), id="l-all"),
    pytest.param("l all -f", ("live_scan_all", {"force": True}), id="l-all-f"),
    pytest.param("la", ("live_all", {}), id="la"),
    pytest.param("lall", ("live_all", {}), id="lall"),
    pytest.param("l 600519", ("live", {"stock_code": "600519"}), id="single-l"),
    pytest.param("600519", ("live", {"stock_code": "600519"}), id="single-bare"),
])
def test_parse_without_budget_returns_exact_legacy_dicts(text, expected):
    got = start.parse_input(text)
    assert got == expected
    assert "budget" not in got[1], "默认无预算: 不得附加 budget 键"


@pytest.mark.parametrize("text,codes", [
    ("l 600519 000001 --budget 600000", ["600519", "000001"]),
    ("l 600519 000001 --budget=600000", ["600519", "000001"]),
    ("l --budget 600000 600519 000001", ["600519", "000001"]),
    ("600519 000001 --budget 600000", ["600519", "000001"]),
])
def test_budget_value_token_never_becomes_a_stock_code(text, codes):
    """600000 本身是合法 6 位代码形状: 预算值必须先于代码提取被分离。"""
    assert start.parse_input(text) == ("live_multi", {"codes": codes, "budget": 600000.0})


def test_budget_value_that_looks_like_code_on_all_and_la():
    assert start.parse_input("l all --budget 600000") == (
        "live_scan_all", {"force": False, "budget": 600000.0})
    assert start.parse_input("la --budget 600000") == ("live_all", {"budget": 600000.0})


@pytest.mark.parametrize("text", [
    "l 600519 --budget 600000",
    "l 600519 --budget=600000",
    "600519 --budget 600000",
    "live 600519 --budget 000001",
])
def test_budget_value_is_not_miscounted_as_a_second_code(text, capsys):
    """单只 + 形如代码的预算值: 不得被误当成两只而放行成多代码批。"""
    assert start.parse_input(text) is None
    out = capsys.readouterr().out
    assert "--budget" in out and "不支持" in out


# ═══════ 2. 解析: 单只带预算先拒绝(不读扫描状态)、坏值先拒绝 ═══════

SINGLE_WITH_BUDGET = [
    "l 600519 --budget 60",
    "live 600519 --budget 60",
    "600519 --budget 60",
    "l #3 --budget 60",
    "l 600519,600519 --budget 60",
    "l 600519 --budget=60",
    "l --budget 60 600519",
    "l 600519，600519 --budget 60",
]


@pytest.mark.parametrize("text", SINGLE_WITH_BUDGET)
def test_single_stock_with_budget_rejected_before_any_state_read(text, monkeypatch, capsys):
    calls = _forbid_state_reads(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == [], "单只/单个 #N 必须在 resolve_index 之前拒绝"
    out = capsys.readouterr().out
    assert "--budget" in out and "不支持" in out, out


BAD_SUFFIXES = ["--budget", "--budget=", "--budget abc", "--budget 0", "--budget -1",
                "--budget nan", "--budget inf", "--budget 1e999",
                "--budget 60 --budget 90", "--budget=60 --budget=90"]
LIVE_PREFIXES = ["l 600519,000001", "600519 000001", "l all", "l all -f", "la", "lall"]


@pytest.mark.parametrize("suffix", BAD_SUFFIXES)
@pytest.mark.parametrize("prefix", LIVE_PREFIXES)
def test_bad_budget_value_rejected_before_any_state_read(prefix, suffix, monkeypatch, capsys):
    calls = _forbid_state_reads(monkeypatch)
    assert start.parse_input(f"{prefix} {suffix}") is None
    assert calls == []
    out = capsys.readouterr().out
    assert "--budget" in out and ("无效" in out or "缺少" in out), out


@pytest.mark.parametrize("text", [
    "l #1,#2 --budget abc",
    "l #1,#2 --budget",
    "l #1,#2 --budget=0",
    "l #1 #2 --budget nan",
    "l #3 --budget abc",
    "l #1,#2 --budget 60 --budget 90",
])
def test_bad_budget_with_index_refs_rejected_before_resolving_them(text, monkeypatch, capsys):
    calls = _forbid_state_reads(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == [], "坏预算必须先于 #N 解析拒绝, 不能先读扫描状态"
    out = capsys.readouterr().out
    assert "--budget" in out and ("无效" in out or "缺少" in out), out


@pytest.mark.parametrize("text", [
    "l 600519,000001 --budgetx 60",
    "la --budgetx",
    "l all --budgets=60",
    "600519 000001 --budget-any 60",
])
def test_unknown_budget_shapes_still_rejected(text, monkeypatch, capsys):
    calls = _forbid_state_reads(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    assert "未知预算" in capsys.readouterr().out


@pytest.mark.parametrize("text", [
    "scan market --budget 60",
    "pos list --budget 60",
    "today --budget 60",
    "tasks --budget 60",
    "doctor --budget 60",
    "b 600519 --budget 60",
    "bz 600519 --budget 60",
    "q --budget 60",
])
def test_budget_still_rejected_on_unsupported_commands(text, monkeypatch, capsys):
    """live 批开放预算后, 其余命令仍必须显式拒绝(不是静默忽略)。"""
    calls = _forbid_state_reads(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    out = capsys.readouterr().out
    assert "--budget" in out and "不支持" in out, out


# ═══════ 3. run_cli 门: 程序化坏预算零业务动作 ═══════

@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"), pytest.param(True, id="bool"),
])
@pytest.mark.parametrize("mode,args", [
    pytest.param("live_multi", {"codes": ["600519", "000001"]}, id="multi"),
    pytest.param("live_all", {}, id="la"),
    pytest.param("live_scan_all", {"force": False}, id="l-all"),
])
def test_run_cli_rejects_invalid_programmatic_budget_before_any_business(
        mode, args, bad, monkeypatch, capsys):
    calls = _forbid_live_business(monkeypatch)
    start.run_cli(mode, dict(args, budget=bad))
    assert calls == [], "无 analyze/预取/账户/账本/扫描读取/确认"
    assert "预算参数无效" in capsys.readouterr().out


def test_run_cli_single_live_still_rejects_budget(monkeypatch, capsys):
    calls = _forbid_live_business(monkeypatch)
    start.run_cli("live", {"stock_code": "600519", "budget": 60})
    assert calls == []
    assert "不支持 --budget" in capsys.readouterr().out


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("budget", [60, 0.5, 60.0], ids=["int", "fraction", "float"])
def test_run_cli_accepts_valid_programmatic_budget(mode, budget, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    out = env.run(budget=budget)
    assert [c for c, _ in env.calls] == CODES2
    assert "预算参数无效" not in out and "不支持 --budget" not in out


@pytest.mark.parametrize("mode", MODES)
def test_run_cli_none_budget_is_plain_no_budget(mode, monkeypatch, capsys):
    env = _Env(monkeypatch, capsys, mode, CODES2)
    seen = env.spy_ledger(with_budget=False)
    out = env.run(budget=None)
    assert [c for c, _ in env.calls] == CODES2
    assert "启动预算" not in out
    assert seen["start"][0][3] is None


# ═══════ 4. 截止点语义: 到期不再开始新股票 ═══════

@pytest.mark.parametrize("mode", MODES)
def test_expiry_stops_starting_new_stock_and_registers_the_rest(mode, monkeypatch, capsys):
    """预算 50, 每只耗时 30: 第 1/2 只在截止前开始(第 2 只会跑超), 第 3 只不开始。"""
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    env.on_call = lambda code: clock.advance(30.0)
    out = env.run(budget=50)

    assert [c for c, _ in env.calls] == CODES3[:2], "未开始的股票绝不传给 analyze_live"
    run = _latest()
    assert run["kind"] == KIND_OF[mode]
    assert run["codes"] == CODES3
    assert set(run["done"]) == set(CODES3[:2])
    assert run["not_started"] == {CODES3[2]: "budget_exhausted"}
    assert run["stop_reason"] == "budget_exhausted"
    assert run["budget"] == dict(LIVE_BUDGET, budget_seconds=50.0)
    assert "预算到期" in out and "预算未开始" in out
    line = _summary(out)
    assert line.strip().startswith("批量完成：2/3 成功")
    assert "预算未开始 1" in line


@pytest.mark.parametrize("mode", MODES)
def test_expiry_registers_not_started_through_ledger_api_with_run_token(mode, monkeypatch, capsys):
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    seen = env.spy_ledger(run_id="RUN-NS", with_budget=True)
    env.on_call = lambda code: clock.advance(30.0)
    env.run(budget=50)

    assert [c for c, _ in env.calls] == CODES3[:2]
    assert seen["ns"] == [(KIND_OF[mode], seen["start"][0][1], CODES3[2], "RUN-NS",
                           "budget_exhausted")]
    assert [m["code"] for m in seen["mark"]] == CODES3[:2]


@pytest.mark.parametrize("mode", MODES)
def test_exact_deadline_boundary_does_not_start_next_stock(mode, monkeypatch, capsys):
    """t == deadline 即到期: 不得再开始下一只。"""
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    env.on_call = lambda code: clock.advance(30.0)
    out = env.run(budget=30)

    assert [c for c, _ in env.calls] == CODES3[:1]
    assert _latest()["not_started"] == {c: "budget_exhausted" for c in CODES3[1:]}
    assert "预算未开始 2" in _summary(out)


@pytest.mark.parametrize("mode", MODES)
def test_just_before_deadline_still_starts_next_stock(mode, monkeypatch, capsys):
    """比截止点早一点点仍开始(与恰好等于相对照, 锁死是 >= 而不是 >)。"""
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    env.on_call = lambda code: clock.advance(29.5)
    env.run(budget=30)
    assert [c for c, _ in env.calls] == CODES2


@pytest.mark.parametrize("mode", MODES)
def test_deadline_is_rechecked_after_header_before_calling_analyze(mode, monkeypatch, capsys):
    """第 2 只的标题打印期间预算到期: 标题已输出, 但不得调用 analyze_live。"""
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    fired = _advance_on_print(monkeypatch, "[2/3]", clock, 1000.0)
    out = env.run(budget=100)

    assert fired, "第 2 只的标题必须被打印(否则用例没有覆盖标题后的二次检查)"
    assert "[2/3]" in out
    assert [c for c, _ in env.calls] == CODES3[:1], "标题后二次检查必须拦下第 2 只"
    run = _latest()
    assert set(run["done"]) == {CODES3[0]}
    assert run["not_started"] == {c: "budget_exhausted" for c in CODES3[1:]}
    assert "预算未开始 2" in _summary(out)


@pytest.mark.parametrize("mode", MODES)
def test_expiry_after_the_last_stock_registers_nothing(mode, monkeypatch, capsys):
    """最后一只跑完才超时: 没有任何股票"未开始", 不登记、不输出预算未开始。"""
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    env.on_call = lambda code: clock.advance(20.0)
    out = env.run(budget=30)

    assert [c for c, _ in env.calls] == CODES2
    run = _latest()
    assert set(run["done"]) == set(CODES2)
    assert "not_started" not in run and "stop_reason" not in run, "没有确认未开始的项: 不登记"
    line = _summary(out)
    assert "批量完成：2/2 成功" in line
    assert "预算未开始" not in line


PREP_HOOKS = [
    pytest.param("live_multi", "on_prefetch", id="multi-prefetch"),
    pytest.param("live_multi", "on_snapshot", id="multi-snapshot"),
    pytest.param("live_all", "on_snapshot", id="la-snapshot"),
    pytest.param("live_scan_all", "on_snapshot", id="lall-snapshot"),
]


@pytest.mark.parametrize("mode,hook", PREP_HOOKS)
def test_expiry_during_preparation_registers_whole_roster_as_not_started(
        mode, hook, monkeypatch, capsys):
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    setattr(env, hook, lambda: clock.advance(1000.0))
    out = env.run(budget=60)

    assert env.calls == [], "准备阶段已到期: 一只也不得开始"
    run = _latest()
    assert run["kind"] == KIND_OF[mode] and run["codes"] == CODES3
    assert run["not_started"] == {c: "budget_exhausted" for c in CODES3}
    assert run["stop_reason"] == "budget_exhausted"
    assert run["done"] == {} and run["degraded"] == {} and run["failed"] == {}
    assert "预算到期" in out and "预算未开始" in out
    line = _summary(out)
    assert line.strip().startswith("批量完成：0/3 成功") and "预算未开始 3" in line


@pytest.mark.parametrize("mode,hook", PREP_HOOKS)
def test_preparation_time_counts_toward_the_budget(mode, hook, monkeypatch, capsys):
    """预取/账户快照耗时计入预算: 起算点在准备之前(l all 在确认之后、准备之前)。

    预算 100, 准备耗 60, 每只 30: 第 1/2 只开始(60->90->120), 第 3 只不开始。
    若起算点错放在准备之后, 截止点会晚 60 秒, 第 3 只会被开始 -> 本用例红灯。
    """
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    setattr(env, hook, lambda: clock.advance(60.0))
    env.on_call = lambda code: clock.advance(30.0)
    env.run(budget=100)

    assert [c for c, _ in env.calls] == CODES3[:2]
    assert _latest()["not_started"] == {CODES3[2]: "budget_exhausted"}


def test_la_expired_before_snapshot_reads_nothing_and_claims_nothing(monkeypatch, capsys):
    """la 起算后立刻到期(早于账户快照): 不读快照、不登记 run、不声称"预算未开始"。"""
    _clock(monkeypatch, _JumpClock)
    env = _Env(monkeypatch, capsys, "live_all", CODES3)
    seen = env.spy_ledger(with_budget=True)
    out = env.run(budget=60)

    env.pm.return_value.request_account_facts.assert_not_called()
    assert seen["start"] == [] and seen["mark"] == [] and seen["ns"] == []
    assert env.calls == []
    assert "预算到期" in out
    assert "预算未开始" not in out, "持仓名单未知: 不得声称任何股票预算未开始"
    assert ss.get_recent_batch_tasks(5) == []


def test_la_budget_notice_print_then_expiry_reads_no_snapshot(monkeypatch, capsys):
    """卡片机制版: 预算提示一打印假钟就越过截止点(前提: 截止点在提示之前已起算)。"""
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, "live_all", CODES3)
    seen = env.spy_ledger(with_budget=True)
    fired = _advance_on_print(monkeypatch, "启动预算：", clock, 1000.0)
    out = env.run(budget=60)

    assert fired, "必须打印过含启动预算的提示"
    env.pm.return_value.request_account_facts.assert_not_called()
    assert seen["start"] == [] and seen["ns"] == []
    assert env.calls == []
    assert "预算到期" in out
    assert "预算未开始" not in out


def test_l_all_deadline_starts_after_confirmation(monkeypatch, capsys):
    """用户在 y/N 提示前想了 1000 秒: 不得吃掉 15 秒预算(起算点在确认之后)。"""
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, "live_scan_all", CODES2)
    env.on_input = lambda: clock.advance(1000.0)
    env.on_call = lambda code: clock.advance(1.0)
    out = env.run(budget=15)

    assert [c for c, _ in env.calls] == CODES2
    assert "not_started" not in _latest()
    line = _summary(out)
    assert "批量完成：2/2 成功" in line and "预算未开始" not in line
    assert len(env.prompts) == 1


def test_l_all_declining_confirmation_runs_nothing_and_registers_nothing(monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, "live_scan_all", CODES2, confirm="n")
    out = env.run(budget=15)
    assert env.calls == []
    assert "已取消" in out
    assert ss.get_recent_batch_tasks(5) == [], "取消后不得登记任务"


@pytest.mark.parametrize("mode", MODES)
def test_no_budget_means_no_hidden_time_limit(mode, monkeypatch, capsys):
    """默认无隐藏上限: 即使每只耗时极长(假钟推进 1e9 秒), 三只也全部开始。"""
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    env.on_call = lambda code: clock.advance(1e9)
    out = env.run()
    assert [c for c, _ in env.calls] == CODES3
    assert "启动预算" not in out and "预算到期" not in out
    run = _latest()
    assert "budget" not in run and "not_started" not in run


# ═══════ 5. 启动预算提示 / 无预算提示 / 账本接线 ═══════

@pytest.mark.parametrize("mode", MODES)
def test_budget_notice_printed_before_first_stock(mode, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    out = env.run(budget=60)
    assert "启动预算：" in out
    assert out.index("启动预算：") < out.index("[1/2]")


def test_l_all_budget_notice_is_before_the_confirmation_prompt(monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, "live_scan_all", CODES2)
    env.run(budget=60)
    assert len(env.pre) == 1
    assert "启动预算：" in env.pre[0], "l all 的预算提示必须在 y/N 确认之前输出"


@pytest.mark.parametrize("mode", MODES)
def test_no_budget_prints_optional_hint_and_keeps_legacy_start_signature(mode, monkeypatch, capsys):
    env = _Env(monkeypatch, capsys, mode, CODES2)
    seen = env.spy_ledger(with_budget=False)   # 旧签名: 传 budget kwarg 会 TypeError
    out = env.run()

    hint_scope = env.pre[0] if mode == "live_scan_all" else out
    assert "可选时限" in hint_scope and "--budget" in hint_scope, \
        "无预算也要主动提示可选时限(l all 必须在 y/N 之前)"
    assert "启动预算" not in out
    assert [c for c, _ in env.calls] == CODES2
    assert seen["start"][0][3] is None
    assert seen["ns"] == []


@pytest.mark.parametrize("mode", MODES)
def test_budget_run_hands_valid_live_descriptor_to_ledger_start(mode, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    seen = env.spy_ledger(with_budget=True)
    env.run(budget=60)

    kind, key, codes, budget = seen["start"][0]
    assert kind == KIND_OF[mode] and codes == CODES2
    assert budget == dict(LIVE_BUDGET, budget_seconds=60.0)
    assert ss.validate_live_budget_descriptor(budget) == budget


@pytest.mark.parametrize("text,mode,codes", [
    pytest.param("l 600001,600002 --budget 600", "live_multi", ["600001", "600002"], id="multi"),
    pytest.param("la --budget 600", "live_all", ["600001", "600002"], id="la"),
    pytest.param("l all --budget 600", "live_scan_all", ["600001", "600002"], id="l-all"),
])
def test_parse_then_run_cli_chain_stores_budget_in_real_ledger(
        text, mode, codes, monkeypatch, capsys):
    """接线全链: parse_input -> run_cli -> 真实账本里的 live 预算描述。"""
    _clock(monkeypatch)
    parsed = start.parse_input(text)
    assert parsed is not None and parsed[0] == mode
    env = _Env(monkeypatch, capsys, mode, codes)
    start.run_cli(*parsed)
    out = env.out()
    assert [c for c, _ in env.calls] == codes
    run = _latest()
    assert run["kind"] == KIND_OF[mode]
    assert run["budget"] == dict(LIVE_BUDGET, budget_seconds=600.0)
    assert "启动预算：" in out


# ═══════ 6. 去重: 执行/账本 roster/计数共用保序去重名单 ═══════

def test_duplicate_codes_run_once_and_roster_is_deduped(monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, "live_multi", CODES2)
    seen = env.spy_ledger(with_budget=True)
    out = env.run(codes=["600519", "600519", "000001"], budget=60)

    assert [c for c, _ in env.calls] == ["600519", "000001"], "同一代码只分析一次"
    assert seen["start"][0][2] == ["600519", "000001"], "账本 roster 保序去重"
    assert [m["code"] for m in seen["mark"]] == ["600519", "000001"]
    assert "[1/2]" in out and "[2/2]" in out and "[3/" not in out
    assert "批量完成：2/2 成功" in out


def test_duplicate_codes_without_budget_also_deduped(monkeypatch, capsys):
    env = _Env(monkeypatch, capsys, "live_multi", CODES2)
    seen = env.spy_ledger(with_budget=False)
    out = env.run(codes=["000001", "600519", "000001", "600519"])
    assert [c for c, _ in env.calls] == ["000001", "600519"]
    assert seen["start"][0][2] == ["000001", "600519"]
    assert "批量完成：2/2 成功" in out


# ═══════ 7. tasks 展示: live 类预算行 / 联合门 / ba 保持评分措辞 ═══════

def _render_tasks(capsys):
    cli_main.batch_tasks_cmd()
    out = capsys.readouterr().out
    return out, out.replace("\n", "").replace(" ", "")   # rich 按终端宽度折行, 断言前拉平


def _edit_latest(fn):
    path = ss._BATCH_TASKS_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data["tasks"][0])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


BUDGET_LINE_60 = "预算:60秒" + _LP + "策略：到期不再开始新股票" + _RP


@pytest.mark.parametrize("kind", ["l", "la", "l all"])
def test_tasks_live_budget_lines_use_analysis_wording_and_confirm_not_started(kind, capsys):
    key = "手动 A,B,C"
    rid = ss.batch_task_start(kind, key, ["A", "B", "C"], budget=ss.live_budget_descriptor(60))
    assert rid
    ss.batch_task_mark(kind, key, "A", True, run_id=rid)
    ss.batch_task_mark(kind, key, "B", False, "数据获取失败", run_id=rid)
    assert ss.batch_task_not_started(kind, key, "C", run_id=rid,
                                     reason="budget_exhausted") is True
    out, flat = _render_tasks(capsys)

    assert BUDGET_LINE_60 in flat
    assert "单只分析" in flat and "单只评分" not in flat
    assert "预算未开始1" in flat
    assert "已确认1只未开始" in flat
    assert "已完成1/3" in flat and "失败1" in flat


@pytest.mark.parametrize("kind", ["l", "la", "l all"])
def test_tasks_live_record_with_ba_schema_budget_is_not_trusted(kind, capsys):
    """live 记录里带 ba schema 预算: 描述校验未通过, 未开始项隔离待确认(不计入、不确认)。"""
    rid = ss.batch_task_start(kind, "k", ["A", "B"])
    assert rid

    def _tamper(rec):
        rec["budget"] = batch_scorer.budget_descriptor(60)
        rec["stop_reason"] = "budget_exhausted"
        rec["not_started"] = {"B": "budget_exhausted"}

    _edit_latest(_tamper)
    before = ss._BATCH_TASKS_FILE.read_bytes()
    out, flat = _render_tasks(capsys)

    assert "描述校验未通过" in flat
    assert "预算未开始1" not in flat, "跨 schema 的未开始项不得计入"
    assert "已确认1只未开始" not in flat
    assert "待确认" in flat
    assert BUDGET_LINE_60 not in flat, "不得渲染未通过校验的预算秒数"
    assert ss._BATCH_TASKS_FILE.read_bytes() == before, "tasks 只读展示不得改账本"


@pytest.mark.parametrize("kind", ["l", "la", "l all"])
def test_tasks_live_not_started_without_stop_reason_is_isolated(kind, capsys):
    """读门联核: live 预算合法但缺 run 级 stop_reason -> 未开始项待确认, 不计数。"""
    rid = ss.batch_task_start(kind, "k", ["A", "B"], budget=ss.live_budget_descriptor(60))
    _edit_latest(lambda rec: rec.__setitem__("not_started", {"B": "budget_exhausted"}))
    out, flat = _render_tasks(capsys)
    assert "预算未开始1" not in flat
    assert "待确认" in flat


@pytest.mark.parametrize("kind", ["l", "la", "l all"])
def test_tasks_live_record_without_budget_shows_unrecorded(kind, capsys):
    ss.batch_task_start(kind, "k", ["A"])
    out, flat = _render_tasks(capsys)
    assert "预算:未记录" in flat
    assert "单只评分" not in flat


def test_tasks_ba_budget_lines_keep_scoring_wording(capsys):
    ss.batch_task_start("ba", "k", ["A", "B"], budget=batch_scorer.budget_descriptor(60))
    out, flat = _render_tasks(capsys)
    assert BUDGET_LINE_60 in flat
    assert "单只评分" in flat and "单只分析" not in flat


def test_tasks_ba_record_with_live_schema_budget_is_not_trusted(capsys):
    ss.batch_task_start("ba", "k", ["A", "B"])
    _edit_latest(lambda rec: rec.__setitem__("budget", ss.live_budget_descriptor(60)))
    out, flat = _render_tasks(capsys)
    assert "描述校验未通过" in flat
    assert BUDGET_LINE_60 not in flat


# ═══════ 8. 帮助 / 横幅可发现性 ═══════

HELP_ROWS = ["l 600519,000001 --budget 600", "la --budget 600", "l all [-f] --budget 600"]


def test_help_lists_live_budget_rows_within_border(capsys):
    start.show_help()
    out = capsys.readouterr().out
    for row in HELP_ROWS:
        assert row in out, f"帮助菜单缺少: {row}"
    assert "bz scan --budget 600 --limit 15" in out, "T05-N 的 bz scan 预算示例缺失"
    budget_lines = [ln for ln in out.splitlines() if "--budget" in ln]
    assert len(budget_lines) >= len(HELP_ROWS) + 2, "ba / bz 多代码示例仍在, live 三行为新增"
    for line in budget_lines:
        assert start._display_width(line) <= 74, \
            f"帮助行撑破边框: {line!r} width={start._display_width(line)}"


def test_help_does_not_advertise_budget_on_single_stock_or_scan(capsys):
    start.show_help()
    out = capsys.readouterr().out
    # 顶层 scan / l 单只 / bz 单只仍不支持；bz scan 自 T05-N 起支持，故从本表移除
    for bad in ("l 600519 --budget", "l <代码> --budget", "bz 600519 --budget"):
        assert bad not in out, f"帮助不得宣传不支持入口: {bad}"


def test_banner_shows_live_budget_rows(capsys):
    start.show_banner()
    out = capsys.readouterr().out
    assert "暮云思辨投资助手" in out
    for row in HELP_ROWS:
        assert row in out, f"横幅(帮助)缺少: {row}"



# ── 独立复核 P2 回归（code-quality-guard 探针转正）────────────────

@pytest.mark.parametrize("text", [
    "l #1 foo --budget 60",
    "l #1,foo --budget 60",
    "#1 --budget 60",
])
def test_review_p2_single_ref_plus_junk_rejected_before_scan_read(text, monkeypatch, capsys):
    """无法识别的片段不算代码：单个 #N + 垃圾片段仍按单只在读扫描状态之前拒绝。"""
    calls = _forbid_state_reads(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []


@pytest.mark.parametrize("text", [
    "l 600519,000001,--budget=60",
    "l 600519,--budget,60 000001",
    "600519,000001,--budget=60",
    "bz 600519,000001,--budget=60",
    "la,--budget=60",
])
def test_review_p2_comma_glued_budget_rejected_not_dropped(text, monkeypatch, capsys):
    """逗号粘连的预算选项不得被当 flag 静默丢弃变成无预算批：显式拒绝。

    粘在参数里 → “未知预算选项”；粘在命令词本身 → “无法识别”；两者都点名原 token。
    """
    calls = _forbid_state_reads(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    assert "--budget" in capsys.readouterr().out
