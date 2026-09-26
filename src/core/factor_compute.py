"""因子计算函数（plan/fusion F4 + iteration2 R4，RESEARCH_LOOP §3）。

纪律（与 factor_registry.py 的分工）：
- factor_registry 只登记不计算（测试机器锁）——本模块是计算层，按登记表的
  definition/missing_policy 逐条实现，不许发明登记表之外的合成口径
- 全部**纯函数**：输入证据/行情数据，输出 FactorResult（value 或显式 UNKNOWN）——
  缺数据不补 0、不猜（G15）；经济假设是假设不是结论（VALIDATION §3）
- 信息集标签：rule_bz_proxy（金融数据 baostock 季频 pubDate PIT，探针实证——
  注意 R1 后 latest-only 财务不进 strict 历史快照，本层产出属 live/研究口径）
- **R4 能力ID=真实计算**（A08 修复）：roe_observed_v1/pe_ttm_v1/cash_conversion_v1/
  relative_return_v2 是真实能力；capital_return_v1（ROIC）与 valuation_range_v1（区间）
  当前**不可计算**（登记表 needs_data_probe=True），代理不得自动满足——研究资格按
  能力ID匹配，返回非空数字不代表满足某个研究能力
- 旧函数名（capital_return_v1/earnings_quality_v1/valuation_pe_v1）保留为**弃用委托**
  （E1/E7 脚本迁移前兼容，R7 迁移后删除）——返回新能力ID的 FactorResult

覆盖（登记表可计算的 6+1 个）：
- cash_conversion_v1  ← CFOToNP（含接近零分母防护）
- roe_observed_v1     ← roeAvg（ROE 观察值）
- balance_risk_v1     ← currentRatio/quickRatio/liabilityToAsset（到期分布不可得→UNKNOWN）
- pe_ttm_v1           ← trailing PE（price/epsTTM）
- relative_return_v2  ← 日期对齐的相对收益（停牌缺 bar 显式处理）
- trading_capacity_v1 ← 拟交易金额/20日均成交额
- business_exposure_v1 / demand_change_v1 / capital_return_v1 / valuation_range_v1：
  上游未取得——不实现，不填通用数字强行纳入
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
    """从 financial_data.get_financial_quarterly 产出里取最新值（可限报告期）。

    「最新」按 published_at 排序判定（审查 P2：多期拼接顺序无契约，不依赖入参顺序）。"""
    cands = [r for r in records if r.get("metric") == metric
             and (period_end is None or r.get("period_end") == period_end)
             and r.get("value") is not None]
    if not cands:
        return None
    cands.sort(key=lambda r: str(r.get("published_at") or ""))
    return cands[-1]["value"]


def cash_conversion_v1(financial_records: list[dict]) -> FactorResult:
    """现金转化 = 经营现金流/净利润（CFOToNP，baostock 现成累计比值）。

    missing_policy（登记表）：净利润≤0 或接近零 → 输出『利润为负/微利+现金流方向』
    的风险解释，不做比值（sector_growth_guard 落地——接近零分母的比值无意义且易误读）。
    缺 CFO 或净利润 → UNKNOWN。"""
    cfo_np = _get(financial_records, "CFOToNP")
    net_profit = _get(financial_records, "netProfit")
    if net_profit is not None and net_profit <= 0:
        return FactorResult(
            factor_id="cash_conversion_v1", status="NOT_APPLICABLE",
            note=f"净利润为负（{net_profit:.0f} 元）——不做比值；"
                 f"现金流方向={'净流入' if (cfo_np or 0) > 0 else '净流出/未知'}（风险解释口径）",
            components={"netProfit": net_profit, "CFOToNP_raw": cfo_np})
    if net_profit is not None and abs(net_profit) < 1e4:
        # 接近零分母（<1万元）：比值数值失真（微利/一次性损益），转人工口径
        return FactorResult(
            factor_id="cash_conversion_v1", status="NOT_APPLICABLE",
            note=f"净利润接近零（{net_profit:.0f} 元，含一次性损益时比值失真）——不做比值，转专门研究",
            components={"netProfit": net_profit, "CFOToNP_raw": cfo_np})
    if cfo_np is None:
        return FactorResult(factor_id="cash_conversion_v1", status=UNKNOWN,
                            note="经营现金流/净利润缺失——不猜")
    return FactorResult(factor_id="cash_conversion_v1", value=round(cfo_np, 4), unit="倍",
                        note="≥1 利润有现金流支撑；<1 盈利未完全转化为现金（口径：年初累计）",
                        components={"CFOToNP": cfo_np})


def roe_observed_v1(financial_records: list[dict]) -> FactorResult:
    """ROE 观察值 = roeAvg（平均净资产收益率）——独立能力ID（不冒 capital_return/ROIC 名义）。

    note 显式登记『非金融企业高杠杆会抬高 ROE，不做杠杆调整』；缺失 → UNKNOWN。"""
    roe = _get(financial_records, "roeAvg")
    if roe is None:
        return FactorResult(factor_id="roe_observed_v1", status=UNKNOWN,
                            note="ROE 缺失——不猜")
    return FactorResult(factor_id="roe_observed_v1", value=round(roe, 4), unit="倍",
                        note="ROE 观察值（roeAvg）；高杠杆会抬高 ROE，跨行业比较需结合 liabilityToAsset——"
                             "不自动满足 ROIC（capital_return_v1）能力",
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


def pe_ttm_v1(price: Optional[float], eps_ttm: Optional[float]) -> FactorResult:
    """估值位置 v1：trailing PE（price / epsTTM）——独立能力ID，不冒 valuation_range（区间）名义。

    登记表的区间口径=『正常化盈利×可比倍数区间』——可比样本集未接线不可计算；
    本因子只产当前位置观察。亏损企业不用普通 PE（sector_growth_guard）→ NOT_APPLICABLE。"""
    if price is None or eps_ttm is None:
        return FactorResult(factor_id="pe_ttm_v1", status=UNKNOWN,
                            note="价格或 EPS TTM 缺失——不猜")
    if eps_ttm <= 0:
        return FactorResult(factor_id="pe_ttm_v1", status="NOT_APPLICABLE",
                            note=f"EPS TTM={eps_ttm:.2f}≤0（亏损/微利）——不用普通 PE，转专门研究")
    return FactorResult(factor_id="pe_ttm_v1", value=round(price / eps_ttm, 2),
                        unit="倍",
                        note="trailing PE 位置观察——不自动满足 valuation_range_v1（区间）能力",
                        components={"price": price, "epsTTM": eps_ttm})


def relative_return_v2(stock_bars: list[dict], index_bars: list[dict],
                       window: int = 60, *, max_missing_pct: float = 0.2) -> FactorResult:
    """相对收益 v2 = 个股近 N 个指数交易日收益 − 行业指数收益（**按交易日期对齐**）。

    R4（验收2）：输入必须是带日期的 bar 序列 [{date, close}]——两条无日期数组各自
    取窗口的「伪同窗」被本函数取代。对齐规则（RESEARCH_LOOP §3 声明）：
    - 指数交易日为日历（指数正常交易、无停牌概念）；统一截止日 = 两序列共同最大日期，
      窗口 = 截止日起往前的 N 个指数交易日
    - 个股停牌缺 bar：窗口两端点（起点/终点）必须有个股**真实 bar**（缺 → UNKNOWN，
      不前复权不填假价）；区间内部缺 bar 计数声明，占比 > max_missing_pct → UNKNOWN
    - 对齐失败（日期不可解析/共同日期不足）→ UNKNOWN
    """
    from datetime import datetime as _dt

    def _as_map(bars: list[dict]) -> dict[str, float]:
        out = {}
        for b in bars or []:
            d = str(b.get("date") or b.get("period_end") or "").strip()
            c = b.get("close")
            if d and c is not None:
                try:
                    _dt.fromisoformat(d)
                    out[d] = float(c)
                except ValueError:
                    continue  # 不可解析日期丢弃（坏数据不猜）
        return out

    stock_map, index_map = _as_map(stock_bars), _as_map(index_bars)
    if not index_map or not stock_map:
        return FactorResult(factor_id="relative_return_v2", status=UNKNOWN,
                            note="个股或行业指数序列缺失——不用全市场指数冒充行业")
    common_dates = sorted(set(stock_map) & set(index_map))
    if len(common_dates) < 2:
        return FactorResult(factor_id="relative_return_v2", status=UNKNOWN,
                            note="日期对齐失败：共同交易日不足（停牌/日历错位）——不猜")
    end = common_dates[-1]
    dates = common_dates[-(window + 1):]  # 共同交易日上的窗口
    if len(dates) < window + 1:
        return FactorResult(factor_id="relative_return_v2", status=UNKNOWN,
                            note=f"共同交易日不足 {window} 日（仅 {len(dates)-1}）——不降窗硬算")
    start = dates[0]
    ps, pe_, cs, ce = stock_map[start], stock_map[end], index_map[start], index_map[end]
    if ps <= 0 or pe_ <= 0 or cs <= 0 or ce <= 0:
        return FactorResult(factor_id="relative_return_v2", status=UNKNOWN,
                            note="序列含零/负价（坏数据）——不猜")
    # 停牌声明：指数日历在窗口内的交易日中，个股缺 bar 的占比
    idx_dates_full = sorted(index_map)
    window_idx = idx_dates_full[idx_dates_full.index(end) - window: idx_dates_full.index(end) + 1]
    missing = [d for d in window_idx if d not in stock_map]
    if len(missing) > len(window_idx) * max_missing_pct:
        return FactorResult(factor_id="relative_return_v2", status=UNKNOWN,
                            note=f"窗口内个股缺 {len(missing)}/{len(window_idx)} 个交易日"
                                 f"（超 {max_missing_pct:.0%}，疑似长期停牌）——对齐失败不硬算")
    r_stock = (pe_ / ps - 1) * 100
    r_idx = (ce / cs - 1) * 100
    note = (f"近{window}个指数交易日（{start}→{end}）个股 {r_stock:+.1f}% − 行业指数 {r_idx:+.1f}%"
            + (f"；区间停牌缺 {len(missing)} 日（两端点真实成交）" if missing else ""))
    return FactorResult(factor_id="relative_return_v2", value=round(r_stock - r_idx, 2), unit="%",
                        note=note,
                        components={"stock_return_pct": round(r_stock, 2),
                                    "index_return_pct": round(r_idx, 2),
                                    "missing_bars": len(missing),
                                    "window_start": start, "window_end": end})


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
           "容量一般（1%-5%）" if ratio < 0.05 else "容量紧张（≥5% 拟交易额/日均成交——注意冲击成本）"
    return FactorResult(factor_id="trading_capacity_v1", value=round(ratio, 6), unit="倍",
                        note=note, components={"status": status_note})


# ──────────────── 弃用委托（E1/E7 脚本迁移前兼容；R7 迁移后删除）────────────────

def capital_return_v1(financial_records: list[dict]) -> FactorResult:
    """弃用（R4 A08）：capital_return_v1=ROIC 能力，当前不可计算——本函数只兼容旧调用，
    返回 roe_observed_v1 的真实结果（factor_id 已改为真实能力ID）。"""
    r = roe_observed_v1(financial_records)
    return FactorResult(factor_id=r.factor_id, value=r.value, unit=r.unit, status=r.status,
                        note=r.note + "（经弃用委托 capital_return_v1 调用——请改用 roe_observed_v1）",
                        components=r.components)


def earnings_quality_v1(financial_records: list[dict]) -> FactorResult:
    """弃用（R4）：现金转化计算承接至 cash_conversion_v1——本函数只兼容旧调用。"""
    r = cash_conversion_v1(financial_records)
    return FactorResult(factor_id=r.factor_id, value=r.value, unit=r.unit, status=r.status,
                        note=r.note + "（经弃用委托 earnings_quality_v1 调用——请改用 cash_conversion_v1）",
                        components=r.components)


def valuation_pe_v1(price: Optional[float], eps_ttm: Optional[float]) -> FactorResult:
    """弃用（R4）：trailing PE 计算承接至 pe_ttm_v1——本函数只兼容旧调用。"""
    r = pe_ttm_v1(price, eps_ttm)
    return FactorResult(factor_id=r.factor_id, value=r.value, unit=r.unit, status=r.status,
                        note=r.note + "（经弃用委托 valuation_pe_v1 调用——请改用 pe_ttm_v1）",
                        components=r.components)
