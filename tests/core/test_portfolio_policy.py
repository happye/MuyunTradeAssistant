"""F7 组合预算求解回归测试（plan/fusion TASKS.md F7，DESIGN ADR-F07）

锁死语义：
1. 约束分配公式：计划目标/单股上限/周期预算/行业上限/可用现金/可交易额/损失预算
   取 min；非负截断；被哪个约束截断可追溯（trimmed_to）
2. 资金不足时只给可行计划（拒绝原因明确——G09）
3. 卖不出不得提前花卖出现金（sell_eligible_nav 只计已确认且可成交的卖出）
4. 同主题风险合并（industry 标签聚合，多标签不重复计额）
5. 排列不变性（G09：输入排列变化不改变结果——fingerprint）
6. 压力损失率未知 → 不给精确额度（DESIGN 原文）
7. 稳定排序：风险紧迫度→计划内到期→明示因子→code（BUY/SELL 不混强度榜）

纯内存测试，无网络。跑法：pytest tests/core/test_portfolio_policy.py -q
"""
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.portfolio_policy import (
    AddProposal,
    BudgetConstraints,
    HoldingWeight,
    solve_budget,
)


def _proposal(code, target=0.2, industries=None, pressure=0.15, score=0.0, urgency=0,
              due=None, grade=0) -> AddProposal:
    return AddProposal(stock_code=code, stock_name=f"股{code}", target_weight=target,
                       industries=industries or ["电子"], pressure_loss_rate=pressure,
                       score=score, risk_urgency=urgency, plan_due=due, evidence_grade=grade)


_EMPTY_CONSTRAINTS = BudgetConstraints(cash_nav=1.0)  # 全现金账户，无其他上限


def test_basic_allocation_meets_target():
    sol = solve_budget([_proposal("600519", target=0.15)],
                       [], _EMPTY_CONSTRAINTS)
    assert sol.adds and sol.adds[0].add_weight == 0.15
    assert sol.adds[0].trimmed_to == "计划目标"


def test_cash_constraint_trims_and_rejects():
    """G09：现金只够两股——第三只被拒且原因明确；前两只按序分配。"""
    constraints = BudgetConstraints(cash_nav=0.30)
    proposals = [_proposal("000001", target=0.2),
                 _proposal("000002", target=0.2),
                 _proposal("000003", target=0.2)]
    sol = solve_budget(proposals, [], constraints)
    assert [a.stock_code for a in sol.adds] == ["000001", "000002"]
    assert sol.adds[1].add_weight == 0.10 and sol.adds[1].trimmed_to == "可用现金"
    assert sol.rejected and "可用现金" in sol.rejected[0].reason


def test_sell_cash_not_counted_unless_eligible():
    """G09/G11：受阻卖出不得提前释放现金——sell_eligible_nav=0 时不计入。"""
    constraints = BudgetConstraints(cash_nav=0.0)
    sol = solve_budget([_proposal("600519", target=0.2)], [], constraints,
                       sell_eligible_nav=0.0)
    assert sol.adds == [], "现金 0 且无可成交卖出 → 无新增"
    sol2 = solve_budget([_proposal("600519", target=0.2)], [], constraints,
                        sell_eligible_nav=0.2)
    assert sol2.adds and sol2.adds[0].add_weight == 0.2, "已确认可成交卖出才释放现金"


def test_industry_cap_aggregates_multiple_tags():
    """同主题多标签聚合： holdings 已占电子 0.15，行业上限 0.2 → 新增至多 0.05；
    同股双标签（电子+AI）取最紧约束。"""
    constraints = BudgetConstraints(cash_nav=1.0, industry_max=0.2)
    holdings = [HoldingWeight(stock_code="000001", weight=0.15, industries=["电子"])]
    sol = solve_budget([_proposal("600519", target=0.3, industries=["电子", "AI"])],
                       holdings, constraints)
    assert sol.adds[0].add_weight == 0.05, "行业上限按聚合暴露算（0.2-0.15）"


def test_permutation_invariance():
    """G09：输入排列变化不改变结果（fingerprint 一致）。"""
    constraints = BudgetConstraints(cash_nav=0.5, per_stock_max=0.2)
    proposals = [_proposal(f"00000{i}", target=0.2, score=float(i)) for i in range(1, 5)]
    sol1 = solve_budget(proposals, [], constraints)
    sol2 = solve_budget(list(reversed(proposals)), [], constraints)
    assert sol1.fingerprint == sol2.fingerprint, "排列不变（G09）"


def test_stable_order_urgency_then_due_then_score():
    """稳定排序：紧迫度 desc → 到期 asc → score desc → code。"""
    constraints = BudgetConstraints(cash_nav=0.6, per_stock_max=0.2)
    proposals = [
        _proposal("000003", target=0.2, score=0.9),
        _proposal("000001", target=0.2, score=0.5, urgency=2),
        _proposal("000002", target=0.2, score=0.7, due="2026-09-30"),
    ]
    sol = solve_budget(proposals, [], constraints)
    assert [a.stock_code for a in sol.adds] == ["000001", "000002", "000003"], \
        "紧迫度2 → 有到期 → 高分"


def test_unknown_pressure_loss_rate_no_precise_budget():
    """DESIGN：压力损失率未知/非正数时不给此模型的精确仓位。"""
    sol = solve_budget([_proposal("600519", pressure=None)], [], _EMPTY_CONSTRAINTS)
    assert sol.adds == []
    assert sol.rejected and "压力损失率未知" in sol.rejected[0].reason


def test_portfolio_loss_budget_consumed():
    constraints = BudgetConstraints(cash_nav=1.0, portfolio_loss_budget=0.03)
    # 压力损失率 0.15 → 0.03 预算可承载总新增 0.2
    proposals = [_proposal("000001", target=0.15, pressure=0.15),
                 _proposal("000002", target=0.15, pressure=0.15)]
    sol = solve_budget(proposals, [], constraints)
    assert sol.adds[0].add_weight == 0.15, "第一只在预算内"
    assert sol.adds[1].add_weight == 0.05 and sol.adds[1].trimmed_to == "组合剩余压力损失预算"


def test_max_adds_research_budget():
    constraints = BudgetConstraints(cash_nav=1.0, max_adds=2)
    proposals = [_proposal(f"00000{i}", pressure=0.1) for i in range(1, 5)]
    sol = solve_budget(proposals, [], constraints)
    assert len(sol.adds) == 2 and len(sol.rejected) == 2
    assert "研究预算上限" in sol.rejected[0].reason


def test_higher_risk_candidate_does_not_grow_budget():
    """DESIGN：增加一只风险更高或证据更差的候选不得自动增加总预算——
    总新增受组合损失预算封顶，与候选数量无关。"""
    constraints = BudgetConstraints(cash_nav=1.0, portfolio_loss_budget=0.03)
    two = [_proposal("000001", pressure=0.15), _proposal("000002", pressure=0.15)]
    three = two + [_proposal("000003", pressure=0.15)]
    total2 = sum(a.add_weight for a in solve_budget(two, [], constraints).adds)
    total3 = sum(a.add_weight for a in solve_budget(three, [], constraints).adds)
    assert total2 == total3, "总预算不随候选数增长（损失预算封顶）"


# ── 5. F7 对抗审查修复回归锁 ─────────────────────────────

def test_tradeable_nav_decrements_across_candidates():
    """P1-1 回归锁：可交易额容量约束逐笔递减——N 只候选合计不超容量
    （原实现每只用全值，N 只突破 N 倍）。"""
    constraints = BudgetConstraints(cash_nav=1.0, tradeable_nav=0.1)
    proposals = [_proposal(f"00000{i}", target=0.2) for i in range(1, 6)]
    sol = solve_budget(proposals, [], constraints)
    total = sum(a.add_weight for a in sol.adds)
    assert total <= 0.1 + 1e-9, f"合计 {total} 不得超过容量 0.1"


def test_unknown_cash_is_not_zero_cash():
    """P1-2 回归锁：cash_nav=None（未设置）≠ 0 现金——跳过现金约束
    （DESIGN：没有可用配置不假装知道承受能力）。"""
    constraints = BudgetConstraints()  # 现金未设置
    sol = solve_budget([_proposal("600519", target=0.2)], [], constraints)
    assert sol.adds, "现金未知不得按 0 拒绝"
    assert all("可用现金" not in a.trimmed_to for a in sol.adds)


def test_industry_sentinel_not_in_public_output():
    """P2-4 回归锁：无行业提案的内部哨兵不入公开行业暴露输出。"""
    sol = solve_budget([_proposal("600519", industries=[])], [], _EMPTY_CONSTRAINTS)
    assert "__none__" not in sol.committed_industry_exposure


def test_constraints_reject_typo_keys():
    """P2-5 回归锁：约束名拼错当场炸（extra=forbid，不静默放松）。"""
    with pytest.raises(ValidationError):
        BudgetConstraints(cash_nav=1.0, per_stock_caps=0.2)


def test_nan_score_rejected():
    """P2-6 回归锁：score NaN 拒收（NaN 破坏排序全序=破坏排列不变性）。"""
    with pytest.raises(ValidationError):
        _proposal("600519", score=float("nan"))
