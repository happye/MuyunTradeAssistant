"""T04-B TUI 加仓：失败如实显示 + 不污染 stdout。

旧实现：action_add_pos 经 asyncio.to_thread(manage_positions, "add", ...) 后无条件写
「已加仓」——已有持仓/保存失败等 manage_positions 只 print 并 return None，TUI 拿不到失败
信号（假成功），且 CLI 的 console.print 直接写 stdout 与 Textual 界面冲突。
修复：改调 add_position_checked（仍在线程里），按状态写 panel。

隔离：portfolio/proposals 默认路径指向 tmp（同 test_tui_shared_account 手法）；
引擎初始化桩成只设 _portfolio/_engines_ready；扫描表直接塞一行（不联网）。
"""
import io
import sys

import pytest

import src.cli.main  # noqa: F401  顺序敏感：先导入真实 by-value 绑定，再 patch
from src.data import portfolio, proposals
from src.tui.app import MuyunTUI

CODE = "600519"


def _make_app(tmp_path, monkeypatch, *, seed_held):
    monkeypatch.setattr(portfolio, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    pm = portfolio.PortfolioManager()
    if seed_held:
        assert pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)

    def init(app):
        app._portfolio = pm
        app._engines_ready = True

    monkeypatch.setattr(MuyunTUI, "_init_engines", init)
    return MuyunTUI()


def _panel_text(app):
    return "\n".join(line.text for line in app.query_one("#analysis-panel").lines)


async def _press_add(app, pilot, monkeypatch):
    table = app.query_one("#scan-table")
    table.add_row("1", CODE, "测试股", "10", "0", "0", "0", key=CODE)
    await pilot.pause()
    spy = io.StringIO()
    monkeypatch.setattr(sys, "stdout", spy)
    await app.action_add_pos().wait()
    await pilot.pause()
    return spy


@pytest.mark.asyncio
async def test_add_already_held_shows_reason_not_success(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch, seed_held=True)
    async with app.run_test(size=(180, 50)) as pilot:
        real_stdout = sys.stdout
        spy = await _press_add(app, pilot, monkeypatch)
        monkeypatch.setattr(sys, "stdout", real_stdout)
        text = _panel_text(app)
        assert spy.getvalue() == "", f"TUI 路径不得向 stdout 写 CLI 输出: {spy.getvalue()!r}"
        assert "已加仓" not in text, f"已有持仓却显示成功: {text}"
        assert "已有持仓" in text, f"应显示失败原因: {text}"
    assert portfolio.PortfolioManager().get_position(CODE).current_ratio == 0.1


@pytest.mark.asyncio
async def test_add_new_position_success_text_and_no_stdout(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch, seed_held=False)
    async with app.run_test(size=(180, 50)) as pilot:
        real_stdout = sys.stdout
        spy = await _press_add(app, pilot, monkeypatch)
        monkeypatch.setattr(sys, "stdout", real_stdout)
        text = _panel_text(app)
        assert f"已加仓 {CODE}（仓位 20%）" in text, text
        assert "未带价格，跳过 TradePlan 草稿；可用 pos plan <代码> 生成" in text, text
        assert spy.getvalue() == "", f"TUI 路径不得向 stdout 写 CLI 输出: {spy.getvalue()!r}"
    pos = portfolio.PortfolioManager().get_position(CODE)
    assert pos is not None and pos.current_ratio == 0.2


@pytest.mark.asyncio
async def test_add_save_failed_shows_reason(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch, seed_held=False)
    monkeypatch.setattr(portfolio.PortfolioManager, "_save",
                        lambda self: portfolio.SaveResult(False))
    async with app.run_test(size=(180, 50)) as pilot:
        real_stdout = sys.stdout
        spy = await _press_add(app, pilot, monkeypatch)
        monkeypatch.setattr(sys, "stdout", real_stdout)
        text = _panel_text(app)
        assert "已加仓" not in text, text
        assert "保存失败" in text, text
        assert spy.getvalue() == ""


@pytest.mark.asyncio
async def test_add_total_over_80_warns(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch, seed_held=False)
    assert portfolio.PortfolioManager().add_position("000001", stock_name="压舱股",
                                                     entry_price=5, ratio=0.7)
    async with app.run_test(size=(180, 50)) as pilot:
        real_stdout = sys.stdout
        await _press_add(app, pilot, monkeypatch)
        monkeypatch.setattr(sys, "stdout", real_stdout)
        text = _panel_text(app)
        assert f"已加仓 {CODE}（仓位 20%）" in text
        assert "总仓位 90%>80%" in text, text
