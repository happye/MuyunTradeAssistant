"""统一行动卡投影（plan/fusion F2，DESIGN ADR-F02/ADR-F08）：所有入口同一终态语义。

病根（PROBES A/B 实证，RESEARCH §3.1）：旧 `_print_plain_summary` 读原始
DecisionResult 与原始 entry_exit——PlanGuard 压制后的 HOLD 被播报成「卖出信号」；
后置强制退出（force_exit/硬信号覆盖降级救回）被播报成「今天什么都不用做」。

本模块以**末端 StrategyDecision.position_action**（PlanGuard/force_exit 已应用）+
ExecutionEvaluation（执行可行性）为唯一判定来源，产出：
- terminal_verdict：终态行动判定（结构化，chat/Web/TUI/REPL 共用语义）
- render_action_card：人话行动卡（行动→决定理由→阻塞→下次触发→数据时点）

原始信号与买卖点细节只在诊断字段/详版报告中出现，不再混入终态判定。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 终态 bucket：所有入口对同一 decision_id 必须给出同一 bucket（DESIGN §2.3 不变量）
VERDICT_EXIT = "EXIT"
VERDICT_REDUCE = "REDUCE"
VERDICT_ADD = "ADD"
VERDICT_OPEN = "OPEN"
VERDICT_HOLD = "HOLD"
VERDICT_WAIT = "WAIT"
VERDICT_REVIEW = "REVIEW"

_ACTION_CN = {
    VERDICT_EXIT: "卖出/清仓",
    VERDICT_REDUCE: "减仓",
    VERDICT_ADD: "加仓",
    VERDICT_OPEN: "建仓",
    VERDICT_HOLD: "继续持有",
    VERDICT_WAIT: "观望",
    VERDICT_REVIEW: "需要复核",
}


def terminal_verdict(strategy_decision, execution_eval=None, has_position: bool = False,
                     fallback_decision=None) -> dict:
    """末端 StrategyDecision → 终态行动判定（PROBES A/B 的接线回归锚点）。

    Args:
        strategy_decision: StrategyDecision（**末端**；None 时退回 fallback_decision 原始信号，
            并标 degraded=True——此时没有终态可依据，展示层必须知道这是降级视图）
        execution_eval: ExecutionEvaluation（可 None）
        has_position: 是否已确认持有（组合事实，非建议）
        fallback_decision: 原始 DecisionResult（仅 strategy_decision 缺失时的降级依据）

    Returns:
        {bucket, cn, blocked, blocked_reason, reason, degraded}
        bucket ∈ EXIT/REDUCE/ADD/OPEN/HOLD/WAIT/REVIEW；blocked=True 表示意图受阻
        （退出意图保留，不是"继续看好"——DESIGN §2.3 EXIT+BLOCKED 语义）。
    """
    if strategy_decision is None:
        # 降级：无末端结果（旧调用方/渲染容错）——按原始信号给保守判定
        raw = fallback_decision.decision.value if fallback_decision is not None else "WATCH"
        bucket = {("BUY",): VERDICT_OPEN, ("SELL",): VERDICT_EXIT}.get((raw,), VERDICT_WAIT)
        if raw == "HOLD":
            bucket = VERDICT_HOLD if has_position else VERDICT_WAIT
        if raw == "WATCH":
            bucket = VERDICT_REVIEW if not has_position else VERDICT_HOLD
        return {"bucket": bucket, "cn": _ACTION_CN[bucket], "blocked": False,
                "blocked_reason": "", "reason": "", "degraded": True}

    pos_action = strategy_decision.position_action.value
    blocked = bool(execution_eval is not None and getattr(execution_eval, "blocked", False))
    blocked_reason = (getattr(execution_eval, "block_reason", "") or "") if blocked else ""

    mapping = {
        "CLOSE_ALL": VERDICT_EXIT,
        "REDUCE": VERDICT_REDUCE,
        "ADD": VERDICT_ADD,
        "OPEN": VERDICT_OPEN,
        "HOLD_POSITION": VERDICT_HOLD,
        "STAY_OUT": VERDICT_REVIEW if strategy_decision.decision.value == "WATCH" else VERDICT_WAIT,
    }
    bucket = mapping.get(pos_action, VERDICT_WAIT)

    # 理由：优先策略层结构化理由，其次卖出路径，最后买卖点原始原因（仅作注解）
    reasons = getattr(strategy_decision, "strategy_reasons", None) or []
    reason = reasons[0] if reasons else (strategy_decision.sell_path or "")

    return {"bucket": bucket, "cn": _ACTION_CN.get(bucket, bucket), "blocked": blocked,
            "blocked_reason": blocked_reason, "reason": reason, "degraded": False}


def render_action_card(verdict: dict, *, reasons: Optional[list[str]] = None,
                       blockers: Optional[list[str]] = None,
                       next_check: str = "", as_of: str = "") -> str:
    """行动卡人话渲染（DESIGN ADR-F08 顺序：行动+周期→决定理由→阻塞→下次触发→时点）。

    纯文本输出，REPL 面板/chat/Web 共用；展示层不猜 sell_path、不发明新动作。
    """
    bucket = verdict.get("bucket", VERDICT_WAIT)
    lines: list[str] = []

    if bucket == VERDICT_EXIT and verdict.get("blocked"):
        # G03：受阻退出——保留退出意图，绝不写成"继续看好"
        lines.append(f"**退出条件已触发，但当前无法成交**（{verdict.get('blocked_reason') or '执行受阻'}）。")
        lines.append("持仓记录保持不变，退出待办保留，下一交易时段重新检查可成交性。")
    elif bucket == VERDICT_EXIT:
        lines.append("**退出条件已触发：按纪律执行卖出，别拖。**")
    elif bucket == VERDICT_REDUCE:
        lines.append("**减仓纪律触发：按计划减一部分仓位。**")
    elif bucket == VERDICT_ADD:
        lines.append("**加仓条件成立：按计划加仓（注意总仓位纪律）。**")
    elif bucket == VERDICT_OPEN:
        lines.append("**买入条件成立：按纪律分批建仓，别一次打满。**")
    elif bucket == VERDICT_HOLD:
        lines.append("**继续持有，今天不用操作。**")
    elif bucket == VERDICT_REVIEW:
        lines.append("**需要复核：先看清楚再动手。**")
    else:
        lines.append("**观望：今天不是行动时点。**")

    for r in (reasons or [])[:2]:
        lines.append(f"原因：{r}")
    for b in (blockers or [])[:1]:
        lines.append(f"阻塞：{b}")
    if next_check:
        lines.append(f"下次关注：{next_check}")
    if as_of:
        lines.append(f"[dim]数据截至 {as_of}[/dim]")
    if verdict.get("degraded"):
        lines.append("[dim]（降级视图：终态结果缺失，按原始信号口径展示）[/dim]")
    return "\n".join(lines)
