"""暮云思辨 textual TUI 主界面（Phase 1，接引擎版）

多面板：左 scan 结果表 + 右深度分析栏，键盘操作，@work worker 异步不卡 UI。

复用 chat/tools.py 调引擎模式（不重构 CLI/引擎）：
- scan: _scanner_engine.quick_scan -> DataTable
- analyze: _orchestrator.analyze -> 侧栏 rich 渲染（结构化字段，不走 formatter 字符串）
- add_pos: manage_positions

长任务（scan 4分钟 / analyze 30-60s）用 @work + asyncio.to_thread，UI 不卡。
和 start.py REPL 共存（start_tui.py 入口）。
"""

import asyncio
import logging

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog

logger = logging.getLogger(__name__)


class MuyunTUI(App):
    """暮云思辨 TUI 主界面"""

    CSS = """
    Horizontal { height: 1fr; }
    #scan-table { width: 1fr; }
    #analysis-panel { width: 1fr; border-left: solid cyan; }
    """

    BINDINGS = [
        Binding("s", "scan", "扫描"),
        Binding("enter", "analyze", "分析"),
        Binding("a", "add_pos", "加仓"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(self):
        super().__init__()
        self._config = None
        self._scanner = None
        self._orchestrator = None
        self._portfolio = None
        self._engines_ready = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id="scan-table")
            yield RichLog(id="analysis-panel")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "暮云思辨投资助手"
        table = self.query_one("#scan-table", DataTable)
        table.add_columns("#", "代码", "名称", "价", "涨跌%", "换手%", "量比")
        self.query_one("#analysis-panel", RichLog).write(
            "Phase 1 TUI。键位：s扫描 ↑↓选股 Enter分析 a加仓 q退出"
        )
        self.sub_title = "初始化引擎中..."
        self._init_engines()

    def _init_engines(self) -> None:
        """初始化底层引擎（复用 chat/tools.py）。失败不崩，降级提示。"""
        try:
            from src.chat.tools import _orchestrator, _scanner_engine, init_engines
            from src.data.portfolio import PortfolioManager

            from src.cli.main import load_config

            self._config = load_config()
            init_engines(self._config)
            self._scanner = _scanner_engine
            self._orchestrator = _orchestrator
            self._portfolio = PortfolioManager()
            self._engines_ready = True
            self.sub_title = "引擎就绪（按 s 开始扫描）"
        except Exception as e:
            self.sub_title = f"引擎初始化失败: {e}"
            self.query_one("#analysis-panel", RichLog).write(
                f"[red]引擎初始化失败: {e}[/red]"
            )

    @work
    async def action_scan(self) -> None:
        """scan market，worker 异步不卡 UI"""
        if not self._engines_ready:
            self._init_engines()
            if not self._engines_ready:
                return
        table = self.query_one("#scan-table", DataTable)
        table.clear()
        self.sub_title = "扫描中（全市场行情，约4分钟）..."
        try:
            exclude = {p.stock_code for p in self._portfolio.list_positions()}
        except Exception:
            exclude = set()
        try:
            candidates, info = await asyncio.to_thread(
                self._scanner.quick_scan,
                rule_name="healthy_pullback",
                market_query=None,
                exclude_codes=exclude,
            )
        except Exception as e:
            self.sub_title = f"扫描失败: {e}"
            return
        if not candidates:
            self.sub_title = f"无候选股（{info.get('error', '可能非交易时段')}"
            return
        for i, c in enumerate(candidates, 1):
            table.add_row(
                str(i),
                c.stock_code,
                c.stock_name,
                f"{c.price:.2f}" if c.price else "-",
                f"{c.change_pct:+.2f}" if c.change_pct is not None else "-",
                f"{c.turnover_rate:.2f}" if c.turnover_rate else "-",
                f"{c.volume_ratio:.2f}" if c.volume_ratio else "-",
                key=c.stock_code,
            )
        self.sub_title = f"扫描完成 {len(candidates)} 只（↑↓选股 Enter分析 a加仓）"

    @work
    async def action_analyze(self) -> None:
        """深度分析选中股，worker 异步"""
        if not self._engines_ready:
            return
        table = self.query_one("#scan-table", DataTable)
        if table.row_count == 0:
            return
        code = self._selected_code(table)
        if not code:
            return
        panel = self.query_one("#analysis-panel", RichLog)
        panel.clear()
        panel.write(f"分析 {code} 中...（30-60s）")
        self.sub_title = f"分析 {code} 中..."
        try:
            result = await asyncio.to_thread(self._analyze_work, code)
        except Exception as e:
            panel.clear()
            panel.write(f"[red]分析失败: {e}[/red]")
            self.sub_title = "分析失败"
            return
        if result is None:
            panel.clear()
            panel.write(f"[red]无法获取 {code} 数据[/red]")
            self.sub_title = "分析失败（无数据）"
            return
        panel.clear()
        panel.write(self._render_analysis(*result))
        self.sub_title = f"{result[0].stock_name} 分析完成"

    def _analyze_work(self, code: str):
        """同步：取数据 + 调 _orchestrator.analyze（worker 线程跑，复用 tools.analyze_stock 模式）"""
        from src.data.akshare_client import AKShareClient
        from src.data.models import StockData

        stock_data = AKShareClient.calculate_indicators(code)
        if not stock_data:
            quote = AKShareClient.get_realtime_quote(code)
            if quote:
                # v0.8.7.8 E02 同类点：此前只映射 4 个字段，open/high/low/volume 全丢
                stock_data = StockData(
                    stock_code=quote.get("stock_code", code),
                    stock_name=quote.get("stock_name", code),
                    price=quote.get("price", 0),
                    open=quote.get("open") or None,
                    high=quote.get("high") or None,
                    low=quote.get("low") or None,
                    change_pct=quote.get("change_pct"),
                    volume=quote.get("volume") or None,
                )
        if not stock_data:
            return None
        pos = None
        current_ratio = 0.0
        strategy_state = None
        try:
            for p in self._portfolio.list_positions():
                if p.stock_code == code:
                    pos = p
                    current_ratio = p.current_ratio
                    strategy_state = self._portfolio.to_strategy_state(code)
                    break
        except Exception as e:
            # ISS-078：持仓读取失败按空仓分析是 fail-open——至少要让用户知道
            logger.warning(f"持仓读取失败，{code} 本次按空仓分析（建议稍后重跑）: {e}")
        has_position = pos is not None and pos.current_ratio > 0
        dr, sd, ee, ai = self._orchestrator.analyze(
            stock_data,
            current_position_ratio=current_ratio,
            strategy_state=strategy_state,
            ai_enabled=True,
            has_position=has_position,
            entry_price=pos.entry_price if pos else None,
            high_since_entry=pos.high_since_entry if pos else None,
            trade_plan=pos.trade_plan if pos else None,
        )
        # v0.8.9.5（彻查批 A-4）：持仓股回写策略状态（chat H1/CLI/web 同款行为平价）
        if has_position and pos is not None:
            try:
                # M5：回写保存失败如实告警（update_from_strategy_decision 返回 bool）
                if not self._portfolio.update_from_strategy_decision(
                        pos.stock_code, stock_data.stock_name or pos.stock_code, sd, stock_data):
                    logger.warning(f"TUI 回写策略状态未落盘({pos.stock_code}): 持仓文件被外部修改或写入失败")
            except Exception as e:
                logger.warning(f"TUI 回写策略状态失败({pos.stock_code}): {e}")
        return stock_data, dr, sd, ee, ai

    @work
    async def action_add_pos(self) -> None:
        """加仓选中股（默认 20%）"""
        if not self._engines_ready:
            return
        table = self.query_one("#scan-table", DataTable)
        if table.row_count == 0:
            return
        code = self._selected_code(table)
        if not code:
            return
        panel = self.query_one("#analysis-panel", RichLog)
        panel.write(f"\n加仓 {code}（默认 20%）...")
        try:
            from src.cli.main import manage_positions

            await asyncio.to_thread(
                manage_positions, "add",
                stock_code=code, name="", price=0.0, ratio=0.20,
            )
            panel.write(f"[green]已加仓 {code}[/green]")
        except Exception as e:
            panel.write(f"[red]加仓失败: {e}[/red]")

    def _render_analysis(self, stock_data, dr, sd, ee, ai) -> str:
        """渲染分析结果（结构化字段 -> 文本，不走 formatter）"""
        lines = []
        lines.append(f"=== {stock_data.stock_name} ({stock_data.stock_code}) ===")
        lines.append(f"价:{stock_data.price}  涨跌:{stock_data.change_pct}%")
        lines.append(f"市场:{dr.state.value}  决策:{dr.decision.value}  评分:{dr.score:.2f}")
        pos_map = {
            "OPEN": "试探建仓", "ADD": "加仓", "REDUCE": "减仓",
            "CLOSE_ALL": "清仓", "HOLD_POSITION": "维持", "STAY_OUT": "空仓观望",
        }
        lines.append(f"仓位动作:{pos_map.get(sd.position_action.value, sd.position_action.value)}")
        if stock_data.ma5:
            lines.append(
                f"MA5={stock_data.ma5:.2f} MA20={stock_data.ma20 or 0:.2f} MA60={stock_data.ma60 or 0:.2f}"
            )
        if dr.signals:
            lines.append("--- 信号 ---")
            for sig in dr.signals:
                lines.append(
                    f"  {sig.skill_alias}: {sig.signal.value} (conf {sig.confidence:.0%})"
                )
        if ai and getattr(ai, "adjusted", False):
            lines.append(
                f"AI情绪:{getattr(ai, 'sentiment', '?')} (conf {getattr(ai, 'confidence', 0):.0%})"
            )
        return "\n".join(lines)

    def _selected_code(self, table: DataTable) -> str:
        """取当前选中行的 row_key（=股票代码）"""
        if table.cursor_coordinate is None:
            return ""
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return str(row_key.value) if row_key and row_key.value else ""
