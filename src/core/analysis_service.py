"""分析服务（plan/fusion F2，DESIGN ADR-F01/ADR-F02）：末端结果 → 唯一 DecisionPacket。

职责：把既有七层的**末端**结果（StrategyDecision——PlanGuard/force_exit 已应用；
ExecutionEvaluation——执行可行性已评估）适配成统一决策契约，供所有入口消费。
不抓数据、不反向 import CLI、不改投资参数（先不改任何裁决逻辑，TASKS F2）。

映射原则（DESIGN §2.2/§2.3）：
- desired_action/target_weight 只信末端 position_action/position_ratio；原始信号
  （DecisionResult.decision/score、entry_exit）全部进 legacy_trace 作诊断投影
- 旧七层可能产出"对契约非法"的组合（如 ADD 但当前 0 仓）——适配器**钳制为合法
  包**并记 reason_code，绝不让非法包流入消费方，也绝不静默丢弃退出意图
- research_status 由数据缺口（warnings）与分歧（divergence）判定；legacy 无投资
  逻辑跟踪，thesis_status 固定 UNESTABLISHED（F5 落 PlanV2 后才有 VALID/INVALID）
- AI 解释不创造数值：本适配器零 AI 输入
"""

import logging
from datetime import datetime
from typing import Optional

from src.core.decision_contract import (
    Blocker,
    DecisionPacket,
    DesiredAction,
    ExecutionStatus,
    Horizon,
    LegacyTrace,
    MarketPhase,
    ResearchStatus,
    ThesisStatus,
)

logger = logging.getLogger(__name__)

POLICY_ID_LEGACY = "legacy_v1"  # 未换策略：终态仍由既有七层裁决，F5 才引入 fusion_mid/long


def build_decision_packet(
    decision_result,
    strategy_decision,
    execution_eval,
    *,
    confirmed_ratio: Optional[float] = None,
    source: str = "",
) -> DecisionPacket:
    """末端七层结果 → DecisionPacket（唯一终态）。

    Args:
        decision_result: DecisionResult（原始信号聚合，只作诊断投影）
        strategy_decision: StrategyDecision（**末端**：PlanGuard/force_exit/Execution 后）
        execution_eval: ExecutionEvaluation（执行层评估，可 None）
        confirmed_ratio: 已确认仓位（portfolio.yaml 事实，0-1；None=组合信息未知）
        source: 触发来源（l/la/chat/web/tui），仅诊断

    Returns:
        合法的 DecisionPacket（构造期不变量已通过；非法映射被钳制并记 reason_code）
    """
    pos_action = strategy_decision.position_action.value
    terminal_decision = strategy_decision.decision.value
    has_confirmed = confirmed_ratio is not None and confirmed_ratio > 1e-9
    known_confirmed = confirmed_ratio is not None

    reason_codes: list[str] = [
        str(r) for r in (getattr(strategy_decision, "strategy_reasons", None) or [])[:5]
    ]

    def _clamp(reason: str):
        if reason not in reason_codes:
            reason_codes.append(reason)

    # ── desired_action 映射（含对旧七层输出的防御性钳制）──
    if pos_action == "OPEN":
        desired = DesiredAction.ADD if has_confirmed else DesiredAction.OPEN
        if has_confirmed:
            _clamp("适配钳制: 已持仓时 OPEN 转 ADD")
    elif pos_action == "ADD":
        desired = DesiredAction.ADD if has_confirmed else DesiredAction.OPEN
        if not has_confirmed:
            _clamp("适配钳制: 无持仓时 ADD 转 OPEN")
    elif pos_action == "REDUCE":
        desired = DesiredAction.REDUCE if has_confirmed else DesiredAction.WAIT
        if not has_confirmed:
            _clamp("适配钳制: 无持仓无减仓对象，REDUCE 转 WAIT")
    elif pos_action == "CLOSE_ALL":
        desired = DesiredAction.EXIT if (not known_confirmed or has_confirmed) else DesiredAction.WAIT
        if desired is not DesiredAction.EXIT:
            _clamp("适配钳制: 已确认空仓，清仓建议转 WAIT")
    elif pos_action == "HOLD_POSITION":
        desired = DesiredAction.HOLD
    else:  # STAY_OUT
        desired = DesiredAction.REVIEW if terminal_decision == "WATCH" else DesiredAction.WAIT

    # ── target_weight 映射（不变量优先，冲突时置 None 保住包合法性）──
    target: Optional[float] = None
    sd_ratio = getattr(strategy_decision, "position_ratio", None)
    if desired is DesiredAction.EXIT:
        target = 0.0
    elif desired in (DesiredAction.REDUCE, DesiredAction.ADD, DesiredAction.OPEN):
        cand = float(sd_ratio) if (sd_ratio is not None and sd_ratio > 0) else None
        if not known_confirmed:
            # 组合信息未知：只能给有条件方向（契约不变量：缺组合信息禁精确目标）
            if cand is not None:
                _clamp("适配钳制: 组合信息未知，仅保留方向（target 置 None）")
        elif cand is not None:
            if desired is DesiredAction.REDUCE:
                if known_confirmed and cand >= confirmed_ratio - 1e-9:
                    _clamp("适配钳制: 减仓目标未低于当前仓位，target 置 None 待复核")
                else:
                    target = cand
            elif desired in (DesiredAction.ADD, DesiredAction.OPEN):
                if desired is DesiredAction.ADD and known_confirmed and cand <= confirmed_ratio + 1e-9:
                    _clamp("适配钳制: 加仓目标未高于当前仓位，target 置 None 待复核")
                elif cand > 0:
                    target = cand
        if target is None:
            _clamp("适配钳制: 目标仓位缺失，仅保留方向（有条件建议）")
    # HOLD/WAIT/REVIEW → target 保持 None（不建议比例变化）

    # ── delta_weight ──
    delta: Optional[float] = None
    if target is not None and known_confirmed:
        delta = round(target - confirmed_ratio, 10)

    # ── execution_status（执行层评估是时点可行性唯一来源）──
    blocked = bool(execution_eval is not None and getattr(execution_eval, "blocked", False))
    block_reason = (getattr(execution_eval, "block_reason", "") or "") if blocked else ""
    if desired in (DesiredAction.HOLD, DesiredAction.WAIT, DesiredAction.REVIEW):
        execution_status = ExecutionStatus.NOT_NEEDED
    elif blocked:
        execution_status = ExecutionStatus.BLOCKED
    else:
        execution_status = ExecutionStatus.ELIGIBLE

    blockers = []
    if execution_status is ExecutionStatus.BLOCKED:
        blockers.append(Blocker(kind="execution", detail=block_reason or "执行层评估受阻"))

    # ── research_status（数据缺口→INCOMPLETE；技术/AI 分歧→CONFLICTED）──
    warnings = list(getattr(decision_result, "warnings", None) or [])
    divergence = getattr(strategy_decision, "divergence", None)
    if divergence:
        research_status = ResearchStatus.CONFLICTED
    elif warnings:
        research_status = ResearchStatus.INCOMPLETE
    else:
        research_status = ResearchStatus.COMPLETE

    packet = DecisionPacket(
        security_id=str(decision_result.stock.stock_code),
        as_of=datetime.now().astimezone(),
        # F2 审查 P2 登记：收盘评估口径——盘中跑 l 时 phase 语义与 as_of 有张力，
        # 盘中/收盘 market_phase 区分留 F3（证据快照层随行情时点贯通）
        market_phase=MarketPhase.CLOSE,
        policy_id=POLICY_ID_LEGACY,
        horizon=Horizon.LEGACY,
        research_status=research_status,
        thesis_status=ThesisStatus.UNESTABLISHED,
        desired_action=desired,
        confirmed_weight=confirmed_ratio,
        target_weight=target,
        delta_weight=delta,
        execution_status=execution_status,
        executable_action=(desired if execution_status is ExecutionStatus.ELIGIBLE else None),
        blockers=blockers,
        reason_codes=reason_codes,
        legacy_trace=LegacyTrace(
            decision=decision_result.decision.value,
            action_strength=float(decision_result.score),
            position_action=pos_action,
            position_ratio=(float(sd_ratio) if sd_ratio is not None else None),
            sell_path=getattr(strategy_decision, "sell_path", None),
        ),
    )
    logger.debug(
        f"DecisionPacket built ({source}): {packet.security_id} {desired.value} "
        f"target={target} exec={execution_status.value}")
    return packet
