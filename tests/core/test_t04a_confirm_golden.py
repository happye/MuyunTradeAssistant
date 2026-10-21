"""T04-A-0 黄金输出测试：pos confirm 数量路径（manage_positions("confirm", qty>0)）

目的：在把编排下沉到 account_service 之前，逐字锁住当前 CLI 行为——
  ① 控制台输出完整行序列（rich Console 宽 200、无色、写 StringIO，去尾空白）
  ② 磁盘结果：账本事件数/最后事件字段、快照持股、PortfolioManager 投影、建议状态
下沉后用同一文件逐字比对；任何文案/顺序差异 = 回滚（DELIVERY_PLAN T04-A 停止条件）。

隔离：账本=DEFAULT_LEDGER_PATH、计划库=PLANS_FILE 均 monkeypatch 指向 tmp_path；
PortfolioManager 用显式 tmp 路径（cli_main.PortfolioManager 换成每次返回新实例的工厂，
更贴近真实「每次命令新建管理器」）。proposal_id 固定，fill_id 因而确定（`<pid>#N`）。
归一化：仅 `版本 v<16位hex>`（账本字节哈希，含事件 recorded_at 时间戳，运行间不稳定）
→ `版本 <VER>`；磁盘字典里未被确认触碰的投影记录日期（add_position 当天）→ `<SEED_DAY>`；
期初批次事件 id 随机后缀 → `<opening>`；其余逐字比对。
真实/桩：除下列「桩」外全部走真实路径（AccountService/账本/PortfolioManager/建议库）。
  - PERSIST_UNKNOWN：仅桩 account_service._fsync_ledger 返回 False，confirm_fill 其余真实
  - 账本拒绝：真实（超卖，REJECTED）
  - 投影同步失败：仅桩 PortfolioManager._save 返回 SaveResult(ok=False)，apply_quantity_fill 真实
  - 崩溃恢复：首跑桩 apply_quantity_fill 抛 RuntimeError（模拟投影前崩溃），重跑全真实

跑法：pytest tests/core/test_t04a_confirm_golden.py -q
"""
import io
import os
import re
import sys

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.data.account_service as account_module
import src.data.horizon_plans as horizon_plans_module
from src.data.account_service import AccountService
from src.data.portfolio import PortfolioManager, SaveResult
from src.data.proposals import Proposal

CODE = "600519"
TRADE_DATE = "2026-09-21"      # 固定成交日（期初批次 2026-09-20，T+1 可卖）
_VER_RE = re.compile(r"版本 v[0-9a-f]{16}")


# ── 夹具 ──────────────────────────────────────────────────

class _Env:
    """隔离环境：tmp 内的 portfolio/proposals/账本/计划库 + 输出捕获。"""

    def __init__(self, tmp_path, monkeypatch):
        self.dir = tmp_path / "gold"
        self.dir.mkdir()
        self.portfolio = self.dir / "portfolio.yaml"
        self.proposals = self.dir / "proposals.json"
        self.ledger = self.dir / "events.jsonl"
        self.plans = self.dir / "plans.json"
        self.out = io.StringIO()
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", self.ledger)
        monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", self.plans)
        monkeypatch.setattr(cli_main, "PortfolioManager", self.new_pm)
        monkeypatch.setattr(cli_main, "console", Console(
            file=self.out, width=200, color_system=None, force_terminal=False))
        root = str(tmp_path.resolve())
        for p in (self.portfolio, self.proposals, self.ledger, self.plans):
            assert os.path.abspath(str(p)).startswith(root), f"持久化路径越出临时根: {p}"

    def new_pm(self):
        return PortfolioManager(str(self.portfolio), str(self.proposals))

    def svc(self, plans_store=None):
        return AccountService(self.ledger, plans_store=plans_store)

    def seed(self, *, action, target_ratio, held, avg_cost=10.0, ratio=0.1,
             proposal_id="gp", cash=100000.0):
        """比例视图 10% + 待确认建议；账本期初 held 股@avg_cost + 现金。"""
        pm = self.new_pm()
        pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=ratio)
        pm._proposals.add_proposal(Proposal(
            proposal_id=proposal_id, stock_code=CODE, stock_name="测试股",
            position_action=action, target_ratio=target_ratio, current_ratio=ratio))
        assert pm._proposals._save()
        self.svc().opening_import(
            opening_cash=cash, trade_date="2026-09-20",
            lots=[{"security_id": CODE, "quantity": held, "cost_price": avg_cost,
                   "acquired_at": "2026-09-20"}])
        return proposal_id

    def seed_active_plan(self, plan_id="p_mid_gold"):
        """计划库：已接受的 MID 计划 + account×security 主意图引用。"""
        from src.core.decision_contract import Horizon
        from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
        ps = horizon_plans_module.HorizonPlanStore(self.plans)
        ps.save(HorizonPlan(plan_id=plan_id, security_id=CODE, horizon=Horizon.MID,
                            policy_id=POLICY_ID_MID, intent="T04-A 黄金测试"))
        assert ps.accept(CODE, "MID")[0]
        assert ps.set_active_ref(CODE, ps.get(CODE, "MID"))[0]
        return plan_id

    def confirm(self, **kw):
        kw.setdefault("stock_code", CODE)
        kw.setdefault("trade_date", TRADE_DATE)
        cli_main.manage_positions("confirm", **kw)

    def lines(self):
        """当前累计输出的行序列（去尾空白、版本号归一化；保留空行以锁定段落）。"""
        text = _VER_RE.sub("版本 <VER>", self.out.getvalue())
        return [ln.rstrip() for ln in text.rstrip("\n").split("\n")]

    def mark(self):
        """取出到目前为止的输出行，并清空缓冲（多次 confirm 的场景分段比对）。"""
        got = self.lines()
        self.out.seek(0)
        self.out.truncate(0)
        return got

    def disk(self, proposal_id="gp"):
        """磁盘事实快照：全部来自新实例/新读盘（不信内存对象）。"""
        svc = self.svc()
        events = svc.log.events()
        last = events[-1].model_dump() if events else {}
        snap = svc.snapshot()
        h = next((x for x in snap.holdings if x.security_id == CODE), None)
        pm = self.new_pm()
        pos = pm.get_position(CODE)
        prop = next((p for p in pm._proposals._proposals if p.proposal_id == proposal_id), None)
        return {
            "event_count": len(events),
            "last_event": {
                "event_id": last.get("event_id"),
                "type": getattr(last.get("event_type"), "value", last.get("event_type")),
                "security_id": last.get("security_id"),
                "trade_date": last.get("trade_date"),
                "quantity": last.get("quantity"),
                "price": last.get("price"),
                "cash_delta": last.get("cash_delta"),
                "fee": last.get("fee"),
                "related_fill_id": last.get("related_fill_id"),
                "plan_ref": last.get("plan_ref"),
                "deviation": last.get("deviation_from_proposal"),
                "fill_note": last.get("fill_note"),
            },
            "held_qty": h.quantity if h else 0,
            "avg_cost": h.avg_cost if h else None,
            "projection": None if pos is None else {
                "current_ratio": pos.current_ratio,
                "lifecycle": pos.lifecycle,
                "ratio_stale": pos.ratio_stale,
                "quantity_fact": pos.quantity_fact,
                "entry_price": pos.entry_price,
                "last_action": pos.last_action,
                # 未被确认触碰的记录（last_action=OPEN）日期是 add_position 当天（随运行日变）
                "last_action_date": ("<SEED_DAY>" if pos.last_action == "OPEN"
                                     else pos.last_action_date),
            },
            "applied_fills": sorted(pm._data.get("applied_fills", {}).keys()),
            "has_fill_book": sorted(f.fill_id for f in pm._proposals._fills)
            if hasattr(pm._proposals, "_fills") else None,
            "proposal_status": prop.status.value if prop else None,
            "proposal_note": prop.note if prop else None,
        }


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _Env(tmp_path, monkeypatch)


# ── 期望值构造（只压缩重复字面量；每个场景的输出行与关键字段仍逐字写出）──

_HEAD = ["", "📌 确认成交"]
_OPEN_DAY_LAST = {"last_action": "OPEN", "last_action_date": "<SEED_DAY>"}


def _ev(**kw):
    base = {"security_id": CODE, "trade_date": TRADE_DATE, "fee": 0.0, "plan_ref": "",
            "deviation": False, "fill_note": "proposal=gp"}
    base.update(kw)
    return base


def _proj(qty, avg, last_action):
    return {"current_ratio": 0.1, "lifecycle": "OPEN", "ratio_stale": True,
            "entry_price": avg, "last_action": last_action, "last_action_date": TRADE_DATE,
            "quantity_fact": {"as_of": TRADE_DATE, "avg_cost": avg, "quantity": qty}}


_PROJ_UNTOUCHED = {"current_ratio": 0.1, "lifecycle": "OPEN", "ratio_stale": False,
                   "entry_price": 10.0, "quantity_fact": None, **_OPEN_DAY_LAST}


def _note(verb, n, held, action):
    return (f"本笔{verb}{n}股，现持有{held}股——无法以股数事实核验建议（{action}）的完成度，"
            "建议保持待确认；如继续请再次确认成交")


def _disk(*, events, last, held, avg, proj, applied, status, note):
    return {"event_count": events, "last_event": last, "held_qty": held, "avg_cost": avg,
            "projection": proj, "applied_fills": applied, "has_fill_book": applied,
            "proposal_status": status, "proposal_note": note}


_OPENING_LAST_1000 = {
    "event_id": "<opening>", "type": "OPENING", "security_id": CODE, "trade_date": "2026-09-20",
    "quantity": 1000, "price": 10.0, "cash_delta": 0.0, "fee": 0.0, "related_fill_id": "<opening>",
    "plan_ref": None, "deviation": None, "fill_note": None}


def _disk_norm(d):
    """期初批次事件 id 含随机后缀——归一化为 <opening>（仅 REJECTED/未入账场景的末事件）。"""
    last = dict(d["last_event"])
    if str(last.get("event_id", "")).startswith("fill_opening_lot_"):
        last["event_id"] = "<opening>"
        last["related_fill_id"] = "<opening>"
    return {**d, "last_event": last}


# ── 1. BUY 正常入账 ───────────────────────────────────────

def test_golden_buy_with_active_plan_ref(env):
    """1a BUY（建议 ADD）+ 计划库有 active plan_ref：输出多一行「主意图引用」，plan_ref 随事件落账。
    真实路径（计划库真实写入 tmp）。"""
    env.seed(action="ADD", target_ratio=0.15, held=300)
    env.seed_active_plan("p_mid_gold")
    env.confirm(qty=100, price=15.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: BUY 100 股 @ ¥15.0（依据建议 ADD）",
        "  主意图引用: p_mid_gold…（MID 计划）",
        "✓ 事件账本入账: BUY 100 股 @ ¥15.0 ｜ 现金变动 -1500.00 元 ｜ 版本 <VER>",
        "✓ 已记录本笔成交：已成交 100 股 / 现持有 400 股，批次均价成本 ¥11.25",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    assert env.disk() == _disk(
        events=3, held=400, avg=11.25, proj=_proj(400, 11.25, "CONFIRMED_BUY"),
        last=_ev(event_id="fill_gp#1", type="BUY", quantity=100, price=15.0,
                 cash_delta=-1500.0, related_fill_id="gp#1", plan_ref="p_mid_gold"),
        applied=["gp#1"], status="PROPOSED", note=_note("买入", 100, 400, "ADD"))


def test_golden_buy_without_plan_ref_with_fee(env):
    """1b BUY 无 active plan_ref（不虚构引用，无「主意图引用」行）+ 费用计入现金变动。真实路径。"""
    env.seed(action="ADD", target_ratio=0.15, held=300)
    env.confirm(qty=100, price=15.0, fee=5.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: BUY 100 股 @ ¥15.0（依据建议 ADD）",
        "✓ 事件账本入账: BUY 100 股 @ ¥15.0 ｜ 现金变动 -1505.00 元 ｜ 版本 <VER>",
        "✓ 已记录本笔成交：已成交 100 股 / 现持有 400 股，批次均价成本 ¥11.25",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    assert env.disk() == _disk(
        events=3, held=400, avg=11.25, proj=_proj(400, 11.25, "CONFIRMED_BUY"),
        last=_ev(event_id="fill_gp#1", type="BUY", quantity=100, price=15.0, fee=5.0,
                 cash_delta=-1505.0, related_fill_id="gp#1"),
        applied=["gp#1"], status="PROPOSED", note=_note("买入", 100, 400, "ADD"))


# ── 2/3/4. SELL 部分 / 清仓 / 清仓建议未达成 ────────────────

def test_golden_sell_partial_reduce(env):
    """2 SELL 部分卖出（1000 卖 300，建议 REDUCE）：记录保留、待重估、建议保持 PROPOSED。真实路径。"""
    env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    env.confirm(qty=300, price=12.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 300 股 @ ¥12.0（依据建议 REDUCE）",
        "✓ 事件账本入账: SELL 300 股 @ ¥12.0 ｜ 现金变动 +3600.00 元 ｜ 版本 <VER>",
        "✓ 已记录本笔成交：已成交 300 股 / 剩余 700 股",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    assert env.disk() == _disk(
        events=3, held=700, avg=10.0, proj=_proj(700, 10.0, "CONFIRMED_SELL"),
        last=_ev(event_id="fill_gp#1", type="SELL", quantity=300, price=12.0,
                 cash_delta=3600.0, related_fill_id="gp#1"),
        applied=["gp#1"], status="PROPOSED", note=_note("卖出", 300, 700, "REDUCE"))


def test_golden_sell_full_clear(env):
    """3 SELL 清仓（1000 全卖，CLOSE_ALL）：持仓记录删除、建议 CONFIRMED。真实路径。"""
    env.seed(action="CLOSE_ALL", target_ratio=0.0, held=1000)
    env.confirm(qty=1000, price=12.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 1000 股 @ ¥12.0（依据建议 CLOSE_ALL）",
        "✓ 事件账本入账: SELL 1000 股 @ ¥12.0 ｜ 现金变动 +12000.00 元 ｜ 版本 <VER>",
        "✓ 清仓完成（实际卖出 1000 股）",
        "✓ 比例视图已同步 ｜ 持仓现状: 记录已删除（清仓完成）",
    ]
    assert env.disk() == _disk(
        events=3, held=0, avg=None, proj=None,
        last=_ev(event_id="fill_gp#1", type="SELL", quantity=1000, price=12.0,
                 cash_delta=12000.0, related_fill_id="gp#1"),
        applied=["gp#1"], status="CONFIRMED", note="实际清仓达成（本笔卖出1000股，剩余0股）")


def test_golden_sell_close_all_only_partly_done_marks_deviation(env):
    """4 建议 CLOSE_ALL 但只卖 400/1000：deviation=True，事件注记带偏离，提示「建议（清仓）未完全达成」。真实路径。"""
    env.seed(action="CLOSE_ALL", target_ratio=0.0, held=1000)
    env.confirm(qty=400, price=12.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 400 股 @ ¥12.0（依据建议 CLOSE_ALL）",
        "✓ 事件账本入账: SELL 400 股 @ ¥12.0 ｜ 现金变动 +4800.00 元 ｜ 版本 <VER>",
        "✓ 已记录本笔成交：已成交 400 股 / 剩余 600 股",
        "  ⚠ 建议（清仓）未完全达成——建议保持待确认，如继续卖出请再次执行确认",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    assert env.disk() == _disk(
        events=3, held=600, avg=10.0, proj=_proj(600, 10.0, "CONFIRMED_SELL"),
        last=_ev(event_id="fill_gp#1", type="SELL", quantity=400, price=12.0,
                 cash_delta=4800.0, related_fill_id="gp#1", deviation=True,
                 fill_note="proposal=gp；偏离：建议清仓，本笔卖出400/1000股"),
        applied=["gp#1"], status="PROPOSED", note=_note("卖出", 400, 600, "CLOSE_ALL"))


# ── 5. 重复执行 / DUPLICATE ───────────────────────────────

def test_golden_repeat_after_completed_fill_is_a_new_fill(env):
    """5a 同一命令在上次已完整完成（账本+投影都消费）后再次执行：当前行为是**新的逐笔部分成交**
    （fill_id 递增为 gp#2、再记一笔卖出），不是 DUPLICATE。锁定该行为（重构必须保持）。真实路径。"""
    env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    env.confirm(qty=300, price=12.0)
    env.mark()
    env.confirm(qty=300, price=12.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 300 股 @ ¥12.0（依据建议 REDUCE）",
        "✓ 事件账本入账: SELL 300 股 @ ¥12.0 ｜ 现金变动 +3600.00 元 ｜ 版本 <VER>",
        "✓ 已记录本笔成交：已成交 300 股 / 剩余 400 股",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    assert env.disk() == _disk(
        events=4, held=400, avg=10.0, proj=_proj(400, 10.0, "CONFIRMED_SELL"),
        last=_ev(event_id="fill_gp#2", type="SELL", quantity=300, price=12.0,
                 cash_delta=3600.0, related_fill_id="gp#2"),
        applied=["gp#1", "gp#2"], status="PROPOSED", note=_note("卖出", 300, 400, "REDUCE"))


def test_golden_duplicate_ledger_written_projection_untouched(env):
    """5b DUPLICATE：账本已用 AccountService 直接写入 gp#1（偏离位 True，对应 CLOSE_ALL 卖 100/150），
    投影与建议库未动；CLI 再确认 → 恢复态提示 + 账本 DUPLICATE（幂等跳过）+ 投影补做，
    且 deviation 位以在账事件回读（CLI 当时重算 held_before=50 本会得 False）。
    账本事件为真实 API 写入；CLI 路径全真实。再执行第二次：新 fill 超卖 → REJECTED（磁盘不变）。"""
    from src.data.account_service import FillInput
    env.seed(action="CLOSE_ALL", target_ratio=0.0, held=150)
    receipt = env.svc().confirm_fill("gp#1", "", FillInput(
        security_id=CODE, action="SELL", quantity=100, price=10.0, trade_date=TRADE_DATE,
        deviation_from_proposal=True, note="proposal=gp；偏离：建议清仓，本笔卖出100/150股"))
    assert receipt.status == "ACCEPTED"
    pre = env.disk()
    assert pre["applied_fills"] == [] and pre["proposal_status"] == "PROPOSED"
    assert pre["projection"] == _PROJ_UNTOUCHED
    env.confirm(qty=100, price=10.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 100 股 @ ¥10.0（依据建议 CLOSE_ALL）",
        "↩ 检测到未完成的确认（fill gp#1 已入账、投影未同步）——按崩溃恢复补做，不重复入账",
        "↩ fill gp#1 已入账过（幂等跳过账本）——继续核对比例投影一致性",
        "✓ 已记录本笔成交：已成交 100 股 / 剩余 50 股",
        "  ⚠ 建议（清仓）未完全达成——建议保持待确认，如继续卖出请再次执行确认",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    after = env.disk()
    assert after == _disk(
        events=3, held=50, avg=10.0, proj=_proj(50, 10.0, "CONFIRMED_SELL"),
        last=_ev(event_id="fill_gp#1", type="SELL", quantity=100, price=10.0,
                 cash_delta=1000.0, related_fill_id="gp#1", deviation=True,
                 fill_note="proposal=gp；偏离：建议清仓，本笔卖出100/150股"),
        applied=["gp#1"], status="PROPOSED", note=_note("卖出", 100, 50, "CLOSE_ALL"))
    # 恢复完成后再执行同一命令：已消费 → 新 fill gp#2，100 > 持仓 50 → 账本拒绝，磁盘不变
    env.mark()
    env.confirm(qty=100, price=10.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 100 股 @ ¥10.0（依据建议 CLOSE_ALL）",
        "✗ 数量级确认未入账（REJECTED）: 超卖拒绝：卖出 100 股 > 持仓 50 股",
        "  账户/投影均未改动——核对后重试（版本过期请重读 pos list）",
    ]
    assert env.disk() == after


# ── 6. 崩溃恢复态（投影前崩溃 → 重跑）──────────────────────

def test_golden_crash_before_projection_then_rerun_recovers(env, monkeypatch):
    """6 首跑：桩 PortfolioManager.apply_quantity_fill 抛 RuntimeError（模拟投影前崩溃，账本已入账）；
    重跑（新 PortfolioManager 实例，桩撤销）走「检测到未完成的确认」恢复分支。其余全真实。"""
    env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    real = PortfolioManager.apply_quantity_fill

    def _boom(self, *a, **k):
        raise RuntimeError("模拟投影前崩溃")
    monkeypatch.setattr(PortfolioManager, "apply_quantity_fill", _boom)
    with pytest.raises(RuntimeError):
        env.confirm(qty=300, price=12.0)
    assert env.mark() == _HEAD + [
        "  600519 测试股: SELL 300 股 @ ¥12.0（依据建议 REDUCE）",
        "✓ 事件账本入账: SELL 300 股 @ ¥12.0 ｜ 现金变动 +3600.00 元 ｜ 版本 <VER>",
    ]
    crashed = _disk(
        events=3, held=700, avg=10.0, proj=_PROJ_UNTOUCHED,
        last=_ev(event_id="fill_gp#1", type="SELL", quantity=300, price=12.0,
                 cash_delta=3600.0, related_fill_id="gp#1"),
        applied=[], status="PROPOSED", note="")
    assert env.disk() == crashed, "崩溃态：账本已入账，投影/水位/建议均未动"
    monkeypatch.setattr(PortfolioManager, "apply_quantity_fill", real)
    env.confirm(qty=300, price=12.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 300 股 @ ¥12.0（依据建议 REDUCE）",
        "↩ 检测到未完成的确认（fill gp#1 已入账、投影未同步）——按崩溃恢复补做，不重复入账",
        "↩ fill gp#1 已入账过（幂等跳过账本）——继续核对比例投影一致性",
        "✓ 已记录本笔成交：已成交 300 股 / 剩余 700 股",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    assert env.disk() == _disk(
        events=3, held=700, avg=10.0, proj=_proj(700, 10.0, "CONFIRMED_SELL"),
        last=crashed["last_event"], applied=["gp#1"], status="PROPOSED",
        note=_note("卖出", 300, 700, "REDUCE"))


# ── 7. PERSIST_UNKNOWN / 账本拒绝 / 投影同步失败 ────────────

def test_golden_persist_unknown_then_rerun(env, monkeypatch):
    """7a PERSIST_UNKNOWN：仅桩 account_service._fsync_ledger→False，confirm_fill 其余真实
    （事件真实追加到盘）。输出黄叹号行后即返回，投影/建议未动；撤桩重跑 → 恢复态 + DUPLICATE。"""
    env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    real_fsync = account_module._fsync_ledger
    monkeypatch.setattr(account_module, "_fsync_ledger", lambda path: False)
    env.confirm(qty=300, price=12.0)
    assert env.mark() == _HEAD + [
        "  600519 测试股: SELL 300 股 @ ¥12.0（依据建议 REDUCE）",
        "⚠ 写入状态待核对（PERSIST_UNKNOWN）: 事件已写入但磁盘落盘确认失败（fsync）——写入状态待核对。"
        "请重跑同一确认核对：已入账会返回 DUPLICATE（幂等不重复入账）",
    ]
    unknown = _disk(
        events=3, held=700, avg=10.0, proj=_PROJ_UNTOUCHED,
        last=_ev(event_id="fill_gp#1", type="SELL", quantity=300, price=12.0,
                 cash_delta=3600.0, related_fill_id="gp#1"),
        applied=[], status="PROPOSED", note="")
    assert env.disk() == unknown
    monkeypatch.setattr(account_module, "_fsync_ledger", real_fsync)
    env.confirm(qty=300, price=12.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 300 股 @ ¥12.0（依据建议 REDUCE）",
        "↩ 检测到未完成的确认（fill gp#1 已入账、投影未同步）——按崩溃恢复补做，不重复入账",
        "↩ fill gp#1 已入账过（幂等跳过账本）——继续核对比例投影一致性",
        "✓ 已记录本笔成交：已成交 300 股 / 剩余 700 股",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    assert env.disk() == _disk(
        events=3, held=700, avg=10.0, proj=_proj(700, 10.0, "CONFIRMED_SELL"),
        last=unknown["last_event"], applied=["gp#1"], status="PROPOSED",
        note=_note("卖出", 300, 700, "REDUCE"))


def test_golden_ledger_rejects_oversell(env):
    """7b 账本拒绝（receipt.ok=False，REJECTED）：真实路径，卖出 2000 > 持仓 1000。
    输出红叉两行；账本/投影/建议均未改动（末事件仍是期初批次，id 随机后缀已归一化）。"""
    env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    before = _disk_norm(env.disk())
    env.confirm(qty=2000, price=12.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 2000 股 @ ¥12.0（依据建议 REDUCE）",
        "✗ 数量级确认未入账（REJECTED）: 超卖拒绝：卖出 2000 股 > 持仓 1000 股",
        "  账户/投影均未改动——核对后重试（版本过期请重读 pos list）",
    ]
    after = _disk_norm(env.disk())
    assert after == before == _disk(
        events=2, held=1000, avg=10.0, proj=_PROJ_UNTOUCHED, last=_OPENING_LAST_1000,
        applied=[], status="PROPOSED", note="")


def test_golden_projection_sync_failure_then_rerun_recovers(env, monkeypatch):
    """7c 投影同步失败（result.ok=False）：仅桩 PortfolioManager._save→SaveResult(ok=False)，
    apply_quantity_fill 真实执行并返回其真实失败消息。账本已入账、投影/水位未落盘；
    撤桩后重跑 → 恢复态 + 投影补做（输出承诺的「重跑可幂等恢复」）。"""
    env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    real_save = PortfolioManager._save
    monkeypatch.setattr(PortfolioManager, "_save", lambda self: SaveResult(False))
    env.confirm(qty=300, price=12.0)
    assert env.mark() == _HEAD + [
        "  600519 测试股: SELL 300 股 @ ¥12.0（依据建议 REDUCE）",
        "✓ 事件账本入账: SELL 300 股 @ ¥12.0 ｜ 现金变动 +3600.00 元 ｜ 版本 <VER>",
        "✓ 已记录本笔成交：已成交 300 股 / 剩余 700 股",
        "⚠ 数量级事实已入账（fill_id=gp#1），但比例投影同步失败: 持仓文件保存失败（详见告警），"
        "比例投影未同步（数量事实已在账户事件账本，重跑同一确认可幂等恢复投影）",
        "  重跑同一命令可幂等恢复投影（账本不会重复入账）",
    ]
    failed = _disk(
        events=3, held=700, avg=10.0, proj=_PROJ_UNTOUCHED,
        last=_ev(event_id="fill_gp#1", type="SELL", quantity=300, price=12.0,
                 cash_delta=3600.0, related_fill_id="gp#1"),
        applied=[], status="PROPOSED", note="")
    assert env.disk() == failed
    monkeypatch.setattr(PortfolioManager, "_save", real_save)
    env.confirm(qty=300, price=12.0)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 300 股 @ ¥12.0（依据建议 REDUCE）",
        "↩ 检测到未完成的确认（fill gp#1 已入账、投影未同步）——按崩溃恢复补做，不重复入账",
        "↩ fill gp#1 已入账过（幂等跳过账本）——继续核对比例投影一致性",
        "✓ 已记录本笔成交：已成交 300 股 / 剩余 700 股",
        "✓ 比例视图已同步 ｜ 数量事实以账户账本为准（比例视图已标待重估）",
    ]
    assert env.disk() == _disk(
        events=3, held=700, avg=10.0, proj=_proj(700, 10.0, "CONFIRMED_SELL"),
        last=failed["last_event"], applied=["gp#1"], status="PROPOSED",
        note=_note("卖出", 300, 700, "REDUCE"))


# ── 8. 缺价格 ─────────────────────────────────────────────

@pytest.mark.parametrize("price", [0.0, None])
def test_golden_missing_price_rejected_before_any_write(env, price):
    """8 价格为空（0.0 默认值 / None）：「数量级确认需成交价」，账本与投影均未改动。真实路径。"""
    env.seed(action="REDUCE", target_ratio=0.05, held=1000)
    before = _disk_norm(env.disk())
    env.confirm(qty=300, price=price)
    assert env.lines() == _HEAD + [
        "  600519 测试股: SELL 300 股（依据建议 REDUCE）",
        "✗ 数量级确认需成交价（--qty 与成交价成对给）——未入账",
    ]
    assert _disk_norm(env.disk()) == before == _disk(
        events=2, held=1000, avg=10.0, proj=_PROJ_UNTOUCHED, last=_OPENING_LAST_1000,
        applied=[], status="PROPOSED", note="")
