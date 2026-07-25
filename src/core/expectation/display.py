"""预期事件日历 Rich 展示（v0.8.7 预期管理 Phase 1）

仿 scan_events（src/cli/main.py:2485）的 Rich 展示风格。
纯展示，不写入决策。
"""

from rich.table import Table
from rich.panel import Panel
from rich.console import Console

from src.core.expectation.calendar import EventCalendar
from src.core.expectation.overdraw import ExpectationOverdrawCalculator

console = Console(legacy_windows=False)

_IMPACT_ICON = {"high": "🔴", "medium": "🟡", "low": "·"}
_TYPE_CN = {"policy": "政策", "macro": "宏观", "earnings": "财报", "other": "其他"}


def _countdown_icon(days: int) -> str:
    if days < 3:
        return "🟠"
    if days <= 10:
        return "🟡"
    return "·"


def _source_tag(source: str) -> str:
    if source == "akshare":
        return "akshare✓"
    if source == "static":
        return "static⚠"
    return "❌无源"


def show_expect(days: int = 30, codes=None) -> None:
    """展示预期事件日历 + 预期透支度。"""
    cal = EventCalendar()
    events = cal.get_upcoming_events(days=days, codes=codes)
    stale = cal.check_stale_static_events()
    overdraw = ExpectationOverdrawCalculator().calculate()

    console.print(f"\n[bold cyan]⚡ 预期事件日历（未来{days}天）[/bold cyan]")

    if events:
        table = Table(show_lines=False, expand=True, title="未来事件")
        table.add_column("倒计时", width=8, justify="center")
        table.add_column("日期", width=12)
        table.add_column("事件", style="white", overflow="fold")
        table.add_column("类型", width=6)
        table.add_column("影响", width=5, justify="center")
        table.add_column("数据源", width=10)
        for e in events:
            table.add_row(
                f"{_countdown_icon(e.days_to_event)}{e.days_to_event}天",
                e.date,
                e.name,
                _TYPE_CN.get(e.event_type, e.event_type),
                _IMPACT_ICON.get(e.impact, "·"),
                _source_tag(e.source),
            )
        console.print(table)
    else:
        console.print(f"  [dim]未来{days}天无事件[/dim]")

    for w in stale:
        console.print(f"  [yellow]⚠ {w}[/yellow]")

    console.print(_render_overdraw_panel(overdraw))

    console.print(
        "[dim]提示：events=事后事件预警 | expect=事前预期日历\n"
        "       ⚠=静态事件表手动维护，日期可能调整\n"
        "       透支度是环境信号非预期差，真正预期差需一致预期值（Phase 2）[/dim]"
    )


def _render_overdraw_panel(o) -> Panel:
    if o.data_status == "failed":
        return Panel(
            "[red]⚠ 数据源全部失效，仅展示静态事件表[/red]\n"
            "[dim]国债/沪深300 接口均拉取失败，透支度无法计算[/dim]",
            title="🌡️ 预期透支度（环境温度计）",
            border_style="red",
        )
    lines = ["[dim]环境温度计（非预期差，一致预期值无数据源）[/dim]", ""]
    if o.rate_value is not None and o.rate_percentile is not None:
        col = "green" if o.rate_percentile < 40 else ("yellow" if o.rate_percentile < 70 else "red")
        tag = "偏松" if o.rate_percentile < 40 else ("中性" if o.rate_percentile < 70 else "偏紧")
        lines.append(f"  利率环境：10Y国债 {o.rate_value}% （3年 [{col}]{o.rate_percentile}%[/{col}]分位）{tag}")
    else:
        lines.append("  利率环境：[red]数据失效[/red]")
    if o.valuation_value is not None and o.valuation_percentile is not None:
        col = "green" if o.valuation_percentile < 40 else ("yellow" if o.valuation_percentile < 70 else "red")
        tag = "偏低" if o.valuation_percentile < 40 else ("中性" if o.valuation_percentile < 70 else "偏高")
        lines.append(f"  估值环境：沪深300 {o.valuation_value} （3年 [{col}]{o.valuation_percentile}%[/{col}]分位）{tag}")
    else:
        lines.append("  估值环境：[red]数据失效[/red]")
    if o.momentum_20d is not None:
        col = "green" if o.momentum_20d < 0 else ("yellow" if o.momentum_20d < 5 else "red")
        tag = "偏弱" if o.momentum_20d < 0 else ("偏强" if o.momentum_20d > 5 else "中性")
        lines.append(f"  短期动能：近20日 [{col}]{o.momentum_20d:+.1f}%[/{col}] {tag}")
    else:
        lines.append("  短期动能：[red]数据失效[/red]")
    score_col = "red" if o.score >= 70 else ("yellow" if o.score >= 40 else "green")
    level = "高" if o.score >= 70 else ("中" if o.score >= 40 else "低")
    lines.append("")
    lines.append(f"  -> 透支度 [{score_col}]{o.score:.0f}/100 {level}度透支[/{score_col}]")
    lines.append(f"  [dim]解读：{o.interpretation}[/dim]")
    return Panel("\n".join(lines), title="🌡️ 预期透支度", border_style="cyan")
