"""因子计算函数回归测试（plan/fusion，src/core/factor_compute.py）

锁死语义（按 factor_registry 登记表的 definition/missing_policy 逐条）：
1. earnings_quality：CFOToNP 比值；净利润≤0 → NOT_APPLICABLE+风险解释（不比值）
2. capital_return：ROE 口径 + 高杠杆 caveat；缺失 UNKNOWN
3. balance_risk：分量独立不合成；到期分布 UNKNOWN（数据源不可得不假装）
4. valuation_pe：亏损不用普通 PE（NOT_APPLICABLE）；正常输出 trailing PE
5. relative_trend：缺行业指数 UNKNOWN（不冒充）；窗口不足 UNKNOWN（不降窗硬算）
6. trading_capacity：停牌/缺成交额 UNKNOWN；比值+容量提示
7. 全部缺数据路径不补 0 不猜（G15）

纯内存测试，无网络。跑法：pytest tests/core/test_factor_compute.py -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.factor_compute import (
    UNKNOWN,
    balance_risk_v1,
    capital_return_v1,
    earnings_quality_v1,
    relative_trend_v1,
    trading_capacity_v1,
    valuation_pe_v1,
)


def _fin(**overrides):
    """financial_data.get_financial_quarterly 产出的最小模拟（茅台 2023Q4 量级）。"""
    base = {
        "metric": "netProfit", "value": 7.75e10, "unit": "元",
        "period_kind": "cumulative", "period_end": "2023-12-31",
        "published_at": "2024-04-03", "source_uri": "baostock.query_profit_data",
        "source_version": "baostock_financial_v1",
    }
    base.update(overrides)
    return base


def _fin_multi(**kw):
    return [
        _fin(metric="netProfit", value=kw.get("netProfit", 7.75e10)),
        _fin(metric="CFOToNP", value=kw.get("CFOToNP", 0.859), unit="倍"),
        _fin(metric="roeAvg", value=kw.get("roeAvg", 0.3618), unit="倍"),
        _fin(metric="currentRatio", value=kw.get("currentRatio", 4.62), unit="倍"),
        _fin(metric="quickRatio", value=kw.get("quickRatio", 3.67), unit="倍"),
        _fin(metric="liabilityToAsset", value=kw.get("liabilityToAsset", 0.18), unit="倍"),
    ]


# ── 1. earnings_quality ────────────────────────────────────

def test_earnings_quality_normal_ratio():
    r = earnings_quality_v1(_fin_multi())
    assert r.value == 0.859 and r.unit == "倍" and r.status == "OK"


def test_earnings_quality_negative_profit_no_ratio():
    """sector_growth_guard：净利润≤0 → 不做比值，输出风险解释。"""
    r = earnings_quality_v1(_fin_multi(netProfit=-5e8, CFOToNP=-0.2))
    assert r.status == "NOT_APPLICABLE" and r.value is None
    assert "利润为负" in r.note and "净流出" in r.note


def test_earnings_quality_missing_cfo_unknown():
    r = earnings_quality_v1([_fin(metric="netProfit", value=1e9)])
    assert r.status == UNKNOWN and "不猜" in r.note


# ── 2. capital_return ──────────────────────────────────────

def test_capital_return_roe_with_leverage_note():
    r = capital_return_v1(_fin_multi())
    assert r.value == 0.3618 and "ROE 口径" in r.note and "杠杆" in r.note


def test_capital_return_missing_unknown():
    assert capital_return_v1([_fin(metric="netProfit", value=1e9)]).status == UNKNOWN


# ── 3. balance_risk ────────────────────────────────────────

def test_balance_risk_components_independent_maturity_unknown():
    r = balance_risk_v1(_fin_multi())
    assert r.components["currentRatio"] == 4.62
    assert r.components["debt_maturity_profile"] == UNKNOWN  # 缺到期分布不假装
    assert "不合成" in r.note


def test_balance_risk_high_leverage_note():
    r = balance_risk_v1(_fin_multi(liabilityToAsset=0.78))
    assert "70%" in r.note


def test_balance_risk_all_missing_unknown():
    r = balance_risk_v1([_fin(metric="netProfit", value=1e9)])
    assert r.status == UNKNOWN


# ── 4. valuation_pe ────────────────────────────────────────

def test_valuation_pe_normal():
    r = valuation_pe_v1(price=1500.0, eps_ttm=59.49)
    assert r.value == 25.21 and "trailing PE" in r.note and "不是目标价" in r.note


def test_valuation_pe_loss_not_aplicable():
    r = valuation_pe_v1(price=10.0, eps_ttm=-0.5)
    assert r.status == "NOT_APPLICABLE" and "不用普通 PE" in r.note


def test_valuation_pe_missing_unknown():
    assert valuation_pe_v1(None, 59.49).status == UNKNOWN


# ── 5. relative_trend ──────────────────────────────────────

def test_relative_trend_normal():
    stock = [100 + i for i in range(61)]           # +60%
    index = [1000 + 5 * i for i in range(61)]      # +30%
    r = relative_trend_v1(stock, index)
    assert r.value == 30.0 and r.unit == "%"
    assert r.components["stock_return_pct"] == 60.0


def test_relative_trend_missing_index_unknown_not_spoofed():
    """missing_policy：行业指数缺失 → UNKNOWN（不用全市场指数冒充行业）。"""
    r = relative_trend_v1([100, 110], None)
    assert r.status == UNKNOWN and "冒充" in r.note


def test_relative_trend_insufficient_window_unknown():
    """窗口不足不降窗硬算。"""
    r = relative_trend_v1([100, 110], [1000, 1050], window=60)
    assert r.status == UNKNOWN and "不足 60 日" in r.note


# ── 6. trading_capacity ────────────────────────────────────

def test_trading_capacity_normal():
    r = trading_capacity_v1(avg_amount_20d=1e8, trade_amount=5e5)
    assert r.value == 0.005 and "充足" in r.note and r.components["status"] == "正常交易"
    r2 = trading_capacity_v1(avg_amount_20d=1e8, trade_amount=3e6)  # 3% → 一般
    assert "一般" in r2.note


def test_trading_capacity_suspended_unknown():
    r = trading_capacity_v1(1e8, 5e5, suspended=True)
    assert r.status == UNKNOWN and "停牌" in r.components["status"]


def test_trading_capacity_missing_amount_unknown_no_precision():
    """成交额未知不能默认正常——UNKNOWN + 不发精确指令。"""
    r = trading_capacity_v1(None, 5e5)
    assert r.status == UNKNOWN and "不发精确指令" in r.note
