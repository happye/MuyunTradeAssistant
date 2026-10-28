# -*- coding: utf-8 -*-
"""T05-Q：REPL `pos plan all`（manage_positions 的 plan 批量分支）结果三态 + 可选启动预算。

锁死语义（plan/fusion/iteration10/DELIVERY_PLAN.md 2026-10-28 T05-Q 合同）：
- 结果三态：`_plan_outcome` 返回普通 Enum PlanOutcome（SUCCESS/DECLINED/FAILED）；用户答 N
  （“未接受，原计划继续有效”）是 DECLINED，不是 FAILED；`_generate_or_update_plan` 保留为
  bool 薄包装（单只路径不变）。批量汇总无未接受/未开始时逐字保持旧文案；declined>0 时在
  跳过之后插入 ` / ↩ N 未接受`；有未开始时另起一行列出代码与计数（不计失败）。
- 解析：`pos plan all [--update] --budget 600`；单只 pos plan 与其他 pos 子命令带预算仍拒绝；
  golden 四条输出逐字不变；run_cli 只有显式预算才向 manage_positions 多传 budget_seconds。
- manage_positions(..., *, budget_seconds=None)：仅 plan all 使用；坏值/非 all 在 PortfolioManager
  构造与取数前拒绝（零业务）；入口一次 monotonic 截止；检查点：校验后、持仓清单读取后、每只
  开始前（含 y/N 等待在内的当前一只整只完成，下一只零调用）；到期只阻止开始下一只。
- 无预算：不取时钟，标题后多一行可选提示，其余逐字不变。

替身边界：PortfolioManager（最小假对象）/ prepare_generate_or_update_plan（plan_update_prep
模块属性）/ builtins.input / console（真实 rich Console 写入 StringIO）；时间用假钟（替换
time.monotonic，只记录来自 cli/main.py 与 cli/start_budget.py 的读数）。零网络零 AI 零真实账户。

跑法（公司内网必须经项目未修改的守卫 runner）：
  .venv\\Scripts\\python.exe plan/fusion/iteration9/review_test_runner.py \\
      tests/core/test_pos_plan_all_budget.py
"""

import builtins
import enum
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
import src.core.benzong.batch_scorer as batch_scorer_mod  # noqa: F401  先于假钟导入
import src.core.trade_plan.plan_update_prep as prep_mod
from src.core.trade_plan.plan_update_prep import (
    PU_DATA_UNAVAILABLE, PU_GENERATE_FAILED, PU_NO_POSITION, PU_READY_NEW, PU_READY_UPDATE,
    PlanGenUpdateOutcome,
)

T0 = 1000.0
BUDGET = 600.0          # 默认截止 = T0 + 600 = 1600
CODES = ("600001", "600002", "600003")
NAMES = {"600001": "甲股", "600002": "乙股", "600003": "丙股"}
OLD_SUMMARY = "批量完成：✓ {ok} 成功 / ⏭ {skip} 跳过 / ✗ {fail} 失败"


# ── 假钟：只记录来自 cli/main.py 与 cli/start_budget.py 的读数 ──

def _caller_is_budget_code() -> bool:
    frame = sys._getframe(2)   # 0=本函数 1=Clock.__call__ 2=真正的调用方
    name = frame.f_code.co_filename.replace("\\", "/")
    return name.endswith("src/cli/main.py") or name.endswith("src/cli/start_budget.py")


class Clock:
    def __init__(self, events, t=T0):
        self.now = float(t)
        self.events = events
        self.reads = []
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


def _plan_obj():
    return SimpleNamespace(mode="qizong", max_hold_days=300, adjustments=[])


def _pos(code, with_plan=False):
    return SimpleNamespace(stock_code=code, stock_name=NAMES[code],
                           trade_plan=_plan_obj() if with_plan else None)


def oc_new(code):
    return PlanGenUpdateOutcome(
        status=PU_READY_NEW, stock_code=code, action_label="生成", plan=_plan_obj(), meta={},
        price=10.0, atr_str="0.50", score_normalized=70.0, benzong_grade="A",
        industry_prosperity=80.0)


def oc_update(code):
    return PlanGenUpdateOutcome(
        status=PU_READY_UPDATE, stock_code=code, action_label="更新", plan=_plan_obj(), meta={},
        old_plan=_plan_obj(), old_stop=9.0, proposed_stop=9.5, price=10.0, atr_str="0.50",
        is_update_path=True, reused_mode="qizong", prosperity_note="bare")


def oc_data_fail(code, label="生成"):
    return PlanGenUpdateOutcome(status=PU_DATA_UNAVAILABLE, stock_code=code,
                                action_label=label, data_error="net down")


class PlanRig:
    """manage_positions('plan', 'all') 的全替身运行台：事件序列 + 假钟 + 钩子 + 输出缓冲。"""

    def __init__(self):
        self.events = []
        self.clock = Clock(self.events)
        self.hooks = {}
        self.positions = [_pos(c) for c in CODES]
        self.outcome_fn = None         # (code, update) -> outcome；None 用默认成功形态
        self.answers = []              # builtins.input 的依次回答
        self.attach_ok = {}
        self.buf = io.StringIO()
        self.console = Console(file=self.buf, width=10000)

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

    def run(self, update=False, **kwargs):
        return cli_main.manage_positions("plan", stock_code="all", update=update, **kwargs)

    # —— 替身 ——
    def make_pm(self):
        rig = self

        class _PM:
            def list_positions(self):
                rig.fire("list")
                return list(rig.positions)

            def has_position(self, code):
                return any(p.stock_code == code for p in rig.positions)

            def get_position(self, code):
                for p in rig.positions:
                    if p.stock_code == code:
                        return p
                return None

            def attach_plan(self, code, plan):
                rig.fire(f"attach:{code}")
                return rig.attach_ok.get(code, True)

        pm = _PM()

        def factory():
            rig.fire("pm_init")
            return pm

        return pm, factory

    def fake_prepare(self, pm, code, update=False, **kwargs):
        self.fire(f"prep:{code}")
        if self.outcome_fn is not None:
            return self.outcome_fn(code, update)
        return oc_update(code) if update else oc_new(code)

    def fake_input(self, prompt=""):
        self.fire("input")
        if not self.answers:
            return ""
        ans = self.answers.pop(0)
        if isinstance(ans, BaseException):
            raise ans
        return ans


@pytest.fixture
def rig(monkeypatch):
    r = PlanRig()
    pm, factory = r.make_pm()
    r.pm = pm
    monkeypatch.setattr(time, "monotonic", r.clock)
    monkeypatch.setattr(cli_main, "PortfolioManager", factory)
    monkeypatch.setattr(cli_main, "console", r.console)
    monkeypatch.setattr(prep_mod, "prepare_generate_or_update_plan", r.fake_prepare)
    monkeypatch.setattr(builtins, "input", r.fake_input)

    real_import = builtins.__import__
    tags = {("src.core.benzong.batch_scorer", "budget_descriptor"): "import:batch_scorer"}

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


# ═══════ 1. 结果三态 PlanOutcome / _plan_outcome / bool 薄包装 ═══════

def test_plan_outcome_is_plain_enum_not_str():
    po = cli_main.PlanOutcome
    assert issubclass(po, enum.Enum) and not issubclass(po, str)
    assert {m.name for m in po} == {"SUCCESS", "DECLINED", "FAILED"}


def test_plan_outcome_new_plan_success(rig):
    assert cli_main._plan_outcome(rig.pm, "600001", update=False) is cli_main.PlanOutcome.SUCCESS
    assert rig.count("attach:600001") == 1
    assert "生成成功" in rig.out()


def test_plan_outcome_update_accepted_with_y(rig):
    rig.answers = ["y"]
    assert cli_main._plan_outcome(rig.pm, "600001", update=True) is cli_main.PlanOutcome.SUCCESS
    assert rig.count("attach:600001") == 1


@pytest.mark.parametrize("answer", ["n", "", "no", "随便"])
def test_plan_outcome_update_declined_is_not_failure(answer, rig):
    rig.answers = [answer]
    assert cli_main._plan_outcome(rig.pm, "600001", update=True) is cli_main.PlanOutcome.DECLINED
    assert rig.count("attach:") == 0, "未接受不得写回"
    assert "未接受，原计划继续有效" in rig.out()


@pytest.mark.parametrize("exc", [EOFError(), KeyboardInterrupt()])
def test_plan_outcome_update_input_interrupted_is_declined(exc, rig):
    rig.answers = [exc]
    assert cli_main._plan_outcome(rig.pm, "600001", update=True) is cli_main.PlanOutcome.DECLINED


def test_plan_outcome_data_unavailable_is_failed(rig):
    rig.outcome_fn = lambda code, update: oc_data_fail(code)
    assert cli_main._plan_outcome(rig.pm, "600001") is cli_main.PlanOutcome.FAILED
    assert "技术面数据拉取失败" in rig.out()


def test_plan_outcome_generate_failed_and_no_position_are_failed(rig):
    rig.outcome_fn = lambda code, update: PlanGenUpdateOutcome(
        status=PU_GENERATE_FAILED, stock_code=code, action_label="生成", generate_error="boom")
    assert cli_main._plan_outcome(rig.pm, "600001") is cli_main.PlanOutcome.FAILED
    rig.outcome_fn = lambda code, update: PlanGenUpdateOutcome(
        status=PU_NO_POSITION, stock_code=code)
    assert cli_main._plan_outcome(rig.pm, "600001") is cli_main.PlanOutcome.FAILED
    assert "无持仓记录" in rig.out()


def test_plan_outcome_attach_failure_is_failed(rig):
    rig.attach_ok["600001"] = False
    assert cli_main._plan_outcome(rig.pm, "600001") is cli_main.PlanOutcome.FAILED
    assert "写回失败" in rig.out()


def test_generate_or_update_plan_keeps_bool_contract(rig):
    assert cli_main._generate_or_update_plan(rig.pm, "600001") is True
    rig.outcome_fn = lambda code, update: oc_data_fail(code)
    assert cli_main._generate_or_update_plan(rig.pm, "600001") is False
    rig.outcome_fn = None
    rig.answers = ["n"]
    assert cli_main._generate_or_update_plan(rig.pm, "600001", update=True) is False
    rig.answers = ["y"]
    assert cli_main._generate_or_update_plan(rig.pm, "600001", update=True) is True


def test_display_text_unchanged_for_update_decline(rig):
    rig.answers = ["n"]
    cli_main._plan_outcome(rig.pm, "600001", update=True)
    out = rig.out()
    assert "更新预览：mode=qizong、期限=300天保持；止损 ¥9.00 → ¥9.50" in out, out
    assert "沿用旧计划 mode=qizong（更新不重跑笨总评分）" in out


# ═══════ 2. 批量汇总三态（无预算）═══════

def test_batch_summary_keeps_old_text_when_no_declined_no_not_started(rig):
    rig.positions = [_pos("600001", with_plan=True), _pos("600002"), _pos("600003")]
    rig.outcome_fn = lambda code, update: oc_new(code) if code == "600002" else oc_data_fail(code)
    rig.run(update=False)
    out = rig.out()
    assert OLD_SUMMARY.format(ok=1, skip=1, fail=1) in out, out
    assert "未接受" not in out.replace("未接受，原计划继续有效", "")
    assert "未开始" not in out
    assert "已有计划，跳过" in out


def test_batch_update_declined_counted_separately_not_as_failure(rig):
    rig.positions = [_pos(c, with_plan=True) for c in CODES]

    def outcomes(code, update):
        return oc_data_fail(code, "更新") if code == "600003" else oc_update(code)

    rig.outcome_fn = outcomes
    rig.answers = ["y", "n"]
    rig.run(update=True)
    out = rig.out()
    assert "批量完成：✓ 1 成功 / ⏭ 0 跳过 / ↩ 1 未接受 / ✗ 1 失败" in out, out
    assert rig.count("attach:") == 1


def test_batch_update_all_declined_reports_zero_failures(rig):
    rig.positions = [_pos(c, with_plan=True) for c in CODES]
    rig.answers = ["n", "n", "n"]
    rig.run(update=True)
    out = rig.out()
    assert "批量完成：✓ 0 成功 / ⏭ 0 跳过 / ↩ 3 未接受 / ✗ 0 失败" in out, out


def test_batch_no_budget_takes_old_path_without_clock_and_prints_hint_once(rig):
    rig.run(update=False)
    out = rig.out()
    assert rig.clock.reads == [], "无预算不得取时钟"
    assert OLD_SUMMARY.format(ok=3, skip=0, fail=0) in out
    assert out.count("可选时限") == 1, out
    assert "可选时限：pos plan all [--update] --budget 600" in out
    assert "默认不启用" in out and "到期不再开始下一只，当前一只跑完" in out
    assert "启动预算：" not in out
    assert out.index("批量生成交易计划") < out.index("可选时限") < out.index("▶ 600001")


def test_batch_empty_positions_unchanged_without_hint(rig):
    rig.positions = []
    rig.run()
    out = rig.out()
    assert "当前无持仓记录" in out and "可选时限" not in out and "批量完成" not in out


# ═══════ 3. 解析层 ═══════

@pytest.mark.parametrize("text,expected", [
    ("pos plan all --budget 600", {"stock_code": "all", "update": False, "budget": 600.0}),
    ("pos plan all --update --budget=90",
     {"stock_code": "all", "update": True, "budget": 90.0}),
    ("pos plan all --budget 90 --update",
     {"stock_code": "all", "update": True, "budget": 90.0}),
    ("pos plan all -u --budget 5", {"stock_code": "all", "update": True, "budget": 5.0}),
    ("pos plan all --u --budget 0.5", {"stock_code": "all", "update": True, "budget": 0.5}),
    ("pos p all --budget 60", {"stock_code": "all", "update": False, "budget": 60.0}),
    ("POS PLAN ALL --BUDGET 60", {"stock_code": "ALL", "update": False, "budget": 60.0}),
    ("pos plan * --budget 60", {"stock_code": "*", "update": False, "budget": 60.0}),
    ("pos plan 全部 --budget 60", {"stock_code": "全部", "update": False, "budget": 60.0}),
])
def test_parse_valid_plan_all_budget(text, expected):
    assert start.parse_input(text) == ("pos_plan", expected)


def test_parse_golden_four_cases_verbatim():
    assert start.parse_input("pos plan") is None
    assert start.parse_input("pos plan 600519") == (
        "pos_plan", {"stock_code": "600519", "update": False})
    assert start.parse_input("pos plan 600519 --update") == (
        "pos_plan", {"stock_code": "600519", "update": True})
    assert start.parse_input("pos plan all") == (
        "pos_plan", {"stock_code": "all", "update": False})
    assert start.parse_input("pos plan all --update") == (
        "pos_plan", {"stock_code": "all", "update": True})


def _forbid_business(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append("touched")
        raise AssertionError("拒绝路径不得触碰业务")

    monkeypatch.setattr(session_state, "get_last_scan", boom)
    monkeypatch.setattr(session_state, "resolve_index", boom)
    monkeypatch.setattr(cli_main, "manage_positions", boom)
    monkeypatch.setattr(cli_main, "PortfolioManager", boom)
    monkeypatch.setattr(prep_mod, "prepare_generate_or_update_plan", boom)
    return calls


@pytest.mark.parametrize("text", [
    "pos plan all --budget",
    "pos plan all --budget=",
    "pos plan all --budget 60 --budget 90",
    "pos plan all --budget abc",
    "pos plan all --budget 0",
    "pos plan all --budget -5",
    "pos plan all --budget nan",
    "pos plan all --budget inf",
    "pos plan all --budget=60x",
])
def test_parse_bad_budget_rejected_zero_business(text, monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    out = capsys.readouterr().out
    assert "--budget" in out and ("无效" in out or "缺少" in out or "只能给一次" in out), out


def test_parse_unknown_budget_shape_still_rejected(monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input("pos plan all --budgetx 60") is None
    assert calls == []
    assert "未知预算" in capsys.readouterr().out


@pytest.mark.parametrize("text", [
    "pos plan 600519 --budget 60",
    "pos plan 600519 --update --budget 60",
    "pos p 600519 --budget=60",
    "pos plan --budget 60",
])
def test_parse_single_plan_with_budget_rejected(text, monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    assert "不支持 --budget" in capsys.readouterr().out


@pytest.mark.parametrize("text", [
    "pos --budget 60",
    "pos list --budget 60",
    "pos ls --budget 60",
    "pos add 600519 --budget 60",
    "pos rm 600519 --budget 60",
    "pos confirm 600519 --budget 60",
    "pos verify 600519 --budget 60",
    "pos overweight 600519 --budget 60",
    "pos opening 50000 --budget 60",
])
def test_parse_other_pos_subcommands_with_budget_rejected(text, monkeypatch, capsys):
    calls = _forbid_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    assert "不支持 --budget" in capsys.readouterr().out


def test_parse_pos_without_budget_unchanged():
    assert start.parse_input("pos") == ("pos_list", {})
    assert start.parse_input("pos list") == ("pos_list", {})


# ═══════ 4. run_cli 分发与统一门 ═══════

@pytest.fixture
def spy_manage(monkeypatch):
    seen = []
    monkeypatch.setattr(cli_main, "manage_positions",
                        lambda action, **kw: seen.append((action, dict(kw))))
    monkeypatch.setattr(start, "_no_ai", False)
    monkeypatch.setattr(start, "_ai_debug", False)
    return seen


def test_run_cli_pos_plan_all_budget_passes_budget_seconds(spy_manage):
    start.run_cli("pos_plan", {"stock_code": "all", "update": True, "budget": 600})
    assert spy_manage == [("plan", {"stock_code": "all", "update": True, "budget_seconds": 600})]


@pytest.mark.parametrize("target", ["ALL", "*", "全部"])
def test_run_cli_budget_accepts_all_aliases(target, spy_manage):
    start.run_cli("pos_plan", {"stock_code": target, "update": False, "budget": 30})
    assert spy_manage == [("plan", {"stock_code": target, "update": False,
                                    "budget_seconds": 30})]


def test_parse_then_run_cli_chain(spy_manage):
    parsed = start.parse_input("pos plan all --update --budget=45")
    assert parsed is not None
    start.run_cli(*parsed)
    assert spy_manage == [("plan", {"stock_code": "all", "update": True,
                                    "budget_seconds": 45.0})]


@pytest.mark.parametrize("extra", [{}, {"budget": None}])
def test_run_cli_without_budget_keeps_golden_call_shape(extra, spy_manage):
    args = {"stock_code": "600519", "update": True}
    args.update(extra)
    start.run_cli("pos_plan", args)
    assert spy_manage == [("plan", {"stock_code": "600519", "update": True})]
    assert "budget_seconds" not in spy_manage[0][1]


@pytest.mark.parametrize("bad", [
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"), pytest.param(True, id="bool"),
])
def test_run_cli_bad_budget_rejected_without_calling(bad, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "manage_positions", _boom)
    start.run_cli("pos_plan", {"stock_code": "all", "update": False, "budget": bad})
    out = capsys.readouterr().out
    assert "预算参数无效" in out and "未开始批量计划" in out, out


def test_run_cli_valid_budget_with_single_code_rejected(monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "manage_positions", _boom)
    start.run_cli("pos_plan", {"stock_code": "600519", "update": False, "budget": 60})
    out = capsys.readouterr().out
    assert "不支持 --budget" in out, out


# ═══════ 5. 程序化 manage_positions：坏值/非 all 零业务，None 旧路径 ═══════

@pytest.mark.parametrize("bad", [
    pytest.param(True, id="true"), pytest.param(False, id="false"),
    pytest.param(0, id="zero"), pytest.param(-1, id="neg"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param("60", id="str"),
])
def test_programmatic_bad_budget_rejected_before_any_business(bad, rig):
    rig.run(budget_seconds=bad)
    out = rig.out()
    assert "预算参数无效" in out and "未开始批量计划" in out, out
    assert rig.business() == [], f"坏预算不得构造 PortfolioManager/读清单/取数: {rig.business()}"


def test_programmatic_budget_on_single_code_rejected_before_any_business(rig):
    cli_main.manage_positions("plan", stock_code="600001", update=False, budget_seconds=BUDGET)
    out = rig.out()
    assert "单只 pos plan 不支持 --budget" in out, out
    assert rig.business() == []


def test_programmatic_budget_on_other_action_rejected_before_any_business(rig):
    cli_main.manage_positions("list", budget_seconds=BUDGET)
    out = rig.out()
    assert "不支持 --budget" in out, out
    assert "仅 pos plan all 支持" in out and "单只" not in out, out
    assert rig.business() == []


# ═══════ 6. 预算：入口计时与各检查点恰到期 ═══════

def test_entry_clock_before_validation_import(rig):
    rig.run(budget_seconds=BUDGET)
    assert rig.events.index("clock") < rig.events.index("import:batch_scorer")
    assert rig.events.index("clock") < rig.events.index("pm_init"), "入口钟早于 PortfolioManager 构造"


def test_cp1_expired_after_entry_validation_before_list(rig):
    rig.hooks["import:batch_scorer"] = _adv(rig, BUDGET)
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert rig.business() == [], rig.business()
    assert "启动预算已到期" in out and "入口校验后" in out, out
    assert "未读取持仓清单" in out and "当前无持仓" not in out, out
    assert "批量完成" not in out


def test_cp2_expired_after_list_formed_marks_all_not_started(rig):
    rig.hooks["list"] = _adv(rig, BUDGET)
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert rig.count("list") == 1 and rig.count("prep:") == 0 and rig.count("input") == 0
    assert "3 只持仓" in out and "持仓清单读取后" in out, out
    assert "启动预算未开始 3 只：600001、600002、600003" in out, out
    assert "不计失败" in out and "再次 pos plan all" in out, out


def test_cp3_expired_before_first_start_no_title_no_prep(rig):
    # 读数序号：0 入口 1 入口校验后 2 清单读取后 3 首只开始前
    rig.clock.advance_at_read[3] = BUDGET
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert rig.count("prep:") == 0
    assert "▶ 600001" not in out, "开始前到期不得打印该只标题"
    assert "第 1/3 只开始前" in out, out
    assert "启动预算未开始 3 只：600001、600002、600003" in out, out
    assert "可能含已有计划" in out, out


@pytest.mark.parametrize("k", [1, 2])
def test_mid_run_crossing_keeps_current_complete_and_next_zero_calls(k, rig):
    done, rest = CODES[:k], CODES[k:]
    rig.hooks[f"prep:{CODES[k - 1]}"] = _once(_adv(rig, BUDGET))
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    for code in done:
        assert rig.count(f"prep:{code}") == 1 and rig.count(f"attach:{code}") == 1
    for code in rest:
        assert rig.count(f"prep:{code}") == 0, f"{code} 不得取数/生成"
        assert rig.count(f"attach:{code}") == 0
        assert f"▶ {code}" not in out
    # 已开始的一只中途不再核截止：入口+清单后 +(k 只开始前)+下一只开始前 1 次
    assert len(rig.clock.reads) == 3 + k + 1, rig.clock.reads
    assert f"✓ {k} 成功" in out and "✗ 0 失败" in out, out
    assert f"启动预算未开始 {len(rest)} 只：" + "、".join(rest) in out, out
    assert "不计失败" in out


def test_update_path_yn_wait_inside_current_stock_completes_whole_stock(rig):
    """更新路径 y/N 等待计入预算：等待期间跨截止，当前一只仍整只完成，下一只零调用。"""
    rig.positions = [_pos(c, with_plan=True) for c in CODES]
    rig.hooks["input"] = _once(_adv(rig, BUDGET))
    rig.answers = ["y", "y", "y"]
    rig.run(update=True, budget_seconds=BUDGET)
    out = rig.out()
    assert rig.count("input") == 1, "第 2 只不得再弹 y/N"
    assert rig.count("attach:600001") == 1, "已确认的当前一只必须写回完成"
    assert rig.count("prep:600002") == 0 and rig.count("prep:600003") == 0
    assert "✓ 1 成功" in out and "启动预算未开始 2 只：600002、600003" in out, out


def test_update_path_yn_decline_then_expiry_not_counted_as_failure(rig):
    rig.positions = [_pos(c, with_plan=True) for c in CODES]
    rig.hooks["input"] = _once(_adv(rig, BUDGET))
    rig.answers = ["n"]
    rig.run(update=True, budget_seconds=BUDGET)
    out = rig.out()
    assert "批量完成：✓ 0 成功 / ⏭ 0 跳过 / ↩ 1 未接受 / ✗ 0 失败" in out, out
    assert "启动预算未开始 2 只：600002、600003" in out


def test_failed_then_expiry_keeps_failure_and_rest_not_started(rig):
    rig.outcome_fn = lambda code, update: oc_data_fail(code)
    rig.hooks["prep:600001"] = _once(_adv(rig, BUDGET))
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert "✗ 1 失败" in out and "启动预算未开始 2 只：600002、600003" in out, out


def test_skip_does_not_read_clock_and_not_started_may_include_skippable(rig):
    # 600001 无计划（处理）、600002 无计划、600003 有计划（本会跳过）
    rig.positions = [_pos("600001"), _pos("600002"), _pos("600003", with_plan=True)]
    rig.hooks["prep:600001"] = _once(_adv(rig, BUDGET))
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert rig.count("prep:600002") == 0
    assert "启动预算未开始 2 只：600002、600003" in out, out
    assert "可能含已有计划" in out, out


def test_skipped_positions_are_not_checkpoints(rig):
    rig.positions = [_pos("600001", with_plan=True), _pos("600002"), _pos("600003")]
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    # 入口 + 校验后 + 清单后 + 两只实际开始前；被跳过的 600001 不产生读数
    assert len(rig.clock.reads) == 3 + 2, rig.clock.reads
    assert OLD_SUMMARY.format(ok=2, skip=1, fail=0) in out, out
    assert "未开始" not in out


def test_just_before_deadline_is_not_expired(rig):
    rig.hooks["list"] = _adv(rig, 599.9)
    rig.run(budget_seconds=BUDGET)
    assert rig.count("prep:") == 3, "差 0.1s 到期不得提前停止"
    assert "未开始" not in rig.out()


def test_exact_deadline_equality_counts_as_expired(rig):
    rig.hooks["list"] = _adv(rig, 600.0)
    assert rig.clock.now == T0
    rig.run(budget_seconds=BUDGET)
    assert rig.clock.reads[-1] == T0 + 600.0
    assert rig.count("prep:") == 0 and "启动预算未开始 3 只" in rig.out()


def test_fractional_budget_supported(rig):
    rig.hooks["list"] = _adv(rig, 0.5)
    rig.run(budget_seconds=0.5)
    out = rig.out()
    assert rig.count("prep:") == 0 and "启动预算未开始 3 只" in out
    assert "启动预算：0.5 秒" in out


def test_budgeted_run_without_expiry_keeps_old_summary_text(rig):
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert OLD_SUMMARY.format(ok=3, skip=0, fail=0) in out, out
    assert "未开始" not in out


def test_budgeted_run_prints_semantics_once_without_optional_hint(rig):
    rig.run(update=True, budget_seconds=BUDGET)
    out = rig.out()
    assert out.count("启动预算：600 秒") == 1, out
    assert "pos plan all" in out and "从批量计划入口计时" in out
    assert "逐只取数" in out and "笨总评分" in out and "AI" in out and "y/N 确认等待" in out
    assert "到期不再开始下一只，当前一只会跑完" in out
    assert "非强制中断" in out and "非费用上限" in out
    assert "可选时限" not in out, "有预算时不得再打印可选提示"


def test_budgeted_empty_positions_keeps_original_text(rig):
    rig.positions = []
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert "当前无持仓记录" in out and "批量完成" not in out and "启动预算已到期" not in out


# ═══════ 7. 可发现性：banner / 预算文案 ═══════

def test_banner_pos_plan_budget_row_within_border(capsys):
    start.show_help()
    out = capsys.readouterr().out
    rows = [ln for ln in out.splitlines() if "pos plan all [--update] --budget" in ln]
    assert len(rows) == 1, rows
    assert start._display_width(rows[0]) <= 74, f"帮助行撑破边框: {rows[0]!r}"
    # 既有帮助守卫（test_batch_budget_parse）禁止出现 `l --budget`（单只 l 不支持预算）；
    # 本行写成 `all [--update] --budget`，不得再出现 `l --budget` 子串
    assert "l --budget" not in out


def test_budget_help_and_scope_list_pos_plan_all():
    assert "pos plan all" in start._BUDGET_HELP and "pos plan all" in start._BUDGET_SCOPE


# ═══════ 复核 P1 追补：续跑指引按 update 分支；全部未开始不打印 0/0/0 汇总 ═══════

def test_update_mode_entry_expiry_hint_says_restart_from_first(rig):
    rig.hooks["import:batch_scorer"] = _adv(rig, BUDGET)
    rig.run(update=True, budget_seconds=BUDGET)
    out = rig.out()
    assert "pos plan all --update（会从第 1 只重新开始）" in out, out
    assert "可再次 pos plan all 继续" not in out, out


def test_update_mode_not_started_hint_points_to_per_stock_update(rig):
    """更新模式不跳过任何持仓：未开始提示不得说“本会跳过”，也不得只给不带 --update 的重跑命令。"""
    rig.positions = [_pos(c, with_plan=True) for c in CODES]
    rig.hooks["input"] = _once(_adv(rig, BUDGET))
    rig.answers = ["y"]
    rig.run(update=True, budget_seconds=BUDGET)
    out = rig.out()
    assert "启动预算未开始 2 只：600002、600003" in out, out
    assert "逐只 pos plan <代码> --update" in out and "会从第 1 只重新开始" in out, out
    assert "本会被跳过" not in out and "可再次 pos plan all 继续" not in out, out


def test_all_not_started_prints_no_zero_summary(rig):
    rig.hooks["list"] = _adv(rig, BUDGET)
    rig.run(budget_seconds=BUDGET)
    out = rig.out()
    assert "本次没有开始任何计划生成/更新（全部为预算未开始，不是失败）" in out, out
    assert "批量完成" not in out, out
