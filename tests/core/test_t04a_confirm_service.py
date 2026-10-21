"""T04-A-1：account_service.confirm_quantity_from_proposal 服务单测（不经 CLI）。

直接调用被测函数（不 mock 它）；账本/计划库/portfolio/建议库全部落 tmp_path，
ledger_path/plans_store 显式注入——绝不碰真实 HOME。CLI 输出逐字锁定见
test_t04a_confirm_golden.py。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.account_service import (
    AccountService, FillInput, QuantityConfirmOutcome, confirm_quantity_from_proposal,
)
from src.data.horizon_plans import HorizonPlanStore
from src.data.portfolio import PortfolioManager
from src.data.proposals import Proposal

CODE = "600519"
TRADE_DATE = "2026-09-21"   # 期初批次 2026-09-20 -> T+1 可卖


class _Env:
    def __init__(self, tmp_path):
        d = tmp_path / "svc"
        d.mkdir()
        self.ledger = d / "events.jsonl"
        self.plans = HorizonPlanStore(d / "plans.json")
        self.pm = PortfolioManager(str(d / "portfolio.yaml"), str(d / "proposals.json"))
        self.svc = AccountService(self.ledger, plans_store=self.plans)

    def seed(self, *, action, target_ratio, held, avg_cost=10.0, pid="sp"):
        self.pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)
        self.pm._proposals.add_proposal(Proposal(
            proposal_id=pid, stock_code=CODE, stock_name="测试股",
            position_action=action, target_ratio=target_ratio, current_ratio=0.1))
        assert self.pm._proposals._save()
        self.svc.opening_import(
            opening_cash=100000.0, trade_date="2026-09-20",
            lots=[{"security_id": CODE, "quantity": held, "cost_price": avg_cost,
                   "acquired_at": "2026-09-20"}])
        return self.pm.pending_proposals(CODE)[-1]

    def call(self, target, action, qty, price, **kw):
        kw.setdefault("trade_date", TRADE_DATE)
        return confirm_quantity_from_proposal(
            self.pm, target, CODE, action, qty, price,
            ledger_path=self.ledger, plans_store=self.plans, **kw)


@pytest.fixture
def env(tmp_path):
    return _Env(tmp_path)


@pytest.mark.parametrize("price", [0.0, None])
def test_missing_price_returns_without_touching_ledger(env, price):
    target = env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    before = len(env.svc.log.events())
    oc = env.call(target, "SELL", 300, price)
    assert isinstance(oc, QuantityConfirmOutcome)
    assert oc.status == "MISSING_PRICE"
    assert oc.receipt is None and oc.fill_id == ""
    assert len(env.svc.log.events()) == before
    assert env.pm.get_position(CODE).quantity_fact is None   # 投影未动


def test_buy_applied_reports_before_after_and_fill_id(env):
    target = env.seed(action="ADD", target_ratio=0.15, held=300)
    oc = env.call(target, "BUY", 100, 15.0, fee=5.0)
    assert oc.status == "APPLIED"
    assert oc.fill_id == "sp#1" and oc.recovered is False
    assert oc.receipt.status == "ACCEPTED" and oc.receipt.cash_delta == -1505.0
    assert (oc.held_before, oc.held_after) == (300, 400)
    assert oc.avg_cost_after == pytest.approx(11.25)
    assert oc.has_active_ref is False and oc.plan_ref == ""
    assert oc.projection is not None and oc.projection.ok
    assert oc.error is None and oc.stage == "complete"
    # 事实以磁盘为准
    snap = AccountService(env.ledger).snapshot()
    assert next(h.quantity for h in snap.holdings if h.security_id == CODE) == 400
    assert env.pm.has_applied_fill("sp#1")


def test_close_all_partial_sell_marks_deviation_in_ledger(env):
    target = env.seed(action="CLOSE_ALL", target_ratio=0.0, held=1000)
    oc = env.call(target, "SELL", 400, 12.0)
    assert oc.status == "APPLIED" and oc.held_after == 600
    ev = env.svc.log.events()[-1].model_dump()
    assert ev["deviation_from_proposal"] is True
    assert "偏离：建议清仓，本笔卖出400/1000股" in ev["fill_note"]


def test_recovered_state_reuses_fill_id_and_returns_duplicate(env):
    target = env.seed(action="CLOSE_ALL", target_ratio=0.0, held=150)
    receipt = env.svc.confirm_fill("sp#1", "", FillInput(
        security_id=CODE, action="SELL", quantity=100, price=10.0, trade_date=TRADE_DATE,
        deviation_from_proposal=True, note="proposal=sp；偏离：建议清仓，本笔卖出100/150股"))
    assert receipt.status == "ACCEPTED"          # 账本已入账，投影未动
    oc = env.call(target, "SELL", 100, 10.0)
    assert oc.status == "APPLIED"
    assert oc.recovered is True and oc.fill_id == "sp#1"
    assert oc.receipt.status == "DUPLICATE"
    assert len(env.svc.log.events()) == 3        # 不重复入账
    assert oc.held_before == 50 and oc.held_after == 50
    assert env.pm.has_applied_fill("sp#1")       # 投影补做


def test_ledger_rejection_oversell_is_ledger_failed(env):
    target = env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    before = len(env.svc.log.events())
    oc = env.call(target, "SELL", 2000, 12.0)
    assert oc.status == "LEDGER_FAILED"
    assert oc.receipt.status == "REJECTED" and "超卖拒绝" in oc.receipt.reason
    assert oc.held_before == 1000 and oc.projection is None
    assert len(env.svc.log.events()) == before
    assert not env.pm.has_applied_fill("sp#1")


def test_active_ref_fields_populated(env):
    from src.core.decision_contract import Horizon
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    env.plans.save(HorizonPlan(plan_id="p_mid_svc", security_id=CODE, horizon=Horizon.MID,
                               policy_id=POLICY_ID_MID, intent="T04-A-2"))
    assert env.plans.accept(CODE, "MID")[0]
    assert env.plans.set_active_ref(CODE, env.plans.get(CODE, "MID"))[0]
    target = env.seed(action="ADD", target_ratio=0.15, held=300)
    oc = env.call(target, "BUY", 100, 15.0)
    assert oc.status == "APPLIED" and oc.stage == "complete"
    assert oc.has_active_ref is True
    assert oc.plan_ref == "p_mid_svc" and oc.active_horizon == "MID"
    assert env.svc.log.events()[-1].model_dump()["plan_ref"] == "p_mid_svc"


def test_error_before_ledger_keeps_partial_fields_and_same_exception(env):
    from pydantic import ValidationError
    target = env.seed(action="ADD", target_ratio=0.15, held=300)
    before = len(env.svc.log.events())
    oc = env.call(target, "BUY", 100, 15.0, fee=-1.0)   # FillInput fee ge=0
    assert oc.status == "ERROR_BEFORE_LEDGER" and oc.stage == "before_ledger"
    assert isinstance(oc.error, ValidationError)
    assert oc.receipt is None and oc.held_before == 300 and oc.projection is None
    assert len(env.svc.log.events()) == before


def test_projection_error_after_ledger_written(env, monkeypatch):
    target = env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    boom = RuntimeError("模拟投影前崩溃")

    def _raise(self, *a, **k):
        raise boom
    monkeypatch.setattr(PortfolioManager, "apply_quantity_fill", _raise)
    oc = env.call(target, "SELL", 300, 12.0)
    assert oc.status == "PROJECTION_ERROR" and oc.stage == "after_ledger"
    assert oc.error is boom                      # 同一实例，CLI 原样重抛
    assert oc.receipt.status == "ACCEPTED" and oc.fill_id == "sp#1"
    assert (oc.held_before, oc.held_after) == (1000, 700)
    assert oc.projection is None
    assert len(env.svc.log.events()) == 3        # 账本已入账
    assert not env.pm.has_applied_fill("sp#1")   # 投影未同步
    monkeypatch.undo()
    oc2 = env.call(target, "SELL", 300, 12.0)    # 重跑同一命令：幂等恢复
    assert oc2.status == "APPLIED" and oc2.recovered is True
    assert oc2.receipt.status == "DUPLICATE" and len(env.svc.log.events()) == 3


def test_base_exception_is_captured_not_swallowed(env, monkeypatch):
    class _Stop(BaseException):
        pass
    target = env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    stop = _Stop()

    def _raise(self, *a, **k):
        raise stop
    monkeypatch.setattr(PortfolioManager, "apply_quantity_fill", _raise)
    oc = env.call(target, "SELL", 300, 12.0)
    assert oc.status == "PROJECTION_ERROR" and oc.error is stop
