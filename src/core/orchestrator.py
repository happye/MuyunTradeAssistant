"""编排层 - 协调数据、技能、决策、事件、AI调节、策略、执行流程

v0.8.1 架构：
  Data Layer
     ↓
  Signal Layer（Skill Engine）
     ↓
  Decision Layer（信号聚合器）
     ↓
  Event Layer（事件检测+预警，v0.8.1 RAG增强）
     ↓
  AI Modifier Layer（新闻分析+情绪调节，v0.8.1 RAG增强）
     ↓
  Strategy Layer（约束行为、延迟决策、抑制噪声）
     ↓
  Execution Layer（现实约束建模）

Orchestrator 协调完整的七层流程。
v0.8.1 新增 rag_service 参数，传递给 EventLayer 和 AIModifier。
"""

import logging
from typing import Optional

from src.data.models import (
    StockData, DecisionResult, StrategyState, StrategyDecision,
    ExecutionConstraint, TradeLifecycle, AIModifierResult, MarketEvent,
    SignalType, PositionAction
)
from src.core.skill_engine import SkillEngine
from src.core.decision_engine import DecisionEngine, StateMachine
from src.core.strategy_layer import StrategyLayer
from src.core.execution_layer import ExecutionLayer, ExecutionEvaluation

logger = logging.getLogger(__name__)

# v0.8.7.8 审计修复 D03 同类点（第四轮审查 ISS-072）：
# 本文件有**三条**「强制离场」通道，全部是确定性触发（技术破位 / 基本面地雷 / 大顶信号），
# 与止损一票否决同级（stop_loss 技能规则 conf 0.85~0.9）。但三条通道历史上都只改
# decision/position_action，**从不改 score** —— 而策略层的「信号确认」「反转成本」两道闸门
# 都挂在 score 上，于是安全网被当成「零置信度信号」静默降级为 HOLD，HOLD 分支再走
# `buy_score > sell_score*1.5` 反手 ADD（在破位 / 大顶 / 被ST 当日加仓）。
# 三处统一用本常量给 score 兜底：
#   ① EntryExit Chandelier / 趋势破坏（条件是 `force_exit`，EXIT/STOP/TRIM 三种 action 全兜底；
#      不能写成 `position_action == CLOSE_ALL`——趋势破坏的 MA5<MA20 走 TRIM/REDUCE，
#      实测 601318/2024 有 2 例因此漏兜底，安全网被降级后反手 ADD）
#   ② Layer 3.84  FundamentalAlert（被ST / 业绩预亏，回测禁用但 live 生效）
#   ③ Layer 3.85  TopSignal（高位止盈 3 维大顶，P1 硬信号）
# 取值依据：需同时跨过 _apply_reverse_cost（成本 0.003 + 0.002/次反转，10 次也才 0.023）
# 与 _needs_confirmation 的弱信号阈值；与 StrategyLayer.STOP_LOSS_EXIT_THRESHOLD=0.85 同档。
FORCE_EXIT_SCORE = 0.85


class Orchestrator:
    """编排器 - 协调七层分析流程（v0.8.0 含事件层+AI调节层）"""

    def __init__(
        self,
        skills_dir: str = "./src/skills",
        enabled_skills: Optional[list[str]] = None,
        signal_weights: Optional[dict[str, float]] = None,
        skill_types: Optional[dict[str, str]] = None,
        execution_constraint: Optional[ExecutionConstraint] = None,
        ai_config: Optional[dict] = None,
        event_config: Optional[dict] = None,
        rag_service=None,  # v0.8.1: RAG服务实例
        entry_exit_config: Optional[dict] = None,  # v0.8.3 Phase C
    ):
        # v0.8.1: RAG服务（可选，用于策略知识增强）
        self.rag_service = rag_service

        # 初始化技能引擎（Signal Layer）
        self.skill_engine = SkillEngine(skills_dir, skill_types)
        self.skill_engine.load_skills(enabled_skills)

        # 初始化信号聚合器（Decision Layer）
        self.decision_engine = DecisionEngine(signal_weights)

        # 初始化AI调节层（AI Modifier Layer, v0.8.0新增, v0.8.1增加RAG）
        self.ai_modifier = None
        if ai_config and ai_config.get("enabled", False):
            try:
                from src.core.ai_modifier import AIModifier
                self.ai_modifier = AIModifier(ai_config, rag_service=rag_service)
                if not self.ai_modifier.is_available():
                    logger.warning("AI Modifier初始化失败（API Key未配置？），将跳过AI调节")
                    self.ai_modifier = None
            except Exception as e:
                logger.warning(f"AI Modifier初始化异常: {e}，将跳过AI调节")
                self.ai_modifier = None

        # 初始化事件驱动层（Event Layer, v0.8.0 Phase 3新增, v0.8.1增加RAG）
        self.event_layer = None
        if event_config and event_config.get("enabled", False):
            try:
                from src.core.event_layer import EventLayer
                self.event_layer = EventLayer(event_config, ai_config=ai_config, rag_service=rag_service)
                if not self.event_layer.is_available():
                    logger.warning("EventLayer初始化失败，将跳过事件检测")
                    self.event_layer = None
            except Exception as e:
                logger.warning(f"EventLayer初始化异常: {e}，将跳过事件检测")
                self.event_layer = None

        # 初始化策略层（Strategy Layer）
        self.strategy_layer = StrategyLayer()

        # 初始化执行层（Execution Layer）
        self.execution_layer = ExecutionLayer(execution_constraint)

        # v0.8.3 Phase C: 买卖点计算器
        self.entry_exit_calc = None
        if entry_exit_config and entry_exit_config.get("enabled", False):
            try:
                from src.core.entry_exit import EntryExitCalculator
                self.entry_exit_calc = EntryExitCalculator(entry_exit_config)
                logger.info("EntryExitCalculator initialized")
            except Exception as e:
                logger.warning(f"EntryExitCalculator init error: {e}")

        ai_status = "enabled" if self.ai_modifier else "disabled"
        event_status = "enabled" if self.event_layer else "disabled"
        rag_status = "enabled" if self.rag_service else "disabled"
        logger.info(f"Orchestrator initialized (v0.8.1: Signal→Decision→Event({event_status})→AI Modifier({ai_status})→Strategy→Execution, RAG={rag_status})")

    @staticmethod
    def _compute_fundamental_alert(data, has_position: bool, is_backtest: bool, trade_plan) -> Optional[str]:
        """ISS-053 基本面恶化硬退出判定（被 ST / 业绩预告预亏预减）。

        回测禁用（is_backtest=True）：baostock ST 状态/业绩预告均为**当前**数据，非 bar 时点的
        point-in-time--回测里未来预亏预告会在第一根持仓bar即触发退出（前瞻偏差，审查修复）。
        live（is_backtest=False）启用：持仓才查，最新预告/ST 状态正是 live 该用的。
        fail-open：异常返回 None（不假退出）但记 WARNING。
        """
        if not has_position or is_backtest:
            return None
        try:
            from src.core.exit_signals.fundamental import check_fundamental_alert
            return check_fundamental_alert(
                data.stock_code,
                entry_date=trade_plan.opened_at if trade_plan else None,
            )
        except Exception as e:
            logger.warning(f"[FundamentalAlert] 检查异常(安全网当日可能有洞): {e}")
            return None

    def analyze(
        self,
        data: StockData,
        force_state: Optional[str] = None,
        current_position_ratio: float = 0.0,
        strategy_state: Optional[StrategyState] = None,
        ai_enabled: bool = True,
        has_position: bool = False,
        entry_price: Optional[float] = None,
        position_tier: str = "pilot",
        high_since_entry: Optional[float] = None,
        trade_plan=None,  # v0.8.5 阶段 1.3: TradePlan 实例（来自 PortfolioManager）
        today: Optional[str] = None,  # v0.8.5: 用于 PlanGuard 的时间止损判定
        is_backtest: bool = False,  # ISS-053 审查修复: 回测禁用 fundamental_alert（baostock 当前数据非 point-in-time，会前瞻）
    ) -> tuple[DecisionResult, StrategyDecision, ExecutionEvaluation, Optional[AIModifierResult]]:
        """执行完整分析流程（v0.8.0 六层架构）

        Args:
            data: 股票数据
            force_state: 强制市场状态
            current_position_ratio: 当前仓位比例（0-1）
            strategy_state: 上一交易日的策略层状态（None=从FLAT开始）
            ai_enabled: 是否启用AI调节层（默认True）

        Returns:
            (信号聚合结果, 策略层决策, 执行层评估, AI调节结果)
        """
        logger.info(f"Analyzing {data.stock_code} {data.stock_name}")

        # Layer 1: 确定市场状态
        if force_state:
            from src.data.models import MarketState
            state = MarketState[force_state.upper()]
        else:
            state = StateMachine.determine_state(data)

        logger.info(f"Market state: {state}")

        # Layer 2: 执行所有技能（Signal Layer）
        signals = self.skill_engine.execute_all(data)
        logger.info(f"Generated {len(signals)} signals")

        for sig in signals:
            logger.info(f"  - {sig.skill_alias}: {sig.signal} (conf: {sig.confidence})")

        # Layer 3: 信号聚合（Decision Layer）
        decision_result = self.decision_engine.make_decision(
            data, signals, state, current_position_ratio=current_position_ratio
        )
        logger.info(f"Decision aggregate: {decision_result.decision} (score: {decision_result.score})")

        # Layer 3.25: 事件驱动层（Event Layer, v0.8.0 Phase 3新增）
        # v0.8.7.6 审计修复 B05：补 is_backtest 门控（原仅靠回测构造时不传 config 侥幸隔离，
        # 新复用 CLI orchestrator 的回测调用点会把"今天"的新闻/事件注入历史 bar）
        event_ai_result = None
        if (not is_backtest and self.event_layer
                and self.event_layer.enabled and self.event_layer.auto_scan):
            active_events = self.event_layer.check_events()
            if active_events:
                # 取impact_level最高的事件
                top_event = max(active_events, key=lambda e: e.impact_level)
                if top_event.impact_level >= 3:
                    event_ai_result = self.event_layer.to_ai_modifier_result(top_event)
                    logger.info(
                        f"Event Layer detected: {top_event.event_type} "
                        f"impact={top_event.impact_level} sentiment={top_event.sentiment}"
                    )
                    # 记录事件到决策理由
                    event_type_cn = {
                        "policy": "政策", "war": "地缘冲突", "earnings": "财报",
                        "macro": "宏观", "black_swan": "黑天鹅", "market_crash": "暴跌"
                    }
                    sentiment_cn = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}
                    decision_result.reason.append(
                        f"⚡事件预警: [{event_type_cn.get(top_event.event_type, top_event.event_type)}] "
                        f"{sentiment_cn.get(top_event.sentiment, top_event.sentiment)} "
                        f"(等级{top_event.impact_level}), {top_event.summary}"
                    )

        # Layer 3.5: AI调节层（v0.8.0新增）
        # B05 修复：同事件层，回测硬门控（AI 看到的新闻是"今天"的，注入历史 bar = 前瞻）
        ai_result = None
        if ai_enabled and not is_backtest and self.ai_modifier and self.ai_modifier.is_available():
            ai_result = self.ai_modifier.analyze(data)
            if ai_result.adjusted:
                # 应用信号调节
                original_score = decision_result.score
                decision_result.score = max(0.0, min(1.0, decision_result.score + ai_result.score_adjustment))
                logger.info(
                    f"AI Modifier adjusted score: {original_score:.3f} → {decision_result.score:.3f} "
                    f"(adjustment={ai_result.score_adjustment:.3f})"
                )

                # 应用仓位调节（传递给Strategy Layer通过decision_result）
                if ai_result.position_cap < 1.0:
                    decision_result.position_ratio = min(
                        decision_result.position_ratio, ai_result.position_cap
                    )
                    logger.info(f"AI Modifier capped position: max {ai_result.position_cap:.0%}")

                # 应用状态干预
                if ai_result.force_state:
                    force_state = ai_result.force_state
                    from src.data.models import MarketState
                    state = MarketState[force_state.upper()]
                    decision_result.state = state
                    logger.warning(f"AI Modifier forced state: {state.value}")

                # 记录AI调节到决策理由
                sentiment_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
                decision_result.reason.append(
                    f"AI情绪: {sentiment_cn.get(ai_result.sentiment, ai_result.sentiment)}"
                    f"({ai_result.confidence:.0%}), {ai_result.summary}"
                )

        # 合并事件层调节到AI调节结果
        if event_ai_result:
            # 如果AI调节也存在，叠加事件效果
            if ai_result and ai_result.adjusted:
                # v0.8.7.6 审计修复 B07：AI 分支此前已把 AI 自己的调节应用到 decision_result，
                # 本合并块原先只改 ai_result 字段不重新应用 → 事件层的分数压制/仓位上限/
                # 强制状态在 AI 开启时被静默丢弃。此处只补应用"事件层增量"（AI 部分已应用过，勿二次叠加）。
                _orig = decision_result.score
                decision_result.score = max(0.0, min(1.0, decision_result.score + event_ai_result.score_adjustment))
                logger.info(
                    f"Event Layer merged adjustment: {_orig:.3f} → {decision_result.score:.3f} "
                    f"(event adjustment={event_ai_result.score_adjustment:.3f})"
                )
                if event_ai_result.position_cap < 1.0:
                    decision_result.position_ratio = min(
                        decision_result.position_ratio, event_ai_result.position_cap
                    )
                    logger.info(f"Event Layer capped position: max {event_ai_result.position_cap:.0%}")
                if event_ai_result.force_state:
                    from src.data.models import MarketState
                    decision_result.state = MarketState[event_ai_result.force_state.upper()]
                    logger.warning(f"Event Layer forced state: {decision_result.state.value}")
                # 同步合并后的 ai_result 字段（下游展示/日志用）
                ai_result.score_adjustment += event_ai_result.score_adjustment
                ai_result.position_cap = min(ai_result.position_cap, event_ai_result.position_cap)
                if event_ai_result.force_state:
                    ai_result.force_state = event_ai_result.force_state
            else:
                # 事件层独立调节
                ai_result = event_ai_result

                # 应用信号调节
                original_score = decision_result.score
                decision_result.score = max(0.0, min(1.0, decision_result.score + ai_result.score_adjustment))
                logger.info(
                    f"Event Layer adjusted score: {original_score:.3f} → {decision_result.score:.3f} "
                    f"(adjustment={ai_result.score_adjustment:.3f})"
                )

                # 应用仓位调节
                if ai_result.position_cap < 1.0:
                    decision_result.position_ratio = min(
                        decision_result.position_ratio, ai_result.position_cap
                    )
                    logger.info(f"Event Layer capped position: max {ai_result.position_cap:.0%}")

                # 应用状态干预
                if ai_result.force_state:
                    force_state = ai_result.force_state
                    from src.data.models import MarketState
                    state = MarketState[force_state.upper()]
                    decision_result.state = state
                    logger.warning(f"Event Layer forced state: {state.value}")

        # Layer 3.75: 买卖点精确触发（Entry/Exit Calculator, v0.8.3 Phase C）
        entry_exit_result = None
        if self.entry_exit_calc and self.entry_exit_calc.enabled:
            entry_exit_result = self.entry_exit_calc.calculate(
                data, has_position, entry_price, position_tier, high_since_entry
            )
            if entry_exit_result.override_decision:
                decision_result.overridden_by = "entry_exit"
                decision_result.overridden_reason = (
                    entry_exit_result.entry_reason or entry_exit_result.exit_reason
                )
                # v0.8.3 Phase C: 买点仅精化BUY，卖点可覆盖HOLD（Chandelier/趋势破坏是安全网）
                current_decision = decision_result.decision
                action = entry_exit_result.override_action
                exit_type = entry_exit_result.exit_type
                if action in ("ENTRY", "ADD") and current_decision == SignalType.BUY:
                    decision_result.position_action = PositionAction.OPEN if action == "ENTRY" else PositionAction.ADD
                    decision_result.reason.append(f"[EntryExit] {decision_result.overridden_reason}")
                    logger.info(f"[EntryExit] Refine entry: {decision_result.overridden_reason}")
                elif action in ("EXIT", "STOP", "TRIM"):
                    # 卖点覆盖条件：决策已SELL，或Chandelier/趋势破坏安全网触发（覆盖HOLD）
                    force_exit = exit_type in ("chandelier_stop", "trend_break")
                    if current_decision == SignalType.SELL or (has_position and force_exit):
                        decision_result.decision = SignalType.SELL
                        decision_result.position_action = PositionAction.CLOSE_ALL if action in ("EXIT", "STOP") else PositionAction.REDUCE
                        # A06 修复：CLOSE_ALL 同步清零仓位，防 portfolio 记成幽灵残留仓位
                        if decision_result.position_action == PositionAction.CLOSE_ALL:
                            decision_result.position_ratio = 0.0
                        # v0.8.7.8 审计修复 D03 同类点（第四轮审查 ISS-072，🔴）：
                        # 本分支（Chandelier Exit / 趋势破坏）是**安全网**，与止损一票否决同级，
                        # 但此处只改 decision/position_action，从未改 score —— 而策略层
                        # 的「信号确认」「反转成本」两道闸门都挂在 score 上，score 仍是
                        # make_decision 算出的原桶（WATCH/HOLD 通常 0.0）→ 安全网被静默降级为
                        # HOLD，HOLD 分支再走 buy_score > sell_score*1.5 反手 ADD（在破位当日加仓）。
                        # 与 decision_engine 的 veto_conf 兜底同一思路：确定性技术触发
                        # 给予与止损技能同档的置信度（stop_loss 规则 conf 0.85~0.9）。
                        #
                        # ⚠️ 兜底条件是 `force_exit`（安全网是否触发），**不是** position_action。
                        # 2026-08-30 实测修正：趋势破坏的短期信号（MA5<MA20）产出 exit_action="TRIM"
                        # → position_action=REDUCE，走不到 CLOSE_ALL 分支；但 decision 已被强设为
                        # SELL，score 仍是 0 → 同样被降级成 HOLD 并反手 ADD。601318/2024 全部
                        # 2 例残留 score=0 SELL 均来自此。安全网一触发就兜底，减仓量级由
                        # position_action 控制，与 score 无关，两者不该耦合。
                        if force_exit and decision_result.score < FORCE_EXIT_SCORE:
                            decision_result.score = FORCE_EXIT_SCORE
                        decision_result.reason.append(f"[EntryExit] {decision_result.overridden_reason}")
                        logger.info(f"[EntryExit] Force exit: {decision_result.overridden_reason}")
                    else:
                        logger.debug(f"[EntryExit] Exit blocked: decision={current_decision.value}")
                elif action in ("ENTRY", "ADD"):
                    logger.debug(f"[EntryExit] Entry blocked: decision={current_decision.value} != BUY")
                elif action in ("EXIT", "STOP", "TRIM"):
                    logger.debug(f"[EntryExit] Exit blocked: decision={current_decision.value} != SELL")

        # Layer 3.84: 基本面恶化硬退出（ISS-053）--仅持仓检查，独立 fundamental_alert 通道
        # 被 ST / 业绩预告预亏预减 -> 强制 SELL+CLOSE_ALL。fail-open（异常跳过不假退出）但记 WARNING。
        # 回测禁用（is_backtest）：baostock ST/预告为当前数据非 point-in-time，回测里未来预告/当前ST会前瞻（审查修复）。
        falert = self._compute_fundamental_alert(data, has_position, is_backtest, trade_plan)
        if falert:
            decision_result.decision = SignalType.SELL
            decision_result.position_action = PositionAction.CLOSE_ALL
            decision_result.position_ratio = 0.0  # A06 修复：同步清零，防幽灵仓位
            # v0.8.7.8 ISS-072 D03 同类点②：与 Chandelier 通道同理，确定性强制离场必须
            # 给 score 兜底，否则策略层两道闸门（_needs_confirmation / _apply_reverse_cost）
            # 会把「被 ST / 业绩预亏」这种硬退出当成零置信度信号降级成 HOLD 甚至反手 ADD。
            if decision_result.score < FORCE_EXIT_SCORE:
                decision_result.score = FORCE_EXIT_SCORE
            decision_result.reason.append(f"[FundamentalAlert] 重大利空: {falert}")
            logger.info(f"[FundamentalAlert] 强制离场: {falert}")

        # Layer 3.85: 高位止盈3维度大顶信号检查（跳法A 阶段2 / v0.8.6.4）
        # 仅持仓时检查；触发即作为 P1 信号强制 SELL（PlanGuard 不可压制，仅次于致命止损）。
        # 全客观硬规则（成交额/换手/缩量/减持），不依赖 AI 实时判断。
        top_signal = None
        if has_position:
            try:
                from src.core.exit_signals import check_top_signals
                top_signal = check_top_signals(
                    data, data.stock_code,
                    announcements=getattr(data, "recent_announcements", None),
                    market_turnover_trillion=getattr(data, "market_turnover_trillion", None),
                    trade_plan=trade_plan,
                    live=not is_backtest,
                )
            except Exception as e:
                logger.debug(f"高位止盈信号检查失败: {e}")
            if top_signal:
                decision_result.decision = SignalType.SELL
                decision_result.position_action = PositionAction.CLOSE_ALL
                decision_result.position_ratio = 0.0  # A06 修复：同步清零，防幽灵仓位
                # v0.8.7.8 ISS-072 D03 同类点③：TopSignal 是 P1 硬信号（PlanGuard 不可压制，
                # 仅次于致命止损），同样必须给 score 兜底，否则大顶当日被降级成 HOLD/ADD。
                if decision_result.score < FORCE_EXIT_SCORE:
                    decision_result.score = FORCE_EXIT_SCORE
                decision_result.reason.append(f"[TopSignal] 高位止盈: {top_signal}")
                logger.info(f"[TopSignal] 大顶信号强制离场: {top_signal}")

        # 报告④ 高位利好=出货风险（笨总"主升浪时最怕利好，出货良机"）
        # 高位(从60日低点翻倍) + AI利好 -> advisory 警告（非强制清仓，提示用户兑现勿贪婪）
        if has_position and not top_signal and ai_result and ai_result.sentiment in ("看涨", "bullish"):
            try:
                _price = getattr(data, "price", None)
                _low60 = getattr(data, "low_60d", None)
                if _price and _low60 and _low60 > 0 and _price / _low60 >= 2.0:
                    decision_result.reason.append(
                        f"[高位利好提示] 笨总: 主升浪高位遇利好=出货良机"
                        f"（近60日涨{_price / _low60:.1f}倍+AI看多），注意兑现勿贪婪"
                    )
            except Exception:
                pass

        # Layer 4: 策略层过滤（Strategy Layer）
        if strategy_state is None:
            strategy_state = StrategyState(
                current_position_ratio=current_position_ratio,
                lifecycle=TradeLifecycle.FLAT if current_position_ratio <= 0 else TradeLifecycle.HOLD,
            )

        # 跳法A（MarketState 降级）：把笨总 mode 透传给策略层，气宗走固定长持参数
        self.strategy_layer._active_mode = trade_plan.mode if trade_plan is not None else None
        strategy_decision = self.strategy_layer.process(
            decision_result, strategy_state, data, current_date=today
        )

        # v0.8.3 Phase C: 将买卖点结果附加到策略决策
        if entry_exit_result:
            strategy_decision.entry_exit = entry_exit_result.model_dump()

        # 跳法A 阶段2: 把大顶信号标到策略决策上，让 PlanGuard 按 P1 不可压处理
        if top_signal:
            strategy_decision.top_signal = top_signal
            if strategy_decision.decision == SignalType.SELL and not strategy_decision.sell_path:
                strategy_decision.sell_path = "top_signal"

        # ISS-053: fundamental_alert 标到策略决策，PlanGuard 规则4.5 按不可压处理
        if falert:
            strategy_decision.fundamental_alert = falert
            if strategy_decision.decision == SignalType.SELL and not strategy_decision.sell_path:
                strategy_decision.sell_path = "fundamental_alert"

        # v0.8.6.3 (ISS-033) + P1(a) 审查修复：Chandelier/trend_break force_exit 经 strategy_layer 后
        # sell_path 可能落空（_calculate_position 重算 position_action 致 _infer_sell_path 推断不到）。
        # P1(a)：force_exit(EXIT/STOP) 是技术安全网，strategy_layer 5机制可把 SELL 降级为 HOLD 致丢失，
        # 触发即强制 SELL+CLOSE_ALL 覆盖降级（不被 confirmation/inertia 吞）。sell_path 标 trend_exit
        # （气宗可压，ISS-033 设计；P1(b) 气宗是否该压 Chandelier 另议，待笨总+回测）。
        if (entry_exit_result
                and entry_exit_result.override_action in ("EXIT", "STOP")
                and entry_exit_result.exit_type in ("chandelier_stop", "trend_break")):
            if strategy_decision.decision != SignalType.SELL:
                strategy_decision.decision = SignalType.SELL
                strategy_decision.strategy_reasons.insert(0, f"force_exit 救回: {entry_exit_result.exit_type}（覆盖 strategy_layer 降级）")
            strategy_decision.position_action = PositionAction.CLOSE_ALL
            # ISS-078：sell_path 残留 weak_sell 时必须覆写为 trend_exit——weak_sell 会被
            # PlanGuard 规则1 在剑宗也压制（CLOSE_ALL+weak_sell 组合已加守卫，双保险），
            # 且标注语义错误：这是技术安全网触发的清仓，不是策略层的弱卖出减仓。
            if not strategy_decision.sell_path or strategy_decision.sell_path == "weak_sell":
                strategy_decision.sell_path = "trend_exit"
        elif (entry_exit_result
                and strategy_decision.decision == SignalType.SELL
                and entry_exit_result.override_action == "TRIM"
                and entry_exit_result.exit_type in ("chandelier_stop", "trend_break")
                and not strategy_decision.sell_path):
            strategy_decision.sell_path = "trend_exit"

        # v0.8.3 收尾 ISS-030: 买卖点与AI情绪分歧检测
        if ai_result and entry_exit_result:
            ai_sentiment = ai_result.sentiment
            ai_bearish = ai_sentiment in ("看跌", "bearish")
            ai_bullish = ai_sentiment in ("看涨", "bullish")
            tech_buy = entry_exit_result.entry_triggered
            tech_sell = entry_exit_result.exit_triggered

            if tech_buy and ai_bearish:
                strategy_decision.divergence = {
                    "type": "entry_vs_bearish_ai",
                    "technical_signal": "买点触发",
                    "ai_sentiment": ai_sentiment,
                    "warning": f"技术面买点已触发，但AI情绪{ai_sentiment}。买点规则优先，AI仅作风险提示。"
                }
            elif tech_sell and ai_bullish:
                strategy_decision.divergence = {
                    "type": "exit_vs_bullish_ai",
                    "technical_signal": "卖点触发",
                    "ai_sentiment": ai_sentiment,
                    "warning": f"技术面卖点已触发，但AI情绪{ai_sentiment}。卖点规则优先，AI仅作风险提示。"
                }

        # AI仓位上限影响Strategy Layer的仓位建议
        if ai_result and ai_result.position_cap < 1.0:
            original_ratio = strategy_decision.position_ratio
            strategy_decision.position_ratio = min(
                strategy_decision.position_ratio, ai_result.position_cap
            )
            if strategy_decision.position_ratio != original_ratio:
                logger.info(
                    f"AI Modifier adjusted strategy position: {original_ratio:.0%} → {strategy_decision.position_ratio:.0%}"
                )

        logger.info(f"Strategy decision: {strategy_decision.decision} "
                     f"(lifecycle: {strategy_decision.lifecycle_before.value}→{strategy_decision.lifecycle_after.value})")

        # v0.8.5 阶段 1.3: PlanGuard — 根据 TradePlan 调整 strategy_decision
        # 只压制 weak_sell（计划未失效时不卖）；致命止损/趋势退出/止盈分批不动
        if trade_plan is not None:
            from src.core.plan_guard import PlanGuard
            guard = PlanGuard()
            strategy_decision = guard.evaluate(strategy_decision, trade_plan, data, today)
            logger.debug(f"PlanGuard evaluated; final decision={strategy_decision.decision}")

        # Layer 5: 执行层评估（Execution Layer）
        volume_ratio = None
        if data.avg_volume_20 and data.avg_volume_20 > 0:
            volume_ratio = data.volume / data.avg_volume_20

        execution_eval = self.execution_layer.evaluate(
            data, strategy_decision.position_action, volume_ratio
        )
        if execution_eval.blocked:
            logger.info(f"Execution blocked: {execution_eval.block_reason}")

        return decision_result, strategy_decision, execution_eval, ai_result

    def get_available_skills(self) -> list[str]:
        """获取已加载的技能列表"""
        return list(self.skill_engine.skills.keys())
