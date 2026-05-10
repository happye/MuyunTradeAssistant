"""回测分析导出器。

基于现有 BacktestResult 组装 AI 可消费的分析载荷，
不改变回测引擎本身的统计口径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.data.models import BacktestResult


LAYER_COMPARE_ORDER = [
    "decision_only",
    "decision_strategy",
    "decision_strategy_execution",
]


def build_analysis_payload(result: BacktestResult, backtest_mode: str, layer_mode: str | None = None) -> dict[str, Any]:
    """构造 AI 分析导出载荷。"""
    layer_mode = layer_mode or result.layer_mode
    total_buy_amount = sum(trade.amount for trade in result.trades if trade.action == "BUY")
    total_sell_amount = sum(trade.amount for trade in result.trades if trade.action == "SELL")
    net_profit = total_sell_amount - total_buy_amount
    excess_return_pct = round(result.total_return_pct - result.benchmark_return_pct, 2)
    diagnostics = result.diagnostics or {}
    action_source_table = _build_action_source_table(result, diagnostics)
    hold_break_table = _build_hold_break_table(result, diagnostics)
    has_real_diagnostics = bool(diagnostics.get("execution_logs"))

    payload = {
        "meta": {
            "stock_code": result.stock_code,
            "stock_name": result.stock_name,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "initial_capital": result.initial_capital,
            "final_value": result.final_value,
            "backtest_mode": backtest_mode,
            "analysis_version": "v0.8.2",
            "layer_mode": layer_mode,
        },
        "summary": {
            "total_return_pct": result.total_return_pct,
            "benchmark_return_pct": result.benchmark_return_pct,
            "excess_return_pct": excess_return_pct,
            "invested_return_pct": result.invested_return_pct,
            "annualized_return_pct": result.annualized_return_pct,
            "max_drawdown_pct": result.max_drawdown_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "win_rate": result.win_rate,
            "profit_loss_ratio": result.profit_loss_ratio,
            "total_trades": result.total_trades,
            "buy_count": result.buy_count,
            "sell_count": result.sell_count,
            "total_buy_amount": round(total_buy_amount, 2),
            "total_sell_amount": round(total_sell_amount, 2),
            "net_profit": round(net_profit, 2),
            "decision_stability": result.decision_stability,
            "drawdown_stability": result.drawdown_stability,
            "worst_case_return_pct": result.worst_case_return_pct,
            "result_variance": result.result_variance,
            "mc_simulations": result.mc_simulations,
            "blocked_by_limit_up": result.blocked_by_limit_up,
            "blocked_by_limit_down": result.blocked_by_limit_down,
            "blocked_by_liquidity": result.blocked_by_liquidity,
        },
        "trades": [trade.model_dump() for trade in result.trades],
        "daily_snapshots": [snapshot.model_dump() for snapshot in result.daily_snapshots],
        "layer_breakdown": [
            {
                "layer_mode": layer_mode,
                "total_return_pct": result.total_return_pct,
                "benchmark_return_pct": result.benchmark_return_pct,
                "excess_return_pct": excess_return_pct,
                "max_drawdown_pct": result.max_drawdown_pct,
                "total_trades": result.total_trades,
                "buy_count": result.buy_count,
                "sell_count": result.sell_count,
            }
        ],
        "action_source_table": action_source_table,
        "hold_break_table": hold_break_table,
        "diagnostics": diagnostics,
        "analysis_hints": _build_analysis_hints(result, excess_return_pct),
        "limitations": [
            "当前导出一次只包含本次回测结果；若要横向比较 decision_only / decision_strategy / decision_strategy_execution，需分别运行并导出多个文件。",
            "当前导出文件不会自动归档到固定目录，是否持久化取决于是否显式传入导出路径。",
            "若需更细粒度的逐日归因模型，后续仍可继续补充专用诊断字段与对照分析器。",
        ],
    }
    if not has_real_diagnostics:
        payload["limitations"].insert(1, "动作来源与持仓破坏表当前由交易记录和原因字段推断，不是完整逐日归因日志。")
    return payload


def export_analysis_json(result: BacktestResult, backtest_mode: str, output_path: str, layer_mode: str | None = None) -> Path:
    """导出结构化 JSON。"""
    payload = build_analysis_payload(result, backtest_mode, layer_mode)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def export_analysis_txt(result: BacktestResult, backtest_mode: str, output_path: str, layer_mode: str | None = None) -> Path:
    """导出面向人工和 AI 的文本摘要。"""
    payload = build_analysis_payload(result, backtest_mode, layer_mode)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_text_report(payload), encoding="utf-8")
    return target


def build_merged_timeline(results_by_layer: dict[str, BacktestResult]) -> dict[str, Any]:
    """构建逐日三层对照 merged timeline。

    将 decision_only / decision_strategy / decision_strategy_execution 的
    daily_decisions + execution_logs 按信号日期对齐，形成统一逐日视图。
    """
    # 从三层结果中提取 daily_decisions，按日期建索引
    dd_index_by_layer: dict[str, dict[str, dict[str, Any]]] = {}
    for layer_name in LAYER_COMPARE_ORDER:
        result = results_by_layer.get(layer_name)
        if not result:
            continue
        daily_decisions = result.diagnostics.get("daily_decisions") or []
        dd_index_by_layer[layer_name] = {
            entry["date"]: entry for entry in daily_decisions
        }

    # 从 decision_strategy_execution 的 execution_logs 按信号日期建索引
    exec_index: dict[str, dict[str, Any]] = {}
    full_result = results_by_layer.get("decision_strategy_execution")
    if full_result:
        for log in (full_result.diagnostics.get("execution_logs") or []):
            signal_date = log.get("signal_date")
            if signal_date:
                exec_index[signal_date] = log

    # 收集所有出现过的信号日期
    all_dates: set[str] = set()
    for idx in dd_index_by_layer.values():
        all_dates.update(idx.keys())
    all_dates.update(exec_index.keys())
    sorted_dates = sorted(all_dates)

    timeline: list[dict[str, Any]] = []
    strategy_modified_count = 0
    execution_blocked_count = 0
    strategy_and_execution_count = 0
    divergence_count = 0

    for date in sorted_dates:
        # Decision 层信号
        decision_only_dd = dd_index_by_layer.get("decision_only", {}).get(date)
        decision_entry = _extract_decision_snapshot(decision_only_dd)

        # Strategy 层信号
        decision_strategy_dd = dd_index_by_layer.get("decision_strategy", {}).get(date)
        strategy_entry = _extract_strategy_snapshot(decision_strategy_dd)

        # Execution 层结果（来自完整模式的 execution_logs）
        execution_entry = _extract_execution_snapshot(exec_index.get(date))

        # 计算层间分歧
        divergence = _classify_divergence(decision_entry, strategy_entry, execution_entry)

        if divergence != "none":
            divergence_count += 1
        if divergence in ("strategy_modified", "strategy_and_execution"):
            strategy_modified_count += 1
        if divergence in ("execution_blocked", "strategy_and_execution"):
            execution_blocked_count += 1
        if divergence == "strategy_and_execution":
            strategy_and_execution_count += 1

        timeline.append({
            "date": date,
            "decision": decision_entry,
            "strategy": strategy_entry,
            "execution": execution_entry,
            "divergence": divergence,
        })

    return {
        "timeline": timeline,
        "divergence_summary": {
            "total_signal_days": len(sorted_dates),
            "divergence_days": divergence_count,
            "strategy_modified_days": strategy_modified_count,
            "execution_blocked_days": execution_blocked_count,
            "strategy_and_execution_days": strategy_and_execution_count,
        },
    }


def _extract_decision_snapshot(dd_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 daily_decisions 条目提取决策层快照。"""
    if not dd_entry:
        return None
    decision = dd_entry.get("decision")
    if not decision:
        return None
    return {
        "decision": decision.get("decision"),
        "score": decision.get("score"),
        "position_action": decision.get("position_action"),
        "position_ratio": decision.get("position_ratio"),
    }


def _extract_strategy_snapshot(dd_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 decision_strategy 的 daily_decisions 条目提取策略层快照。"""
    if not dd_entry:
        return None
    strategy = dd_entry.get("strategy")
    decision = dd_entry.get("decision")
    if not strategy and not decision:
        return None
    result: dict[str, Any] = {}
    if strategy:
        result.update({
            "decision": strategy.get("decision"),
            "position_action": strategy.get("position_action"),
            "position_ratio": strategy.get("position_ratio"),
            "lifecycle": f"{strategy.get('lifecycle_before', '?')}→{strategy.get('lifecycle_after', '?')}",
            "flags": _collect_strategy_flags(strategy),
        })
    elif decision:
        # 策略层未启用，直接透传决策层
        result.update({
            "decision": decision.get("decision"),
            "position_action": decision.get("position_action"),
            "position_ratio": decision.get("position_ratio"),
            "lifecycle": "",
            "flags": [],
        })
    return result


def _extract_execution_snapshot(exec_log: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 execution_logs 条目提取执行层快照。"""
    if not exec_log:
        return None
    execution = exec_log.get("execution")
    trade = exec_log.get("trade")
    return {
        "effective_action": execution.get("effective_action") if execution else None,
        "blocked": execution.get("blocked", False) if execution else False,
        "block_reason": execution.get("block_reason", "") if execution else "",
        "trade_action": trade.get("action") if trade else None,
        "trade_position_action": trade.get("position_action") if trade else None,
    }


def _collect_strategy_flags(strategy: dict[str, Any]) -> list[str]:
    """收集策略层激活的标志。"""
    flags: list[str] = []
    if strategy.get("inertia_applied"):
        flags.append("inertia")
    if strategy.get("confirmation_required"):
        flags.append("confirmation")
    if strategy.get("cooldown_blocked"):
        flags.append("cooldown")
    if strategy.get("reverse_cost_paid"):
        flags.append("reverse_cost")
    if strategy.get("stability_adjusted"):
        flags.append("stability")
    return flags


def _classify_divergence(
    decision: dict[str, Any] | None,
    strategy: dict[str, Any] | None,
    execution: dict[str, Any] | None,
) -> str:
    """判断层间分歧类型。"""
    has_strategy_change = False
    has_execution_change = False

    if decision and strategy:
        # 策略层是否修改了决策层的动作
        d_action = decision.get("position_action")
        s_action = strategy.get("position_action")
        d_decision = decision.get("decision")
        s_decision = strategy.get("decision")
        strategy_flags = strategy.get("flags", [])
        if d_action != s_action or d_decision != s_decision or strategy_flags:
            has_strategy_change = True

    if execution:
        if execution.get("blocked"):
            has_execution_change = True
        # 执行层是否改变了策略层动作
        elif strategy and execution.get("effective_action") != strategy.get("position_action"):
            has_execution_change = True
        elif not strategy and decision and execution.get("effective_action") != decision.get("position_action"):
            has_execution_change = True

    if has_strategy_change and has_execution_change:
        return "strategy_and_execution"
    if has_strategy_change:
        return "strategy_modified"
    if has_execution_change:
        return "execution_blocked"
    return "none"


def build_layer_comparison_payload(results_by_layer: dict[str, BacktestResult], backtest_mode: str) -> dict[str, Any]:
    """构造三层回测对照载荷。"""
    ordered_layers = [layer for layer in LAYER_COMPARE_ORDER if layer in results_by_layer]
    if not ordered_layers:
        raise ValueError("results_by_layer must contain at least one layer result")

    base_result = results_by_layer[ordered_layers[0]]
    layer_breakdown = [_build_layer_breakdown_row(results_by_layer[layer]) for layer in ordered_layers]

    # 构建 merged timeline
    merged_timeline = build_merged_timeline(results_by_layer)

    layer_payloads = {
        layer: build_analysis_payload(results_by_layer[layer], backtest_mode, layer)
        for layer in ordered_layers
    }

    return {
        "meta": {
            "stock_code": base_result.stock_code,
            "stock_name": base_result.stock_name,
            "start_date": base_result.start_date,
            "end_date": base_result.end_date,
            "initial_capital": base_result.initial_capital,
            "backtest_mode": backtest_mode,
            "analysis_version": "v0.8.2",
            "comparison_type": "layer_comparison",
            "layers": ordered_layers,
        },
        "layer_breakdown": layer_breakdown,
        "layer_deltas": _build_layer_deltas(layer_breakdown),
        "comparison_hints": _build_layer_comparison_hints(layer_breakdown),
        "merged_timeline": merged_timeline,
        "layers": layer_payloads,
        "limitations": [
            "当前对照导出会顺序执行多个 layer_mode，因此耗时高于单次回测。",
        ],
    }


def export_layer_comparison_json(results_by_layer: dict[str, BacktestResult], backtest_mode: str, output_path: str) -> Path:
    """导出三层回测对照 JSON。"""
    payload = build_layer_comparison_payload(results_by_layer, backtest_mode)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def export_layer_comparison_txt(results_by_layer: dict[str, BacktestResult], backtest_mode: str, output_path: str) -> Path:
    """导出三层回测对照文本报告。"""
    payload = build_layer_comparison_payload(results_by_layer, backtest_mode)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_layer_comparison_text(payload), encoding="utf-8")
    return target


def _render_layer_comparison_text(payload: dict[str, Any]) -> str:
    """渲染三层回测对照文本报告。"""
    meta = payload["meta"]
    breakdown = payload["layer_breakdown"]
    deltas = payload["layer_deltas"]
    hints = payload["comparison_hints"]
    merged = payload.get("merged_timeline", {})
    timeline = merged.get("timeline", [])
    div_summary = merged.get("divergence_summary", {})

    lines: list[str] = []

    # 标题
    lines.append(f"{meta['stock_name'] or meta['stock_code']} 三层回测对照分析报告")
    lines.append(f"区间: {meta['start_date']} ~ {meta['end_date']}")
    lines.append(f"回测模式: {meta['backtest_mode']}")
    lines.append("")

    # 一、层级汇总
    lines.append("一、层级汇总")
    lines.append("-" * 80)
    header = f"{'层级':<35} {'收益%':>8} {'超额%':>8} {'回撤%':>8} {'交易数':>6} {'买入':>4} {'卖出':>4}"
    lines.append(header)
    lines.append("-" * 80)
    for row in breakdown:
        name = row["layer_mode"]
        lines.append(
            f"{name:<35} {row['total_return_pct']:>+8.2f} {row['excess_return_pct']:>+8.2f} "
            f"-{row['max_drawdown_pct']:>7.2f} {row['total_trades']:>6} "
            f"{row['buy_count']:>4} {row['sell_count']:>4}"
        )
    lines.append("")

    # 二、层间差异
    if deltas:
        lines.append("二、层间差异")
        lines.append("-" * 60)
        for delta in deltas:
            lines.append(f"  {delta['from_layer']} → {delta['to_layer']}:")
            lines.append(f"    收益变化: {delta['return_delta_pct']:+.2f}%  "
                         f"超额变化: {delta['excess_return_delta_pct']:+.2f}%  "
                         f"交易变化: {delta['trade_delta']:+d}")
            lines.append(f"    回撤变化: {delta['drawdown_delta_pct']:+.2f}%  "
                         f"买入变化: {delta['buy_delta']:+d}  "
                         f"卖出变化: {delta['sell_delta']:+d}")
        lines.append("")

    # 三、分析提示
    if hints:
        lines.append("三、对照分析提示")
        for hint in hints:
            lines.append(f"  - {hint}")
        lines.append("")

    # 四、逐日层间分歧统计
    if div_summary:
        lines.append("四、逐日层间分歧统计")
        lines.append("-" * 50)
        total = div_summary.get("total_signal_days", 0)
        div_days = div_summary.get("divergence_days", 0)
        strat_mod = div_summary.get("strategy_modified_days", 0)
        exec_blocked = div_summary.get("execution_blocked_days", 0)
        both = div_summary.get("strategy_and_execution_days", 0)
        lines.append(f"  总信号天数: {total}")
        lines.append(f"  存在分歧的天数: {div_days} ({div_days / max(total, 1) * 100:.1f}%)")
        lines.append(f"    策略层修改决策层: {strat_mod} 天")
        lines.append(f"    执行层阻断/修改: {exec_blocked} 天")
        lines.append(f"    策略+执行同时修改: {both} 天")
        lines.append("")

    # 五、分歧逐日明细（仅输出有分歧的日期）
    divergence_entries = [entry for entry in timeline if entry.get("divergence") != "none"]
    if divergence_entries:
        lines.append(f"五、层间分歧逐日明细（共 {len(divergence_entries)} 天）")
        lines.append("-" * 100)
        lines.append(f"{'日期':<12} {'分歧类型':<25} {'决策层':<16} {'策略层':<16} {'执行层':<16}")
        lines.append("-" * 100)
        for entry in divergence_entries[:80]:  # 限制最多80条，避免报告过长
            date = entry["date"]
            div_type = _DIV_TYPE_LABELS.get(entry["divergence"], entry["divergence"])
            d_info = _format_layer_signal(entry.get("decision"))
            s_info = _format_layer_signal(entry.get("strategy"))
            e_info = _format_exec_signal(entry.get("execution"))
            lines.append(f"{date:<12} {div_type:<25} {d_info:<16} {s_info:<16} {e_info:<16}")
        if len(divergence_entries) > 80:
            lines.append(f"  ... 省略 {len(divergence_entries) - 80} 条，完整数据请查看 JSON 导出")
        lines.append("")

    # 六、限制说明
    lines.append("六、限制说明")
    for item in payload.get("limitations", []):
        lines.append(f"  - {item}")

    return "\n".join(lines) + "\n"


_DIV_TYPE_LABELS = {
    "strategy_modified": "策略层修改决策",
    "execution_blocked": "执行层阻断/修改",
    "strategy_and_execution": "策略+执行同时修改",
    "none": "无分歧",
}


def _format_layer_signal(layer: dict[str, Any] | None) -> str:
    """格式化决策层/策略层信号为短文本。"""
    if not layer:
        return "-"
    action = layer.get("position_action") or layer.get("decision") or "?"
    ratio = layer.get("position_ratio")
    if ratio is not None:
        return f"{action}/{ratio:.0%}"
    return str(action)


def _format_exec_signal(exec_layer: dict[str, Any] | None) -> str:
    """格式化执行层信号为短文本。"""
    if not exec_layer:
        return "-"
    if exec_layer.get("blocked"):
        return f"BLOCKED({exec_layer.get('block_reason', '')})"
    action = exec_layer.get("effective_action") or "?"
    trade = exec_layer.get("trade_action")
    if trade:
        return f"{action}→{trade}"
    return str(action)


def _build_layer_breakdown_row(result: BacktestResult) -> dict[str, Any]:
    excess_return_pct = round(result.total_return_pct - result.benchmark_return_pct, 2)
    return {
        "layer_mode": result.layer_mode,
        "total_return_pct": result.total_return_pct,
        "benchmark_return_pct": result.benchmark_return_pct,
        "excess_return_pct": excess_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "total_trades": result.total_trades,
        "buy_count": result.buy_count,
        "sell_count": result.sell_count,
        "decision_stability": result.decision_stability,
    }


def _build_layer_deltas(layer_breakdown: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deltas: list[dict[str, Any]] = []
    for index in range(1, len(layer_breakdown)):
        prev_row = layer_breakdown[index - 1]
        curr_row = layer_breakdown[index]
        deltas.append({
            "from_layer": prev_row["layer_mode"],
            "to_layer": curr_row["layer_mode"],
            "return_delta_pct": round(curr_row["total_return_pct"] - prev_row["total_return_pct"], 2),
            "excess_return_delta_pct": round(curr_row["excess_return_pct"] - prev_row["excess_return_pct"], 2),
            "trade_delta": curr_row["total_trades"] - prev_row["total_trades"],
            "buy_delta": curr_row["buy_count"] - prev_row["buy_count"],
            "sell_delta": curr_row["sell_count"] - prev_row["sell_count"],
            "drawdown_delta_pct": round(curr_row["max_drawdown_pct"] - prev_row["max_drawdown_pct"], 2),
        })
    return deltas


def _build_layer_comparison_hints(layer_breakdown: list[dict[str, Any]]) -> list[str]:
    by_layer = {row["layer_mode"]: row for row in layer_breakdown}
    hints: list[str] = []

    decision_only = by_layer.get("decision_only")
    decision_strategy = by_layer.get("decision_strategy")
    decision_strategy_execution = by_layer.get("decision_strategy_execution")

    if decision_only and decision_strategy:
        trade_delta = decision_strategy["total_trades"] - decision_only["total_trades"]
        return_delta = round(decision_strategy["total_return_pct"] - decision_only["total_return_pct"], 2)
        if trade_delta < 0:
            hints.append(f"策略层使交易次数减少 {abs(trade_delta)} 次，说明它在压制部分原始决策动作。")
        elif trade_delta > 0:
            hints.append(f"策略层使交易次数增加 {trade_delta} 次，说明它在放大或重排原始动作。")
        if return_delta > 0:
            hints.append(f"策略层相对 decision_only 提升收益 {return_delta}%，说明行为约束当前在改善结果。")
        elif return_delta < 0:
            hints.append(f"策略层相对 decision_only 降低收益 {abs(return_delta)}%，需检查是否存在过度抑制或错误加仓。")

    if decision_strategy and decision_strategy_execution:
        return_delta = round(decision_strategy_execution["total_return_pct"] - decision_strategy["total_return_pct"], 2)
        trade_delta = decision_strategy_execution["total_trades"] - decision_strategy["total_trades"]
        if return_delta < 0:
            hints.append(f"执行层相对策略层带来 {abs(return_delta)}% 的收益回落，需关注成本、流动性和成交约束。")
        elif return_delta > 0:
            hints.append(f"执行层相对策略层提升收益 {return_delta}%，需检查是否来自更真实的成交过滤。")
        if trade_delta != 0:
            hints.append(f"执行层使最终成交次数变化 {trade_delta} 次，说明现实约束已改变最终执行结果。")

    if not hints:
        hints.append("三层结果差异较小，当前样本下层级约束影响有限，需扩大样本再判断。")
    return hints


def _infer_trigger_type(trade: Any) -> str:
    pos_action = (trade.position_action or "").upper()
    if pos_action == "OPEN":
        return "entry"
    if pos_action == "ADD":
        return "add"
    if pos_action == "REDUCE":
        return "trim"
    if pos_action == "CLOSE_ALL":
        reason_text = (trade.reason or "").lower()
        if "止损" in trade.reason or "panic" in reason_text or "极端" in trade.reason:
            return "stop"
        return "exit"
    return "hold"


def _infer_trigger_source(trade: Any) -> str:
    reason_text = (trade.reason or "").lower()
    if "止损" in trade.reason:
        return "stop_loss"
    if "止盈" in trade.reason:
        return "take_profit"
    if "冷却" in trade.reason:
        return "cooldown"
    if "惯性" in trade.reason:
        return "inertia"
    if "确认" in trade.reason:
        return "confirmation"
    if "极端" in trade.reason or "panic" in reason_text:
        return "extreme"
    return "decision"


def _build_action_source_table(result: BacktestResult, diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    execution_logs = diagnostics.get("execution_logs") or []
    if not execution_logs:
        return [
            {
                "date": trade.date,
                "action": trade.action,
                "position_action": trade.position_action,
                "reason": trade.reason,
                "signal_score": trade.signal_score,
                "position_ratio_after": trade.position_ratio_after,
                "trigger_layer": _infer_trigger_layer_from_trade(trade),
                "trigger_type": _infer_trigger_type(trade),
                "trigger_source": _infer_trigger_source(trade),
            }
            for trade in result.trades
        ]

    rows: list[dict[str, Any]] = []
    for log in execution_logs:
        trade = log.get("trade")
        strategy = log.get("strategy") or {}
        decision = log.get("decision") or {}
        execution = log.get("execution") or {}
        if not trade and not execution.get("blocked"):
            continue
        reason = ""
        if trade:
            reason = trade.get("reason", "")
        elif execution.get("block_reason"):
            reason = execution.get("block_reason", "")

        rows.append({
            "date": log.get("execution_date"),
            "signal_date": log.get("signal_date"),
            "action": trade.get("action") if trade else None,
            "final_signal": strategy.get("decision") or decision.get("decision"),
            "position_action": (trade.get("position_action") if trade else None) or strategy.get("position_action") or execution.get("effective_action"),
            "trigger_layer": _infer_trigger_layer_from_log(log),
            "trigger_type": _infer_trigger_type_from_log(log),
            "trigger_source": _infer_trigger_source_from_log(log),
            "main_reason": reason,
            "signal_score": decision.get("score", 0.0),
            "position_ratio_before": log.get("position_ratio_before"),
            "position_ratio_after": log.get("position_ratio_after"),
            "blocked": execution.get("blocked", False),
            "block_reason": execution.get("block_reason", ""),
        })
    return rows


def _build_hold_break_table(result: BacktestResult, diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not result.trades or not result.daily_snapshots:
        return rows

    execution_logs = diagnostics.get("execution_logs") or []
    snapshot_index = {snapshot.date: idx for idx, snapshot in enumerate(result.daily_snapshots)}
    if execution_logs:
        iterable = []
        for log in execution_logs:
            trade = log.get("trade")
            if not trade:
                continue
            pos_action = (trade.get("position_action") or log.get("strategy", {}).get("position_action") or "").upper()
            if pos_action not in {"REDUCE", "CLOSE_ALL"}:
                continue
            iterable.append((log.get("execution_date"), trade, pos_action, log))
    else:
        iterable = [
            (trade.date, trade.model_dump(), (trade.position_action or "").upper(), None)
            for trade in result.trades
            if (trade.position_action or "").upper() in {"REDUCE", "CLOSE_ALL"}
        ]

    for trade_date, trade, pos_action, log in iterable:
        idx = snapshot_index.get(trade_date)
        future_5 = _future_return(result, idx, 5) if idx is not None else None
        future_10 = _future_return(result, idx, 10) if idx is not None else None
        rows.append({
            "date": trade_date,
            "stock_code": result.stock_code,
            "hold_context": _build_hold_context(log),
            "broken_by": pos_action,
            "broken_layer": _infer_trigger_layer_from_log(log) if log else _infer_trigger_layer_from_trade(trade),
            "trigger_source": _infer_trigger_source_from_log(log) if log else _infer_trigger_source(trade),
            "broken_reason": trade.get("reason", ""),
            "from_state": "holding",
            "to_state": "reduced" if pos_action == "REDUCE" else "flat",
            "pnl_if_hold_5d": future_5,
            "pnl_if_hold_10d": future_10,
        })
    return rows


def _infer_trigger_type_from_log(log: dict[str, Any]) -> str:
    trade = log.get("trade") or {}
    pos_action = (trade.get("position_action") or log.get("strategy", {}).get("position_action") or "").upper()
    reason = trade.get("reason", "")
    if pos_action == "OPEN":
        return "entry"
    if pos_action == "ADD":
        return "add"
    if pos_action == "REDUCE":
        return "trim"
    if pos_action == "CLOSE_ALL":
        if "止损" in reason or "极端" in reason or "panic" in reason.lower():
            return "stop"
        return "exit"
    return "hold"


def _infer_trigger_layer_from_trade(trade: Any) -> str:
    reason = getattr(trade, "reason", "") or trade.get("reason", "")
    lower_reason = reason.lower()
    if any(token in reason for token in ("策略层修正", "反转成本", "冷却", "惯性", "确认", "稳定性")):
        return "strategy"
    if any(token in reason for token in ("涨停", "跌停", "流动性")) or "blocked" in lower_reason:
        return "execution"
    return "decision"


def _infer_trigger_layer_from_log(log: dict[str, Any]) -> str:
    decision = log.get("decision") or {}
    strategy = log.get("strategy") or {}
    execution = log.get("execution") or {}

    if execution.get("blocked"):
        return "execution"

    strategy_action = strategy.get("position_action")
    execution_action = execution.get("effective_action")
    if execution_action and strategy_action and execution_action != strategy_action:
        return "execution"

    if strategy:
        decision_changed = strategy.get("decision") != decision.get("decision")
        action_changed = strategy.get("position_action") != decision.get("position_action")
        ratio_changed = strategy.get("position_ratio") != decision.get("position_ratio")
        strategy_flags = any(
            strategy.get(flag) for flag in (
                "inertia_applied",
                "confirmation_required",
                "cooldown_blocked",
                "reverse_cost_paid",
                "stability_adjusted",
            )
        )
        strategy_reason_hit = any(
            token in reason
            for reason in strategy.get("strategy_reasons", [])
            for token in ("策略层修正", "反转成本", "冷却", "惯性", "确认", "稳定性")
        )
        if decision_changed or action_changed or ratio_changed or strategy_flags or strategy_reason_hit:
            return "strategy"

    return "decision"


def _infer_trigger_source_from_log(log: dict[str, Any]) -> str:
    trade = log.get("trade") or {}
    reason = trade.get("reason", "")
    strategy = log.get("strategy") or {}
    execution = log.get("execution") or {}
    if execution.get("blocked"):
        if "流动性" in execution.get("block_reason", ""):
            return "liquidity"
        if "涨停" in execution.get("block_reason", ""):
            return "limit_up"
        if "跌停" in execution.get("block_reason", ""):
            return "limit_down"
    if "止损" in reason:
        return "stop_loss"
    if "止盈" in reason:
        return "take_profit"
    if strategy.get("cooldown_blocked"):
        return "cooldown"
    if strategy.get("confirmation_required"):
        return "confirmation"
    if strategy.get("inertia_applied"):
        return "inertia"
    if "极端" in reason or "panic" in reason.lower():
        return "extreme"
    return "decision"


def _build_hold_context(log: dict[str, Any] | None) -> str:
    if not log:
        return "持仓被卖出或减仓后，观察后续价格延续情况"
    strategy = log.get("strategy") or {}
    decision = log.get("decision") or {}
    lifecycle = strategy.get("lifecycle_before") or "holding"
    final_signal = strategy.get("decision") or decision.get("decision") or "UNKNOWN"
    return f"执行前处于 {lifecycle}，策略最终信号为 {final_signal}"


def _future_return(result: BacktestResult, current_index: int | None, offset: int) -> float | None:
    if current_index is None:
        return None
    future_index = current_index + offset
    if future_index >= len(result.daily_snapshots):
        return None
    current_price = result.daily_snapshots[current_index].price
    future_price = result.daily_snapshots[future_index].price
    if not current_price:
        return None
    return round((future_price / current_price - 1) * 100, 2)


def _build_analysis_hints(result: BacktestResult, excess_return_pct: float) -> list[str]:
    hints: list[str] = []
    if excess_return_pct < 0:
        hints.append("策略跑输买入持有，优先检查是否存在过早减仓或清仓。")
    if result.total_trades >= 40:
        hints.append("交易频率偏高，优先检查卖出语义是否过于敏感。")
    if result.sell_count > result.buy_count * 2 and result.buy_count > 0:
        hints.append("卖出显著多于买入，可能存在频繁减仓或清仓后难以再入场。")
    if result.blocked_by_limit_down > 0 or result.blocked_by_liquidity > 0:
        hints.append("执行约束已实际影响结果，需要区分策略问题和执行问题。")
    if result.max_drawdown_pct > 12:
        hints.append("回撤偏大，需要结合持仓破坏表判断防守是否既慢又碎。")
    if not hints:
        hints.append("结果较平稳，建议重点复盘关键买点和卖点的语义是否清晰。")
    return hints


def _render_text_report(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    summary = payload["summary"]
    lines = [
        f"{meta['stock_name'] or meta['stock_code']} 回测分析导出",
        f"区间: {meta['start_date']} ~ {meta['end_date']}",
        f"回测模式: {meta['backtest_mode']}",
        "",
        "一、汇总指标",
        f"总收益率: {summary['total_return_pct']:+.2f}%",
        f"基准收益: {summary['benchmark_return_pct']:+.2f}%",
        f"超额收益: {summary['excess_return_pct']:+.2f}%",
        f"年化收益率: {summary['annualized_return_pct']:+.2f}%",
        f"最大回撤: -{summary['max_drawdown_pct']:.2f}%",
        f"夏普比率: {summary['sharpe_ratio']:.2f}",
        f"总交易次数: {summary['total_trades']}",
        f"买入次数: {summary['buy_count']}",
        f"卖出次数: {summary['sell_count']}",
        "",
        "二、AI分析提示",
    ]
    lines.extend(f"- {hint}" for hint in payload["analysis_hints"])
    lines.extend([
        "",
        "三、限制说明",
    ])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.extend([
        "",
        "四、交易记录（前20条）",
    ])
    for trade in payload["trades"][:20]:
        lines.append(
            f"- {trade['date']} | {trade['action']} | {trade.get('position_action', '')} | "
            f"价格 {trade['price']:.2f} | 金额 {trade['amount']:.2f} | 原因: {trade.get('reason', '')}"
        )
    return "\n".join(lines) + "\n"