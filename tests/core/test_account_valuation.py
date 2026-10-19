"""NAV 估值（account_valuation）单测：固定时钟 + 假行情，零网络。"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import src.data.account_service as account_module
import src.data.portfolio as portfolio_module
import src.data.account_valuation as val
from src.data.account_service import AccountService
from src.data.portfolio import PortfolioManager, POSITION_HELD

SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 19, 14, 10, tzinfo=SH)
# 中性示例数据（非真实账户）
CASH = 50000.0
LOTS = {"600519": (100, 10.0, 12.0), "000858": (200, 20.0, 25.0), "300750": (300, 30.0, 28.0)}
NAV = CASH + sum(q * p for q, _, p in LOTS.values())   # 64600.0
EXPECTED = {c: q * p / NAV * 100 for c, (q, _, p) in LOTS.items()}
T = "2026-10-19T14:10:00"


def _quotes(override=None):
    q = {c: {"price": p, "effective_at": T} for c, (_, _, p) in LOTS.items()}
    q.update(override or {})
    return lambda codes: {c: q[c] for c in codes if c in q and q[c] is not None}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    svc = AccountService(tmp_path / "account_events.jsonl")
    svc.opening_import(opening_cash=CASH, trade_date="2026-10-16")
    svc.opening_import(lots=[{"security_id": c, "quantity": q, "cost_price": cost,
                              "acquired_at": "2026-10-16"} for c, (q, cost, _) in LOTS.items()],
                       trade_date="2026-10-16")
    return svc


def test_nav_is_cash_plus_market_value(ledger):
    nav, at, reason = val.compute_nav(ledger.snapshot(), _quotes(), as_of=NOW)
    assert reason == "ok" and nav == pytest.approx(NAV, abs=0.01)
    assert at.date() == NOW.date()


def test_missing_quote_keeps_nav_unknown(ledger):
    nav, _, reason = val.compute_nav(ledger.snapshot(), _quotes({"300750": None}), as_of=NOW)
    assert nav is None and "300750" in reason


@pytest.mark.parametrize("bad", [{"price": 0, "effective_at": T}, {"price": float("nan"), "effective_at": T},
                                 {"price": 28.0}, {"price": 28.0, "effective_at": "garbage"},
                                 {"price": 28.0, "effective_at": "2026-10-19T15:30:00"}])
def test_invalid_quote_keeps_nav_unknown(ledger, bad):
    nav, _, _ = val.compute_nav(ledger.snapshot(), _quotes({"300750": bad}), as_of=NOW)
    assert nav is None


def test_mixed_days_not_stitched(ledger):
    nav, _, reason = val.compute_nav(
        ledger.snapshot(), _quotes({"300750": {"price": 28.0, "effective_at": "2026-10-16T15:00:00"}}), as_of=NOW)
    assert nav is None and "同一交易日" in reason


def test_quote_failure_keeps_unknown(ledger):
    def boom(codes):
        raise OSError("down")
    assert val.compute_nav(ledger.snapshot(), boom, as_of=NOW)[0] is None


def test_priced_snapshot_disabled_by_env_is_noop(ledger, monkeypatch):
    monkeypatch.setenv("MUYUN_NAV_QUOTES", "0")
    assert val.priced_snapshot(ledger.snapshot()).nav is None


def test_request_facts_weights_from_nav(ledger, monkeypatch):
    real = val.priced_snapshot
    monkeypatch.setattr(val, "priced_snapshot", lambda s, quote_fn=None, as_of=None: real(s, _quotes(), as_of=NOW))
    facts = PortfolioManager().request_account_facts()
    assert facts.nav == pytest.approx(NAV, abs=0.01)
    for c, (q, _, p) in LOTS.items():
        ctx = facts.context_for(c, price=p, price_as_of=T)
        assert ctx.position_state == POSITION_HELD and ctx.quantity == q
        assert ctx.confirmed_weight is not None, ctx.weight_reason
        assert ctx.confirmed_weight * 100 == pytest.approx(EXPECTED[c], abs=1e-6)
