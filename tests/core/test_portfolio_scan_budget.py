# -*- coding: utf-8 -*-
"""T05-P：REPL 顶层 `scan`/`s`（一键持仓分析 analyze_portfolio）可选启动预算。

锁死语义（plan/fusion/iteration10/DELIVERY_PLAN.md 2026-10-28 合同）：
- 解析：`scan --budget 600` / `s --budget=90` 合法；无预算仍返回 ("scan", {})；坏值
  （缺值/重复/非数/0/负/nan/inf/粘连）在业务前拒绝；剥离预算后仍有其他 token
  （`scan 半导体`、`s 600519`）业务前拒绝并指引 `scan market <规则|主题>`。
- run_cli：只有显式预算才向 analyze_portfolio 多传 budget_seconds；无预算调用形状
  与旧版逐字一致；程序化坏值（0/nan/"60"/True/-1）统一门拒绝且不调用分析。
- 程序化 `analyze_portfolio(..., *, budget_seconds=None)`：None 不取时钟、行为不变；
  非 None 经 budget_descriptor 同门校验，坏值在 PortfolioManager/账户/配置前拒绝。
- 一个 monotonic 绝对截止从函数入口（预算验证 import 之前）起算，不重起钟；检查点：
  入口校验后、业务导入后、清单形成后、配置/编排器后、每只标题前、标题后取数前。
  到期只阻止开始下一只；取数线程一旦启动，该只走完整旧路径（中途不再核截止）。
- 未开始集合只来自调度器切片 positions[k:]，汇总表/操作建议只含已处理项；清单未形成
  即到期不得称空仓；清单不完整只称“已枚举范围内”。
- 无预算：不取时钟，仅多一行可选提示；空清单不提示。

替身边界：PortfolioManager/load_config/build_live_orchestrator/get_stock_data/
AKShareClient/证据/影子替身，时间用假钟（替换 time.monotonic，只记录来自 cli/main.py
的读数）；真实 parse_input / run_cli / analyze_portfolio 被驱动。零网络零 AI 零真实账户。

跑法（公司内网必须经项目未修改的守卫 runner）：
  .venv\\Scripts\\python.exe -B -X utf8 plan/fusion/iteration9/review_test_runner.py \\
      tests/core/test_portfolio_scan_budget.py
"""

import builtins
import io
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
import src.core.analysis_service as analysis_mod
import src.core.benzong.batch_scorer as batch_scorer_mod  # noqa: F401  先于假钟导入
import src.core.shadow_diff as shadow_mod
import src.data.akshare_client as akshare_mod
from src.data.models import SignalType, StockData

T0 = 1000.0
BUDGET = 600.0          # 默认截止 = T0 + 600 = 1600
CODES = ("600001", "600002", "600003")
NAMES = {"600001": "甲股", "600002": "乙股", "600003": "丙股"}


# ── 假钟：只记录来自 cli/main.py 的读数，其余调用方（rich/线程等）照常读 now ──

def _caller_is_cli_main() -> bool:
    frame = sys._getframe(2)   # 0=本函数 1=Clock.__call__ 2=真正的调用方
    return frame.f_code.co_filename.replace("\\", "/").endswith("src/cli/main.py")


class Clock:
    def __init__(self, events, t=T0):
        self.now = float(t)
        self.events = events
        self.reads = []                # 来自 cli/main.py 的读数序列
        self.advance_at_read = {}      # 第 n 次(0 基)读数前先推进的秒数

    def __call__(self):
        if _caller_is_cli_main():
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


def _pos(code):
    return SimpleNamespace(stock_code=code, stock_name=NAMES.get(code), entry_price=10.0,
                           high_since_entry=None, trade_plan=None, current_ratio=0.1,
                           from_ledger=False)


class _Facts:
    nav = 1000000.0
    nav_day = "2026-10-28"

    def __init__(self, rig):
        self._rig = rig

    def holding_entries(self):
        return list(self._rig.entries), self._rig.incomplete

    def context_for(self, code, price=None, price_as_of=None):
        return SimpleNamespace(position_state="HELD", trade_plan=None,
                               confirmed_weight=0.1, account_version="v-test")

    def storage_key_for(self, code):
        return code


class _Orch:
    def __init__(self, rig):
        self._rig = rig

    def analyze(self, stock_data, **kwargs):
        self._rig.fire(f"analyze:{stock_data.stock_code}")
        decision = SimpleNamespace(decision=SignalType.HOLD)
        strategy = SimpleNamespace(
            position_action=SimpleNamespace(value="HOLD_POSITION"),
            action_semantic="持有观察", entry_exit=None, divergence=None)
        return decision, strategy, None, None


class _FakeAK:
    @staticmethod
    def get_realtime_quote(code):
        return None


class Rig:
    """analyze_portfolio 的全替身运行台：事件序列 + 假钟 + 钩子 + 输出缓冲。"""

    def __init__(self):
        self.events = []
        self.clock = Clock(self.events)
        self.hooks = {}
        self.entries = [_pos(c) for c in CODES]
        self.incomplete = False
        self.buf = io.StringIO()
        self.console = HookConsole(file=self.buf, width=10000)

    def fire(self, name):
        self.events.append(name)
        hook = self.hooks.get(name)
        if hook is not None:
            hook()

    def business(self):
        return [e for e in self.events if e != "clock" and not e.startswith("import:")]

    def count(self, prefix):
        return sum(1 for e in self.events if e.startswith(prefix))

    def out(self):
        return self.buf.getvalue()

    def run(self, **kwargs):
        cli_main.analyze_portfolio(**kwargs)
        return self.out()

    # —— 替身 ——
    def make_pm(self):
        rig = self

        class _PM:
            def __init__(self):
                rig.fire("pm_init")

            def request_account_facts(self, estimate_nav=False):
                rig.fire("facts")
                return _Facts(rig)

            def to_strategy_state(self, code, **kwargs):
                return SimpleNamespace(current_position_ratio=0.1)

            def record_analysis_observation(self, *a, **k):
                return True

            def record_proposal(self, *a, **k):
                return None

        return _PM

    def fake_load_config(self, config_path=None):
        self.fire("config")
        return {"skills": {"enabled": [], "dir": "skills"}, "decision": {}, "ai": None}

    def fake_build(self, config, rag_service=None):
        self.fire("orch")
        return _Orch(self)

    def fake_get_stock_data(self, code):
        self.fire(f"fetch:{code}")
        return StockData(stock_code=code, stock_name="测试股", price=10.0, volume=100000,
                         change_pct=1.0, avg_volume_20=100000.0,
                         ma5=9.8, ma20=9.5, ma60=9.0)

    def fake_logout(self):
        self.fire("logout")


@pytest.fixture
def rig(monkeypatch):
    r = Rig()
    monkeypatch.setattr(time, "monotonic", r.clock)
    monkeypatch.setattr(cli_main, "PortfolioManager", r.make_pm())
    monkeypatch.setattr(cli_main, "load_config", r.fake_load_config)
    monkeypatch.setattr(cli_main, "build_live_orchestrator", r.fake_build)
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    monkeypatch.setattr(cli_main, "console", r.console)
    monkeypatch.setattr(akshare_mod, "get_stock_data", r.fake_get_stock_data)
    monkeypatch.setattr(akshare_mod, "AKShareClient", _FakeAK)
    monkeypatch.setattr(akshare_mod, "_baostock_logout", r.fake_logout)
    monkeypatch.setattr(analysis_mod, "build_decision_packet", lambda *a, **k: None)
    monkeypatch.setattr(shadow_mod, "capture_shadow", lambda *a, **k: None)
    monkeypatch.setattr(cli_main._evidence, "record_evidence", lambda *a, **k: None)

    real_import = builtins.__import__
    tags = {("src.core.benzong.batch_scorer", "budget_descriptor"): "import:batch_scorer",
            ("src.data.akshare_client", "get_stock_data"): "import:akshare"}

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


def _boom(*a, **k):
    raise AssertionError("该路径不得触发业务动作")


# ═══════ 1. 解析层 ═══════

@pytest.mark.parametrize("text,seconds", [
    ("scan --budget 600", 600.0),
    ("s --budget=90", 90.0),
    ("scan --budget 0.5", 0.5),
    ("SCAN --BUDGET 60", 60.0),
])
def test_parse_valid_budget(text, seconds):
    assert start.parse_input(text) == ("scan", {"budget": seconds})


def test_parse_no_budget_keeps_old_shape():
    assert start.parse_input("scan") == ("scan", {})
    assert start.parse_input("s") == ("scan", {})


def _forbid_business(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append("touched")
        raise AssertionError("拒绝路径不得触碰业务")

    monkeypatch.setattr(session_state, "get_last_scan", boom)
    monkeypatch.setattr(session_state, "resolve_index", boom)
    monkeypatch.setattr(cli_main, "analyze_portfolio", boom)
    monkeypatch.setattr(cli_main, "PortfolioManager", boom)
    monkeypatch.setattr(cli_main, "load_config", boom)
    monkeypatch.setattr(akshare_mod, "get_stock_data", boom)
    return calls


@pytest.mark.parametrize("text", [
    "scan --budget",
    "scan --budget=",
    "scan --budget 60 --budget 90",
    "scan --budget abc",
    "scan --budget 0",
    "scan --budget -5",
    "scan --budget nan",
    "scan --budget inf",
    "scan --budget=60x",
    "s --budget abc",
])
def test_parse_bad_budget_rejected_zero_business(text, monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    out = capsys.readouterr().out
    assert "--budget" in out and ("无效" in out or "缺少" in out or "只能给一次" in out), out


@pytest.mark.parametrize("text", [
    "scan 半导体",
    "scan foo --budget 60",
    "s 600519",
    "scan --budget 60 market",
    "s AI,半导体",
])
def test_parse_extra_tokens_rejected_with_market_guidance(text, monkeypatch, capsys):
    """顶层 scan 是一键持仓分析：不再静默忽略主题词跑全部持仓 AI 分析。"""
    calls = _forbid_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    out = capsys.readouterr().out
    assert "scan market" in out, out
    assert "本次未执行" in out, out


def test_parse_unknown_budget_shape_still_rejected(monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input("scan --budgetx 60") is None
    assert calls == []
    assert "未知预算" in capsys.readouterr().out


def test_parse_market_and_review_paths_unchanged(monkeypatch, capsys):
    assert start.parse_input("scan market 健康回调")[0] == "scan_market"
    assert start.parse_input("scan review 14") == ("scan_review", {"days": 14})
    assert start.parse_input("scan review --budget 60") is None
    assert "不支持 --budget" in capsys.readouterr().out


# ═══════ 2. run_cli 分发与统一门 ═══════

@pytest.fixture
def spy_portfolio(monkeypatch):
    seen = []
    monkeypatch.setattr(cli_main, "analyze_portfolio", lambda **kw: seen.append(dict(kw)))
    monkeypatch.setattr(start, "_no_ai", False)
    monkeypatch.setattr(start, "_ai_debug", False)
    return seen


def test_run_cli_scan_budget_passes_budget_seconds(spy_portfolio):
    start.run_cli("scan", {"budget": 600})
    assert spy_portfolio == [{"ai_overrides": {}, "ai_debug": False, "budget_seconds": 600}]


def test_parse_then_run_cli_chain(spy_portfolio):
    parsed = start.parse_input("scan --budget=45")
    assert parsed is not None
    start.run_cli(*parsed)
    assert spy_portfolio == [{"ai_overrides": {}, "ai_debug": False, "budget_seconds": 45.0}]


@pytest.mark.parametrize("args", [{}, {"budget": None}])
def test_run_cli_scan_without_budget_keeps_old_call_shape(args, spy_portfolio):
    start.run_cli("scan", dict(args))
    assert spy_portfolio == [{"ai_overrides": {}, "ai_debug": False}]
    assert "budget_seconds" not in spy_portfolio[0]


@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"), pytest.param(True, id="bool"),
])
def test_run_cli_scan_bad_budget_rejected_without_calling(bad, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "analyze_portfolio", _boom)
    start.run_cli("scan", {"budget": bad})
    out = capsys.readouterr().out
    assert "预算参数无效" in out and "未开始持仓扫描" in out, out


# ═══════ 3. 程序化入口：坏值零业务 / None 旧路径 ═══════

@pytest.mark.parametrize("bad", [
    pytest.param(True, id="true"), pytest.param(False, id="false"),
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"),
])
def test_programmatic_bad_budget_rejected_before_any_business(bad, rig):
    out = rig.run(budget_seconds=bad)
    assert "预算参数无效" in out and "未开始持仓扫描" in out, out
    assert rig.business() == [], f"坏预算不得触达账户/取数/配置: {rig.business()}"


def test_programmatic_none_budget_takes_old_path_without_clock(rig):
    out = rig.run(budget_seconds=None)
    assert rig.clock.reads == [], "无预算不得取时钟"
    for code in CODES:
        assert f"analyze:{code}" in rig.events
    assert "持仓扫描汇总" in out and "启动预算" not in out


# ═══════ 4. 入口计时：时钟早于验证 import，不重起钟 ═══════

def test_entry_clock_before_validation_import_and_no_restart(rig):
    rig.hooks["import:batch_scorer"] = _adv(rig, 599.0)   # 验证 import 耗掉 599s
    rig.hooks["facts"] = _adv(rig, 1.0)                    # 清单形成后恰到 T0+600
    out = rig.run(budget_seconds=BUDGET)
    assert rig.events.index("clock") < rig.events.index("import:batch_scorer"), \
        "入口 monotonic 读数必须早于预算验证 import"
    # 599s 后仍未到期（入口校验后/业务导入后继续），说明截止 = 入口读数 + 预算
    assert "pm_init" in rig.events and "facts" in rig.events
    # 恰等于 deadline 视为到期：清单形成后停止
    assert "持仓清单形成后" in out, out
    assert rig.count("config") == 0 and rig.count("orch") == 0 and rig.count("fetch:") == 0


# ═══════ 5. 各检查点恰到期 ═══════

def test_cp1_expired_after_entry_validation(rig):
    rig.hooks["import:batch_scorer"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert rig.business() == [], rig.business()
    assert "启动预算已到期" in out and "入口校验后" in out, out
    assert "不代表空仓" in out and "当前无持仓" not in out, out


def test_cp2_expired_after_business_import(rig):
    rig.hooks["import:akshare"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert rig.business() == [], "业务导入后到期不得构造 PortfolioManager/读账户"
    assert "启动预算已到期" in out and "业务模块导入后" in out, out
    assert "不代表空仓" in out and "当前无持仓" not in out, out


def test_cp3_expired_after_list_formed_marks_all_not_started(rig):
    rig.hooks["facts"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert rig.count("pm_init") == 1 and rig.count("facts") == 1
    assert rig.count("config") == 0 and rig.count("orch") == 0
    assert rig.count("fetch:") == 0 and rig.count("analyze:") == 0
    assert "持仓扫描模式" in out and "共 3 只持仓股" in out
    assert "持仓清单形成后" in out, out
    assert "启动预算未开始 3 只：600001 甲股、600002 乙股、600003 丙股" in out, out
    assert "本次没有完成分析的持仓" in out and "不是分析失败" in out, out
    assert "AI情绪" not in out and "操作建议:" not in out, "全部未开始不得打印空汇总表/操作建议行"
    assert "当前无持仓" not in out


def test_cp4_expired_after_orchestrator_built(rig):
    rig.hooks["orch"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert rig.count("config") == 1 and rig.count("orch") == 1
    assert rig.count("fetch:") == 0 and rig.count("analyze:") == 0
    assert "配置与编排器准备后" in out, out
    assert "启动预算未开始 3 只" in out and "不是分析失败" in out, out


def test_cp5a_expired_before_first_title(rig):
    # 读数序号：0 入口 1 入口校验后 2 导入后 3 清单后 4 编排器后 5 首只标题前
    rig.clock.advance_at_read[5] = BUDGET
    out = rig.run(budget_seconds=BUDGET)
    assert rig.count("orch") == 1 and rig.count("fetch:") == 0
    assert "─── [1/3]" not in out, "标题前到期不得打印该只标题"
    assert "第 1/3 只开始前" in out, out
    assert "启动预算未开始 3 只" in out, out


def test_cp5b_expired_after_first_title_before_fetch(rig):
    rig.console.print_hook = (
        lambda text: rig.clock.advance(BUDGET) if "[1/3]" in text else None)
    out = rig.run(budget_seconds=BUDGET)
    assert "[1/3] 甲股 (600001)" in out, "标题已打印"
    assert rig.count("fetch:") == 0 and rig.count("analyze:") == 0, "标题后到期不得取数"
    assert "第 1/3 只标题后" in out, out
    assert "启动预算未开始 3 只：600001 甲股、600002 乙股、600003 丙股" in out, out


@pytest.mark.parametrize("hook_event", ["fetch", "analyze"])
@pytest.mark.parametrize("k", [1, 2])
def test_mid_run_deadline_crossing_keeps_current_stock_complete(k, hook_event, rig):
    """第 k 只取数/分析途中跨截止：第 k 只走完整旧路径，k+1 起零取数零分析。"""
    done, rest = CODES[:k], CODES[k:]
    rig.hooks[f"{hook_event}:{CODES[k - 1]}"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    for code in done:
        assert f"fetch:{code}" in rig.events and f"analyze:{code}" in rig.events
    for code in rest:
        assert f"fetch:{code}" not in rig.events, f"{code} 不得取数"
        assert f"analyze:{code}" not in rig.events, f"{code} 不得分析"
    # 已启动的一只中途不再核截止：入口+4 个准备检查点+(k 只 × 2)+下一只标题前 1 次
    assert len(rig.clock.reads) == 5 + 2 * k + 1, rig.clock.reads
    assert rig.count("logout") == 1
    summary = out[out.index("持仓扫描汇总"):out.index("启动预算未开始")]
    for code in done:
        assert code in summary, f"已处理 {code} 必须在汇总表"
    for code in rest:
        assert code not in summary, f"未开始 {code} 不得进汇总表"
    assert f"持仓×{k}" in summary, "操作建议只计已处理项"
    rest_text = "、".join(f"{c} {NAMES[c]}" for c in rest)
    assert f"启动预算未开始 {len(rest)} 只：{rest_text}" in out, out
    assert "不计失败" in out and "不自动续跑" in out, out


def test_just_before_deadline_is_not_expired(rig):
    rig.hooks["facts"] = _adv(rig, 599.9)
    out = rig.run(budget_seconds=BUDGET)
    assert rig.count("analyze:") == 3, "差 0.1s 到期不得提前停止"
    assert "启动预算未开始" not in out


def test_exact_deadline_equality_counts_as_expired(rig):
    rig.hooks["orch"] = _adv(rig, 600.0)   # now 恰等于 T0 + 600
    assert rig.clock.now == T0
    out = rig.run(budget_seconds=BUDGET)
    assert rig.clock.reads[-1] == T0 + 600.0
    assert rig.count("fetch:") == 0 and "启动预算未开始 3 只" in out


def test_fractional_budget_supported(rig):
    rig.hooks["orch"] = _adv(rig, 0.5)
    out = rig.run(budget_seconds=0.5)
    assert rig.count("fetch:") == 0 and "启动预算未开始 3 只" in out
    assert "启动预算：0.5 秒" in out


# ═══════ 6. 清单口径：空清单 / 未形成 / 不完整 ═══════

def test_empty_list_keeps_original_text_even_if_budget_expired(rig):
    rig.entries = []
    rig.hooks["facts"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert "当前无持仓记录" in out, out
    assert "启动预算已到期" not in out and "启动预算未开始" not in out
    assert rig.count("config") == 0 and rig.count("fetch:") == 0


def test_empty_incomplete_list_keeps_reconcile_text(rig):
    rig.entries = []
    rig.incomplete = True
    out = rig.run(budget_seconds=BUDGET)
    assert "持仓清单读取异常" in out and "待对账" in out
    assert "当前无持仓记录" not in out


def test_list_not_formed_expiry_never_claims_empty_or_all_not_started(rig):
    rig.hooks["import:akshare"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert "当前无持仓" not in out
    assert "不代表空仓" in out and "持仓名单未知" in out, out
    assert "启动预算未开始" not in out, "清单未形成不得列出未开始代码"


def test_incomplete_list_not_started_only_enumerated_scope(rig):
    rig.incomplete = True
    rig.hooks["facts"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert "已枚举范围" in out and "未枚举的持仓不在此列" in out and "待对账" in out, out


def test_complete_list_not_started_has_no_enumerated_scope_note(rig):
    rig.hooks["facts"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert "启动预算未开始 3 只" in out
    assert "已枚举范围" not in out


# ═══════ 7. 汇总口径 ═══════

def test_partial_not_started_not_in_results_or_failure_counts(rig):
    rig.hooks["fetch:600001"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert "数据失败" not in out and "无指标" not in out, "未开始不得被当作失败/无指标行"
    assert out.count("600002") == 1 and out.count("600003") == 1, \
        "未开始代码只出现在未开始块（无标题、无汇总行）"


def test_no_budget_complete_run_matches_never_expiring_budget(rig, monkeypatch):
    out_plain = rig.run()
    events_plain = rig.business()
    assert rig.clock.reads == []

    rig2 = Rig()
    monkeypatch.setattr(time, "monotonic", rig2.clock)
    monkeypatch.setattr(cli_main, "PortfolioManager", rig2.make_pm())
    monkeypatch.setattr(cli_main, "load_config", rig2.fake_load_config)
    monkeypatch.setattr(cli_main, "build_live_orchestrator", rig2.fake_build)
    monkeypatch.setattr(cli_main, "console", rig2.console)
    monkeypatch.setattr(akshare_mod, "get_stock_data", rig2.fake_get_stock_data)
    monkeypatch.setattr(akshare_mod, "_baostock_logout", rig2.fake_logout)
    out_budget = rig2.run(budget_seconds=10 ** 6)
    assert rig2.business() == events_plain, "永不到期的预算必须与无预算同一业务序列"

    def core(text):
        return [ln for ln in text.splitlines()
                if not any(k in ln for k in ("启动预算", "可选时限", "盘中模式", "盘后模式"))]

    assert core(out_plain) == core(out_budget)


# ═══════ 8. 可发现性：预算说明 / 无预算提示 / banner / 提示文案 ═══════

def test_budgeted_run_prints_semantics_once_without_optional_hint(rig):
    out = rig.run(budget_seconds=BUDGET)
    assert out.count("启动预算：600 秒") == 1, out
    assert "scan 持仓扫描" in out and "入口" in out and "账户快照" in out
    assert "非强制中断" in out and "非费用上限" in out and "当前一只会跑完" in out
    assert "可选时限" not in out, "有预算时不得再打印可选提示"


def test_no_budget_prints_optional_hint_once_after_count_line(rig):
    out = rig.run()
    assert out.count("可选时限") == 1, out
    assert "scan --budget 600" in out and "默认不启用" in out
    assert "非强制中断" in out and "非费用上限" in out
    assert "启动预算：" not in out
    assert out.index("共 3 只持仓股，开始逐个分析...") < out.index("可选时限")


def test_no_budget_empty_list_has_no_hint(rig):
    rig.entries = []
    out = rig.run()
    assert "当前无持仓记录" in out and "可选时限" not in out and "--budget" not in out


def test_help_and_hints_advertise_scan_budget(capsys):
    start.show_help()
    out = capsys.readouterr().out
    rows = [ln for ln in out.splitlines() if "scan [s]" in ln]
    assert len(rows) == 1 and "--budget" in rows[0], rows
    assert start._display_width(rows[0]) <= 74, f"帮助行撑破边框: {rows[0]!r}"
    assert "scan 持仓扫描" in start._BUDGET_HELP
    assert "scan 持仓扫描" in start._BUDGET_SCOPE
    hint = start._COMMAND_HINTS["scan"]
    assert "scan <主题>" not in hint
    assert "--budget" in hint and "scan market" in hint


# ═══════ 8. 复核 P2 追补：预算写在子命令前的指引 / scan review 范围 / 异常后到期 ═══════

def test_parse_budget_before_market_points_to_supported_form(monkeypatch, capsys):
    """`scan --budget 60 market deep`：指引必须指向真正支持预算的写法，不让用户二次被拒。"""
    calls = _forbid_business(monkeypatch)
    assert start.parse_input("scan --budget 60 market deep") is None
    assert calls == []
    out = capsys.readouterr().out
    assert "scan market deep [规则|主题] --budget 600" in out, out
    assert "普通 scan market 不支持预算" in out, out


def test_parse_budget_before_review_says_review_unsupported(monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input("s --budget=60 review") is None
    assert calls == []
    out = capsys.readouterr().out
    assert "scan review 复盘不支持 --budget" in out, out
    assert "全市场主题扫描请用" not in out, out


def test_scan_review_budget_rejection_reflects_current_scope(monkeypatch, capsys):
    """T05-P 之后预算范围含 scan 持仓扫描：scan review 拒绝语不得再说只限 scan market deep。"""
    calls = _forbid_business(monkeypatch)
    assert start.parse_input("scan review --budget 60") is None
    assert calls == []
    out = capsys.readouterr().out
    assert "scan review 不支持 --budget" in out and "scan 持仓扫描" in out, out


def test_fetch_failure_then_expiry_keeps_failed_row_and_rest_not_started(rig):
    """第 1 只取数抛异常（降级行情也无）后到期：该只记数据失败，其余列未开始。"""
    def _fail_after_advance():
        rig.clock.advance(BUDGET)
        raise RuntimeError("取数失败（测试）")

    rig.hooks[f"fetch:{CODES[0]}"] = _fail_after_advance
    out = rig.run(budget_seconds=BUDGET)
    assert rig.count("fetch:") == 1 and rig.count("analyze:") == 0
    assert rig.count("logout") == 1
    summary = out[out.index("持仓扫描汇总"):out.index("启动预算未开始")]
    assert CODES[0] in summary and "数据失败" in summary, summary
    for code in CODES[1:]:
        assert code not in summary
    assert "启动预算未开始 2 只" in out, out


def test_analyze_failure_then_expiry_keeps_failed_row_and_rest_not_started(rig):
    """第 1 只分析抛异常后到期：显示分析失败，其余列未开始，不计入操作建议。"""
    def _fail_after_advance():
        rig.clock.advance(BUDGET)
        raise RuntimeError("分析失败（测试）")

    rig.hooks[f"analyze:{CODES[0]}"] = _fail_after_advance
    out = rig.run(budget_seconds=BUDGET)
    assert rig.count("fetch:") == 1 and rig.count("analyze:") == 1
    assert "分析失败" in out, out
    summary = out[out.index("持仓扫描汇总"):out.index("启动预算未开始")]
    assert CODES[0] in summary and "操作建议" not in summary, summary
    for code in CODES[1:]:
        assert code not in summary
    assert "启动预算未开始 2 只" in out, out


def test_incomplete_list_all_not_started_says_enumerated_only(rig):
    """清单不完整且全部未开始：总述只称已枚举的持仓，不说“全部”。"""
    rig.incomplete = True
    rig.hooks["facts"] = _adv(rig, BUDGET)
    out = rig.run(budget_seconds=BUDGET)
    assert "已枚举的持仓均为预算未开始" in out and "清单不完整" in out, out
    assert "全部为预算未开始" not in out, out


# ═══════ 9. ISS-135：分析成功后逐只展示抛异常，汇总表不得重复该股 ═══════

def test_iss135_display_failure_after_analyze_keeps_single_summary_row(rig):
    raised = []

    def hook(text):
        if "决策:" in text and not raised:
            raised.append(text)
            raise RuntimeError("展示异常（测试）")

    rig.console.print_hook = hook
    out = rig.run()
    assert raised, "钩子必须在逐只展示行触发"
    assert rig.count("analyze:") == 3
    summary = out[out.index("持仓扫描汇总"):]
    assert summary.count(CODES[0]) == 1, f"汇总表重复行: {summary}"
    assert "分析失败" not in out, "分析已成功，展示异常不得报成分析失败"
    assert "展示异常" in out and "分析已完成" in out, out
    assert "持仓×3" in summary, "已完成分析的一只仍计入操作建议"
