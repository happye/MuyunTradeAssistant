"""F1 观察量回归测试（plan/fusion TASKS.md F1，DESIGN ADR-F03）

锁死语义：
1. 核心验收：临时持仓 20% → 建议 EXIT（CLOSE_ALL）且执行受阻，记录仍 20% 且
   成本/日期不变——"建议已卖"不再被当成"实际已卖"
2. 所有分析入口都不能伪造持仓：无持仓时观察量更新返回 False 且不建记录
3. 同日重复分析不重复推进交易日计数（绝对值写入 + 无变化不落盘）
4. 旧记录 LEGACY_UNVERIFIED 迁移：数值保持原样，仅加来源标记
5. high_since_entry 三者取大语义保留；已有冷却随交易日倒数、观察绝不发起冷却

纯 mock/临时目录，无网络。跑法：pytest tests/core/test_portfolio_observation.py -q
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.models import TradeLifecycle
from src.data.portfolio import PortfolioManager

import contextlib


@contextlib.contextmanager
def _tmp_portfolio(positions: dict):
    """临时 portfolio.yaml + 临时建议账本（脚本直跑也不碰真实 ~/.muyun）。"""
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="f1_obs_test_")
    os.close(fd)
    ledger_fd, ledger = tempfile.mkstemp(suffix=".json", prefix="f1_obs_ledger_")
    os.close(ledger_fd)
    os.remove(ledger)  # 账本允许不存在（空账本起步）
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"positions": positions}, f, allow_unicode=True)
    _tmp_portfolio.current_ledger = ledger
    try:
        yield path
    finally:
        _tmp_portfolio.current_ledger = None
        for p in (path, ledger):
            if os.path.exists(p):
                os.remove(p)


def _pm(path) -> PortfolioManager:
    """带注入账本的 PortfolioManager（proposals_path 取当前 _tmp_portfolio）。"""
    return PortfolioManager(path, proposals_path=getattr(_tmp_portfolio, "current_ledger", None))


_HOLD_STATE = dict(
    lifecycle=TradeLifecycle.HOLD, cooldown_remaining=0,
    cooldown_reason=None, reverse_count=0, inertia_counter=1,
    last_decision=SimpleNamespace(value="HOLD"), recent_signals=["HOLD"],
    signal_stability_score=1.0, reduce_protection_remaining=0,
    min_hold_remaining=0, add_protection_remaining=0, last_tick_date="2026-09-25",
    current_position_ratio=0.0,
)


def _decision(pos_action="HOLD_POSITION", ratio=0.0, exit_state=None, ee=None):
    """鸭子类型 StrategyDecision 替身；exit_state 用于 CLOSE_ALL 场景。"""
    state = SimpleNamespace(**(exit_state or _HOLD_STATE))
    return SimpleNamespace(
        position_action=SimpleNamespace(value=pos_action),
        action_semantic="EXIT" if pos_action == "CLOSE_ALL" else "HOLD",
        sell_path="stop_loss_exit" if pos_action == "CLOSE_ALL" else None,
        position_ratio=ratio, new_state=state,
        entry_exit=ee,
    )


_STOCK = SimpleNamespace(price=10.0, stock_name="探针股")


# ── 1. 核心验收：建议 EXIT 受阻，持仓事实不动 ───────────────

def test_exit_proposal_does_not_touch_confirmed_position():
    """TASKS F1 验收原文：临时持仓20%→建议EXIT且execution受阻，记录仍20%且成本/日期不变。"""
    with _tmp_portfolio({"601318": {
        "stock_name": "中国平安", "entry_date": "2026-08-10", "entry_price": 50.0,
        "current_ratio": 0.2, "last_action": "OPEN", "lifecycle": "HOLD",
        "strategy_state": {"cooldown_remaining": 0, "recent_signals": ["BUY"],
                           "min_hold_remaining": 2, "last_tick_date": "2026-09-24"},
    }}) as path:
        pm = _pm(path)
        # 策略层建议清仓：new_state 已按"已清仓"推演（比率0、FLAT、冷却5天）——
        # 正是旧 update_from_strategy_decision 会整包写盘、吞掉持仓的数据
        exit_state = dict(
            lifecycle=TradeLifecycle.FLAT, cooldown_remaining=5,
            cooldown_reason="close_all", reverse_count=0, inertia_counter=0,
            last_decision=SimpleNamespace(value="SELL"), recent_signals=["SELL", "HOLD"],
            signal_stability_score=0.9, reduce_protection_remaining=0,
            min_hold_remaining=0, add_protection_remaining=0,
            last_tick_date="2026-09-25", current_position_ratio=0.0,
        )
        ok = pm.record_analysis_observation(
            "601318", "中国平安", _decision("CLOSE_ALL", 0.0, exit_state=exit_state), _STOCK)
        assert ok is True

        rec = pm.get_position("601318")
        assert rec is not None, "分析建议清仓不得删除持仓记录"
        assert rec.current_ratio == 0.2, f"仓位必须保持 0.2，实际 {rec.current_ratio}"
        assert rec.entry_date == "2026-08-10", "开仓日期不得被建议改动"
        assert rec.entry_price == 50.0, "开仓价不得被建议改动"
        assert rec.lifecycle == "HOLD", "生命周期是成交驱动的事实，不得随建议翻转"
        assert rec.strategy_state.get("cooldown_remaining") == 0, \
            "被建议的清仓不得启动冷却（G03：受阻 EXIT 不能伪装成已清仓）"
        # 信号历史照常推进（观察量）
        assert rec.strategy_state.get("last_tick_date") == "2026-09-25"
        assert "SELL" in rec.strategy_state.get("recent_signals", [])

        # 建议入账（PROPOSED），用户可见"这是建议，尚未记为成交"
        prop = pm.record_proposal("601318", "中国平安", _decision("CLOSE_ALL", 0.0, exit_state=exit_state), source="l")
        assert prop is not None and prop.status.value == "PROPOSED"
        assert prop.position_action == "CLOSE_ALL"
        assert pm.get_position("601318").current_ratio == 0.2, "建议入账后持仓仍不得变"


# ── 2. 不伪造持仓 ─────────────────────────────────────────

def test_observation_never_fabricates_position():
    """TASKS F1 验收：所有分析入口都不能伪造持仓。"""
    with _tmp_portfolio({}) as path:
        pm = _pm(path)
        assert pm.record_analysis_observation(
            "600519", "贵州茅台", _decision(), _STOCK) is False, "无持仓观察量更新必须返回 False"
        assert pm.get_position("600519") is None, "无持仓不得被分析创建"
        with open(path, encoding="utf-8") as f:
            disk = yaml.safe_load(f)
        assert disk is not None and not disk.get("positions"), "磁盘上不得出现新持仓"


def test_proposal_requires_real_position():
    """建议也只对真实持仓出：非持仓不产生 ADD/REDUCE/CLOSE_ALL 建议。"""
    with _tmp_portfolio({}) as path:
        pm = _pm(path)
        assert pm.record_proposal("600519", "贵州茅台", _decision("REDUCE", 0.1)) is None
        assert pm.record_proposal("600519", "贵州茅台", _decision("CLOSE_ALL", 0.0)) is None
        assert pm.pending_proposals("600519") == []


# ── 3. 同日重复分析幂等 ───────────────────────────────────

def test_same_day_reanalysis_does_not_double_advance():
    """TASKS F1 验收：同日重复分析不重复推进交易日计数（min_hold 不被烧穿）。

    同日重入时 strategy_layer 按 ISS-086 日期去重不递减——new_state 仍是 min_hold=3；
    观察量为绝对值写入，两次落盘结果一致。
    """
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "lifecycle": "HOLD",
        "strategy_state": {"min_hold_remaining": 3, "last_tick_date": "2026-09-25"},
    }}) as path:
        pm = _pm(path)
        same_day_state = dict(_HOLD_STATE, min_hold_remaining=3, last_tick_date="2026-09-25")
        assert pm.record_analysis_observation(
            "600519", "贵州茅台", _decision(exit_state=same_day_state), _STOCK) is True
        assert pm.record_analysis_observation(
            "600519", "贵州茅台", _decision(exit_state=same_day_state), _STOCK) is True
        rec = pm.get_position("600519")
        assert rec.strategy_state.get("min_hold_remaining") == 3, "同日重复分析不得重复递减"


# ── 4. LEGACY_UNVERIFIED 迁移 ─────────────────────────────

def test_legacy_record_tagged_unverified_values_preserved():
    """旧记录无来源标记 → LEGACY_UNVERIFIED，数值保持原样（不自动"修正"）。"""
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "entry_date": "2026-01-01", "entry_price": 1600.0,
        "current_ratio": 0.3, "lifecycle": "HOLD", "strategy_state": {},
    }}) as path:
        pm = _pm(path)
        rec = pm.get_position("600519")
        assert rec.holding_verification == "LEGACY_UNVERIFIED"
        assert rec.current_ratio == 0.3 and rec.entry_price == 1600.0, "迁移不得改动数值"
        # 观察量更新后标记随保存落盘、数值仍原样
        pm.record_analysis_observation("600519", "贵州茅台", _decision(), _STOCK)
        rec2 = pm.get_position("600519")
        assert rec2.holding_verification == "LEGACY_UNVERIFIED"
        assert rec2.current_ratio == 0.3


# ── 5. high_since_entry 与冷却语义 ────────────────────────

def test_high_since_entry_merges_max():
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "lifecycle": "HOLD",
        "high_since_entry": 12.0, "strategy_state": {},
    }}) as path:
        pm = _pm(path)
        ee = {"highest_since_entry": 15.0}
        pm.record_analysis_observation("600519", "贵州茅台", _decision(ee=ee), _STOCK)
        assert pm.get_position("600519").high_since_entry == 15.0, "三者取大应取 entry_exit 值"
        # 回落价格不得把高点拉低
        pm.record_analysis_observation("600519", "贵州茅台", _decision(), _STOCK)
        assert pm.get_position("600519").high_since_entry == 15.0


def test_existing_cooldown_ticks_down_but_never_initiated():
    """已有冷却（confirmed 事实）随交易日倒数；观察对 cooldown=0 的记录永不发起冷却。"""
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.0, "lifecycle": "COOLDOWN",
        "strategy_state": {"cooldown_remaining": 2, "cooldown_reason": "confirmed_close"},
    }}) as path:
        pm = _pm(path)
        # 策略层把冷却倒数到 1（tick_cooldown 语义）——观察应持久化这个衰减
        cool_state = dict(_HOLD_STATE, cooldown_remaining=1, cooldown_reason="confirmed_close",
                          lifecycle=TradeLifecycle.COOLDOWN)
        pm.record_analysis_observation("600519", "贵州茅台", _decision(exit_state=cool_state), _STOCK)
        rec = pm.get_position("600519")
        assert rec.strategy_state.get("cooldown_remaining") == 1, "既有冷却必须随日倒数"

    # 观察绝不发起冷却：建议 CLOSE_ALL（new_state 自带冷却5）也不写入 cooldown=0 的记录
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.2, "lifecycle": "HOLD",
        "strategy_state": {"cooldown_remaining": 0},
    }}) as path:
        pm = _pm(path)
        exit_state = dict(_HOLD_STATE, cooldown_remaining=5, cooldown_reason="close_all",
                          lifecycle=TradeLifecycle.FLAT)
        pm.record_analysis_observation(
            "600519", "贵州茅台", _decision("CLOSE_ALL", 0.0, exit_state=exit_state), _STOCK)
        rec = pm.get_position("600519")
        assert rec.strategy_state.get("cooldown_remaining") == 0, "观察不得发起冷却"


def test_record_deleted_after_cooldown_expires():
    """确认清仓后的冷却归零（FLAT+0仓）→ 记录删除（既有语义）。"""
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.0, "lifecycle": "COOLDOWN",
        "strategy_state": {"cooldown_remaining": 1, "cooldown_reason": "confirmed_close"},
    }}) as path:
        pm = _pm(path)
        done_state = dict(_HOLD_STATE, cooldown_remaining=0, cooldown_reason=None,
                          lifecycle=TradeLifecycle.FLAT)
        pm.record_analysis_observation("600519", "贵州茅台", _decision(exit_state=done_state), _STOCK)
        assert pm.get_position("600519") is None, "冷却结束且已清仓的记录应删除"
