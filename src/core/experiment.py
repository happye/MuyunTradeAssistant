"""实验矩阵与严格回放基建（plan/fusion F8，VALIDATION §2/§3 + DESIGN 迁移）。

诚实范围声明（TASKS F8）：
- 本模块交付**实验基建的离线可验证部分**：E0–E7 注册（冻结字段）、实验 manifest
  模板、固定组合资金的组合回放纯模型（与"多只独立满仓回测均值"严格区分）、
  制度模型检查（T+1/手数/税费单调性/未来数据探针钩子）
- **E0–E7 的真实执行**需要外源数据与长时回测——单独入口 opt-in 跑（external/AI
  纪律），不混进默认 pytest；执行前须先落 manifest（VALIDATION §2：代码提交/
  配置 hash/数据版本/as_of/样本集/纳入排除原因/冻结时间缺一不可）
- 不动 backtest_engine/execution_layer（零改动——窄适配以独立模块消费其输出概念）；
  已有 DataFeeder 等价性参照基准不得删除（tests/backtest/test_datafeeder_vectorized_equiv.py）
- 严格区分三类信息集：规则 bz 代理 / AI 前瞻输出 / 人工 mode 案例——实验 ID 命名
  强制携带信息集标签（E_SPEC.source_tag），不得合并为同一策略名称

离线可验证的验收（TASKS F8）：
- 离线重放可复现：PortfolioReplay 同输入同结果（fingerprint）
- 未来数据探针红灯：含未来 bar 的输入必须被拒（PIT 闸门钩子）
- 成本加大不会无解释提高净收益：成本单调性性质测试
- 场景覆盖：IPO/停牌/一字板/除权分红/退市/财务修订——ReplayChecks 的纯函数检查
  （真实数据场景跑批属 external 操作，本模块提供检查函数+离线单元验证）
"""

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

EXPERIMENT_FRAMEWORK_VERSION = "f8.v1"


class InfoSetTag(str, Enum):
    """信息集标签（TASKS F8：严格区分三类，不得混记）。"""

    RULE_PROXY = "rule_bz_proxy"     # 规则版 bz 代理
    AI_LOOKAHEAD = "ai_lookahead"    # AI 前瞻输出（live 口径，回测禁用）
    HUMAN_MODE = "human_mode"        # 人工指定 mode 案例


class ExperimentSpec(BaseModel):
    """一条 E 矩阵实验注册（VALIDATION §3 表的机器化）。"""

    model_config = ConfigDict(extra="allow")

    experiment_id: str = Field(description="E0..E7")
    name: str
    fixed: str = Field(description="固定什么（VALIDATION §3 表）")
    varied: str = Field(description="改变什么")
    answers: str = Field(description="回答什么")
    source_tag: InfoSetTag = Field(description="信息集标签（强制）")
    depends_on: list[str] = Field(default_factory=list, description="依赖的模块/数据")
    runnable_offline: bool = Field(default=False, description="当前基建下能否离线执行")
    status: Literal["registered", "blocked_on_data", "blocked_on_module", "runnable"] = "registered"


# E0–E7 注册（VALIDATION §3 原表；真实执行随数据资格/模块就绪解锁）
E_SPEC: list[ExperimentSpec] = [
    ExperimentSpec(experiment_id="E0", name="正确性基线", fixed="同一历史快照、旧策略参数",
                   varied="仅终态/事实持仓/执行正确性修正", answers="旧收益含哪些错误或执行差异",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["F1", "F2", "F8.replay"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E1", name="候选召回", fixed="同时点市场全集、研究预算",
                   varied="技术单路/产业单路/长期质量单路/并集配额",
                   answers="有效机会覆盖是否增加，是否只是多看更多股",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["F4.candidate_pool"],
                   runnable_offline=True, status="blocked_on_data"),  # 机制可跑、历史市场快照未备（F8 审查 P2）
    ExperimentSpec(experiment_id="E2", name="投资逻辑资格", fixed="同一候选集合、相同入场/退出",
                   varied="无资格过滤/bz旧分/证据资格", answers="产业/公司研究是否有增量，拒绝了哪些机会",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["F3.qualify", "F5"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E3", name="持有纪律", fixed="同一事前候选与入场日",
                   varied="legacy/fusion_mid/fusion_long（分别报告）",
                   answers="收益/回撤/换手改善是否来自周期纪律",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["F5.decision_policy"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E4", name="技术择时", fixed="同一投资逻辑和预算",
                   varied="固定周期分批/技术触发/混合门控", answers="技术层能否改善成本、风险或机会捕捉",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["F5", "F7.budget"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E5", name="AI", fixed="同一证据、计划、预算",
                   varied="无AI/结构化提取/提取+反证；旧modifier作独立对照",
                   answers="是信息增量还是重复技术/新闻，净收益是否覆盖费用",
                   source_tag=InfoSetTag.AI_LOOKAHEAD, depends_on=["F6.claims"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E6", name="组合", fixed="同一单股动作集合",
                   varied="逐股建议对照/统一资金与集中度约束",
                   answers="是否减少不可实现仓位和集中风险",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["F7.portfolio_policy"],
                   runnable_offline=True, status="runnable"),
    ExperimentSpec(experiment_id="E7", name="完整系统", fixed="同一PIT全集、成本、研究资源预算",
                   varied="修正后基线 vs 完整融合漏斗", answers="真实产品流程的净效果",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["E0..E6"],
                   runnable_offline=False, status="blocked_on_data"),
]


class ExperimentManifest(BaseModel):
    """实验 manifest（VALIDATION §2：先建数据资格表——缺一字段不许开跑）。"""

    model_config = ConfigDict(extra="allow")

    experiment_id: str
    code_commit: str = Field(description="代码提交")
    config_hash: str = Field(description="配置/参数 hash")
    data_versions: dict = Field(default_factory=dict, description="数据源版本")
    as_of: str = Field(description="截止时点")
    trading_calendar: str = Field(default="", description="交易日历版本")
    fee_rules: str = Field(default="", description="费用规则")
    sample_set: list[str] = Field(default_factory=list, description="样本集与纳入/排除原因（代码级）")
    missing_ratio: float = Field(default=0.0, description="缺失比例")
    ai_model: str = Field(default="", description="AI 模型/prompt 版本（无 AI 留空）")
    trial_number: str = Field(default="", description="试验编号（失败试验也保留）")
    frozen_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"),
                           description="冻结时间")

    @classmethod
    def build(cls, experiment_id: str, *, config_hash: str, as_of: str,
              sample_set: list[str], code_commit: str = "HEAD", **kw) -> "ExperimentManifest":
        import subprocess
        if code_commit == "HEAD":
            try:
                code_commit = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                    text=True, encoding="utf-8", timeout=10).stdout.strip()
            except Exception:
                code_commit = ""
            if not code_commit:
                code_commit = "unknown"  # 非 git 环境/命令失败兜底（F8 审查 P2）
        m = cls(experiment_id=experiment_id, code_commit=code_commit,
                config_hash=config_hash, as_of=as_of, sample_set=sample_set, **kw)
        if not m.sample_set:
            raise ValueError("manifest 样本集为空——不许开跑（VALIDATION §2）")
        return m

    def fingerprint(self) -> str:
        d = self.model_dump()
        return hashlib.sha256(json.dumps(d, ensure_ascii=False, sort_keys=True,
                                         default=str).encode("utf-8")).hexdigest()[:16]


# ──────────────── 固定组合资金回放（纯模型；与独立满仓均值严格区分）────────────────

class PortfolioPosition(BaseModel):
    """组合回放中的持仓（组合级：共享现金，非逐股独立账户）。

    lots：买入批次 [{"date","shares"}]——T+1 按批次（G11 正确答案：可卖量按交易批次，
    不能整仓当日锁住也不允许卖当日新买份额；F8 审查 P1-B 的收口实现）。"""

    stock_code: str
    shares: int = Field(ge=0, description="股数（非手）")
    cost_per_share: float = Field(ge=0.0)
    last_price: float = Field(ge=0.0)
    buy_date: str = Field(description="首次建仓日 YYYY-MM-DD（展示用；T+1 判定看 lots）")
    lots: list = Field(default_factory=list, description='买入批次 [{"date","shares"}]')


class ReplayTrade(BaseModel):
    """一笔回放成交。"""

    stock_code: str
    date: str
    action: Literal["BUY", "SELL"]
    shares: int
    price: float
    fee: float = 0.0
    note: str = ""


class ReplayChecks:
    """制度模型检查（纯函数；TASKS F8 成交模型核对清单）。"""

    LOT = 100  # A股一手=100股（数量单位）

    @staticmethod
    def check_t_plus_1(buy_date: str, sell_date: str) -> None:
        """T+1：当日买入不可当日卖出（G11）。违规抛错。"""
        if date.fromisoformat(buy_date) >= date.fromisoformat(sell_date):
            raise ValueError(f"T+1 违规：买入 {buy_date} 不可在 {sell_date} 卖出")

    @staticmethod
    def check_lot_size(shares: int, action: str) -> int:
        """数量单位（F8 审查 P2：原恒等函数已实现语义）：BUY 必须整手；
        SELL 允许尾股（清仓尾数）。违规抛错，返回原股数。"""
        if shares < 0:
            raise ValueError("股数不可为负")
        if action == "BUY" and shares % ReplayChecks.LOT != 0 and shares != 0:
            raise ValueError(f"买入必须整手（{ReplayChecks.LOT} 股）：收到 {shares}")
        return shares

    @staticmethod
    def round_lot_buy(target_shares: int) -> int:
        """买入取整到整手（向下）。"""
        return (target_shares // ReplayChecks.LOT) * ReplayChecks.LOT

    @staticmethod
    def check_limit_board(action: str, price: float, limit_price: Optional[float]) -> None:
        """一字板检查：买入价≥涨停价 / 卖出价≤跌停价 → 不可成交（G03 同源语义）。"""
        if limit_price is None:
            return
        if action == "BUY" and price >= limit_price:
            raise ValueError(f"涨停封板不可买入：{price} >= {limit_price}")
        if action == "SELL" and price <= limit_price:
            raise ValueError(f"跌停封板不可卖出：{price} <= {limit_price}")

    @staticmethod
    def validate_no_future_bars(bars: list[dict], as_of: str) -> None:
        """未来数据探针：输入 bar 中任何 date > as_of 即红灯（回放前强制调用）。"""
        for b in bars:
            if str(b.get("date")) > as_of:
                raise ValueError(f"未来数据探针红灯：bar {b.get('date')} 晚于 as_of {as_of}")


class PortfolioReplay:
    """固定组合资金回放（纯模型）：共享现金的组合同步持仓——与"多只独立满仓回测
    均值"严格区分（TASKS F8：不能把后者叫可投资组合）。

    确定性：同输入（初始现金+交易序列+价格）同结果；成本参数进入每笔成交的 fee，
    成本单调性由测试锁定（成本加大净收益不升）。
    """

    FEE_RATE = 0.0003   # 佣金（双边）
    STAMP_RATE = 0.001  # 印花税（卖出）

    def __init__(self, initial_cash: float, *, fee_rate: Optional[float] = None,
                 stamp_rate: Optional[float] = None):
        if initial_cash <= 0:
            raise ValueError("初始资金必须为正")
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.fee_rate = self.FEE_RATE if fee_rate is None else fee_rate
        self.stamp_rate = self.STAMP_RATE if stamp_rate is None else stamp_rate
        self.positions: dict[str, PortfolioPosition] = {}
        self.trades: list[ReplayTrade] = []

    def fingerprint(self) -> str:
        canon = {
            "cash": round(self.cash, 6),
            "positions": sorted((p.model_dump() for p in self.positions.values()),
                                key=lambda d: d["stock_code"]),
            "trades": [t.model_dump() for t in self.trades],
        }
        return hashlib.sha256(json.dumps(canon, ensure_ascii=False, sort_keys=True,
                                         default=str).encode("utf-8")).hexdigest()[:16]

    def buy(self, stock_code: str, date: str, price: float, target_weight: float,
            total_nav: Optional[float] = None) -> ReplayTrade:
        """按目标权重买入（整手向下取整；现金不足按现金能买的最大整手）。"""
        ReplayChecks.check_limit_board("BUY", price, None)
        nav = total_nav if total_nav is not None else self.nav({})  # 无现价表用 last_price
        budget = min(target_weight * nav, self.cash)
        if budget <= 0:
            raise ValueError(f"现金不足不可买入：{stock_code} budget={budget}")
        # F8 审查 P1-A：ADR-F07"转换份额前扣除费用"——先扣费再取整（满仓输入
        # 不再因费差崩溃）
        shares = ReplayChecks.round_lot_buy(int(budget / (1 + self.fee_rate) / price))
        if shares <= 0:
            raise ValueError(f"预算不足一手：{stock_code} budget={budget:.2f} price={price}")
        return self._fill(stock_code, date, "BUY", shares, price)

    def sell(self, stock_code: str, date: str, price: float, shares: Optional[int] = None) -> ReplayTrade:
        """卖出（默认全仓=卖出全部可卖批次；部分成交按股数）。

        T+1 按批次（G11）：可卖 = 各批次中 date 早于当日的份额和——当日新买份额不可卖，
        旧份额当日可卖（不再整仓锁死）。请求超过可卖 → 拒绝（严格回放不伪造成交）。"""
        pos = self.positions.get(stock_code)
        if pos is None:
            raise ValueError(f"无持仓不可卖出：{stock_code}")
        sellable = sum(l["shares"] for l in pos.lots if l["date"] < date)
        n = pos.shares if shares is None else shares
        if n > sellable:
            raise ValueError(
                f"T+1 批次限制：{stock_code} 可卖 {sellable} 股（当日买入 {pos.shares - sellable} "
                f"股不可卖），请求 {n}")
        if n <= 0:
            raise ValueError(f"卖出股数非法：{stock_code} {n}")
        # FIFO 消耗最旧批次（可卖批次必然 date < 当日）
        remaining = n
        new_lots = []
        for l in pos.lots:
            if remaining > 0 and l["date"] < date:
                take = min(l["shares"], remaining)
                remaining -= take
                if l["shares"] - take > 0:
                    new_lots.append({"date": l["date"], "shares": l["shares"] - take})
            else:
                new_lots.append(dict(l))
        pos.lots = new_lots
        return self._fill(stock_code, date, "SELL", n, price)

    def _fill(self, stock_code: str, date: str, action: str, shares: int, price: float) -> ReplayTrade:
        amount = shares * price
        fee = round(amount * self.fee_rate + (amount * self.stamp_rate if action == "SELL" else 0.0), 2)
        if action == "BUY":
            cost = amount + fee
            if cost > self.cash + 1e-6:
                raise ValueError(f"现金不足：需 {cost:.2f} 现 {self.cash:.2f}")
            self.cash -= cost
            pos = self.positions.get(stock_code)
            if pos is None:
                self.positions[stock_code] = PortfolioPosition(
                    stock_code=stock_code, shares=shares, cost_per_share=price,
                    last_price=price, buy_date=date,
                    lots=[{"date": date, "shares": shares}])
            else:
                total_cost = pos.cost_per_share * pos.shares + amount
                pos.shares += shares
                pos.cost_per_share = total_cost / pos.shares
                pos.last_price = price
                pos.lots.append({"date": date, "shares": shares})  # 批次登记（T+1 按批次）
        else:
            pos = self.positions[stock_code]
            proceeds = amount - fee
            self.cash += proceeds
            pos.shares -= shares
            pos.last_price = price
            if pos.shares == 0:
                del self.positions[stock_code]
        trade = ReplayTrade(stock_code=stock_code, date=date, action=action,
                            shares=shares, price=price, fee=fee)
        self.trades.append(trade)
        return trade

    def nav(self, prices: dict) -> float:
        """总资产 = 现金 + Σ持仓市值（prices: {code: price}；缺价用 last_price）。"""
        mv = 0.0
        for code, pos in self.positions.items():
            p = prices.get(code, pos.last_price)
            mv += pos.shares * (p if p is not None else pos.last_price)
        return self.cash + mv
