# -*- coding: utf-8 -*-
"""T05-O：`scan market deep --budget` 阶段预算、deep 未开始与候选保存事实。

锁死语义（合同 O0–O10，DSH 唯一产品测试 owner 的定向用例）：
- 入口只支持 REPL `scan market deep [规则/主题] --budget 600`（含 s/m 别名、
  `--budget=600`）与独立 CLI `--scan ... --scan-deep --budget 600`；普通交互
  `scan market`、`scan`（持仓组合）、其他 CLI 入口带预算在业务前拒绝。
- 程序化 `scan_market(..., budget_seconds=None)` 同门：只接受有限正数；bool/
  非数/nan/inf/<=0 与“非 deep”在配置/账户/引擎/扫描前拒绝；无预算保持旧 kwargs
  形状且不取 clock。
- 一个 monotonic 绝对截止从 `scan_market` 入口（预算验证 import 之前）起算；
  导入/配置/账户/引擎/规则/大盘查询/缓存预览/初筛/必要主题兜底共用同一截止；
  到期只阻止开始下一阶段/下一只，已开始的阶段/股票允许完成。
- 最后必需候选调用正常返回（即使跨截止）时，完整非空候选可按原语义保存；仍需
  主题兜底却到期时不启动兜底、不落盘部分名单；空/异常/结构未知不覆盖上次有效扫描。
- 候选保存消费真实 `ScanSaveResult(snapshot, history, timestamp)` 与报告空串，
  三项独立；只有快照成功才给本批 #N/l all/ba 指引。
- `ScannerEngine.deep_analyze(..., *, deadline=None)`：非法 deadline 业务前固定
  原因拒绝；过去有限时点视为已到期；构造编排器/账户准备后、每股循环前、progress
  callback 后取数前核同一绝对截止；该股取数已开始则整股旧路径完成；未开始的剩余
  股票只由调度器标 `not_started=True`/`stop_reason="budget_exhausted"`。
- 排名只收已处理结果（排除未开始项后再附回，不改排名算法）；展示“分析返回成功/
  未取得完整分析结果/预算未开始”，全未开始不称全失败，部分未开始不计失败/零分。
- banner/help 列 `scan market deep --budget 600`；默认无预算自动 deep 在首次准备前
  主动提示。

替身边界：ScannerEngine / load_config / PortfolioManager / AKShareClient /
save_last_scan / persist_scan_report / RankingLayer 全部模块替身；时间用假钟
（替换全局 `time.monotonic`，scan_market 与 scanner_engine 共用）。真实
`parse_input` / `run_cli` / `scan_market` / `deep_analyze` / 展示函数 / `show_help`
原函数被驱动。全程零网络、零子进程、零真实账户/知识库访问。

跑法（必须经项目未修改的完整 guard）：
  .venv\\Scripts\\python.exe -B -X utf8 plan/fusion/iteration9/review_test_runner.py \\
      tests/core/test_scan_market_budget.py
"""

import builtins
import copy
import os
import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import start
import src.cli.main as cli_main
import src.cli.session_state as session_state
import src.core.ranking_layer as ranking_mod
import src.data.akshare_client as akshare_mod
import src.data.portfolio as portfolio_mod
import src.scanner.scanner_engine as scanner_engine

CONFIG = {
    "scanner": {"auto_exclude_holdings": False, "rules_path": "rules.yaml",
                "cache_ttl": 300},
    "skills": {"dir": "skills", "enabled": [], "types": {}},
    "decision": {"signal_weights": {}},
    "entry_exit": None,
    "ai": None,
    "ranking": {},
}

BUDGET_STOP_REASON = "budget_exhausted"


# ── 假钟 ─────────────────────────────────────────────────────

class Clock:
    def __init__(self, t: float = 1000.0):
        self.now = float(t)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class AutoClock(Clock):
    """每次取时都前进：构造“校验/import 耗尽到 deadline”的阶段边界。"""

    def __init__(self, t: float = 1000.0, step: float = 600.0):
        super().__init__(t)
        self.step = float(step)

    def __call__(self) -> float:
        self.now += self.step
        return self.now


# ── 候选/扫描引擎替身（scan_market 驱动）─────────────────────

class Candidate:
    def __init__(self, code: str):
        self.stock_code = code
        self.stock_name = f"{code}名"
        self.price = 10.0
        self.change_pct = 1.0
        self.turnover_rate = 1.0
        self.volume_ratio = 1.0
        self.amplitude = 2.0
        self.amount = 1e8


class FakeCache:
    def get_cache_status(self):
        return {"stocks": {"cached": False, "expired": False, "count": 0,
                           "age_seconds": 0}}


class FakeEngine:
    instances = []
    quick_calls = []
    deep_calls = []
    resolve_calls = []
    on_init = None
    on_resolve = None
    on_quick = None
    quick_result = ([], {"total_stocks": 5000, "after_exclude": 4000,
                         "elapsed_seconds": 0.1})
    deep_result = []
    quick_exc = None

    @classmethod
    def reset(cls):
        cls.instances = []
        cls.quick_calls = []
        cls.deep_calls = []
        cls.resolve_calls = []
        cls.on_init = None
        cls.on_resolve = None
        cls.on_quick = None
        cls.quick_result = ([Candidate("600001")],
                            {"total_stocks": 5000, "after_exclude": 4000,
                             "elapsed_seconds": 0.1})
        cls.deep_result = []
        cls.quick_exc = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.market_cache = FakeCache()
        FakeEngine.instances.append(self)
        if FakeEngine.on_init is not None:
            FakeEngine.on_init(self)

    def resolve_rule_name(self, name):
        FakeEngine.resolve_calls.append(name)
        if FakeEngine.on_resolve is not None:
            FakeEngine.on_resolve(name)
        return "healthy_pullback" if name in ("healthy_pullback", "健康回调",
                                             "缩量", "AI", "AI漫剧") else None

    def get_available_rules(self):
        return [{"name": "healthy_pullback", "display_name": "健康回调",
                 "description": "x"}]

    def quick_scan(self, rule_name=None, market_query=None, exclude_codes=None):
        FakeEngine.quick_calls.append({
            "rule": rule_name, "query": market_query,
            "exclude": set(exclude_codes or ()),
        })
        if FakeEngine.on_quick is not None:
            FakeEngine.on_quick()
        if FakeEngine.quick_exc is not None:
            raise FakeEngine.quick_exc
        result = FakeEngine.quick_result
        return result() if callable(result) else result

    def deep_analyze(self, **kwargs):
        FakeEngine.deep_calls.append(dict(kwargs))
        return list(FakeEngine.deep_result)


# ── 配置/账户/大盘/落盘替身 ──────────────────────────────────

class FakeConfig:
    calls = 0
    value = CONFIG
    hook = None

    @classmethod
    def reset(cls):
        cls.calls = 0
        cls.value = CONFIG
        cls.hook = None


def fake_load_config(config_path=None):
    FakeConfig.calls += 1
    if FakeConfig.hook is not None:
        FakeConfig.hook()
    return copy.deepcopy(FakeConfig.value)


class FakeFacts:
    def __init__(self, codes):
        self._codes = list(codes)

    def holding_entries(self):
        return ([SimpleNamespace(stock_code=c) for c in self._codes], None)


class FakePortfolio:
    calls = []
    requests = []
    hook = None
    on_init = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.requests = []
        cls.hook = None
        cls.on_init = None

    def __init__(self):
        FakePortfolio.calls.append("init")
        if FakePortfolio.on_init is not None:
            FakePortfolio.on_init()

    def request_account_facts(self):
        FakePortfolio.requests.append("request")
        if FakePortfolio.hook is not None:
            FakePortfolio.hook()
        return FakeFacts([])


class FakeAK:
    calls = []
    hook = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.hook = None

    @classmethod
    def _get_index_trend(cls):
        FakeAK.calls.append("query")
        if FakeAK.hook is not None:
            FakeAK.hook()
        return None


class FakeSaves:
    saves = []
    reports = []
    snapshot_ok = True
    history_ok = True
    timestamp = "2026-10-28T00:00:00"
    report_value = "reports/fake-scan.md"
    save_exc = None
    report_exc = None

    @classmethod
    def reset(cls):
        cls.saves = []
        cls.reports = []
        cls.snapshot_ok = True
        cls.history_ok = True
        cls.timestamp = "2026-10-28T00:00:00"
        cls.report_value = "reports/fake-scan.md"
        cls.save_exc = None
        cls.report_exc = None

    def save_last_scan(self, items, source):
        FakeSaves.saves.append((list(items), source))
        if FakeSaves.save_exc is not None:
            raise FakeSaves.save_exc
        from src.cli.session_state import ScanSaveResult
        return ScanSaveResult(FakeSaves.snapshot_ok, FakeSaves.history_ok,
                              FakeSaves.timestamp)

    def persist_scan_report(self, items, source, timestamp=None, columns=None):
        FakeSaves.reports.append((list(items), source, timestamp, bool(columns)))
        if FakeSaves.report_exc is not None:
            raise FakeSaves.report_exc
        return FakeSaves.report_value


class FakeConsole:
    def __init__(self):
        self.lines = []
        self.on_print = None
        self.on_status_enter = None

    def print(self, *args, **kwargs):
        text = " ".join(str(a) for a in args)
        self.lines.append(text)
        if self.on_print is not None:
            self.on_print(text)

    @contextmanager
    def status(self, *args, **kwargs):
        if self.on_status_enter is not None:
            self.on_status_enter()
        yield

    def text(self):
        return "\n".join(self.lines)


# ── 环境 fixture ─────────────────────────────────────────────

class Env:
    def __init__(self, clock, capsys, console):
        self.clock = clock
        self.capsys = capsys
        self.console = console

    def run_market(self, **kwargs):
        cli_main.scan_market(**kwargs)
        return self.console.text()

    def run_cli(self, mode, args):
        start.run_cli(mode, dict(args))
        return self.capsys.readouterr().out

    def output(self):
        return self.console.text()


@pytest.fixture
def env(monkeypatch, capsys):
    clock = Clock()
    FakeEngine.reset()
    FakeConfig.reset()
    FakePortfolio.reset()
    FakeSaves.reset()
    FakeAK.reset()
    console = FakeConsole()

    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(cli_main, "load_config", fake_load_config)
    monkeypatch.setattr(scanner_engine, "ScannerEngine", FakeEngine)
    monkeypatch.setattr(portfolio_mod, "PortfolioManager", FakePortfolio)
    monkeypatch.setattr(akshare_mod, "AKShareClient", FakeAK)
    monkeypatch.setattr(session_state, "save_last_scan", FakeSaves().save_last_scan)
    monkeypatch.setattr(session_state, "persist_scan_report",
                        FakeSaves().persist_scan_report)
    monkeypatch.setattr(cli_main, "console", console)
    return Env(clock, capsys, console)


def _boom(*a, **k):
    raise AssertionError("该路径不得触发业务动作")


def _forbid_market_business(monkeypatch):
    """scan_market 拒绝/到期零业务哨兵。"""
    monkeypatch.setattr(cli_main, "load_config", _boom)
    monkeypatch.setattr(scanner_engine, "ScannerEngine", _boom)
    monkeypatch.setattr(portfolio_mod, "PortfolioManager", _boom)
    monkeypatch.setattr(akshare_mod, "AKShareClient", _boom)
    monkeypatch.setattr(session_state, "save_last_scan", _boom)
    monkeypatch.setattr(session_state, "persist_scan_report", _boom)


def _row_success(code: str) -> dict:
    return {
        "stock_code": code,
        "stock_name": f"{code}名",
        "success": True,
        "decision_result": SimpleNamespace(
            decision=SimpleNamespace(value="HOLD"), score=60.0, stock=None),
        "strategy_decision": SimpleNamespace(
            position_action=SimpleNamespace(value="HOLD"),
            action_semantic="-", lifecycle_after=SimpleNamespace(value="OBSERVING"),
            entry_exit=None),
        "ai_result": None,
        "ranking": None,
    }


def _row_not_started(code: str, **patch) -> dict:
    row = {"stock_code": code, "success": False,
           "not_started": True, "stop_reason": BUDGET_STOP_REASON}
    row.update(patch)
    return row


def _row_incomplete(code: str, error="行情降级", **patch) -> dict:
    row = {"stock_code": code, "success": False, "error": error}
    row.update(patch)
    return row


# ═══════ 1. 解析层：REPL 入口/别名/两种写法/无预算旧形状 ═══════

def test_parse_market_deep_budget_space_and_equals():
    mode, args = start.parse_input("scan market deep --budget 600")
    assert mode == "scan_market"
    assert args == {"rule_name": "healthy_pullback", "market_query": None,
                    "deep": True, "budget": 600.0}
    mode, args = start.parse_input("scan market deep 缩量 --budget=90")
    assert mode == "scan_market"
    assert args["rule_name"] == "缩量" and args["budget"] == 90.0
    assert args["deep"] is True


def test_parse_market_deep_budget_aliases_and_query():
    for text in ("s market deep --budget 600", "scan m deep --budget=600",
                 "s m deep --budget 600"):
        parsed = start.parse_input(text)
        assert parsed is not None, f"别名入口应支持预算: {text}"
        mode, args = parsed
        assert mode == "scan_market" and args["deep"] is True
        assert args["budget"] == 600.0
    mode, args = start.parse_input("scan market deep AI,半导体 --budget 60")
    assert mode == "scan_market"
    assert args["rule_name"] == "AI,半导体" and args["market_query"] is None
    assert args["budget"] == 60.0


def test_parse_market_no_budget_keeps_legacy_dict():
    assert start.parse_input("scan market deep") == (
        "scan_market",
        {"rule_name": "healthy_pullback", "market_query": None, "deep": True},
    )
    assert start.parse_input("scan market") == (
        "scan_market",
        {"rule_name": "healthy_pullback", "market_query": None, "deep": False},
    )
    assert "budget" not in start.parse_input("scan market deep 缩量")[1]


@pytest.mark.parametrize("text", [
    "scan market deep --budget",
    "scan market deep --budget=",
    "scan market deep --budget abc",
    "scan market deep --budget 0",
    "scan market deep --budget -5",
    "scan market deep --budget nan",
    "scan market deep --budget inf",
    "scan market deep --budget 60 --budget 90",
])
def test_parse_market_deep_bad_budget_rejected(text, monkeypatch, capsys):
    monkeypatch.setattr(session_state, "get_last_scan", _boom)
    monkeypatch.setattr(session_state, "resolve_index", _boom)
    assert start.parse_input(text) is None
    out = capsys.readouterr().out
    assert "--budget" in out, out
    assert ("无效" in out or "缺少" in out or "只能给一次" in out), out


@pytest.mark.parametrize("text", [
    "scan market deep,--budget=60",
    "scan market deep --budgetx 60",
    "scan market deep --budgets=60",
])
def test_parse_market_glued_or_unknown_budget_rejected(text, capsys):
    assert start.parse_input(text) is None
    out = capsys.readouterr().out
    assert "未知预算" in out or "不支持 --budget" in out, out


@pytest.mark.parametrize("text", [
    "scan market --budget 60",
    "scan market 缩量 --budget 60",
    "scan review --budget 60",
    "scan review 7 --budget=60",
])
def test_parse_unsupported_entries_keep_rejecting_budget(text, monkeypatch, capsys):
    """普通交互 scan market / scan review 带预算仍先拒（旧负例保留；顶层 scan 持仓扫描自 T05-P 起支持）。"""
    monkeypatch.setattr(session_state, "get_last_scan", _boom)
    monkeypatch.setattr(session_state, "resolve_index", _boom)
    assert start.parse_input(text) is None
    out = capsys.readouterr().out
    assert "不支持 --budget" in out, out


# ═══════ 2. 分发接线：只有显式预算才向 scan_market 传 budget_seconds ═══════

def test_parse_then_run_cli_market_budget_chain(monkeypatch):
    seen = []

    def fake_scan_market(**kwargs):
        seen.append(dict(kwargs))

    monkeypatch.setattr(cli_main, "scan_market", fake_scan_market)
    parsed = start.parse_input("scan market deep 缩量 --budget=600")
    assert parsed is not None
    start.run_cli(*parsed)
    assert seen == [{"rule_name": "缩量", "market_query": None, "ai_debug": False,
                     "deep": True, "ai_enabled": True, "budget_seconds": 600.0}]


def test_run_cli_market_no_budget_keeps_old_kwargs(monkeypatch):
    seen = []

    def fake_scan_market(**kwargs):
        seen.append(dict(kwargs))

    monkeypatch.setattr(cli_main, "scan_market", fake_scan_market)
    start.run_cli("scan_market", {"rule_name": "healthy_pullback",
                                  "market_query": None, "deep": True})
    assert seen == [{"rule_name": "healthy_pullback", "market_query": None,
                     "ai_debug": False, "deep": True, "ai_enabled": True}]
    assert "budget_seconds" not in seen[0]


@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"), pytest.param(True, id="bool"),
])
def test_run_cli_market_bad_programmatic_budget_zero_business(
        bad, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "scan_market", _boom)
    start.run_cli("scan_market", {"rule_name": "healthy_pullback",
                                  "market_query": None, "deep": True,
                                  "budget": bad})
    out = capsys.readouterr().out
    assert "预算参数无效" in out and "未开始扫描" in out


def test_run_cli_market_budget_without_deep_rejected(monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "scan_market", _boom)
    start.run_cli("scan_market", {"rule_name": "healthy_pullback",
                                  "market_query": None, "deep": False,
                                  "budget": 60.0})
    out = capsys.readouterr().out
    assert "不支持 --budget" in out or "仅用于" in out


# ═══════ 3. 程序化 scan_market：同门坏值 / 非 deep 零业务 ═══════

@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"), pytest.param(True, id="bool"),
])
def test_scan_market_programmatic_bad_budget_rejected_before_business(
        bad, monkeypatch, capsys):
    _forbid_market_business(monkeypatch)
    cli_main.scan_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=bad)
    out = capsys.readouterr().out
    assert "预算参数无效" in out and "未开始扫描" in out


def test_scan_market_budget_requires_deep_before_business(monkeypatch, capsys):
    _forbid_market_business(monkeypatch)
    cli_main.scan_market(rule_name="healthy_pullback", deep=False,
                         budget_seconds=600.0)
    out = capsys.readouterr().out
    assert "不支持 --budget" in out and "本次未执行" in out


# F4（review1 P2）：有预算分支只认 deep is True，与 REPL 同门；不得 truthiness 放行。

@pytest.mark.parametrize("deep_value", [1, "false"])
def test_budgeted_scan_market_requires_strict_deep_true(deep_value, env):
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=deep_value,
                         budget_seconds=60.0)
    assert "不支持 --budget" in out and "本次未执行" in out
    assert FakeConfig.calls == 0
    assert FakePortfolio.calls == [] and FakePortfolio.requests == []
    assert FakeEngine.instances == [] and FakeEngine.quick_calls == []
    assert FakeAK.calls == []
    assert FakeSaves.saves == [] and FakeSaves.reports == []
    assert FakeEngine.deep_calls == []


def test_scan_market_no_budget_does_not_start_clock(env, monkeypatch):
    FakeEngine.quick_result = ([Candidate("600001")],
                               {"total_stocks": 5000, "after_exclude": 4000,
                                "elapsed_seconds": 0.1})
    monkeypatch.setattr("builtins.input", lambda prompt: "q")
    monkeypatch.setattr(time, "monotonic", _boom)
    out = env.run_market(rule_name="healthy_pullback", deep=False)
    assert FakeEngine.quick_calls and FakeSaves.saves
    assert "启动预算" not in out
    assert "--budget" not in out and "可选时限" not in out, \
        "现有非自动交互不新增预算计时/选择提示"


# ═══════ 4. 同一绝对截止：入口时点/notice/透传/past=到期 ═══════

def test_budget_entry_clock_before_validation_import_and_no_reset(env, monkeypatch):
    """入口时点必须在 budget_descriptor 验证 import 之前；import 耗尽后不重起钟。"""
    import src.core.benzong.batch_scorer as batch_scorer_mod
    assert callable(batch_scorer_mod.budget_descriptor)
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    real_import = builtins.__import__

    def slow_import(name, globals=None, locals=None, fromlist=(), level=0):
        mod = real_import(name, globals, locals, fromlist, level)
        if name == "src.core.benzong.batch_scorer" and fromlist and \
                "budget_descriptor" in fromlist:
            clock.advance(600.0)
        return mod

    monkeypatch.setattr(builtins, "__import__", slow_import)
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "启动预算：600 秒" in out
    assert FakeConfig.calls == 0, "验证 import 耗尽到原截止后不得加载配置"
    assert FakeEngine.instances == [] and FakeSaves.saves == []
    assert "启动预算已到期" in out and "入口" in out


def test_budget_notice_and_deadline_passthrough(env):
    FakeEngine.quick_result = ([Candidate("600001"), Candidate("600002")],
                               {"total_stocks": 5000, "after_exclude": 4000,
                                "elapsed_seconds": 0.1})
    FakeEngine.deep_result = [_row_success("600001"), _row_success("600002")]
    FakeConfig.hook = lambda: env.clock.advance(200.0)   # 准备耗时但不重起钟
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert FakeEngine.deep_calls, "预算内应启动自动深度分析"
    assert FakeEngine.deep_calls[0]["deadline"] == 1600.0, "同一入口截止原样透传"
    assert "deadline" in FakeEngine.deep_calls[0]
    assert "启动预算：600 秒" in out
    assert "逐只" in out and "当前股票会跑完" in out
    assert "非强制中断" in out and "非费用上限" in out


def test_no_budget_deep_hint_before_config_and_old_deep_kwargs(env):
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    FakeEngine.deep_result = [_row_success("600001")]
    snapshots = []
    FakeConfig.hook = lambda: snapshots.append(env.console.text())
    out = env.run_market(rule_name="healthy_pullback", deep=True)
    assert snapshots, "无预算自动 deep 必须在首次准备（配置加载）前提示"
    assert "--budget 600" in snapshots[0] and "默认不启用" in snapshots[0]
    assert "启动预算：" not in snapshots[0]
    assert FakeEngine.deep_calls
    assert "deadline" not in FakeEngine.deep_calls[0], "无预算不得新增 deadline kwarg"
    assert "启动预算" not in out


def test_entry_validation_expiry_blocks_config(env, monkeypatch):
    clock = AutoClock(step=600.0)
    monkeypatch.setattr(time, "monotonic", clock)
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "启动预算已到期" in out and "入口" in out
    assert FakeConfig.calls == 0 and FakeEngine.instances == []
    assert FakeEngine.quick_calls == [] and FakeSaves.saves == []
    assert FakeEngine.deep_calls == []


def test_config_expiry_blocks_account_and_engine(env):
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    FakeConfig.hook = lambda: env.clock.advance(60.0)
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=60.0)
    assert "启动预算已到期" in out and "配置" in out
    assert FakePortfolio.calls == [] and FakeEngine.instances == []
    assert FakeEngine.quick_calls == [] and FakeSaves.saves == []


def test_account_prep_expiry_blocks_engine(env):
    FakeConfig.value = dict(copy.deepcopy(CONFIG),
                            scanner={"auto_exclude_holdings": True,
                                     "rules_path": "rules.yaml", "cache_ttl": 300})
    FakePortfolio.hook = lambda: env.clock.advance(60.0)
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=60.0)
    assert "启动预算已到期" in out and "账户" in out
    assert FakeEngine.instances == [] and FakeEngine.quick_calls == []
    assert FakeSaves.saves == []


def test_engine_construct_expiry_blocks_rule_resolve(env):
    FakeEngine.on_init = lambda engine: env.clock.advance(600.0)
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "启动预算已到期" in out and "引擎" in out
    assert FakeEngine.resolve_calls == [] and FakeEngine.quick_calls == []
    assert FakeSaves.saves == []


def test_rule_resolve_expiry_blocks_next_resolve_and_scan(env):
    FakeEngine.on_resolve = lambda name: env.clock.advance(600.0)
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "启动预算已到期" in out and "规则" in out
    assert len(FakeEngine.resolve_calls) == 1, "第一次规则解析后到期不得再解析"
    assert FakeEngine.quick_calls == [] and FakeSaves.saves == []
    assert FakeEngine.deep_calls == []


def test_index_query_expiry_blocks_quick_scan(env):
    FakeAK.hook = lambda: env.clock.advance(600.0)
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "启动预算已到期" in out and "大盘" in out
    assert FakeEngine.quick_calls == [] and FakeSaves.saves == []


def test_slow_prep_output_expiry_blocks_quick_scan(env):
    def slow_cache_line(text):
        if "行情缓存" in text:
            env.clock.advance(600.0)

    env.console.on_print = slow_cache_line
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "启动预算已到期" in out and "初筛" in out
    assert FakeEngine.quick_calls == [] and FakeSaves.saves == []


def test_slow_scan_header_output_expiry_blocks_index_and_scan(env):
    def slow_header(text):
        if "全市场扫描" in text:
            env.clock.advance(600.0)

    env.console.on_print = slow_header
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "启动预算已到期" in out and "规则展示后" in out
    assert FakeEngine.quick_calls == [] and FakeSaves.saves == []


def test_slow_deep_title_output_expiry_skips_deep_analyze(env):
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    FakeEngine.deep_result = [_row_success("600001")]

    def slow_title(text):
        if "自动深度分析" in text:
            env.clock.advance(600.0)

    env.console.on_print = slow_title
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert FakeEngine.deep_calls == [], "标题输出后到期不得再调用 deep_analyze"
    assert "深度分析标题输出后" in out and "未开始逐只分析" in out
    assert "候选快照已保存" in out


# F1（review1 P1）：四个“真实调用前”漏门——前一步恰等 deadline 后，下一实际调用必须 0。

def test_gate_portfolio_constructor_exact_deadline_blocks_account_request(env):
    FakeConfig.value = dict(copy.deepcopy(CONFIG),
                            scanner={"auto_exclude_holdings": True,
                                     "rules_path": "rules.yaml", "cache_ttl": 300})
    FakePortfolio.on_init = lambda: env.clock.advance(60.0)  # 构造后恰等 deadline
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=60.0)
    assert FakePortfolio.calls == ["init"], "构造已完成"
    assert FakePortfolio.requests == [], "构造后到期不得再 request_account_facts"
    assert FakeEngine.instances == [] and FakeEngine.quick_calls == []
    assert FakeSaves.saves == [] and FakeEngine.deep_calls == []
    assert "账户管理器构造后" in out and "启动预算已到期" in out


def test_gate_index_module_import_exact_deadline_blocks_query(env, monkeypatch):
    query_calls = []

    class GateAK:
        @staticmethod
        def _get_index_trend():
            query_calls.append("query")
            return None

    monkeypatch.setattr(akshare_mod, "AKShareClient", GateAK)
    real_import = builtins.__import__
    state = {"advanced": False}

    def slow_import(name, globals=None, locals=None, fromlist=(), level=0):
        mod = real_import(name, globals, locals, fromlist, level)
        if (name == "src.data.akshare_client" and fromlist
                and "AKShareClient" in fromlist and not state["advanced"]):
            state["advanced"] = True
            env.clock.advance(60.0)   # 模块 import 返回后恰等 deadline
        return mod

    monkeypatch.setattr(builtins, "__import__", slow_import)
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=60.0)
    assert state["advanced"], "必须发生过 index 模块 import"
    assert query_calls == [], "import 后到期不得再查询大盘"
    assert FakeEngine.quick_calls == [] and FakeSaves.saves == []
    assert FakeEngine.deep_calls == []
    assert "大盘模块导入后" in out and "启动预算已到期" in out


def test_gate_console_status_enter_exact_deadline_blocks_quick_scan(env):
    env.console.on_status_enter = lambda: env.clock.advance(60.0)
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=60.0)
    assert FakeEngine.quick_calls == [], "status.__enter__ 后到期不得再 quick_scan"
    assert FakeSaves.saves == [] and FakeEngine.deep_calls == []
    assert "扫描状态启动后" in out and "启动预算已到期" in out


def test_gate_fallback_title_print_exact_deadline_blocks_helper(env, monkeypatch):
    calls = []
    state = {"advanced": False}

    def fake_fallback(*a, **k):
        calls.append(1)
        return [Candidate("600009")], {
            "error": None, "located_count": 1, "invalid_count": 0,
            "has_quote": 1, "filtered_count": 1, "fallback_all": False,
            "stocks": []}

    monkeypatch.setattr(cli_main, "_scan_market_theme_fallback", fake_fallback)
    FakeEngine.quick_result = ([Candidate("600001")],
                               {"total_stocks": 5000,
                                "fallback_unfiltered": True})

    def slow_fallback_title(text):
        if "AI 报股兜底" in text and not state["advanced"]:
            state["advanced"] = True
            env.clock.advance(60.0)   # 兜底提示 print 后恰等 deadline

    env.console.on_print = slow_fallback_title
    out = env.run_market(rule_name="healthy_pullback", market_query="AI漫剧",
                         deep=True, budget_seconds=60.0)
    assert state["advanced"], "必须打印过主题兜底标题"
    assert calls == [], "兜底提示输出后到期不得调用 helper"
    assert FakeSaves.saves == [] and FakeEngine.deep_calls == []
    assert "主题兜底提示输出后" in out and "候选不完整" in out


# ═══════ 5. 候选阶段：必需兜底/最后调用跨截止/空异常结构未知 ═══════

def test_quick_scan_requires_fallback_but_expired_no_fallback_no_save(
        env, monkeypatch):
    fallback_calls = []

    def fake_fallback(*a, **k):
        fallback_calls.append(1)
        return [Candidate("600009")], {"error": None}

    monkeypatch.setattr(cli_main, "_scan_market_theme_fallback", fake_fallback)
    FakeEngine.quick_result = ([], {"total_stocks": 5000,
                                    "fallback_unfiltered": True})
    FakeEngine.on_quick = lambda: env.clock.advance(600.0)
    out = env.run_market(rule_name="healthy_pullback", market_query="AI漫剧",
                         deep=True, budget_seconds=600.0)
    assert "候选不完整" in out and "不落盘" in out and "不进入深度分析" in out
    assert fallback_calls == []
    assert FakeSaves.saves == [] and FakeEngine.deep_calls == []


def test_quick_scan_last_call_crosses_deadline_saves_but_deep_not_started(env):
    FakeEngine.quick_result = ([Candidate("600001"), Candidate("600002")],
                               {"total_stocks": 5000, "after_exclude": 4000,
                                "elapsed_seconds": 0.1})
    FakeEngine.on_quick = lambda: env.clock.advance(600.0)
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert len(FakeSaves.saves) == 1, "最后必需初筛调用已正常返回：完整候选可保存"
    assert [it["code"] for it in FakeSaves.saves[0][0]] == ["600001", "600002"]
    assert FakeEngine.deep_calls == [], "候选阶段后到期不得启动 deep"
    assert "候选初筛" in out and "深度分析未开始" in out
    assert "#N" in out and "l all" in out
    assert "深度分析完成" not in out


def test_fallback_last_call_crosses_deadline_saves_complete_candidates(
        env, monkeypatch):
    full_meta = {
        "error": None, "located_count": 2, "invalid_count": 0,
        "has_quote": 2, "filtered_count": 2, "fallback_all": False,
        "stocks": [{"code": "600009", "name": "候选9", "term": "AI漫剧", "why": "法C"},
                   {"code": "600010", "name": "候选10", "term": "AI漫剧", "why": "法C"}],
    }

    def fake_fallback(*a, **k):
        env.clock.advance(600.0)
        return [Candidate("600009"), Candidate("600010")], dict(full_meta)

    monkeypatch.setattr(cli_main, "_scan_market_theme_fallback", fake_fallback)
    FakeEngine.quick_result = ([], {"total_stocks": 5000,
                                    "fallback_unfiltered": True})
    out = env.run_market(rule_name="healthy_pullback", market_query="AI漫剧",
                         deep=True, budget_seconds=600.0)
    assert len(FakeSaves.saves) == 1
    assert [it["code"] for it in FakeSaves.saves[0][0]] == ["600009", "600010"]
    assert FakeEngine.deep_calls == []
    assert "候选初筛" in out and "深度分析未开始" in out
    assert "AI 报股 2 只" in out and "兜底失败" not in out, \
        "完整 meta 必须走成功路径，不能靠异常吞掉 KeyError 伪阳性保存"


@pytest.mark.parametrize("outcome", ["raise", "empty", "error", "unknown"])
def test_budgeted_required_fallback_not_confirmed_does_not_save_or_deep(
        outcome, env, monkeypatch):
    """F2：有预算且需要主题兜底时，异常/空/error/未知结构都不得拿未过滤原池冒充。"""
    def fake_fallback(*a, **k):
        if outcome == "raise":
            raise RuntimeError("helper boom")
        if outcome == "empty":
            return [], {"error": None, "located_count": 0, "invalid_count": 0,
                        "has_quote": 0, "filtered_count": 0,
                        "fallback_all": False, "stocks": []}
        if outcome == "error":
            return [], {"error": "AI unavailable", "located_count": 0,
                        "invalid_count": 0, "has_quote": 0,
                        "filtered_count": 0, "fallback_all": False, "stocks": []}
        return {"unexpected": "shape"}

    monkeypatch.setattr(cli_main, "_scan_market_theme_fallback", fake_fallback)
    FakeEngine.quick_result = ([Candidate("600001"), Candidate("600002")],
                               {"total_stocks": 5000,
                                "fallback_unfiltered": True})
    out = env.run_market(rule_name="healthy_pullback", market_query="AI漫剧",
                         deep=True, budget_seconds=600.0)
    assert "本批主题候选未确认" in out
    assert "上次有效扫描保留" in out
    assert FakeSaves.saves == [] and FakeSaves.reports == [], \
        "scan/history/report 均不得落盘"
    assert FakeEngine.deep_calls == [], "未确认主题候选不得进入 deep"


def test_unbudgeted_fallback_failure_keeps_legacy_pool_and_save(env, monkeypatch):
    """F2：无预算旧 fallback 语义保持（本卡不收紧/不核定）。"""
    def fake_fallback(*a, **k):
        raise RuntimeError("legacy helper boom")

    monkeypatch.setattr(cli_main, "_scan_market_theme_fallback", fake_fallback)
    FakeEngine.quick_result = ([Candidate("600001")],
                               {"total_stocks": 5000,
                                "fallback_unfiltered": True})
    FakeEngine.deep_result = [_row_success("600001")]
    out = env.run_market(rule_name="healthy_pullback", market_query="AI漫剧",
                         deep=True)
    assert len(FakeSaves.saves) == 1, "无预算旧语义：保留未过滤原池并保存"
    assert [it["code"] for it in FakeSaves.saves[0][0]] == ["600001"]
    assert FakeEngine.deep_calls, "无预算旧语义：继续 deep"
    assert "AI 报股兜底失败" in out


def test_fallback_expiry_before_second_stage_does_not_save_partial(env, monkeypatch):
    calls = []

    def fake_fallback(*a, **k):
        calls.append(1)
        return [], {"error": "no stocks"}

    monkeypatch.setattr(cli_main, "_scan_market_theme_fallback", fake_fallback)
    FakeEngine.quick_result = ([], {"total_stocks": 5000,
                                    "fallback_unfiltered": True})
    FakeEngine.on_quick = lambda: env.clock.advance(1000.0)
    out = env.run_market(rule_name="healthy_pullback", market_query="AI漫剧",
                         deep=True, budget_seconds=600.0)
    assert calls == []
    assert FakeSaves.saves == [] and FakeEngine.deep_calls == []
    assert "候选不完整" in out


def test_empty_candidates_do_not_save_or_deep(env):
    FakeEngine.quick_result = ([], {"total_stocks": 5000, "after_exclude": 4000,
                                    "elapsed_seconds": 0.1})
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "未找到符合条件的股票" in out
    assert FakeSaves.saves == [] and FakeEngine.deep_calls == []


def test_quick_scan_exception_does_not_overwrite_last_scan(env):
    FakeEngine.quick_exc = RuntimeError("supplier boom")
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "扫描失败" in out and "不覆盖上次有效扫描" in out
    assert FakeSaves.saves == [] and FakeEngine.deep_calls == []


def test_quick_scan_unknown_structure_does_not_overwrite_last_scan(env):
    FakeEngine.quick_result = {"unexpected": "shape"}
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "结构未知" in out and "不覆盖上次有效扫描" in out
    assert FakeSaves.saves == [] and FakeEngine.deep_calls == []


# ═══════ 6. 候选保存三项独立事实与资格指引 ═══════

def _one_candidate(env):
    FakeEngine.quick_result = ([Candidate("600001")], {"total_stocks": 5000})
    FakeEngine.deep_result = [_row_success("600001")]


def test_snapshot_failure_reports_facts_and_no_new_list_links(env):
    _one_candidate(env)
    FakeSaves.snapshot_ok = False
    FakeSaves.history_ok = True
    FakeSaves.report_value = "reports/fake.md"
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "候选快照未保存" in out and "不要按" in out
    assert "候选历史已记录" in out
    assert "候选报告已生成" in out and "reports/fake.md" in out
    assert "本批 #N / l all / ba" not in out, "快照失败不得给本批新名单指引"
    assert len(FakeSaves.saves) == 1 and len(FakeSaves.reports) == 1


def test_all_three_save_failures_reported_independently(env):
    _one_candidate(env)
    FakeSaves.snapshot_ok = False
    FakeSaves.history_ok = False
    FakeSaves.report_value = ""
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "候选快照未保存" in out
    assert "候选历史未记录" in out
    assert "候选报告未生成" in out
    assert "本批 #N / l all / ba" not in out


def test_snapshot_ok_history_fail_report_empty_keeps_links(env):
    _one_candidate(env)
    FakeSaves.snapshot_ok = True
    FakeSaves.history_ok = False
    FakeSaves.report_value = ""
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "候选快照已保存" in out and "本批 #N / l all / ba" in out
    assert "候选历史未记录" in out and "候选报告未生成" in out


def test_save_exception_reports_unconfirmed_and_deep_still_runs(env):
    _one_candidate(env)
    FakeSaves.save_exc = RuntimeError("save boom")
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "候选保存异常" in out and "不覆盖上次有效扫描" in out
    assert FakeSaves.reports == [], "保存结果不可用时不得冒称报告已尝试"
    assert FakeEngine.deep_calls, "候选保存异常不阻止本次内存候选的 deep"


def test_report_exception_keeps_snapshot_history_facts(env):
    _one_candidate(env)
    FakeSaves.report_exc = RuntimeError("report boom")
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "候选快照已保存" in out and "候选历史已记录" in out
    assert "候选报告未生成" in out
    assert "本批 #N / l all / ba" in out, "报告失败不回滚已成功的快照资格"
    assert len(FakeSaves.saves) == 1 and len(FakeSaves.reports) == 1


# ═══════ 7. deep 结果展示：成功/未取得完整分析结果/预算未开始 ═══════

def test_deep_results_partial_display_keeps_candidate_save(env):
    _one_candidate(env)
    FakeEngine.quick_result = ([Candidate("600001"), Candidate("600002")],
                               {"total_stocks": 5000})
    FakeEngine.deep_result = [_row_success("600001"),
                              _row_not_started("600002")]
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "候选快照已保存" in out
    assert "分析返回成功 1 只" in out
    assert "未取得完整分析结果 0 只" in out
    assert "预算未开始 1 只" in out and "600002" in out
    assert "所有股票深度分析均失败" not in out


def test_deep_results_all_not_started_not_failure(env):
    _one_candidate(env)
    FakeEngine.quick_result = ([Candidate("600001"), Candidate("600002")],
                               {"total_stocks": 5000})
    FakeEngine.deep_result = [_row_not_started("600001"),
                              _row_not_started("600002")]
    out = env.run_market(rule_name="healthy_pullback", deep=True,
                         budget_seconds=600.0)
    assert "预算未开始 2 只" in out
    assert "所有股票深度分析均失败" not in out
    assert "未开始不是分析失败" in out
    assert "候选快照已保存" in out


def test_display_no_success_incomplete_plus_not_started_no_all_failed(env):
    """F3：无成功时 [旧 successFalse/未取得完整] + [typed 未开始] 不得称全失败。"""
    rows = [_row_incomplete("600001", error="无法获取技术指标（K线数据缺失）"),
            _row_not_started("600002")]
    cli_main._display_scan_deep_results(rows)
    out = env.console.text()
    assert "所有股票深度分析均失败" not in out
    assert "分析返回成功 0 只" in out
    assert "未取得完整分析结果 1 只" in out and "600001" in out
    assert "预算未开始 1 只" in out and "600002" in out
    assert "0分" not in out and "零分" not in out.replace("不是分析失败或零分", "")


def test_display_bad_not_started_shapes_not_counted(env):
    rows = [
        {"stock_code": "600001", "success": False, "not_started": True,
         "stop_reason": "other"},
        {"stock_code": "600002", "success": False, "not_started": "yes",
         "stop_reason": BUDGET_STOP_REASON},
        {"stock_code": "600003", "success": True, "not_started": False},
        {"success": True},                       # 未知/坏返回
        "garbage",                               # 未知/坏返回
    ]
    cli_main._display_scan_deep_results(rows)
    out = env.console.text()
    assert "预算未开始 0 只" in out or "预算未开始" not in out
    assert "分析返回成功 1 只" in out
    assert "未取得完整分析结果 2 只" in out
    assert "未知/坏返回 2 条" in out
    assert "所有股票深度分析均失败" not in out


def test_display_conflicting_success_and_not_started_fields_unknown(env):
    """F5：successTrue 与 typed/非法 not_started 冲突行必须显式未知，不能计成功/未开始。"""
    rows = [
        {"stock_code": "600001", "success": True, "not_started": True,
         "stop_reason": BUDGET_STOP_REASON},
        {"stock_code": "600002", "success": True, "not_started": "yes"},
    ]
    cli_main._display_scan_deep_results(rows)
    out = env.console.text()
    assert "分析返回成功 0 只" in out
    assert "未取得完整分析结果 0 只" in out
    assert "预算未开始 0 只" in out
    assert "未知/坏返回 2 条" in out
    assert "所有股票深度分析均失败" not in out
    assert "600001" not in out and "600002" not in out, "冲突行不得冒充可展示结果"


@pytest.mark.parametrize("rows", [
    [
        {"stock_code": "600011", "success": True,
         "stop_reason": BUDGET_STOP_REASON},
        {"stock_code": "600012", "success": True, "not_started": False,
         "stop_reason": BUDGET_STOP_REASON},
    ],
    [
        {"stock_code": "600012", "success": True, "not_started": False,
         "stop_reason": BUDGET_STOP_REASON},
        {"stock_code": "600011", "success": True,
         "stop_reason": BUDGET_STOP_REASON},
    ],
], ids=["reason_without_not_started", "reason_with_not_started_false"])
def test_display_success_with_budget_stop_reason_only_unknown(rows, env):
    """F5 残留：successTrue 携带 stop_reason（not_started 缺失/False）同属状态互斥冲突。"""
    cli_main._display_scan_deep_results(rows)
    out = env.console.text()
    assert "分析返回成功 0 只" in out
    assert "未取得完整分析结果 0 只" in out
    assert "预算未开始 0 只" in out
    assert "未知/坏返回 2 条" in out
    assert "所有股票深度分析均失败" not in out
    assert "600011" not in out and "600012" not in out


def test_display_mixed_success_incomplete_not_started(env):
    rows = [_row_success("600001"), _row_incomplete("600002"),
            _row_not_started("600003")]
    cli_main._display_scan_deep_results(rows)
    out = env.console.text()
    assert "分析返回成功 1 只" in out
    assert "未取得完整分析结果 1 只" in out and "600002" in out
    assert "预算未开始 1 只" in out and "600003" in out
    assert "未取得完整分析结果 1 只：600002" in out
    assert "候选初筛" in out or "预算未开始" in out


# ═══════ 8. deep_analyze：deadline 校验/准备边界/逐股门/排名 ═══════

class FakeDeepPortfolio:
    inits = 0
    hook = None

    @classmethod
    def reset(cls):
        cls.inits = 0
        cls.hook = None

    def __init__(self):
        FakeDeepPortfolio.inits += 1
        if FakeDeepPortfolio.hook is not None:
            FakeDeepPortfolio.hook()

    def list_positions(self):
        return []

    def request_account_facts(self):
        return SimpleNamespace(
            nav=None, nav_day=None,
            context_for=lambda code, price=None, price_as_of=None:
            SimpleNamespace(position_state="EMPTY"))

    def to_strategy_state(self, *a, **k):
        return SimpleNamespace(current_position_ratio=0.0)


class FakeDeepAK:
    calls = []
    on_calc = None
    empty_codes = set()

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.on_calc = None
        cls.empty_codes = set()

    @staticmethod
    def calculate_indicators(code):
        FakeDeepAK.calls.append(code)
        if FakeDeepAK.on_calc is not None:
            FakeDeepAK.on_calc(code)
        if code in FakeDeepAK.empty_codes:
            return None
        return SimpleNamespace(stock_name=f"{code}名", price=10.0,
                               quote_as_of="2026-10-28")

    @staticmethod
    def get_realtime_quote(code):
        return {"stock_name": f"{code}名"}


class FakeOrch:
    calls = []

    def __init__(self):
        self.analyze_calls = FakeOrch.calls

    def analyze(self, **kwargs):
        FakeOrch.calls.append(kwargs["data"].stock_name
                              if hasattr(kwargs.get("data"), "stock_name") else "?")
        return (SimpleNamespace(decision=SimpleNamespace(value="HOLD")),
                SimpleNamespace(position_action=SimpleNamespace(value="HOLD")),
                None, None)


class FakeRanking:
    calls = []

    @classmethod
    def reset(cls):
        cls.calls = []

    def __init__(self, config=None):
        self.config = config

    def rank(self, results, ai_enabled=True):
        FakeRanking.calls.append([r["stock_code"] for r in results])
        out = []
        for i, r in enumerate(results):
            row = dict(r)
            row["ranking"] = SimpleNamespace(rank=i + 1, total_score=60.0,
                                             dimensions=[])
            out.append(row)
        return out


class DeepEnv:
    def __init__(self, engine, clock):
        self.engine = engine
        self.clock = clock


@pytest.fixture
def deep_env(monkeypatch):
    clock = Clock()
    FakeDeepPortfolio.reset()
    FakeDeepAK.reset()
    FakeRanking.reset()
    FakeOrch.calls = []
    engine = scanner_engine.ScannerEngine.__new__(scanner_engine.ScannerEngine)
    engine._orchestrator = FakeOrch()
    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(portfolio_mod, "PortfolioManager", FakeDeepPortfolio)
    monkeypatch.setattr(scanner_engine, "AKShareClient", FakeDeepAK)
    monkeypatch.setattr(ranking_mod, "RankingLayer", FakeRanking)
    return DeepEnv(engine, clock)


def test_deep_no_deadline_old_path(deep_env):
    rows = deep_env.engine.deep_analyze(["600001", "600002"])
    assert [r["stock_code"] for r in rows] == ["600001", "600002"]
    assert all(r["success"] for r in rows)
    assert FakeRanking.calls == [["600001", "600002"]]
    assert FakeDeepAK.calls == ["600001", "600002"]


@pytest.mark.parametrize("bad", [
    pytest.param(True, id="bool"), pytest.param("1600", id="str"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param(-float("inf"), id="neg_inf"),
])
def test_deep_invalid_deadline_rejected_before_business(bad, deep_env,
                                                        monkeypatch):
    engine = deep_env.engine
    engine._orchestrator = None
    monkeypatch.setattr(scanner_engine.ScannerEngine, "_get_orchestrator", _boom)
    monkeypatch.setattr(portfolio_mod, "PortfolioManager", _boom)
    monkeypatch.setattr(scanner_engine, "AKShareClient", _boom)
    with pytest.raises(ValueError, match="deadline"):
        engine.deep_analyze(["600001"], deadline=bad)
    assert FakeDeepAK.calls == [] and FakeRanking.calls == []


def test_deep_past_deadline_returns_all_not_started(deep_env, monkeypatch):
    engine = deep_env.engine
    engine._orchestrator = None
    monkeypatch.setattr(scanner_engine.ScannerEngine, "_get_orchestrator", _boom)
    rows = engine.deep_analyze(["600001", "600002"],
                               deadline=deep_env.clock.now - 1)
    assert [r["stock_code"] for r in rows] == ["600001", "600002"]
    assert all(r.get("not_started") is True for r in rows)
    assert all(r.get("stop_reason") == BUDGET_STOP_REASON for r in rows)
    assert all(r.get("success") is False for r in rows)
    assert FakeDeepAK.calls == [] and FakeRanking.calls == []


def test_deep_orchestrator_prep_expiry_all_not_started(deep_env, monkeypatch):
    engine = deep_env.engine
    engine._orchestrator = None

    def slow_orchestrator():
        deep_env.clock.advance(60.0)
        return FakeOrch()

    monkeypatch.setattr(engine, "_get_orchestrator", slow_orchestrator)
    rows = engine.deep_analyze(["600001", "600002"],
                               deadline=deep_env.clock.now + 60.0)
    assert all(r.get("not_started") is True for r in rows)
    assert FakeDeepPortfolio.inits == 0
    assert FakeDeepAK.calls == [] and FakeRanking.calls == []


def test_deep_account_prep_expiry_all_not_started(deep_env):
    FakeDeepPortfolio.hook = lambda: deep_env.clock.advance(60.0)
    rows = deep_env.engine.deep_analyze(["600001"],
                                        deadline=deep_env.clock.now + 60.0)
    assert all(r.get("not_started") is True for r in rows)
    assert FakeDeepAK.calls == [] and FakeRanking.calls == []


def test_deep_progress_callback_expiry_skips_fetch(deep_env):
    def slow_progress(step, current, total, message):
        deep_env.clock.advance(60.0)

    rows = deep_env.engine.deep_analyze(
        ["600001", "600002"], progress_callback=slow_progress,
        deadline=deep_env.clock.now + 60.0)
    assert all(r.get("not_started") is True for r in rows)
    assert FakeDeepAK.calls == [] and FakeRanking.calls == []


def test_deep_current_stock_completes_next_not_started_and_rank_excludes(deep_env):
    def slow_first(code):
        if code == "600001":
            deep_env.clock.advance(60.0)

    FakeDeepAK.on_calc = slow_first
    rows = deep_env.engine.deep_analyze(
        ["600001", "600002"], deadline=deep_env.clock.now + 60.0)
    assert FakeDeepAK.calls == ["600001"], "该股取数已开始允许整股完成，下一股 0 调用"
    assert rows[0]["success"] is True
    assert rows[1].get("not_started") is True
    assert FakeRanking.calls == [["600001"]], "排名只收已处理结果，未开始项排除后附回"
    assert [r["stock_code"] for r in rows] == ["600001", "600002"]


def test_deep_ranking_disabled_does_not_rank(deep_env):
    rows = deep_env.engine.deep_analyze(["600001"], ranking_config={"enabled": False})
    assert len(rows) == 1 and rows[0]["success"] is True
    assert FakeRanking.calls == []


def test_deep_failure_result_not_marked_not_started(deep_env):
    FakeDeepAK.empty_codes = {"600001"}
    rows = deep_env.engine.deep_analyze(["600001", "600002"])
    assert rows[0]["success"] is False
    assert rows[0].get("not_started") is not True
    assert rows[1]["success"] is True
    assert [r["stock_code"] for r in rows] == ["600001", "600002"]


def test_deep_no_deadline_does_not_read_clock(deep_env, monkeypatch):
    monkeypatch.setattr(time, "monotonic", _boom)
    rows = deep_env.engine.deep_analyze(["600001"])
    assert rows and rows[0]["success"] is True


# ═══════ 9. 独立 CLI：--scan --scan-deep --budget 接线与拒绝 ═══════

def _patch_cli_prelude(monkeypatch):
    import src.data.source_check as source_check_mod
    monkeypatch.setattr(source_check_mod, "fix_curl_ssl_paths", lambda: None)


def _patch_cli_business(monkeypatch):
    for name in ("scan_market", "analyze_portfolio", "manage_positions",
                 "scan_events", "analyze_live", "run_backtest",
                 "run_batch_validation", "show_expect", "show_fear",
                 "scan_review", "scan_review_import", "batch_tasks_cmd",
                 "today_command", "doctor", "diff_evidence_cmd",
                 "diff_list_cmd", "watch_pool", "shadow_command",
                 "plan2_command", "research_command", "benzong_batch_analyze"):
        if hasattr(cli_main, name):
            monkeypatch.setattr(cli_main, name, _boom)


def test_cli_valid_budget_wires_scan_market(monkeypatch):
    _patch_cli_prelude(monkeypatch)
    seen = []
    monkeypatch.setattr(cli_main, "scan_market",
                        lambda **kw: seen.append(dict(kw)))
    monkeypatch.setattr(sys, "argv",
                        ["prog", "--scan", "AI", "--scan-deep",
                         "--budget", "600"])
    cli_main.main()
    assert seen == [{"rule_name": "AI", "market_query": None, "deep": True,
                     "ai_enabled": True, "budget_seconds": 600.0}]


def test_cli_no_budget_keeps_old_scan_market_kwargs(monkeypatch):
    _patch_cli_prelude(monkeypatch)
    seen = []
    monkeypatch.setattr(cli_main, "scan_market",
                        lambda **kw: seen.append(dict(kw)))
    monkeypatch.setattr(sys, "argv", ["prog", "--scan", "AI", "--scan-deep"])
    cli_main.main()
    assert seen == [{"rule_name": "AI", "market_query": None, "deep": True,
                     "ai_enabled": True}]
    assert "budget_seconds" not in seen[0]


@pytest.mark.parametrize("argv", [
    ["--scan", "--budget", "600"],
    ["--scan-deep", "--budget", "600"],
    ["--budget", "600"],
    ["--scan", "AI", "--budget", "600"],
])
def test_cli_budget_requires_scan_and_scan_deep(argv, monkeypatch, capsys):
    _patch_cli_prelude(monkeypatch)
    _patch_cli_business(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "--budget" in err and ("仅用于" in err or "scan-deep" in err), err


@pytest.mark.parametrize("argv", [
    ["--scan", "--scan-deep", "--budget", "0"],
    ["--scan", "--scan-deep", "--budget", "-5"],
    ["--scan", "--scan-deep", "--budget", "nan"],
    ["--scan", "--scan-deep", "--budget", "inf"],
    ["--scan", "--scan-deep", "--budget", "abc"],
    ["--scan", "--scan-deep", "--budget", "60", "--budget", "90"],
])
def test_cli_bad_budget_rejected_before_business(argv, monkeypatch, capsys):
    _patch_cli_prelude(monkeypatch)
    _patch_cli_business(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "--budget" in err and ("无效" in err or "只能给一次" in err), err


@pytest.mark.parametrize("argv", [
    ["--scan", "--scan-deep", "--budget", "600", "--portfolio"],
    ["--scan", "--scan-deep", "--budget", "600", "--pos-list"],
    ["--scan", "--scan-deep", "--budget", "600", "--events"],
    ["--scan", "--scan-deep", "--budget", "600", "--live", "600519"],
    ["--scan", "--scan-deep", "--budget", "600", "--backtest", "600519"],
    ["--scan", "--scan-deep", "--budget", "600", "--scan-list-rules"],
    ["--scan", "--scan-deep", "--budget", "600", "--capital", "200000"],
])
def test_cli_mixed_actions_with_budget_rejected(argv, monkeypatch, capsys):
    _patch_cli_prelude(monkeypatch)
    _patch_cli_business(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "--budget" in err and "混用" in err, err


def test_cli_help_mentions_scan_deep_budget(monkeypatch, capsys):
    _patch_cli_prelude(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["prog", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--budget" in out
    assert "自动深度分析" in out


# ═══════ 10. 可发现性：REPL help/banner + 默认无预算提示 ═══════

def test_help_lists_scan_market_deep_budget_line(capsys):
    start.show_help()
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines()
             if "scan market deep --budget 600" in ln]
    assert lines, "帮助菜单必须列出 scan market deep --budget 600"
    for line in lines:
        assert start._display_width(line) <= 74, line
    joined = "\n".join(lines)
    assert "默认不启用" in joined or "可选" in joined
    assert "不开新只" in joined or "不开始" in joined
    start.show_banner()
    banner = capsys.readouterr().out
    assert "scan market deep --budget 600" in banner
