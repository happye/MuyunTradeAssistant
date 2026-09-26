"""R6 交易制度与回放闭环回归测试（plan/fusion iteration2，experiment.py R6 扩展）

锁死语义：
1. 日期化规则：切换日边界（印花税 2023-08-28 前后）+ 来源可查（验收4）
2. 公司行动恒等：分红现金+未复权价差=收益；送转扩股；无双计通道（验收2）
3. 停牌/IPO/退市拒绝（验收3 的拒绝路径；状态数据缺失 → 调用方标 NON_STRICT）
4. 成本单调性：固定意图与数量下，费率升高净现金不升（验收5）
5. 回放确定性：同输入同指纹（F8 既有语义回归）

纯内存测试，无网络。跑法：pytest tests/core/test_experiment_r6.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.experiment import (
    CorporateAction,
    DATED_RULES,
    MarketStatusCheck,
    PortfolioReplay,
    ReplayChecks,
    apply_corporate_action,
    rule_at,
)


# ── 1. 日期化规则 ──────────────────────────────────────────

def test_stamp_tax_boundary_with_source():
    """验收4：2023-08-28 前后税率切换、边界归属正确、来源登记。"""
    pre = rule_at("stamp_tax_sell", "2023-08-27")
    on = rule_at("stamp_tax_sell", "2023-08-28")
    assert pre.value["stamp_rate"] == 0.001 and on.value["stamp_rate"] == 0.0005
    assert "财政部" in on.source and on.effective_from == "2023-08-28"


def test_board_scoped_rules():
    """板块范围：科创板最低申报在 main 板不生效。"""
    assert rule_at("star_min_order", "2026-09-01", board="star") is not None
    assert rule_at("star_min_order", "2026-09-01", board="main") is None
    assert rule_at("main_min_order", "2026-09-01", board="main") is not None


def test_rules_registry_has_sources():
    """R6 验收4：注册表每条规则必须带来源（不把设计示例当无源制度清单）。"""
    assert DATED_RULES and all(r.source for r in DATED_RULES)


# ── 2. 公司行动恒等 ────────────────────────────────────────

def test_dividend_cash_identity_no_double_counting():
    """验收2：分红现金 = 持股×每股分红；现金流里分红只出现一次（未复权价+行动台账
    配对——不存在「前复权价差 + 分红现金」的双计通道）。"""
    rp = PortfolioReplay(1_000_000.0)
    rp.buy("600519", "2026-09-01", 100.0, 0.1)
    n = rp.positions["600519"].shares
    cash_before = rp.cash
    acts = apply_corporate_action(rp, CorporateAction(
        security_id="600519", ex_date="2026-09-10", cash_dividend_per_share=5.0,
        source="合成场景分红公告"))
    assert rp.cash - cash_before == n * 5.0
    assert len(acts) == 1
    # 再应用一次同行动：分红不应重复入账的防线在调用方（行动台账按 ex_date 去重）——
    # 本函数如实执行两次就加两次，因此台账幂等由 E0b 报告的行动清单锁死（唯一登记）
    rp2 = PortfolioReplay(1_000_000.0)
    rp2.buy("600519", "2026-09-01", 100.0, 0.1)
    n2 = rp2.positions["600519"].shares
    before2 = rp2.cash
    apply_corporate_action(rp2, CorporateAction(
        security_id="600519", ex_date="2026-09-10", cash_dividend_per_share=5.0))
    apply_corporate_action(rp2, CorporateAction(
        security_id="600519", ex_date="2026-09-10", cash_dividend_per_share=5.0))
    assert (rp2.cash - before2) == n2 * 5.0 * 2, "两次应用=两笔（重复防护在台账层——E0b 行动清单唯一登记）"


def test_share_split_adjusts_lots_not_cash():
    """送转：股数扩大、现金不动、成本价除权调整。"""
    rp = PortfolioReplay(1_000_000.0)
    rp.buy("600519", "2026-09-01", 100.0, 0.1)
    n0, cash0 = rp.positions["600519"].shares, rp.cash
    apply_corporate_action(rp, CorporateAction(
        security_id="600519", ex_date="2026-09-10", share_ratio=0.3))
    pos = rp.positions["600519"]
    assert pos.shares == int(n0 * 1.3) and rp.cash == cash0
    assert pos.cost_per_share == pytest.approx(100.0 / 1.3, abs=1e-4)


# ── 3. 市场状态拒绝 ────────────────────────────────────────

def test_market_status_rejections_and_missing_data_semantics():
    """验收3 拒绝路径；状态数据缺失（None）→ 检查跳过但语义要求调用方标 NON_STRICT
    ——本测试锁「缺数据≠无停牌」的契约位置（不绿灯）。"""
    with pytest.raises(ValueError, match="停牌"):
        MarketStatusCheck.check_suspended("600519", "2026-09-20", {"2026-09-20"})
    MarketStatusCheck.check_suspended("600519", "2026-09-20", None)  # 缺数据：跳过不炸
    with pytest.raises(ValueError, match="上市"):
        MarketStatusCheck.check_listed("301001", "2026-09-01", "2026-09-15")
    MarketStatusCheck.check_listed("301001", "2026-09-20", "2026-09-15")  # 上市后可买
    with pytest.raises(ValueError, match="退市"):
        MarketStatusCheck.check_not_delisted("600090", "2026-09-01", "2026-08-31")


# ── 4. 成本单调性（固定意图与数量）────────────────────────

def test_cost_monotonicity_fixed_intent_and_quantity():
    """验收5：固定买入意图与股数下，费率升高 → 净现金不升（F8 性质在 R6 固定意图
    口径下复验）。"""
    cashes = []
    for fee in (0.0003, 0.0006, 0.001):
        rp = PortfolioReplay(1_000_000.0, fee_rate=fee)
        rp.buy("600519", "2026-09-20", 150.0, 0.1)
        cashes.append(rp.cash)
    assert cashes[0] >= cashes[1] >= cashes[2], "费率升高现金不升（固定意图与数量）"
    # 卖出侧：stamp 税升高 → 净回款不升
    proceeds = []
    for stamp in (0.0005, 0.001):
        rp = PortfolioReplay(1_000_000.0, stamp_rate=stamp)
        rp.buy("600519", "2026-09-20", 150.0, 0.1)
        rp.sell("600519", "2026-09-21", 150.0)
        proceeds.append(rp.cash)
    assert proceeds[0] >= proceeds[1]


def test_replay_determinism_regression():
    """F8 语义回归：同输入同指纹（R6 扩展不破坏确定性）。"""
    rp1 = PortfolioReplay(1_000_000.0)
    rp2 = PortfolioReplay(1_000_000.0)
    for rp in (rp1, rp2):
        rp.buy("600519", "2026-09-20", 150.0, 0.1)
        rp.buy("000001", "2026-09-20", 10.0, 0.05)
        rp.sell("600519", "2026-09-21", 152.0)
    assert rp1.fingerprint() == rp2.fingerprint()


def test_t_plus_1_check_regression():
    """F8 语义回归：T+1/整手检查不被 R6 扩展破坏。"""
    with pytest.raises(ValueError, match="T\\+1"):
        ReplayChecks.check_t_plus_1("2026-09-20", "2026-09-20")
    with pytest.raises(ValueError, match="整手"):
        ReplayChecks.check_lot_size(150, "BUY")
