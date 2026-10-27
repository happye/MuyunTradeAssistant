# -*- coding: utf-8 -*-
"""T05-N：bz scan 可选启动预算、候选上限与落盘资格（零网络/零子进程/零真实账户）。

锁死语义（合同 N1–N6）：
- `bz scan` 及 `bz 筛选`/`bz 选股` 别名支持 `--budget 秒` / `--budget=秒`，默认不启用；
  坏预算与坏 `--limit`/`--top` 在解析与程序化入口都先于任何业务拒绝；limit/top 为
  正整数，默认 15/10；预算数值不得混成主题。
- 同一 monotonic 截止从 `run_benzong_scan` 入口起算，贯穿配置/账户/规则解析/主题A/
  主题B/各 allrules 扫描/评分/回测；到期不开始下一阶段（已开始的调用跑完）。
- 候选未完整形成 → 显式说明且不评分；评分只完成一部分 → 标为部分、不覆盖有效
  last_scan/正式扫描报告、不开启 TopN 回测；完整且有效 TopN 才沿旧路径保存。
- `limit` 一致作用于主题/单规则/allrules（去重后送评分名单上限，非发现调用上限）；
  allrules 仍五规则 × 每规则最多 5 个新代码（结构上限 25），按贡献桶轮询取
  min(limit,25)，保留被选证券完整规则命中/共振，不额外扫描/补桶。
- 回测每只在构造引擎前、标题输出后、`run()` 前核同一截止；未开始不记失败/收益0。
- banner/help 列 `bz scan --budget 600 --limit 15`；默认无预算在首次业务准备前提示；
  评分标题 6 维（5 AI + 1 规则），不把 N×5 当日实际请求/费用上限。

替身边界：ScannerEngine / locate_theme_stocks / auto_score_batch / load_config /
PortfolioManager / save_last_scan / persist_scan_report / BacktestEngine 全部模块替身，
时间用假钟（替换 start._monotonic）。真实 `parse_input` / `run_cli` /
`run_benzong_scan` / `show_help` / `show_banner` 原函数被驱动。

跑法（必须经守卫）：
  .venv\\Scripts\\python.exe -B -X utf8 plan/fusion/iteration9/review_test_runner.py \\
      tests/core/test_bzscan_budget.py
"""

import builtins
import copy
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import start

DIMS = ["industry_prosperity", "business_purity", "valuation_position",
        "industry_leader", "market_recognition", "risk_deduction"]
ALL_RULES = ["healthy_pullback", "steady_advance", "shrink_pullback",
             "value_pick", "oversold_watch"]

BASE_ARGS = {
    "theme": "", "top_n": 10, "backtest": False, "start": "", "end": "",
    "capital": 100000.0, "rule": "healthy_pullback", "limit": 15, "refresh": False,
}

CONFIG = {
    "scanner": {"auto_exclude_holdings": False, "rules_path": "rules.yaml",
                "cache_ttl": 300},
    "skills": {"dir": "skills", "enabled": [], "types": {}},
    "decision": {"signal_weights": {}},
    "entry_exit": None,
    "ai": None,
}


def scan_args(text: str) -> dict:
    """用真实 parse_input 构造 scan args（解析本身也是被测对象）。"""
    parsed = start.parse_input(text)
    assert parsed is not None, f"解析失败: {text}"
    assert parsed[0] == "benzong_scan", f"非 bz scan 分支: {text} -> {parsed[0]}"
    return dict(parsed[1])


# ── 假钟 ─────────────────────────────────────────────────────

class Clock:
    def __init__(self, t: float = 1000.0):
        self.now = float(t)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class AutoClock(Clock):
    """每次取时都消耗时间：用于“入口校验后立即到期”的阶段用例。"""

    def __init__(self, t: float = 1000.0, step: float = 600.0):
        super().__init__(t)
        self.step = float(step)

    def __call__(self) -> float:
        self.now += self.step
        return self.now


# ── 替身：扫描引擎 ───────────────────────────────────────────

class Candidate:
    def __init__(self, code: str):
        self.stock_code = code
        self.stock_name = f"{code}名"


class FakeEngine:
    instances = []
    scan_calls = []
    resolve_calls = []
    scan_results = {}
    on_init = None
    on_resolve = None
    on_scan = None

    @classmethod
    def reset(cls):
        cls.instances = []
        cls.scan_calls = []
        cls.resolve_calls = []
        cls.scan_results = {}
        cls.on_init = None
        cls.on_resolve = None
        cls.on_scan = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeEngine.instances.append(self)
        if FakeEngine.on_init is not None:
            FakeEngine.on_init(self)

    def resolve_rule_name(self, name):
        FakeEngine.resolve_calls.append(name)
        if FakeEngine.on_resolve is not None:
            FakeEngine.on_resolve(name)
        return name if name in ALL_RULES or name == "theme_members" else None

    def quick_scan(self, rule_name=None, market_query=None, exclude_codes=None):
        FakeEngine.scan_calls.append(rule_name)
        if FakeEngine.on_scan is not None:
            FakeEngine.on_scan(rule_name)
        codes = FakeEngine.scan_results.get(rule_name, [])
        # 成功返回不带 "error" 键（与真实 quick_scan 一致；带 None 会误触旧错误分支）
        return ([Candidate(c) for c in codes], {"cache_status": {}})


# ── 替身：配置/账户/主题定位/评分/落盘/回测 ──────────────────

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
    hook = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.hook = None

    def __init__(self):
        FakePortfolio.calls.append("init")

    def request_account_facts(self):
        if FakePortfolio.hook is not None:
            FakePortfolio.hook()
        return FakeFacts([])


class FakeLocator:
    calls = []
    stocks = []
    hook = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.stocks = []
        cls.hook = None

    def __call__(self, terms, config=None, use_cache=True):
        FakeLocator.calls.append((list(terms), use_cache))
        if FakeLocator.hook is not None:
            FakeLocator.hook(len(FakeLocator.calls))
        return {"stocks": [{"code": c, "name": f"{c}名",
                            "term": (list(terms)[0] if terms else ""),
                            "why": "法C"} for c in FakeLocator.stocks],
                "invalid": [], "cached": False}


def _ranked_item(code: str) -> dict:
    return {
        "code": code, "name": f"{code}名", "total_score": 60.0,
        "normalized_score": 60.0, "grade": "C", "effective_grade": "C",
        "confidence": 0.5, "dim_scores": {d: 50 for d in DIMS},
        "dim_confidences": {d: 1 for d in DIMS}, "warnings": [],
        "invalidate": False, "scoring_status": "complete", "degraded_dims": [],
    }


def complete_result(codes, top_n=10):
    items = [_ranked_item(c) for c in codes]
    return {"ranked": items, "top_n": items[:top_n], "failures": [],
            "scanned": len(codes), "scored": len(items), "failed": 0,
            "result_cb_errors": 0, "not_started": [], "stop_reason": None}


def partial_result(codes, top_n=10, started=1):
    items = [_ranked_item(c) for c in codes[:started]]
    return {"ranked": items, "top_n": items[:top_n], "failures": [],
            "scanned": len(codes), "scored": len(items), "failed": 0,
            "result_cb_errors": 0,
            "not_started": list(codes[started:]), "stop_reason": "budget_exhausted"}


def empty_result(codes, top_n=10):
    return {"ranked": [], "top_n": [], "failures": [{"code": c, "error": "x"} for c in codes],
            "scanned": len(codes), "scored": 0, "failed": len(codes),
            "result_cb_errors": 0, "not_started": [], "stop_reason": None}


class FakeScorer:
    calls = []
    result_factory = None
    hook = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.result_factory = None
        cls.hook = None

    def __call__(self, codes, top_n=10, force_refresh=False, config=None,
                 progress_cb=None, **kwargs):
        FakeScorer.calls.append({"codes": list(codes), "top_n": top_n,
                                 "force_refresh": force_refresh,
                                 "config": config, "kwargs": dict(kwargs)})
        if FakeScorer.hook is not None:
            FakeScorer.hook()
        if FakeScorer.result_factory is not None:
            return FakeScorer.result_factory(list(codes), top_n, kwargs)
        return complete_result(list(codes), top_n)


class FakeSaves:
    """真实 session_state.ScanSaveResult 形状的三项独立结果替身。

    snapshot/history 通过真实 ScanSaveResult 字段返回（不是只有 timestamp 的
    SimpleNamespace）；报告用 "" 表示 persist_scan_report 的失败返回。
    """
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

    def persist_scan_report(self, items, source, timestamp=None):
        FakeSaves.reports.append((list(items), source, timestamp))
        if FakeSaves.report_exc is not None:
            raise FakeSaves.report_exc
        return FakeSaves.report_value


class FakeBacktest:
    inits = []
    runs = []
    results = {}
    hook_init = None
    hook_run = None

    @classmethod
    def reset(cls):
        cls.inits = []
        cls.runs = []
        cls.results = {}
        cls.hook_init = None
        cls.hook_run = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeBacktest.inits.append(kwargs)
        if FakeBacktest.hook_init is not None:
            FakeBacktest.hook_init(kwargs)

    def run(self):
        code = self.kwargs.get("stock_code")
        FakeBacktest.runs.append(code)
        if FakeBacktest.hook_run is not None:
            FakeBacktest.hook_run(self.kwargs)
        ret, bench, trades = FakeBacktest.results.get(code, (5.0, 2.0, 3))
        return SimpleNamespace(total_return_pct=ret, benchmark_return_pct=bench,
                               total_trades=trades)


# ── 环境 fixture：全部外部依赖替身 + 假钟 ────────────────────

class Env:
    def __init__(self, clock, capsys):
        self.clock = clock
        self.capsys = capsys

    def run(self, args):
        start.run_benzong_scan(dict(args))
        return self.capsys.readouterr().out

    def run_cli(self, args):
        start.run_cli("benzong_scan", dict(args))
        return self.capsys.readouterr().out


@pytest.fixture
def env(monkeypatch, capsys):
    import src.cli.main as cli_main
    import src.cli.session_state as session_state
    import src.core.backtest_engine as backtest_engine
    import src.core.benzong.batch_scorer as batch_scorer
    import src.core.benzong.theme_locator as theme_locator
    import src.data.portfolio as portfolio
    import src.scanner.scanner_engine as scanner_engine

    clock = Clock()
    FakeEngine.reset()
    FakeConfig.reset()
    FakePortfolio.reset()
    FakeLocator.reset()
    FakeScorer.reset()
    FakeSaves.reset()
    FakeBacktest.reset()

    monkeypatch.setattr(start, "_monotonic", clock)
    monkeypatch.setattr(scanner_engine, "ScannerEngine", FakeEngine)
    monkeypatch.setattr(batch_scorer, "auto_score_batch", FakeScorer())
    monkeypatch.setattr(cli_main, "load_config", fake_load_config)
    monkeypatch.setattr(portfolio, "PortfolioManager", FakePortfolio)
    monkeypatch.setattr(theme_locator, "locate_theme_stocks", FakeLocator())
    monkeypatch.setattr(session_state, "save_last_scan", FakeSaves().save_last_scan)
    monkeypatch.setattr(session_state, "persist_scan_report",
                        FakeSaves().persist_scan_report)
    monkeypatch.setattr(backtest_engine, "BacktestEngine", FakeBacktest)
    return Env(clock, capsys)


def _boom(*a, **k):
    raise AssertionError("坏值/到期路径不得触发业务动作")


def _forbid_business_deps(monkeypatch):
    """坏值/到期零业务哨兵：配置/引擎/定位/评分/落盘/回测调用都算失败。"""
    import src.cli.main as cli_main
    import src.cli.session_state as session_state
    import src.core.backtest_engine as backtest_engine
    import src.core.benzong.batch_scorer as batch_scorer
    import src.core.benzong.theme_locator as theme_locator
    import src.data.portfolio as portfolio
    import src.scanner.scanner_engine as scanner_engine

    monkeypatch.setattr(scanner_engine, "ScannerEngine", _boom)
    monkeypatch.setattr(batch_scorer, "auto_score_batch", _boom)
    monkeypatch.setattr(cli_main, "load_config", _boom)
    monkeypatch.setattr(portfolio, "PortfolioManager", _boom)
    monkeypatch.setattr(theme_locator, "locate_theme_stocks", _boom)
    monkeypatch.setattr(session_state, "save_last_scan", _boom)
    monkeypatch.setattr(session_state, "persist_scan_report", _boom)
    monkeypatch.setattr(backtest_engine, "BacktestEngine", _boom)


def _no_business(monkeypatch):
    _forbid_business_deps(monkeypatch)
    monkeypatch.setattr(start, "run_benzong_scan", _boom)


# ═══════ 1. 解析层：支持入口/两种写法/别名/默认值 ═══════

def test_parse_scan_budget_space_and_equals():
    parsed = start.parse_input("bz scan --budget 600 --limit 15")
    assert parsed is not None, "bz scan 应支持 --budget（基线红=业务断言）"
    mode, args = parsed
    assert mode == "benzong_scan"
    assert args["budget"] == 600.0 and args["limit"] == 15 and args["top_n"] == 10
    mode, args = start.parse_input("bz scan 科技 --budget=90 --top 3")
    assert mode == "benzong_scan"
    assert args["budget"] == 90.0 and args["limit"] == 15 and args["top_n"] == 3


def test_parse_scan_budget_aliases_and_positions():
    for text in ("bz 筛选 科技 --budget 60", "bz 选股 --budget=60",
                 "bz --budget 60 scan 科技"):
        parsed = start.parse_input(text)
        assert parsed is not None, f"scan 别名应支持 --budget: {text}"
        mode, args = parsed
        assert mode == "benzong_scan"
        assert args["budget"] == 60.0


def test_parse_scan_budget_does_not_leak_into_theme():
    parsed = start.parse_input("bz scan --budget 60 AI")
    assert parsed is not None, "bz scan --budget 60 AI 应可解析"
    mode, args = parsed
    assert args["budget"] == 60.0
    assert args["rule"] == "AI" and args["theme"] == ""
    assert "60" not in (args["theme"] + args["rule"])


def test_parse_scan_no_budget_keeps_legacy_dict():
    assert start.parse_input("bz scan") == ("benzong_scan", dict(BASE_ARGS))


# ═══════ 2. 解析层：坏预算/坏 limit/top 先于业务拒绝 ═══════

@pytest.mark.parametrize("text", [
    "bz scan 科技 --budget",
    "bz scan 科技 --budget=",
    "bz scan 科技 --budget abc",
    "bz scan 科技 --budget 0",
    "bz scan 科技 --budget -5",
    "bz scan 科技 --budget nan",
    "bz scan 科技 --budget inf",
    "bz scan 科技 --budget 60 --budget 90",
])
def test_parse_scan_bad_budget_rejected(text, monkeypatch, capsys):
    from src.cli import session_state

    def boom(*a, **k):
        raise AssertionError("解析层拒绝不得读取扫描状态")

    monkeypatch.setattr(session_state, "get_last_scan", boom)
    monkeypatch.setattr(session_state, "resolve_index", boom)
    assert start.parse_input(text) is None
    out = capsys.readouterr().out
    assert "--budget" in out and ("无效" in out or "缺少" in out)


@pytest.mark.parametrize("text,label", [
    ("bz scan 科技 --limit 0", "--limit"),
    ("bz scan 科技 --limit -2", "--limit"),
    ("bz scan 科技 --limit abc", "--limit"),
    ("bz scan 科技 --limit 2.5", "--limit"),
    ("bz scan 科技 --limit", "--limit"),
    ("bz scan 科技 --top 0", "--top"),
    ("bz scan 科技 --top x", "--top"),
    ("bz scan 科技 --top", "--top"),
])
def test_parse_scan_bad_limit_or_top_rejected(text, label, monkeypatch, capsys):
    from src.cli import session_state

    def boom(*a, **k):
        raise AssertionError("坏 limit/top 不得读取扫描状态")

    monkeypatch.setattr(session_state, "get_last_scan", boom)
    monkeypatch.setattr(session_state, "resolve_index", boom)
    assert start.parse_input(text) is None
    out = capsys.readouterr().out
    assert label in out and "正整数" in out


def test_parse_scan_glued_or_unknown_budget_rejected(capsys):
    for text in ("bz scan 科技,--budget=60", "bz scan --budgetx 60",
                 "bz scan --budgets=60 科技"):
        assert start.parse_input(text) is None
        out = capsys.readouterr().out
        assert "未知预算" in out or "不支持 --budget" in out
        assert "科技" not in out or "选项" in out


# ═══════ 3. 程序化入口：坏值零业务 ═══════

@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"), pytest.param(True, id="bool"),
])
def test_run_cli_scan_bad_budget_zero_business(bad, monkeypatch, capsys):
    _no_business(monkeypatch)
    start.run_cli("benzong_scan", dict(BASE_ARGS, budget=bad))
    out = capsys.readouterr().out
    assert "预算参数无效" in out and "未开始扫描" in out


@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param("60", id="str"),
])
def test_run_benzong_scan_programmatic_bad_budget_zero_business(bad, env, monkeypatch):
    args = dict(BASE_ARGS, budget=bad)
    out = env.run(args)
    assert "预算参数无效" in out and "未开始扫描" in out
    assert FakeConfig.calls == 0 and FakeEngine.instances == []
    assert FakeScorer.calls == [] and FakeSaves.saves == []
    assert FakeBacktest.inits == []


@pytest.mark.parametrize("args_patch,label", [
    ({"limit": 0}, "limit"), ({"limit": -1}, "limit"),
    ({"limit": "15"}, "limit"), ({"limit": True}, "limit"),
    ({"limit": None}, "limit"),
    ({"top_n": 0}, "TopN"), ({"top_n": -2}, "TopN"),
    ({"top_n": 2.5}, "TopN"), ({"top_n": True}, "TopN"),
    ({"top_n": "10"}, "TopN"),
])
def test_run_benzong_scan_programmatic_bad_limit_top_zero_business(
        args_patch, label, env):
    out = env.run(dict(BASE_ARGS, **args_patch))
    assert label in out and "正整数" in out
    assert FakeConfig.calls == 0 and FakeEngine.instances == []
    assert FakeScorer.calls == [] and FakeSaves.saves == []


# ═══════ 4. 同一截止：入口起算并原样接评分器 ═══════

def test_budget_deadline_passes_through_run_cli_to_scorer(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}
    args = dict(BASE_ARGS, budget=600.0)
    out = env.run_cli(args)
    assert len(FakeScorer.calls) == 1
    call = FakeScorer.calls[0]
    assert call["codes"] == ["600001", "600002"]
    assert call["kwargs"]["deadline"] == 1600.0
    assert "budget_seconds" not in call["kwargs"]
    assert "启动预算：600 秒" in out and "从本次扫描入口计时" in out


def test_no_budget_scan_keeps_old_scorer_call(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001"]}
    out = env.run(dict(BASE_ARGS))
    assert len(FakeScorer.calls) == 1
    assert "deadline" not in FakeScorer.calls[0]["kwargs"]
    assert "budget_seconds" not in FakeScorer.calls[0]["kwargs"]
    assert FakeSaves.saves and FakeSaves.reports
    assert "启动预算：600 秒" not in out and "准备阶段可能超出预算" not in out
    assert FakeConfig.calls == 1


# ═══════ 5. 准备阶段到期：不开始后续阶段 ═══════

def test_entry_expired_before_rule_resolution(monkeypatch, capsys):
    _forbid_business_deps(monkeypatch)
    # 假钟每次取时都前进：截止点刚算出，入口校验时即到期——必须先于任何业务拒绝。
    clock = AutoClock(step=600.0)
    monkeypatch.setattr(start, "_monotonic", clock)
    start.run_benzong_scan(dict(BASE_ARGS, budget=600.0))
    out = capsys.readouterr().out
    assert "启动预算已到期" in out and "入口" in out
    assert "不写 last_scan" in out


def test_rule_resolution_expired_stops_before_config(env):
    def slow_resolve(name):
        env.clock.advance(1000.0)

    FakeEngine.on_resolve = slow_resolve
    out = env.run(dict(BASE_ARGS, budget=10.0))
    assert "启动预算已到期" in out and "规则解析" in out
    assert FakeConfig.calls == 0 and FakeScorer.calls == [] and FakeSaves.saves == []


def test_config_load_expired_stops_before_account_and_scan(env):
    FakeConfig.value = dict(copy.deepcopy(CONFIG),
                            scanner={"auto_exclude_holdings": True,
                                     "rules_path": "rules.yaml", "cache_ttl": 300})
    FakeConfig.hook = lambda: env.clock.advance(1000.0)
    out = env.run(dict(BASE_ARGS, budget=10.0))
    assert "启动预算已到期" in out and "配置加载" in out
    assert FakePortfolio.calls == [] and FakeEngine.scan_calls == []
    assert FakeScorer.calls == [] and FakeSaves.saves == []


def test_account_prep_expired_stops_before_candidate(env):
    FakeConfig.value = dict(copy.deepcopy(CONFIG),
                            scanner={"auto_exclude_holdings": True,
                                     "rules_path": "rules.yaml", "cache_ttl": 300})
    FakePortfolio.hook = lambda: env.clock.advance(1000.0)
    out = env.run(dict(BASE_ARGS, budget=10.0))
    assert "启动预算已到期" in out and "账户准备" in out
    assert FakeEngine.scan_calls == [] and FakeScorer.calls == []
    assert FakeSaves.saves == []


# ═══════ 6. 三个候选路径的阶段到期 ═══════

def test_theme_channel_a_expired_marks_candidates_incomplete(env):
    FakeLocator.stocks = ["600001", "600002"]
    FakeLocator.hook = lambda n: env.clock.advance(1000.0)
    out = env.run(scan_args("bz scan 科技") | {"budget": 10.0})
    assert "候选不完整" in out and "不开始评分" in out and "不落盘" in out
    assert FakeEngine.scan_calls == []          # 通道B 未开始
    assert FakeScorer.calls == [] and FakeSaves.saves == []


def test_theme_channel_b_expired_after_complete_list_no_scoring(env):
    FakeLocator.stocks = ["600001", "600002"]
    FakeEngine.scan_results = {"theme_members": ["600003"]}
    FakeEngine.on_scan = (
        lambda rule: env.clock.advance(1000.0) if rule == "theme_members" else None)
    out = env.run(scan_args("bz scan 科技") | {"budget": 10.0})
    assert "候选已完整形成" in out and "不开始评分" in out
    assert FakeScorer.calls == [] and FakeSaves.saves == []


def test_single_rule_scan_expired_after_complete_list_no_scoring(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}
    FakeEngine.on_scan = lambda rule: env.clock.advance(1000.0)
    out = env.run(dict(BASE_ARGS, budget=10.0))
    assert "候选已完整形成" in out and "不开始评分" in out
    assert FakeScorer.calls == [] and FakeSaves.saves == []


def test_single_rule_engine_construct_expired_before_scan(env):
    def slow_second_init(engine):
        if len(FakeEngine.instances) == 2:
            env.clock.advance(1000.0)

    FakeEngine.on_init = slow_second_init
    out = env.run(dict(BASE_ARGS, budget=10.0))
    assert "启动预算已到期" in out and "引擎构造" in out
    assert FakeEngine.scan_calls == [] and FakeScorer.calls == []
    assert FakeSaves.saves == []


def test_allrules_expired_between_rules_marks_candidates_incomplete(env):
    FakeEngine.scan_results = {r: [f"{r[:2]}{i}" for i in range(3)] for r in ALL_RULES}
    FakeEngine.on_scan = (
        lambda rule: env.clock.advance(1000.0) if rule == "steady_advance" else None)
    out = env.run(scan_args("bz scan --allrules") | {"budget": 60.0})
    assert "候选不完整" in out and "不开始评分" in out
    assert FakeEngine.scan_calls == ["healthy_pullback", "steady_advance"]
    assert FakeScorer.calls == [] and FakeSaves.saves == []


def test_allrules_expired_after_final_rule_complete_list_no_scoring(env):
    FakeEngine.scan_results = {r: [f"{r[:2]}{i}" for i in range(3)] for r in ALL_RULES}
    FakeEngine.on_scan = (
        lambda rule: env.clock.advance(1000.0) if rule == "oversold_watch" else None)
    out = env.run(scan_args("bz scan --allrules") | {"budget": 60.0})
    assert "候选已完整形成" in out and "不开始评分" in out
    assert FakeEngine.scan_calls == list(ALL_RULES)
    assert FakeScorer.calls == [] and FakeSaves.saves == []


# ═══════ 7. 评分部分结果：展示标注 + 不落盘 + 不开启回测 ═══════

def test_partial_scoring_marked_partial_blocks_save_and_backtest(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002", "600003"]}
    FakeScorer.result_factory = (
        lambda codes, top_n, kw: partial_result(codes, top_n, started=1))
    out = env.run(dict(BASE_ARGS, budget=600.0, backtest=True))
    assert "部分结果" in out and "不覆盖" in out and "不开启 TopN 回测" in out
    assert FakeScorer.calls[0]["kwargs"]["deadline"] == 1600.0
    assert FakeSaves.saves == [] and FakeSaves.reports == []
    assert FakeBacktest.inits == []


def test_complete_scoring_saved_then_backtest_expiry_keeps_scan(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}
    FakeScorer.hook = lambda: env.clock.advance(1000.0)  # 评分耗时跨过截止
    out = env.run(dict(BASE_ARGS, budget=10.0, backtest=True))
    assert len(FakeSaves.saves) == 1 and len(FakeSaves.reports) == 1
    assert FakeSaves.saves[0][0][0]["code"] == "600001"
    assert "回测未开始（0/2 只）" in out and "不计失败" in out
    assert "新名单快照已保存" in out, "快照成功时到期说明应明确已保存"
    assert FakeBacktest.inits == []


def test_empty_topn_does_not_overwrite_last_scan(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001"]}
    FakeScorer.result_factory = lambda codes, top_n, kw: empty_result(codes, top_n)
    out = env.run(dict(BASE_ARGS, backtest=True))
    assert "无有效 TopN" in out and "不覆盖" in out
    assert FakeSaves.saves == [] and FakeSaves.reports == []
    assert FakeBacktest.inits == []


def test_empty_candidate_list_does_not_save(env):
    FakeEngine.scan_results = {"healthy_pullback": []}
    out = env.run(dict(BASE_ARGS))
    assert "初筛无候选" in out
    assert FakeScorer.calls == [] and FakeSaves.saves == []


# ═══════ 8. 回测边界：构造前 / 标题后 / run 前 / 当前 run 完成 ═══════

def test_backtest_construction_expiry_skips_run_and_next_item(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}

    def slow_first_init(kwargs):
        if len(FakeBacktest.inits) == 1:
            env.clock.advance(1000.0)

    FakeBacktest.hook_init = slow_first_init
    out = env.run(dict(BASE_ARGS, budget=60.0, backtest=True))
    assert FakeScorer.calls and FakeSaves.saves
    assert len(FakeBacktest.inits) == 1 and FakeBacktest.runs == []
    assert "未调用 run()" in out and "未开始 2 只" in out
    assert "成功 0/2 只" in out and "失败 0 只" in out


def test_backtest_slow_title_after_check_does_not_start_next(env, monkeypatch):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}
    real_print = print
    state = {"hit": False}

    def slow_title(*parts, **kwargs):
        text = " ".join(str(p) for p in parts)
        if not state["hit"] and "回测 600002" in text:
            state["hit"] = True
            env.clock.advance(1000.0)
        return real_print(*parts, **kwargs)

    monkeypatch.setattr("builtins.print", slow_title)
    out = env.run(dict(BASE_ARGS, budget=60.0, backtest=True))
    assert FakeBacktest.runs == ["600001"] and len(FakeBacktest.inits) == 1
    assert "标题输出后" in out and "未开始回测" in out


def test_backtest_run_completes_current_then_stops_next(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}

    def slow_first_run(kwargs):
        if kwargs.get("stock_code") == "600001":
            env.clock.advance(1000.0)

    FakeBacktest.hook_run = slow_first_run
    out = env.run(dict(BASE_ARGS, budget=60.0, backtest=True))
    assert FakeBacktest.runs == ["600001"] and len(FakeBacktest.inits) == 1
    assert "成功 1/2 只" in out and "失败 0 只" in out and "未开始 1 只" in out
    assert "不计失败" in out and "收益0" in out


# ═══════ 9. limit：主题/单规则去重上限；allrules 结构上限与轮询 ═══════

def test_topic_limit_dedupes_merged_candidates(env):
    FakeLocator.stocks = ["600001", "600001", "600002", "600003"]
    FakeEngine.scan_results = {"theme_members": ["600001", "600004"]}
    args = scan_args("bz scan 科技") | {"limit": 2}
    env.run(args)
    assert FakeScorer.calls[0]["codes"] == ["600001", "600002"]
    assert FakeEngine.scan_calls == ["theme_members"]


def test_single_rule_limit_dedupes_and_caps(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600001", "600002", "600003"]}
    env.run(dict(BASE_ARGS, limit=2))
    assert FakeScorer.calls[0]["codes"] == ["600001", "600002"]
    FakeScorer.calls = []
    env.run(dict(BASE_ARGS, limit=5))
    assert FakeScorer.calls[0]["codes"] == ["600001", "600002", "600003"]


def test_allrules_default_limit_round_robin_15(env):
    FakeEngine.scan_results = {
        r: [f"{i}{r}" for i in range(8)] for r in ALL_RULES}
    out = env.run(scan_args("bz scan --allrules"))
    codes = FakeScorer.calls[0]["codes"]
    expected = []
    buckets = [[f"{i}{r}" for i in range(5)] for r in ALL_RULES]
    for depth in range(3):
        expected.extend(bucket[depth] for bucket in buckets)
    assert codes == expected and len(codes) == 15
    assert "结构上限 25" in out and "实际选取 15" in out
    assert "用户请求候选上限 limit=15" in out
    assert "已选证券完整规则命中" in out
    assert FakeEngine.scan_calls == list(ALL_RULES)


def test_allrules_small_limit_round_robin(env):
    FakeEngine.scan_results = {r: [f"{i}{r}" for i in range(8)] for r in ALL_RULES}
    env.run(scan_args("bz scan --allrules") | {"limit": 3})
    assert FakeScorer.calls[0]["codes"] == [f"0{r}" for r in ALL_RULES[:3]]


def test_allrules_large_limit_capped_by_structure_and_reports_request(env):
    FakeEngine.scan_results = {r: [f"{i}{r}" for i in range(8)] for r in ALL_RULES}
    out = env.run(scan_args("bz scan --allrules") | {"limit": 40})
    assert len(FakeScorer.calls[0]["codes"]) == 25
    assert "limit=40" in out and "结构上限 25" in out and "按 25 截断" in out
    assert FakeEngine.scan_calls == list(ALL_RULES)


def test_allrules_overlap_buckets_keep_full_hits_and_resonance(env):
    FakeEngine.scan_results = {
        "healthy_pullback": ["A", "B", "C", "D", "E"],
        "steady_advance": ["A", "B", "F", "G", "H"],
        "shrink_pullback": ["A", "F", "I", "J", "K"],
        "value_pick": [],
        "oversold_watch": [],
    }
    out = env.run(scan_args("bz scan --allrules"))
    codes = FakeScorer.calls[0]["codes"]
    assert codes == ["A", "F", "I", "B", "G", "J", "C", "H", "K", "D", "E"]
    assert "3 只多规则共振" in out
    assert "healthy_pullback、steady_advance、shrink_pullback（共振）" in out
    assert "steady_advance、shrink_pullback（共振）" in out
    assert FakeEngine.scan_calls == list(ALL_RULES)


def test_allrules_small_limit_still_scans_all_five_rules(env):
    FakeEngine.scan_results = {r: [f"{i}{r}" for i in range(8)] for r in ALL_RULES}
    env.run(scan_args("bz scan --allrules") | {"limit": 1})
    assert len(FakeScorer.calls[0]["codes"]) == 1
    assert FakeEngine.scan_calls == list(ALL_RULES)


# ═══════ 10. 可发现性 / 标题 / refresh 语义 ═══════

def test_help_and_banner_list_scan_budget_example(capsys):
    start.show_help()
    help_out = capsys.readouterr().out
    assert "bz scan --budget 600 --limit 15" in help_out
    for line in help_out.splitlines():
        if "bz scan --budget" in line:
            assert start._display_width(line) <= 74, line
    start.show_banner()
    banner_out = capsys.readouterr().out
    assert "bz scan --budget 600 --limit 15" in banner_out


def test_default_scan_hint_before_first_business(env):
    captured = []
    FakeConfig.hook = lambda: captured.append(env.capsys.readouterr().out)
    FakeEngine.scan_results = {"healthy_pullback": ["600001"]}
    env.run(dict(BASE_ARGS))
    assert captured, "load_config 必须已被调用"
    assert "--budget 600" in captured[0] and "默认不启用" in captured[0]
    assert "启动预算：600 秒" not in captured[0]


def test_scoring_title_is_6dim_5ai_1rule(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}
    out = env.run(dict(BASE_ARGS))
    assert "6 维" in out and "5 维 AI" in out and "1 维规则" in out
    assert "不是实耗" in out
    assert "10次AI" not in out and "× 5维" not in out


def test_refresh_semantics_preserved_with_budget(env):
    FakeLocator.stocks = ["600001"]
    FakeEngine.scan_results = {"theme_members": []}
    env.run(scan_args("bz scan --refresh 科技") | {"budget": 600.0})
    assert FakeLocator.calls[0][1] is False        # use_cache = not refresh
    assert FakeScorer.calls[0]["force_refresh"] is True


# ═══════ 11. 独立复核 F1：真正下一次调用前的同一绝对截止门 ═══════
# 构造与标题输出都可能耗尽预算；只有“调用前”再核一次才能保证下一调用数为 0。
# 假钟推进量恰等于 budget（t=1000, budget=60 → 恰等于 deadline），边界为 >= 到期。

def test_f1_rule_engine_construct_expiry_blocks_resolve(env):
    def slow_first_init(engine):
        if len(FakeEngine.instances) == 1:
            env.clock.advance(60.0)      # 构造耗尽到恰等于 deadline

    FakeEngine.on_init = slow_first_init
    out = env.run(dict(BASE_ARGS, budget=60.0))
    assert FakeEngine.resolve_calls == [], "构造后到期不得再调用 resolve_rule_name"
    assert len(FakeEngine.instances) == 1
    assert FakeConfig.calls == 0 and FakeScorer.calls == []
    assert FakeSaves.saves == [] and FakeBacktest.inits == []
    assert "启动预算已到期" in out and "规则" in out


def test_f1_theme_step1_title_expiry_blocks_locate(env, monkeypatch):
    FakeLocator.stocks = ["600001"]
    real_print = print

    def slow_title(*parts, **kwargs):
        text = " ".join(str(p) for p in parts)
        if "双通道选股" in text:
            env.clock.advance(60.0)      # 标题输出耗尽到恰等于 deadline
        return real_print(*parts, **kwargs)

    monkeypatch.setattr("builtins.print", slow_title)
    out = env.run(scan_args("bz scan 科技") | {"budget": 60.0})
    assert FakeLocator.calls == [], "标题后到期不得再调用 locate_theme_stocks"
    assert FakeEngine.scan_calls == []
    assert FakeScorer.calls == [] and FakeSaves.saves == []
    assert FakeBacktest.inits == []
    assert "启动预算已到期" in out


def test_f1_single_rule_step1_title_expiry_blocks_scan(env, monkeypatch):
    FakeEngine.scan_results = {"healthy_pullback": ["600001"]}
    real_print = print

    def slow_title(*parts, **kwargs):
        text = " ".join(str(p) for p in parts)
        if "技术面初筛中" in text:
            env.clock.advance(60.0)      # 标题输出耗尽到恰等于 deadline
        return real_print(*parts, **kwargs)

    monkeypatch.setattr("builtins.print", slow_title)
    out = env.run(dict(BASE_ARGS, budget=60.0))
    assert FakeEngine.scan_calls == [], "标题后到期不得再调用 quick_scan"
    assert FakeScorer.calls == [] and FakeSaves.saves == []
    assert FakeBacktest.inits == []
    assert "启动预算已到期" in out


def test_f1_step2_title_expiry_blocks_scorer(env, monkeypatch):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}
    real_print = print

    def slow_title(*parts, **kwargs):
        text = " ".join(str(p) for p in parts)
        if "Step 2/3" in text:
            env.clock.advance(60.0)      # 评分标题输出耗尽到恰等于 deadline
        return real_print(*parts, **kwargs)

    monkeypatch.setattr("builtins.print", slow_title)
    out = env.run(dict(BASE_ARGS, budget=60.0))
    assert FakeScorer.calls == [], "评分标题后到期不得再调用 auto_score_batch"
    assert FakeSaves.saves == [] and FakeBacktest.inits == []
    assert "启动预算已到期" in out


# ═══════ 12. 独立复核 F2：快照/历史/报告三项独立事实与资格指引 ═══════
# 真实 helper：save_last_scan 返回 ScanSaveResult(snapshot, history, timestamp)，
# persist_scan_report 失败返回空串；不得把失败当成功，也不得无条件给新名单指引。

def _single_candidate(env, code="600001"):
    FakeEngine.scan_results = {"healthy_pullback": [code]}


def test_f2_snapshot_failure_reports_facts_and_no_new_list_links(env):
    _single_candidate(env)
    FakeSaves.snapshot_ok = False
    FakeSaves.history_ok = True
    FakeSaves.report_value = "reports/fake.md"
    out = env.run(dict(BASE_ARGS))
    assert "快照失败" in out and "历史成功" in out and "报告已生成" in out
    assert "l all 批量深分析这" not in out, "快照失败不得给本批新名单快捷指引"
    assert "不要按" in out and "旧名单" in out
    assert FakeSaves.saves and FakeSaves.reports
    assert FakeBacktest.inits == []


def test_f2_all_three_failures_reported_independently(env):
    _single_candidate(env)
    FakeSaves.snapshot_ok = False
    FakeSaves.history_ok = False
    FakeSaves.report_value = ""
    out = env.run(dict(BASE_ARGS))
    assert "快照失败" in out and "历史失败" in out and "报告未生成" in out
    assert "l all 批量深分析这" not in out
    assert FakeSaves.saves and FakeSaves.reports


def test_f2_snapshot_ok_history_fail_report_empty_keeps_links(env):
    _single_candidate(env)
    FakeSaves.snapshot_ok = True
    FakeSaves.history_ok = False
    FakeSaves.report_value = ""
    out = env.run(dict(BASE_ARGS))
    assert "快照成功" in out and "历史失败" in out and "报告未生成" in out
    assert "l all 批量深分析这" in out, "只有快照成功才给本批新名单快捷指引"


def test_f2_save_exception_reports_unconfirmed_and_backtest_still_runs(env):
    _single_candidate(env)
    FakeSaves.save_exc = RuntimeError("save boom")
    out = env.run(dict(BASE_ARGS, backtest=True))
    assert "scan 落盘异常" in out
    assert "不要按" in out and "旧名单" in out
    assert FakeSaves.reports == [], "保存结果不可用时不得冒称报告已尝试"
    assert FakeBacktest.runs == ["600001"], "保存失败不阻止本次完整内存 TopN 回测"


def test_f2_report_exception_keeps_snapshot_history_facts(env):
    _single_candidate(env)
    FakeSaves.snapshot_ok = True
    FakeSaves.history_ok = True
    FakeSaves.report_exc = RuntimeError("report boom")
    out = env.run(dict(BASE_ARGS))
    assert "快照成功" in out and "历史成功" in out and "报告未生成" in out
    assert "l all 批量深分析这" in out, "报告失败不回滚已成功的快照/历史资格"
    assert FakeSaves.saves and FakeSaves.reports


def test_f2_backtest_expiry_not_claim_saved_when_snapshot_failed(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}
    FakeSaves.snapshot_ok = False
    FakeSaves.history_ok = False
    FakeScorer.hook = lambda: env.clock.advance(1000.0)   # 评分后跨过截止
    out = env.run(dict(BASE_ARGS, budget=10.0, backtest=True))
    assert "回测未开始（0/2 只）" in out
    assert "新名单快照已保存" not in out
    assert "已完整评分并保存" not in out
    assert "未成功保存" in out
    assert FakeBacktest.inits == []


# ═══════ 13. 独立复核 F3：回测成功/失败/未开始分别计数 ═══════

def test_f3_constructor_failure_not_counted_as_complete(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}

    def fail_first_init(kwargs):
        if len(FakeBacktest.inits) == 1:
            env.clock.advance(60.0)      # 构造耗尽到恰等于 deadline 后失败
            raise RuntimeError("ctor boom")

    FakeBacktest.hook_init = fail_first_init
    out = env.run(dict(BASE_ARGS, budget=60.0, backtest=True))
    assert FakeBacktest.runs == [] and len(FakeBacktest.inits) == 1
    assert "成功 0/2 只" in out and "失败 1 只" in out and "未开始 1 只" in out
    assert "回测完成 1/2" not in out
    assert "未开始不计失败" in out or "不计失败" in out


def test_f3_run_failure_not_counted_as_complete(env):
    FakeEngine.scan_results = {"healthy_pullback": ["600001", "600002"]}

    def fail_first_run(kwargs):
        if kwargs.get("stock_code") == "600001":
            env.clock.advance(60.0)      # run 耗尽到恰等于 deadline 后失败
            raise RuntimeError("run boom")

    FakeBacktest.hook_run = fail_first_run
    out = env.run(dict(BASE_ARGS, budget=60.0, backtest=True))
    assert FakeBacktest.runs == ["600001"] and len(FakeBacktest.inits) == 1
    assert "成功 0/2 只" in out and "失败 1 只" in out and "未开始 1 只" in out
    assert "回测完成 1/2" not in out


# ═══════ 14. 独立复核 fix2：import 后首构造门 / 入口时点在验证 import 前 ═══════
# 真实模块已在 fixture/import 时预导入；只包装 builtins.__import__ 的“fromimport 返回”
# 时点推进假钟，不把其余 fakeclock/getter 边界扩成大矩阵。

def _slow_fromimport(monkeypatch, module_name, from_name, advance):
    """仅在指定 `from <module> import <from_name>` 返回后调用 advance()。"""
    real_import = builtins.__import__

    def slow_import(name, globals=None, locals=None, fromlist=(), level=0):
        mod = real_import(name, globals, locals, fromlist, level)
        if name == module_name and fromlist and from_name in fromlist:
            advance()
        return mod

    monkeypatch.setattr(builtins, "__import__", slow_import)


def test_f1r2_scanner_import_expiry_blocks_first_ctor(env, monkeypatch):
    """scanner_engine fromimport 返回即耗尽到 deadline：不得再构造首个扫描引擎。"""
    import src.scanner.scanner_engine as _se  # 预导入真实模块（替身只改属性）
    assert _se.ScannerEngine is FakeEngine
    _slow_fromimport(monkeypatch, "src.scanner.scanner_engine", "ScannerEngine",
                     lambda: env.clock.advance(60.0))
    out = env.run(dict(BASE_ARGS, budget=60.0))
    assert FakeEngine.instances == [], "import 后到期不得再构造 ScannerEngine"
    assert FakeEngine.resolve_calls == [] and FakeEngine.scan_calls == []
    assert FakeConfig.calls == 0 and FakeLocator.calls == []
    assert FakeScorer.calls == [] and FakeSaves.saves == []
    assert FakeBacktest.inits == []
    assert "启动预算已到期" in out


def test_f1r2_budget_import_entry_clock_no_reset(env, monkeypatch):
    """入口时点在 budget_descriptor 验证 import 之前；验证 import 耗尽到原入口 deadline
    后不得重起钟（否则会多出 budget 秒），也不得构造引擎/读配置。"""
    import src.core.benzong.batch_scorer as _bs  # 预导入真实模块（属性替身已在 fixture）
    assert callable(_bs.budget_descriptor)
    _slow_fromimport(monkeypatch, "src.core.benzong.batch_scorer", "budget_descriptor",
                     lambda: env.clock.advance(60.0))
    out = env.run(dict(BASE_ARGS, budget=60.0))
    assert FakeEngine.instances == [], "入口预算耗尽不得再构造 ScannerEngine"
    assert FakeEngine.resolve_calls == [] and FakeEngine.scan_calls == []
    assert FakeConfig.calls == 0 and FakeLocator.calls == []
    assert FakeScorer.calls == [] and FakeSaves.saves == []
    assert FakeBacktest.inits == []
    assert "启动预算已到期" in out
