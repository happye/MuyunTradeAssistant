"""恐慌指数 CLI/REPL 渲染层（Rich）。

文字输出全部为确定性模板填数，不做任何 AI 调用（方案 P-纯客观）。
分档着色参照 expect 日历面板的成熟范式。
"""
from __future__ import annotations

from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(legacy_windows=False)

_TIER_STYLE = {
    "极度贪婪": "bold red", "贪婪": "yellow", "中性": "white",
    "恐慌": "cyan", "极度恐慌": "bold cyan", "无法评估": "grey50",
}


def show_overview(report: dict) -> None:
    """渲染一键总览：总分面板 + 成分明细表 + 数据基准。"""
    score, tier = report.get("score"), report.get("tier", "无法评估")
    color = {"极度贪婪": "red", "贪婪": "yellow", "中性": "white",
             "恐慌": "cyan", "极度恐慌": "bright_cyan"}.get(tier, "white")
    missing = report.get("missing", [])
    miss_note = f"（缺失{len(missing)}项: {','.join(missing)}，已剔除权重）" if missing else ""
    head = Text()
    head.append(f"  {score:.1f}" if score is not None else "  --", style=f"bold {color} 40")
    head.append(f"  / 100   {tier}", style=f"bold {color}")
    head.append(f"{miss_note}", style="grey50")
    body = Text("\n")
    body.append(f"  基准交易日: {report.get('as_of', '?')}    生成时刻: "
                f"{report.get('generated_at', '?')[11:19]}    "
                f"聚合成分: {len(report.get('used', []))}/7\n", style="grey50")
    body.append("  0-20 极度贪婪 | 20-40 贪婪 | 40-60 中性 | 60-80 恐慌 | 80-100 极度恐慌"
                "（越高越恐慌）", style="grey50")
    console.print(Panel(body, title=head, border_style=color, expand=False))

    table = Table(title="恐慌成分明细（成分级溯源，缺失显式标注）", expand=False)
    for col, kw in (("成分", {"style": "cyan", "no_wrap": True}),
                    ("原始值", {"justify": "right"}),
                    ("恐慌分", {"justify": "right"}),
                    ("状态", {}),
                    ("数据源", {}),
                    ("口径说明", {})):
        table.add_column(col, **kw)
    for c in report.get("components", []):
        score_str = "--" if c.get("score") is None else f"{c['score']:.1f}"
        status = c.get("status", "OK")
        status_style = {"OK": "green", "STALE": "yellow", "MISSING": "red"}.get(status, "white")
        mark = "" if c.get("in_aggregate", True) else " [展示]"
        table.add_row(
            c["label"] + mark,
            _fmt_raw(c),
            f"[{status_style}]{score_str}[/]",
            f"[{status_style}]{status}[/]",
            c.get("source", ""),
            c.get("note", ""),
        )
    console.print(table)
    failed = [k for k, v in (report.get("quality") or {}).items() if v == "failed"]
    if failed:
        console.print(f"  [yellow]⚠ 历史数据更新失败（沿用旧序列）: {','.join(failed)}"
                      f"——相关成分分位可能偏旧[/]")

    # 人话总结（模板填数，非 AI）
    zt = next((c for c in report.get("components", []) if c["name"] == "zt_heat"), None)
    lines = []
    if score is not None:
        lines.append(f"市场恐慌指数 {score:.1f}（{tier}）。")
    if zt and zt.get("extra"):
        e = zt["extra"]
        line = f"今日涨停 {e.get('zt', '?')} 家"
        if e.get("dt") is not None:
            line += f"、跌停 {e.get('dt')} 家"
        if e.get("zb_rate") is not None:
            line += f"、炸板率 {e.get('zb_rate'):.0f}%"
        lines.append(line + "。")
    if missing:
        lines.append(f"注意：{','.join(missing)} 数据缺失，总分由其余成分重归一。")
    if lines:
        console.print("  📖 " + "".join(lines))


def show_history(summary: dict) -> None:
    """渲染多周期回顾：摘要表 + 人话总结 + 图片路径。"""
    console.print(f"\n[bold]📊 恐慌指数多周期回顾（基准 {summary.get('as_of', '?')}，"
                  f"交易日口径）[/]")
    table = Table(expand=False)
    for col, kw in (("周期", {"style": "cyan", "no_wrap": True}),
                    ("区间", {"no_wrap": True}),
                    ("均值", {"justify": "right"}),
                    ("最低/最高", {"justify": "center"}),
                    ("当前位置", {"justify": "right"}),
                    ("趋势", {})):
        table.add_column(col, **kw)
    for w in summary.get("windows", []):
        table.add_row(
            w["label"],
            f"{w['start']} → {w['end']}",
            f"{w['mean']:.1f}",
            f"{w['min']:.0f} / {w['max']:.0f}",
            _fmt_rank(w),
            _fmt_trend(w),
        )
    console.print(table)
    headline = summary.get("headline")
    if headline:
        console.print(f"  📖 {headline}")
    chart_path = summary.get("chart_path")
    if chart_path:
        console.print(f"  🖼 走势图已保存: {chart_path}")
    elif summary.get("chart_error"):
        console.print(f"  [yellow]⚠ {summary['chart_error']}[/]")


def print_backfill_progress(i: int, total: int, date: str, ok: bool) -> None:
    """fear backfill 的进度显示（每 10 日 + 最后一日打印，避免刷屏）。"""
    if i % 10 == 0 or i == total:
        mark = "✓" if ok else "✗"
        console.print(f"  [{i}/{total}] {date} {mark}")


def _fmt_raw(c: dict) -> str:
    raw, name = c.get("raw"), c.get("name", "")
    if raw is None:
        return "--"
    if name == "breadth":
        return f"{raw:.0%}"
    if name == "turnover":
        return f"{raw:.2f}万亿"
    if name in ("volatility",):
        return f"{raw:.2f}%"
    if name in ("momentum", "margin"):
        return f"{raw:+.2f}%"
    if name == "erp":
        return f"{raw:.2f}pct"
    if name == "zt_heat":
        e = c.get("extra") or {}
        dt = e.get("dt")
        return f"跌停{dt if dt is not None else '--'}家"
    return f"{raw:.2f}"


def _fmt_rank(w: dict) -> str:
    r = w.get("cur_rank")
    if r is None:
        return "--"
    pos = "高位" if r >= 80 else "偏高位" if r >= 60 else "中位" if r >= 40 else "偏低位" if r >= 20 else "低位"
    return f"{r:.0f}%分位({pos})"


def _fmt_trend(w: dict) -> str:
    t = w.get("trend", "")
    style = {"恐慌加深": "cyan", "恐慌缓和": "yellow", "震荡": "white"}.get(t, "white")
    return f"[{style}]{t}[/]" if t else "--"
