"""K0a 账户完整性/现金口径回归测试（plan/fusion iteration4，DELIVERY_PLAN K0a）

锁死语义（架构师深审 D2/D7——反例先红后绿）：
1. D2 完整性先于写入：损坏尾行 → 快照隔离留痕（PARTIAL+行号/偏移/hash）；写侧
   拒绝向截断 JSON 尾部追加（旧口径 ACCEPTED 会把新成交接在损坏尾部、重放丢失）；
   完整 JSON 缺最终换行 ≠ 损坏（追加前补换行）；中部坏行只隔离不冻结追加
2. 回执三分：未写（REJECTED）/ 已持久化（ACCEPTED/DUPLICATE）/ 写入状态待核对
   （PERSIST_UNKNOWN——fsync 失败不得当普通成功；重试幂等可核对）
3. D7 现金口径：cash_available 按声明合同=已扣冻结净额；reserved 仅解释不再二次减
   （含冻结的分配测试）

纯内存+临时文件，零网络零 AI，不读写真实 HOME。
跑法：pytest tests/core/test_k0a_account_integrity.py -q
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.account_service import AccountService, FillInput
from src.data.account_snapshot import (
    AccountSnapshot,
    AllocationState,
    FeeModel,
    LotRules,
    allocate_tradeable_budget,
)


def _svc(tmp_path):
    return AccountService(tmp_path / "events.jsonl")


def _fill(sec="600519", action="BUY", qty=100, price=10.0, fee=5.0,
          date="2026-09-21", **kw):
    return FillInput(security_id=sec, action=action, quantity=qty, price=price,
                     fee=fee, trade_date=date, **kw)


def _append_raw(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


# ── 1. D2：损坏尾行进快照隔离留痕 ──────────────────────────

def test_d2_corrupt_tail_isolated_in_snapshot(tmp_path):
    """D2：截断尾行必须进 isolated_events（旧口径只 logger 告警、快照看不见）——
    data_completeness=PARTIAL + 行号/偏移保留供人工恢复。"""
    svc = _svc(tmp_path)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20")
    _append_raw(svc.log.path, '{"truncated":')  # 无换行截断 JSON
    snap = svc.snapshot()
    assert snap.isolated_events, "损坏段必须进隔离留痕（不能只打日志）"
    entry = snap.isolated_events[-1]
    assert entry.get("line_no") == 2 and entry.get("byte_offset") > 0, \
        f"恢复信息（行号/偏移）必须结构化保留: {entry}"
    assert snap.data_completeness == "PARTIAL"
    assert snap.cash_available == 10000.0, "已持久化事件不受损坏段影响"


# ── 2. D2：写完整性门（主反例）────────────────────────────

def test_d2_confirm_after_corrupt_tail_rejected_not_accepted(tmp_path):
    """D2 主反例：坏尾后确认新成交——必须 REJECTED（未写），不再 ACCEPTED 把事件
    接在损坏尾部（重放整行丢失）。账本字节不动（原件保留供恢复）。"""
    svc = _svc(tmp_path)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20")
    _append_raw(svc.log.path, '{"truncated":')
    before_bytes = svc.log.path.read_bytes()
    receipt = svc.confirm_fill("after_tail", svc.account_version(), _fill())
    assert receipt.status == "REJECTED" and not receipt.ok, \
        f"坏尾后新写必须拒绝（旧口径 ACCEPTED 丢成交）: {receipt.status}"
    assert "损坏" in receipt.reason, f"回执必须可操作（指出损坏与处理建议）: {receipt.reason}"
    assert svc.log.path.read_bytes() == before_bytes, "拒绝追加不得改动原件（保留供恢复）"
    snap = svc.snapshot()
    assert not snap.holdings, "新成交不得入账"
    assert snap.cash_available == 10000.0


def test_d2_opening_import_refused_on_corrupt_tail(tmp_path):
    """期初导入同样过完整性门——坏尾时拒绝追加（未写）。"""
    svc = _svc(tmp_path)
    svc.opening_import(opening_cash=100.0, trade_date="2026-09-20")
    _append_raw(svc.log.path, '{"truncated":')
    receipts = svc.opening_import(opening_cash=200.0, trade_date="2026-09-21")
    assert receipts and receipts[0].status == "REJECTED" and not receipts[0].ok


def test_d2_duplicate_check_still_answerable_with_corrupt_tail(tmp_path):
    """幂等/冲突判定是只读操作——尾部损坏时重试同一 fill 仍可得到 DUPLICATE
    （重试可核对，不用等修文件）。"""
    svc = _svc(tmp_path)
    assert svc.confirm_fill("f1", "", _fill()).status == "ACCEPTED"
    _append_raw(svc.log.path, '{"truncated":')
    r2 = svc.confirm_fill("f1", "", _fill())
    assert r2.status == "DUPLICATE" and r2.ok


# ── 3. D2：完整 JSON 缺尾换行 ≠ 损坏；中部坏行不冻结追加 ──

def test_d2_complete_json_without_trailing_newline_can_append(tmp_path):
    """「完整 JSON 但缺最终换行」与截断 JSON 分开处理：快照正常解析（不算损坏），
    新事件追加前补换行成功、重放完整。"""
    svc = _svc(tmp_path)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20")
    data = svc.log.path.read_bytes()
    assert data.endswith(b"\n")
    svc.log.path.write_bytes(data.rstrip(b"\n"))
    snap = svc.snapshot()
    assert snap.cash_available == 10000.0 and not snap.isolated_events, \
        "完整 JSON 缺换行不算损坏"
    r = svc.confirm_fill("f1", svc.account_version(), _fill())
    assert r.status == "ACCEPTED" and r.ok
    snap2 = svc.snapshot()
    assert snap2.holdings[0].quantity == 100, "补换行后事件完整可重放（不丢）"


def test_d2_midfile_corrupt_isolated_but_append_allowed(tmp_path):
    """中部坏行：隔离留痕（PARTIAL）；尾部完好 → 不冻结追加（写门只针对截断尾部）。"""
    svc = _svc(tmp_path)
    assert svc.confirm_fill("f1", "", _fill()).status == "ACCEPTED"
    assert svc.confirm_fill("f2", "", _fill(date="2026-09-22")).status == "ACCEPTED"
    lines = svc.log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    lines.insert(1, "{broken json")  # 中部坏行（后面还有完好事件行）
    svc.log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    snap = svc.snapshot()
    assert snap.isolated_events and snap.data_completeness == "PARTIAL"
    r3 = svc.confirm_fill("f3", "", _fill(date="2026-09-23"))
    assert r3.status == "ACCEPTED", "尾部完好时追加不受中部坏行影响（坏行已隔离留痕）"


# ── 4. 回执三分：fsync 失败 → PERSIST_UNKNOWN ─────────────

def test_d2_fsync_failure_persist_unknown_not_success(tmp_path, monkeypatch):
    """fsync 失败不得当普通成功——回执 PERSIST_UNKNOWN（写入状态待核对，ok=False）；
    重试同一 fill 幂等返回 DUPLICATE（事件已在盘上，可核对）。"""
    import os as _os
    svc = _svc(tmp_path)

    def _boom(fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(_os, "fsync", _boom)
    r1 = svc.confirm_fill("f1", "", _fill())
    assert r1.status == "PERSIST_UNKNOWN" and not r1.ok, \
        f"fsync 失败不得返回 ACCEPTED（假成功）: {r1.status}"
    assert "核对" in r1.reason or "重跑" in r1.reason
    monkeypatch.undo()
    r2 = svc.confirm_fill("f1", "", _fill())
    assert r2.status == "DUPLICATE" and r2.ok, "重试可核对：事件已在账本，幂等返回"
    assert svc.snapshot().holdings[0].quantity == 100


def test_d2_fault_injection_before_append_zero_write(tmp_path, monkeypatch):
    """逐写点故障注入·追加前：append 抛 OSError → REJECTED（零写入、半个事件都
    没有）；修复后重试干净入账。"""
    from src.data.account_snapshot import AccountEventLog
    svc = _svc(tmp_path)
    orig_append = AccountEventLog.append

    def _boom(self, event):
        raise OSError("simulated disk full")

    monkeypatch.setattr(AccountEventLog, "append", _boom)
    r = svc.confirm_fill("f1", "", _fill())
    assert r.status == "REJECTED" and not r.ok
    assert "未写入" in r.reason
    monkeypatch.setattr(AccountEventLog, "append", orig_append)
    r2 = svc.confirm_fill("f1", "", _fill())
    assert r2.status == "ACCEPTED" and r2.ok
    assert svc.snapshot().holdings[0].quantity == 100


# ── 5. D7：现金口径统一（净额，reserved 仅解释）────────────

def _snap(cash, reserved=0.0, nav=1_000_000.0):
    return AccountSnapshot(as_of=datetime(2026, 9, 25, tzinfo=timezone.utc),
                           cash_available=cash, cash_reserved=reserved, nav=nav,
                           data_completeness="QUANTITY_LEVEL")


class _Rules:
    def lot_rules(self, security_id, as_of):
        return LotRules(security_id=security_id, board="main", min_order_qty=100,
                        lot_step=100, effective_from="2006-01-01", source="fixture")

    def fee_model(self, security_id, as_of):
        return FeeModel()


class _Line:
    stock_code = "600519"
    add_weight = 0.5


def test_d7_cash_deployable_is_net_per_declared_contract():
    """D7：合同原文=cash_available 已扣冻结、reserved 另记不重复减——deployable
    必须等于 cash_available（旧实现再减一次 reserved，与合同矛盾）。"""
    snap = _snap(cash=1000.0, reserved=200.0)
    assert snap.cash_deployable == 1000.0, \
        f"净额口径：{snap.cash_deployable} != 1000（旧实现错减 reserved）"
    assert _snap(cash=None, reserved=200.0).cash_deployable is None


def test_d7_allocation_uses_full_net_cash_with_reserved():
    """D7 含冻结分配测试：reserved 只做解释。可用 10005、冻结 200 → 足额买
    1000 股@10（10000+5 佣金=10005 恰好够）；旧实现错减成 9805 只够 900 股。"""
    out = allocate_tradeable_budget([_Line()], _snap(cash=10005.0, reserved=200.0),
                                    _Rules(), as_of="2026-09-25",
                                    price_provider=lambda c: 10.0)
    a = out[0]
    assert a.allocation_state == AllocationState.FEASIBLE
    assert a.quantity == 1000, f"reserved 不得二次扣减: {a.quantity}"
