"""T04-B-0 黄金输出测试：pos add / pos rm（CLI）与 chat manage_portfolio(action="remove")

目的：在把 add/remove 校验与写入下沉到 portfolio 服务、TUI/chat 改调服务之前，
逐字锁住当前行为——
  ① CLI：控制台输出完整行序列（rich Console 宽 200、无色、写 StringIO，去尾空白）
  ② chat：manage_portfolio 返回文本（redirect 到 _TeeBuf，rich 宽度固定 80 与非 tty 默认一致）
  ③ 磁盘结果：portfolio.yaml 中持仓记录（日期字段归一为 <TODAY>）
改造后用同一文件逐字比对；任何文案/顺序差异 = 回滚（DELIVERY_PLAN T04-B 停止条件）。

隔离：PortfolioManager 用显式 tmp 路径，cli_main.PortfolioManager 与 portfolio.PortfolioManager
都换成每次返回新实例的工厂（贴近真实「每次命令新建管理器」；chat 路径也不会读 ~/.muyun）。
桩（仅此三类，其余全真实）：
  - cli_main._try_attach_trade_plan：只记录被调用及参数（不测草稿本身，免联网/AI）
  - PortfolioManager._save：返回 SaveResult(ok=False) 模拟保存失败
  - 无其他桩

跑法：pytest tests/core/test_t04b_position_bridge_golden.py -q
"""
import contextlib
import io
import os
import sys
from datetime import datetime

import pytest
import yaml
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.chat.tools as chat_tools
import src.cli.main as cli_main
import src.cli.plain_errors  # noqa: F401
import src.data.portfolio as portfolio_mod
from src.data.portfolio import PortfolioManager, SaveResult

CODE = "600519"
OTHER = "000001"
_TODAY = datetime.now().strftime("%Y-%m-%d")


class _Env:
    def __init__(self, tmp_path, monkeypatch):
        self.dir = tmp_path / "gold"
        self.dir.mkdir()
        self.portfolio = self.dir / "portfolio.yaml"
        self.proposals = self.dir / "proposals.json"
        self.out = io.StringIO()
        self.mp = monkeypatch
        self.attach_calls = []
        monkeypatch.setattr(cli_main, "PortfolioManager", self.new_pm)
        monkeypatch.setattr(portfolio_mod, "PortfolioManager", self.new_pm)
        monkeypatch.setattr(cli_main, "console", Console(
            file=self.out, width=200, color_system=None, force_terminal=False))
        monkeypatch.setattr(cli_main, "_try_attach_trade_plan", self._attach)
        root = str(tmp_path.resolve())
        for p in (self.portfolio, self.proposals):
            assert os.path.abspath(str(p)).startswith(root), f"持久化路径越出临时根: {p}"
        chat_tools._portfolio_manager = None

    def new_pm(self):
        return PortfolioManager(str(self.portfolio), str(self.proposals))

    def _attach(self, pm, code, name, price, ratio):
        self.attach_calls.append((code, name, price, ratio))

    def seed(self, code=CODE, name="测试股", ratio=0.1, price=10.0):
        pm = self.new_pm()
        assert pm.add_position(code, stock_name=name, entry_price=price, ratio=ratio)

    def seed_unknown_weight(self, code=OTHER):
        """数量账户 + 比例视图过期 → 总仓位未知（None）。"""
        self.seed(code=code, name="待重估股", ratio=0.1)
        pm = self.new_pm()
        pm._data["positions"][code]["ratio_stale"] = True
        pm._data["positions"][code]["quantity_fact"] = {
            "quantity": 100, "as_of": "2026-09-21", "avg_cost": 10.0}
        assert pm._save().ok

    def fail_save(self):
        self.mp.setattr(PortfolioManager, "_save", lambda self_: SaveResult(False))

    def lines(self):
        text = self.out.getvalue()
        return [ln.rstrip() for ln in text.rstrip("\n").split("\n")] if text.strip() else []

    def disk(self):
        """磁盘事实：新实例读盘，日期归一。"""
        if not self.portfolio.exists():
            return {}
        raw = yaml.safe_load(self.portfolio.read_text(encoding="utf-8")) or {}
        res = {}
        for code, d in (raw.get("positions") or {}).items():
            res[str(code)] = {
                "stock_name": d.get("stock_name"),
                "entry_price": d.get("entry_price"),
                "current_ratio": d.get("current_ratio"),
                "lifecycle": d.get("lifecycle"),
                "last_action": d.get("last_action"),
                "entry_date": "<TODAY>" if d.get("entry_date") == _TODAY else d.get("entry_date"),
                "ratio_stale": d.get("ratio_stale", False),
            }
        return res

    def add(self, **kw):
        kw.setdefault("stock_code", CODE)
        cli_main.manage_positions("add", **kw)

    def rm(self, **kw):
        kw.setdefault("stock_code", CODE)
        cli_main.manage_positions("remove", **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _Env(tmp_path, monkeypatch)


def _rec(name, price, ratio):
    return {"stock_name": name, "entry_price": price, "current_ratio": ratio,
            "lifecycle": "OPEN", "last_action": "OPEN", "entry_date": "<TODAY>",
            "ratio_stale": False}


_SEEDED = _rec("测试股", 10.0, 0.1)
_NO_PRICE_HINT = [
    "⚠ 未带价格参数，本次跳过 TradePlan 草稿生成",
    "  草稿需要入场价算止损位；可补价格重跑 pos add，或事后 pos plan <代码> 生成",
]


# ── CLI add ───────────────────────────────────────────────

def test_cli_add_missing_code(env):
    env.add(stock_code="", price=-1.0, ratio=2.0)   # 缺代码先于价格/比例校验
    assert env.lines() == ["请指定股票代码"]
    assert env.disk() == {} and env.attach_calls == []


def test_cli_add_already_held_checked_before_price_and_ratio(env):
    env.seed()
    env.add(price=-1.0, ratio=2.0)                  # 已持仓先于价格/比例校验
    assert env.lines() == ["⚠ 600519 已有持仓记录，请先 --pos-remove 删除"]
    assert env.disk() == {CODE: _SEEDED} and env.attach_calls == []


def test_cli_add_negative_price(env):
    env.add(price=-0.01, ratio=0.2)
    assert env.lines() == ["开仓价不能为负数（不设置请留空）"]
    assert env.disk() == {} and env.attach_calls == []


def test_cli_add_ratio_out_of_range(env):
    env.add(price=10.0, ratio=1.5)
    env.add(price=10.0, ratio=-0.1)
    assert env.lines() == ["仓位比例需在 0-1 之间（0%-100%）"] * 2
    assert env.disk() == {} and env.attach_calls == []


def test_cli_add_negative_price_checked_before_ratio(env):
    env.add(price=-1.0, ratio=1.5)
    assert env.lines() == ["开仓价不能为负数（不设置请留空）"]


def test_cli_add_save_failed_uses_name_then_code(env):
    env.fail_save()
    env.add(name="测试股", price=10.0, ratio=0.2)
    env.add(name="", price=0.0, ratio=0.2)
    assert env.lines() == [
        "✗ 持仓保存失败：测试股 (600519) 未写入——请看上方告警，处理后重试",
        "✗ 持仓保存失败：600519 (600519) 未写入——请看上方告警，处理后重试",
    ]
    assert env.disk() == {} and env.attach_calls == []


def test_cli_add_success_with_price_attaches_plan(env):
    env.add(name="测试股", price=10.5, ratio=0.2)
    assert env.lines() == [
        "✓ 已添加持仓: 测试股 (600519)",
        "  仓位: 20%  开仓价: 10.50",
    ]
    assert env.disk() == {CODE: _rec("测试股", 10.5, 0.2)}
    assert env.attach_calls == [(CODE, "测试股", 10.5, 0.2)]


def test_cli_add_success_total_over_80_warns(env):
    env.seed(code=OTHER, name="压舱股", ratio=0.7, price=5.0)
    env.add(name="测试股", price=10.5, ratio=0.2)
    assert env.lines() == [
        "✓ 已添加持仓: 测试股 (600519)",
        "  仓位: 20%  开仓价: 10.50",
        "  ⚠ 总仓位 90%>80%，笨总教学十：任何时候给自己留一张底牌（建议≤80%，预留20%应对突发/抄底）",
    ]
    assert env.disk() == {OTHER: _rec("压舱股", 5.0, 0.7), CODE: _rec("测试股", 10.5, 0.2)}
    assert env.attach_calls == [(CODE, "测试股", 10.5, 0.2)]


def test_cli_add_success_total_exactly_80_no_warning(env):
    env.seed(code=OTHER, name="压舱股", ratio=0.6, price=5.0)
    env.add(name="测试股", price=10.5, ratio=0.2)    # 0.6+0.2 == 0.8，不触发（> 0.80 才警告）
    assert env.lines() == [
        "✓ 已添加持仓: 测试股 (600519)",
        "  仓位: 20%  开仓价: 10.50",
    ]


def test_cli_add_success_total_unknown_weight_notice(env):
    env.seed_unknown_weight()
    env.add(name="测试股", price=10.5, ratio=0.2)
    assert env.lines() == [
        "✓ 已添加持仓: 测试股 (600519)",
        "  仓位: 20%  开仓价: 10.50",
        "  ⚠ 存在权重待重估持仓（数量账户比例视图过期）——总仓位未知，暂无法判断80%底牌线；重估后再看",
    ]
    assert env.disk()[CODE] == _rec("测试股", 10.5, 0.2)
    assert env.attach_calls == [(CODE, "测试股", 10.5, 0.2)]


def test_cli_add_price_zero_skips_plan_with_hint_and_default_ratio(env):
    env.add(name="", price=0.0, ratio=None)          # ratio None→0.20；名称缺省取代码；价格 None 落盘
    assert env.lines() == [
        "✓ 已添加持仓: 600519 (600519)",
        "  仓位: 20%",
    ] + _NO_PRICE_HINT
    assert env.disk() == {CODE: _rec("600519", None, 0.2)}
    assert env.attach_calls == []


# ── CLI remove ────────────────────────────────────────────

def test_cli_remove_missing_code(env):
    env.rm(stock_code="")
    assert env.lines() == ["请指定股票代码"]


def test_cli_remove_not_held(env):
    env.seed(code=OTHER, name="别的股", ratio=0.1)
    env.rm()
    assert env.lines() == ["⚠ 600519 无持仓记录"]
    assert env.disk() == {OTHER: _rec("别的股", 10.0, 0.1)}


def test_cli_remove_save_failed(env):
    env.seed()
    env.fail_save()
    env.rm()
    assert env.lines() == [
        "✗ 持仓删除失败：600519 仍在持仓中（文件可能被其他会话修改，详见上方告警）"]
    assert env.disk() == {CODE: _SEEDED}


def test_cli_remove_success_name_then_code_fallback(env):
    env.seed()
    env.seed(code=OTHER, name="", ratio=0.1)
    env.rm()
    env.rm(stock_code=OTHER)
    assert env.lines() == [
        "✓ 已删除持仓: 测试股 (600519)",
        "✓ 已删除持仓: 000001 (000001)",
    ]
    assert env.disk() == {}


# ── chat manage_portfolio(action="remove") ────────────────

@pytest.fixture
def chat_env(env):
    # chat 路径：cli_main.console 固定宽 80（非 tty 默认）、输出动态跟随 redirect 后的 sys.stdout
    env.mp.setattr(cli_main, "console", Console(width=80, color_system=None, force_terminal=False))
    try:
        from src.cli.plain_errors import drain_new
        drain_new()
    except Exception:
        pass
    return env


def _chat(**kw):
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        return chat_tools.manage_portfolio(**kw)


def test_chat_remove_success(chat_env):
    chat_env.seed()
    r = _chat(action="remove", stock_code=CODE, confirm=True)
    assert r == "✓ 已删除持仓: 测试股 (600519)"
    assert chat_env.disk() == {}


def test_chat_remove_alias_rm_and_code_normalized(chat_env):
    chat_env.seed()
    r = _chat(action="rm", stock_code="SH.600519", confirm=True)
    assert r == "✓ 已删除持仓: 测试股 (600519)"
    assert chat_env.disk() == {}


def test_chat_remove_not_held(chat_env):
    chat_env.seed(code=OTHER, name="别的股", ratio=0.1)
    r = _chat(action="remove", stock_code=CODE, confirm=True)
    assert r == "⚠ 600519 无持仓记录"
    assert chat_env.disk() == {OTHER: _rec("别的股", 10.0, 0.1)}


def test_chat_remove_save_failed(chat_env):
    chat_env.seed()
    chat_env.fail_save()
    r = _chat(action="remove", stock_code=CODE, confirm=True)
    assert r == ("✗ 持仓删除失败：600519 仍在持仓中（文件可能被其他会话修改，详见上方告警）")
    assert chat_env.disk() == {CODE: _SEEDED}


def test_chat_remove_without_confirm_is_gated(chat_env):
    chat_env.seed()
    r = _chat(action="remove", stock_code=CODE)
    assert r == (chat_tools.TOOL_ERROR_MARK + "持仓修改是写盘操作。请先在对话中向用户确认具体内容"
                 "（remove 600519 的名称/价格/仓位等），用户明确同意后带 confirm=true 重新调用")
    assert chat_env.disk() == {CODE: _SEEDED}
