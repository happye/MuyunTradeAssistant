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


# ──────────────── R6：日期化交易规则、公司行动与制度场景 ────────────────

class DatedRuleEntry(BaseModel):
    """一条日期化规则（生效日期 + 板块范围 + 来源——R6 验收4：切换日边界有真实来源）。

    诚实边界：本注册表只登记**可公开核验的稳定规则事实**并附来源；交易所可能
    调整——R9 发布前须按官方现行文件复核，本表不冒充最新制度清单。"""

    rule_id: str
    board_scope: list[str] = Field(default_factory=list, description="适用板块（空=全市场）")
    effective_from: str = Field(description="生效日期 YYYY-MM-DD")
    value: dict = Field(description="规则参数（如 {'stamp_rate': 0.0005}）")
    source: str = Field(description="来源（官方公告/交易所规则名——可查）")


# 已知真实的规则切换事实（示例：印花税 2023-08-28 减半——财政部/税务总局公告
# 2023年第39号；科创板最低申报 200 股——上交所科创板股票交易规则）。
# 用途：切换日边界测试的真实来源锚点；**非最新制度清单**（R9 复核）。
DATED_RULES: list[DatedRuleEntry] = [
    DatedRuleEntry(rule_id="stamp_tax_sell", board_scope=[],
                   effective_from="2023-08-28",
                   value={"stamp_rate": 0.0005},
                   source="财政部 税务总局公告2023年第39号：证券交易印花税减半征收"),
    DatedRuleEntry(rule_id="stamp_tax_sell", board_scope=[],
                   effective_from="1900-01-01",
                   value={"stamp_rate": 0.001},
                   source="2023-08-28 前的印花税口径（减半前）"),
    DatedRuleEntry(rule_id="star_min_order", board_scope=["star"],
                   effective_from="2019-07-22",
                   value={"min_order_qty": 200, "lot_step": 1},
                   source="上海证券交易所科创板股票交易规则：限价申报单笔不低于200股"),
    DatedRuleEntry(rule_id="main_min_order", board_scope=["main", "chinext"],
                   effective_from="1900-01-01",
                   value={"min_order_qty": 100, "lot_step": 100},
                   source="沪深交易所交易规则：买入以100股（一手）为单位"),
]


def rule_at(rule_id: str, as_of: str, board: str = "") -> Optional[DatedRuleEntry]:
    """取某规则在 as_of 时点对某板块生效的最新条目（切换日边界判定）。"""
    cands = [r for r in DATED_RULES
             if r.rule_id == rule_id and r.effective_from <= as_of
             and (not r.board_scope or board in r.board_scope)]
    if not cands:
        return None
    return max(cands, key=lambda r: r.effective_from)


class CorporateAction(BaseModel):
    """一条公司行动（除权/除息/送转——与未复权价格配对使用，DATA_TRUST §5：
    「以未复权成交价配公司行动台账计算持仓现金与数量」——不得复权价与分红现金双计）。"""
    model_config = ConfigDict(extra="allow")

    security_id: str
    ex_date: str = Field(description="除权除息日 YYYY-MM-DD")
    cash_dividend_per_share: float = Field(default=0.0, ge=0.0, description="每股现金分红（税前口径登记）")
    share_ratio: float = Field(default=0.0, ge=0.0, description="每股送转比例（如 10送3 → 0.3）")
    source: str = Field(default="", description="分红送转公告来源（可查）")


def apply_corporate_action(replay: "PortfolioReplay", action: CorporateAction) -> list[ReplayTrade]:
    """把公司行动落到回放账本（现金分红入现金；送转扩股不产生现金）——返回生成的
    非交易账目（action 类型 ReplayTrade：shares 变动记录为 price=0 的 note 账目）。

    恒等关系（R6 验收2）：除权日前持有 N 股、未复权价 P：
      行动后现金 += N×每股分红；股数 += N×送转比例；股价口径保持未复权——
      投资者收益 = (未复权卖价−未复权买价)×股数 + 分红现金，**不得**再用前复权
      序列重复计入分红（前复权已把分红从历史价里扣除——双计即虚增收益）。"""
    generated: list[ReplayTrade] = []
    pos = replay.positions.get(action.security_id)
    if pos is None or pos.shares <= 0:
        return generated
    if action.cash_dividend_per_share > 0:
        cash_amt = round(pos.shares * action.cash_dividend_per_share, 2)
        replay.cash += cash_amt
        generated.append(ReplayTrade(stock_code=action.security_id, date=action.ex_date,
                                     action="SELL", shares=0, price=0.0,
                                     note=f"现金分红 {cash_amt} 元（{action.cash_dividend_per_share}/股，"
                                          f"来源:{action.source or '未登记'}）——独立事件不并入价差"))
    if action.share_ratio > 0:
        new_shares = int(pos.shares * action.share_ratio)
        if new_shares > 0:
            pos.shares += new_shares
            pos.lots.append({"date": action.ex_date, "shares": new_shares,
                             "corporate_action": True})
            pos.cost_per_share = round(pos.cost_per_share / (1 + action.share_ratio), 6)
            generated.append(ReplayTrade(stock_code=action.security_id, date=action.ex_date,
                                         action="BUY", shares=new_shares, price=0.0,
                                         note=f"送转股 +{new_shares} 股（成本价除权调整）——非现金交易"))
    return generated


class MarketStatusCheck:
    """停牌/IPO/退市状态检查（纯函数；真实历史状态数据缺失时 run 必须标 NON_STRICT
    ——不因接口存在就绿灯，R6 验收3）。"""

    @staticmethod
    def check_suspended(security_id: str, trade_date: str,
                        suspended_days: Optional[set]) -> None:
        """停牌日不可成交。suspended_days=None 表示状态数据缺失——本检查跳过但
        由调用方登记 NON_STRICT（缺数据不等于无停牌）。"""
        if suspended_days is None:
            return
        if trade_date in suspended_days:
            raise ValueError(f"停牌不可成交：{security_id} {trade_date}")

    @staticmethod
    def check_listed(security_id: str, trade_date: str, listing_date: Optional[str]) -> None:
        """上市日前不可买入（IPO 未上市）。listing_date=None → 数据缺失，调用方标 NON_STRICT。"""
        if listing_date is None:
            return
        if trade_date < listing_date:
            raise ValueError(f"IPO 未上市不可买入：{security_id} 上市 {listing_date}，交易日 {trade_date}")

    @staticmethod
    def check_not_delisted(security_id: str, trade_date: str,
                           delist_date: Optional[str]) -> None:
        """退市后不可成交；持仓在退市日按最后价格估值（虚构强平收益被禁止）。"""
        if delist_date is None:
            return
        if trade_date > delist_date:
            raise ValueError(f"已退市不可成交：{security_id} 退市 {delist_date}，交易日 {trade_date}")


# ──────────────── R7：manifest v2（冻结真实信息集，VALIDATION §3）────────────────

class ExperimentManifestV2(BaseModel):
    """实验 manifest v2（VALIDATION §3 扩展字段；旧 ExperimentManifest 保留读兼容）。

    核心纪律：temporal_eligibility **从每个输入的资格派生**，不按实验编号硬赋；
    人工挑选/参数试验次数/剔除记录全部可见。"""
    model_config = ConfigDict(extra="allow")

    experiment_id: str
    run_id: str = Field(default_factory=lambda: f"run_{datetime.now().strftime('%Y%m%d%H%M%S')}")
    preregistered_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: str = ""
    completed_at: str = ""
    code_commit: str = ""
    dirty_tree_fingerprint: str = ""
    schema_adapter_policy_versions: dict = Field(default_factory=dict)
    universe_snapshot_ids: list[str] = Field(default_factory=list)
    evidence_snapshot_ids: list[str] = Field(default_factory=list)
    price_corporate_action_rule_versions: dict = Field(default_factory=dict)
    selection_source: str = Field(default="", description="候选来源（scan规则/产业AI/质量抽样——分路登记）")
    mode_source: str = Field(default="", description="mode 来源（人工标注/规则代理/AI）")
    thesis_source: str = Field(default="", description="逻辑来源（用户计划/系统草稿/模拟）")
    extraction_source: str = Field(default="", description="提取来源（deterministic/LLM@model）")
    temporal_eligibility: Literal["STRICT", "CONTEMPORARY", "NON_STRICT", "BLOCKED_DATA"] = "NON_STRICT"
    temporal_eligibility_reasons: list[str] = Field(default_factory=list)
    unavailable_components: list[str] = Field(default_factory=list)
    human_overrides: list[str] = Field(default_factory=list, description="人工覆盖记录（谁/何时/改了什么）")
    arms_and_single_variation: list[dict] = Field(default_factory=list, description="臂清单（每臂单一变量）")
    costs: dict = Field(default_factory=dict, description="AI tokens/请求数/外源调用/失败次数")
    budgets: dict = Field(default_factory=dict, description="冻结预算（超限停止）")
    exclusions_and_reasons: list[dict] = Field(default_factory=list)
    primary_metric: str = ""
    guardrail_metrics: list[str] = Field(default_factory=list)
    inference_method: str = ""
    stop_rule: str = ""
    result_paths: list[str] = Field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        d = self.model_dump(mode="json")
        d.pop("run_id", None)
        return hashlib.sha256(json.dumps(d, ensure_ascii=False, sort_keys=True,
                                         default=str).encode("utf-8")).hexdigest()[:16]


def validate_resource_parity(manifest: ExperimentManifestV2) -> list[str]:
    """E1b 资源同价检查（VALIDATION §4 E1b）：三路可比性的机器化——
    「相同深研究名额」与「相同实际成本」两口径必须显式声明且不为空。
    精确匹配 E1b（不用 startswith——E10+ 不误套，监督员 P2）。"""
    problems = []
    if manifest.experiment_id == "E1b":
        budgets = manifest.budgets or {}
        if not budgets.get("deep_research_quota"):
            problems.append("E1b 缺 deep_research_quota（相同深研究名额口径未声明）")
        if not budgets.get("actual_cost_cap"):
            problems.append("E1b 缺 actual_cost_cap（相同实际成本口径未声明）")
        for arm in manifest.arms_and_single_variation:
            if not arm.get("extraction_source") and not arm.get("notes"):
                problems.append(f"臂 {arm.get('name')} 缺资源消耗登记")
    return problems


def derive_temporal_eligibility(input_eligibilities: list[str]) -> tuple[str, list[str]]:
    """temporal_eligibility 从输入资格派生（VALIDATION §3：不按实验编号硬赋）。

    规则：任一 BLOCKED_DATA → BLOCKED_DATA；任一 NON_STRICT → NON_STRICT；
    任一 CONTEMPORARY → CONTEMPORARY；全部 STRICT → STRICT。
    input_eligibilities：各输入源的资格标签（STRICT/CONTEMPORARY/NON_STRICT/BLOCKED_DATA）。"""
    tags = set(input_eligibilities) or {"NON_STRICT"}
    reasons: list[str] = []
    if "BLOCKED_DATA" in tags:
        tag = "BLOCKED_DATA"
        reasons.append("存在 BLOCKED_DATA 输入（数据源缺历史版本资格——R1 暂停点）")
    elif "NON_STRICT" in tags:
        tag = "NON_STRICT"
        reasons.append("存在 NON_STRICT 输入（latest-only/合成/状态数据缺失）")
    elif "CONTEMPORARY" in tags:
        tag = "CONTEMPORARY"
        reasons.append("存在 CONTEMPORARY 输入（当时捕获——只支持其后决策）")
    else:
        tag = "STRICT"
    return tag, reasons


# E2b/E3b/E4b/E5b/E6b/E7b 注册（VALIDATION §4 重定级口径；旧 E_SPEC 保留读兼容）
E_SPEC_V2: list[ExperimentSpec] = [
    ExperimentSpec(experiment_id="E0b", name="正确性定向场景",
                   fixed="同一冻结日历与价格（合成输入明确标注）",
                   varied="定向成交场景（同日退出/老仓可卖/末日估值/分红恒等/税费切换）",
                   answers="执行正确性逐笔差异按原因解释——不以收益无差证明没有问题",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["R6.replay"],
                   runnable_offline=True, status="runnable"),
    ExperimentSpec(experiment_id="E1b", name="候选召回同资源对照",
                   fixed="同一冻结市场全集、相同排除规则、同一 cutoff、预设研究总名额",
                   varied="技术/产业/质量单路 vs 并集（相同深研究名额+相同实际成本两口径）",
                   answers="同等资源下有没有额外的合格机会（非交集Jaccard）",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["F4.candidate_pool", "R4.recall"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E2b", name="研究资格增量",
                   fixed="共同候选集合、同一研究截止时点、相同后续交易政策",
                   varied="无新增资格门 vs MID 资格门 vs LONG 资格门（周期分别报告）",
                   answers="覆盖率/拒绝原因/入选分布；被拒候选保留 shadow outcome 检验误杀",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["R1.qualify", "R3.assertions"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E3b", name="持有纪律增量",
                   fixed="同一批达标标的、相同入场时点与初始资金；MID/LONG 分别检验",
                   varied="legacy vs 对应周期新纪律（记录每次 sell_path/逻辑失效/技术退出/受阻）",
                   answers="收益回撤之外计换手/过早退出机会损失/失效后滞留时长",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["R5.decision_policy"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E4b", name="技术择时增量",
                   fixed="同一资格候选、同样计划目标/退出纪律、相同规则版本费用",
                   varied="无技术择时的预设可交易时点 vs 技术条件入场/加仓（未触发保留现金入分母）",
                   answers="等待时间/错过机会/现金拖累——不只评价成功入场者",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["R4.relative_return_v2"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E5b", name="AI 证据与决策价值（两层）",
                   fixed="第一层冻结人工核验语料（公司/时间切分）；第二层冻结相同快照/候选/计划",
                   varied="A=确定性路径；B=A+LLM提取；C=B+独立反证核验预算",
                   answers="事实precision/拒识率/错误拒识/每有效变化成本——产出更多claim不算成功",
                   source_tag=InfoSetTag.AI_LOOKAHEAD, depends_on=["R2.verify_claim_tiered"],
                   runnable_offline=False, status="blocked_on_data"),
    ExperimentSpec(experiment_id="E6b", name="组合预算与执行",
                   fixed="事前动作意图流（分配/成交机制验证）与完整状态回放分开",
                   varied="预算约束臂（删除未来全区间最大回撤——固定预注册压力场景）",
                   answers="现金数量守恒/买入违规0/未知现金不给精确数量/粒度不再拒绝",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["R5.allocate", "R6.rules"],
                   runnable_offline=True, status="runnable"),
    ExperimentSpec(experiment_id="E7b", name="完整系统受控场景",
                   fixed="全链受控场景（合成输入明确标注）+ 同 cutoff 真实非空路径观察分开",
                   varied="必须命中 OPEN/ADD/HOLD/REDUCE/EXIT/WAIT/REVIEW 与 EXIT+BLOCKED；"
                          "预算含可行新增/不足最低申报/现金不足/未确认卖出不释放四案例",
                   answers="路径命中证据——真实市场缺失某类路径标 NOT_OBSERVED 不计已通过",
                   source_tag=InfoSetTag.RULE_PROXY, depends_on=["R3.bundle", "R5.allocate", "R6.replay"],
                   runnable_offline=True, status="runnable"),
]


def check_strict_eligibility_or_block(manifest: ExperimentManifestV2,
                                      input_eligibilities: list[str]) -> str:
    """E2b/E3b/E4b 数据资格门（R7 验收4）：无严格资料的子实验显式 BLOCKED_DATA——
    执行可完成部分前先拒绝，不「结构性收口」代替原效果验收。返回派生资格标签；
    BLOCKED_DATA 时抛 RuntimeError（调用方如实登记后终止）。"""
    tag, reasons = derive_temporal_eligibility(input_eligibilities)
    manifest.temporal_eligibility = tag
    manifest.temporal_eligibility_reasons = reasons
    if tag == "BLOCKED_DATA":
        raise RuntimeError(
            f"{manifest.experiment_id} BLOCKED_DATA：{'；'.join(reasons)}——"
            "严格口径实验保持阻断（R1 暂停点），可完成部分=场景/资格审计，不做收益结论")
    return tag


class FixedStressScenario(BaseModel):
    """预注册固定压力场景（E6b：删除未来全区间最大回撤输入的替代品）。

    每条压力损失率带估计截止日/窗口/方法版本——不取未来数据；场景在运行前冻结。"""
    model_config = ConfigDict(extra="allow")

    scenario_id: str
    frozen_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    estimate_cutoff: str = Field(description="压力估计的截止日（只用截止前信息）")
    window_days: int = Field(default=250, description="估计窗口（交易日）")
    method_version: str = "fixed_scenario_v1"
    rates: dict = Field(default_factory=dict, description="{security_id 或 行业: 压力损失率}")
    source: str = Field(default="", description="估计来源（历史窗口统计/用户风险档）")

    def rate_for(self, security_id: str, industry: str = "") -> Optional[float]:
        """查压力损失率：个股优先，行业兜底；两者皆无 → None（不给精确额度）。"""
        return self.rates.get(security_id, self.rates.get(f"industry:{industry}"))
