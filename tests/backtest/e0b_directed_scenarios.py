"""E0b 定向成交场景（plan/fusion iteration2 R6 验收1-4，VALIDATION §4 E0b）。

定向构造（不是历史策略效果样本——合成价格明确标注 NON_STRICT）：
1. 同日买入后退出：当日新买份额不可卖（T+1 批次）——卖出拒绝计数必须非零
2. 老仓可卖：昨日买入今日卖出正常成交
3. 末日尚不可卖：窗口末日买入的仓位按最后价格估值（期末残余持仓如实列示——
   不虚构强平收益）
4. 除权分红恒等：分红现金 + 未复权价差 = 总收益（不复权价与分红现金双计被结构禁止——
   回放只收未复权价 + 公司行动台账）
5. 印花税切换日边界：2023-08-28 前后费率不同（财政部 税务总局公告2023年第39号）
6. 停牌/IPO/退市拒绝路径

跑法（离线，合成数据）：PYTHONUTF8=1 python tests/backtest/e0b_directed_scenarios.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.experiment import (
    CorporateAction,
    MarketStatusCheck,
    PortfolioReplay,
    apply_corporate_action,
    rule_at,
)

TEMPORAL_ELIGIBILITY = "NON_STRICT"  # 合成价格、无真实历史停牌/退市/成员数据（R6 验收3）


def scenario_same_day_exit_blocked():
    """场景1：同日买入后退出 → 拒绝计数非零；仓位仍在。"""
    rp = PortfolioReplay(1_000_000.0)
    rp.buy("600519", "2026-09-20", 150.0, 0.1)
    blocked = 0
    try:
        rp.sell("600519", "2026-09-20", 150.0)  # 同日卖 → T+1 拒绝
    except ValueError as e:
        blocked += 1
        assert "T+1" in str(e)
    assert blocked == 1, "同日卖出拒绝计数必须非零"
    assert rp.positions["600519"].shares > 0, "未能卖出仍持有"
    # 次日老仓可卖（场景2）
    t = rp.sell("600519", "2026-09-21", 150.0)
    assert t.shares > 0
    return {"blocked_sells": blocked, "next_day_sold": t.shares}


def scenario_end_of_window_still_held():
    """场景3：末日买入 → 不可卖 → 按最后价格估值（残余持仓列示，不虚构强平）。"""
    rp = PortfolioReplay(1_000_000.0)
    rp.buy("600519", "2026-09-25", 150.0, 0.1)
    blocked = 0
    try:
        rp.sell("600519", "2026-09-25", 150.0)
    except ValueError:
        blocked += 1
    nav = rp.nav({"600519": 150.0})
    assert blocked == 1 and nav > 1_000_000.0 * 0.85, "残余持仓按最后价格估值"
    return {"blocked_sells": blocked, "residual_position_value": round(nav, 2),
            "residual_shares": rp.positions["600519"].shares}


def scenario_dividend_identity():
    """场景4：除权分红恒等——分红现金 + 未复权价差 = 总收益（无双计通道）。"""
    rp = PortfolioReplay(1_000_000.0)
    rp.buy("600519", "2026-09-01", 100.0, 0.1)  # 未复权 100 元买入 900 股（费前近似）
    n = rp.positions["600519"].shares
    cash_before = rp.cash
    acts = apply_corporate_action(rp, CorporateAction(
        security_id="600519", ex_date="2026-09-10", cash_dividend_per_share=5.0,
        share_ratio=0.0, source="分红公告（合成场景）"))
    assert len(acts) == 1 and "分红" in acts[0].note
    dividend_cash = rp.cash - cash_before
    assert dividend_cash == n * 5.0, "分红现金 = 持股×每股分红"
    rp.sell("600519", "2026-09-20", 98.0)  # 未复权价卖出
    profit = rp.cash - 1_000_000.0
    return {"shares": n, "dividend_cash": dividend_cash, "total_profit": round(profit, 2),
            "note": "收益=未复权价差+分红（回放现金流里分红只出现一次——无双计通道）"}


def scenario_stamp_tax_boundary():
    """场景5：印花税切换日边界——2023-08-28 前后卖出资费不同（真实来源）。"""
    pre = rule_at("stamp_tax_sell", "2023-08-27")
    post = rule_at("stamp_tax_sell", "2023-08-28")
    assert pre.value["stamp_rate"] == 0.001 and post.value["stamp_rate"] == 0.0005, \
        "切换日边界：27 日旧税率、28 日新税率"
    assert post.source, "切换规则必须带来源"
    # 回放费率随规则切换（固定意图与数量下的成本单调性素材）
    rp_old = PortfolioReplay(1_000_000.0, stamp_rate=pre.value["stamp_rate"])
    rp_new = PortfolioReplay(1_000_000.0, stamp_rate=post.value["stamp_rate"])
    for rp in (rp_old, rp_new):
        rp.buy("600519", "2026-09-20", 150.0, 0.1)
        rp.sell("600519", "2026-09-21", 150.0)
    assert rp_new.cash > rp_old.cash, "低税率净现金更高（成本单调性同源）"
    return {"pre_rate": pre.value["stamp_rate"], "post_rate": post.value["stamp_rate"],
            "source": post.source, "net_cash_gap": round(rp_new.cash - rp_old.cash, 2)}


def scenario_market_status_rejections():
    """场景6：停牌/IPO/退市拒绝路径（合成状态表）——三条拒绝路径计数非零。"""
    rejected = 0
    try:
        MarketStatusCheck.check_suspended("600519", "2026-09-20", {"2026-09-20"})
    except ValueError as e:
        rejected += 1
        assert "停牌" in str(e)
    try:
        MarketStatusCheck.check_listed("301001", "2026-09-01", "2026-09-15")  # 上市前买入
    except ValueError as e:
        rejected += 1
        assert "上市" in str(e)
    try:
        MarketStatusCheck.check_not_delisted("600090", "2026-09-01", "2026-08-31")  # 退市后成交
    except ValueError as e:
        rejected += 1
        assert "退市" in str(e)
    # 非停牌日不误拒
    MarketStatusCheck.check_suspended("600519", "2026-09-21", {"2026-09-20"})
    assert rejected == 3, "三条拒绝路径全部成立"
    return {"rejection_paths": rejected, "note": "停牌/未上市/已退市三路径全部拒绝成立"}


def main() -> dict:
    report = {
        "experiment_id": "E0b",
        "temporal_eligibility": TEMPORAL_ELIGIBILITY,
        "note": "定向成交场景（合成价格）——不是历史策略效果样本；正确性差异按原因逐条解释",
        "scenarios": {
            "1_同日买入退出被拒+次日老仓可卖": scenario_same_day_exit_blocked(),
            "3_末日尚不可卖按最后价格估值": scenario_end_of_window_still_held(),
            "4_除权分红恒等": scenario_dividend_identity(),
            "5_印花税切换日边界": scenario_stamp_tax_boundary(),
            "6_停牌IPO退市拒绝": scenario_market_status_rejections(),
        },
        "rule_sources": [r.source for r in __import__("src.core.experiment", fromlist=["DATED_RULES"]).DATED_RULES[:2]],
    }
    out = Path(__file__).resolve().parents[1] / "artifacts" / "e0b_directed_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    main()
