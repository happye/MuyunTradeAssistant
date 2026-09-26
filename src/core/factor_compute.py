"""因子计算函数（plan/fusion E2/E4 前置，影子批登记的「接线批」主体）。

纪律（与 factor_registry.py 的分工）：
- factor_registry 只登记不计算（测试机器锁）——本模块是计算层，按登记表的
  definition/missing_policy 逐条实现，不许发明登记表之外的合成口径
- 全部**纯函数**：输入证据/行情数据，输出 FactorResult（value 或显式 UNKNOWN）——
  缺数据不补 0、不猜（G15）；经济假设是假设不是结论（VALIDATION §3）
- 信息集标签：rule_bz_proxy（金融数据 baostock 季频 pubDate PIT，探查实证）

v1 覆盖（登记表 8 因子中上游已就绪的 6 个）：
- earnings_quality_v1  ← CFOToNP（经营现金流/净利润，baostock 现成比值）
- capital_return_v1    ← roeAvg（ROE；ROIC 的投入资本分项 baostock 不可得→口径降级登记）
- balance_risk_v1      ← currentRatio/quickRatio/liabilityToAsset（到期分布不可得→UNKNOWN）
- valuation_range_v1   ← v1 只做 trailing PE 位置（可比样本集未接线，区间口径留待）
- relative_trend_v1    ← 纯函数（个股60日收益−行业指数收益）；行业映射未接线，
  调用方传指数序列（akshare index_hist_sw 探查可用）
- trading_capacity_v1  ← 拟交易金额/20日均成交额
- business_exposure_v1 / demand_change_v1：上游未取得（needs_data_probe=True）——
  不实现，不填通用数字强行纳入
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

UNKNOWN = "UNKNOWN"


@dataclass
class FactorResult:
    """单因子计算输出：value 或显式 UNKNOWN（missing_policy 落地形态）。"""

    factor_id: str
    value: Optional[float] = None
    unit: str = ""
    status: str = "OK"  # OK / UNKNOWN / NOT_APPLICABLE
    note: str = ""
    components: dict = field(default_factory=dict)  # 多分量因子（如 balance_risk）


def _get(records: list[dict], metric: str, period_end: Optional[str] = None) -> Optional[float]:
    """从 financial_data.get_financial_quarterly 产出里取最新值（可限报告期）。"""
    cands = [r for r in records if r.get("metric") == metric
             and (period_end is None or r.get("period_end") == period_end)]
    vals = [r["value"] for r in cands if r.get("value") is not None]
    return vals[-1] if vals else None


def earnings_quality_v1(financial_records: list[dict]) -> FactorResult:
    """盈利质量 = 经营现金流/净利润（CFOToNP，baostock 现成累计比值）。

    missing_policy（登记表）：净利润≤0 → 输出『利润为负+现金流方向』的风险解释，
    不做比值（sector_growth_guard 落地）。缺 CFO 或净利润 → UNKNOWN。"""
    cfo_np = _get(financial_records, "CFOToNP")
    net_profit = _get(financial_records, "netProfit")
    if net_profit is not None and net_profit <= 0:
        return FactorResult(
            factor_id="earnings_quality_v1", status="NOT_APPLICABLE",
            note=f"净利润为负（{net_profit:.0f} 元）——不做比值；"
                 f"现金流方向={'净流入' if (cfo_np or 0) > 0 else '净流出/未知'}（风险解释口径）",
            components={"netProfit": net_profit, "CFOToNP_raw": cfo_np})
    if cfo_np is None:
        return FactorResult(factor_id="earnings_quality_v1", status=UNKNOWN,
                            note="经营现金流/净利润缺失——不猜")
    return FactorResult(factor_id="earnings_quality_v1", value=round(cfo_np, 4), unit="倍",
                        note="≥1 利润有现金流支撑；<1 盈利未完全转化为现金（口径：年初累计）",
                        components={"CFOToNP": cfo_np})


def capital_return_v1(financial_records: list[dict]) -> FactorResult:
    """资本回报 = roeAvg（平均 ROE）。

    v1 口径降级（登记表允许）：ROIC 的投入资本分项 baostock 不可得 → ROE 口径，
    note 显式登记『非金融企业高杠杆会抬高 ROE，不做杠杆调整』；缺失 → UNKNOWN。"""
    roe = _get(financial_records, "roeAvg")
    if roe is None:
        return FactorResult(factor_id="capital_return_v1", status=UNKNOWN,
                            note="ROE 缺失——不猜")
    return FactorResult(factor_id="capital_return_v1", value=round(roe, 4), unit="倍",
                        note="v1=ROE 口径（ROIC 投入资本分项数据源不可得）；"
                             "高杠杆会抬高 ROE，跨行业比较需结合 liabilityToAsset",
                        components={"roeAvg": roe})


def balance_risk_v1(financial_records: list[dict]) -> FactorResult:
    """资产负债结构：流动/速动比率、资产负债率；到期分布数据源不可得 → UNKNOWN
    （missing_policy：缺到期分布不认定无偿债压力——该分量 UNKNOWN 并注明）。"""
    cur = _get(financial_records, "currentRatio")
    quick = _get(financial_records, "quickRatio")
    lev = _get(financial_records, "liabilityToAsset")
    components = {
        "currentRatio": cur, "quickRatio": quick, "liabilityToAsset": lev,
        "debt_maturity_profile": UNKNOWN,  # 数据源不可得（登记表 missing_policy）
    }
    if cur is None and quick is None and lev is None:
        return FactorResult(factor_id="balance_risk_v1", status=UNKNOWN,
                            note="资产负债分量全部缺失——不猜", components=components)
    notes = []
    if lev is not None and lev > 0.7:
        notes.append("资产负债率>70%（偏重，金融/地产口径外需注意）")
    return FactorResult(factor_id="balance_risk_v1", value=None, unit="各分量原生单位（倍）",
                        note="；".join(notes) or "分量独立输出不合成单一分（登记表口径）",
                        components=components)


def valuation_pe_v1(price: Optional[float], eps_ttm: Optional[float]) -> FactorResult:
    """估值 v1：trailing PE 位置（price / epsTTM）。

    登记表定义=『正常化盈利×可比倍数区间』——可比样本集未接线，v1 降级为单股 PE，
    note 如实登记；亏损企业不用普通 PE（sector_growth_guard）→ NOT_APPLICABLE。"""
    if price is None or eps_ttm is None:
        return FactorResult(factor_id="valuation_range_v1", status=UNKNOWN,
                            note="价格或 EPS TTM 缺失——不猜")
    if eps_ttm <= 0:
        return FactorResult(factor_id="valuation_range_v1", status="NOT_APPLICABLE",
                            note=f"EPS TTM={eps_ttm:.2f}≤0（亏损/微利）——不用普通 PE，转专门研究")
    return FactorResult(factor_id="valuation_range_v1", value=round(price / eps_ttm, 2),
                        unit="倍",
                        note="v1=trailing PE（可比样本区间口径未接线）；PE 是位置维度不是目标价",
                        components={"price": price, "epsTTM": eps_ttm})


def relative_trend_v1(stock_closes: list[float], industry_index_closes: Optional[list[float]],
                      window: int = 60) -> FactorResult:
    """相对趋势 = 个股近 N 日收益 − 行业指数近 N 日收益（Weinstein 量化投影）。

    行业映射未接线：指数序列由调用方传入（akshare index_hist_sw 探查可用）；
    缺指数 → UNKNOWN（missing_policy：不用全市场指数冒充行业）。数据不足窗口 → UNKNOWN。
    """
    if industry_index_closes is None or len(industry_index_closes) < 2:
        return FactorResult(factor_id="relative_trend_v1", status=UNKNOWN,
                            note="行业指数缺失——不用全市场指数冒充行业")
    if len(stock_closes) < 2:
        return FactorResult(factor_id="relative_trend_v1", status=UNKNOWN,
                            note="个股收盘序列缺失——不猜")
    n_stock = min(window + 1, len(stock_closes))
    n_idx = min(window + 1, len(industry_index_closes))
    r_stock = (stock_closes[-1] / stock_closes[-n_stock] - 1) * 100
    r_idx = (industry_index_closes[-1] / industry_index_closes[-n_idx] - 1) * 100
    if n_stock < window + 1 or n_idx < window + 1:
        return FactorResult(factor_id="relative_trend_v1", status=UNKNOWN,
                            note=f"序列不足 {window} 日（个股 {n_stock-1}/指数 {n_idx-1}）——不降窗硬算")
    return FactorResult(factor_id="relative_trend_v1", value=round(r_stock - r_idx, 2), unit="%",
                        note=f"近{window}日个股 {r_stock:+.1f}% − 行业指数 {r_idx:+.1f}%",
                        components={"stock_return_pct": round(r_stock, 2),
                                    "index_return_pct": round(r_idx, 2)})


def trading_capacity_v1(avg_amount_20d: Optional[float], trade_amount: Optional[float],
                        suspended: bool = False) -> FactorResult:
    """执行容量 = 拟交易金额 / 过去20完整交易日日均成交额（+停牌状态）。

    missing_policy：成交额未知不能默认正常——UNKNOWN + 不发精确指令；停牌 → 状态标注。"""
    status_note = "停牌" if suspended else "正常交易"
    if suspended:
        return FactorResult(factor_id="trading_capacity_v1", status=UNKNOWN,
                            note="停牌——不可交易（状态分量）", components={"status": status_note})
    if not avg_amount_20d or not trade_amount or avg_amount_20d <= 0 or trade_amount <= 0:
        return FactorResult(factor_id="trading_capacity_v1", status=UNKNOWN,
                            note="成交额未知不能默认正常——UNKNOWN + 不发精确指令",
                            components={"status": status_note})
    ratio = trade_amount / avg_amount_20d
    note = "容量充足（<1% 拟交易额占日均成交）" if ratio < 0.01 else \
           "容量一般（1%-5%）" if ratio < 0.05 else "容量紧张（>5% 拟交易额/日均成交——注意冲击成本）"
    return FactorResult(factor_id="trading_capacity_v1", value=round(ratio, 6), unit="倍",
                        note=note, components={"status": status_note})
