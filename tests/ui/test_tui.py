"""textual TUI pilot 测试（Phase 1，接引擎版 mock）

mock 底层引擎（FakeScanner/FakePM），验证 @work worker 异步填表 + 键盘不崩。
不接真引擎（避免真 scan 4分钟 + 真 init）。
"""

# 顺序敏感（M5 批修复的隔离脆弱性）：_mock_engines 会替换
# portfolio.PortfolioManager，而 tui/app.py:73 的引擎初始化会连带**首次导入**
# src.cli.main——其顶层 `from src.data.portfolio import PortfolioManager` 是
# 按值绑定，若在 patch 生效期间才执行会把 lambda 永久捕获进 main 命名空间
# （monkeypatch 只恢复 portfolio 模块的属性，管不到 main 的副本），污染后续
# 全部测试。收集期先导入 main，保证 by-value 绑定是真实类。
import src.cli.main  # noqa: F401,E402


class _FakeCand:
    def __init__(self, code, name):
        self.stock_code = code
        self.stock_name = name
        self.price = 100.0
        self.change_pct = 2.5
        self.turnover_rate = 1.5
        self.volume_ratio = 1.2
        self.amplitude = 3.0
        self.amount = 10e8


class _FakeScanner:
    def quick_scan(self, **kw):
        return [_FakeCand("600519", "贵州茅台"), _FakeCand("002230", "科大讯飞")], {
            "total_stocks": 3
        }


class _FakePM:
    def list_positions(self):
        return []

    def to_strategy_state(self, code):
        return None


def _mock_engines(monkeypatch):
    from src.chat import tools
    monkeypatch.setattr(tools, "init_engines", lambda cfg: None)
    monkeypatch.setattr(tools, "_scanner_engine", _FakeScanner())
    monkeypatch.setattr(tools, "_orchestrator", None)
    monkeypatch.setattr("src.data.portfolio.PortfolioManager", lambda *a, **k: _FakePM())


import pytest


@pytest.mark.asyncio
async def test_tui_mounts_no_crash(monkeypatch):
    _mock_engines(monkeypatch)
    from src.tui.app import MuyunTUI
    app = MuyunTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._engines_ready  # 引擎就绪


@pytest.mark.asyncio
async def test_tui_scan_worker(monkeypatch):
    _mock_engines(monkeypatch)
    from src.tui.app import MuyunTUI
    app = MuyunTUI()
    async with app.run_test() as pilot:
        await pilot.pause()  # on_mount + _init_engines
        await pilot.press("s")  # 触发 scan worker
        await pilot.pause()
        await pilot.pause()  # 多 pause 确保 worker（asyncio.to_thread）完成
        table = app.query_one("#scan-table")
        assert table.row_count == 2  # 2 假 candidates


@pytest.mark.asyncio
async def test_tui_arrow_select(monkeypatch):
    _mock_engines(monkeypatch)
    from src.tui.app import MuyunTUI
    app = MuyunTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("down")  # 选中第二行
        await pilot.pause()
        # 不崩即过


@pytest.mark.asyncio
async def test_tui_quit(monkeypatch):
    _mock_engines(monkeypatch)
    from src.tui.app import MuyunTUI
    app = MuyunTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        # 退出不崩
