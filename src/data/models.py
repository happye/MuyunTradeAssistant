"""数据模型定义 - Pydantic"""

from typing import Optional
from pydantic import BaseModel, Field
from enum import Enum


class SignalType(str, Enum):
    """信号类型"""
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    WATCH = "WATCH"  # 观望


class MarketState(str, Enum):
    """市场状态"""
    RISK_ON = "RISK_ON"       # 积极状态（多数维度看多）
    RISK_OFF = "RISK_OFF"     # 谨慎状态（多数维度看空）
    PANIC = "PANIC"           # 恐慌状态（极端下跌）
    TRANSITION = "TRANSITION"  # 过渡状态（多空分歧，趋势不明）


class PositionAction(str, Enum):
    """仓位动作"""
    OPEN = "OPEN"                    # 试探建仓（首次买入）
    ADD = "ADD"                      # 加仓（趋势确认后追加）
    REDUCE = "REDUCE"                # 减仓（分批止盈等）
    CLOSE_ALL = "CLOSE_ALL"          # 全部清仓（止损/趋势反转）
    HOLD_POSITION = "HOLD_POSITION"  # 维持当前仓位
    STAY_OUT = "STAY_OUT"            # 空仓观望


class StockData(BaseModel):
    """股票数据结构"""
    stock_code: str = Field(description="股票代码，如 600519")
    stock_name: str = Field(description="股票名称，如 贵州茅台")

    # 价格数据
    price: float = Field(description="当前价格")
    open: Optional[float] = Field(default=None, description="开盘价")
    high: Optional[float] = Field(default=None, description="最高价")
    low: Optional[float] = Field(default=None, description="最低价")

    # 均线数据
    ma5: Optional[float] = Field(default=None, description="5日均线")
    ma10: Optional[float] = Field(default=None, description="10日均线")
    ma20: Optional[float] = Field(default=None, description="20日均线")
    ma60: Optional[float] = Field(default=None, description="60日均线")
    ma120: Optional[float] = Field(default=None, description="120日均线")
    ma200: Optional[float] = Field(default=None, description="200日均线")

    # 成交量数据
    volume: float = Field(description="当前成交量")
    avg_volume_5: Optional[float] = Field(default=None, description="5日均量")
    avg_volume_20: Optional[float] = Field(default=None, description="20日均量")

    # 常用指标
    change_pct: Optional[float] = Field(default=None, description="涨跌幅(%)")

    # 位置信息
    high_60d: Optional[float] = Field(default=None, description="60日高点")
    low_60d: Optional[float] = Field(default=None, description="60日低点")
    high_120d: Optional[float] = Field(default=None, description="120日高点")
    low_120d: Optional[float] = Field(default=None, description="120日低点")

    # ===== MACD指标 =====
    macd_dif: Optional[float] = Field(default=None, description="MACD-DIF差值线")
    macd_dea: Optional[float] = Field(default=None, description="MACD-DEA信号线")
    macd_hist: Optional[float] = Field(default=None, description="MACD柱状图")

    # ===== RSI指标 =====
    rsi_6: Optional[float] = Field(default=None, description="RSI-6日")
    rsi_12: Optional[float] = Field(default=None, description="RSI-12日")
    rsi_24: Optional[float] = Field(default=None, description="RSI-24日")

    # ===== 布林带指标 =====
    boll_upper: Optional[float] = Field(default=None, description="布林带上轨")
    boll_mid: Optional[float] = Field(default=None, description="布林带中轨")
    boll_lower: Optional[float] = Field(default=None, description="布林带下轨")

    # ===== KDJ指标 =====
    kdj_k: Optional[float] = Field(default=None, description="KDJ-K值")
    kdj_d: Optional[float] = Field(default=None, description="KDJ-D值")
    kdj_j: Optional[float] = Field(default=None, description="KDJ-J值")

    # 多时间框架数据
    monthly: Optional[dict] = Field(default=None, description="月线数据")
    weekly: Optional[dict] = Field(default=None, description="周线数据")

    # ===== 大盘环境数据 =====
    index_trend: Optional[str] = Field(default=None, description="大盘趋势: BULLISH/BEARISH/NEUTRAL")
    index_ma20: Optional[float] = Field(default=None, description="沪深300 MA20")
    index_ma60: Optional[float] = Field(default=None, description="沪深300 MA60")
    index_ma250: Optional[float] = Field(default=None, description="沪深300 MA250(年线，牛熊分界线)")
    index_close: Optional[float] = Field(default=None, description="沪深300最新收盘价")
    index_change_pct: Optional[float] = Field(default=None, description="沪深300涨跌幅(%)")
    index_high_250d: Optional[float] = Field(default=None, description="沪深300近250日最高价(用于计算回撤幅度)")

    class Config:
        extra = "allow"  # 允许额外字段


class SkillSignal(BaseModel):
    """技能信号输出"""
    skill_name: str = Field(description="技能名称")
    skill_alias: str = Field(description="技能中文别名")
    signal: SignalType = Field(description="信号类型")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度 0-1")
    reason: list[str] = Field(default_factory=list, description="判断理由")
    metadata: dict = Field(default_factory=dict, description="额外数据")
    skill_type: str = Field(default="base", description="技能类型: base/regulator/action")
    position_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="建议仓位比例(0-1)，None表示不指定")


class DecisionTrace(BaseModel):
    """决策追溯 - 记录决策引擎每一步的状态"""
    step: str = Field(description="步骤名称")
    description: str = Field(description="步骤描述")
    data: dict = Field(default_factory=dict, description="步骤数据（如分数、系数等）")


class DecisionResult(BaseModel):
    """最终决策结果"""
    stock: StockData = Field(description="股票数据")
    state: MarketState = Field(description="市场状态")
    decision: SignalType = Field(description="最终决策")
    score: float = Field(ge=0.0, le=1.0, description="综合评分")
    signals: list[SkillSignal] = Field(default_factory=list, description="各技能信号")
    reason: list[str] = Field(default_factory=list, description="决策理由")
    warnings: list[str] = Field(default_factory=list, description="风险提示")
    trace: list[DecisionTrace] = Field(default_factory=list, description="决策追溯路径")
    position_action: PositionAction = Field(default=PositionAction.STAY_OUT, description="仓位动作")
    position_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="建议仓位比例(0-1)")


# ===== 回测相关模型 =====

class TradeRecord(BaseModel):
    """单笔交易记录"""
    date: str = Field(description="交易日期 YYYY-MM-DD")
    action: str = Field(description="操作: BUY/SELL")
    price: float = Field(description="成交价格")
    shares: int = Field(description="成交股数(手×100)")
    amount: float = Field(description="成交金额")
    reason: str = Field(default="", description="交易原因")
    signal_score: float = Field(default=0.0, description="信号评分")
    position_action: str = Field(default="", description="仓位动作: OPEN/ADD/REDUCE/CLOSE_ALL")
    position_ratio_after: float = Field(default=0.0, description="交易后仓位比例")


class DailySnapshot(BaseModel):
    """每日账户快照"""
    date: str = Field(description="日期")
    price: float = Field(description="当日收盘价")
    cash: float = Field(description="现金")
    position: int = Field(default=0, description="持仓股数")
    market_value: float = Field(default=0.0, description="持仓市值")
    total_value: float = Field(description="总资产(现金+市值)")
    return_pct: float = Field(default=0.0, description="累计收益率%")


class BacktestResult(BaseModel):
    """回测结果"""
    stock_code: str = Field(description="股票代码")
    stock_name: str = Field(default="", description="股票名称")
    start_date: str = Field(description="回测起始日期")
    end_date: str = Field(description="回测结束日期")
    initial_capital: float = Field(description="初始资金")
    final_value: float = Field(description="最终总资产")
    total_return_pct: float = Field(description="总收益率%")
    annualized_return_pct: float = Field(default=0.0, description="年化收益率%")
    max_drawdown_pct: float = Field(default=0.0, description="最大回撤%")
    win_rate: float = Field(default=0.0, description="胜率%")
    profit_loss_ratio: float = Field(default=0.0, description="盈亏比")
    sharpe_ratio: float = Field(default=0.0, description="夏普比率")
    total_trades: int = Field(default=0, description="总交易次数")
    buy_count: int = Field(default=0, description="买入次数")
    sell_count: int = Field(default=0, description="卖出次数")
    trades: list[TradeRecord] = Field(default_factory=list, description="交易记录")
    daily_snapshots: list[DailySnapshot] = Field(default_factory=list, description="每日快照")
    benchmark_return_pct: float = Field(default=0.0, description="基准收益率%(买入持有)")
    invested_return_pct: float = Field(default=0.0, description="投入资金收益率%(盈利/实际投入成本)")
