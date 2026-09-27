"""J2 账户确认闭环回归测试（plan/fusion iteration3，DELIVERY_PLAN J2 验收）

锁死语义：
1. N5 整事件原子生效：无持仓卖出/缺成本买入/现金方向不符 → 现金与份额都不动
   （isolated_events 留痕，data_completeness=PARTIAL）——不再「拒份额改现金」
2. 账本存在隔离事件 → allocate 精确新增冻结（CONDITIONAL 给阻塞话术，不产可买数量）
3. confirm_fill 协议：同 fill 同 payload 幂等回执；同 fill 不同内容 CONFLICT；
   版本不符 STALE；超卖/可卖批次不足（T+1）/无持仓/非法输入/主计划引用不符 REJECTED
4. 事务与崩溃恢复：append+fsync 成功才发布新版本；截断尾行读侧隔离；
   事实落账后投影失败 → 重跑同 fill_id 幂等恢复（账本不重复入账、投影不重复变动）
5. 进程内争写串行：同 fill 并发恰一 ACCEPTED；不同 fill 并发都入账
6. 生命周期：期初导入→建仓→加仓→部分卖→清仓→重建（T+1 日期递进）；快照可重放重建
7. today 一致性：同一账户服务读模型——未知现金显示未知不产可买数量；隔离冻结显式提示

纯内存+临时文件测试，零网络零 AI，不读写真实 HOME。
跑法：pytest tests/core/test_j2_account_service.py -q
"""
import json
import os
import sys
import threading
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.account_service import AccountService, FillInput, FillReceipt
from src.data.account_snapshot import (
    AccountEvent,
    AccountEventLog,
    AllocationState,
    EventType,
    allocate_tradeable_budget,
)


def _svc(tmp_path, plans_store=None):
    return AccountService(tmp_path / "events.jsonl", plans_store=plans_store)


def _fill(sec="600519", action="BUY", qty=100, price=10.0, fee=5.0,
          date="2026-09-20", **kw):
    return FillInput(security_id=sec, action=action, quantity=qty, price=price,
                     fee=fee, trade_date=date, **kw)


# ── 1. N5：整事件原子生效 ─────────────────────────────────

def test_n5_ghost_sell_does_not_touch_cash(tmp_path):
    """N5：无持仓 SELL 整事件隔离——现金 5000 保持 5000（原来被 +1000 变 6000）。"""
    log = AccountEventLog(tmp_path / "e.jsonl")
    log.append(AccountEvent(event_id="ghost", event_type=EventType.SELL,
                            security_id="600000", trade_date="2026-09-27",
                            quantity=100, price=10, cash_delta=1000))
    snap = log.replay(opening_cash=5000.0)
    assert snap.cash_available == 5000.0, "幽灵卖出不得动现金（整事件隔离）"
    assert snap.holdings == []
    assert len(snap.isolated_events) == 1
    assert snap.isolated_events[0]["event_id"] == "ghost"
    assert snap.data_completeness == "PARTIAL"


def test_n5_bad_buy_does_not_touch_cash(tmp_path):
    """N5：缺有效成本价的 BUY 整事件隔离——现金不再被静默扣减（原 -1000 变 4000）。"""
    log = AccountEventLog(tmp_path / "e.jsonl")
    log.append(AccountEvent(event_id="bad", event_type=EventType.BUY,
                            security_id="600000", trade_date="2026-09-27",
                            quantity=100, price=None, cash_delta=-1000))
    snap = log.replay(opening_cash=5000.0)
    assert snap.cash_available == 5000.0
    assert snap.holdings == []
    assert snap.isolated_events[0]["event_id"] == "bad"


def test_n5_sign_mismatch_isolated(tmp_path):
    """N5：现金方向与事件类型不符（BUY cash_delta>0）→ 整事件隔离。"""
    log = AccountEventLog(tmp_path / "e.jsonl")
    log.append(AccountEvent(event_id="weird", event_type=EventType.BUY,
                            security_id="600519", trade_date="2026-09-27",
                            quantity=100, price=10.0, lot_cost_price=10.0,
                            cash_delta=+1000))
    snap = log.replay(opening_cash=5000.0)
    assert snap.cash_available == 5000.0 and snap.holdings == []
    assert snap.isolated_events[0]["reason"].startswith("现金方向")


def test_isolated_events_freeze_precise_additions(tmp_path):
    """账本存在隔离事件 → allocate 精确新增冻结（CONDITIONAL，不产可买数量）。"""
    log = AccountEventLog(tmp_path / "e.jsonl")
    log.append(AccountEvent(event_id="ghost", event_type=EventType.SELL,
                            security_id="600000", trade_date="2026-09-27",
                            quantity=100, price=10, cash_delta=1000))
    snap = log.replay(opening_cash=5000.0)

    class _Line:
        stock_code = "600519"
        add_weight = 0.1

    class _Rules:
        def lot_rules(self, security_id, as_of):
            from src.data.account_snapshot import LotRules
            return LotRules(security_id=security_id, board="main", min_order_qty=100,
                            lot_step=100, effective_from="2020-01-01", source="fixture")

        def fee_model(self, security_id, as_of):
            from src.data.account_snapshot import FeeModel
            return FeeModel()

    out = allocate_tradeable_budget([_Line()], snap, _Rules(), as_of="2026-09-27",
                                    price_provider=lambda code: (10.0, None))
    assert out[0].allocation_state is AllocationState.CONDITIONAL
    assert out[0].quantity == 0, "冻结期不产可买数量"
    assert "待核对" in out[0].blocked_field


# ── 2. confirm_fill 协议 ──────────────────────────────────

def test_confirm_fill_accepted_then_duplicate_and_conflict(tmp_path):
    svc = _svc(tmp_path)
    v0 = svc.account_version()
    r1 = svc.confirm_fill("f1", v0, _fill())
    assert r1.status == "ACCEPTED" and r1.ok
    assert r1.event_id == "fill_f1"
    assert r1.cash_delta == pytest.approx(-(100 * 10.0 + 5.0))
    assert r1.account_version != v0, "持久化成功才发布新版本"
    snap = svc.snapshot()
    assert snap.cash_available is None, "期初未导入——现金未知，绝不冒充 0（None≠0）"
    assert snap.holdings[0].quantity == 100
    # 相同 fill_id + 相同规范化 payload → 原回执（幂等）
    r2 = svc.confirm_fill("f1", r1.account_version, _fill())
    assert r2.status == "DUPLICATE" and r2.ok
    assert r2.cash_delta == r1.cash_delta
    # 同 fill_id 不同内容 → CONFLICT
    r3 = svc.confirm_fill("f1", r1.account_version, _fill(qty=200))
    assert r3.status == "CONFLICT" and not r3.ok
    assert "不能当重复成功" in r3.reason
    assert len(svc.log.events()) == 1, "冲突不得再入账"


def test_confirm_fill_stale_version(tmp_path):
    svc = _svc(tmp_path)
    r1 = svc.confirm_fill("f1", "", _fill())  # 空 expected=不校验（首笔）
    assert r1.status == "ACCEPTED"
    r2 = svc.confirm_fill("f2", "v_stale", _fill(date="2026-09-21"))
    assert r2.status == "STALE" and not r2.ok
    assert "重读快照" in r2.reason
    assert len(svc.log.events()) == 1


def test_confirm_fill_rejections(tmp_path):
    from pydantic import ValidationError
    svc = _svc(tmp_path)
    # 非法输入：构造层约束拦截（pydantic）——服务层 _validate 为 dict 形态输入兜底
    assert svc.confirm_fill("f1", "", _fill(sec="abc")).status == "REJECTED"
    with pytest.raises(ValidationError):
        _fill(qty=0)
    with pytest.raises(ValidationError):
        _fill(price=0)
    with pytest.raises(ValidationError):
        _fill(fee=-1)
    assert svc.confirm_fill("f1", "", _fill(date="2026/09/20")).status == "REJECTED"
    assert svc.confirm_fill("", "", _fill()).status == "REJECTED"
    # 无持仓卖出
    r = svc.confirm_fill("s1", "", _fill(action="SELL", date="2026-09-20"))
    assert r.status == "REJECTED" and "无持仓可卖" in r.reason
    # 建仓后同日卖出 → T+1 可卖批次不足
    assert svc.confirm_fill("b1", "", _fill(date="2026-09-20")).status == "ACCEPTED"
    r2 = svc.confirm_fill("s2", "", _fill(action="SELL", qty=50, date="2026-09-20"))
    assert r2.status == "REJECTED" and "T+1" in r2.reason
    # 超卖（持仓 100，次日卖 150）
    r3 = svc.confirm_fill("s3", "", _fill(action="SELL", qty=150, date="2026-09-21"))
    assert r3.status == "REJECTED" and "超卖" in r3.reason
    assert len(svc.log.events()) == 1, "所有 REJECTED 都不入账"


def test_confirm_fill_t1_sellable_next_day(tmp_path):
    svc = _svc(tmp_path)
    assert svc.confirm_fill("b1", "", _fill(date="2026-09-20")).status == "ACCEPTED"
    r = svc.confirm_fill("s1", "", _fill(action="SELL", qty=60, date="2026-09-21"))
    assert r.status == "ACCEPTED"
    snap = svc.snapshot()
    assert snap.holdings[0].quantity == 40
    assert len(snap.holdings[0].lots) == 1  # 最早批次被扣


def test_confirm_fill_plan_ref_mismatch(tmp_path):
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    from src.data.horizon_plans import HorizonPlanStore
    from src.core.decision_contract import Horizon
    store = HorizonPlanStore(tmp_path / "plans.json")
    store.save(HorizonPlan(plan_id="p_mid_1", security_id="600519", horizon=Horizon.MID,
                           policy_id=POLICY_ID_MID, intent="测试计划"))
    svc = _svc(tmp_path, plans_store=store)
    r = svc.confirm_fill("f1", "", _fill(plan_ref="p_wrong"))
    assert r.status == "REJECTED" and "主计划引用不匹配" in r.reason
    r2 = svc.confirm_fill("f2", "", _fill(plan_ref="p_mid_1"))
    assert r2.status == "ACCEPTED"


def test_confirm_fill_records_deviation_not_falsification(tmp_path):
    """实际成交与建议不同允许记录真实结果并标偏离——不为建议吻合篡改事实。"""
    svc = _svc(tmp_path)
    r = svc.confirm_fill("f1", "", _fill(price=11.0, deviation_from_proposal=True,
                                         note="建议价10，实际追高成交"))
    assert r.status == "ACCEPTED"
    e = svc.log.events()[0]
    assert e.model_dump().get("deviation_from_proposal") is True
    assert "追高" in (e.model_dump().get("fill_note") or "")


# ── 3. 事务与崩溃恢复 ─────────────────────────────────────

def test_truncated_last_line_isolated_rebuild(tmp_path):
    """崩溃点：append 写一半断电 → 截断尾行快照隔离留痕（G14）。

    K0a/D2 收紧：损坏段进 isolated_events/PARTIAL，写侧**拒绝向损坏尾部追加**
    （旧口径「照常 ACCEPTED」会把新成交粘在损坏行上、重放整行丢失）；人工修复
    损坏尾段后恢复写入。"""
    svc = _svc(tmp_path)
    assert svc.confirm_fill("f1", "", _fill()).status == "ACCEPTED"
    with open(tmp_path / "events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"event_id": "fill_f2", "event_type": "BUY", "secur')  # 截断半行
    snap = svc.snapshot()
    assert snap.holdings[0].quantity == 100, "坏行隔离，好事件照常重建"
    assert snap.data_completeness == "PARTIAL" and snap.isolated_events, \
        "损坏段必须进快照隔离留痕（不能只打日志）"
    r = svc.confirm_fill("f2", "", _fill(date="2026-09-21"))
    assert r.status == "REJECTED" and not r.ok, \
        "完整性先于写入：损坏尾部冻结新写（不再假 ACCEPTED）"
    # 用户人工修复损坏尾段（备份后删除坏行）→ 恢复写入
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "events.jsonl").write_text(lines[0] + "\n", encoding="utf-8")
    assert svc.confirm_fill("f2", "", _fill(date="2026-09-21")).status == "ACCEPTED"


def test_crash_between_fact_and_projection_idempotent_recover(tmp_path):
    """崩溃点：事实已入账、比例投影未更新 → 重跑同一命令：账本 DUPLICATE
    （不重复入账）+ 投影按 fill_id 幂等补做（不重复变动）。"""
    from src.data.portfolio import PortfolioManager
    svc = _svc(tmp_path)
    pm = PortfolioManager(portfolio_path=str(tmp_path / "portfolio.yaml"),
                          proposals_path=str(tmp_path / "proposals.json"))
    pm.add_position("600519", stock_name="测试", entry_price=10.0, ratio=0.10)
    fill_id = "prop-1#1"
    payload = _fill(date="2026-09-20")
    r1 = svc.confirm_fill(fill_id, svc.account_version(), payload)  # 事实落账
    assert r1.status == "ACCEPTED"
    # 假设此处崩溃：投影未更新。恢复：
    r2 = svc.confirm_fill(fill_id, svc.account_version(), payload)
    assert r2.status == "DUPLICATE", "账本幂等——不重复入账"
    res1 = pm.confirm_fill("600519", "BUY", 0.05, price=10.0, fill_id=fill_id)
    assert res1.ok
    res2 = pm.confirm_fill("600519", "BUY", 0.05, price=10.0, fill_id=fill_id)
    assert res2.ok and "幂等" in res2.message, "投影按 fill_id 幂等——不重复变动"
    assert len(svc.log.events()) == 1


def test_concurrent_same_fill_serialized(tmp_path):
    """进程内争写：同 fill 并发确认恰一 ACCEPTED（文件锁+键锁串行）。"""
    svc = _svc(tmp_path)
    results = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        r = svc.confirm_fill("f1", "", _fill())
        results.append(r.status)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ACCEPTED") == 1
    assert results.count("DUPLICATE") == 3
    assert len(svc.log.events()) == 1


def test_concurrent_different_fills_all_recorded(tmp_path):
    """不同 fill 并发确认 → 全部入账（串行不丢）。"""
    svc = _svc(tmp_path)
    barrier = threading.Barrier(3)

    def worker(i):
        barrier.wait()
        svc.confirm_fill(f"f{i}", "", _fill(date=f"2026-09-2{i}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(svc.log.events()) == 3
    assert svc.snapshot().holdings[0].quantity == 300


# ── 4. 期初导入与生命周期 ─────────────────────────────────

def test_opening_import_anchors_cash_and_lots(tmp_path):
    svc = _svc(tmp_path)
    receipts = svc.opening_import(opening_cash=100_000.0,
                                  lots=[{"security_id": "600519", "quantity": 300,
                                         "cost_price": 10.0, "acquired_at": "2026-09-01"}])
    assert all(r.ok for r in receipts)
    snap = svc.snapshot()
    assert snap.cash_available == 100_000.0, "OPENING 锚定期初现金"
    assert snap.data_completeness == "QUANTITY_LEVEL"
    assert snap.holdings[0].quantity == 300
    # 幂等：同内容重复导入返回原回执
    again = svc.opening_import(opening_cash=100_000.0,
                               lots=[{"security_id": "600519", "quantity": 300,
                                      "cost_price": 10.0, "acquired_at": "2026-09-01"}])
    assert all(r.status == "DUPLICATE" for r in again)
    assert len(svc.log.events()) == 2


def test_pick_reusable_fill_id_numeric_seq_and_recovery(tmp_path):
    """J5 审查 P1-1 回归：序号取最大按**数字**（"9" 与 "10" 字典序陷阱）；
    崩溃恢复态（指纹一致+未消费）复用同 id；已消费/指纹不符 → 新序号。"""
    from src.data.account_service import pick_reusable_fill_id
    log = AccountEventLog(tmp_path / "e.jsonl")
    # 已有 9 笔（含 #9 与 #10——字典序下 "#9">"#10"）
    for i in list(range(1, 10)) + [10]:
        log.append(AccountEvent(event_id=f"fill:p1#{i}", event_type=EventType.BUY,
                                security_id="600519", trade_date="2026-09-20",
                                quantity=1, price=1.0, cash_delta=-1.0,
                                lot_cost_price=1.0, related_fill_id=f"p1#{i}"))
    events = log.events()
    fp_same = "fp_same"
    # 构造 #10 与待提交 payload 同指纹：直接复用事件自身指纹做期望
    from src.data.account_service import _event_fingerprint
    last_fp = _event_fingerprint(next(e for e in events if e.related_fill_id == "p1#10"))
    # 崩溃恢复态（未消费）→ 复用 #10（字典序实现会错取 #9 → 新建 #11 → 双记账）
    assert pick_reusable_fill_id(events, "p1#", last_fp,
                                 has_fill_fn=lambda rid: False) == "p1#10"
    # 已消费 → 新序号 #11
    assert pick_reusable_fill_id(events, "p1#", last_fp,
                                 has_fill_fn=lambda rid: True) == "p1#11"
    # 指纹不符 → 新序号 #11（新的真实成交）
    assert pick_reusable_fill_id(events, "p1#", "fp_other",
                                 has_fill_fn=lambda rid: False) == "p1#11"
    # 空账本 → #1
    assert pick_reusable_fill_id([], "p2#", "fp_x") == "p2#1"


def test_full_position_lifecycle_with_rebuild(tmp_path):
    """无仓→建仓→加仓→部分卖→清仓→重建 主计划生命周期（快照逐阶段可重放重建）。"""
    svc = _svc(tmp_path)
    svc.opening_import(opening_cash=200_000.0)
    # 建仓
    assert svc.confirm_fill("f1", svc.account_version(),
                            _fill(qty=500, price=10.0, date="2026-09-01")).status == "ACCEPTED"
    # 加仓（次日）
    assert svc.confirm_fill("f2", svc.account_version(),
                            _fill(qty=300, price=12.0, date="2026-09-02")).status == "ACCEPTED"
    snap = svc.snapshot()
    assert snap.holdings[0].quantity == 800 and len(snap.holdings[0].lots) == 2
    # 部分卖（第三日，先扣最早批次）
    assert svc.confirm_fill("f3", svc.account_version(),
                            _fill(action="SELL", qty=200, price=13.0, date="2026-09-03")
                            ).status == "ACCEPTED"
    snap = svc.snapshot()
    assert snap.holdings[0].quantity == 600
    assert snap.holdings[0].lots[0].quantity == 300, "最早批次（500股@10）先扣 200 剩 300"
    # 清仓
    assert svc.confirm_fill("f4", svc.account_version(),
                            _fill(action="SELL", qty=600, price=14.0, date="2026-09-04")
                            ).status == "ACCEPTED"
    snap = svc.snapshot()
    assert all(h.quantity == 0 for h in snap.holdings) or snap.holdings[0].quantity == 0
    # 重建（新一轮建仓）
    assert svc.confirm_fill("f5", svc.account_version(),
                            _fill(qty=100, price=15.0, date="2026-09-05")).status == "ACCEPTED"
    # 全程重放一致（崩溃恢复语义：同事件集重放结果一致）
    rebuilt = AccountService(tmp_path / "events.jsonl").snapshot()
    assert rebuilt.holdings[0].quantity == 100
    assert rebuilt.isolated_events == []
    cash_expected = 200_000.0 - (500 * 10 + 5) - (300 * 12 + 5) + (200 * 13 - 5) \
        + (600 * 14 - 5) - (100 * 15 + 5)
    assert rebuilt.cash_available == pytest.approx(cash_expected)


# ── 5. today 一致性（同一账户服务读模型）──────────────────

def test_today_account_section_unknown_cash_not_zero(tmp_path):
    """未知现金显示未知——不产可买数量、不冒充 0。"""
    from src.cli.today_service import build_today_view
    from src.data.portfolio import PortfolioManager
    svc = _svc(tmp_path)
    svc.confirm_fill("f1", "", _fill())  # 只有买入事件——期初未知
    pm = PortfolioManager(portfolio_path=str(tmp_path / "portfolio.yaml"),
                          proposals_path=str(tmp_path / "proposals.json"))
    view = build_today_view(pm, account=svc.snapshot())
    assert any("现金未知" in line for line in view.account)
    assert not any("可买" in line and "不产可买数量" not in line
                   for line in view.account), "未知现金不得产生可买数量话术"


def test_today_account_section_consistent_with_service(tmp_path):
    """确认后 today 账户区与账户服务快照一致（现金/股数/隔离冻结）。"""
    from src.cli.today_service import build_today_view
    from src.data.portfolio import PortfolioManager
    svc = _svc(tmp_path)
    svc.opening_import(opening_cash=50_000.0)
    svc.confirm_fill("f1", "", _fill(qty=200, price=10.0, fee=5.0, date="2026-09-20"))
    pm = PortfolioManager(portfolio_path=str(tmp_path / "portfolio.yaml"),
                          proposals_path=str(tmp_path / "proposals.json"))
    view = build_today_view(pm, account=svc.snapshot())
    assert any("47,995" in line for line in view.account), \
        f"现金=50000-2005={50000 - 2005}: {[l for l in view.account]}"
    assert any("600519 持有 200 股" in line for line in view.account)
    # 隔离事件 → 冻结提示进 notices
    svc2 = _svc(tmp_path / "r2")
    svc2.opening_import(opening_cash=5000.0)
    (tmp_path / "r2" / "events.jsonl").open("a", encoding="utf-8").write(
        json.dumps({"event_id": "ghost", "event_type": "SELL", "security_id": "600000",
                    "trade_date": "2026-09-27", "quantity": 100, "price": 10,
                    "cash_delta": 1000, "fee": 0, "related_fill_id": ""}) + "\n")
    pm2 = PortfolioManager(portfolio_path=str(tmp_path / "portfolio2.yaml"),
                           proposals_path=str(tmp_path / "proposals2.json"))
    view2 = build_today_view(pm2, account=svc2.snapshot())
    assert any("待核对事件" in n for n in view2.notices), "隔离事件显式提示（不静默）"


def test_today_without_ledger_keeps_legacy_view(tmp_path):
    """无数量级账本 → 账户区为空（不发明数字），legacy 比例视图不受累。"""
    from src.cli.today_service import build_today_view
    from src.data.portfolio import PortfolioManager
    pm = PortfolioManager(portfolio_path=str(tmp_path / "portfolio.yaml"),
                          proposals_path=str(tmp_path / "proposals.json"))
    view = build_today_view(pm, account=None)
    assert view.account == []
