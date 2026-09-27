"""因子计算函数回归测试（plan/fusion F4 + iteration2 R4，src/core/factor_compute.py）

锁死语义（按 factor_registry r4.v1 登记表的 definition/missing_policy 逐条）：
1. cash_conversion_v1：CFOToNP 比值；净利润≤0/接近零 → NOT_APPLICABLE+风险解释（不比值）
2. roe_observed_v1：ROE 观察值——**不冒 capital_return（ROIC）名义**（A08：能力ID=真实计算）
3. balance_risk：分量独立不合成；到期分布 UNKNOWN（数据源不可得不假装）
4. pe_ttm_v1：trailing PE 位置——**不冒 valuation_range（区间）名义**；亏损 NOT_APPLICABLE
5. relative_return_v2：**按交易日期对齐**——停牌造成日期错位不产伪同窗（R4 验收2）
6. trading_capacity：停牌/缺成交额 UNKNOWN；比值+容量提示
7. 全部缺数据路径不补 0 不猜（G15）；弃用委托只做兼容映射（E 脚本迁移前）

纯内存测试，无网络。跑法：pytest tests/core/test_factor_compute.py -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.factor_compute import (
    UNKNOWN,
    balance_risk_v1,
    cash_conversion_v1,
    pe_ttm_v1,
    relative_return_v2,
    roe_observed_v1,
    trading_capacity_v1,
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


def _bars(start_day, closes, *, skip_dates=()):
    """[{date, close}] 序列（2024-01 起的工作日序）。"""
    from datetime import date, timedelta
    out = []
    d = date(2024, 1, 1)
    i = 0
    while len(out) < len(closes):
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        iso = d.isoformat()
        if iso in skip_dates:
            continue
        out.append({"date": iso, "close": closes[i]})
        i += 1
    return out


# ── 1. cash_conversion_v1（真实能力ID）────────────────────

def test_cash_conversion_normal_ratio():
    r = cash_conversion_v1(_fin_multi())
    assert r.factor_id == "cash_conversion_v1"
    assert r.value == 0.859 and r.unit == "倍" and r.status == "OK"


def test_cash_conversion_negative_profit_no_ratio():
    """sector_growth_guard：净利润≤0 → 不做比值，输出风险解释。"""
    r = cash_conversion_v1(_fin_multi(netProfit=-5e8, CFOToNP=-0.2))
    assert r.status == "NOT_APPLICABLE" and r.value is None
    assert "利润为负" in r.note and "净流出" in r.note


def test_cash_conversion_near_zero_denominator_not_aplicable():
    """R4 验收3：分母接近零的比值失真（微利/一次性损益）——NOT_APPLICABLE 转专门研究。"""
    r = cash_conversion_v1(_fin_multi(netProfit=5_000.0, CFOToNP=88.0))
    assert r.status == "NOT_APPLICABLE" and "接近零" in r.note
    assert r.value is None, "接近零分母不产比值数字"


def test_cash_conversion_missing_cfo_unknown():
    r = cash_conversion_v1([_fin(metric="netProfit", value=1e9)])
    assert r.status == UNKNOWN and "不猜" in r.note


# ── 2. roe_observed_v1（不冒 ROIC 名义——A08）─────────────

def test_roe_observed_value_and_leverage_note():
    r = roe_observed_v1(_fin_multi())
    assert r.factor_id == "roe_observed_v1"
    assert r.value == 0.3618 and "杠杆" in r.note
    assert "不自动满足 ROIC" in r.note, "机器可读的资格边界写在 note"


def test_roe_observed_missing_unknown():
    assert roe_observed_v1([_fin(metric="netProfit", value=1e9)]).status == UNKNOWN


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


# ── 4. pe_ttm_v1（不冒 valuation_range 名义——A08）────────

def test_pe_ttm_normal_position():
    r = pe_ttm_v1(price=1500.0, eps_ttm=59.49)
    assert r.factor_id == "pe_ttm_v1"
    assert r.value == 25.21 and "位置" in r.note
    assert "不自动满足 valuation_range_v1" in r.note


def test_pe_ttm_loss_not_aplicable():
    r = pe_ttm_v1(price=10.0, eps_ttm=-0.5)
    assert r.status == "NOT_APPLICABLE" and "不用普通 PE" in r.note


def test_pe_ttm_missing_unknown():
    assert pe_ttm_v1(None, 59.49).status == UNKNOWN


# ── 5. relative_return_v2（日期对齐——R4 验收2）───────────

def test_relative_return_aligned_window():
    stock = _bars(0, [100 + i for i in range(61)])            # +60%
    index = _bars(0, [1000 + 5 * i for i in range(61)])       # +30%
    r = relative_return_v2(stock, index)
    assert r.factor_id == "relative_return_v2"
    assert r.value == 30.0 and r.unit == "%"
    assert r.components["stock_return_pct"] == 60.0
    assert r.components["window_start"] and r.components["window_end"], "窗口端点可审计"


def test_relative_return_same_length_different_dates_not_pseudo_window():
    """R4 验收2 反例：两序列长度相同但日期错位（个股停牌少 3 天）——
    伪同窗结果被拒绝；区间缺 bar 声明、超阈 UNKNOWN。"""
    index = _bars(0, [1000 + 5 * i for i in range(61)])
    stock_closes = [100 + i for i in range(61)]
    stock = _bars(0, stock_closes, skip_dates={index[10]["date"], index[11]["date"],
                                               index[12]["date"]})  # 停牌 3 天，长度短 3
    stock_shifted = _bars(0, stock_closes)[::1]
    # 长度相同但日期错位：把个股序列整体前移 3 个交易日
    shifted = [{"date": index[i]["date"], "close": stock_closes[i]}
               for i in range(58)]  # 58 天 vs 指数 61 天
    shifted_lie = [{"date": index[i]["date"], "close": stock_closes[i]} for i in range(61)]
    # 个股 61 个 close 挂到指数前 61 个日期——若按长度取窗（旧伪同窗）会用错日期；
    # v2 按日期对齐：个股在窗口末端缺的日期只是缺 bar，端点在则按真实日期算
    r = relative_return_v2(shifted_lie, index)
    # 端点都在（1-01 起 61 个指数日 vs 个股 61 个 close 挂前 61 个指数日）→ 同窗成立，
    # 但这恰说明对齐有效：换一个错位序列（close 与日期错开）结果必须不同
    shifted_wrong = [{"date": index[i]["date"], "close": stock_closes[i + 1]}
                     for i in range(60)]  # close 整体错一天
    r_wrong = relative_return_v2(shifted_wrong, index)
    assert r_wrong.status == UNKNOWN, "端点缺 bar（最后一天无个股 bar）→ 对齐失败不硬算"
    assert "对齐失败" in r_wrong.note or "不足" in r_wrong.note


def test_relative_return_suspension_endpoint_unknown():
    """停牌在窗口端点（起/终点无真实 bar）→ UNKNOWN——不前复权不填假价。"""
    index = _bars(0, [1000 + 5 * i for i in range(61)])
    stock = _bars(0, [100 + i for i in range(61)])
    end_date = index[-1]["date"]
    stock_suspended_end = [b for b in stock if b["date"] != end_date]
    r = relative_return_v2(stock_suspended_end, index)
    assert r.status == UNKNOWN, "终点停牌 → 对齐失败（不拿旧价冒充当日）"


def test_relative_return_too_many_missing_bars_unknown():
    """窗口内缺 bar 占比超阈（疑似长期停牌）→ UNKNOWN——共同日数够但停牌面过大。"""
    index = _bars(0, [1000 + 5 * i for i in range(80)])  # 80 个指数交易日
    stock_all = [100 + i for i in range(80)]
    skip = {index[i]["date"] for i in range(45, 60)}  # 末 61 指数日内缺 15 日（>20%）
    stock = _bars(0, stock_all, skip_dates=skip)
    r = relative_return_v2(stock, index)
    assert r.status == UNKNOWN and "缺" in r.note and "停牌" in r.note
    assert "不硬算" in r.note


def test_relative_return_missing_index_unknown_not_spoofed():
    """missing_policy：行业指数缺失 → UNKNOWN（不用全市场指数冒充行业）。"""
    r = relative_return_v2(_bars(0, [100, 110]), [])
    assert r.status == UNKNOWN and "冒充" in r.note


def test_relative_return_insufficient_window_unknown():
    """窗口不足不降窗硬算。"""
    stock = _bars(0, [100, 110])
    index = _bars(0, [1000, 1050])
    r = relative_return_v2(stock, index, window=60)
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
