"""BUG-TUI-MARKUP：分析面板不得把 [red]/[green] 字面标签露给用户。

根因：RichLog(id="analysis-panel") 默认不开 markup；旧路径用 f"[red]...[/red]"
字符串 write，标签原样显示。T04-B 新加仓提示已改 rich.Text；本卡收齐剩余路径。
"""
import src.cli.main  # noqa: F401  顺序敏感：先真实 by-value 绑定

from types import SimpleNamespace

import pytest

from src.data import portfolio, proposals
from src.tui.app import MuyunTUI


def _panel_text(app) -> str:
    return "\n".join(line.text for line in app.query_one("#analysis-panel").lines)


def _stub_ready_app(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    pm = portfolio.PortfolioManager()

    def init(app):
        app._portfolio = pm
        app._scanner = SimpleNamespace(quick_scan=lambda **kw: ([], {}))
        app._orchestrator = SimpleNamespace(analyze=lambda *a, **k: None)
        app._engines_ready = True

    monkeypatch.setattr(MuyunTUI, "_init_engines", init)
    return MuyunTUI(), pm


@pytest.mark.asyncio
async def test_engine_init_failure_no_literal_markup_tags(monkeypatch):
    """引擎初始化失败提示应着色，不得出现字面 [red]/[/red]。"""

    def fail_load_config():
        raise RuntimeError("boom")

    monkeypatch.setattr("src.config.load_config", fail_load_config)
    app = MuyunTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        text = _panel_text(app)
        assert "引擎初始化失败" in text, text
        assert "[red]" not in text and "[/red]" not in text, f"字面 markup 泄露: {text!r}"
        assert "[green]" not in text and "[/green]" not in text, text


@pytest.mark.asyncio
async def test_analyze_exception_no_literal_markup_tags(tmp_path, monkeypatch):
    """分析抛错路径不得字面显示 [red]。"""
    app, _pm = _stub_ready_app(tmp_path, monkeypatch)

    def boom_work(self, code):
        raise RuntimeError("analyze-boom")

    monkeypatch.setattr(MuyunTUI, "_analyze_work", boom_work)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#scan-table")
        table.add_row("1", "600519", "测试股", "10", "0", "0", "0", key="600519")
        await pilot.pause()
        await app.action_analyze().wait()
        await pilot.pause()
        text = _panel_text(app)
        assert "分析失败" in text, text
        assert "[red]" not in text and "[/red]" not in text, f"字面 markup 泄露: {text!r}"


@pytest.mark.asyncio
async def test_analyze_no_data_no_literal_markup_tags(tmp_path, monkeypatch):
    """无数据路径不得字面显示 [red]。"""
    app, _pm = _stub_ready_app(tmp_path, monkeypatch)
    monkeypatch.setattr(MuyunTUI, "_analyze_work", lambda self, code: None)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#scan-table")
        table.add_row("1", "600519", "测试股", "10", "0", "0", "0", key="600519")
        await pilot.pause()
        await app.action_analyze().wait()
        await pilot.pause()
        text = _panel_text(app)
        assert "无法获取 600519 数据" in text, text
        assert "[red]" not in text and "[/red]" not in text, f"字面 markup 泄露: {text!r}"


@pytest.mark.asyncio
async def test_add_pos_exception_no_literal_markup_tags(tmp_path, monkeypatch):
    """加仓异常路径不得字面显示 [red]（与 T04-B 成功/已知失败 Text 路径并列）。"""
    app, _pm = _stub_ready_app(tmp_path, monkeypatch)

    def boom_add(*a, **k):
        raise RuntimeError("add-boom")

    monkeypatch.setattr("src.data.portfolio.add_position_checked", boom_add)
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()
        table = app.query_one("#scan-table")
        table.add_row("1", "600519", "测试股", "10", "0", "0", "0", key="600519")
        await pilot.pause()
        await app.action_add_pos().wait()
        await pilot.pause()
        text = _panel_text(app)
        assert "加仓失败" in text, text
        assert "[red]" not in text and "[/red]" not in text, f"字面 markup 泄露: {text!r}"