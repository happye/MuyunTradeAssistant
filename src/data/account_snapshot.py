"""账户事实快照、事件账本与离散数量分配（plan/fusion iteration2 R5，RESEARCH_LOOP §5）。

三件事：
1. **AccountSnapshot**：账户事实（account_id/as_of/cash_available/cash_reserved/
   holdings(数量/成本/批次)/NAV 及定价时点/data_completeness/version）。
   百分比仓位旧数据只能构造**权重假设视图**（RATIO_ONLY）——不能悄悄推成确切
   数量/可用现金（缺什么如实标 data_completeness）。
2. **AccountEventLog**：账户事件追加账本（BUY/SELL/费用/分红/存取款各自独立事件，
   不冒充股票收益）——event_id 幂等键（重复确认不重复入账，验收3）；**快照可由
   事件重放重建**（先写某账本后崩溃 → 重放恢复，不丢不重）。绝不写真实用户
   账户文件（测试隔离目录）。
3. **allocate_tradeable_budget**：连续预算（solve_budget 的 indicative 额度）→ 离散
   可行数量。产出 allocation_state=FEASIBLE/CONDITIONAL/REJECTED + 每条约束来源；
   FEASIBLE 仍是「满足已知规则的提案」，不保证市场成交。现金缺失不给可买金额/
   股数（None ≠ 0，验收1）；预算不足最低申报量 → 明确拒绝并释放额度。

交易规则（最小申报单位/板块差异/涨跌停等）经**注入的 rules 接口**进入——R6 提供
日期化官方规则文件核验的实现；本模块不写一套全市场硬编码 100 股规则（任务卡红线）。

纯函数+注入存储：零网络、零 AI、不碰真实 HOME。
"""

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

ACCOUNT_SNAPSHOT_VERSION = "r5.v1"
EVENT_SCHEMA_VERSION = 1


# ──────────────── 账户快照 ────────────────

class HoldingLot(BaseModel):
    """一笔持仓批次（T+1 可卖判定的最小单位——R6 回放消费）。"""
    model_config = ConfigDict(extra="allow")

    quantity: int = Field(gt=0, description="股数（正整数）")
    cost_price: float = Field(gt=0, description="买入成本价（未复权口径）")
    acquired_at: str = Field(description="买入日期 YYYY-MM-DD（T+1 起可卖）")


class AccountHolding(BaseModel):
    """一只持仓的事实（数量/成本/批次；plan_ref 关联主计划——R3 生命周期）。"""
    model_config = ConfigDict(extra="allow")

    security_id: str
    quantity: int = Field(default=0, description="总股数；RATIO_ONLY 视图为 0（未知）")
    avg_cost: Optional[float] = None
    lots: list[HoldingLot] = Field(default_factory=list)
    weight: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                    description="占 NAV 比例（权重假设视图用）")
    plan_ref: str = Field(default="", description="关联持仓主意图（active_refs 键）")


class AccountSnapshot(BaseModel):
    """账户事实快照（不可变；由事件重放或用户录入构造）。"""
    model_config = ConfigDict(extra="allow")

    account_id: str = "default"
    as_of: datetime
    currency: str = "CNY"
    cash_available: Optional[float] = Field(default=None, description="可用现金（元）；None=未知——不给可买金额/股数")
    cash_reserved: float = Field(default=0.0, ge=0.0, description="已占用现金（拟买未成交挂单等）——不重复计可用")
    nav: Optional[float] = Field(default=None, description="账户净资产（元）；None=未知（数量分配 CONDITIONAL）")
    nav_priced_at: Optional[datetime] = Field(default=None, description="NAV 定价时点（不是快照生成时刻）")
    holdings: list[AccountHolding] = Field(default_factory=list)
    data_completeness: Literal["QUANTITY_LEVEL", "RATIO_ONLY", "PARTIAL"] = Field(
        default="RATIO_ONLY", description="QUANTITY_LEVEL=数量/成本/现金齐备；RATIO_ONLY=仅权重假设视图；PARTIAL=部分齐备")
    version: str = ACCOUNT_SNAPSHOT_VERSION

    @property
    def cash_deployable(self) -> Optional[float]:
        """可用于新增买入的现金——已占用（reserved）不重复计（验收4）。
        拟卖未确认的钱**不在** cash_available 里（卖出确认入账后才出现）。"""
        if self.cash_available is None:
            return None
        return max(0.0, self.cash_available - self.cash_reserved)


# ──────────────── 账户事件账本（幂等 + 重放）────────────────

class EventType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FEE = "FEE"              # 佣金/税费（独立事件，不冒充股票收益）
    DIVIDEND = "DIVIDEND"    # 分红（独立事件）
    DEPOSIT = "DEPOSIT"      # 存入现金
    WITHDRAW = "WITHDRAW"    # 取出现金


class AccountEvent(BaseModel):
    """一条账户事件（event_id 幂等键——重复确认/崩溃重放不重复入账）。"""
    model_config = ConfigDict(extra="allow")

    event_id: str
    event_type: EventType
    security_id: str = ""                  # 现金类事件为空
    trade_date: str = Field(description="YYYY-MM-DD")
    quantity: int = Field(default=0, description="股数（现金类=0）")
    price: Optional[float] = None
    cash_delta: float = Field(default=0.0, description="现金变动（元，带方向：买入为负含费用）")
    fee: float = Field(default=0.0, ge=0.0, description="本事件产生的费用（元）")
    lot_cost_price: Optional[float] = None  # BUY 批次成本价
    related_fill_id: str = Field(default="", description="关联确认 fill（幂等联动）")
    recorded_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class AccountEventLog:
    """账户事件追加账本（JSONL；event_id 幂等；重放重建快照——验收3 崩溃恢复）。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> list[AccountEvent]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(AccountEvent(**json.loads(line)))
            except Exception as e:  # 坏行隔离（G14）
                logger.warning(f"账户事件坏行隔离: {e}")
        return out

    def append(self, event: AccountEvent) -> bool:
        """追加事件；重复 event_id 幂等拒绝（返回 False，不重复入账）。"""
        if any(e.event_id == event.event_id for e in self._load()):
            logger.info(f"账户事件 {event.event_id} 已入账，幂等跳过")
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return True

    def replay(self, *, opening_cash: Optional[float] = None) -> AccountSnapshot:
        """事件重放 → 账户快照（追加序即权威序；同事件集重放结果一致——崩溃恢复
        不丢不重：已落盘事件重放一次与多次结果相同）。

        opening_cash：期初现金。None（缺省）=期初未知——有现金事件也无法推算绝对
        余额，cash_available=None（未知≠0，与验收1 同口径）；显式传 0.0=已知从零起。
        fee 字段仅分类留痕：现金一律以 cash_delta 为准（写侧不得以为 fee 自动扣现金）。

        单写者假设：append 幂等是 check-then-act，多进程并发同 event_id 会双行——
        账本沿用 proposals.json 的单写者口径（M5 指纹拒绝在调用方，验收7 注记）。"""
        cash: Optional[float] = opening_cash
        holdings: dict[str, AccountHolding] = {}
        for e in self._load():
            cash = None if cash is None else cash + e.cash_delta
            if not e.security_id:
                continue
            if e.event_type is EventType.BUY and e.quantity > 0:
                cost = e.lot_cost_price or e.price
                if not cost or cost <= 0:
                    # 坏批次（无有效成本价）：隔离留痕，不炸整本重放（监督员 P1——
                    # 一行坏账拖死全部恢复违背 G14 哲学）
                    logger.warning(f"账户事件 {e.event_id} 缺有效成本价（lot_cost_price/price 均无效）"
                                   "——该批次不入快照，事件保留待人工核对")
                    continue
                h = holdings.setdefault(e.security_id, AccountHolding(security_id=e.security_id))
                h.quantity += e.quantity
                h.lots.append(HoldingLot(quantity=e.quantity, cost_price=cost,
                                         acquired_at=e.trade_date))
            elif e.event_type is EventType.SELL and e.quantity > 0:
                h = holdings.get(e.security_id)
                if h is None or h.quantity <= 0:
                    logger.warning(f"卖出事件 {e.event_id} 无对应持仓（{e.security_id}）——"
                                   "幽灵卖出隔离留痕，不建空持仓条目（监督员 P2）")
                    continue
                # 卖出从最早批次扣（T+1/先买先卖）；数量不足 = 账本不一致 → 显式告警
                remain = e.quantity
                while remain > 0 and h.lots:
                    lot = h.lots[0]
                    take = min(lot.quantity, remain)
                    lot.quantity -= take
                    remain -= take
                    if lot.quantity == 0:
                        h.lots.pop(0)
                if remain > 0:
                    logger.warning(f"卖出事件 {e.event_id} 超出持仓 {remain} 股（账本不一致）——"
                                   "按 0 下限处理并留痕，不造假")
                h.quantity = max(0, h.quantity - e.quantity)
        return AccountSnapshot(as_of=datetime.now().astimezone(),
                               cash_available=cash,
                               holdings=list(holdings.values()),
                               data_completeness="QUANTITY_LEVEL" if cash is not None else "PARTIAL")


# ──────────────── 交易规则接口（R6 提供日期化实现；R5 只消费）────────────────

class LotRules(BaseModel):
    """某证券某日的申报数量规则（由注入的 rules 接口给出——带来源与生效日期）。"""
    model_config = ConfigDict(extra="allow")

    security_id: str
    board: str = Field(description="板块（main/chinext/star/bj——规则差异来源）")
    min_order_qty: int = Field(gt=0, description="最低申报数量（股）")
    lot_step: int = Field(gt=0, description="递增单位（股；卖出零股规则独立由 sell_odd_lots_allowed 表达）")
    sell_odd_lots_allowed: bool = Field(default=True, description="卖出是否允许零股（清仓场景）")
    effective_from: str = Field(description="规则生效日期 YYYY-MM-DD（来源可查）")
    source: str = Field(default="", description="规则来源（官方文件/交易所规则引用——R6 核验）")


class TradeRulesAdapter:
    """交易规则接口（协议）：R6 提供官方核验实现；测试注入固定夹具。
    本模块**不实现**全市场规则表（任务卡红线：R5 不得另写一套 100 股规则）。"""

    def lot_rules(self, security_id: str, as_of: str) -> Optional[LotRules]:
        raise NotImplementedError

    def fee_model(self, security_id: str, as_of: str) -> "FeeModel":
        return FeeModel()


class FeeModel(BaseModel):
    """费用模型（参数显式注入；佣金最低价边界是费用反例的测试点）。"""
    commission_rate: float = Field(default=0.0003, ge=0.0)
    min_commission: float = Field(default=5.0, ge=0.0, description="单笔最低佣金（元）——小额申报的费用边界")
    stamp_tax_rate: float = Field(default=0.0005, ge=0.0, description="印花税（仅卖出收取）")

    def buy_fee(self, amount: float) -> float:
        return max(self.min_commission, amount * self.commission_rate)

    def sell_fee(self, amount: float) -> float:
        return max(self.min_commission, amount * self.commission_rate) + amount * self.stamp_tax_rate


# ──────────────── 离散数量分配 ────────────────

class AllocationState(str, Enum):
    FEASIBLE = "FEASIBLE"          # 满足已知规则的可执行提案（不保证成交）
    CONDITIONAL = "CONDITIONAL"    # 缺输入（现金/NAV/规则未知）——只给唯一阻塞字段
    REJECTED = "REJECTED"          # 确定不可行（含预算不足最低申报量）


class TradeAllocation(BaseModel):
    """一条离散分配结果（数量级；连续 indicative 额度的可行化）。"""
    model_config = ConfigDict(extra="allow")

    stock_code: str
    indicative_weight: float = Field(description="来自 solve_budget 的连续额度（展示为研究假设）")
    quantity: int = Field(default=0, description="可行申报数量（股）；CONDITIONAL/REJECTED 为 0")
    price_used: Optional[float] = None
    estimated_cost: Optional[float] = None
    estimated_fee: Optional[float] = None
    allocation_state: AllocationState
    blocked_field: str = Field(default="", description="唯一阻塞字段（CONDITIONAL 时人话——今天只问这个）")
    rejected_reason: str = Field(default="", description="REJECTED 人话原因（如预算不足最低申报量）")
    rules_source: str = Field(default="", description="数量规则来源（可查）")
    released_budget_weight: float = Field(default=0.0, ge=0.0,
                                          description="不可行时释放回池的额度——**已释放待再分配**"
                                                      "（自动再分配随 R8 接线，本版只登记不加量）")


def allocate_tradeable_budget(lines: list, snapshot: AccountSnapshot,
                              rules: TradeRulesAdapter, *, as_of: str,
                              price_provider: Optional[Callable] = None) -> list[TradeAllocation]:
    """连续 indicative 额度 → 离散可行分配（RESEARCH_LOOP §5 六步；验收1/2/4/6）。

    - cash/NAV 未知 → CONDITIONAL + blocked_field（不给可买金额或股数——None≠0）
    - 预算含费不足最低申报量 → REJECTED + released_budget_weight（额度释放回池）
    - 数量 = 可承担金额按 lot_step 向下取整；浮点不借取整突破上限
    - 卖出零股规则独立（buy 用 min_order_qty/lot_step；本函数只管新增买入——
      卖出数量由持仓批次与 R6 规则裁决）
    - 已持仓风险占用在 solve_budget 的压力约束内（不在此重复实现）；"未来回撤参数"
      无法输入：本函数不接收任何未来窗口回撤输入（参数表里没有它的位置——验收6）
    - price_provider(security_id) -> (price, priced_at)：价格缺失 → CONDITIONAL
    """
    out: list[TradeAllocation] = []
    release_pool: list[tuple[str, float]] = []  # (code, weight) 释放队列（再分配随 R8）
    deployable = snapshot.cash_deployable
    remaining_cash = deployable  # **串行扣减的现金池**（监督员 P1：多行合计不得超可用）
    nav = snapshot.nav
    for line in lines:
        indicative = float(getattr(line, "add_weight", 0.0) or 0.0)
        if indicative <= 1e-9:
            out.append(TradeAllocation(stock_code=line.stock_code,
                                       indicative_weight=indicative,
                                       allocation_state=AllocationState.REJECTED,
                                       rejected_reason="无可新增额度（solve_budget 已拒）"))
            continue
        lr = rules.lot_rules(line.stock_code, as_of)
        if lr is None:
            out.append(TradeAllocation(stock_code=line.stock_code,
                                       indicative_weight=indicative,
                                       allocation_state=AllocationState.CONDITIONAL,
                                       blocked_field="交易规则未知（该板块/日期无已核验规则）"))
            continue
        price = price_provider(line.stock_code) if price_provider else None
        if isinstance(price, (tuple, list)):
            p = price[0] if price else None
        else:
            p = price
        if not isinstance(p, (int, float)) or isinstance(p, bool) or p <= 0:
            out.append(TradeAllocation(stock_code=line.stock_code,
                                       indicative_weight=indicative,
                                       allocation_state=AllocationState.CONDITIONAL,
                                       blocked_field="价格缺失或形状非法（不能编造估值输入）",
                                       rules_source=lr.source))
            continue
        if nav is None:
            out.append(TradeAllocation(stock_code=line.stock_code,
                                       indicative_weight=indicative,
                                       allocation_state=AllocationState.CONDITIONAL,
                                       blocked_field="账户净值未知（不能把权重推成金额）",
                                       price_used=p, rules_source=lr.source))
            continue
        if remaining_cash is None:
            out.append(TradeAllocation(stock_code=line.stock_code,
                                       indicative_weight=indicative,
                                       allocation_state=AllocationState.CONDITIONAL,
                                       blocked_field="可用现金未知（现金缺失≠0，不给可买金额/股数）",
                                       price_used=p, rules_source=lr.source))
            continue
        budget_amount = indicative * nav
        fee_m = rules.fee_model(line.stock_code, as_of)
        # 可承担数量：预算与**剩余现金池**的较小者（前序行已扣减——多行合计不超可用），
        # 按 lot_step 向下取整（含费试算）
        affordable_cash = min(budget_amount, remaining_cash)
        qty = int(affordable_cash // (p * lr.lot_step) * lr.lot_step) if p > 0 else 0
        # 含费边界：数量取整后总成本+费用不得超过现金（最低佣金边界在此暴露）
        while qty >= lr.min_order_qty:
            amount = qty * p
            fee = fee_m.buy_fee(amount)
            if amount + fee <= remaining_cash + 1e-6 and amount + fee <= budget_amount + 1e-6:
                break
            qty -= lr.lot_step
        if qty < lr.min_order_qty:
            min_amount = lr.min_order_qty * p
            min_total = min_amount + fee_m.buy_fee(min_amount)
            reason = (f"一笔最低合法申报（{lr.min_order_qty} 股 ≈ {min_total:.0f} 元含费）"
                      f"所需资金仍不足——预算 {budget_amount:.0f} 元、可用现金 {remaining_cash:.0f} 元")
            out.append(TradeAllocation(stock_code=line.stock_code,
                                       indicative_weight=indicative,
                                       quantity=0, price_used=p,
                                       allocation_state=AllocationState.REJECTED,
                                       rejected_reason=reason,
                                       released_budget_weight=indicative,
                                       rules_source=lr.source))
            release_pool.append((line.stock_code, indicative))
            continue
        amount = qty * p
        fee = fee_m.buy_fee(amount)
        # 终检：离散取整不得突破上限（浮点不借取整越限——验收6 后半）；递减后
        # 复核最低申报量（防御——监督员 P2）
        final_weight = (amount + fee) / nav
        if final_weight > indicative + 1e-6:
            qty -= lr.lot_step
            amount = qty * p
            fee = fee_m.buy_fee(amount)
        if qty < lr.min_order_qty:
            out.append(TradeAllocation(stock_code=line.stock_code,
                                       indicative_weight=indicative,
                                       quantity=0, price_used=p,
                                       allocation_state=AllocationState.REJECTED,
                                       rejected_reason="终检递减后不足最低申报量",
                                       released_budget_weight=indicative,
                                       rules_source=lr.source))
            release_pool.append((line.stock_code, indicative))
            continue
        out.append(TradeAllocation(stock_code=line.stock_code,
                                   indicative_weight=indicative,
                                   quantity=qty, price_used=p,
                                   estimated_cost=round(amount, 2),
                                   estimated_fee=round(fee, 2),
                                   allocation_state=AllocationState.FEASIBLE,
                                   rules_source=lr.source))
        remaining_cash = max(0.0, remaining_cash - amount - fee)
    # 释放额度如实登记于 released_budget_weight（回池待再分配——**再分配实现随 R8**：
    # 加量会改变用户已见建议语义，先登记不自动加量——监督员 P2 如实披露）
    return out
