"""回测验证基线。

提供最小可执行的 Phase 3 研究纪律：
- 时序完整性检查（lookahead / 执行先后顺序）
- 样本内 / 样本外拆分
- walk-forward 滚动窗口验证
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.orchestrator import Orchestrator
from src.data.data_feeder import DataFeeder
from src.data.models import BacktestResult, StrategyState


def load_trading_dates(stock_code: str, start_date: str, end_date: str) -> list[str]:
    """加载交易日序列，用于切分训练/测试窗口。"""
    feeder = DataFeeder(stock_code, start_date, end_date)
    if not feeder.load():
        raise ValueError(f"无法加载交易日期: {stock_code} {start_date}~{end_date}")
    return list(feeder._dates)


def build_validation_windows(
    dates: list[str],
    train_ratio: float = 0.7,
    walk_forward_splits: int = 3,
) -> dict[str, Any]:
    """基于交易日构造样本内/样本外和 walk-forward 窗口。"""
    if len(dates) < 20:
        raise ValueError("交易日不足，至少需要20个交易日才能做基础验证")

    total_count = len(dates)
    min_test_count = max(5, min(20, total_count // 5))
    min_train_count = max(10, min(120, total_count // 2))
    desired_train_count = int(total_count * train_ratio)
    train_count = max(desired_train_count, min_train_count)
    train_count = min(train_count, total_count - min_test_count)

    in_sample = {
        "start_date": dates[0],
        "end_date": dates[train_count - 1],
        "trading_days": train_count,
    }
    out_of_sample = {
        "start_date": dates[train_count],
        "end_date": dates[-1],
        "trading_days": total_count - train_count,
    }

    remaining_count = total_count - train_count
    split_count = max(1, min(walk_forward_splits, remaining_count // min_test_count))
    step = max(min_test_count, remaining_count // split_count) if split_count > 0 else remaining_count

    walk_forward_windows: list[dict[str, Any]] = []
    test_start_idx = train_count
    for split_index in range(split_count):
        if test_start_idx >= total_count:
            break
        test_end_idx = min(total_count - 1, test_start_idx + step - 1)
        if total_count - test_start_idx < min_test_count and walk_forward_windows:
            break
        window = {
            "label": f"wf_{split_index + 1}",
            "train_start": dates[0],
            "train_end": dates[test_start_idx - 1],
            "test_start": dates[test_start_idx],
            "test_end": dates[test_end_idx],
            "train_days": test_start_idx,
            "test_days": test_end_idx - test_start_idx + 1,
        }
        walk_forward_windows.append(window)
        test_start_idx = test_end_idx + 1

    return {
        "total_trading_days": total_count,
        "train_ratio": round(train_count / total_count, 4),
        "in_sample": in_sample,
        "out_of_sample": out_of_sample,
        "walk_forward_windows": walk_forward_windows,
    }


def build_validation_payload(
    full_result: BacktestResult,
    backtest_mode: str,
    windows: dict[str, Any],
    in_sample_result: BacktestResult,
    out_of_sample_result: BacktestResult,
    walk_forward_results: list[dict[str, Any]],
    consistency_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造偏差检查与研究验证 payload。"""
    lookahead_check = _validate_execution_timing(full_result)
    in_sample_summary = _summarize_slice("in_sample", in_sample_result)
    out_of_sample_summary = _summarize_slice("out_of_sample", out_of_sample_result)
    walk_forward_summary = [_summarize_walk_forward(item) for item in walk_forward_results]

    payload = {
        "meta": {
            "stock_code": full_result.stock_code,
            "stock_name": full_result.stock_name,
            "start_date": full_result.start_date,
            "end_date": full_result.end_date,
            "initial_capital": full_result.initial_capital,
            "backtest_mode": backtest_mode,
            "layer_mode": full_result.layer_mode,
            "validation_version": "v0.8.2",
        },
        "window_config": windows,
        "checks": {
            "lookahead_integrity": lookahead_check,
            "replay_consistency": consistency_check or {},
            "in_sample_vs_out_of_sample": _build_generalization_check(in_sample_summary, out_of_sample_summary),
            "walk_forward": _build_walk_forward_check(walk_forward_summary),
        },
        "slices": {
            "full_period": _summarize_slice("full_period", full_result),
            "in_sample": in_sample_summary,
            "out_of_sample": out_of_sample_summary,
            "walk_forward": walk_forward_summary,
        },
        "validation_hints": _build_validation_hints(
            lookahead_check,
            in_sample_summary,
            out_of_sample_summary,
            walk_forward_summary,
            consistency_check,
        ),
    }
    return payload


def build_replay_consistency_check(
    result: BacktestResult,
    stock_code: str,
    skills_dir: str,
    signal_weights: dict[str, float] | None = None,
    skill_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    """重放同一历史快照，检查回测路径与准实时分析路径是否一致。"""
    diagnostics = result.diagnostics or {}
    daily_decisions = diagnostics.get("daily_decisions") or []
    if not daily_decisions:
        return {
            "checked_days": 0,
            "passed": False,
            "mismatch_count": 0,
            "message": "缺少 daily_decisions，无法执行一致性检查",
            "samples": [],
        }

    feeder = DataFeeder(stock_code, result.start_date, result.end_date)
    if not feeder.load():
        return {
            "checked_days": 0,
            "passed": False,
            "mismatch_count": 0,
            "message": "历史数据加载失败，无法执行一致性检查",
            "samples": [],
        }

    orchestrator = Orchestrator(skills_dir, None, signal_weights, skill_types)
    expected_by_date = {entry["date"]: entry for entry in daily_decisions}
    strategy_state = StrategyState()
    mismatches: list[dict[str, Any]] = []
    checked_days = 0

    for date, stock_data in feeder.iterate():
        expected = expected_by_date.get(date)
        if not expected:
            continue
        current_position_ratio = expected.get("position_ratio_before", 0.0)
        strategy_state.current_position_ratio = current_position_ratio
        decision_result, strategy_decision, _, _ = orchestrator.analyze(
            stock_data,
            current_position_ratio=current_position_ratio,
            strategy_state=strategy_state,
            ai_enabled=False,
        )
        replay_decision = _serialize_replay_decision(decision_result)
        replay_strategy = _serialize_replay_strategy(strategy_decision)

        checked_days += 1
        decision_mismatch = _diff_dict(expected.get("decision"), replay_decision)
        strategy_mismatch = _diff_dict(expected.get("strategy"), replay_strategy)
        if decision_mismatch or strategy_mismatch:
            mismatches.append({
                "date": date,
                "decision_diff": decision_mismatch,
                "strategy_diff": strategy_mismatch,
                "expected_decision": expected.get("decision"),
                "replay_decision": replay_decision,
                "expected_strategy": expected.get("strategy"),
                "replay_strategy": replay_strategy,
            })

        strategy_state = strategy_decision.new_state

    return {
        "checked_days": checked_days,
        "passed": len(mismatches) == 0,
        "mismatch_count": len(mismatches),
        "message": "回测路径与准实时重放路径一致" if not mismatches else "发现回测与准实时重放结果不一致，需检查分析口径漂移",
        "samples": mismatches[:20],
    }


def export_validation_json(payload: dict[str, Any], output_path: str) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def export_validation_txt(payload: dict[str, Any], output_path: str) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_validation_report(payload), encoding="utf-8")
    return target


def _validate_execution_timing(result: BacktestResult) -> dict[str, Any]:
    execution_logs = (result.diagnostics or {}).get("execution_logs") or []
    violations: list[dict[str, Any]] = []
    checked = 0
    for log in execution_logs:
        signal_date = log.get("signal_date")
        execution_date = log.get("execution_date")
        if not signal_date or not execution_date:
            continue
        checked += 1
        if signal_date >= execution_date:
            violations.append({
                "signal_date": signal_date,
                "execution_date": execution_date,
                "position_action": (log.get("trade") or {}).get("position_action") or (log.get("strategy") or {}).get("position_action"),
            })

    return {
        "checked_records": checked,
        "passed": len(violations) == 0,
        "violation_count": len(violations),
        "violations": violations[:20],
        "message": "所有已检查记录均满足 signal_date < execution_date" if not violations else "发现时序违规，需检查前视偏差或日志记录错误",
    }


def _serialize_replay_decision(decision_result) -> dict[str, Any] | None:
    if not decision_result:
        return None
    return {
        "decision": decision_result.decision.value,
        "state": decision_result.state.value,
        "score": round(decision_result.score, 4),
        "position_action": decision_result.position_action.value,
        "position_ratio": round(decision_result.position_ratio, 4),
        "reason": decision_result.reason[:5],
        "warnings": decision_result.warnings[:5],
    }


def _serialize_replay_strategy(strategy_decision) -> dict[str, Any] | None:
    if not strategy_decision:
        return None
    return {
        "decision": strategy_decision.decision.value,
        "position_action": strategy_decision.position_action.value,
        "position_ratio": round(strategy_decision.position_ratio, 4),
        "lifecycle_before": strategy_decision.lifecycle_before.value,
        "lifecycle_after": strategy_decision.lifecycle_after.value,
        "inertia_applied": strategy_decision.inertia_applied,
        "confirmation_required": strategy_decision.confirmation_required,
        "cooldown_blocked": strategy_decision.cooldown_blocked,
        "reverse_cost_paid": strategy_decision.reverse_cost_paid,
        "stability_adjusted": strategy_decision.stability_adjusted,
        "strategy_reasons": strategy_decision.strategy_reasons[:5],
    }


def _diff_dict(expected: dict[str, Any] | None, actual: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if expected == actual:
        return {}
    keys = set((expected or {}).keys()) | set((actual or {}).keys())
    diff: dict[str, dict[str, Any]] = {}
    for key in sorted(keys):
        exp_val = (expected or {}).get(key)
        act_val = (actual or {}).get(key)
        if exp_val != act_val:
            diff[key] = {"expected": exp_val, "actual": act_val}
    return diff


def _summarize_slice(label: str, result: BacktestResult) -> dict[str, Any]:
    return {
        "label": label,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "layer_mode": result.layer_mode,
        "total_return_pct": result.total_return_pct,
        "benchmark_return_pct": result.benchmark_return_pct,
        "excess_return_pct": round(result.total_return_pct - result.benchmark_return_pct, 2),
        "max_drawdown_pct": result.max_drawdown_pct,
        "total_trades": result.total_trades,
        "buy_count": result.buy_count,
        "sell_count": result.sell_count,
        "decision_stability": result.decision_stability,
    }


def _summarize_walk_forward(item: dict[str, Any]) -> dict[str, Any]:
    result = item["result"]
    window = item["window"]
    summary = _summarize_slice(window["label"], result)
    summary.update({
        "train_start": window["train_start"],
        "train_end": window["train_end"],
        "test_start": window["test_start"],
        "test_end": window["test_end"],
        "train_days": window["train_days"],
        "test_days": window["test_days"],
    })
    return summary


def _build_generalization_check(in_sample_summary: dict[str, Any], out_of_sample_summary: dict[str, Any]) -> dict[str, Any]:
    return_gap = round(out_of_sample_summary["total_return_pct"] - in_sample_summary["total_return_pct"], 2)
    excess_gap = round(out_of_sample_summary["excess_return_pct"] - in_sample_summary["excess_return_pct"], 2)
    trade_gap = out_of_sample_summary["total_trades"] - in_sample_summary["total_trades"]
    passed = not (
        in_sample_summary["total_return_pct"] > 0
        and out_of_sample_summary["total_return_pct"] < 0
        and abs(return_gap) > 5
    )
    return {
        "passed": passed,
        "return_gap_pct": return_gap,
        "excess_return_gap_pct": excess_gap,
        "trade_gap": trade_gap,
        "message": "样本内外结果没有出现明显断裂" if passed else "样本内外断裂明显，需警惕过拟合或参数对区间敏感",
    }


def _build_walk_forward_check(walk_forward_summary: list[dict[str, Any]]) -> dict[str, Any]:
    if not walk_forward_summary:
        return {
            "passed": False,
            "window_count": 0,
            "positive_windows": 0,
            "message": "没有可用的 walk-forward 窗口",
        }

    positive_windows = sum(1 for item in walk_forward_summary if item["total_return_pct"] > 0)
    avg_return = round(sum(item["total_return_pct"] for item in walk_forward_summary) / len(walk_forward_summary), 2)
    passed = positive_windows >= max(1, len(walk_forward_summary) // 2)
    return {
        "passed": passed,
        "window_count": len(walk_forward_summary),
        "positive_windows": positive_windows,
        "average_return_pct": avg_return,
        "message": "walk-forward 结果具备一定稳定性" if passed else "walk-forward 稳定性偏弱，需扩大样本或减少参数敏感度",
    }


def _build_validation_hints(
    lookahead_check: dict[str, Any],
    in_sample_summary: dict[str, Any],
    out_of_sample_summary: dict[str, Any],
    walk_forward_summary: list[dict[str, Any]],
    consistency_check: dict[str, Any] | None = None,
) -> list[str]:
    hints: list[str] = []
    if not lookahead_check["passed"]:
        hints.append("发现 signal_date 与 execution_date 时序违规，需先排查前视偏差。")
    if consistency_check and not consistency_check.get("passed", True):
        hints.append("回测路径与准实时重放路径存在差异，需优先检查决策/策略口径是否漂移。")
    if out_of_sample_summary["total_return_pct"] < 0 < in_sample_summary["total_return_pct"]:
        hints.append("样本内赚钱、样本外亏损，优先怀疑过拟合或区间依赖。")
    positive_windows = sum(1 for item in walk_forward_summary if item["total_return_pct"] > 0)
    if walk_forward_summary and positive_windows < max(1, len(walk_forward_summary) // 2):
        hints.append("walk-forward 正收益窗口偏少，当前策略稳定性不足。")
    if not hints:
        hints.append("基础偏差检查未见明显异常，可以继续扩大样本做多标的验证。")
    return hints


def _render_validation_report(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    lookahead = payload["checks"]["lookahead_integrity"]
    consistency = payload["checks"].get("replay_consistency") or {}
    generalization = payload["checks"]["in_sample_vs_out_of_sample"]
    walk_forward = payload["checks"]["walk_forward"]
    in_sample = payload["slices"]["in_sample"]
    out_of_sample = payload["slices"]["out_of_sample"]

    lines = [
        f"{meta['stock_name'] or meta['stock_code']} 回测验证基线",
        f"区间: {meta['start_date']} ~ {meta['end_date']}",
        f"分层模式: {meta['layer_mode']}",
        "",
        "一、时序完整性",
        f"- 结果: {'通过' if lookahead['passed'] else '未通过'}",
        f"- 检查记录数: {lookahead['checked_records']}",
        f"- 违规数: {lookahead['violation_count']}",
        f"- 说明: {lookahead['message']}",
        "",
        "二、回测 / 准实时一致性",
        f"- 结果: {'通过' if consistency.get('passed', False) else '未通过'}",
        f"- 检查天数: {consistency.get('checked_days', 0)}",
        f"- 差异数: {consistency.get('mismatch_count', 0)}",
        f"- 说明: {consistency.get('message', '未执行一致性检查')}",
        "",
        "三、样本内 / 样本外",
        f"- 样本内: {in_sample['start_date']} ~ {in_sample['end_date']} | 收益 {in_sample['total_return_pct']:+.2f}% | 交易 {in_sample['total_trades']}",
        f"- 样本外: {out_of_sample['start_date']} ~ {out_of_sample['end_date']} | 收益 {out_of_sample['total_return_pct']:+.2f}% | 交易 {out_of_sample['total_trades']}",
        f"- 泛化检查: {'通过' if generalization['passed'] else '未通过'} | 收益差 {generalization['return_gap_pct']:+.2f}% | 超额差 {generalization['excess_return_gap_pct']:+.2f}%",
        "",
        "四、Walk-Forward",
        f"- 结果: {'通过' if walk_forward['passed'] else '未通过'}",
        f"- 窗口数: {walk_forward['window_count']}",
        f"- 正收益窗口: {walk_forward['positive_windows']}",
        f"- 平均收益: {walk_forward.get('average_return_pct', 0.0):+.2f}%",
        "",
        "五、提示",
    ]
    lines.extend(f"- {hint}" for hint in payload["validation_hints"])
    return "\n".join(lines) + "\n"