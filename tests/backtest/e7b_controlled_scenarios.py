"""E7b 完整系统受控场景（plan/fusion iteration2 R7，VALIDATION §4 E7b）。

受控场景（合成输入明确标注 SYNTHETIC——绝不为让真实候选出现 BUY 改资格或入口条件）：
- 动作路径命中证据：OPEN/ADD/HOLD/REDUCE/EXIT/WAIT/REVIEW + EXIT+BLOCKED 全部 ≥1
- 预算案例：可行新增 / 不足最低合法申报 / 现金不足 / 未确认卖出不释放现金
- 真实非空路径观察：同 cutoff 的真实候选观察（live）——本脚本不跑 live；真实市场
  缺失某类路径标 NOT_OBSERVED 不计已通过（R7 验收2 后半）

跑法（离线）：PYTHONUTF8=1 python tests/backtest/e7b_controlled_scenarios.py
产物：tests/artifacts/e7b_controlled_report.json
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.decision_contract import (
    Horizon,
    InvalidationRule,
    ResearchStatus,
    ThesisStatus,
    TruthValue,
)
from src.core.decision_policy import (
    POLICY_ID_MID,
    HorizonFacts,
    HorizonPlan,
    evaluate_horizon_intent,
)
from src.core.portfolio_policy import BudgetConstraints, BudgetLine, HoldingWeight, solve_budget
from src.data.account_snapshot import (
    AccountSnapshot,
    AllocationState,
    LotRules,
    TradeRulesAdapter,
    allocate_tradeable_budget,
)

TEMPORAL_ELIGIBILITY = "NON_STRICT"  # 受控场景合成输入（E7b 上半场）
UNIVERSAL_AS_OF = "2026-09-25"


def _plan(plan_id="p_e7b", horizon=Horizon.MID, invalidate=None, accepted=True):
    return HorizonPlan(
        plan_id=plan_id, security_id="600519", horizon=horizon,
        policy_id=POLICY_ID_MID, intent="E7b 受控场景计划",
        accepted_at=("2026-09-20T08:00:00" if accepted else None),
        invalidate_if=invalidate or [],
        fact_evidence_refs={"订单已签约": ["claim-1"]},  # 带证据引用（R0/R3 资格门语义）
        facts_observed=["订单已签约"],
    )


def _facts(**kw):
    base = dict(research_status=ResearchStatus.COMPLETE,
                thesis_status=ThesisStatus.VALID)
    base.update(kw)
    return HorizonFacts(**base)


class _FixtureRules(TradeRulesAdapter):
    def lot_rules(self, security_id: str, as_of: str):
        return LotRules(security_id=security_id, board="main", min_order_qty=100,
                        lot_step=100, sell_odd_lots_allowed=True,
                        effective_from="2006-01-01", source="fixture(main)")


def action_paths():
    """决策表七类动作意图逐一命中（PolicyIntent——不含执行资格，R3 验收7 语义）。"""
    p = _plan()
    paths = {}
    # OPEN：MID VALID + 入场成立 + 预算可用 + 未持有（组合已知无持仓）
    paths["OPEN"] = evaluate_horizon_intent(
        p, _facts(entry_condition_met=True, budget_available=True), "600519",
        confirmed_ratio=0.0).desired_action.value
    # ADD：已持有 + 预算可用
    paths["ADD"] = evaluate_horizon_intent(
        p, _facts(entry_condition_met=True, budget_available=True), "600519",
        confirmed_ratio=0.2).desired_action.value
    # HOLD：已持有 + 预算不可用
    paths["HOLD"] = evaluate_horizon_intent(
        p, _facts(entry_condition_met=True, budget_available=False), "600519",
        confirmed_ratio=0.2).desired_action.value
    # REDUCE：MID VALID + 预设技术退出成立（行5）
    paths["REDUCE"] = evaluate_horizon_intent(
        p, _facts(technical_exit_triggered=True), "600519",
        confirmed_ratio=0.2).desired_action.value
    # EXIT：逻辑失效条件 TRUE（行3——技术反弹不能覆盖）
    p_inv = _plan(plan_id="p_e7b_inv",
                  invalidate=[InvalidationRule(rule_id="r1", condition="行业景气证伪",
                                               evaluation=TruthValue.TRUE)])
    paths["EXIT"] = evaluate_horizon_intent(
        p_inv, _facts(), "600519", confirmed_ratio=0.2).desired_action.value
    # WAIT：未持有 + 预算明确不可用（行9）
    paths["WAIT"] = evaluate_horizon_intent(
        p, _facts(entry_condition_met=True, budget_available=False), "600519",
        confirmed_ratio=0.0).desired_action.value
    # REVIEW：研究证据冲突（行4）
    paths["REVIEW"] = evaluate_horizon_intent(
        p, _facts(research_status=ResearchStatus.CONFLICTED), "600519",
        confirmed_ratio=0.2).desired_action.value
    return paths


def exit_blocked_path():
    """EXIT+BLOCKED：退出意图保持 + 可卖份额不足（不用「建议持有」掩盖受阻退出）。"""
    p_inv = _plan(plan_id="p_e7b_inv2",
                  invalidate=[InvalidationRule(rule_id="r1", condition="业绩预亏坐实",
                                               evaluation=TruthValue.TRUE)])
    intent = evaluate_horizon_intent(p_inv, _facts(), "600519", confirmed_ratio=0.2)
    # 执行层受阻证据：可卖份额不足由 T+1 批次检查给出（R6 ReplayChecks）——
    # intent.blockers R3 期恒空（决策表行接通证据引用后填充），受阻在执行层验
    from src.core.experiment import PortfolioReplay
    rp = PortfolioReplay(1_000_000.0)
    rp.buy("600519", "2026-09-25", 150.0, 0.2)  # 当日买入
    blocked_reason = ""
    try:
        rp.sell("600519", "2026-09-25", 150.0)
    except ValueError as e:
        blocked_reason = str(e)
    return {"intent": intent.desired_action.value, "blocked_reason": blocked_reason,
            "position_still_held": rp.positions["600519"].shares > 0,
            "note": "EXIT 意图保持；执行受阻（T+1）如实上报——本次持仓数量没有变化"}


def budget_cases():
    """四个预算案例（VALIDATION E7b 要求全命中）。"""
    cases = {}
    rules = _FixtureRules()
    prices = lambda code: 10.0
    # ① 可行新增
    ok_snap = AccountSnapshot(as_of=datetime(2026, 9, 25, tzinfo=timezone.utc),
                              cash_available=200_000.0, nav=1_000_000.0)
    out = allocate_tradeable_budget([BudgetLine(stock_code="000001", current_weight=0.0,
                                                add_weight=0.05, target_weight=0.05, horizon="MID")],
                                    ok_snap, rules, as_of=UNIVERSAL_AS_OF, price_provider=prices)
    cases["可行新增"] = {"state": out[0].allocation_state.value, "quantity": out[0].quantity}
    # ② 不足最低合法申报（预算 900 < 100 股含费）
    tight = AccountSnapshot(as_of=datetime(2026, 9, 25, tzinfo=timezone.utc),
                            cash_available=900.0, nav=1_000_000.0)
    out2 = allocate_tradeable_budget([BudgetLine(stock_code="000001", current_weight=0.0,
                                                 add_weight=0.0009, target_weight=0.0009,
                                                 horizon="MID")],
                                     tight, rules, as_of=UNIVERSAL_AS_OF, price_provider=prices)
    cases["不足最低合法申报"] = {"state": out2[0].allocation_state.value,
                                 "released": out2[0].released_budget_weight,
                                 "reason_head": out2[0].rejected_reason[:30]}
    # ③ 现金不足（0 现金——与缺失分开）
    zero = AccountSnapshot(as_of=datetime(2026, 9, 25, tzinfo=timezone.utc),
                           cash_available=0.0, nav=1_000_000.0)
    out3 = allocate_tradeable_budget([BudgetLine(stock_code="000001", current_weight=0.0,
                                                 add_weight=0.05, target_weight=0.05,
                                                 horizon="MID")],
                                     zero, rules, as_of=UNIVERSAL_AS_OF, price_provider=prices)
    cases["现金不足"] = {"state": out3[0].allocation_state.value}
    # ④ 未确认卖出不释放现金：**对照臂**——SELL 确认入账前可用不变，入账后才增加
    # （「确认入账是唯一释放通道」成为被测语义——监督员 P2：空账本平凡通过不算数）
    from src.data.account_snapshot import AccountEvent, AccountEventLog, EventType
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        log = AccountEventLog(Path(td) / "e.jsonl")  # 空账本——「拟卖出」从未确认入账
        snap = log.replay(opening_cash=100_000.0)
        out4 = allocate_tradeable_budget([BudgetLine(stock_code="000001", current_weight=0.0,
                                                     add_weight=0.05, target_weight=0.05,
                                                     horizon="MID")],
                                         snap, rules, as_of=UNIVERSAL_AS_OF, price_provider=prices)
        cases["未确认卖出不释放现金"] = {
            "deployable_before_confirm": snap.cash_deployable,
            "state": out4[0].allocation_state.value,
            "note": "卖出确认入账前，可用现金不含拟卖金额"}
        # 对照臂：卖出确认入账（cash_delta 落账）→ 可用现金才增加。
        # J2 整事件原子生效：SELL 需先有合法 BUY 建仓（无持仓卖出整事件隔离）——
        # 场景算术相应为 100_000 − 1000(买) + 995(卖净额) = 99_995
        log.append(AccountEvent(event_id="buy-0", event_type=EventType.BUY,
                                security_id="000002", trade_date="2026-09-20",
                                quantity=100, price=10.0, cash_delta=-1000.0,
                                lot_cost_price=10.0))
        log.append(AccountEvent(event_id="sell-1", event_type=EventType.SELL,
                                security_id="000002", trade_date="2026-09-25",
                                quantity=100, price=10.0, cash_delta=+995.0))
        snap_confirmed = log.replay(opening_cash=100_000.0)
        cases["未确认卖出不释放现金"]["deployable_after_confirm"] = snap_confirmed.cash_deployable
        assert snap_confirmed.cash_deployable == 100_000.0 - 1000.0 + 995.0, \
            "确认入账后现金才增加（对照臂；J2 原子语义下买入卖出均须合法配对）"
    return cases


def main() -> dict:
    paths = action_paths()
    required_actions = {"OPEN", "ADD", "HOLD", "REDUCE", "EXIT", "WAIT", "REVIEW"}
    missing = required_actions - set(paths.values())
    eb = exit_blocked_path()
    budgets = budget_cases()
    report = {
        "experiment_id": "E7b",
        "run_scope": "受控场景上半场（SYNTHETIC/NON_STRICT——真实非空路径观察另批）",
        "temporal_eligibility": TEMPORAL_ELIGIBILITY,
        "action_paths": paths,
        "missing_actions": sorted(missing),
        "exit_blocked": eb,
        "budget_cases": budgets,
        "not_observed_real_market": "真实市场当期未出现的路径标 NOT_OBSERVED——不计已通过",
    }
    # 受控场景门：七类动作 + EXIT 受阻 + 四预算案例全部命中（缺一 = 场景没设计全）
    assert not missing, f"E7b 场景缺口: {missing}"
    assert eb["intent"] == "EXIT" and eb["blocked_reason"], "EXIT+BLOCKED 未命中"
    assert budgets["可行新增"]["state"] == "FEASIBLE"
    assert budgets["不足最低合法申报"]["state"] == "REJECTED"
    assert budgets["现金不足"]["state"] == "REJECTED"
    assert budgets["未确认卖出不释放现金"]["deployable_before_confirm"] == 100_000.0
    assert budgets["未确认卖出不释放现金"]["deployable_after_confirm"] == 99_995.0, \
        "J2 原子语义：合法买入+卖出配对后净增 995（100_000−1000+995）"
    out = Path(__file__).resolve().parents[1] / "artifacts" / "e7b_controlled_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    main()
