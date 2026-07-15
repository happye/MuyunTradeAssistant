"""持仓记录管理器 - 方案B（自动+手动混合）

功能：
1. 读取 portfolio.yaml → 初始化 StrategyState
2. 分析后建议更新 → Y/N 交互确认
3. 支持手动添加/删除/列出持仓

设计原则：
- 持仓数据必须真实，系统只建议不强制
- YAML格式透明可编辑，用户随时可手动修改
- 系统自动维护strategy_state，用户一般不需碰
"""

import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import yaml

from src.data.models import (
    StrategyState, TradeLifecycle, SignalType, TradePlan
)

logger = logging.getLogger(__name__)

# 默认持仓文件路径（项目根目录）
DEFAULT_PORTFOLIO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "portfolio.yaml"
)


class PositionRecord:
    """单只股票的持仓记录"""

    def __init__(
        self,
        stock_code: str,
        stock_name: str = "",
        entry_date: Optional[str] = None,
        entry_price: Optional[float] = None,
        current_ratio: float = 0.0,
        last_action: str = "OPEN",
        last_action_semantic: Optional[str] = None,
        last_sell_path: Optional[str] = None,
        last_action_date: Optional[str] = None,
        high_since_entry: Optional[float] = None,
        lifecycle: str = "FLAT",
        strategy_state: Optional[dict] = None,
        trade_plan: Optional[TradePlan] = None,
    ):
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.current_ratio = current_ratio
        self.last_action = last_action
        self.last_action_semantic = last_action_semantic
        self.last_sell_path = last_sell_path
        self.last_action_date = last_action_date
        self.high_since_entry = high_since_entry
        self.lifecycle = lifecycle
        self.strategy_state = strategy_state or {}
        self.trade_plan = trade_plan  # v0.8.5 TradePlan 子系统

    def to_dict(self) -> dict:
        """转为YAML可序列化的字典"""
        d = {
            "stock_name": self.stock_name,
            "entry_date": self.entry_date,
            "entry_price": self.entry_price,
            "current_ratio": self.current_ratio,
            "last_action": self.last_action,
            "last_action_semantic": self.last_action_semantic,
            "last_sell_path": self.last_sell_path,
            "last_action_date": self.last_action_date,
            "high_since_entry": self.high_since_entry,
            "lifecycle": self.lifecycle,
            "strategy_state": self.strategy_state,
        }
        if self.trade_plan is not None:
            # Pydantic v2: model_dump() 转 dict；YAML 兼容
            d["trade_plan"] = self.trade_plan.model_dump()
        return d

    @classmethod
    def from_dict(cls, stock_code: str, data: dict) -> "PositionRecord":
        """从YAML字典创建"""
        plan_data = data.get("trade_plan")
        trade_plan = TradePlan(**plan_data) if plan_data else None
        return cls(
            stock_code=stock_code,
            stock_name=data.get("stock_name", ""),
            entry_date=data.get("entry_date"),
            entry_price=data.get("entry_price"),
            current_ratio=data.get("current_ratio", 0.0),
            last_action=data.get("last_action", "OPEN"),
            last_action_semantic=data.get("last_action_semantic"),
            last_sell_path=data.get("last_sell_path"),
            last_action_date=data.get("last_action_date"),
            high_since_entry=data.get("high_since_entry"),
            lifecycle=data.get("lifecycle", "FLAT"),
            strategy_state=data.get("strategy_state", {}),
            trade_plan=trade_plan,
        )


class PortfolioManager:
    """持仓记录管理器

    用法：
        pm = PortfolioManager()
        # 读取持仓
        pos = pm.get_position("002192")
        # 转为StrategyState
        state = pm.to_strategy_state("002192")
        # 分析后更新
        pm.suggest_update("002192", strategy_decision, stock_data)
        # 手动操作
        pm.add_position("002192", stock_name="融捷股份", entry_price=35.20, ratio=0.20)
        pm.list_positions()
        pm.remove_position("002192")
    """

    def __init__(self, portfolio_path: Optional[str] = None):
        self.portfolio_path = portfolio_path or DEFAULT_PORTFOLIO_PATH
        self._data: dict = {}
        self._load()

    DEFAULT_MIN_HOLD_DAYS = 5

    def _load(self):
        """加载portfolio.yaml"""
        if not os.path.exists(self.portfolio_path):
            logger.info(f"持仓文件不存在，将创建: {self.portfolio_path}")
            self._data = {"positions": {}}
            return

        try:
            with open(self.portfolio_path, 'r', encoding='utf-8') as f:
                self._data = yaml.safe_load(f) or {"positions": {}}
            logger.info(f"持仓文件已加载: {self.portfolio_path}")
        except Exception as e:
            logger.error(f"持仓文件加载失败: {e}")
            self._data = {"positions": {}}

    def _save(self):
        """保存到portfolio.yaml"""
        # 确保目录存在
        os.makedirs(os.path.dirname(self.portfolio_path) or ".", exist_ok=True)

        try:
            with open(self.portfolio_path, 'w', encoding='utf-8') as f:
                # 先写入文件头注释
                f.write("# ============================================================\n")
                f.write("# 暮云思辨投资助手 - 持仓记录本\n")
                f.write("# ============================================================\n")
                f.write("#\n")
                f.write("# 使用说明：\n")
                f.write("# 1. 实时分析时系统会自动读取此文件，获取你的持仓状态\n")
                f.write("# 2. 分析完成后，系统会建议更新持仓，输入Y确认/N跳过\n")
                f.write("# 3. 你也可以手动编辑此文件（如在外部做了交易操作）\n")
                f.write("#\n")
                f.write("# 重要：系统决策依赖此数据的准确性！\n")
                f.write("#   - 如果你在券商APP做了买卖，请及时手动更新此文件\n")
                f.write("#   - lifecycle字段说明：FLAT=空仓 OPEN=新开仓 HOLD=持仓 EXIT=退出过程 COOLDOWN=冷却期\n")
                f.write("#   - strategy_state由系统自动维护，一般不需要手动修改\n")
                f.write("#\n")
                f.write("# ============================================================\n\n")

                # 写入positions
                yaml.dump(
                    self._data,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            logger.info(f"持仓文件已保存: {self.portfolio_path}")
        except Exception as e:
            logger.error(f"持仓文件保存失败: {e}")

    # ===== 读取操作 =====

    def get_position(self, stock_code: str) -> Optional[PositionRecord]:
        """获取指定股票的持仓记录"""
        positions = self._data.get("positions", {})
        pos_data = positions.get(stock_code)
        if pos_data is None:
            return None
        return PositionRecord.from_dict(stock_code, pos_data)

    def has_position(self, stock_code: str) -> bool:
        """是否持有指定股票"""
        return stock_code in self._data.get("positions", {})

    def list_positions(self) -> list[PositionRecord]:
        """列出所有持仓"""
        positions = self._data.get("positions", {})
        return [
            PositionRecord.from_dict(code, data)
            for code, data in positions.items()
        ]

    def to_strategy_state(self, stock_code: str) -> StrategyState:
        """将持仓记录转为StrategyState（供策略层使用）

        如果没有持仓记录，返回默认的FLAT状态。
        """
        pos = self.get_position(stock_code)
        if pos is None:
            return StrategyState()  # 默认FLAT

        # 解析lifecycle
        try:
            lifecycle = TradeLifecycle(pos.lifecycle)
        except ValueError:
            lifecycle = TradeLifecycle.FLAT

        # 从strategy_state字典恢复
        ss = pos.strategy_state or {}

        # 解析last_decision
        last_decision = None
        if ss.get("last_decision"):
            try:
                last_decision = SignalType(ss["last_decision"])
            except ValueError:
                last_decision = None

        return StrategyState(
            lifecycle=lifecycle,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            current_position_ratio=pos.current_ratio,
            recent_signals=ss.get("recent_signals", []),
            inertia_counter=ss.get("inertia_counter", 0),
            last_decision=last_decision,
            cooldown_remaining=ss.get("cooldown_remaining", 0),
            cooldown_reason=ss.get("cooldown_reason"),
            reverse_count=ss.get("reverse_count", 0),
            total_commission_paid=ss.get("total_commission_paid", 0.0),
            signal_stability_score=ss.get("signal_stability_score", 1.0),
            reduce_protection_remaining=ss.get("reduce_protection_remaining", 0),
            min_hold_remaining=ss.get("min_hold_remaining", 0),
            add_protection_remaining=ss.get("add_protection_remaining", 0),
        )

    # ===== 写入操作 =====

    def add_position(
        self,
        stock_code: str,
        stock_name: str = "",
        entry_price: Optional[float] = None,
        ratio: float = 0.20,
        lifecycle: str = "OPEN",
    ):
        """手动添加持仓"""
        today = datetime.now().strftime("%Y-%m-%d")
        record = PositionRecord(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_date=today,
            entry_price=entry_price,
            current_ratio=ratio,
            last_action="OPEN",
            last_action_date=today,
            lifecycle=lifecycle,
            strategy_state={
                "cooldown_remaining": 0,
                "cooldown_reason": None,
                "reverse_count": 0,
                "inertia_counter": 1,
                "last_decision": "BUY",
                "recent_signals": ["BUY"],
                "signal_stability_score": 1.0,
                "reduce_protection_remaining": 0,
                "min_hold_remaining": self.DEFAULT_MIN_HOLD_DAYS if lifecycle == "OPEN" else 0,
                "add_protection_remaining": 0,
            }
        )

        if "positions" not in self._data:
            self._data["positions"] = {}
        self._data["positions"][stock_code] = record.to_dict()
        self._save()

    def remove_position(self, stock_code: str):
        """删除持仓记录"""
        positions = self._data.get("positions", {})
        if stock_code in positions:
            del positions[stock_code]
            self._save()

    def attach_plan(self, stock_code: str, plan: TradePlan) -> bool:
        """为已存在持仓附加 TradePlan（v0.8.5）。

        Args:
            stock_code: 股票代码
            plan: TradePlan 实例（建议由 TradePlanGenerator 生成 + 用户编辑后传入）

        Returns:
            True 表示附加成功；False 表示持仓不存在
        """
        if "positions" not in self._data or stock_code not in self._data["positions"]:
            return False
        self._data["positions"][stock_code]["trade_plan"] = plan.model_dump()
        self._save()
        return True

    def update_from_strategy_decision(
        self,
        stock_code: str,
        stock_name: str,
        strategy_decision,  # StrategyDecision
        stock_data,  # StockData
        price: Optional[float] = None,
    ):
        """根据策略层决策结果更新持仓记录

        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            strategy_decision: 策略层决策结果（StrategyDecision）
            stock_data: 股票数据（StockData）
            price: 成交价格（用于更新entry_price），如果None则使用stock_data.price
        """
        today = datetime.now().strftime("%Y-%m-%d")
        new_state = strategy_decision.new_state
        price = price or stock_data.price

        # 获取或创建持仓记录
        existing = self.get_position(stock_code)

        # 防御：stock_name 为空或等于代码（Baostock 行情不含名称时回填代码）时，
        # 优先保留 existing 已维护的名称，避免把正确名称覆盖成代码（旧版 bug）
        if not stock_name or stock_name == stock_code:
            stock_name = (existing.stock_name if existing and existing.stock_name else stock_name)

        # 仓位动作映射
        pos_action = strategy_decision.position_action.value

        # 如果是OPEN动作且之前没有记录，设置开仓信息
        entry_date = existing.entry_date if existing else today
        entry_price = existing.entry_price if existing else price

        # 如果清仓了，清除开仓信息
        if strategy_decision.position_action.value == "CLOSE_ALL":
            entry_date = None
            entry_price = None

        # 仓位比例：审查修复 M-E--HOLD_POSITION（含 PlanGuard 规则1压制 REDUCE->HOLD）时
        # 必须保持原仓位（existing.current_ratio），否则回退到 new_state.current_position_ratio
        # （strategy_layer 算的减仓目标 0.65×原值）会导致记录 desync：last_action=HOLD_POSITION
        # 无实际卖出，但 current_ratio 被记成减仓后比例，持仓记录与券商 desync。
        if pos_action == "HOLD_POSITION":
            actual_ratio = existing.current_ratio if existing else 0.0
        else:
            # 非 HOLD：优先用策略层 position_ratio，回退到 new_state（OPEN/ADD/REDUCE/CLOSE_ALL 路径）
            actual_ratio = strategy_decision.position_ratio if strategy_decision.position_ratio > 0 else new_state.current_position_ratio

        record = PositionRecord(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_date=entry_date,
            entry_price=entry_price,
            current_ratio=actual_ratio,
            last_action=pos_action,
            last_action_semantic=strategy_decision.action_semantic,
            last_sell_path=strategy_decision.sell_path,
            last_action_date=today,
            lifecycle=new_state.lifecycle.value,
            strategy_state={
                "cooldown_remaining": new_state.cooldown_remaining,
                "cooldown_reason": new_state.cooldown_reason,
                "reverse_count": new_state.reverse_count,
                "inertia_counter": new_state.inertia_counter,
                "last_decision": new_state.last_decision.value if new_state.last_decision else None,
                "recent_signals": new_state.recent_signals,
                "signal_stability_score": new_state.signal_stability_score,
                "reduce_protection_remaining": new_state.reduce_protection_remaining,
                "min_hold_remaining": new_state.min_hold_remaining,
                "add_protection_remaining": new_state.add_protection_remaining,
            }
        )

        if "positions" not in self._data:
            self._data["positions"] = {}

        # 如果仓位为0且lifecycle是FLAT或COOLDOWN，保留记录（冷却期需要）
        # 如果仓位为0且lifecycle是FLAT且冷却期结束，删除记录
        if (actual_ratio <= 0
                and new_state.lifecycle == TradeLifecycle.FLAT
                and new_state.cooldown_remaining <= 0):
            # 交易完全结束，删除记录
            del self._data["positions"][stock_code]
        else:
            self._data["positions"][stock_code] = record.to_dict()

        self._save()

    def suggest_update(
        self,
        stock_code: str,
        stock_name: str,
        strategy_decision,
        stock_data,
        console=None,
    ) -> bool:
        """分析后建议更新持仓，Y/N交互确认

        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            strategy_decision: 策略层决策结果
            stock_data: 股票数据
            console: Rich Console实例

        Returns:
            bool: 是否更新了持仓
        """
        from rich.console import Console as RichConsole
        if console is None:
            console = RichConsole(legacy_windows=False)

        # 构建建议摘要
        pos_action_cn = {
            "OPEN": "试探建仓", "ADD": "加仓", "REDUCE": "减仓",
            "CLOSE_ALL": "全部清仓", "HOLD_POSITION": "维持仓位", "STAY_OUT": "空仓观望"
        }
        action_display = pos_action_cn.get(
            strategy_decision.position_action.value,
            strategy_decision.position_action.value
        )
        new_state = strategy_decision.new_state

        # 展示建议
        console.print(f"\n[bold cyan]📋 持仓更新建议[/bold cyan]")
        console.print(f"  股票: {stock_name} ({stock_code})")
        console.print(f"  动作: [bold]{action_display}[/bold]")
        if strategy_decision.action_semantic:
            console.print(f"  动作语义: {strategy_decision.action_semantic}")
        if strategy_decision.sell_path:
            console.print(f"  卖出路径: {strategy_decision.sell_path}")
        console.print(f"  生命周期: {strategy_decision.lifecycle_before.value} → {strategy_decision.lifecycle_after.value}")
        if new_state.cooldown_remaining > 0:
            console.print(f"  冷却期: 剩余{new_state.cooldown_remaining}天")

        # Y/N 确认
        try:
            answer = console.input("\n  [bold yellow]是否更新持仓记录？[Y/n][/bold yellow] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n  [dim]跳过更新[/dim]")
            return False

        if answer in ("", "y", "yes"):
            self.update_from_strategy_decision(
                stock_code, stock_name, strategy_decision, stock_data
            )
            console.print("  [green]✓ 持仓记录已更新[/green]")
            return True
        else:
            console.print("  [dim]跳过更新，持仓记录不变[/dim]")
            return False
