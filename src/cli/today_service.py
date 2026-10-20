"""today 统一行动工作台（plan/fusion F7，DESIGN ADR-F08）：先持仓风险，再机会。

分组（DESIGN ADR-F08 原文）：`today` 分"需要处理""等待条件""继续持有"三组——
紧急风险始终显示全部数量与入口；无操作是合法且清晰的结果，不为了显得有用每天
推荐新票。

数据源（全部 F1 已落地的事实/建议分离语义）：
- PortfolioManager.request_account_facts()——已确认持仓（事实）
- PortfolioManager.pending_proposals()——待确认建议（"这是建议，尚未记为成交"）
- session_state 观察池——等待条件组
- holding_verification——LEGACY_UNVERIFIED 核对提示

渲染语义与 action_view 同源（行动+阻塞+下次关注）；卡片收敛到 action_view.
render_action_card 的统一路径随 chat/Web 接线迭代（F7 审查 P2-9b 登记，当前两套
渲染语义并存——跨批 TODO 见 EXECUTION_RECORD）。
"""

from typing import Optional

from pydantic import BaseModel, Field

from src.data.portfolio import PortfolioManager, _normalize_stock_code

_ACTION_CN = {"OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓", "CLOSE_ALL": "清仓"}


class TodayCard(BaseModel):
    """一张行动卡（今天该做什么——顺序：行动→理由→阻塞→下次关注）。"""

    stock_code: str
    stock_name: str = ""
    bucket: str = Field(description="needs_action / waiting / holding")
    lines: list[str] = Field(default_factory=list)
    position_state: str = ""
    quantity: Optional[int] = None
    confirmed_weight: Optional[float] = None
    weight_reason: str = ""
    account_version: str = ""


class TodayView(BaseModel):
    """today 视图（三组 + 提示；紧急风险不因分组隐藏）。"""

    needs_action: list[TodayCard] = Field(default_factory=list, description="需要处理（待确认建议/硬提示）")
    waiting: list[TodayCard] = Field(default_factory=list, description="等待条件（观察池/过期建议）")
    holding: list[TodayCard] = Field(default_factory=list, description="继续持有（无待办持仓）")
    notices: list[str] = Field(default_factory=list, description="全组提示（LEGACY 核对等）")
    account: list[str] = Field(default_factory=list, description="账户事实区（J2：同一账户服务——现金/股数/隔离冻结；空=无数量级账本）")
    as_of: str = ""
    account_version: str = ""
    holdings_incomplete: bool = False

    @property
    def needs_action_count(self) -> int:
        return len(self.needs_action)


def build_today_view(pm: PortfolioManager, *, watch_entries: Optional[list[dict]] = None,
                     risk_profile: Optional[dict] = None,
                     account=None, account_facts=None) -> TodayView:
    """从持仓事实+待确认建议构建 today 视图（纯读取，零写入零网络）。

    watch_entries: 观察池在池明细 [{code, name, price, ts, source}]（ISS-104：
    用户走查——只给数量用户不知道「在等什么」，需逐只列出）。
    risk_profile: 用户风险档（settings.yaml fusion.risk_profile，ISS-108）——
    逐只显示单股额度占用；行业映射未接线，industry_max 暂不核对（如实标注）。
    account_facts: 同请求 RequestAccountFacts，清单/上下文/账户区共享快照与版本。
    account: 旧直调兼容 AccountSnapshot（J2——today 调同一账户服务的读模型）：现金/股数/
    隔离冻结、卡片与版本共用显式快照；None=缺省读取，account_facts 优先。"""
    from datetime import datetime
    view = TodayView(as_of=datetime.now().strftime("%Y-%m-%d %H:%M"))
    if account_facts is not None:
        facts = account_facts
    elif account is not None:
        facts = pm.request_account_facts(account_snapshot=account)
    else:
        facts = pm.request_account_facts()
    entries, incomplete = facts.holding_entries()
    positions = {p.stock_code: p for p in entries}
    contexts = {code: facts.context_for(code) for code in positions}
    view.account_version = facts.account_version
    view.holdings_incomplete = incomplete
    account = getattr(facts, "account_snapshot", None)
    if incomplete:
        view.notices.append("持仓清单可能不全——账户未对账，不能据此证明空仓")
    for code, reason in facts.projection_gaps.items():
        view.notices.append(f"{code} {reason}")
    if getattr(facts, "_read_failed", False):
        view.account.append("账户账本读取失败——账户事实未知，不能证明空仓；下一次命令重新读取")
    if view.account_version:
        view.account.append(f"账户版本 {view.account_version}")

    def holding_detail(card, code):
        ctx = contexts[code]
        card.position_state = ctx.position_state
        card.quantity = ctx.quantity
        card.confirmed_weight = ctx.confirmed_weight
        card.weight_reason = ctx.weight_reason
        card.account_version = ctx.account_version
        state = {"HELD": "持有", "NONE": "无持仓", "UNKNOWN": "持仓待核"}.get(ctx.position_state, "持仓待核")
        snapshot = getattr(facts, "account_snapshot", None)
        ledger_codes = {_normalize_stock_code(h.security_id) for h in snapshot.holdings
                        if h.quantity > 0} if snapshot is not None else set()
        source = "事件账本" if code in ledger_codes else "数量投影"
        if ctx.position_state == "UNKNOWN":
            source = "旧数量投影，待核"
        quantity = (f"持仓 {ctx.quantity}股（{source}）" if ctx.quantity is not None
                    else "股数未记录/待核")
        weight = (f"仓位 {ctx.confirmed_weight:.0%}" if ctx.confirmed_weight is not None
                  else "权重待对账/待重估")
        return f"{state} ｜ {quantity} ｜ {weight} ｜ {ctx.weight_reason}"

    pending = pm.pending_proposals()
    pending_by_code: dict[str, list] = {}
    for prop in pending:
        pending_by_code.setdefault(_normalize_stock_code(prop.stock_code) or prop.stock_code, []).append(prop)

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
            card.lines.append(holding_detail(card, code) + "（记录未变——建议尚未记为成交）")
        view.needs_action.append(card)

    # 继续持有：无待办的持仓
    legacy_count = 0
    cap = None
    if risk_profile:
        try:
            cap = float(risk_profile.get("per_stock_max")) if risk_profile.get("per_stock_max") is not None else None
        except (TypeError, ValueError):
            cap = None
    over_cap: list[str] = []
    for code in sorted(positions):
        if code in pending_by_code:
            continue
        pos = positions[code]
        ctx = contexts[code]
        metadata = ctx.projection
        if metadata is not None and metadata.holding_verification == "LEGACY_UNVERIFIED":
            legacy_count += 1
        card = TodayCard(stock_code=code, stock_name=pos.stock_name, bucket="holding")
        detail = holding_detail(card, code)
        if metadata is not None:
            if (metadata.quantity_fact is not None and metadata.quantity_held != ctx.quantity
                    and ctx.quantity is not None):
                detail += f" ｜ 旧数量投影 {metadata.quantity_held}股，待对账"
            if metadata.entry_price and metadata.entry_price > 0:
                detail += f" ｜ 投影成本 {metadata.entry_price}"
            if metadata.lifecycle and ctx.position_state == "HELD" and metadata.lifecycle != "FLAT":
                detail += f" ｜ {metadata.lifecycle}"
        weight = ctx.confirmed_weight
        if weight is not None and cap is not None and cap > 0:
            detail += f" ｜ 单股额度 {weight:.0%}/{cap:.0%}"
            if weight > cap + 1e-9:
                over_cap.append(f"{code} {pos.stock_name} {weight:.0%}")
        suffix = "无待办——今天不用操作" if ctx.position_state != "UNKNOWN" else "无待确认建议——先核对持仓事实"
        card.lines.append(f"{detail} ｜ {suffix}")
        view.holding.append(card)
    if over_cap:
        view.notices.append(
            f"⚠ 单股额度超限（用户档 {cap:.0%}）：{'、'.join(over_cap)}"
            "——多为价格上涨漂移所致，注意集中度；行业额度需行业映射（未接线，暂不核对）")

    # 等待条件：观察池逐只明细（ISS-104：在等的是「这些股的买入条件触发」）
    if watch_entries:
        view.waiting.append(TodayCard(
            stock_code="", bucket="waiting",
            lines=[f"观察池 {len(watch_entries)} 只——在等它们的买入条件触发"
                   f"（watch 看入池价→现价涨跌）"]))
        for e in watch_entries:
            line = f"{e.get('code')} {e.get('name') or ''}  ｜ 入池 {str(e.get('ts') or '')[:10]}"
            if e.get("source"):
                line += f"（来自 {e['source']}）"
            if e.get("price"):
                line += f"  入池价 {e['price']}"
            view.waiting.append(TodayCard(
                stock_code=str(e.get("code") or ""), stock_name=str(e.get("name") or ""),
                bucket="waiting", lines=[line]))

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

    # ── 账户事实区（J2：today 调同一账户服务——现金/股数/隔离冻结如实显示）──
    if account is not None:
        isolated = list(getattr(account, "isolated_events", []) or [])
        if isolated:
            view.notices.append(
                f"⚠ 账本存在 {len(isolated)} 条待核对事件（{'、'.join(e.get('event_id', '') for e in isolated)}）"
                "——精确新增冻结，先人工核对账户事件账本")
        deployable = account.cash_deployable
        nav = getattr(account, "nav", None)
        if nav is not None:
            at = getattr(account, "nav_priced_at", None)
            view.account.append(f"总资产(NAV) {nav:,.2f} 元（现金+持仓×同日行情"
                                + (f"，定价 {at:%m-%d %H:%M}" if at else "") + "）")
        if deployable is not None:
            line = f"可用现金 {deployable:,.0f} 元"
            if account.cash_reserved:
                # K0a/D7：cash_available 已是扣冻结净额——reserved 是额外的占用解释，
                # 不在可用额内（K0a 审查 P3-1：措辞不得暗示包含关系）
                line += f"（另有已占用/冻结 {account.cash_reserved:,.0f} 元，不在上述可用额内）"
            view.account.append(line)
        else:
            view.account.append("现金未知（未导入期初/期初现金缺失——不产可买数量，None≠0）")
        qty_holdings = [h for h in account.holdings if h.quantity > 0]
        if qty_holdings:
            for h in qty_holdings:
                cost = f"，平均成本 {h.avg_cost:.2f}" if h.avg_cost else ""
                view.account.append(f"{h.security_id} 持有 {h.quantity} 股{cost}")
        elif incomplete:
            view.account.append("数量级持仓清单不完整——待对账，不能证明空仓")
        elif deployable is None:
            view.account.append("数量级持仓/现金未建立——当前仅比例视图"
                                "（pos opening 锚定期初现金、pos openinglot 导入持仓批次、"
                                "pos confirm --qty 记成交后此处显示股数/现金）")
        else:
            view.account.append("事件账本无正数量持仓（比例投影兼容持仓另见卡片，不据此认定整个账户空仓）")
    return view


def render_today(view: TodayView) -> str:
    """人话渲染（REPL 用；chat/Web 复用同一服务时自行包 UI）。"""
    out = [f"📅 今日工作台  {view.as_of}"]
    if view.account:
        out.append(f"\n[cyan]💰 账户事实（事件账本——同一账户服务）[/cyan]")
        for line in view.account:
            out.append(f"  {line}")
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
        n_stocks = sum(1 for c in view.waiting if c.stock_code)
        out.append(f"\n[dim]⏳ 等待条件（观察池 {n_stocks} 只）[/dim]")
        for card in view.waiting:
            for line in card.lines:
                out.append(f"  [dim]{line}[/dim]")
    for n in view.notices:
        out.append(f"\n[yellow]{n}[/yellow]")
    out.append("\n[dim]无操作是合法结果——没有信号就不动。完整依据: l <代码> / pos / watch[/dim]")
    return "\n".join(out)
