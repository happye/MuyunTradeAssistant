"""结构化数据 → 纯文本格式化器 (Chat Agent Mode)

所有输出均为纯文本（无Rich标记），因为：
1. AI需要纯文本输入
2. Chat模式的用户输出也用print()纯文本
"""


def format_industry_list(industries: list[dict], keyword: str) -> str:
    """格式化行业板块列表"""
    lines = [f"行业板块（搜索'{keyword}'，共{len(industries)}个）："]
    for ind in industries[:30]:
        change = ind.get("change_pct", 0)
        arrow = "+" if change > 0 else ""
        up = ind.get("up_count", "?")
        down = ind.get("down_count", "?")
        lead = ind.get("lead_stock", "")
        lead_str = f" 领涨:{lead}" if lead else ""
        lines.append(
            f"  {ind['name']:12s} {arrow}{change:.2f}%  "
            f"涨{up}/跌{down}{lead_str}"
        )
    if len(industries) > 30:
        lines.append(f"  ... 共{len(industries)}个，仅显示前30个")
    return "\n".join(lines)


def format_basic_quote(stock_data) -> str:
    """格式化基础行情（无技术指标时）"""
    lines = [
        f"{stock_data.stock_name} ({stock_data.stock_code})",
        f"  当前价: {stock_data.price}",
    ]
    if stock_data.change_pct is not None:
        lines.append(f"  涨跌幅: {stock_data.change_pct}%")
    return "\n".join(lines)


def format_analysis_result(
    stock_data, decision_result, strategy_decision,
    execution_eval, ai_result
) -> str:
    """格式化完整分析结果（纯文本版本）"""
    lines = []

    # 基本信息
    lines.append(f"=== {stock_data.stock_name} ({stock_data.stock_code}) 分析报告 ===")
    lines.append("")
    lines.append(f"当前价: {stock_data.price}  涨跌幅: {stock_data.change_pct}%")
    lines.append(f"市场状态: {decision_result.state.value}")
    lines.append(f"综合决策: {decision_result.decision.value}  评分: {decision_result.score:.2f}")

    # 仓位动作
    pos_action_map = {
        "OPEN": "试探建仓", "ADD": "加仓", "REDUCE": "减仓",
        "CLOSE_ALL": "全部清仓", "HOLD_POSITION": "维持仓位", "STAY_OUT": "空仓观望"
    }
    pos_action = pos_action_map.get(
        strategy_decision.position_action.value,
        strategy_decision.position_action.value
    )
    lines.append(f"仓位动作: {pos_action}")

    # 技术指标
    indicators = []
    if stock_data.ma5:
        indicators.append(f"MA5={stock_data.ma5:.2f}")
    if stock_data.ma20:
        indicators.append(f"MA20={stock_data.ma20:.2f}")
    if stock_data.ma60:
        indicators.append(f"MA60={stock_data.ma60:.2f}")
    if stock_data.macd_dif is not None:
        indicators.append(f"MACD_DIF={stock_data.macd_dif:.3f}")
    if stock_data.rsi_6 is not None:
        indicators.append(f"RSI-6={stock_data.rsi_6:.1f}")
    if stock_data.boll_upper is not None:
        indicators.append(
            f"BOLL={stock_data.boll_lower:.1f}~{stock_data.boll_upper:.1f}"
        )
    if stock_data.kdj_k is not None:
        indicators.append(
            f"KDJ={stock_data.kdj_k:.1f}/{stock_data.kdj_d:.1f}/{stock_data.kdj_j:.1f}"
        )
    if indicators:
        lines.append(f"技术指标: {', '.join(indicators)}")

    # 各技能信号
    lines.append("")
    lines.append("--- 各技能信号 ---")
    for sig in decision_result.signals:
        reasons = ", ".join(sig.reason[:2]) if sig.reason else "-"
        lines.append(
            f"  {sig.skill_alias}: {sig.signal.value} "
            f"(置信度{sig.confidence:.0%}) - {reasons}"
        )

    # AI情绪
    if ai_result and ai_result.adjusted:
        sentiment_cn = {
            "bullish": "看多", "bearish": "看空", "neutral": "中性"
        }
        lines.append("")
        lines.append("--- AI情绪分析 ---")
        sent_cn = sentiment_cn.get(ai_result.sentiment, ai_result.sentiment)
        lines.append(f"  情绪: {sent_cn} (置信度: {ai_result.confidence:.0%})")
        if ai_result.event_type and ai_result.event_type != "none":
            event_cn = {
                "policy": "政策", "war": "地缘冲突", "earnings": "财报",
                "macro": "宏观", "black_swan": "黑天鹅"
            }
            lines.append(f"  事件: {event_cn.get(ai_result.event_type, ai_result.event_type)}")
        if ai_result.score_adjustment != 0:
            pct = abs(ai_result.score_adjustment) * 100
            direction = "下调" if ai_result.score_adjustment < 0 else "上调"
            lines.append(f"  信号{direction}{pct:.0f}%")
        if ai_result.position_cap < 1.0:
            lines.append(f"  仓位上限: {ai_result.position_cap:.0%}")
        if ai_result.summary:
            lines.append(f"  摘要: {ai_result.summary}")
    elif ai_result and not ai_result.adjusted:
        lines.append("")
        lines.append(f"--- AI情绪: 未生效({ai_result.summary or '未知'}) ---")

    # 策略层信息
    if strategy_decision:
        lines.append("")
        lines.append("--- 策略层 ---")
        lines.append(
            f"  生命周期: {strategy_decision.lifecycle_before.value} "
            f"→ {strategy_decision.lifecycle_after.value}"
        )
        lines.append(f"  信号稳定性: {strategy_decision.new_state.signal_stability_score:.0%}")
        if strategy_decision.strategy_reasons:
            lines.append(
                f"  策略理由: {'; '.join(strategy_decision.strategy_reasons[:3])}"
            )

    # 执行层
    if execution_eval and execution_eval.blocked:
        lines.append("")
        lines.append(f"执行约束: {execution_eval.block_reason}")

    # 决策理由
    if decision_result.reason:
        lines.append("")
        lines.append("--- 决策理由 ---")
        for i, reason in enumerate(decision_result.reason, 1):
            lines.append(f"  {i}. {reason}")

    # 风险提示
    if decision_result.warnings:
        lines.append("")
        lines.append("--- 风险提示 ---")
        for w in decision_result.warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)


def format_scan_result(candidates: list, scan_info: dict) -> str:
    """格式化市场扫描结果"""
    lines = []
    rule_display = scan_info.get("rule_display_name", scan_info.get("rule_name", "default"))
    lines.append(f"=== 全市场扫描: {rule_display} ===")
    lines.append(
        f"初筛: 全市场{scan_info.get('total_stocks', '?')}只 → "
        f"{len(candidates)}只匹配 ({scan_info.get('elapsed_seconds', 0)}秒)"
    )
    lines.append("")

    for i, c in enumerate(candidates[:20], 1):
        change_str = f"{c.change_pct:+.2f}%" if c.change_pct is not None else "-"
        amount_str = f"{c.amount / 1e8:.1f}亿" if c.amount else "-"
        turnover_str = f"换手{c.turnover_rate:.1f}%" if c.turnover_rate else ""
        volume_str = f"量比{c.volume_ratio:.1f}" if c.volume_ratio else ""

        lines.append(
            f"  {i:2d}. {c.stock_code} {c.stock_name:8s} "
            f"价格{c.price:.2f} {change_str} {amount_str} {turnover_str} {volume_str}"
        )

    if len(candidates) > 20:
        lines.append(f"  ... 共{len(candidates)}只，仅显示前20只")

    lines.append("")
    lines.append("提示: 使用 analyze_stock 分析感兴趣的股票")

    return "\n".join(lines)


def format_portfolio(positions: list) -> str:
    """格式化持仓列表"""
    lines = ["=== 当前持仓 ==="]
    lines.append("")

    for pos in positions:
        price_str = f"开仓价:{pos.entry_price:.2f}" if pos.entry_price else ""
        lines.append(
            f"  {pos.stock_code} {pos.stock_name or '-':8s} "
            f"仓位:{pos.current_ratio:.0%} {price_str} "
            f"生命周期:{pos.lifecycle} "
            f"上次操作:{pos.last_action}({pos.last_action_date or '-'})"
        )

    return "\n".join(lines)


def format_news(stock_code: str, stock_news: list, macro_news: list) -> str:
    """格式化新闻列表"""
    lines = [f"=== {stock_code} 新闻 ==="]
    lines.append("")

    if stock_news:
        lines.append("--- 个股新闻 ---")
        for i, news in enumerate(stock_news[:5], 1):
            title = news.get("title", "")
            source = news.get("source", "")
            time_str = news.get("time", "")
            lines.append(f"  {i}. [{time_str}] {title} — {source}")

    if macro_news:
        lines.append("")
        lines.append("--- 宏观快讯 ---")
        for i, news in enumerate(macro_news[:5], 1):
            title = news.get("title", "")
            lines.append(f"  {i}. {title}")

    if not stock_news and not macro_news:
        lines.append("  暂无相关新闻")

    return "\n".join(lines)
