"""BUG-FINITE（iteration10）：账户事件账本拒绝非有限数值（inf/nan）

反例（HEAD 实测）：
- FillInput(price=inf) / FillInput(fee=inf) 通过 gt=0 / ge=0，入账成功，快照现金 -inf、均价 inf
- opening_import(opening_cash=inf/nan)、lot cost_price=inf/nan 被接受并写入账本；
  nan 成本入账后整本重放抛 ValidationError（账本被毒化）；lot 数量 inf 抛原始 OverflowError
- 入口 `pos opening inf|nan`、`pos openinglot <码> <量> inf|nan` 进入服务并写账本
锁死（修后）：
- 服务层：与现有负数拒绝同类的异常（FillInput -> pydantic ValidationError；opening_import -> ValueError），
  账本事件数不变
- 入口层：parse_input 返回 None、打印「  [!] … 需有限数字 …」、账本事件数不变（走 parse_input -> run_cli 全链）
- 已绿记录：FillInput(price=nan / fee=nan) 由 gt=0 / ge=0 已拒；lot 数量 nan 已是 ValueError
- 旧数据（TestLegacyNonfiniteEvents）：账本里**已存在**的非有限事件在快照/出口的现状——本卡不改快照层，
  现状表征测试仅作证据，快照层若另卡修复，这些断言应随之翻转

隔离：持久化路径全部指向 tmp_path；零网络零 AI。
跑法：pytest tests/core/test_bug_finite_ledger.py -q
"""
import io
import json
import math
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime

import pytest
from pydantic import ValidationError
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.data.account_service as account_module
import src.data.horizon_plans as horizon_plans_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.cli.today_service import build_today_view, render_today
from src.data.account_service import AccountService, FillInput
from src.data.account_snapshot import AccountEvent, EventType
from src.data.account_valuation import compute_nav
from src.data.portfolio import PortfolioManager

CODE = "600519"
OTHER = "000001"
INF = float("inf")
NAN = float("nan")


@pytest.fixture
def acct(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", tmp_path / "horizon_plans.json")
    root = str(tmp_path.resolve())
    for p in (tmp_path / "portfolio.yaml", tmp_path / "account_events.jsonl",
              tmp_path / "horizon_plans.json", tmp_path / "proposals.json"):
        assert os.path.abspath(str(p)).startswith(root), f"持久化路径越出临时根: {p}"
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10.0, ratio=0.1)
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=100000.0, trade_date="2026-09-20",
        lots=[{"security_id": CODE, "quantity": 300, "cost_price": 10.0,
               "acquired_at": "2026-09-20"}])
    return tmp_path


def _svc():
    return AccountService(account_module.DEFAULT_LEDGER_PATH)


def _n_events():
    return len(_svc().log.events())


def _ledger_bytes():
    return account_module.DEFAULT_LEDGER_PATH.read_bytes()


def _repl(cmd):
    """真实 REPL 全链：parse_input -> （非 None 时）run_cli。返回 (parsed, stdout, 异常或 None)。"""
    from unittest.mock import patch
    buf = io.StringIO()
    exc = None
    parsed = None
    with patch.object(cli_main, "console", Console(file=buf, width=200)):
        with redirect_stdout(buf):
            try:
                parsed = start_module.parse_input(cmd)
                if parsed is not None:
                    start_module.run_cli(parsed[0], parsed[1])
            except Exception as e:  # noqa: BLE001 —— 记录原始呈现，断言处判红
                exc = e
    return parsed, buf.getvalue(), exc


# ───────────────────────── 服务层：FillInput ─────────────────────────

@pytest.mark.parametrize("field, value", [
    ("price", INF),
    ("price", NAN),   # 已绿：gt=0 已拒 nan
    ("fee", INF),
    ("fee", NAN),     # 已绿：ge=0 已拒 nan
])
def test_fill_input_rejects_nonfinite(field, value):
    kwargs = {"security_id": CODE, "action": "BUY", "quantity": 100, "price": 12.0,
              "fee": 0.0, "trade_date": "2026-09-21"}
    kwargs[field] = value
    with pytest.raises(ValidationError):
        FillInput(**kwargs)


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_fill_input_inf_price_never_reaches_ledger(acct, action):
    """入账层面的反例：旧版 inf 价 BUY/SELL 返回 ACCEPTED 并写账本。"""
    n0 = _n_events()
    with pytest.raises(ValidationError):
        _svc().confirm_fill("fin1", "", FillInput(
            security_id=CODE, action=action, quantity=100, price=INF, trade_date="2026-09-21"))
    assert _n_events() == n0


def test_fill_input_finite_still_valid():
    f = FillInput(security_id=CODE, action="BUY", quantity=100, price=12.5, fee=5.0,
                  trade_date="2026-09-21")
    assert f.price == 12.5 and f.fee == 5.0
    assert FillInput(security_id=CODE, action="BUY", quantity=1, price=0.01,
                     trade_date="2026-09-21").fee == 0.0


# ───────────────────────── 服务层：opening_import ─────────────────────────

@pytest.mark.parametrize("cash", [INF, -INF, NAN])
def test_opening_import_rejects_nonfinite_cash(acct, cash):
    n0 = _n_events()
    with pytest.raises(ValueError):
        _svc().opening_import(opening_cash=cash, trade_date="2026-09-21")
    assert _n_events() == n0


@pytest.mark.parametrize("cost", [INF, NAN])
def test_opening_import_rejects_nonfinite_cost(acct, cost):
    n0 = _n_events()
    with pytest.raises(ValueError):
        _svc().opening_import(lots=[{"security_id": OTHER, "quantity": 100, "cost_price": cost,
                                     "acquired_at": "2026-09-20"}])
    assert _n_events() == n0
    # 账本未被毒化：快照仍可读
    assert _svc().snapshot().cash_available == 100000.0


@pytest.mark.parametrize("qty", [INF, -INF, NAN])
def test_opening_import_rejects_nonfinite_quantity(acct, qty):
    """数量 inf 旧版抛原始 OverflowError（非 ValueError）；nan 已是 ValueError（已绿）。"""
    n0 = _n_events()
    with pytest.raises(ValueError):
        _svc().opening_import(lots=[{"security_id": OTHER, "quantity": qty, "cost_price": 5.0,
                                     "acquired_at": "2026-09-20"}])
    assert _n_events() == n0


def test_opening_import_bad_second_lot_writes_nothing(acct):
    """整批先校验后写：第二批非有限，第一批也不得落账（含期初现金）。"""
    n0 = _n_events()
    with pytest.raises(ValueError):
        _svc().opening_import(
            opening_cash=5000.0, trade_date="2026-09-21",
            lots=[{"security_id": OTHER, "quantity": 100, "cost_price": 5.0, "acquired_at": "2026-09-20"},
                  {"security_id": "000002", "quantity": 100, "cost_price": INF, "acquired_at": "2026-09-20"}])
    assert _n_events() == n0


def test_opening_import_finite_still_books(acct):
    n0 = _n_events()
    receipts = _svc().opening_import(
        opening_cash=2500.5, trade_date="2026-09-21",
        lots=[{"security_id": OTHER, "quantity": 100, "cost_price": 5.25, "acquired_at": "2026-09-20"}])
    assert [r.ok for r in receipts] == [True, True]
    assert _n_events() == n0 + 2


# ───────────────────────── 入口层接线：parse_input -> run_cli ─────────────────────────

@pytest.mark.parametrize("cmd, token", [
    ("pos opening inf", "期初现金"),
    ("pos opening -inf", "期初现金"),
    ("pos opening nan", "期初现金"),
    (f"pos openinglot {OTHER} 100 inf 2026-09-20", "成本价"),
    (f"pos openinglot {OTHER} 100 nan 2026-09-20", "成本价"),
    (f"pos openinglot {OTHER} inf 5.0 2026-09-20", "数量"),   # 已绿：int('inf') 原已拒
    (f"pos openinglot {OTHER} nan 5.0 2026-09-20", "数量"),   # 已绿
])
def test_nonfinite_opening_rejected_at_entry(acct, cmd, token):
    before = _ledger_bytes()
    parsed, out, exc = _repl(cmd)
    assert exc is None, f"入口不得抛原始异常: {type(exc).__name__}: {exc}"
    assert parsed is None, f"应在入口被拒（不进入 run_cli）: {parsed}"
    hints = [ln for ln in out.splitlines() if ln.startswith("  [!]")]
    assert hints, f"须有「  [!]」人话提示: {out!r}"
    assert any(token in ln for ln in hints), f"提示须点名 {token}: {hints}"
    assert "Traceback" not in out and "ValidationError" not in out
    assert _ledger_bytes() == before, "账本不得改动"


@pytest.mark.parametrize("cmd, token", [
    ("pos opening inf", "有限数字"),
    (f"pos openinglot {OTHER} 100 nan 2026-09-20", "有限数字"),
])
def test_nonfinite_entry_hint_says_finite(acct, cmd, token):
    _, out, _ = _repl(cmd)
    hints = [ln for ln in out.splitlines() if ln.startswith("  [!]")]
    assert any(token in ln for ln in hints), hints


def test_valid_opening_and_openinglot_still_book(acct):
    n0 = _n_events()
    parsed, out, exc = _repl("pos opening 2500.5")
    assert exc is None and parsed is not None and parsed[0] == "pos_opening"
    assert "期初现金已锚定" in out, out
    parsed, out, exc = _repl(f"pos openinglot {OTHER} 100 5.25 2026-09-20")
    assert exc is None and parsed is not None and parsed[0] == "pos_openinglot"
    assert "期初批次已导入" in out, out
    assert _n_events() == n0 + 2


# ───────────────────────── 旧数据：账本里已存在的非有限事件（现状表征）─────────────────────────
# 注意：以下断言锁定「当前快照层/出口」对旧非有限事件的实际表现（HEAD 实测），不是期望行为。
# 快照层（account_snapshot.py）不在本卡范围——需主 Agent 另卡裁决；修复后这些断言应翻转。

def _inject(**kw):
    """绕过服务层，直接把事件写进账本（模拟修复前已落盘的旧毒数据）。"""
    ev = AccountEvent(**kw)
    with account_module.DEFAULT_LEDGER_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev.model_dump(mode="json"), ensure_ascii=False) + "\n")


def _legacy(kind):
    if kind == "cash_inf":
        _inject(event_id="fill_legacy_cash", event_type=EventType.OPENING, trade_date="2026-09-21",
                cash_delta=INF)
    elif kind == "cash_nan":
        _inject(event_id="fill_legacy_cash", event_type=EventType.OPENING, trade_date="2026-09-21",
                cash_delta=NAN)
    elif kind == "buy_price_inf":
        _inject(event_id="fill_legacy_buy", event_type=EventType.BUY, security_id=OTHER,
                trade_date="2026-09-21", quantity=100, price=INF, cash_delta=-INF, lot_cost_price=INF)
    elif kind == "lot_cost_inf":
        _inject(event_id="fill_legacy_lot", event_type=EventType.OPENING, security_id=OTHER,
                trade_date="2026-09-20", quantity=100, price=INF, lot_cost_price=INF)
    elif kind == "lot_cost_nan":
        _inject(event_id="fill_legacy_lot", event_type=EventType.OPENING, security_id=OTHER,
                trade_date="2026-09-20", quantity=100, price=NAN, lot_cost_price=NAN)
    else:
        raise AssertionError(kind)


def _quotes(codes):
    return {c: {"price": 10.0, "effective_at": "2026-09-21 10:00:00"} for c in codes}


@pytest.mark.parametrize("kind, cash_check", [
    ("cash_inf", math.isinf),
    ("cash_nan", math.isnan),
    ("buy_price_inf", math.isinf),
])
def test_legacy_current_nonfinite_cash_propagates_to_snapshot_and_exits(acct, kind, cash_check):
    """现状：非有限现金不被隔离——快照 QUANTITY_LEVEL、today 显示金额、NAV 估值给出非有限『ok』。"""
    _legacy(kind)
    snap = _svc().snapshot()
    assert cash_check(snap.cash_available)
    assert snap.data_completeness == "QUANTITY_LEVEL" and not snap.isolated_events

    pm = PortfolioManager()
    facts = pm.request_account_facts()
    assert facts.read_failed is False and facts._ledger_partial is False
    view = build_today_view(pm, account_facts=facts)
    text = render_today(view)
    if kind == "cash_inf":
        assert "可用现金 inf 元" in text            # inf 作为金额显示
    else:
        assert "可用现金 0 元" in text              # -inf/nan 被 max(0.0, x) 吞成 0，亦非「未知」
    nav, _, reason = compute_nav(facts.snapshot, _quotes, as_of=datetime(2026, 9, 22, 10, 0))
    assert reason == "ok" and not math.isfinite(nav)


@pytest.mark.parametrize("kind", ["lot_cost_inf", "buy_price_inf"])
def test_legacy_current_inf_cost_propagates_to_avg_cost_display(acct, kind):
    _legacy(kind)
    snap = _svc().snapshot()
    h = next(x for x in snap.holdings if x.security_id == OTHER)
    assert math.isinf(h.avg_cost)
    pm = PortfolioManager()
    text = render_today(build_today_view(pm, account_facts=pm.request_account_facts()))
    assert f"{OTHER} 持有 100 股，平均成本 inf" in text


def test_legacy_current_nan_cost_crashes_snapshot_but_request_facts_fail_safe(acct):
    """现状：nan 成本使整本重放抛 ValidationError（账本被毒化）；
    RequestAccountFacts 捕获后走 read_failed（UNKNOWN，不当空仓）；写侧 confirm_fill 回执前同样崩溃。"""
    _legacy("lot_cost_nan")
    with pytest.raises(ValidationError):
        _svc().snapshot()
    pm = PortfolioManager()
    facts = pm.request_account_facts()
    assert facts.read_failed is True
    assert facts.context_for(OTHER).position_state == "UNKNOWN"   # 无投影的代码：未对账，不当空仓
    ctx = facts.context_for(CODE)                                 # 有旧比例投影：仅保留持仓方向，权重待对账
    assert (ctx.position_state, ctx.confirmed_weight) == ("HELD", None)
    with pytest.raises(ValidationError):
        _svc().confirm_fill("after-poison", "", FillInput(
            security_id=CODE, action="BUY", quantity=100, price=12.0, trade_date="2026-09-21"))


# ── 复核 P2：超大整数数量（>309 位）可写入但快照转 float 溢出，同类毒化 ──
HUGE_QTY = 10 ** 400


def test_opening_import_rejects_huge_int_quantity(acct):
    n0 = _n_events()
    with pytest.raises(ValueError):
        _svc().opening_import(lots=[{"security_id": OTHER, "quantity": HUGE_QTY, "cost_price": 5.0,
                                     "acquired_at": "2026-09-20"}])
    assert _n_events() == n0
    _svc().snapshot()  # 账本未被毒化：快照仍可读


def test_entry_openinglot_rejects_huge_int_quantity(acct):
    n0 = _n_events()
    parsed, out, exc = _repl(f"pos openinglot {OTHER} {HUGE_QTY} 5.0 2026-09-20")
    assert parsed is None and exc is None
    assert "[!]" in out and "数量" in out
    assert _n_events() == n0
