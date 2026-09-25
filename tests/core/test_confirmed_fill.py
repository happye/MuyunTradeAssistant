"""F1 成交确认回归测试（plan/fusion TASKS.md F1，DESIGN ADR-F03）

锁死语义：
1. 用户确认部分成交才减仓，重复确认幂等（fill_id 去重，持仓不得重复变动）
2. 清仓确认按建议进冷却（记录保留）；无冷却可走则删除记录（既有手动语义）
3. 双实例冲突不吃更新：_save 冲突时 confirm_fill 失败且账本无脏数据
4. BUY 确认可建仓/加仓；越界（>100%、卖超）拒绝
5. 建议状态机：PROPOSED -> CONFIRMED / PARTIAL（部分成交保留剩余待办）

纯 mock/临时目录，无网络。跑法：pytest tests/core/test_confirmed_fill.py -q
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.portfolio import PortfolioManager

import contextlib


@contextlib.contextmanager
def _tmp_env(positions: dict):
    """临时 portfolio.yaml + 临时建议账本（不得碰真实 ~/.muyun）。"""
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="f1_fill_test_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"positions": positions}, f, allow_unicode=True)
    ledger_fd, ledger = tempfile.mkstemp(suffix=".json", prefix="f1_fill_ledger_")
    os.close(ledger_fd)
    os.remove(ledger)  # 账本允许不存在（空账本起步）
    try:
        yield path, ledger
    finally:
        for p in (path, ledger):
            if os.path.exists(p):
                os.remove(p)


def _make_pm(path, ledger):
    return PortfolioManager(path, proposals_path=ledger)


def _exit_decision(ratio=0.0, cooldown=5):
    """建议清仓的 StrategyDecision 替身（new_state 携带策略层建议的冷却天数）。"""
    return SimpleNamespace(
        position_action=SimpleNamespace(value="CLOSE_ALL"),
        action_semantic="EXIT", sell_path="stop_loss_exit", position_ratio=ratio,
        strategy_reasons=["止损触发"],
        new_state=SimpleNamespace(
            lifecycle=SimpleNamespace(value="FLAT"), cooldown_remaining=cooldown,
            cooldown_reason="close_all", current_position_ratio=0.0),
    )


def _reduce_decision(ratio=0.1):
    return SimpleNamespace(
        position_action=SimpleNamespace(value="REDUCE"),
        action_semantic="TRIM", sell_path="take_profit_trim", position_ratio=ratio,
        strategy_reasons=["止盈减仓"],
        new_state=SimpleNamespace(
            lifecycle=SimpleNamespace(value="HOLD"), cooldown_remaining=0,
            cooldown_reason=None, current_position_ratio=ratio),
    )


# ── 1. 部分成交与幂等 ─────────────────────────────────────

def test_partial_sell_then_full_sell_with_proposal():
    """TASKS F1 验收：用户确认部分成交才减仓；剩余待办保持可见；清仓按建议进冷却。"""
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": {},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        # 先出建议（分析入口只到这里为止）
        prop = pm.record_proposal("601318", "中国平安", _exit_decision(cooldown=5), source="l")
        assert prop is not None
        assert pm.get_position("601318").current_ratio == 0.2, "建议不改持仓"

        # 部分成交：卖 10 个点
        r1 = pm.confirm_fill("601318", "SELL", 0.10, date="2026-09-25", price=48.0)
        assert r1.ok and not r1.duplicate
        rec = pm.get_position("601318")
        assert abs(rec.current_ratio - 0.10) < 1e-9, "部分成交后仓位 0.2→0.1"
        assert rec.holding_verification == "CONFIRMED_FILL"
        assert rec.lifecycle == "HOLD", "部分减仓不是清仓，生命周期不变"
        # 剩余待办保持可见（DESIGN：部分成交保留剩余待办）
        pend = pm.pending_proposals("601318")
        assert len(pend) == 1 and pend[0].status.value == "PARTIAL"

        # 全额卖出剩余 10 个点（自动匹配 PARTIAL 建议 → 清仓 → 按建议进冷却 5 天）
        r2 = pm.confirm_fill("601318", "SELL", 0.10, date="2026-09-26")
        assert r2.ok
        rec2 = pm.get_position("601318")
        assert rec2 is not None and rec2.lifecycle == "COOLDOWN"
        assert rec2.strategy_state.get("cooldown_remaining") == 5
        assert pm.pending_proposals("601318") == [], "全额确认后待办清空"

def test_duplicate_fill_id_idempotent():
    """重复 fill_id：第二次确认幂等跳过，持仓不得重复变动。"""
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": {},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        r1 = pm.confirm_fill("601318", "SELL", 0.05, fill_id="F-001", date="2026-09-25")
        assert r1.ok and not r1.duplicate
        assert abs(pm.get_position("601318").current_ratio - 0.15) < 1e-9
        r2 = pm.confirm_fill("601318", "SELL", 0.05, fill_id="F-001", date="2026-09-25")
        assert r2.ok and r2.duplicate, "重复 fill_id 应标记 duplicate"
        assert abs(pm.get_position("601318").current_ratio - 0.15) < 1e-9, "重复确认不得再减仓"


def test_full_close_with_proposal_enters_cooldown():
    """清仓确认且关联建议带冷却 → 记录保留为 COOLDOWN，冷却按建议值启动。"""
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": {},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        pm.record_proposal("601318", "中国平安", _exit_decision(cooldown=5), source="l")
        r = pm.confirm_fill("601318", "SELL", 0.2, date="2026-09-25")
        assert r.ok
        rec = pm.get_position("601318")
        assert rec is not None, "有冷却的清仓保留记录"
        assert rec.current_ratio == 0.0
        assert rec.lifecycle == "COOLDOWN"
        assert rec.strategy_state.get("cooldown_remaining") == 5, "冷却天数取自建议"
        assert rec.strategy_state.get("cooldown_reason") == "confirmed_close"
        assert rec.last_action == "CONFIRMED_SELL", "成交事实写入动作字段"


# ── 2. 冲突与越界 ─────────────────────────────────────────

def test_confirm_fill_conflict_does_not_eat_update():
    """双实例：A 保存后 B confirm_fill 必须失败，持仓不被吃、账本无脏 fill。"""
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": {},
    }}) as (path, ledger):
        a = _make_pm(path, ledger)
        b = _make_pm(path, ledger)
        assert a.confirm_fill("601318", "SELL", 0.1, date="2026-09-25").ok
        r = b.confirm_fill("601318", "SELL", 0.05, fill_id="F-B1", date="2026-09-25")
        assert r.ok is False, "B 的旧快照确认必须被拒绝"
        assert abs(a.get_position("601318").current_ratio - 0.10) < 1e-9, "A 的成交完好"
        assert not b._proposals.has_fill("F-B1"), "失败的确认不得留下账本脏数据"


def test_buy_creates_and_adds_position():
    with _tmp_env({}) as (path, ledger):
        pm = _make_pm(path, ledger)
        r = pm.confirm_fill("600519", "BUY", 0.15, date="2026-09-25", price=1500.0,
                            stock_name="贵州茅台")
        assert r.ok
        rec = pm.get_position("600519")
        assert rec is not None and rec.current_ratio == 0.15
        assert rec.entry_date == "2026-09-25" and rec.entry_price == 1500.0
        assert rec.lifecycle == "OPEN" and rec.holding_verification == "CONFIRMED_FILL"
        # 加仓
        r2 = pm.confirm_fill("600519", "BUY", 0.05, date="2026-09-26")
        assert r2.ok and abs(pm.get_position("600519").current_ratio - 0.20) < 1e-9


def test_boundary_violations_rejected():
    """闭区间边界（>100% 加仓 / 卖超 / 非法比例）显式拒绝，不静默夹断。"""
    with _tmp_env({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.95, "lifecycle": "HOLD",
        "strategy_state": {},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        r = pm.confirm_fill("600519", "BUY", 0.10)
        assert not r.ok and "100%" in r.message, "加仓越界拒绝"
        r2 = pm.confirm_fill("600519", "SELL", 0.96)
        assert not r2.ok, "卖超当前仓位拒绝"
        r3 = pm.confirm_fill("600519", "SELL", 0.0)
        assert not r3.ok, "零变化拒绝"
        r4 = pm.confirm_fill("600519", "HOLD", 0.1)
        assert not r4.ok, "非法 action 拒绝"
        r5 = pm.confirm_fill("600001", "SELL", 0.1)
        assert not r5.ok, "无持仓卖出拒绝"
        # 以上全部不得产生任何账本 fill
        assert pm._proposals._fills == [], "被拒绝的确认不得留下成交账本"
        assert len(pm._proposals.list_pending()) == 0


def test_proposal_state_machine():
    """PROPOSED -> PARTIAL（部分成交保留剩余待办语义由状态如实反映）。"""
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": {},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        pm.record_proposal("601318", "中国平安", _reduce_decision(ratio=0.1), source="l")
        assert len(pm.pending_proposals("601318")) == 1
        r = pm.confirm_fill("601318", "SELL", 0.05, date="2026-09-25")  # 建议目标 0.1 只成一半
        assert r.ok
        all_p = pm._proposals._proposals
        assert all_p[-1].status.value == "PARTIAL", "未达建议目标的成交转 PARTIAL"


# ── 3. F1 对抗审查修复回归锁 ──────────────────────────────

def test_add_proposal_confirmation_completes():
    """P1-3：ADD 建议→BUY 确认后必须转 CONFIRMED（状态机加仓腿不断裂）。"""
    add_decision = SimpleNamespace(
        position_action=SimpleNamespace(value="ADD"),
        action_semantic="ADD", sell_path=None, position_ratio=0.3,
        strategy_reasons=["加仓条件成立"],
        new_state=SimpleNamespace(
            lifecycle=SimpleNamespace(value="HOLD"), cooldown_remaining=0,
            cooldown_reason=None, current_position_ratio=0.3),
    )
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": {},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        pm.record_proposal("601318", "中国平安", add_decision, source="l")
        r = pm.confirm_fill("601318", "BUY", 0.1, date="2026-09-25")  # 缺省按建议全额 0.2→0.3
        assert r.ok
        assert abs(pm.get_position("601318").current_ratio - 0.30) < 1e-9
        assert pm.pending_proposals("601318") == [], "ADD 全额确认后待办必须清空"
        assert pm._proposals._proposals[-1].status.value == "CONFIRMED"


def test_reduce_exact_target_confirms_not_partial():
    """P1-4：REDUCE 恰达目标（剩仓==目标）→ CONFIRMED，不留假待办（闭区间边界）。"""
    reduce_decision = SimpleNamespace(
        position_action=SimpleNamespace(value="REDUCE"),
        action_semantic="TRIM", sell_path="take_profit_trim", position_ratio=0.1,
        strategy_reasons=["止盈减仓"],
        new_state=SimpleNamespace(
            lifecycle=SimpleNamespace(value="HOLD"), cooldown_remaining=0,
            cooldown_reason=None, current_position_ratio=0.1),
    )
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": {},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        pm.record_proposal("601318", "中国平安", reduce_decision, source="l")
        r = pm.confirm_fill("601318", "SELL", 0.1, date="2026-09-25")  # 缺省=按建议卖到 0.1
        assert r.ok
        assert abs(pm.get_position("601318").current_ratio - 0.10) < 1e-9
        assert pm.pending_proposals("601318") == [], "恰达目标是完成不是部分成交"
        assert pm._proposals._proposals[-1].status.value == "CONFIRMED"


def test_ledger_conflict_rejects_stale_store():
    """P1-2：账本也有版本冲突拒绝——陈旧实例的写入不得覆盖较新建议（M5 同款）。

    时序：A 写 v1 → B refresh 后写 v2（读-改-写合并，A 的建议保留）→
    A 再写 v3 → B 不重同步直接落盘 → 冲突拒绝，v3 完好。
    """
    with _tmp_env({
        "601318": {"stock_name": "中国平安", "current_ratio": 0.2, "lifecycle": "HOLD",
                   "strategy_state": {}},
        "000001": {"stock_name": "平安银行", "current_ratio": 0.1, "lifecycle": "HOLD",
                   "strategy_state": {}},
        "600519": {"stock_name": "贵州茅台", "current_ratio": 0.1, "lifecycle": "HOLD",
                   "strategy_state": {}},
    }) as (path, ledger):
        a = _make_pm(path, ledger)
        b = _make_pm(path, ledger)
        assert a.record_proposal("601318", "中国平安", _exit_decision(), source="l") is not None
        # B 陈旧实例 refresh 后写自己的建议：合并语义，不得丢 A 的
        assert b.record_proposal("000001", "平安银行", _reduce_decision(ratio=0.05), source="tui") is not None
        assert len(b.pending_proposals()) == 2, "refresh 后写入应合并保留对方建议"
        # A 再写第三条（磁盘前进到 v3；b 的基线还停在 v2）
        assert a.record_proposal("600519", "贵州茅台", _reduce_decision(ratio=0.05), source="l") is not None
        # b 不 refresh 直接落盘 → 冲突拒绝（不得覆盖 v3）
        assert b._proposals._save() is False, "陈旧基线落盘必须被冲突拒绝"
        # v3 完好：三条建议都在磁盘
        a._proposals.refresh()
        assert len(a.pending_proposals()) == 3, "冲突拒绝后磁盘上的全部建议完好"


def test_cooldown_fill_records_fill_day_as_tick_date():
    """P2-4：确认清仓写冷却时同步 last_tick_date=成交日（防成交日被多扣一天冷却）。"""
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": {},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        pm.record_proposal("601318", "中国平安", _exit_decision(cooldown=5), source="l")
        assert pm.confirm_fill("601318", "SELL", 0.2, date="2026-09-25").ok
        rec = pm.get_position("601318")
        assert rec.strategy_state.get("last_tick_date") == "2026-09-25", "成交日=冷却 tick 基准日"
        assert rec.strategy_state.get("cooldown_remaining") == 5


def test_buy_on_cooldown_record_resets_lifecycle():
    """P2-2：从冷却/空仓记录上确认买入=重新建仓——重置 lifecycle+strategy_state。"""
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-07-01", "entry_price": 60.0,
        "current_ratio": 0.0, "lifecycle": "COOLDOWN",
        "strategy_state": {"cooldown_remaining": 3, "cooldown_reason": "confirmed_close"},
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        r = pm.confirm_fill("601318", "BUY", 0.15, date="2026-09-25", price=48.0)
        assert r.ok
        rec = pm.get_position("601318")
        assert rec.lifecycle == "OPEN", "重新建仓不得残留 COOLDOWN"
        assert rec.strategy_state.get("cooldown_remaining") == 0, "冷却残留必须清零"
        assert rec.entry_date == "2026-09-25" and rec.entry_price == 48.0, "成本基线取本次成交"
        assert abs(rec.current_ratio - 0.15) < 1e-9


def test_null_strategy_state_normalized_not_crash():
    """P1-1：手工编辑留下 strategy_state: null → 观察量/清仓确认不崩，归一为 dict。"""
    # 清仓确认进冷却分支不崩
    with _tmp_env({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "lifecycle": "HOLD", "strategy_state": None,
    }}) as (path, ledger):
        pm = _make_pm(path, ledger)
        pm.record_proposal("601318", "中国平安", _exit_decision(cooldown=5), source="l")
        assert pm.confirm_fill("601318", "SELL", 0.2, date="2026-09-25").ok
        rec = pm.get_position("601318")
        assert rec.strategy_state.get("cooldown_remaining") == 5
