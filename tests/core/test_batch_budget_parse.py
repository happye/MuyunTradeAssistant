# -*- coding: utf-8 -*-
"""T05-J：ba/bz 多代码共用批评分的 `--budget` 解析与分发接线（零业务动作拒绝）。

锁死语义（合同 J1）：
- 支持 ba / bzall（`ba --budget 60`）与 bz 多代码（`bz 600519,000001 --budget 60`）
- `--budget VALUE` 与 `--budget=VALUE` 两种写法；只接受有限正数秒
- 缺值/重复/0/负/nan/inf/非数字/未知选项/不支持入口（单只 bz、bz --check/--manual、
  单只 l/裸代码）→ 返回 None，且在确认、扫描读取、网络/AI、账本写入之前拒绝
- T05-L 起 l 多代码 / la / l all / 裸多代码改为支持入口（live 合同，见
  test_live_batch_budget.py），这里不再列为不支持
- T05-N 起 `bz scan`（含 筛选/选股 别名）改为支持入口（见 test_bzscan_budget.py）
- 默认无 budget 时返回 dict 与旧黄金完全一致（不附加 budget 键，旧 mock/调用方兼容）

跑法：pytest tests/core/test_batch_budget_parse.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import start


def _parse(text):
    """调 parse_input 并断言不抛（坏输入必须走显式拒绝而不是异常）。"""
    return start.parse_input(text)


def _forbid_state_reads(monkeypatch):
    """坏预算/不支持入口必须在任何扫描读取之前返回（合同 J1）。"""
    from src.cli import session_state

    def _boom(*a, **k):
        raise AssertionError("拒绝路径不得读取会话扫描状态")

    monkeypatch.setattr(session_state, "get_last_scan", _boom)
    monkeypatch.setattr(session_state, "resolve_index", _boom)
    monkeypatch.setattr(session_state, "last_scan_count", _boom)


# ── 支持的两种入口与两种写法 ───────────────────────────────

def test_ba_budget_space_value():
    assert _parse("ba --budget 60") == ("benzong_all", {"budget": 60.0})


def test_bzall_budget_equals_fraction():
    assert _parse("bzall --budget=0.5") == ("benzong_all", {"budget": 0.5})


def test_bz_multi_budget_after_codes():
    assert _parse("bz 600519,000001 --budget 60") == (
        "benzong_multi",
        {"codes": ["600519", "000001"], "refresh": False, "budget": 60.0},
    )


def test_bz_multi_budget_equals_with_refresh_alias():
    assert _parse("bz 600519 000001 -r --budget=90") == (
        "benzong_multi",
        {"codes": ["600519", "000001"], "refresh": True, "budget": 90.0},
    )


def test_bz_multi_budget_before_codes_and_chinese_comma():
    assert _parse("bz --budget 30 600519，000001") == (
        "benzong_multi",
        {"codes": ["600519", "000001"], "refresh": False, "budget": 30.0},
    )


def test_bz_multi_budget_and_refresh_word_alias():
    assert _parse("bz 600519,000001 refresh --budget 120") == (
        "benzong_multi",
        {"codes": ["600519", "000001"], "refresh": True, "budget": 120.0},
    )


# ── T05-N：bz scan 起支持 --budget（值不混入主题/规则）──────────

def test_scan_budget_supported_t05n():
    mode, args = _parse("bz scan 科技 --budget 60")
    assert mode == "benzong_scan" and args["budget"] == 60.0
    assert args["theme"] == "" and args["rule"] == "科技"
    mode, args = _parse("bz --budget 60 scan 科技")
    assert mode == "benzong_scan" and args["budget"] == 60.0


# ── 无预算：返回形状与旧黄金一致（不新增键） ────────────────

def test_no_budget_exact_legacy_dicts():
    assert _parse("ba") == ("benzong_all", {})
    assert _parse("bzall") == ("benzong_all", {})
    assert _parse("bz 600519,000001") == (
        "benzong_multi", {"codes": ["600519", "000001"], "refresh": False})
    assert _parse("bz 600519,000001 --refresh") == (
        "benzong_multi", {"codes": ["600519", "000001"], "refresh": True})


def test_budget_key_only_when_requested():
    assert "budget" not in _parse("ba")[1]
    assert "budget" not in _parse("bz 600519,000001")[1]
    assert _parse("ba --budget 60")[1]["budget"] == 60.0


# ── 坏预算值：拒绝 + 清晰帮助，且不读扫描状态 ──────────────

@pytest.mark.parametrize("text", [
    "ba --budget",                 # 缺值
    "ba --budget=",                # 空值
    "ba --budget abc",             # 非数字
    "ba --budget 0",               # 零
    "ba --budget -5",              # 负
    "ba --budget nan",             # nan
    "ba --budget inf",             # inf
    "ba --budget 1e999",           # 溢出为 inf
    "ba --budget 60 --budget 90",  # 重复
    "bz 600519,000001 --budget",   # 多代码缺值
    "bz 600519,000001 --budget=nan",
    "bz 600519,000001 --budget=-1",
    "bz 600519,000001 --budget 60 --budget=90",
])
def test_bad_budget_rejected_before_business(text, monkeypatch, capsys):
    _forbid_state_reads(monkeypatch)
    assert start.parse_input(text) is None
    out = capsys.readouterr().out
    assert "--budget" in out, "拒绝必须点名 --budget（清晰帮助）"
    assert ("无效" in out or "缺少" in out), out


# ── 不支持入口：单只 bz / bz scan/check/manual / 单只 l / 单只裸代码 ──
# T05-L：l 多代码 / la / l all / 裸多代码已改为支持（live 合同），从本表移出

@pytest.mark.parametrize("text", [
    "bz 600519 --budget 60",
    "bz 600519 --budget=60",
    "bz --manual --budget 60",
    "bz 600519 --manual --budget 60",
    "bz --check --budget 60",
    "l 600519 --budget 60",
    "l #3 --budget 60",
    "600519 --budget 60",
])
def test_unsupported_entries_reject_before_business(text, monkeypatch, capsys):
    _forbid_state_reads(monkeypatch)
    assert start.parse_input(text) is None
    out = capsys.readouterr().out
    assert "--budget" in out and "不支持" in out, out


# ── 未知选项：预算可用入口不得静默吞掉 ──────────────────────

def test_ba_rejects_unknown_arguments(capsys):
    assert start.parse_input("ba extra") is None
    out = capsys.readouterr().out
    assert "不接受其他参数" in out and "extra" in out
    assert start.parse_input("ba --foo") is None
    assert "不接受其他参数" in capsys.readouterr().out


def test_bz_multi_rejects_unknown_dashed_option(capsys):
    assert start.parse_input("bz 600519,000001 --foo 60") is None
    out = capsys.readouterr().out
    assert "未知选项" in out and "--foo" in out


def test_bz_multi_keeps_invalid_token_warning_behavior(capsys):
    """非选项类非法 token 仍是既有行为：告警跳过，不影响多代码批量。"""
    mode, args = start.parse_input("bz 600519,000001 abc")
    assert mode == "benzong_multi"
    assert args["codes"] == ["600519", "000001"]
    assert "已跳过" in capsys.readouterr().out


# ── 分发接线：只有显式 budget 才向 benzong_batch_analyze 传 budget ──

def test_run_cli_benzong_multi_passes_budget_kwarg(monkeypatch):
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache

    seen = []

    def fake_analyze(items, force_refresh=False, budget=None):
        seen.append(([i["code"] for i in items], force_refresh, budget))

    monkeypatch.setattr(cli_main, "benzong_batch_analyze", fake_analyze)
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    start.run_cli("benzong_multi",
                  {"codes": ["600519", "000001"], "refresh": False, "budget": 60.0})
    assert seen == [(["600519", "000001"], False, 60.0)]


def test_run_cli_benzong_all_passes_budget_and_prints_preconfirm_notice(monkeypatch, capsys):
    from datetime import datetime
    from src.cli import session_state
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache

    now = datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(session_state, "get_last_scan", lambda: {
        "items": [{"code": "600519"}], "source": "bzscan", "timestamp": now})
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    seen = []
    monkeypatch.setattr(cli_main, "benzong_batch_analyze",
                        lambda items, src_label="?", budget=None:
                        seen.append((src_label, budget)))
    start.run_cli("benzong_all", {"budget": 60.0})
    assert seen == [("bzscan", 60.0)]
    out = capsys.readouterr().out
    assert "启动预算" in out and "60 秒" in out
    assert "确认前缓存预览不计入" in out, "文案必须说明确认前预览不计入预算"
    assert "非强制中断" in out, "不得暗示硬取消/强制中断"


def test_run_cli_benzong_all_no_budget_keeps_old_call(monkeypatch):
    """无预算：旧 mock 签名（无 budget 参数）必须继续可用（J1 兼容）。"""
    from datetime import datetime
    from src.cli import session_state
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache

    now = datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(session_state, "get_last_scan", lambda: {
        "items": [{"code": "600519"}], "source": "bzscan", "timestamp": now})
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    seen = []
    monkeypatch.setattr(cli_main, "benzong_batch_analyze",
                        lambda items, src_label="?": seen.append(src_label))
    start.run_cli("benzong_all", {})
    assert seen == ["bzscan"]


def test_run_cli_invalid_programmatic_budget_rejected_before_scan_lookup(monkeypatch, capsys):
    """绕过 parse 直接给坏 budget：分发层也要在扫描读取前拒绝。"""
    from src.cli import session_state
    import src.core.benzong.cache as bz_cache

    def _boom(*a, **k):
        raise AssertionError("坏预算不得读取扫描结果")

    monkeypatch.setattr(session_state, "get_last_scan", _boom)
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    for bad in (0, -1, float("nan"), float("inf"), "60", True):
        start.run_cli("benzong_all", {"budget": bad})
    out = capsys.readouterr().out
    assert "预算参数无效" in out, out


def test_parse_then_run_cli_budget_chain(monkeypatch):
    """parse_input → run_cli 全链（接线测试与单元直调并存）。"""
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache

    parsed = start.parse_input("bz 600519,000001 --budget=45")
    assert parsed is not None
    mode, args = parsed
    assert mode == "benzong_multi"
    seen = []
    monkeypatch.setattr(cli_main, "benzong_batch_analyze",
                        lambda items, force_refresh=False, budget=None:
                        seen.append(([i["code"] for i in items], budget)))
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    start.run_cli(mode, args)
    assert seen == [(["600519", "000001"], 45.0)]


# ═══════ fix2（主控独立复核 BLOCKERS，仍属原 J1-J4 范围）═══════
# BLOCKER 1：业务绕门——未知预算形状不得变主题/位置参数/被忽略；不支持命令的
# 显式 budget（含正数）在 parse 早返回与 run_cli 最前统一拒绝；合法无预算不变。

def _forbid_all_business(monkeypatch):
    """统一拒绝路径不得触发：确认/扫描读取/缓存/分析/评分/账本。"""
    import src.cli.main as cli_main
    from src.cli import session_state
    import src.core.benzong.cache as bz_cache
    import src.core.benzong.batch_scorer as batch_scorer

    calls = []

    def boom(*a, **k):
        calls.append("touched")
        raise AssertionError("拒绝路径不得触碰业务")

    monkeypatch.setattr(session_state, "get_last_scan", boom)
    monkeypatch.setattr(session_state, "resolve_index", boom)
    monkeypatch.setattr(session_state, "batch_task_start", boom)
    monkeypatch.setattr(bz_cache, "get", boom)
    monkeypatch.setattr(batch_scorer, "auto_score_batch", boom)
    for name in ("benzong_batch_analyze", "analyze_live", "analyze_portfolio",
                 "scan_market", "manage_positions", "today_command",
                 "batch_tasks_cmd", "run_benzong_scoring"):
        attr = getattr(cli_main, name, None)
        if attr is not None:
            monkeypatch.setattr(cli_main, name, boom)
    monkeypatch.setattr(start, "run_benzong_scoring", boom)
    monkeypatch.setattr(start, "run_benzong_scan", boom)
    return calls


@pytest.mark.parametrize("text", [
    "la --budgetx 60",          # BLOCKER 例：此前直接 live_all
    "la --budgets",
    "l 600519 --budgetx",
    "600519 --budgetx 60",
    "bz scan 科技 --budgetx 60",   # BLOCKER 例：此前当主题
    "bz scan --budgets=60 科技",
    "bz 600519,000001 --budgetx=60",
    "ba --budgetx 60",
    "bzall --budget-any",
    "scan market --budgetx 60",
    "q --budgetx",
    "help --budgetz",
    "pos list --budget-any",
    "today --budgety=1",
])
def test_fix2_unknown_budget_option_shapes_rejected_before_early_returns(
        text, monkeypatch, capsys):
    """未知预算形状（--budgetx/--budgets…）必须显式拒绝，不得变主题/被忽略。"""
    calls = _forbid_all_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == [], "解析层拒绝不得触发任何业务动作"
    out = capsys.readouterr().out
    assert "未知预算" in out or "不支持 --budget" in out, out
    assert "60" not in out or "预算" in out


@pytest.mark.parametrize("text", [
    "q --budget 60",
    "help --budget 60",
    "scan --budget 60",
    "scan market --budget 60",
    "scan review --budget 60",
    "pos list --budget 60",
    "events --budget 60",
    "today --budget 60",
    "doctor --budget 60",
    "tasks --budget 60",
    "fear --budget 60",
    "watch --budget 60",
    "diff 600519 --budget 60",
    "b 600519 --budget 60",
    "bb 600519 --budget 60",
    "expect 30 --budget 60",
    "rules --budget 60",
    "industries --budget 60",
    "plan2 600519 --budget 60",
    "research 600519 --budget 60",
    "shadow --budget 60",
    "noai --budget 60",
    "debug --budget 60",
])
def test_fix2_recognized_budget_on_unsupported_commands_rejected(
        text, monkeypatch, capsys):
    """不支持命令上的合法 --budget 也不得被静默忽略（含早返回分支）。"""
    calls = _forbid_all_business(monkeypatch)
    assert start.parse_input(text) is None
    assert calls == []
    out = capsys.readouterr().out
    assert "不支持 --budget" in out, out


def test_fix2_plain_words_containing_budget_are_not_options(capsys):
    """只核 option token：普通文本/主题/文件名里出现 budget 字样不得误伤。"""
    assert start.parse_input("bz budget")[0] == "benzong"
    assert start.parse_input("bz scan budget")[0] == "benzong_scan"
    assert start.parse_input("scan market budget")[0] == "scan_market"
    assert start.parse_input("research 600519 --claims budget.json")[0] == "research"


@pytest.mark.parametrize("mode,args", [
    ("benzong", {"meta": "600519", "manual": False, "refresh": False,
                 "check": False, "budget": 0}),          # BLOCKER 例
    ("benzong", {"meta": "600519", "manual": False, "refresh": False,
                 "check": False, "budget": 60}),         # 正数同样不支持
    ("live", {"stock_code": "600519", "budget": 60}),
    # T05-L：live 批改为支持模式，坏值同样先拒（合法正数见 test_live_batch_budget.py）
    ("live_multi", {"codes": ["600519", "000001"], "budget": 0}),
    ("live_all", {"budget": float("nan")}),
    ("live_scan_all", {"force": False, "budget": "60"}),
    ("scan", {"budget": 60}),
    ("today", {"budget": 60}),
    ("batch_tasks", {"budget": 60}),
    ("benzong_all", {"budget": 0}),                      # 支持模式坏值先拒
    ("benzong_all", {"budget": float("nan")}),
    ("benzong_multi", {"codes": ["600519"], "refresh": False, "budget": "60"}),
])
def test_fix2_run_cli_rejects_budget_before_any_business(
        mode, args, monkeypatch, capsys):
    """run_cli 统一门：不支持 mode 的显式 budget（含正数）与支持 mode 坏值
    一律在确认/扫描/缓存/分析/评分/账本之前拒绝。"""
    calls = _forbid_all_business(monkeypatch)
    start.run_cli(mode, dict(args))
    assert calls == []
    out = capsys.readouterr().out
    assert "不支持 --budget" in out or "预算参数无效" in out, out


def test_fix2_parse_then_run_cli_rejections_isolated(monkeypatch, capsys):
    """parse→run_cli 全链：不支持/未知预算形状不会落到任何处理器。"""
    calls = _forbid_all_business(monkeypatch)
    assert start.parse_input("l 600519 --budget 60") is None   # T05-L：单只仍不支持
    assert start.parse_input("bz scan --budgetx 60") is None
    assert start.parse_input("q --budget 60") is None
    assert calls == []
    out = capsys.readouterr().out
    assert "不支持 --budget" in out or "未知预算" in out


# ═══════ rev2 / J7：普通用户可发现性（帮助/横幅 + 正常无预算主动提示）═══════
# 要求：真实入口输出可见——帮助菜单在 ba 与 bz 多代码位置列 --budget 示例、默认
# 不启用、到点不开新只/当前只跑完；正常无预算 ba/bzall/多代码在确认/预览前主动
# 提示；不加确认、不改 kwargs/账本/缓存/评分语义；单股/顶层 scan/l 不宣传适用性
# （T05-N 起 bz scan 是支持入口，其可发现性见 test_bzscan_budget.py）。

def _help_menu_lines(out):
    return [ln for ln in out.splitlines() if "--budget" in ln]


# 不进入评分的入口（单股 / 顶层 scan / l）不得宣传适用性；bz scan 自 T05-N 起支持
def test_vis_help_lists_optional_budget_examples_with_short_rows(capsys):
    start.show_help()
    out = capsys.readouterr().out
    budget_lines = _help_menu_lines(out)
    assert len(budget_lines) >= 2, "帮助菜单必须列出 ba 与 bz 多代码的 --budget 示例"
    joined = "\n".join(budget_lines)
    assert "ba --budget 60" in joined
    assert "bz 600519,000001 --budget 60" in joined
    assert "bz scan --budget 600 --limit 15" in joined   # T05-N 新增可发现行
    assert "可选" in joined and "默认不启用" in joined
    assert "不开新只" in joined and "当前只跑完返回" in joined, \
        "必须说明到点不再开始下一只、当前一只完成后返回"
    # 既有菜单边框规则：新增行不得撑破内容区（W=70，整行显示宽度 W+4）
    for line in budget_lines:
        assert start._display_width(line) <= 74, \
            f"帮助行撑破边框: {line!r} width={start._display_width(line)}"
    # 不支持入口不得宣传适用性（bz scan 已支持，不再列入）
    all_text = out
    for bad in ("l --budget", "bz 600519 --budget"):
        assert bad not in all_text, f"帮助不得宣传不支持入口: {bad}"


def test_vis_banner_real_entry_shows_help_budget_examples(capsys):
    start.show_banner()
    out = capsys.readouterr().out
    assert "暮云思辨投资助手" in out
    assert "ba --budget 60" in out
    assert "bz 600519,000001 --budget 60" in out


def test_vis_ba_no_budget_hint_before_confirmation(monkeypatch, capsys):
    """正常 ba 无预算：确认前主动提示可选用法；用户取消则零评分、旧签名无新 kwargs。"""
    from datetime import datetime
    from src.cli import session_state
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache

    now = datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(session_state, "get_last_scan", lambda: {
        "items": [{"code": "600519"}], "source": "bzscan", "timestamp": now})
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: None)   # 未评分 → 会确认
    seen = []
    monkeypatch.setattr(cli_main, "benzong_batch_analyze",
                        lambda items, src_label="?": seen.append(src_label))
    snapshots = []

    def fake_input(prompt):
        snapshots.append(capsys.readouterr().out)   # 确认瞬间已输出的内容
        return "n"

    monkeypatch.setattr("builtins.input", fake_input)
    parsed = start.parse_input("ba")
    assert parsed is not None
    start.run_cli(*parsed)
    assert snapshots, "未评分路径必须经过既有确认（不新增确认）"
    before_confirm = snapshots[0]
    assert "可选时限" in before_confirm and "--budget 60" in before_confirm, \
        "普通无预算流程必须在费用确认前主动提示可选项"
    assert "当前一只跑完" in before_confirm or "当前只跑完" in before_confirm
    assert seen == [], "用户取消不得进入评分"
    rest = capsys.readouterr().out
    assert "已取消" in rest
    assert "--budget" not in rest, "取消路径不得追加预算 kwargs/后续提示"


def test_vis_ba_no_budget_hint_all_cached_before_preview(monkeypatch, capsys):
    """全命中缓存（无确认）也要在预览前提示；仍以旧签名调用（无 budget kwarg）。"""
    from datetime import datetime
    from src.cli import session_state
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache

    now = datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(session_state, "get_last_scan", lambda: {
        "items": [{"code": "600519"}], "source": "bzscan", "timestamp": now})
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    seen = []
    monkeypatch.setattr(cli_main, "benzong_batch_analyze",
                        lambda items, src_label="?": seen.append(src_label))
    start.run_cli("benzong_all", {})
    out = capsys.readouterr().out
    assert "可选时限" in out and "--budget 60" in out
    assert out.index("可选时限") < out.index("全部命中今日缓存"), \
        "提示必须在缓存预览之前出现"
    assert seen == ["bzscan"], "无预算必须保持旧 kwargs（不追加 budget）"


def test_vis_bz_multi_no_budget_hint_before_preview(monkeypatch, capsys):
    """正常 bz 多代码无预算：预览前主动提示，且保持旧 mock 签名（无 budget）。"""
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache

    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    seen = []
    monkeypatch.setattr(cli_main, "benzong_batch_analyze",
                        lambda items, force_refresh=False:
                        seen.append(([i["code"] for i in items], force_refresh)))
    parsed = start.parse_input("bz 600519,000001")
    assert parsed is not None
    start.run_cli(*parsed)
    out = capsys.readouterr().out
    assert "可选时限" in out and "--budget 60" in out
    assert out.index("可选时限") < out.index("全部命中今日缓存")
    assert seen == [(["600519", "000001"], False)], "无预算不得追加 budget kwarg"
    assert "启动预算" not in out, "无预算不得输出已配置预算提示"


def test_vis_explicit_budget_keeps_configured_notice_without_duplicate_hint(
        monkeypatch, capsys):
    """已指定 budget：保留实际配置提示，不再重复普通可选提示。"""
    from datetime import datetime
    from src.cli import session_state
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache

    now = datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(session_state, "get_last_scan", lambda: {
        "items": [{"code": "600519"}], "source": "bzscan", "timestamp": now})
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    seen = []
    monkeypatch.setattr(cli_main, "benzong_batch_analyze",
                        lambda items, src_label="?", budget=None:
                        seen.append((src_label, budget)))
    start.run_cli("benzong_all", {"budget": 60.0})
    out = capsys.readouterr().out
    assert "启动预算：60 秒" in out
    assert "确认前缓存预览不计入" in out
    assert "可选时限" not in out, "已配置预算无需重复普通可选提示"
    assert seen == [("bzscan", 60.0)]


def test_vis_unsupported_modes_do_not_advertise_budget(monkeypatch, capsys):
    """单股 bz / scan / l 等不支持入口不得输出可选项提示。"""
    import src.cli.main as cli_main

    monkeypatch.setattr(start, "run_benzong_scoring",
                        lambda **kw: print("SCORING_RAN"))
    start.run_cli("benzong", {"meta": "600519", "manual": False,
                              "refresh": False, "check": False})
    out = capsys.readouterr().out
    assert "SCORING_RAN" in out
    assert "--budget" not in out and "可选时限" not in out

    monkeypatch.setattr(cli_main, "analyze_live", lambda *a, **k: print("LIVE_RAN"))
    start.run_cli("live", {"stock_code": "600519"})
    out = capsys.readouterr().out
    assert "LIVE_RAN" in out
    assert "--budget" not in out and "可选时限" not in out

    monkeypatch.setattr(cli_main, "analyze_portfolio",
                        lambda **k: print("SCAN_RAN"))
    start.run_cli("scan", {})
    out = capsys.readouterr().out
    assert "SCAN_RAN" in out
    assert "--budget" not in out and "可选时限" not in out


# ═══════ final_gate E：默认提示必须先于实际缓存预览读取 ═══════

def test_gate_e_ba_hint_before_first_cache_read(monkeypatch, capsys):
    """默认提示必须在实际 bz_cache.get 预览之前（第一次读取时已输出）。"""
    from datetime import datetime
    from src.cli import session_state
    import src.core.benzong.cache as bz_cache

    now = datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(session_state, "get_last_scan", lambda: {
        "items": [{"code": "600519"}], "source": "bzscan", "timestamp": now})
    snapshots = []

    def fake_get(*a, **k):
        snapshots.append(capsys.readouterr().out)
        return None

    monkeypatch.setattr(bz_cache, "get", fake_get)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    start.run_cli("benzong_all", {})
    assert snapshots, "必须发生缓存预览读取"
    assert "可选时限" in snapshots[0] and "--budget 60" in snapshots[0], \
        "第一次缓存读取时提示必须已输出（不能等预览算完）"


def test_gate_e_multi_hint_before_first_cache_read(monkeypatch, capsys):
    import src.core.benzong.cache as bz_cache

    snapshots = []

    def fake_get(*a, **k):
        snapshots.append(capsys.readouterr().out)
        return None

    monkeypatch.setattr(bz_cache, "get", fake_get)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    parsed = start.parse_input("bz 600519,000001")
    assert parsed is not None
    start.run_cli(*parsed)
    assert snapshots
    assert "可选时限" in snapshots[0] and "--budget 60" in snapshots[0], \
        "bz 多代码第一次缓存读取时提示必须已输出"


