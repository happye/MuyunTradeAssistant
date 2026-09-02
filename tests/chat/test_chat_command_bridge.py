"""chat 命令桥测试（v0.8.8）：run_command / manage_portfolio / input 补丁 / 持仓 reload。

覆盖：
- 块列表：chat/q/noai/bz --manual/pos add/rm/chains rm 在 parse 之前被拦截
- confirm 硬门：l all/ba/bz scan/deep/pos plan --update/bz --refresh 不带 confirm 拒绝
- parse+dispatch 桥接：run_command("expect 60") 等命令经 start.parse_input→run_cli
- 真跑 rules（本地 YAML，无网络）：输出捕获含规则名
- input 补丁语义：y/N 类按 confirm 应答，菜单/空提示一律 "q"，退出恢复 builtins.input
- manage_portfolio add→update→remove 全流程（tmp portfolio，price=0 跳过 TradePlan 防 AI）
- #N 解析 / 无条件 reload / SystemExit 不炸 / update_position_fields 单元行为

无网络无 AI，不触碰真实 portfolio.yaml 与 ~/.muyun。
"""

import builtins
import contextlib
import io
import os
import sys
import tempfile

# 确保项目根在 path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 命令桥的相对路径（./configs/settings.yaml、./src/scanner/scan_rules.yaml）依赖项目根 cwd
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import yaml

from src.chat import tools as chat_tools
from src.chat.tools import TOOL_ERROR_MARK, manage_portfolio, run_command
from src.data import portfolio as portfolio_mod
from src.data.portfolio import PortfolioManager


# ── 测试基建 ──────────────────────────────────────────────

@contextlib.contextmanager
def _swap(obj, name, value):
    """最小 monkeypatch 替身：with 块内替换属性，退出恢复（pytest/直跑通用）。"""
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


@contextlib.contextmanager
def _tmp_portfolio():
    """tmp portfolio.yaml + patch DEFAULT_PORTFOLIO_PATH（运行时读模块全局，
    一个 patch 点覆盖全部构造点）。退出恢复 + 清 chat 层缓存实例。"""
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="portfolio_test_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"positions": {}}, f, allow_unicode=True)
    try:
        with _swap(portfolio_mod, "DEFAULT_PORTFOLIO_PATH", path):
            chat_tools._portfolio_manager = None
            yield path
    finally:
        chat_tools._portfolio_manager = None
        for suffix in ("", ".bak"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


def _quiet(fn, *args, **kw):
    """静默执行：Tee 的实时回显收进 StringIO（同时可断言回显内容）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kw)
    return result, buf.getvalue()


# ── 块列表 ──────────────────────────────────────────────

def test_blocked_commands():
    for cmd in ("chat", "q", "quit", "exit", "h", "help", "noai", "debug",
                "bz --manual", "bz", "pos rm 600519", "pos add 600519",
                "pos overweight 600519", "chains rm 白酒"):
        result, _ = _quiet(run_command, cmd)
        assert result.startswith(TOOL_ERROR_MARK), f"'{cmd}' 应被拦截，实际: {result[:80]}"


# ── confirm 硬门 ──────────────────────────────────────────

def test_confirm_gate_blocks_batch_ai_modes():
    for cmd in ("l all", "ba", "bz scan 半导体", "scan market deep",
                "pos plan all --update", "bz 600519 --refresh"):
        result, _ = _quiet(run_command, cmd)
        assert result.startswith(TOOL_ERROR_MARK), f"'{cmd}' 无 confirm 应被硬门拒绝"
        assert "confirm=true" in result, f"'{cmd}' 拒绝消息应引导 confirm=true"


def test_confirm_gate_passes_with_confirm():
    """带 confirm=true 时硬门放行（mock run_cli 防真跑）。"""
    import start as start_mod
    calls = []

    def _fake_run_cli(mode, args):
        calls.append((mode, args))

    with _swap(start_mod, "run_cli", _fake_run_cli):
        result, _ = _quiet(run_command, "l all", True)
    assert not result.startswith(TOOL_ERROR_MARK), f"带 confirm 应放行: {result[:80]}"
    assert calls and calls[0][0] == "live_scan_all", "应分发到 live_scan_all"


def test_single_stock_and_readonly_no_gate():
    """单股命令/只读命令与 REPL 平价不设门（b/expect/bz 单股/pos plan 单只）。

    ISS-078 变更：events 与 la 原列在本组"不设门"，但两者实为批量 AI 费用操作
    （events 走事件层 AI 分类、la 逐持仓 AI 深析），已移入 confirm 硬门——
    覆盖断言见 test_chat_confirm_gate_coverage.py。
    """
    import start as start_mod
    calls = []

    def _fake_run_cli(mode, args):
        calls.append((mode, args))

    with _swap(start_mod, "run_cli", _fake_run_cli):
        for cmd in ("b 600519", "expect 60", "bz 600519", "pos plan 600519"):
            result, _ = _quiet(run_command, cmd)
            assert not result.startswith(TOOL_ERROR_MARK), f"'{cmd}' 不应被门拦: {result[:80]}"
    assert len(calls) == 4


# ── parse+dispatch 桥接 ──────────────────────────────────

def test_parse_and_dispatch_bridge():
    import start as start_mod
    calls = []

    def _fake_run_cli(mode, args):
        calls.append((mode, args))

    with _swap(start_mod, "run_cli", _fake_run_cli):
        _quiet(run_command, "expect 60")
        _quiet(run_command, "b 600519 2024-01-01 2025-01-01 200000")
    assert calls[0] == ("expect", {"days": 60})
    assert calls[1] == ("backtest", {"stock_code": "600519", "start_date": "2024-01-01",
                                     "end_date": "2025-01-01", "capital": 200000.0})


def test_unknown_command_returns_usage_text():
    """未识别命令：parse_input 的用法提示被捕获返回（AI 可自我纠正），不抛异常。"""
    result, _ = _quiet(run_command, "不存在的命令xyz")
    assert "无法识别" in result
    assert not result.startswith(TOOL_ERROR_MARK)  # CLI 用法提示本身不是工具失败


def test_run_command_rules_real():
    """真跑 rules（读本地 scan_rules.yaml，无网络）：Rich 表格被捕获为纯文本。"""
    result, _ = _quiet(run_command, "rules")
    assert "healthy_pullback" in result or "健康回调" in result, \
        f"rules 输出应含规则名，实际: {result[:200]}"
    assert "\x1b[" not in result, "非 tty 下 Rich 不应输出 ANSI 码"


# ── input 补丁语义 ────────────────────────────────────────

def test_input_patcher_confirm_prompts():
    from src.chat.tools import _InputPatcher

    with _InputPatcher(True):
        assert builtins.input("继续?(y/N) ") == "y"
        assert builtins.input("是否采用此计划草稿？[Y/n] ") == "y"
    with _InputPatcher(False):
        assert builtins.input("继续?(y/N) ") == "n"
        assert builtins.input("是否采用？[Y/n] ") == "n"


def test_input_patcher_menu_and_empty_prompts():
    """菜单/空提示一律非肯定应答 'q'（Rich console.input 内部调无参 input）。"""
    from src.chat.tools import _InputPatcher

    for confirm in (True, False):
        with _InputPatcher(confirm):
            assert builtins.input("  > ") == "q"
            assert builtins.input() == "q"


def test_input_patcher_restores_builtin():
    from src.chat.tools import _InputPatcher

    orig = builtins.input
    with _InputPatcher(True):
        assert builtins.input is not orig
    assert builtins.input is orig, "退出 with 后必须恢复 builtins.input（管道模式泄漏会废掉 REPL）"


def test_input_patcher_echoes_prompt_and_answer():
    from src.chat.tools import _InputPatcher

    buf = io.StringIO()
    with _InputPatcher(False, echo=buf):
        builtins.input("是否采用此计划草稿？[Y/n] ")
    echoed = buf.getvalue()
    assert "是否采用此计划草稿" in echoed and "[chat自动应答: n]" in echoed, \
        "提示词与所选答案都要回显（否则用户/AI 不知道这个问题出现过）"


# ── manage_portfolio 全流程 ───────────────────────────────

def test_manage_portfolio_roundtrip():
    with _tmp_portfolio() as path:
        # add（price=0 跳过 TradePlan 草稿路径，无 AI）
        r, _ = _quiet(manage_portfolio, action="add", stock_code="600519",
                      stock_name="贵州茅台", confirm=True)
        assert "已添加持仓" in r, f"add 应成功: {r[:200]}"
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert "600519" in data["positions"]

        # update 字段级修改
        r, _ = _quiet(manage_portfolio, action="update", stock_code="600519",
                      ratio=0.3, price=1500.0, confirm=True)
        assert "已更新" in r and "30%" in r, f"update 应成功: {r[:200]}"
        pm = PortfolioManager()
        pos = pm.get_position("600519")
        assert pos.current_ratio == 0.3 and pos.entry_price == 1500.0

        # remove
        r, _ = _quiet(manage_portfolio, action="remove", stock_code="600519", confirm=True)
        assert "已删除持仓" in r
        assert PortfolioManager().list_positions() == []


def test_manage_portfolio_write_requires_confirm():
    with _tmp_portfolio():
        for kw in (dict(action="add", stock_code="600519"),
                   dict(action="remove", stock_code="600519"),
                   dict(action="update", stock_code="600519", ratio=0.3),
                   dict(action="overweight", stock_code="600519")):
            r, _ = _quiet(manage_portfolio, **kw)
            assert r.startswith(TOOL_ERROR_MARK), f"{kw['action']} 无 confirm 应被拒绝"
        # 拒绝路径不能产生写盘
        assert PortfolioManager().list_positions() == []


def test_manage_portfolio_plan_no_gate_and_list():
    """plan/list 与 REPL 平价不设门。plan 走 CLI manage_positions（无持仓时提示）。"""
    with _tmp_portfolio():
        r, _ = _quiet(manage_portfolio, action="plan", stock_code="600519")
        assert "无持仓记录" in r, f"无持仓 plan 应提示先建仓: {r[:120]}"
        r, _ = _quiet(manage_portfolio, action="list")
        assert "无持仓记录" in r or "当前无持仓" in r


def test_manage_portfolio_hash_n_resolve():
    """#N 引用：resolve_index 命中时 code/name 取自最近扫描。"""
    from src.cli import session_state

    def _fake_resolve(n):
        return ({"code": "002192", "name": "融捷股份"}, "")

    with _tmp_portfolio(), _swap(session_state, "resolve_index", _fake_resolve):
        r, _ = _quiet(manage_portfolio, action="add", stock_code="#1", confirm=True)
    assert "已添加持仓" in r
    pos = PortfolioManager().get_position("002192")
    assert pos is not None and pos.stock_name == "融捷股份", "#N 的 name 应带入建仓"


def test_reload_after_write():
    """写盘后 chat 层 _portfolio_manager 无条件重读（防陈旧快照回滚）。"""
    with _tmp_portfolio():
        _quiet(manage_portfolio, action="add", stock_code="600519",
               stock_name="贵州茅台", confirm=True)
        assert chat_tools._portfolio_manager is not None
        assert chat_tools._portfolio_manager.has_position("600519"), \
            "写盘后 chat 层持仓缓存必须重读"


def test_systemexit_survival():
    """run_cli 抛 SystemExit（如 l <代码> 数据失败）不能杀死调用方。"""
    import start as start_mod

    def _boom(mode, args):
        raise SystemExit(1)

    with _swap(start_mod, "run_cli", _boom):
        result, _ = _quiet(run_command, "expect 30")
    assert "[命令异常退出]" in result, f"SystemExit 应转为尾注不外抛: {result[:120]}"


# ── update_position_fields 单元行为 ────────────────────────

def test_update_position_fields_unit():
    with _tmp_portfolio() as path:
        pm = PortfolioManager()
        pm.add_position("600519", stock_name="贵州茅台", entry_price=1400.0, ratio=0.2)

        # 不存在 → False
        assert PortfolioManager().update_position_fields("000001", current_ratio=0.1) is False

        # 无可改项（全 None）→ False
        assert PortfolioManager().update_position_fields("600519") is False

        # 只改仓位
        assert PortfolioManager().update_position_fields("600519", current_ratio=0.35) is True
        pos = PortfolioManager().get_position("600519")
        assert pos.current_ratio == 0.35 and pos.entry_price == 1400.0

        # 只改开仓价；系统字段（strategy_state/lifecycle）不动
        assert PortfolioManager().update_position_fields("600519", entry_price=1500.0) is True
        pos = PortfolioManager().get_position("600519")
        assert pos.entry_price == 1500.0
        assert pos.lifecycle == "OPEN" and pos.last_action == "OPEN"

        # 改名
        assert PortfolioManager().update_position_fields("600519", stock_name="茅台") is True
        assert PortfolioManager().get_position("600519").stock_name == "茅台"

        # 非法值
        try:
            PortfolioManager().update_position_fields("600519", current_ratio=-0.1)
            assert False, "负仓位应抛 ValueError"
        except ValueError:
            pass
        try:
            PortfolioManager().update_position_fields("600519", entry_price=-5)
            assert False, "负价格应抛 ValueError"
        except ValueError:
            pass

        # .bak 滚动备份已生成
        assert os.path.exists(path + ".bak")


if __name__ == "__main__":
    import traceback

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
        except Exception as e:
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
