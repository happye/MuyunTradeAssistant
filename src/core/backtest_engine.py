"""回测引擎 - 历史策略回测

模拟账户：初始资金 → 逐日分析 → 买入/卖出 → 记录交易和收益曲线

交易规则（v0.4.0 修正）：
  - 信号生成：Day N-1收盘后分析，生成信号
  - 执行时机：Day N开盘价执行（消除前视偏差）
  - T+1硬限制：买入后至少下一交易日才能卖出（A股基本规则）
  - 策略级最小持有天数：min_hold_days（可配置，默认3天，含T+1）
  - 佣金：券商佣金(0.025%，最低5元) + 印花税(卖出0.05%) + 过户费(0.001%)
  - 滑点：买入上浮/卖出下浮指定百分比
"""

import logging
import math
from typing import Optional
from datetime import datetime

from src.data.models import (
    StockData, BacktestResult, TradeRecord, DailySnapshot,
    SignalType, DecisionResult, PositionAction
)
from src.data.data_feeder import DataFeeder
from src.core.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class SimulatedAccount:
    """模拟交易账户（支持仓位管理）"""

    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.position = 0        # 持仓股数
        self.avg_cost = 0.0      # 持仓成本价
        self.buy_date = None     # 买入日期（用于T+1和min_hold_days）
        self.total_buy = 0       # 买入次数
        self.total_sell = 0      # 卖出次数

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
        """买入

        Args:
            price: 买入价格
            max_ratio: 最大使用资金比例（仅在不指定target_position_ratio时使用）
            target_position_ratio: 目标仓位比例（如0.2=20%），None则使用max_ratio满仓模式

        Returns:
            TradeRecord 或 None（资金不足）
        """
        # 计算可用资金
        if target_position_ratio is not None and target_position_ratio > 0:
            # 仓位管理模式：计算目标市值，减去当前持仓市值，得到需要买入的金额
            total_value = self.total_value(price)
            target_market_value = total_value * target_position_ratio
            current_market_value = self.market_value(price)
            needed = target_market_value - current_market_value
            if needed <= 0:
                return None  # 已经达到或超过目标仓位
            # 实际可用资金 = min(需要买入的金额, 现金*0.95)
            available = min(needed, self.cash * 0.95)
        else:
            # 满仓模式（兼容旧逻辑）
            available = self.cash * max_ratio

        if available < price * 100:
            return None  # 资金不足

        shares = int(available / price / 100) * 100  # 向下取整到100的倍数
        if shares <= 0:
            return None

        amount = shares * price

        # 更新持仓成本
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
        """卖出

        Args:
            price: 卖出价格
            shares: 卖出股数，None=全部卖出
            reduce_ratio: 减仓比例（如0.5=卖出当前持仓的一半）

        Returns:
            TradeRecord 或 None（无持仓）
        """
        if self.position <= 0:
            return None

        if reduce_ratio is not None:
            # 按比例减仓
            sell_shares = int(self.position * reduce_ratio / 100) * 100  # 100的整数倍
            if sell_shares <= 0:
                sell_shares = self.position  # 余量不足100股，全部卖出
            sell_shares = min(sell_shares, self.position)
        elif shares:
            sell_shares = min(shares, self.position)
        else:
            sell_shares = self.position

        # A股卖出不必是100的整数倍
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
        """持仓市值"""
        return self.position * price

    def total_value(self, price: float) -> float:
        """总资产"""
        return self.cash + self.market_value(price)


class BacktestEngine:
    """回测引擎

    用法:
        engine = BacktestEngine(
            stock_code="600519",
            start_date="2024-01-01",
            end_date="2025-01-01",
            initial_capital=100000,
        )
        result = engine.run()
    """

    # ===== A股交易成本常量 =====
    DEFAULT_COMMISSION_RATE = 0.00025   # 券商佣金费率 0.025%
    MIN_COMMISSION = 5.0                # 最低佣金 5元
    STAMP_TAX_RATE = 0.0005            # 印花税（卖出）0.05%（2023年减半后）
    TRANSFER_FEE_RATE = 0.00001        # 过户费 0.001%

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
        skills_dir: str = "./src/skills",
        enabled_skills: Optional[list[str]] = None,
        signal_weights: Optional[dict[str, float]] = None,
        skill_types: Optional[dict[str, str]] = None,
    ):
        """
        Args:
            stock_code: 股票代码
            start_date: 回测起始 YYYY-MM-DD
            end_date: 回测结束 YYYY-MM-DD
            initial_capital: 初始资金
            min_hold_days: 最少持有天数（含T+1，默认3天）
            cooldown_days: 卖出后冷却天数（此期间不再买入，默认5天）
            commission_rate: 券商佣金费率（单边，默认0.025%）
            slippage_pct: 滑点百分比
            skills_dir: 技能目录
            enabled_skills: 启用的技能
            signal_weights: 信号权重
            skill_types: 技能类型
        """
        self.stock_code = stock_code
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.min_hold_days = max(min_hold_days, 1)  # 至少1天（T+1）
        self.cooldown_days = cooldown_days
        self.commission_rate = commission_rate
        self.slippage_pct = slippage_pct

        # 初始化编排器
        self.orchestrator = Orchestrator(skills_dir, enabled_skills, signal_weights, skill_types)

    def run(self) -> BacktestResult:
        """执行回测

        核心逻辑：前日信号 + 次日开盘价执行
        - Day N-1收盘后分析 → 生成信号
        - Day N开盘价执行 → 消除前视偏差

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
        first_price = None

        # 缓存前一日信号（核心：前日信号+次日开盘价执行）
        pending_signal: Optional[SignalType] = None
        pending_result: Optional[DecisionResult] = None
        last_sell_date: Optional[str] = None  # 冷却期：卖出后N天不再买入
        last_reduce_info: Optional[dict] = None  # 减仓保护期：{date, reason, cooldown_days}

        total_days = feeder.get_date_count()

        for i, (date, stock_data) in enumerate(feeder.iterate()):
            if first_price is None:
                first_price = stock_data.price

            current_price = stock_data.price

            # ===== 执行前日挂单（次日开盘价执行 + 仓位管理） =====
            if pending_signal is not None:
                exec_price = stock_data.open  # 使用当日开盘价执行
                if exec_price is None:
                    # 开盘价不可用时，降级为前一日收盘价
                    logger.warning(f"{date}: 开盘价不可用，降级使用前日收盘价")
                    exec_price = current_price

                # 获取仓位动作（如果有）
                pos_action = PositionAction.STAY_OUT
                pos_ratio = 0.0
                if pending_result:
                    pos_action = pending_result.position_action
                    pos_ratio = pending_result.position_ratio

                # 根据仓位动作执行交易
                trade = None
                actual_pos_action = pos_action  # 实际执行的仓位动作（可能被调整）

                if pos_action == PositionAction.OPEN:
                    # 试探建仓：首次买入，仓位比例由策略决定
                    if not account.has_position and self._not_in_cooldown(date, last_sell_date):
                        buy_price = self._apply_slippage(exec_price, "buy")
                        trade = account.buy(buy_price, target_position_ratio=pos_ratio)
                        if trade:
                            account.buy_date = date
                            actual_pos_action = PositionAction.OPEN

                elif pos_action == PositionAction.ADD:
                    # 加仓：已有持仓，追加买入
                    if account.has_position and self._can_sell(date, account.buy_date):
                        # 加仓：T+1后才能加仓（避免当天买当天加）
                        buy_price = self._apply_slippage(exec_price, "buy")
                        trade = account.buy(buy_price, target_position_ratio=pos_ratio)
                        if trade:
                            actual_pos_action = PositionAction.ADD
                        # 加仓不更新buy_date（保持原始买入日期用于min_hold_days）

                    elif not account.has_position and self._not_in_cooldown(date, last_sell_date):
                        # 没有持仓时，ADD退化为OPEN
                        buy_price = self._apply_slippage(exec_price, "buy")
                        trade = account.buy(buy_price, target_position_ratio=min(pos_ratio, 0.20))
                        if trade:
                            account.buy_date = date
                            actual_pos_action = PositionAction.OPEN  # 实际是建仓

                elif pos_action == PositionAction.REDUCE:
                    # 减仓：卖出部分持仓（分批止盈等）
                    if account.has_position and self._can_sell(date, account.buy_date):
                        # 减仓保护期：同一原因在保护期内不重复减仓（ISS-016方案B）
                        reduce_reason = self._get_reduce_reason(pending_result)
                        if not self._can_reduce(date, last_reduce_info, reduce_reason):
                            # 保护期内不执行减仓
                            pass
                        else:
                            current_ratio = account.position_ratio(exec_price)
                            # 如果仓位已经低于5%，直接清仓（仓位太小，减仓无意义）
                            if current_ratio < 0.05:
                                sell_price = self._apply_slippage(exec_price, "sell")
                                trade = account.sell(sell_price)
                                if trade:
                                    actual_pos_action = PositionAction.CLOSE_ALL
                            elif pos_ratio > 0 and pos_ratio < current_ratio:
                                # 有明确目标仓位：直接按目标仓位减仓
                                total_val = account.total_value(exec_price)
                                target_mv = total_val * pos_ratio
                                current_mv = account.market_value(exec_price)
                                sell_amount = current_mv - target_mv
                                if sell_amount > 0:
                                    sell_shares = int(sell_amount / exec_price / 100) * 100
                                    if sell_shares <= 0:
                                        sell_shares = 100  # 至少卖100股
                                    sell_shares = min(sell_shares, account.position)
                                    # 如果减仓后仓位会低于5%，直接清仓
                                    remaining = account.position - sell_shares
                                    remaining_ratio = (remaining * exec_price) / total_val if total_val > 0 else 0
                                    if remaining_ratio < 0.05:
                                        sell_shares = account.position
                                        actual_pos_action = PositionAction.CLOSE_ALL
                                    sell_price = self._apply_slippage(exec_price, "sell")
                                    trade = account.sell(sell_price, shares=sell_shares)
                                    if trade and remaining_ratio >= 0.10:
                                        actual_pos_action = PositionAction.REDUCE
                                else:
                                    # 目标仓位≥当前仓位，不需要减仓
                                    pass
                            else:
                                # 无明确目标仓位：减仓到当前仓位的60%
                                target_ratio = current_ratio * 0.6
                                total_val = account.total_value(exec_price)
                                target_mv = total_val * target_ratio
                                current_mv = account.market_value(exec_price)
                                sell_amount = current_mv - target_mv
                                if sell_amount > 0:
                                    sell_shares = int(sell_amount / exec_price / 100) * 100
                                    if sell_shares <= 0:
                                        sell_shares = 100
                                    sell_shares = min(sell_shares, account.position)
                                    # 如果减仓后仓位会低于5%，直接清仓
                                    remaining = account.position - sell_shares
                                    remaining_ratio = (remaining * exec_price) / total_val if total_val > 0 else 0
                                    if remaining_ratio < 0.05:
                                        sell_shares = account.position
                                    sell_price = self._apply_slippage(exec_price, "sell")
                                    trade = account.sell(sell_price, shares=sell_shares)
                                    if trade:
                                        if remaining_ratio < 0.05:
                                            actual_pos_action = PositionAction.CLOSE_ALL
                                        else:
                                            actual_pos_action = PositionAction.REDUCE

                elif pos_action == PositionAction.CLOSE_ALL:
                    # 全部清仓（止损/趋势反转）
                    if account.has_position and self._can_sell(date, account.buy_date):
                        sell_price = self._apply_slippage(exec_price, "sell")
                        trade = account.sell(sell_price)
                        if trade:
                            actual_pos_action = PositionAction.CLOSE_ALL

                elif pending_signal == SignalType.BUY and not account.has_position:
                    # 兼容：信号BUY但无仓位动作时，默认试探建仓
                    if self._not_in_cooldown(date, last_sell_date):
                        buy_price = self._apply_slippage(exec_price, "buy")
                        trade = account.buy(buy_price, target_position_ratio=0.20)
                        if trade:
                            account.buy_date = date
                            actual_pos_action = PositionAction.OPEN

                elif pending_signal == SignalType.SELL and account.has_position:
                    # 兼容：信号SELL但无仓位动作时，默认全部清仓
                    if self._can_sell(date, account.buy_date):
                        sell_price = self._apply_slippage(exec_price, "sell")
                        trade = account.sell(sell_price)
                        if trade:
                            actual_pos_action = PositionAction.CLOSE_ALL

                # 记录交易
                if trade:
                    trade.date = date
                    trade.reason = self._get_trade_reason(pending_result, pending_signal)
                    trade.signal_score = pending_result.score if pending_result else 0.0
                    trade.position_action = actual_pos_action.value
                    trade.position_ratio_after = round(account.position_ratio(current_price), 2)

                    # 扣除交易成本
                    if trade.action == "BUY":
                        account.cash = self._deduct_buy_costs(account.cash, trade.amount)
                    else:
                        account.cash = self._deduct_sell_costs(account.cash, trade.amount)

                    trades.append(trade)

                    # 记录卖出日期（用于冷却期）—— 仅清仓时触发买入冷却期
                    if trade.action == "SELL" and account.position == 0:
                        last_sell_date = date
                    # 记录减仓信息（用于动态保护期）
                    if trade.action == "SELL" and account.position > 0:
                        reduce_reason = self._get_reduce_reason(pending_result)
                        last_reduce_info = {
                            "date": date,
                            "reason": reduce_reason,
                            "cooldown_days": self._get_reduce_cooldown(reduce_reason),
                        }

                    action_desc = pos_action.value if pos_action.value else trade.action
                    logger.debug(f"{date} {action_desc}: 开盘价={exec_price:.2f}, 成交价={trade.price:.2f}, "
                                 f"股数={trade.shares}, 仓位={trade.position_ratio_after:.0%}")

                # 清除挂单
                pending_signal = None
                pending_result = None

            # ===== 生成今日信号（明日执行） =====
            # 传入当前仓位比例，让决策引擎计算合理的减仓目标
            current_pos_ratio = account.position_ratio(current_price) if account.has_position else 0.0
            try:
                result = self.orchestrator.analyze(stock_data, current_position_ratio=current_pos_ratio)
            except Exception as e:
                logger.warning(f"分析异常 {date}: {e}")
                result = None

            decision = result.decision if result else SignalType.WATCH

            # 持仓上下文过滤：无持仓时止损/止盈SELL覆盖不应生效
            position_action_override = None
            position_ratio_override = None
            if not account.has_position and result:
                action_signals = [s for s in result.signals if s.skill_type == "action"]
                is_action_override = any(
                    s.signal == SignalType.SELL and s.confidence >= 0.7
                    for s in action_signals
                )
                if is_action_override and decision == SignalType.SELL:
                    base_signals = [s for s in result.signals if s.skill_type == "base"]
                    if base_signals:
                        buy_score = sum(s.confidence for s in base_signals if s.signal == SignalType.BUY)
                        sell_score = sum(s.confidence for s in base_signals if s.signal == SignalType.SELL)
                        if buy_score > sell_score:
                            decision = SignalType.BUY
                            position_action_override = PositionAction.OPEN
                            position_ratio_override = 0.20
                        else:
                            decision = SignalType.WATCH
                            position_action_override = PositionAction.STAY_OUT
                    else:
                        decision = SignalType.WATCH
                        position_action_override = PositionAction.STAY_OUT

            # 缓存信号，等下一个交易日开盘执行
            pending_signal = decision
            pending_result = result
            # 如果持仓上下文过滤覆盖了决策，同步更新仓位动作
            if position_action_override is not None and result:
                # 创建新的DecisionResult，替换position_action和position_ratio
                result_copy = result.model_copy(update={
                    "position_action": position_action_override,
                    "position_ratio": position_ratio_override or 0.0,
                })
                pending_result = result_copy

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

            # 进度（每20%打印一次）
            if (i + 1) % max(1, total_days // 5) == 0:
                pct = (i + 1) / total_days * 100
                logger.info(f"回测进度: {pct:.0f}% ({i+1}/{total_days})")

        # 4. 最终清仓（回测结束时如果还持仓，按最后一天开盘价清仓）
        if account.has_position and daily_snapshots:
            last_snapshot = daily_snapshots[-1]
            # 使用最后一天的开盘价清仓（如果有）
            last_date = last_snapshot.date
            clear_price = feeder.get_open_price(last_date) or last_snapshot.price
            forced_sell = account.sell(clear_price)
            if forced_sell:
                forced_sell.date = last_date
                forced_sell.reason = "[回测结束强制清仓]"
                account.cash = self._deduct_sell_costs(account.cash, forced_sell.amount)
                trades.append(forced_sell)
                # 更新最后一天的快照
                total_val = account.total_value(last_snapshot.price)
                last_snapshot.cash = round(account.cash, 2)
                last_snapshot.position = 0
                last_snapshot.market_value = 0.0
                last_snapshot.total_value = round(total_val, 2)
                last_snapshot.return_pct = round((total_val / self.initial_capital - 1) * 100, 2)

        # 5. 计算统计指标
        return self._calculate_stats(
            trades, daily_snapshots, first_price, feeder
        )

    # ===== T+1 和最小持有天数 =====

    def _can_sell(self, current_date: str, buy_date: Optional[str]) -> bool:
        """检查是否满足卖出条件（T+1硬限制 + 策略级min_hold_days）

        T+1规则：买入当日不能卖出（买入日 < 卖出日）
        min_hold_days：策略级最小持有天数（含T+1，默认3天）
        """
        if buy_date is None:
            return True

        d1 = datetime.strptime(buy_date, "%Y-%m-%d")
        d2 = datetime.strptime(current_date, "%Y-%m-%d")
        hold_days = (d2 - d1).days

        # T+1硬限制：买入日必须严格早于卖出日
        if hold_days < 1:
            logger.warning(f"T+1违规: 买入{buy_date}，卖出{current_date}，持有天数{hold_days}")
            return False

        # 策略级最小持有天数
        if hold_days < self.min_hold_days:
            return False

        return True

    def _not_in_cooldown(self, current_date: str, last_sell_date: Optional[str]) -> bool:
        """检查是否在买入冷却期外（卖出后N天内不再买入同一只股票）

        冷却期目的：防止卖出后马上又买回来（频繁交易）
        """
        if last_sell_date is None:
            return True

        d1 = datetime.strptime(last_sell_date, "%Y-%m-%d")
        d2 = datetime.strptime(current_date, "%Y-%m-%d")
        days_since_sell = (d2 - d1).days

        if days_since_sell < self.cooldown_days:
            return False

        return True

    # ===== 减仓保护期（ISS-016方案B：同一原因+动态时长） =====

    # 减仓原因的保护期天数
    REDUCE_COOLDOWN_MAP = {
        "deep_stop_loss": 0,    # 深度止损：直接清仓，无保护期
        "shallow_stop_loss": 15, # 浅层止损：15天（短期波动已反应，给市场恢复时间）
        "take_profit": 10,       # 止盈：10天（已获利了结，等下一波）
        "weak_sell": 15,         # 弱卖出(sell<0.4)：15天（弱信号不急）
        "strong_sell": 5,        # 强卖出(sell>=0.4)：5天（强信号可信度高）
        "unknown": 10,           # 未知原因：10天（默认）
    }

    def _get_reduce_reason(self, result: Optional[DecisionResult]) -> str:
        """从决策结果中提取减仓原因"""
        if result is None:
            return "unknown"

        # 检查决策理由中的关键词
        reasons_str = " ".join(result.reason) if result.reason else ""

        if "止损信号覆盖" in reasons_str or "止损风险提示" in reasons_str:
            # 区分深度/浅层止损
            if result.position_action == PositionAction.CLOSE_ALL:
                return "deep_stop_loss"
            return "shallow_stop_loss"
        elif "止盈" in reasons_str:
            return "take_profit"
        else:
            # 根据卖出信号强度判断
            sell_score = result.score if result.decision == SignalType.SELL else 0
            if sell_score >= 0.4:
                return "strong_sell"
            return "weak_sell"

    def _get_reduce_cooldown(self, reason: str) -> int:
        """根据减仓原因获取保护期天数"""
        return self.REDUCE_COOLDOWN_MAP.get(reason, 10)

    def _can_reduce(self, current_date: str, last_reduce_info: Optional[dict], current_reason: str) -> bool:
        """检查是否可以执行减仓（动态保护期）

        规则：
        1. 同一原因在保护期内不重复减仓
        2. 不同原因可以减仓，但受最短保护期（5天）限制
        3. 无减仓记录时可以减仓
        """
        if last_reduce_info is None:
            return True

        d1 = datetime.strptime(last_reduce_info["date"], "%Y-%m-%d")
        d2 = datetime.strptime(current_date, "%Y-%m-%d")
        days_since_reduce = (d2 - d1).days

        last_reason = last_reduce_info.get("reason", "unknown")
        last_cooldown = last_reduce_info.get("cooldown_days", 10)

        if last_reason == current_reason:
            # 同一原因：必须等满保护期
            if days_since_reduce < last_cooldown:
                return False
        else:
            # 不同原因：至少等5天（避免频繁操作）
            if days_since_reduce < 5:
                return False

        return True

    # ===== 交易成本计算 =====

    def _deduct_buy_costs(self, cash: float, amount: float) -> float:
        """扣除买入交易成本

        买入成本 = 券商佣金(0.025%, 最低5元) + 过户费(0.001%)
        """
        # 券商佣金
        commission = max(amount * self.commission_rate, self.MIN_COMMISSION)
        # 过户费
        transfer_fee = amount * self.TRANSFER_FEE_RATE
        return cash - commission - transfer_fee

    def _deduct_sell_costs(self, cash: float, amount: float) -> float:
        """扣除卖出交易成本

        卖出成本 = 券商佣金(0.025%, 最低5元) + 印花税(0.05%) + 过户费(0.001%)
        """
        # 券商佣金
        commission = max(amount * self.commission_rate, self.MIN_COMMISSION)
        # 印花税（卖出单向收取）
        stamp_tax = amount * self.STAMP_TAX_RATE
        # 过户费
        transfer_fee = amount * self.TRANSFER_FEE_RATE
        return cash - commission - stamp_tax - transfer_fee

    # ===== 其他辅助方法 =====

    def _apply_slippage(self, price: float, direction: str) -> float:
        """应用滑点

        买入时价格上浮，卖出时价格下浮
        """
        if direction == "buy":
            return price * (1 + self.slippage_pct)
        else:
            return price * (1 - self.slippage_pct)

    @staticmethod
    def _get_trade_reason(result: Optional[DecisionResult], decision: SignalType = None) -> str:
        """获取交易原因"""
        if result is None:
            return decision.value if decision else ""
        reasons = result.reason[:2]
        return "; ".join(reasons) if reasons else result.decision.value

    def _calculate_stats(
        self,
        trades: list[TradeRecord],
        daily_snapshots: list[DailySnapshot],
        first_price: Optional[float],
        feeder: DataFeeder,
    ) -> BacktestResult:
        """计算回测统计指标"""
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
        for snap in daily_snapshots:
            if snap.total_value > peak:
                peak = snap.total_value
            drawdown = (peak - snap.total_value) / peak * 100
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

        # 夏普比率（日收益率标准差）
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
                sharpe = mean_ret / std_ret * math.sqrt(252)  # 年化

        # 基准收益率（买入持有）
        benchmark_return = 0.0
        if first_price and first_price > 0 and final_snapshot.price > 0:
            benchmark_return = (final_snapshot.price / first_price - 1) * 100

        # 投入资金收益率（盈利/实际投入成本，反映策略选股能力）
        total_buy_amount = sum(t.amount for t in buy_trades)
        total_sell_amount = sum(t.amount for t in sell_trades)
        invested_profit = total_sell_amount - total_buy_amount
        invested_return_pct = (invested_profit / total_buy_amount * 100) if total_buy_amount > 0 else 0.0

        return BacktestResult(
            stock_code=self.stock_code,
            stock_name=feeder._stock_name,
            start_date=first_snapshot.date,
            end_date=final_snapshot.date,
            initial_capital=self.initial_capital,
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
        )

    def _empty_result(self, reason: str = "") -> BacktestResult:
        """空结果（数据加载失败等）"""
        return BacktestResult(
            stock_code=self.stock_code,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_capital=self.initial_capital,
            final_value=self.initial_capital,
            total_return_pct=0.0,
        )
