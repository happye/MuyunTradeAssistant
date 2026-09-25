"""today 统一行动工作台（plan/fusion F7，DESIGN ADR-F08）：先持仓风险，再机会。

分组（DESIGN ADR-F08 原文）：`today` 分"需要处理""等待条件""继续持有"三组——
紧急风险始终显示全部数量与入口；无操作是合法且清晰的结果，不为了显得有用每天
推荐新票。

数据源（全部 F1 已落地的事实/建议分离语义）：
- PortfolioManager.list_positions()——已确认持仓（事实）
- PortfolioManager.pending_proposals()——待确认建议（"这是建议，尚未记为成交"）
- session_state 观察池——等待条件组
- holding_verification——LEGACY_UNVERIFIED 核对提示

渲染语义与 action_view 同源（行动+阻塞+下次关注）；卡片收敛到 action_view.
render_action_card 的统一路径随 chat/Web 接线迭代（F7 审查 P2-9b 登记，当前两套
渲染语义并存——跨批 TODO 见 EXECUTION_RECORD）。
"""

from typing import Optional

from pydantic import BaseModel, Field

from src.data.portfolio import PortfolioManager

_ACTION_CN = {"OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓", "CLOSE_ALL": "清仓"}


class TodayCard(BaseModel):
    """一张行动卡（今天该做什么——顺序：行动→理由→阻塞→下次关注）。"""

    stock_code: str
    stock_name: str = ""
    bucket: str = Field(description="needs_action / waiting / holding")
    lines: list[str] = Field(default_factory=list)


class TodayView(BaseModel):
    """today 视图（三组 + 提示；紧急风险不因分组隐藏）。"""

    needs_action: list[TodayCard] = Field(default_factory=list, description="需要处理（待确认建议/硬提示）")
    waiting: list[TodayCard] = Field(default_factory=list, description="等待条件（观察池/过期建议）")
    holding: list[TodayCard] = Field(default_factory=list, description="继续持有（无待办持仓）")
    notices: list[str] = Field(default_factory=list, description="全组提示（LEGACY 核对等）")
    as_of: str = ""

    @property
    def needs_action_count(self) -> int:
        return len(self.needs_action)


def build_today_view(pm: PortfolioManager, *, watch_count: Optional[int] = None) -> TodayView:
    """从持仓事实+待确认建议构建 today 视图（纯读取，零写入零网络）。"""
    from datetime import datetime
    view = TodayView(as_of=datetime.now().strftime("%Y-%m-%d %H:%M"))
    positions = {p.stock_code: p for p in pm.list_positions()}
    pending = pm.pending_proposals()
    pending_by_code: dict[str, list] = {}
    for prop in pending:
        pending_by_code.setdefault(prop.stock_code, []).append(prop)

    # 需要处理：有未确认建议的持仓（建议≠成交——用户可见主线）
    for code in sorted(pending_by_code):
        pos = positions.get(code)
        props = pending_by_code[code]
        card = TodayCard(
            stock_code=code,
            stock_name=(pos.stock_name if pos else "") or (props[0].stock_name or ""),
            bucket="needs_action",
        )
        for prop in props:
            tgt = f" → 目标仓位 {prop.target_ratio:.0%}" if prop.target_ratio is not None else ""
            action_cn = _ACTION_CN.get(prop.position_action, prop.position_action)
            status = "" if prop.status.value == "PROPOSED" else f"（{prop.status.value}：部分已成交，剩余待办）"
            card.lines.append(
                f"建议{action_cn}{tgt}{status} ｜ {prop.reason[:40]}"
                f" ｜ {prop.created_at[:10]} 来自 {prop.source or '?'}"
                f" ｜ 实际成交后: pos confirm {code}")
        if pos is not None:
            card.lines.append(f"当前持仓 {pos.current_ratio:.0%}（记录未变——建议尚未记为成交）")
        view.needs_action.append(card)

    # 继续持有：无待办的持仓
    legacy_count = 0
    for code in sorted(positions):
        if code in pending_by_code:
            continue
        pos = positions[code]
        if pos.holding_verification == "LEGACY_UNVERIFIED":
            legacy_count += 1
        card = TodayCard(stock_code=code, stock_name=pos.stock_name, bucket="holding")
        detail = f"仓位 {pos.current_ratio:.0%}"
        if pos.entry_price and pos.entry_price > 0:
            detail += f" ｜ 成本 {pos.entry_price}"
        if pos.lifecycle:
            detail += f" ｜ {pos.lifecycle}"
        card.lines.append(f"{detail} ｜ 无待办——今天不用操作")
        view.holding.append(card)

    # 等待条件：观察池（数量入口；完整列表 watch 命令看）
    if watch_count:
        view.waiting.append(TodayCard(
            stock_code="", bucket="waiting",
            lines=[f"观察池 {watch_count} 只在池（watch 查看入池价→现价）"]))

    # 全组提示（紧急/核对类不隐藏）
    total = len(positions)
    if legacy_count and legacy_count == total:
        view.notices.append(
            f"⚠ 全部 {legacy_count} 条旧持仓无成交来源标记（升级前录入）——数值未改动，"
            f"建议核对一遍实际持仓")
    elif legacy_count:
        view.notices.append(
            f"⚠ {legacy_count}/{total} 条旧持仓无成交来源标记——数值未改动，建议核对")
    # F7 审查 P2-8：公开 API 计数（近 7 天过期），文案不再对"被新建议取代"型过期撒谎
    recent_expired = pm._proposals.count_recently_expired(7)
    if recent_expired:
        view.notices.append(f"近 7 天有 {recent_expired} 条建议未确认而失效——"
                            f"重新分析会生成新建议")
    if getattr(pm, "_corrupted", False):
        # F7 审查 P2-11：持仓文件损坏时今日视图静默空态=谎报——必须显式提示
        view.notices.append("⚠ 持仓文件此前读取失败（损坏保护生效）——"
                            "下面显示的不是你的真实持仓，请先修复 portfolio.yaml")
    return view


def render_today(view: TodayView) -> str:
    """人话渲染（REPL 用；chat/Web 复用同一服务时自行包 UI）。"""
    out = [f"📅 今日工作台  {view.as_of}"]
    if view.needs_action:
        out.append(f"\n[bold yellow]🟠 需要处理（{len(view.needs_action)}）——这是建议，尚未记为成交[/bold yellow]")
        for card in view.needs_action:
            out.append(f"  [cyan]{card.stock_code}[/cyan] {card.stock_name}")
            for line in card.lines:
                out.append(f"    {line}")
    else:
        out.append("\n[green]✅ 需要处理：无——没有等你确认的建议[/green]")
    if view.holding:
        out.append(f"\n[cyan]💼 继续持有（{len(view.holding)}）[/cyan]")
        for card in view.holding:
            out.append(f"  [cyan]{card.stock_code}[/cyan] {card.stock_name} ｜ {card.lines[0]}")
    if view.waiting:
        out.append(f"\n[dim]⏳ 等待条件（{len(view.waiting)}）[/dim]")
        for card in view.waiting:
            out.append(f"  [dim]{card.lines[0]}[/dim]")
    for n in view.notices:
        out.append(f"\n[yellow]{n}[/yellow]")
    out.append("\n[dim]无操作是合法结果——没有信号就不动。完整依据: l <代码> / pos / watch[/dim]")
    return "\n".join(out)
