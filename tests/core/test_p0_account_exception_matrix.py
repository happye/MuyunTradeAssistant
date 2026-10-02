"""P0 账户异常固定验收矩阵回归（plan/fusion iteration9；R15 Z1/Z2 + R15 裁决固定矩阵）

锁死语义（九行矩阵逐行断言数量状态与权重；账本/记录状态 × 投影或独立事实交叉）：
1. 异常源（PARTIAL/快照读取失败/投影枚举失败）不得从"查不到 lot""旧零投影"
   推导空仓——UNKNOWN/None/不反推股数（Z1：PARTIAL 保护贯穿投影分支）。
2. holding_entries 的 incomplete 必须传播投影枚举失败（Z2）——不能日志说待对账、
   返回值却声称完整；可独立列出的账本持仓仍保留。
3. 正常场景零变化：无账本 NONE、RATIO_ONLY 直通、健康账本正数量 HELD、
   数量不一致待对账——既有已验收语义回归不回退。
4. 清单与逐股 ctx 同请求不矛盾（同一 incomplete 语义）。
5. 读取前后账本文件字节不变（坏行隔离不自动改写）；同请求快照次数不增加。

隔离纪律：持久化路径全量重定向临时根；零网络零 AI。
跑法：pytest tests/core/test_p0_account_exception_matrix.py -q
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.data.account_service as account_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import src.core.shadow_diff as shadow_module
from src.data.account_service import AccountService
from src.data.account_snapshot import AccountEvent, AccountEventLog, EventType
from src.data.portfolio import PortfolioManager

CODE = "600519"


def _isolate(tmp_path, monkeypatch):
    """持久化路径全量重定向（先于一切构造）。"""
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    monkeypatch.setattr(shadow_module, "SHADOW_STORE_PATH", tmp_path / "shadow_diff.jsonl")


def _open_healthy_ledger(*, quantity=100, cash=10000.0):
    """健康账本：期初导入 quantity 股。"""
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=cash, trade_date="2026-09-20",
                       lots=[{"security_id": CODE, "quantity": quantity,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    return svc


def _append(event):
    return AccountEventLog(account_module.DEFAULT_LEDGER_PATH).append(event)


def _zero_qty_ledger():
    """健康账本、该股数量 0（期初 100 股 + 合法全部卖出）。"""
    svc = _open_healthy_ledger(quantity=100)
    _append(AccountEvent(event_id="sell_all", event_type=EventType.SELL,
                         security_id=CODE, trade_date="2026-09-21",
                         quantity=100, cash_delta=1000.0))
    snap = svc.snapshot()
    assert not snap.isolated_events, "构造前提：健康账本"
    assert any(h.security_id == CODE and h.quantity == 0 for h in snap.holdings), \
        "构造前提：该股数量 0"
    return svc


def _assert_ctx(ctx, *, state, qty, weight):
    """数量状态与权重分别断言（R15 矩阵口径）。"""
    assert ctx.position_state == state, \
        f"状态不符: {ctx.position_state}（{ctx.weight_reason}）"
    assert ctx.quantity == qty, f"数量不符: {ctx.quantity!r}（预期 {qty!r}）"
    assert ctx.confirmed_weight == weight, f"权重不符: {ctx.confirmed_weight!r}"


def _codes(entries):
    return [e.stock_code for e in entries]


# ── 矩阵行 1-2：正常无账本（兼容正例回归）────────────────────────────

def test_row1_no_ledger_no_projection_is_none_and_complete_empty_list(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    facts = pm.request_account_facts()
    _assert_ctx(facts.context_for(CODE), state="NONE", qty=0, weight=0.0)
    entries, incomplete = facts.holding_entries()
    assert entries == [] and incomplete is False, "完整空清单（不报假异常）"


def test_row2_no_ledger_legacy_ratio_held(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, entry_price=10, ratio=.1)
    facts = pm.request_account_facts()
    _assert_ctx(facts.context_for(CODE), state="HELD", qty=None, weight=.1)
    entries, incomplete = facts.holding_entries()
    assert _codes(entries) == [CODE] and incomplete is False


# ── 矩阵行 3-5：健康账本 ─────────────────────────────────────────────

def test_row3_healthy_ledger_zero_quantity_is_none_no_fake_anomaly(tmp_path, monkeypatch):
    """健康账本、数量 0 + 无投影/合法数量0投影 → NONE/0；不报假异常。"""
    _isolate(tmp_path, monkeypatch)
    _zero_qty_ledger()
    for with_projection in (False, True):
        pm = PortfolioManager()
        if with_projection:
            pm.add_position(CODE, entry_price=10, ratio=0.0)
            pm._data["positions"][CODE]["quantity_fact"] = {
                "quantity": 0, "as_of": "2026-09-21", "avg_cost": 10.0}
        facts = pm.request_account_facts()
        _assert_ctx(facts.context_for(CODE), state="NONE", qty=0, weight=0.0)
        entries, incomplete = facts.holding_entries()
        assert incomplete is False, f"健康账本不得报异常（projection={with_projection}）"


def test_row10_partial_ratio_only_zero_projection_unknown(tmp_path, monkeypatch):
    """guard P1（R15 矩阵补 cell）：PARTIAL × RATIO_ONLY 零比例投影 → UNKNOWN——
    同一 PARTIAL 状态下与数量投影形态（row7b UNKNOWN）同判，清单/ctx 不矛盾。
    真实形态：全部卖出+冷却记录恰为 ratio=0 且 quantity_fact 已弹出，账本坏行
    可能是隔离掉的一笔新买入——旧比例 0 不足以证明空仓。"""
    _isolate(tmp_path, monkeypatch)
    account_module.DEFAULT_LEDGER_PATH.write_text('{"truncated_event"\n', encoding="utf-8")
    pm = PortfolioManager()
    pm.add_position(CODE, entry_price=10, ratio=0.0)  # RATIO_ONLY 形态（无 quantity_fact）
    assert pm.get_position(CODE).quantity_fact is None
    facts = pm.request_account_facts()
    ctx = facts.context_for(CODE)
    _assert_ctx(ctx, state="UNKNOWN", qty=None, weight=None)
    entries, incomplete = facts.holding_entries()
    assert incomplete is True, "同请求清单与 ctx 必须同判（不矛盾）"


def test_row4_healthy_positive_quantity_no_projection_held(tmp_path, monkeypatch):
    """健康账本、正数量、无投影 → HELD、清单包含该股；缺 NAV 权重 None、成本不伪造。"""
    _isolate(tmp_path, monkeypatch)
    _open_healthy_ledger(quantity=100)
    pm = PortfolioManager()
    facts = pm.request_account_facts()
    _assert_ctx(facts.context_for(CODE), state="HELD", qty=100, weight=None)
    entries, incomplete = facts.holding_entries()
    assert _codes(entries) == [CODE] and incomplete is False
    entry = entries[0]
    assert entry.from_ledger is True
    assert entry.entry_price is None and entry.current_ratio is None, "成本/比例不伪造"


def test_row5_healthy_positive_quantity_conflicting_projection(tmp_path, monkeypatch):
    """健康账本、正数量 + 不一致数量投影 → 保留账本持仓、权重 None、对账原因。"""
    _isolate(tmp_path, monkeypatch)
    _open_healthy_ledger(quantity=100)
    pm = PortfolioManager()
    pm.add_position(CODE, entry_price=10, ratio=0.0)
    pm._data["positions"][CODE]["quantity_fact"] = {
        "quantity": 50, "as_of": "2026-09-21", "avg_cost": 10.0}
    facts = pm.request_account_facts()
    ctx = facts.context_for(CODE)
    _assert_ctx(ctx, state="HELD", qty=100, weight=None)
    assert "不一致" in ctx.weight_reason and "待对账" in ctx.weight_reason


# ── 矩阵行 6-7：PARTIAL ──────────────────────────────────────────────

@pytest.mark.parametrize("with_projection", [False, True])
def test_row6_partial_with_surviving_lot_keeps_holding(tmp_path, monkeypatch, with_projection):
    """PARTIAL、有幸存正 lot + 有/无投影 → 保留已知持仓、权重冻结、清单 incomplete。"""
    _isolate(tmp_path, monkeypatch)
    svc = _open_healthy_ledger(quantity=100)
    _append(AccountEvent(event_id="ghost", event_type=EventType.SELL,
                         security_id="000001", trade_date="2026-09-21",
                         quantity=50, cash_delta=500.0))
    assert svc.snapshot().data_completeness == "PARTIAL"
    pm = PortfolioManager()
    if with_projection:
        pm.add_position(CODE, entry_price=10, ratio=0.0)
        pm._data["positions"][CODE]["quantity_fact"] = {
            "quantity": 100, "as_of": "2026-09-21", "avg_cost": 10.0}
    facts = pm.request_account_facts()
    ctx = facts.context_for(CODE)
    _assert_ctx(ctx, state="HELD", qty=100, weight=None)
    assert "待对账" in ctx.weight_reason
    entries, incomplete = facts.holding_entries()
    assert _codes(entries) == [CODE] and incomplete is True


def test_row7_partial_no_lot_no_projection_unknown(tmp_path, monkeypatch):
    """PARTIAL、无幸存 lot、无投影 → UNKNOWN/None（O批已修，回归锚）；坏行不改原账本。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    account_module.DEFAULT_LEDGER_PATH.write_text('{"truncated_event"\n', encoding="utf-8")
    raw_before = account_module.DEFAULT_LEDGER_PATH.read_bytes()
    facts = pm.request_account_facts()
    _assert_ctx(facts.context_for(CODE), state="UNKNOWN", qty=None, weight=None)
    assert account_module.DEFAULT_LEDGER_PATH.read_bytes() == raw_before
    entries, incomplete = facts.holding_entries()
    assert incomplete is True


def test_row7b_partial_no_lot_zero_projection_unknown(tmp_path, monkeypatch):
    """R15 Z1 红灯主反例：PARTIAL、无幸存 lot、旧零投影 → 不得 NONE/0。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, entry_price=10, ratio=0.0)
    pm._data["positions"][CODE]["quantity_fact"] = {
        "quantity": 0, "as_of": "2026-09-19", "avg_cost": 0.0}
    account_module.DEFAULT_LEDGER_PATH.write_text('{"truncated_event"\n', encoding="utf-8")
    facts = pm.request_account_facts()
    assert facts._ledger_partial, "构造前提：PARTIAL"
    ctx = facts.context_for(CODE)
    _assert_ctx(ctx, state="UNKNOWN", qty=None, weight=None)
    entries, incomplete = facts.holding_entries()
    assert incomplete is True, "PARTIAL 清单必须 incomplete（与 ctx 不矛盾）"


# ── 矩阵行 8：快照读取失败 × 三种投影 ────────────────────────────────

@pytest.mark.parametrize("projection", ["none", "zero", "positive"])
def test_row8_snapshot_read_failure_all_unknown(tmp_path, monkeypatch, projection):
    """快照读取失败 + 无投影/数量0/正数量投影 → 全 UNKNOWN/None；数量提示
    不冒充核对通过（正数量投影给提示性 quantity 但状态仍 UNKNOWN）。"""
    _isolate(tmp_path, monkeypatch)
    _open_healthy_ledger(quantity=100)  # 账本真实存在（读取失败前入账）
    pm = PortfolioManager()
    if projection != "none":
        pm.add_position(CODE, entry_price=10, ratio=0.0)
        pm._data["positions"][CODE]["quantity_fact"] = {
            "quantity": 0 if projection == "zero" else 100,
            "as_of": "2026-09-19", "avg_cost": 10.0}
    with patch.object(AccountService, "snapshot", side_effect=OSError("fixture")):
        facts = pm.request_account_facts()
        ctx = facts.context_for(CODE)
        entries, incomplete = facts.holding_entries()
    assert ctx.position_state == "UNKNOWN", \
        f"projection={projection}: {ctx.position_state}（{ctx.weight_reason}）"
    assert ctx.confirmed_weight is None
    if projection == "positive":
        assert ctx.quantity == 100, "提示性数量保留（不冒充核对通过——状态已 UNKNOWN）"
    else:
        assert ctx.quantity is None
    assert incomplete is True


# ── 矩阵行 9：合法 YAML 但记录解码失败（R15 Z2）──────────────────────

@pytest.mark.parametrize("ledger", ["none", "independent"])
def test_row9_projection_decode_failure_propagates_incomplete(tmp_path, monkeypatch, ledger):
    """合法 YAML、某条目值 42（记录解码失败）→ 清单 incomplete；真 la 不得说
    当前无持仓；可独立列出的账本持仓仍保留。"""
    _isolate(tmp_path, monkeypatch)
    if ledger == "independent":
        _open_healthy_ledger(quantity=100)  # 账本侧独立可列
    import pathlib
    pathlib.Path(portfolio_module.DEFAULT_PORTFOLIO_PATH).write_text(
        "positions:\n  '600519': 42\n", encoding="utf-8")
    pm = PortfolioManager()
    assert not pm._corrupted, "构造前提：文件级语法合法（非损坏保护路径）"
    with pytest.raises(Exception):
        pm.list_positions(), "构造前提：真实记录解码失败"
    facts = pm.request_account_facts()
    entries, incomplete = facts.holding_entries()
    assert incomplete is True, f"Z2：投影枚举失败必须传播 incomplete（{len(entries)}条）"
    if ledger == "independent":
        assert _codes(entries) == [CODE], "可独立列出的账本持仓仍保留"
    else:
        assert entries == [], "无账本时清单为空但 incomplete"
