"""回测分析导出器。

基于现有 BacktestResult 组装 AI 可消费的分析载荷，
不改变回测引擎本身的统计口径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.data.models import BacktestResult


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