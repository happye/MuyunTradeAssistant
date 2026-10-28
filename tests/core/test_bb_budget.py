# -*- coding: utf-8 -*-
"""T05-Q：REPL `bb`（批量回测验证 run_batch_validation）屏幕汇总 / 参数校验 / 可选启动预算，
以及共享启动预算件 src/cli/start_budget.py（StartBudget）。

锁死语义（plan/fusion/iteration10/DELIVERY_PLAN.md 2026-10-28 T05-Q 合同）：
- 解析：剥离 --budget 后按中英文逗号/顿号/分号与空格切片段；6 位代码（_is_stock_code）、
  严格 YYYY-MM-DD 真实日期（最多 2 个，起<=止）；无效片段/-x 类 flag/无代码/第 3 个日期/
  起>止在业务前拒绝并给用法；golden 两条输出逐字不变。
- run_cli：只有显式预算才向 run_batch_validation 多传 budget_seconds；无预算 kwargs 与旧版
  逐字一致；坏值（0/nan/inf/"60"/True/-1）统一门拒绝且不调用批量回测。
- run_batch_validation(..., *, budget_seconds=None)：None 不取时钟；非 None 经 StartBudget
  同门校验（坏值零业务，返回 None）；入口时钟早于回测模块 import；检查点：入口校验后、
  回测模块 import 后、配置后、每只标题前、标题后 run 前；一只开始后其全部窗口跑完，
  截止不传入 BacktestEngine；未开始只来自 stock_codes[k:]，不进成功/失败。
- build_batch_validation_payload(..., not_started=None)：None 时键集合与旧版一致；给列表时
  增 not_started 与 summary.not_started_count，提示说明“非失败”，“已覆盖”按已处理数。
- 结束时总在屏幕打印批量汇总（markup=False：失败原因里的方括号原样）。

替身边界：BacktestEngine（假类）/ load_config / backtest_validator 的取数与切片函数（最小桩）/
console（真实 rich Console 写入 StringIO）；时间用假钟（替换 time.monotonic，只记录来自
cli/main.py 与 cli/start_budget.py 的读数）。零网络零 AI 零真实账户。

跑法（公司内网必须经项目未修改的守卫 runner）：
  .venv\\Scripts\\python.exe plan/fusion/iteration9/review_test_runner.py \\
      tests/core/test_bb_budget.py
"""

import builtins
import io
import json
import math
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest
from rich.console import Console

import start
import src.cli.main as cli_main
import src.cli.session_state as session_state
import src.core.backtest_engine as engine_mod
import src.core.backtest_validator as bv
import src.core.benzong.batch_scorer as batch_scorer_mod  # noqa: F401  先于假钟导入

try:  # 红阶段（共享件尚未存在）时让各测试各自失败，而不是整文件收集失败
    import src.cli.start_budget as sb_mod
except ImportError:  # pragma: no cover
    sb_mod = None

T0 = 1000.0
BUDGET = 600.0          # 默认截止 = T0 + 600 = 1600
CODES = ("600001", "600002", "600003")
WF_WINDOWS = 2          # 每只的 walk-forward 窗口数 → 每只共 1+1+1+2 = 5 次 run
RUNS_PER_STOCK = 5
WINDOWS = {
    "in_sample": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
    "out_of_sample": {"start_date": "2024-07-01", "end_date": "2024-12-31"},
    "walk_forward_windows": [
        {"test_start": "2024-02-01", "test_end": "2024-03-01"},
        {"test_start": "2024-04-01", "test_end": "2024-05-01"},
    ],
}
ENGINE_KW_KEYS = {"stock_code", "start_date", "end_date", "initial_capital", "execution_mode",
                  "layer_mode", "skills_dir", "signal_weights", "skill_types", "entry_exit_config"}
USAGE = "bb <代码1,代码2,...> [起始日期 结束日期] [--budget 秒]"


# ── 假钟：只记录来自 cli/main.py 与 cli/start_budget.py 的读数 ──

def _caller_is_budget_code() -> bool:
    frame = sys._getframe(2)   # 0=本函数 1=Clock.__call__ 2=真正的调用方
    name = frame.f_code.co_filename.replace("\\", "/")
    return name.endswith("src/cli/main.py") or name.endswith("src/cli/start_budget.py")


class Clock:
    def __init__(self, events, t=T0):
        self.now = float(t)
        self.events = events
        self.reads = []                # 来自受测代码的读数序列
        self.advance_at_read = {}      # 第 n 次(0 基)读数前先推进的秒数

    def __call__(self):
        if _caller_is_budget_code():
            n = len(self.reads)
            self.now += self.advance_at_read.get(n, 0.0)
            self.reads.append(self.now)
            self.events.append("clock")
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class HookConsole(Console):
    """真实 rich Console + 打印前钩子（用于“标题打印后到期”类边界）。"""

    print_hook = None

    def print(self, *objects, **kwargs):
        if self.print_hook is not None:
            self.print_hook(" ".join(str(o) for o in objects))
        super().print(*objects, **kwargs)


def _vpayload(code, ret=5.0, replay_ok=True):
    """build_validation_payload 的最小替身：_summarize_validation_payload 与打印行所需字段。"""
    return {
        "meta": {"stock_code": code, "stock_name": code},
        "slices": {"full_period": {"total_return_pct": ret, "excess_return_pct": 1.5,
                                   "total_trades": 3}},
        "checks": {
            "lookahead_integrity": {"passed": True},
            "replay_consistency": {"passed": replay_ok},
            "in_sample_vs_out_of_sample": {"passed": True},
            "walk_forward": {"passed": True},
        },
        "validation_hints": [],
    }


class BatchRig:
    """run_batch_validation 的全替身运行台：事件序列 + 假钟 + 钩子 + 输出缓冲。"""

    def __init__(self):
        self.events = []
        self.clock = Clock(self.events)
        self.hooks = {}
        self.fail = {}                 # code -> 从该只首次 run 抛出的异常
        self.engine_kwargs = []
        self.buf = io.StringIO()
        self.console = HookConsole(file=self.buf, width=10000)

    def fire(self, name):
        self.events.append(name)
        hook = self.hooks.get(name)
        if hook is not None:
            hook()

    def business(self):
        return [e for e in self.events if e != "clock" and e != "import:batch_scorer"]

    def count(self, prefix):
        return sum(1 for e in self.events if e.startswith(prefix))

    def out(self):
        return self.buf.getvalue()

    def run(self, codes=CODES, **kwargs):
        return cli_main.run_batch_validation(list(codes), "2024-01-01", "2024-12-31", **kwargs)

    # —— 替身 ——
    def make_engine_cls(self):
        rig = self

        class _FakeEngine:
            def __init__(self, **kw):
                self.kw = kw
                rig.engine_kwargs.append(dict(kw))
                rig.fire(f"construct:{kw['stock_code']}")

            def run(self):
                code = self.kw["stock_code"]
                rig.fire(f"run:{code}")
                exc = rig.fail.get(code)
                if exc is not None:
                    raise exc
                return SimpleNamespace(stock_code=code)

        return _FakeEngine

    def fake_load_config(self, config_path=None):
        self.fire("config")
        return {"skills": {"dir": "skills", "types": None}, "decision": {}, "entry_exit": None}


@pytest.fixture
def rig(monkeypatch):
    r = BatchRig()
    monkeypatch.setattr(time, "monotonic", r.clock)
    monkeypatch.setattr(cli_main, "console", r.console)
    monkeypatch.setattr(cli_main, "load_config", r.fake_load_config)
    monkeypatch.setattr(engine_mod, "BacktestEngine", r.make_engine_cls())
    monkeypatch.setattr(bv, "load_trading_dates", lambda code, s, e: ["2024-01-02"])
    monkeypatch.setattr(bv, "build_validation_windows", lambda dates: WINDOWS)
    monkeypatch.setattr(bv, "build_replay_consistency_check", lambda result, **kw: {"passed": True})
    monkeypatch.setattr(bv, "build_validation_payload",
                        lambda **kw: _vpayload(kw["full_result"].stock_code))

    real_import = builtins.__import__
    tags = {("src.core.benzong.batch_scorer", "budget_descriptor"): "import:batch_scorer",
            ("src.core.backtest_engine", "BacktestEngine"): "import:engine",
            ("src.core.backtest_validator", "build_batch_validation_payload"): "import:validator"}

    def spy_import(name, globals=None, locals=None, fromlist=(), level=0):
        mod = real_import(name, globals, locals, fromlist, level)
        if fromlist:
            for (mod_name, symbol), tag in tags.items():
                if name == mod_name and symbol in fromlist:
                    r.fire(tag)
        return mod

    monkeypatch.setattr(builtins, "__import__", spy_import)
    return r


def _adv(rig, seconds):
    return lambda: rig.clock.advance(seconds)


def _once(fn):
    done = []

    def wrapper():
        if not done:
            done.append(1)
            fn()

    return wrapper


def _boom(*a, **k):
    raise AssertionError("该路径不得触发业务动作")


# ═══════ 1. 解析层 ═══════

def test_parse_golden_two_cases_verbatim():
    assert start.parse_input("bb 600519,000001") == (
        "batch_backtest", {"codes": ["600519", "000001"]})
    assert start.parse_input("bb 600519,000001 2024-01-01 2024-12-31") == (
        "batch_backtest", {"codes": ["600519", "000001"],
                           "start_date": "2024-01-01", "end_date": "2024-12-31"})


@pytest.mark.parametrize("text,codes", [
    ("bb 600519 000001", ["600519", "000001"]),                 # 空格分隔
    ("bb 600519，000001", ["600519", "000001"]),                 # 中文逗号
    ("bb 600519、000001；000002", ["600519", "000001", "000002"]),  # 顿号/分号
    ("bb 600519,000001 000002", ["600519", "000001", "000002"]),    # 混用
    ("bb 600519,600519 000001", ["600519", "000001"]),          # 去重保序
    ("bb 000001.SZ", ["000001.SZ"]),                            # 后缀保持原字符串
])
def test_parse_code_separators_and_dedup(text, codes):
    assert start.parse_input(text) == ("batch_backtest", {"codes": codes})


def test_parse_single_date_sets_start_only():
    assert start.parse_input("bb 600519 2024-01-01") == (
        "batch_backtest", {"codes": ["600519"], "start_date": "2024-01-01"})


def test_parse_equal_start_end_allowed():
    assert start.parse_input("bb 600519 2024-03-01 2024-03-01") == (
        "batch_backtest", {"codes": ["600519"], "start_date": "2024-03-01",
                           "end_date": "2024-03-01"})


@pytest.mark.parametrize("text,expected", [
    ("bb 600519 --budget 600",
     {"codes": ["600519"], "budget": 600.0}),
    ("bb --budget 600 600519,000001",
     {"codes": ["600519", "000001"], "budget": 600.0}),
    ("bb 600519 000001 --budget=90",
     {"codes": ["600519", "000001"], "budget": 90.0}),
    ("bb 600519 2024-01-01 2024-12-31 --budget 0.5",
     {"codes": ["600519"], "start_date": "2024-01-01", "end_date": "2024-12-31",
      "budget": 0.5}),
    ("BB 600519 --BUDGET 60",
     {"codes": ["600519"], "budget": 60.0}),
    ("bb 600519 --budget 60 2024-01-01",
     {"codes": ["600519"], "start_date": "2024-01-01", "budget": 60.0}),
])
def test_parse_valid_budget_positions(text, expected):
    assert start.parse_input(text) == ("batch_backtest", expected)


def _forbid_business(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append("touched")
        raise AssertionError("拒绝路径不得触碰业务")

    monkeypatch.setattr(session_state, "get_last_scan", boom)
    monkeypatch.setattr(session_state, "resolve_index", boom)
    monkeypatch.setattr(cli_main, "run_batch_validation", boom)
    monkeypatch.setattr(cli_main, "load_config", boom)
    monkeypatch.setattr(engine_mod, "BacktestEngine", boom)
    monkeypatch.setattr(bv, "build_batch_validation_payload", boom)
    return calls


@pytest.mark.parametrize("text", [
    "bb 600519 --budget",
    "bb 600519 --budget=",
    "bb 600519 --budget 60 --budget 90",
    "bb 600519 --budget abc",
    "bb 600519 --budget 0",
    "bb 600519 --budget -5",
    "bb 600519 --budget nan",
    "bb 600519 --budget inf",
    "bb 600519 --budget=60x",
])
def test_parse_bad_budget_rejected_zero_business(text, monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    out = capsys.readouterr().out
    assert "--budget" in out and ("无效" in out or "缺少" in out or "只能给一次" in out), out


def test_parse_unknown_budget_shape_still_rejected(monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input("bb 600519 --budgetx 60") is None
    assert calls == []
    assert "未知预算" in capsys.readouterr().out


@pytest.mark.parametrize("text,reason", [
    ("bb 600519 foo", "foo"),                                    # 无效片段
    ("bb 600519,foo", "foo"),
    ("bb 600519 #1", "#1"),                                      # 不支持 #N
    ("bb 600519 12345", "12345"),                                # 非 6 位代码
    ("bb 2024-01-01", "至少一个股票代码"),                        # 无代码
    ("bb --budget 60", "用法"),                                   # 剥离预算后为空 → 用法
    ("bb 600519 2024-01-01 2024-06-01 2024-12-31", "最多 2 个"),  # 第 3 个日期
    ("bb 600519 2024-12-31 2024-01-01", "晚于"),                  # 起>止
    ("bb 600519 2024-13-01", "2024-13-01"),                      # 非法日期
    ("bb 600519 2024-02-30", "2024-02-30"),
    ("bb 600519 2024-1-1", "2024-1-1"),                          # 非严格格式
    ("bb 600519 20240101", "20240101"),
    ("bb 600519 -x", "-x"),                                      # - 开头非预算 flag
    ("bb 600519 --force", "--force"),
    ("bb 600519,-x", "-x"),
    ("bb 600519 --budget 60 -x", "-x"),
])
def test_parse_shape_rejected_zero_business_with_usage(text, reason, monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    out = capsys.readouterr().out
    assert reason in out, out
    assert USAGE in out, out


def test_parse_bare_bb_prints_usage(monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input("bb") is None
    assert calls == []
    assert "bb <代码1,代码2,...>" in capsys.readouterr().out


# ═══════ 2. run_cli 分发与统一门 ═══════

@pytest.fixture
def spy_batch(monkeypatch):
    seen = []
    monkeypatch.setattr(cli_main, "run_batch_validation", lambda **kw: seen.append(dict(kw)))
    monkeypatch.setattr(cli_main, "run_backtest", _boom)
    monkeypatch.setattr(start, "_no_ai", False)
    monkeypatch.setattr(start, "_ai_debug", False)
    return seen


def test_run_cli_batch_backtest_budget_passes_budget_seconds(spy_batch):
    start.run_cli("batch_backtest", {"codes": ["600519", "000001"],
                                     "start_date": "2024-01-01", "end_date": "2024-12-31",
                                     "budget": 600})
    assert spy_batch == [{"stock_codes": ["600519", "000001"], "start_date": "2024-01-01",
                          "end_date": "2024-12-31", "budget_seconds": 600}]


def test_parse_then_run_cli_chain(spy_batch):
    parsed = start.parse_input("bb 600519 000001 2024-01-01 2024-12-31 --budget=45")
    assert parsed is not None
    start.run_cli(*parsed)
    assert spy_batch == [{"stock_codes": ["600519", "000001"], "start_date": "2024-01-01",
                          "end_date": "2024-12-31", "budget_seconds": 45.0}]


@pytest.mark.parametrize("extra", [{}, {"budget": None}])
def test_run_cli_without_budget_keeps_golden_call_shape(extra, spy_batch):
    args = {"codes": ["600519"], "start_date": "2024-01-01", "end_date": "2024-12-31"}
    args.update(extra)
    start.run_cli("batch_backtest", args)
    assert spy_batch == [{"stock_codes": ["600519"], "start_date": "2024-01-01",
                          "end_date": "2024-12-31"}]
    assert "budget_seconds" not in spy_batch[0]


@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"), pytest.param(True, id="bool"),
])
def test_run_cli_bad_budget_rejected_without_calling(bad, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "run_batch_validation", _boom)
    start.run_cli("batch_backtest", {"codes": ["600519"], "budget": bad})
    out = capsys.readouterr().out
    assert "预算参数无效" in out and "未开始批量回测" in out, out


# ═══════ 3. 共享启动预算件 StartBudget ═══════

def test_start_budget_module_exists():
    assert sb_mod is not None, "src/cli/start_budget.py 必须存在"
    assert hasattr(sb_mod, "StartBudget")


def test_begin_none_returns_none_without_clock(rig):
    budget, err = sb_mod.StartBudget.begin(None)
    assert (budget, err) == (None, None)
    assert rig.clock.reads == []


@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"), pytest.param(True, id="true"), pytest.param(False, id="false"),
])
def test_begin_bad_value_returns_error_text(bad, rig):
    budget, err = sb_mod.StartBudget.begin(bad)
    assert budget is None
    assert isinstance(err, str) and "有限正数秒" in err


def test_begin_valid_reads_clock_before_validation_import(rig):
    budget, err = sb_mod.StartBudget.begin(BUDGET)
    assert err is None and budget is not None
    assert budget.seconds == BUDGET
    assert rig.events.index("clock") < rig.events.index("import:batch_scorer"), \
        "入口时钟必须先于预算验证 import"
    assert len(rig.clock.reads) == 1


def test_expired_reads_fresh_clock_and_equality_counts_as_expired(rig):
    budget, _ = sb_mod.StartBudget.begin(BUDGET)
    assert budget.expired() is False
    rig.clock.advance(599.9)
    assert budget.expired() is False, "差 0.1s 不得到期"
    rig.clock.advance(0.1)
    assert rig.clock.now == T0 + BUDGET
    assert budget.expired() is True, "恰等于截止视为到期"
    rig.clock.advance(5)
    assert budget.expired() is True
    assert len(rig.clock.reads) == 5, "begin 一次 + expired 每次现读一次"


def test_expired_does_not_restart_clock(rig):
    rig.clock.advance(300)
    budget, _ = sb_mod.StartBudget.begin(BUDGET)   # 入口读数 = T0+300
    rig.clock.advance(599.0)
    assert budget.expired() is False, "截止 = 入口读数(T0+300) + 600，未到不得到期"
    rig.clock.advance(1.0)
    assert rig.clock.now == T0 + 300 + BUDGET
    assert budget.expired() is True                 # 非重起钟：恰等于入口读数 + 预算


# ═══════ 4. 程序化入口：坏值零业务 / None 旧路径 ═══════

@pytest.mark.parametrize("bad", [
    pytest.param(True, id="true"), pytest.param(False, id="false"),
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"),
])
def test_programmatic_bad_budget_returns_none_before_any_business(bad, rig):
    result = rig.run(budget_seconds=bad)
    out = rig.out()
    assert result is None
    assert "预算参数无效" in out and "未开始批量回测" in out, out
    assert rig.business() == [], f"坏预算不得 import 回测模块/读配置/回测: {rig.business()}"


def test_programmatic_none_budget_takes_old_path_without_clock(rig):
    payload = rig.run(budget_seconds=None)
    assert rig.clock.reads == [], "无预算不得取时钟"
    for code in CODES:
        assert rig.count(f"run:{code}") == RUNS_PER_STOCK
    assert "not_started" not in payload and "not_started_count" not in payload["summary"]
    assert "启动预算" not in rig.out()


def test_programmatic_default_call_equals_explicit_none(rig):
    payload = rig.run()
    assert rig.clock.reads == []
    assert payload["summary"]["success_count"] == 3


# ═══════ 5. 入口计时：时钟早于回测模块 import，不重起钟 ═══════

def test_entry_clock_before_module_imports(rig):
    rig.run(budget_seconds=BUDGET)
    first_clock = rig.events.index("clock")
    assert first_clock < rig.events.index("import:batch_scorer")
    assert first_clock < rig.events.index("import:engine")
    assert first_clock < rig.events.index("import:validator")


def test_deadline_is_entry_plus_budget_and_never_restarted(rig):
    rig.hooks["import:batch_scorer"] = _adv(rig, 599.0)   # 校验 import 耗掉 599s
    rig.hooks["config"] = _adv(rig, 1.0)                  # 配置后恰到 T0+600
    out_payload = rig.run(budget_seconds=BUDGET)
    out = rig.out()
    # 599s 后仍未到期（入口校验后/回测模块导入后继续），说明截止 = 入口读数 + 预算
    assert rig.count("config") == 1, rig.events
    assert rig.count("construct:") == 0 and rig.count("run:") == 0
    assert "配置读取后" in out, out
    assert out_payload["not_started"] == list(CODES)


# ═══════ 6. 各检查点恰到期 ═══════

def test_cp1_expired_after_entry_validation(rig):
    rig.hooks["import:batch_scorer"] = _adv(rig, BUDGET)
    payload = rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert "import:engine" not in rig.events, "入口校验后到期不得再 import BacktestEngine"
    assert rig.count("config") == 0 and rig.count("construct:") == 0 and rig.count("run:") == 0
    assert "启动预算已到期" in out and "入口校验后" in out, out
    assert payload["not_started"] == list(CODES)
    assert payload["summary"]["not_started_count"] == 3
    assert payload["stocks"] == [] and payload["failures"] == []


def test_cp2_expired_after_backtest_module_import(rig):
    rig.hooks["import:validator"] = _adv(rig, BUDGET)
    payload = rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert "import:engine" in rig.events and "import:validator" in rig.events
    assert rig.count("config") == 0 and rig.count("construct:") == 0
    assert "启动预算已到期" in out and "回测模块导入后" in out, out
    assert payload["not_started"] == list(CODES)


def test_cp3_expired_after_load_config(rig):
    rig.hooks["config"] = _adv(rig, BUDGET)
    payload = rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert rig.count("config") == 1
    assert rig.count("construct:") == 0 and rig.count("run:") == 0
    assert "配置读取后" in out, out
    assert "(1/3) 正在验证" not in out
    assert payload["not_started"] == list(CODES)


def test_cp4_expired_before_first_title(rig):
    # 读数序号：0 入口 1 入口校验后 2 导入后 3 配置后 4 首只标题前
    rig.clock.advance_at_read[4] = BUDGET
    payload = rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert rig.count("config") == 1 and rig.count("construct:") == 0
    assert "(1/3) 正在验证" not in out, "标题前到期不得打印该只标题"
    assert "第 1/3 只开始前" in out, out
    assert payload["not_started"] == list(CODES)


def test_cp5_expired_after_title_before_first_run(rig):
    rig.console.print_hook = (
        lambda text: rig.clock.advance(BUDGET) if "(1/3) 正在验证" in text else None)
    payload = rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert "(1/3) 正在验证 600001" in out, "标题已打印"
    assert rig.count("construct:") == 0 and rig.count("run:") == 0, "标题后到期不得构造/回测"
    assert "第 1/3 只标题后" in out, out
    assert payload["not_started"] == list(CODES), "已打印标题但未开始 run 的那只也算未开始"


@pytest.mark.parametrize("k", [1, 2])
def test_mid_run_deadline_crossing_keeps_current_stock_complete(k, rig):
    """第 k 只 run 途中跨截止：第 k 只全部窗口（全区间/样本内/样本外/walk-forward）完成，
    k+1 起构造/run 0 次。"""
    done, rest = CODES[:k], CODES[k:]
    rig.hooks[f"run:{CODES[k - 1]}"] = _once(_adv(rig, BUDGET))
    payload = rig.run(budget_seconds=BUDGET)
    out = rig.out()
    for code in done:
        assert rig.count(f"construct:{code}") == RUNS_PER_STOCK
        assert rig.count(f"run:{code}") == RUNS_PER_STOCK, f"{code} 必须整只完成"
    for code in rest:
        assert rig.count(f"construct:{code}") == 0, f"{code} 不得构造引擎"
        assert rig.count(f"run:{code}") == 0, f"{code} 不得回测"
    # 已开始的一只中途不再核截止：入口+3 个准备检查点+(k 只 × 2)+下一只标题前 1 次
    assert len(rig.clock.reads) == 4 + 2 * k + 1, rig.clock.reads
    assert [s["stock_code"] for s in payload["stocks"]] == list(done)
    assert payload["failures"] == []
    assert payload["not_started"] == list(rest)
    assert payload["summary"]["not_started_count"] == len(rest)
    assert payload["summary"]["success_count"] == k
    assert f"预算未开始: {len(rest)}" in out
    assert "三·一、预算未开始" in out


def test_engine_never_receives_deadline_or_budget(rig):
    rig.run(budget_seconds=BUDGET)
    assert rig.engine_kwargs, "应构造过引擎"
    for kw in rig.engine_kwargs:
        assert set(kw) == ENGINE_KW_KEYS, f"截止不得传入回测引擎: {set(kw) ^ ENGINE_KW_KEYS}"


def test_just_before_deadline_is_not_expired(rig):
    rig.hooks["config"] = _adv(rig, 599.9)
    payload = rig.run(budget_seconds=BUDGET)
    for code in CODES:
        assert rig.count(f"run:{code}") == RUNS_PER_STOCK, "差 0.1s 到期不得提前停止"
    assert payload["not_started"] == []
    assert "启动预算已到期" not in rig.out()


def test_exact_deadline_equality_counts_as_expired(rig):
    rig.hooks["config"] = _adv(rig, 600.0)   # now 恰等于 T0 + 600
    assert rig.clock.now == T0
    payload = rig.run(budget_seconds=BUDGET)
    assert rig.clock.reads[-1] == T0 + 600.0
    assert rig.count("construct:") == 0 and payload["not_started"] == list(CODES)


def test_fractional_budget_supported(rig):
    rig.hooks["config"] = _adv(rig, 0.5)
    payload = rig.run(budget_seconds=0.5)
    assert rig.count("construct:") == 0 and payload["not_started"] == list(CODES)
    assert "启动预算：0.5 秒" in rig.out()


def test_not_started_uses_normalized_codes(rig):
    rig.hooks["import:batch_scorer"] = _adv(rig, BUDGET)
    payload = rig.run(codes=("1", "2"), budget_seconds=BUDGET)
    assert payload["not_started"] == ["000001", "000002"]
    assert payload["meta"]["requested_codes"] == ["000001", "000002"]


def test_unexpired_budgeted_run_completes_with_empty_not_started(rig):
    payload = rig.run(budget_seconds=BUDGET)
    assert payload["not_started"] == []
    assert payload["summary"]["not_started_count"] == 0
    assert payload["summary"]["success_count"] == 3
    # 有预算时：入口+3 个准备检查点+每只 2 个检查点
    assert len(rig.clock.reads) == 4 + 2 * len(CODES), rig.clock.reads


def test_failed_stock_goes_to_failures_not_not_started(rig):
    rig.fail["600002"] = RuntimeError("取数失败")
    rig.hooks["run:600002"] = _once(_adv(rig, BUDGET))   # 第 2 只中途跨截止
    payload = rig.run(budget_seconds=BUDGET)
    assert [s["stock_code"] for s in payload["stocks"]] == ["600001"]
    assert [f["stock_code"] for f in payload["failures"]] == ["600002"]
    assert payload["not_started"] == ["600003"]
    assert payload["summary"]["failure_count"] == 1


# ═══════ 7. payload / 提示 / 渲染（backtest_validator）═══════

OLD_TOP_KEYS = {"meta", "summary", "stocks", "failures", "batch_hints"}
OLD_META_KEYS = {"requested_codes", "start_date", "end_date", "backtest_mode", "layer_mode",
                 "validation_version"}
OLD_SUMMARY_KEYS = {"requested_count", "success_count", "failure_count", "average_return_pct",
                    "average_excess_return_pct", "positive_return_count", "beat_benchmark_count",
                    "check_overview"}


def _batch(requested, payloads=(), failures=None, **kw):
    return bv.build_batch_validation_payload(
        validation_payloads=list(payloads), requested_codes=list(requested),
        start_date="2024-01-01", end_date="2024-12-31", backtest_mode="framework_strict",
        layer_mode="decision_strategy_execution", failures=failures, **kw)


def test_payload_not_started_none_keeps_old_key_sets():
    p1 = _batch(["600001"], [_vpayload("600001")])
    p2 = _batch(["600001"], [_vpayload("600001")], not_started=None)
    for p in (p1, p2):
        assert set(p) == OLD_TOP_KEYS
        assert set(p["meta"]) == OLD_META_KEYS
        assert set(p["summary"]) == OLD_SUMMARY_KEYS
    assert p1 == p2


def test_payload_with_not_started_list_adds_keys_and_keeps_success_failure_clean():
    payload = _batch(["600001", "600002", "600003"], [_vpayload("600001")],
                     failures=[{"stock_code": "600002", "error": "boom"}],
                     not_started=["600003"])
    assert set(payload) == OLD_TOP_KEYS | {"not_started"}
    assert set(payload["summary"]) == OLD_SUMMARY_KEYS | {"not_started_count"}
    assert payload["not_started"] == ["600003"]
    assert payload["summary"]["not_started_count"] == 1
    assert payload["summary"]["requested_count"] == 3
    assert payload["summary"]["success_count"] == 1 and payload["summary"]["failure_count"] == 1
    assert [s["stock_code"] for s in payload["stocks"]] == ["600001"]
    assert [f["stock_code"] for f in payload["failures"]] == ["600002"]


def test_payload_empty_not_started_list_still_adds_keys():
    payload = _batch(["600001"], [_vpayload("600001")], not_started=[])
    assert payload["not_started"] == [] and payload["summary"]["not_started_count"] == 0


def test_hints_not_started_marked_non_failure_and_coverage_counts_processed():
    payload = _batch(["600001", "600002", "600003"],
                     [_vpayload("600001"), _vpayload("600002")], not_started=["600003"])
    hints = "\n".join(payload["batch_hints"])
    assert "有 1 只因启动预算未开始（非失败）" in hints, hints
    assert "再次 bb 这些代码" in hints and "会重新回测" in hints
    assert "批量验证已覆盖 2 只股票" in hints, "已覆盖句按已处理数而非请求数"
    assert "批量验证已覆盖 3 只股票" not in hints
    assert "失败" not in hints.replace("非失败", "")


def test_hints_all_not_started_has_no_coverage_sentence():
    payload = _batch(["600001", "600002"], [], not_started=["600001", "600002"])
    hints = "\n".join(payload["batch_hints"])
    assert "有 2 只因启动预算未开始（非失败）" in hints
    assert "已覆盖" not in hints


def test_hints_without_not_started_unchanged():
    payload = _batch(["600001", "600002"], [_vpayload("600001"), _vpayload("600002")])
    assert payload["batch_hints"] == [
        "批量验证已覆盖 2 只股票，当前未发现明显共性异常，可继续扩大样本。"]
    failed = _batch(["600001"], [], failures=[{"stock_code": "600001", "error": "x"}])
    assert failed["batch_hints"] == ["有 1 只股票在批量验证中失败，需先补齐数据或排查回测异常。"]


OLD_REPORT = (
    "批量回测验证汇总\n"
    "区间: 2024-01-01 ~ 2024-12-31\n"
    "回测模式: framework_strict | 分层模式: decision_strategy_execution\n"
    "请求股票数: 2 | 成功: 1 | 失败: 1\n"
    "平均收益: +5.00% | 平均超额: +1.50%\n"
    "\n"
    "一、检查通过概览\n"
    "- lookahead: 1/1\n"
    "- replay_consistency: 1/1\n"
    "- in_sample_vs_out_of_sample: 1/1\n"
    "- walk_forward: 1/1\n"
    "\n"
    "二、个股摘要\n"
    "- 600001 | 收益 +5.00% | 超额 +1.50% | 交易 3 | 一致性 通过\n"
    "\n"
    "三、失败记录\n"
    "- 600002: boom\n"
    "\n"
    "四、提示\n"
    "- 有 1 只股票在批量验证中失败，需先补齐数据或排查回测异常。\n"
)


def test_render_without_not_started_key_is_verbatim_old_text():
    payload = _batch(["600001", "600002"], [_vpayload("600001")],
                     failures=[{"stock_code": "600002", "error": "boom"}])
    assert bv._render_batch_validation_report(payload) == OLD_REPORT


def test_render_with_not_started_adds_count_and_section_after_failures():
    payload = _batch(["600001", "600002", "600003", "600004"], [_vpayload("600001")],
                     failures=[{"stock_code": "600002", "error": "boom"}],
                     not_started=["600003", "600004"])
    text = bv._render_batch_validation_report(payload)
    assert "请求股票数: 4 | 成功: 1 | 失败: 1 | 预算未开始: 2" in text, text
    assert "三·一、预算未开始" in text
    assert text.index("三、失败记录") < text.index("三·一、预算未开始") < text.index("四、提示")
    section = text[text.index("三·一、预算未开始"):text.index("四、提示")]
    assert "- 600003" in section and "- 600004" in section
    assert "600002" not in section


def test_render_with_empty_not_started_list_has_no_section():
    payload = _batch(["600001"], [_vpayload("600001")], not_started=[])
    text = bv._render_batch_validation_report(payload)
    assert "三·一、预算未开始" not in text


def test_public_render_wrapper_matches_private():
    payload = _batch(["600001", "600002"], [_vpayload("600001")], not_started=["600002"])
    assert bv.render_batch_validation_report(payload) == bv._render_batch_validation_report(payload)


# ═══════ 8. 屏幕汇总 / 方括号原样 / 可发现提示 ═══════

def test_summary_printed_on_screen_even_without_budget(rig):
    payload = rig.run()
    out = rig.out()
    assert "批量回测验证汇总" in out and "请求股票数: 3" in out, out
    assert "成功: 3" in out
    assert "- 600001 | 收益 +5.00%" in out
    assert payload["summary"]["success_count"] == 3


def test_failure_text_with_brackets_is_printed_verbatim(rig):
    rig.fail["600002"] = RuntimeError("[red]x[/red] [Errno 2] no such file :smile:")
    rig.run()
    out = rig.out()
    assert "- 600002: [red]x[/red] [Errno 2] no such file :smile:" in out, \
        "汇总必须 markup=False 且 emoji=False：异常原文里的方括号/:name: 不得被 rich 解析"


def test_screen_summary_precedes_exports_and_return_value_unchanged(rig, tmp_path):
    json_path = tmp_path / "batch.json"
    txt_path = tmp_path / "batch.txt"
    payload = rig.run(export_batch_validation_json_path=str(json_path),
                      export_batch_validation_txt_path=str(txt_path))
    out = rig.out()
    assert out.index("批量回测验证汇总") < out.index("已导出批量验证 JSON") < out.index("已导出批量验证文本")
    assert json.loads(json_path.read_text(encoding="utf-8")) == payload
    assert txt_path.read_text(encoding="utf-8") == bv._render_batch_validation_report(payload)
    assert payload["summary"]["success_count"] == 3


def test_budgeted_run_prints_semantics_once_without_optional_hint(rig):
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert out.count("启动预算：600 秒") == 1, out
    assert "bb 批量回测验证" in out and "回测模块导入/配置读取都计入" in out
    assert "到期不再开始下一只股票" in out
    assert "含全区间、样本内外与 walk-forward 窗口" in out
    assert "非强制中断" in out and "非费用上限" in out
    assert "可选时限" not in out, "有预算时不得再打印可选提示"
    assert out.index("初始资金") < out.index("启动预算：600 秒"), "语义说明在标题块之后"


def test_no_budget_prints_optional_hint_once_after_title_block(rig):
    rig.run()
    out = rig.out()
    assert out.count("可选时限") == 1, out
    assert "bb <代码,...> --budget 600" in out and "默认不启用" in out
    assert "到期不再开始下一只，当前一只跑完" in out
    assert "启动预算：" not in out
    assert out.index("初始资金") < out.index("可选时限")


def test_early_expiry_still_prints_summary_with_not_started_section(rig):
    rig.hooks["config"] = _adv(rig, BUDGET)
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert "批量回测验证汇总" in out and "预算未开始: 3" in out, out
    assert "三·一、预算未开始" in out
    for code in CODES:
        assert f"- {code}" in out
    assert "非失败" in out


# ═══════ 9. 可发现性：banner / 帮助 / 预算文案 ═══════

def test_banner_bb_row_advertises_budget_within_border(capsys):
    start.show_help()
    out = capsys.readouterr().out
    rows = [ln for ln in out.splitlines() if "bb <代码" in ln]
    assert len(rows) == 1 and "--budget" in rows[0], rows
    assert start._display_width(rows[0]) <= 74, f"帮助行撑破边框: {rows[0]!r}"


def test_budget_help_and_scope_list_bb():
    assert "bb" in start._BUDGET_HELP and "bb" in start._BUDGET_SCOPE
    assert "bb 批量回测验证" in start._BUDGET_HELP
    assert "bb 批量回测验证" in start._BUDGET_SCOPE


def test_bb_fullwidth_digit_date_rejected(monkeypatch, capsys):
    """日期只认 ASCII 数字：全角数字日期不得被当成合法日期透传给回测引擎。"""
    assert start.parse_input("bb 600519 ２０２４-０１-０１") is None
    assert "无法识别的参数" in capsys.readouterr().out
