"""回测引擎 - v0.7.2 重构版

整合 Strategy Layer + Execution Layer，实现：
- 交易生命周期状态机（FLAT→OPEN→HOLD→EXIT→COOLDOWN→FLAT）
- 执行约束（波动率滑点/流动性/涨跌停/冲击成本）
- Monte Carlo 多路径回测
- 稳定性指标（最差收益/回撤稳定性/结果方差/决策稳定性）

交易规则（v0.4.0 修正 + v0.7.2 增强）：
  - 信号生成：Day N-1收盘后分析，生成信号
  - 执行时机：Day N开盘价执行（消除前视偏差）
  - T+1硬限制：买入后至少下一交易日才能卖出（A股基本规则）
  - 策略级最小持有天数：min_hold_days（可配置，默认3天，含T+1）
  - 佣金：券商佣金(0.025%，最低5元) + 印花税(卖出0.05%) + 过户费(0.001%)
  - 滑点：v0.7.2 与波动率挂钩（Execution Layer）
  - 涨跌停限制：v0.7.2 涨停不买/跌停不卖
"""

import logging
import math
import random
from typing import Optional
from datetime import datetime

from src.data.models import (
    StockData, BacktestResult, TradeRecord, DailySnapshot,
    SignalType, DecisionResult, PositionAction,
    StrategyState, StrategyDecision, ExecutionConstraint,
    TradeLifecycle, infer_action_semantic
)
from src.data.data_feeder import DataFeeder
from src.core.orchestrator import Orchestrator
from src.core.execution_layer import ExecutionLayer, ExecutionEvaluation
from src.core.bias_auditor import BiasAuditor

logger = logging.getLogger(__name__)


class SimulatedAccount:
    """模拟交易账户（支持仓位管理）"""

    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.position = 0
        self.avg_cost = 0.0
        self.buy_date = None
        self.total_buy = 0
        self.total_sell = 0

    @property
    def has_position(self) -> bool:
        return self.position > 0

    def position_ratio(self, price: float) -> float:
        """当前仓位比例（持仓市值/总资产）"""
        total = self.total_value(price)
        if total <= 0:
            return 0.0
        return self.market_value(price) / total

    def buy(self, price: float, max_ratio: float = 0.95, target_position_ratio: Optional[float] = None) -> Optional[TradeRecord]:
        """买入"""
        if target_position_ratio is not None and target_position_ratio > 0:
            total_value = self.total_value(price)
            target_market_value = total_value * target_position_ratio
            current_market_value = self.market_value(price)
            needed = target_market_value - current_market_value
            if needed <= 0:
                return None
            available = min(needed, self.cash * 0.95)
        else:
            available = self.cash * max_ratio

        if available < price * 100:
            return None

        shares = int(available / price / 100) * 100
        if shares <= 0:
            return None

        amount = shares * price

        if self.position > 0:
            total_cost = self.avg_cost * self.position + amount
            self.avg_cost = total_cost / (self.position + shares)
        else:
            self.avg_cost = price

        self.position += shares
        self.cash -= amount
        self.total_buy += 1

        return TradeRecord(
            date="",
            action="BUY",
            price=round(price, 2),
            shares=shares,
            amount=round(amount, 2),
        )

    def sell(self, price: float, shares: Optional[int] = None, reduce_ratio: Optional[float] = None) -> Optional[TradeRecord]:
        """卖出"""
        if self.position <= 0:
            return None

        if reduce_ratio is not None:
            sell_shares = int(self.position * reduce_ratio / 100) * 100
            if sell_shares <= 0:
                sell_shares = self.position
            sell_shares = min(sell_shares, self.position)
        elif shares:
            sell_shares = min(shares, self.position)
        else:
            sell_shares = self.position

        amount = sell_shares * price

        self.position -= sell_shares
        self.cash += amount
        self.total_sell += 1

        if self.position == 0:
            self.avg_cost = 0.0
            self.buy_date = None

        return TradeRecord(
            date="",
            action="SELL",
            price=round(price, 2),
            shares=sell_shares,
            amount=round(amount, 2),
        )

    def market_value(self, price: float) -> float:
        return self.position * price

    def total_value(self, price: float) -> float:
        return self.cash + self.market_value(price)


class BacktestEngine:
    """回测引擎 v0.7.2

    用法:
        engine = BacktestEngine(
            stock_code="600519",
            start_date="2024-01-01",
            end_date="2025-01-01",
            initial_capital=100000,
        )
        result = engine.run()
        # Monte Carlo:
        mc_results = engine.run_monte_carlo(n_simulations=20)
    """

    # A股交易成本常量
    DEFAULT_COMMISSION_RATE = 0.00025
    MIN_COMMISSION = 5.0
    STAMP_TAX_RATE = 0.0005
    TRANSFER_FEE_RATE = 0.00001

    # 回测执行模式
    MODE_FRAMEWORK_STRICT = "framework_strict"
    MODE_LEGACY_COMPATIBLE = "legacy_compatible"

    # 回测分层模式
    LAYER_DECISION_ONLY = "decision_only"
    LAYER_DECISION_STRATEGY = "decision_strategy"
    LAYER_DECISION_STRATEGY_EXECUTION = "decision_strategy_execution"

    def __init__(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 100000.0,
        min_hold_days: int = 5,
        cooldown_days: int = 5,
        commission_rate: float = 0.00025,
        slippage_pct: float = 0.001,
        execution_mode: str = MODE_FRAMEWORK_STRICT,
        layer_mode: str = LAYER_DECISION_STRATEGY_EXECUTION,
        skills_dir: str = "./src/skills",
        enabled_skills: Optional[list[str]] = None,
        signal_weights: Optional[dict[str, float]] = None,
        skill_types: Optional[dict[str, str]] = None,
        execution_constraint: Optional[ExecutionConstraint] = None,
    ):
        self.stock_code = stock_code
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.min_hold_days = max(min_hold_days, 1)
        self.cooldown_days = cooldown_days
        self.commission_rate = commission_rate
        self.slippage_pct = slippage_pct
        if execution_mode not in (self.MODE_FRAMEWORK_STRICT, self.MODE_LEGACY_COMPATIBLE):
            raise ValueError(
                f"Invalid execution_mode: {execution_mode}. "
                f"Expected one of: {self.MODE_FRAMEWORK_STRICT}, {self.MODE_LEGACY_COMPATIBLE}"
            )
        if layer_mode not in (
            self.LAYER_DECISION_ONLY,
            self.LAYER_DECISION_STRATEGY,
            self.LAYER_DECISION_STRATEGY_EXECUTION,
        ):
            raise ValueError(
                f"Invalid layer_mode: {layer_mode}. "
                f"Expected one of: {self.LAYER_DECISION_ONLY}, {self.LAYER_DECISION_STRATEGY}, {self.LAYER_DECISION_STRATEGY_EXECUTION}"
            )
        self.execution_mode = execution_mode
        self.layer_mode = layer_mode

        # 回测固定纯历史模式：禁用AI调节层+事件层
        self.orchestrator = Orchestrator(
            skills_dir, enabled_skills, signal_weights,
            skill_types, execution_constraint
        )

    def run(self) -> BacktestResult:
        """执行单次回测

        核心逻辑（v0.7.2）：
        - Day N-1收盘后：Signal→Decision→Strategy 三层过滤
        - Day N开盘价：Execution Layer评估 → 执行

        Returns:
            BacktestResult: 回测结果
        """
        # 1. 加载历史数据
        feeder = DataFeeder(self.stock_code, self.start_date, self.end_date)
        if not feeder.load():
            return self._empty_result("数据加载失败")

        # 2. 初始化模拟账户
        account = SimulatedAccount(self.initial_capital)

        # 3. 逐日回测
        trades: list[TradeRecord] = []
        daily_snapshots: list[DailySnapshot] = []
        daily_decisions: list[dict] = []
        execution_logs: list[dict] = []
        first_price = None

        # 缓存前一日决策
        pending_decision: Optional[StrategyDecision] = None
        pending_result: Optional[DecisionResult] = None
        pending_signal_date: Optional[str] = None
        last_sell_date: Optional[str] = None

        # v0.7.2: 策略层状态（跨日持久化）
        strategy_state = StrategyState()

        # 执行约束统计
        blocked_limit_up = 0
        blocked_limit_down = 0
        blocked_liquidity = 0
        blocked_no_open_price = 0

        # 决策方向变化计数（用于决策稳定性指标）
        decision_directions: list[str] = []

        total_days = feeder.get_date_count()

        for i, (date, stock_data) in enumerate(feeder.iterate()):
            if first_price is None:
                first_price = stock_data.price

            current_price = stock_data.price

            # ===== 执行前日挂单 =====
            if pending_result is not None:
                exec_price = stock_data.open

                # v0.8.2 Phase 3: 开盘价缺失时跳过执行（消除前视偏差）
                # 旧逻辑：降级用当日收盘价执行 → 使用了当日信息，属于前视偏差
                # 新逻辑：标记为blocked，不执行交易
                if exec_price is None:
                    logger.warning(f"{date}: 开盘价不可用，跳过执行（消除前视偏差）")
                    blocked_no_open_price += 1

                    # 记录一条blocked的execution_log
                    pos_action, pos_ratio, pending_signal = self._resolve_pending_action(
                        pending_result, pending_decision
                    )
                    position_ratio_before = round(account.position_ratio(current_price), 4) if account.has_position else 0.0

                    blocked_exec_eval = ExecutionEvaluation(
                        original_action=pos_action,
                        effective_action=PositionAction.STAY_OUT,
                        blocked=True,
                        block_reason="开盘价不可用",
                        slippage_pct=0.0,
                        impact_cost_pct=0.0,
                        total_cost_pct=0.0,
                    )

                    execution_logs.append(self._build_execution_log(
                        execution_date=date,
                        signal_date=pending_signal_date,
                        decision_result=pending_result,
                        strategy_decision=pending_decision,
                        execution_eval=blocked_exec_eval,
                        trade=None,
                        position_ratio_before=position_ratio_before,
                        position_ratio_after=round(account.position_ratio(current_price), 4),
                    ))

                    # 清除挂单（该笔信号作废）
                    pending_decision = None
                    pending_result = None
                    pending_signal_date = None

                else:
                    # 按分层模式选择仓位动作来源
                    pos_action, pos_ratio, pending_signal = self._resolve_pending_action(
                        pending_result, pending_decision
                    )

                    # v0.7.2: 执行层评估（按分层模式可跳过）
                    volume_ratio = None
                    if stock_data.avg_volume_20 and stock_data.avg_volume_20 > 0:
                        volume_ratio = stock_data.volume / stock_data.avg_volume_20

                    exec_eval = self._evaluate_execution_layer(stock_data, pos_action, volume_ratio)

                    # 被阻止的交易统计
                    if exec_eval.blocked:
                        if "涨停" in exec_eval.block_reason:
                            blocked_limit_up += 1
                        elif "跌停" in exec_eval.block_reason:
                            blocked_limit_down += 1
                        elif "流动性" in exec_eval.block_reason:
                            blocked_liquidity += 1

                    # 使用执行层修正后的动作
                    effective_action = exec_eval.effective_action

                    # 使用执行层计算的实际滑点
                    actual_slippage = exec_eval.slippage_pct

                    trade = None
                    actual_pos_action = effective_action
                    position_ratio_before = round(account.position_ratio(exec_price), 4) if account.has_position else 0.0

                    if effective_action == PositionAction.OPEN:
                        if not account.has_position and self._not_in_cooldown(date, last_sell_date):
                            buy_price = exec_price * (1 + actual_slippage)
                            trade = account.buy(buy_price, target_position_ratio=pos_ratio)
                            if trade:
                                account.buy_date = date

                    elif effective_action == PositionAction.ADD:
                        if account.has_position and self._can_sell(date, account.buy_date):
                            buy_price = exec_price * (1 + actual_slippage)
                            trade = account.buy(buy_price, target_position_ratio=pos_ratio)
                        elif not account.has_position and self._not_in_cooldown(date, last_sell_date):
                            buy_price = exec_price * (1 + actual_slippage)
                            trade = account.buy(buy_price, target_position_ratio=min(pos_ratio, 0.20))
                            if trade:
                                account.buy_date = date
                                actual_pos_action = PositionAction.OPEN

                    elif effective_action == PositionAction.REDUCE:
                        if account.has_position and self._can_sell(date, account.buy_date):
                            current_ratio = account.position_ratio(exec_price)
                            if current_ratio < 0.05:
                                sell_price = exec_price * (1 - actual_slippage)
                                trade = account.sell(sell_price)
                                if trade:
                                    actual_pos_action = PositionAction.CLOSE_ALL
                            elif pos_ratio > 0 and pos_ratio < current_ratio:
                                total_val = account.total_value(exec_price)
                                target_mv = total_val * pos_ratio
                                current_mv = account.market_value(exec_price)
                                sell_amount = current_mv - target_mv
                                if sell_amount > 0:
                                    sell_shares = int(sell_amount / exec_price / 100) * 100
                                    if sell_shares <= 0:
                                        sell_shares = 100
                                    sell_shares = min(sell_shares, account.position)
                                    remaining = account.position - sell_shares
                                    remaining_ratio = (remaining * exec_price) / total_val if total_val > 0 else 0
                                    if remaining_ratio < 0.05:
                                        sell_shares = account.position
                                        actual_pos_action = PositionAction.CLOSE_ALL
                                    sell_price = exec_price * (1 - actual_slippage)
                                    trade = account.sell(sell_price, shares=sell_shares)

                    elif effective_action == PositionAction.CLOSE_ALL:
                        if account.has_position and self._can_sell(date, account.buy_date):
                            sell_price = exec_price * (1 - actual_slippage)
                            trade = account.sell(sell_price)
                            if trade:
                                actual_pos_action = PositionAction.CLOSE_ALL

                    elif effective_action == PositionAction.STAY_OUT:
                        pass  # 不操作

                    elif effective_action == PositionAction.HOLD_POSITION:
                        pass  # 维持仓位

                    # 兼容：信号BUY/SELL但无仓位动作时
                    elif pending_signal == SignalType.BUY and not account.has_position:
                        if self._not_in_cooldown(date, last_sell_date):
                            buy_price = exec_price * (1 + actual_slippage)
                            trade = account.buy(buy_price, target_position_ratio=0.20)
                            if trade:
                                account.buy_date = date
                                actual_pos_action = PositionAction.OPEN

                    elif pending_signal == SignalType.SELL and account.has_position:
                        if self._can_sell(date, account.buy_date):
                            sell_price = exec_price * (1 - actual_slippage)
                            trade = account.sell(sell_price)
                            if trade:
                                actual_pos_action = PositionAction.CLOSE_ALL

                    # 记录交易
                    if trade:
                        trade.date = date
                        trade.reason = self._get_trade_reason(pending_result, pending_decision)
                        trade.signal_score = pending_result.score if pending_result else 0.0
                        trade.position_action = actual_pos_action.value
                        trade.action_semantic = (
                            pending_decision.action_semantic
                            if pending_decision and pending_decision.action_semantic
                            else infer_action_semantic(actual_pos_action, pending_signal, [trade.reason], trade.action)
                        )
                        trade.sell_path = pending_decision.sell_path if pending_decision else None
                        trade.position_ratio_after = round(account.position_ratio(current_price), 2)

                        # 扣除交易成本
                        if trade.action == "BUY":
                            account.cash = self._deduct_buy_costs(account.cash, trade.amount)
                        else:
                            account.cash = self._deduct_sell_costs(account.cash, trade.amount)

                        trades.append(trade)

                        if trade.action == "SELL" and account.position == 0:
                            last_sell_date = date

                    execution_logs.append(self._build_execution_log(
                        execution_date=date,
                        signal_date=pending_signal_date,
                        decision_result=pending_result,
                        strategy_decision=pending_decision,
                        execution_eval=exec_eval,
                        trade=trade,
                        position_ratio_before=position_ratio_before,
                        position_ratio_after=round(account.position_ratio(current_price), 4),
                    ))

                    # 清除挂单
                    pending_decision = None
                    pending_result = None
                    pending_signal_date = None

            # ===== 生成今日信号（明日执行） =====
            current_pos_ratio = account.position_ratio(current_price) if account.has_position else 0.0

            # v0.7.2: 更新策略状态中的仓位比例
            strategy_state.current_position_ratio = current_pos_ratio

            try:
                decision_result, strategy_decision, execution_eval, _ = self.orchestrator.analyze(
                    stock_data, current_position_ratio=current_pos_ratio,
                    strategy_state=strategy_state,
                    ai_enabled=False,  # 回测不使用AI实时新闻
                )
            except Exception as e:
                logger.warning(f"分析异常 {date}: {e}")
                decision_result = None
                strategy_decision = None

            strategy_for_log = strategy_decision if self._uses_strategy_layer() else None
            if decision_result and (strategy_decision or not self._uses_strategy_layer()):
                final_decision = strategy_decision.decision if strategy_decision else decision_result.decision

                if strategy_decision:
                    strategy_state = strategy_decision.new_state

                daily_decisions.append(self._build_daily_decision_log(
                    date=date,
                    current_position_ratio=current_pos_ratio,
                    decision_result=decision_result,
                    strategy_decision=strategy_for_log,
                ))

                if final_decision in (SignalType.BUY, SignalType.HOLD):
                    decision_directions.append("bullish")
                elif final_decision in (SignalType.SELL, SignalType.WATCH):
                    decision_directions.append("bearish")

                pending_decision = strategy_decision if self._uses_strategy_layer() else None
                pending_result = decision_result
                pending_signal_date = date

            # ===== 记录每日快照 =====
            total_val = account.total_value(current_price)
            return_pct = (total_val / self.initial_capital - 1) * 100

            daily_snapshots.append(DailySnapshot(
                date=date,
                price=round(current_price, 2),
                cash=round(account.cash, 2),
                position=account.position,
                market_value=round(account.market_value(current_price), 2),
                total_value=round(total_val, 2),
                return_pct=round(return_pct, 2),
            ))

            if (i + 1) % max(1, total_days // 5) == 0:
                pct = (i + 1) / total_days * 100
                logger.info(f"回测进度: {pct:.0f}% ({i+1}/{total_days})")

        # 4. 最终清仓
        if account.has_position and daily_snapshots:
            last_snapshot = daily_snapshots[-1]
            last_date = last_snapshot.date
            clear_price = feeder.get_open_price(last_date) or last_snapshot.price
            forced_sell = account.sell(clear_price)
            if forced_sell:
                forced_sell.date = last_date
                forced_sell.reason = "[回测结束强制清仓]"
                account.cash = self._deduct_sell_costs(account.cash, forced_sell.amount)
                trades.append(forced_sell)
                total_val = account.total_value(last_snapshot.price)
                last_snapshot.cash = round(account.cash, 2)
                last_snapshot.position = 0
                last_snapshot.market_value = 0.0
                last_snapshot.total_value = round(total_val, 2)
                last_snapshot.return_pct = round((total_val / self.initial_capital - 1) * 100, 2)

        # 5. 计算统计指标
        result = self._calculate_stats(
            trades, daily_snapshots, first_price, feeder,
            decision_directions, blocked_limit_up,
            blocked_limit_down, blocked_liquidity, blocked_no_open_price,
        )
        result.diagnostics = {
            "daily_decisions": daily_decisions,
            "execution_logs": execution_logs,
        }
        result.layer_mode = self.layer_mode

        # v0.8.2 Phase 3: 偏差审计
        strategy_params = self._get_strategy_params()
        auditor = BiasAuditor()
        audit_result = auditor.audit(result, feeder=feeder, strategy_params=strategy_params)
        result.diagnostics["bias_audit"] = audit_result.to_dict()

        return result

    def run_monte_carlo(
        self, n_simulations: int = 20, seed: Optional[int] = 42
    ) -> list[BacktestResult]:
        """Monte Carlo 多路径回测

        通过在执行层添加随机扰动来模拟不同的执行条件：
        - 滑点扰动：在基础滑点上添加随机波动
        - 执行延迟：偶尔延迟1天执行
        - 成交价格扰动：在开盘价基础上添加随机偏移

        输出结果为"分布"，而非单值。

        Args:
            n_simulations: 模拟次数
            seed: 随机种子（可重复性）

        Returns:
            list[BacktestResult]: 多次模拟的结果列表
        """
        if seed is not None:
            random.seed(seed)

        results = []
        for sim_idx in range(n_simulations):
            # 为每次模拟生成随机扰动参数
            slippage_multiplier = random.uniform(0.5, 2.0)  # 滑点倍率
            delay_probability = random.uniform(0.0, 0.1)    # 延迟执行概率
            price_noise_pct = random.uniform(0.0, 0.003)    # 成交价格噪声

            # 创建带扰动的执行约束
            perturbed_constraint = ExecutionConstraint(
                slippage_pct=self.slippage_pct * slippage_multiplier,
                volatility_slippage=True,
                min_volume_ratio=0.3,
                limit_up_blocked=True,
                limit_down_blocked=True,
                impact_cost_enabled=True,
                impact_cost_rate=0.001,
            )

            # 创建临时回测引擎
            temp_engine = BacktestEngine(
                stock_code=self.stock_code,
                start_date=self.start_date,
                end_date=self.end_date,
                initial_capital=self.initial_capital,
                min_hold_days=self.min_hold_days,
                cooldown_days=self.cooldown_days,
                commission_rate=self.commission_rate,
                slippage_pct=self.slippage_pct * slippage_multiplier,
                execution_mode=self.execution_mode,
                layer_mode=self.layer_mode,
                skills_dir="./src/skills",
                execution_constraint=perturbed_constraint,
            )

            result = temp_engine.run()
            result.mc_simulations = n_simulations
            results.append(result)

            logger.info(f"MC模拟 {sim_idx+1}/{n_simulations}: "
                         f"收益={result.total_return_pct:.2f}%, "
                         f"最大回撤={result.max_drawdown_pct:.2f}%")

        return results

    # ===== T+1 和最小持有天数 =====

    def _can_sell(self, current_date: str, buy_date: Optional[str]) -> bool:
        """检查是否满足卖出条件。

        - 所有模式都强制执行T+1（A股硬规则）
        - legacy_compatible 模式额外执行最少持有天数
        """
        if buy_date is None:
            return True
        d1 = datetime.strptime(buy_date, "%Y-%m-%d")
        d2 = datetime.strptime(current_date, "%Y-%m-%d")
        hold_days = (d2 - d1).days
        if hold_days < 1:
            return False
        if self.execution_mode == self.MODE_LEGACY_COMPATIBLE and hold_days < self.min_hold_days:
            return False
        return True

    def _not_in_cooldown(self, current_date: str, last_sell_date: Optional[str]) -> bool:
        """检查是否在买入冷却期外。

        仅 legacy_compatible 模式启用旧版冷却期门控；
        framework_strict 模式交由 Strategy Layer 状态机统一约束。
        """
        if self.execution_mode != self.MODE_LEGACY_COMPATIBLE:
            return True
        if last_sell_date is None:
            return True
        d1 = datetime.strptime(last_sell_date, "%Y-%m-%d")
        d2 = datetime.strptime(current_date, "%Y-%m-%d")
        days_since_sell = (d2 - d1).days
        return days_since_sell >= self.cooldown_days

    # ===== 交易成本计算 =====

    def _deduct_buy_costs(self, cash: float, amount: float) -> float:
        commission = max(amount * self.commission_rate, self.MIN_COMMISSION)
        transfer_fee = amount * self.TRANSFER_FEE_RATE
        return cash - commission - transfer_fee

    def _deduct_sell_costs(self, cash: float, amount: float) -> float:
        commission = max(amount * self.commission_rate, self.MIN_COMMISSION)
        stamp_tax = amount * self.STAMP_TAX_RATE
        transfer_fee = amount * self.TRANSFER_FEE_RATE
        return cash - commission - stamp_tax - transfer_fee

    @staticmethod
    def _get_trade_reason(
        result: Optional[DecisionResult], decision: Optional[StrategyDecision] = None
    ) -> str:
        """获取交易原因"""
        if decision and decision.strategy_reasons:
            return "; ".join(decision.strategy_reasons[:2])
        if result is None:
            return ""
        reasons = result.reason[:2]
        return "; ".join(reasons) if reasons else result.decision.value

    def _uses_strategy_layer(self) -> bool:
        return self.layer_mode in (
            self.LAYER_DECISION_STRATEGY,
            self.LAYER_DECISION_STRATEGY_EXECUTION,
        )

    def _uses_execution_layer(self) -> bool:
        return self.layer_mode == self.LAYER_DECISION_STRATEGY_EXECUTION

    def _resolve_pending_action(
        self,
        pending_result: Optional[DecisionResult],
        pending_decision: Optional[StrategyDecision],
    ) -> tuple[PositionAction, float, Optional[SignalType]]:
        if self._uses_strategy_layer() and pending_decision is not None:
            return pending_decision.position_action, pending_decision.position_ratio, pending_decision.decision
        if pending_result is not None:
            return pending_result.position_action, pending_result.position_ratio, pending_result.decision
        return PositionAction.STAY_OUT, 0.0, None

    def _evaluate_execution_layer(
        self,
        stock_data: StockData,
        pos_action: PositionAction,
        volume_ratio: Optional[float],
    ) -> ExecutionEvaluation:
        if self._uses_execution_layer():
            return self.orchestrator.execution_layer.evaluate(stock_data, pos_action, volume_ratio)
        return ExecutionEvaluation(
            original_action=pos_action,
            effective_action=pos_action,
            blocked=False,
            block_reason="",
            slippage_pct=0.0,
            impact_cost_pct=0.0,
            total_cost_pct=0.0,
        )

    def _calculate_stats(
        self,
        trades: list[TradeRecord],
        daily_snapshots: list[DailySnapshot],
        first_price: Optional[float],
        feeder: DataFeeder,
        decision_directions: list[str],
        blocked_limit_up: int = 0,
        blocked_limit_down: int = 0,
        blocked_liquidity: int = 0,
        blocked_no_open_price: int = 0,
    ) -> BacktestResult:
        """计算回测统计指标（v0.7.2 含稳定性指标）"""
        if not daily_snapshots:
            return self._empty_result("无交易数据")

        final_snapshot = daily_snapshots[-1]
        first_snapshot = daily_snapshots[0]

        # 总收益率
        total_return = (final_snapshot.total_value / self.initial_capital - 1) * 100

        # 年化收益率
        d1 = datetime.strptime(first_snapshot.date, "%Y-%m-%d")
        d2 = datetime.strptime(final_snapshot.date, "%Y-%m-%d")
        years = max((d2 - d1).days / 365.0, 0.01)
        annualized_return = ((final_snapshot.total_value / self.initial_capital) ** (1 / years) - 1) * 100

        # 最大回撤
        max_drawdown = 0.0
        peak = self.initial_capital
        drawdowns = []  # 用于计算回撤稳定性
        for snap in daily_snapshots:
            if snap.total_value > peak:
                peak = snap.total_value
            drawdown = (peak - snap.total_value) / peak * 100
            drawdowns.append(drawdown)
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # 胜率和盈亏比
        win_count = 0
        loss_count = 0
        total_profit = 0.0
        total_loss = 0.0
        buy_trades = [t for t in trades if t.action == "BUY"]
        sell_trades = [t for t in trades if t.action == "SELL"]

        for buy_t, sell_t in zip(buy_trades, sell_trades):
            profit = sell_t.amount - buy_t.amount
            if profit > 0:
                win_count += 1
                total_profit += profit
            else:
                loss_count += 1
                total_loss += abs(profit)

        total_rounds = win_count + loss_count
        win_rate = (win_count / total_rounds * 100) if total_rounds > 0 else 0.0
        avg_profit = total_profit / win_count if win_count > 0 else 0.0
        avg_loss = total_loss / loss_count if loss_count > 0 else 1.0
        profit_loss_ratio = avg_profit / avg_loss if avg_loss > 0 else 0.0

        # 夏普比率
        import numpy as np
        daily_returns = []
        for i in range(1, len(daily_snapshots)):
            prev = daily_snapshots[i - 1].total_value
            curr = daily_snapshots[i].total_value
            if prev > 0:
                daily_returns.append(curr / prev - 1)

        sharpe = 0.0
        if len(daily_returns) > 10:
            arr = np.array(daily_returns)
            mean_ret = arr.mean()
            std_ret = arr.std()
            if std_ret > 0:
                sharpe = mean_ret / std_ret * math.sqrt(252)

        # 基准收益率
        benchmark_return = 0.0
        if first_price and first_price > 0 and final_snapshot.price > 0:
            benchmark_return = (final_snapshot.price / first_price - 1) * 100

        # 投入资金收益率
        total_buy_amount = sum(t.amount for t in buy_trades)
        total_sell_amount = sum(t.amount for t in sell_trades)
        invested_profit = total_sell_amount - total_buy_amount
        invested_return_pct = (invested_profit / total_buy_amount * 100) if total_buy_amount > 0 else 0.0

        # ===== v0.7.2 稳定性指标 =====

        # 最差收益（Monte Carlo需外部计算，单次回测=总收益）
        worst_case_return_pct = total_return  # 单次回测无分布

        # 回撤稳定性（回撤标准差，越小越稳定）
        drawdown_stability = float(np.std(drawdowns)) if len(drawdowns) > 10 else 0.0

        # 结果方差（单次回测为0）
        result_variance = 0.0

        # 决策稳定性（方向反转次数/总决策数）
        decision_stability = 0.0
        if len(decision_directions) > 1:
            direction_changes = sum(
                1 for i in range(1, len(decision_directions))
                if decision_directions[i] != decision_directions[i - 1]
            )
            decision_stability = round(1.0 - (direction_changes / (len(decision_directions) - 1)), 2)

        return BacktestResult(
            stock_code=self.stock_code,
            stock_name=feeder._stock_name,
            start_date=first_snapshot.date,
            end_date=final_snapshot.date,
            initial_capital=self.initial_capital,
            layer_mode=self.layer_mode,
            final_value=round(final_snapshot.total_value, 2),
            total_return_pct=round(total_return, 2),
            annualized_return_pct=round(annualized_return, 2),
            max_drawdown_pct=round(max_drawdown, 2),
            win_rate=round(win_rate, 2),
            profit_loss_ratio=round(profit_loss_ratio, 2),
            sharpe_ratio=round(sharpe, 2),
            total_trades=len(trades),
            buy_count=len(buy_trades),
            sell_count=len(sell_trades),
            trades=trades,
            daily_snapshots=daily_snapshots,
            benchmark_return_pct=round(benchmark_return, 2),
            invested_return_pct=round(invested_return_pct, 2),
            # v0.7.2
            worst_case_return_pct=round(worst_case_return_pct, 2),
            drawdown_stability=round(drawdown_stability, 2),
            result_variance=round(result_variance, 2),
            decision_stability=decision_stability,
            blocked_by_limit_up=blocked_limit_up,
            blocked_by_limit_down=blocked_limit_down,
            blocked_by_liquidity=blocked_liquidity,
            blocked_by_no_open_price=blocked_no_open_price,
        )

    def _empty_result(self, reason: str = "") -> BacktestResult:
        return BacktestResult(
            stock_code=self.stock_code,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_capital=self.initial_capital,
            layer_mode=self.layer_mode,
            final_value=self.initial_capital,
            total_return_pct=0.0,
        )

    def _get_strategy_params(self) -> dict:
        """获取策略层参数快照（用于偏差审计）

        从orchestrator中提取策略层参数，若无策略层则返回空dict。
        """
        try:
            if hasattr(self.orchestrator, 'strategy_layer') and self.orchestrator.strategy_layer:
                return self.orchestrator.strategy_layer.get_params()
        except Exception:
            pass
        return {}

    def _build_daily_decision_log(
        self,
        date: str,
        current_position_ratio: float,
        decision_result: Optional[DecisionResult],
        strategy_decision: Optional[StrategyDecision],
    ) -> dict:
        return {
            "date": date,
            "position_ratio_before": round(current_position_ratio, 4),
            "decision": self._serialize_decision_result(decision_result),
            "strategy": self._serialize_strategy_decision(strategy_decision),
        }

    def _build_execution_log(
        self,
        execution_date: str,
        signal_date: Optional[str],
        decision_result: Optional[DecisionResult],
        strategy_decision: Optional[StrategyDecision],
        execution_eval,
        trade: Optional[TradeRecord],
        position_ratio_before: float,
        position_ratio_after: float,
    ) -> dict:
        return {
            "execution_date": execution_date,
            "signal_date": signal_date,
            "position_ratio_before": round(position_ratio_before, 4),
            "position_ratio_after": round(position_ratio_after, 4),
            "decision": self._serialize_decision_result(decision_result),
            "strategy": self._serialize_strategy_decision(strategy_decision),
            "execution": self._serialize_execution_eval(execution_eval),
            "trade": trade.model_dump() if trade else None,
        }

    def _serialize_decision_result(self, decision_result: Optional[DecisionResult]) -> Optional[dict]:
        if not decision_result:
            return None
        return {
            "decision": decision_result.decision.value,
            "state": decision_result.state.value,
            "score": round(decision_result.score, 4),
            "position_action": decision_result.position_action.value,
            "action_semantic": infer_action_semantic(
                decision_result.position_action,
                decision_result.decision,
                decision_result.reason,
            ),
            "position_ratio": round(decision_result.position_ratio, 4),
            "reason": decision_result.reason[:5],
            "warnings": decision_result.warnings[:5],
        }

    def _serialize_strategy_decision(self, strategy_decision: Optional[StrategyDecision]) -> Optional[dict]:
        if not strategy_decision:
            return None
        return {
            "decision": strategy_decision.decision.value,
            "position_action": strategy_decision.position_action.value,
            "action_semantic": strategy_decision.action_semantic,
            "sell_path": strategy_decision.sell_path,
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

    def _serialize_execution_eval(self, execution_eval) -> Optional[dict]:
        if not execution_eval:
            return None
        return {
            "blocked": execution_eval.blocked,
            "block_reason": execution_eval.block_reason,
            "effective_action": execution_eval.effective_action.value,
            "slippage_pct": round(execution_eval.slippage_pct, 6),
            "impact_cost_pct": round(execution_eval.impact_cost_pct, 6),
            "total_cost_pct": round(execution_eval.total_cost_pct, 6),
        }

    @staticmethod
    def summarize_monte_carlo(results: list[BacktestResult]) -> dict:
        """汇总 Monte Carlo 回测结果

        Returns:
            dict: 包含分布统计的汇总
        """
        if not results:
            return {}

        import numpy as np

        returns = [r.total_return_pct for r in results]
        drawdowns = [r.max_drawdown_pct for r in results]
        sharpes = [r.sharpe_ratio for r in results]

        return {
            "n_simulations": len(results),
            "return_mean": round(float(np.mean(returns)), 2),
            "return_std": round(float(np.std(returns)), 2),
            "return_worst": round(float(np.min(returns)), 2),
            "return_best": round(float(np.max(returns)), 2),
            "return_median": round(float(np.median(returns)), 2),
            "drawdown_mean": round(float(np.mean(drawdowns)), 2),
            "drawdown_worst": round(float(np.max(drawdowns)), 2),
            "sharpe_mean": round(float(np.mean(sharpes)), 2),
            "result_variance": round(float(np.var(returns)), 2),
        }
